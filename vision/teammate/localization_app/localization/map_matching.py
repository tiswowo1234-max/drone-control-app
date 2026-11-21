"""Search translation, yaw, and a configurable range of camera ground spans."""

from dataclasses import dataclass, field, replace
import math
from functools import lru_cache
from time import perf_counter

import cv2
import numpy as np

from .camera_simulator import CameraConfig, image_center
from .image_processing import BuildingDetector, ColorConfig
from .map_loader import CampusMap, MapRaster
from .road_detection import RoadObservation, detect_roads, road_match_confidence
from .road_map import RoadMap


SPAN_SEPARATION_RATIO = 1.35
MATCHING_MODES = ("building", "road", "fixed", "adaptive")


def angle_error(a_deg: float, b_deg: float) -> float:
    """Return the smallest unsigned angle between two headings, in degrees."""
    return abs((a_deg - b_deg + 180) % 360 - 180)


@dataclass(frozen=True)
class SearchConfig:
    pixels_per_m: float = 80.0
    nominal_span_m: float = CameraConfig().footprint_m
    scale_factors: tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.2)
    yaw_step_deg: float = 5.0
    local_radius_m: float = 0.25
    min_frame_fraction: float = 0.0125
    min_foreground: int = 35  # Pixels at matching resolution
    min_score: float = 0.36
    min_margin: float = 0.07
    separation_m: float = 0.08
    separation_deg: float = 20.0
    auto_scale: bool = False
    scale_min: float = 0.5
    scale_max: float = 2.0
    scale_steps: int = 15
    scale_refine_steps: int = 5
    min_effective_fraction: float = 0.35
    matching_mode: str = "building"

    def __post_init__(self):
        if self.matching_mode not in MATCHING_MODES:
            raise ValueError("unknown matching mode")
        positive = (self.pixels_per_m, self.nominal_span_m, self.yaw_step_deg,
                    self.local_radius_m, self.min_foreground, self.separation_m,
                    self.separation_deg, *self.scale_factors)
        if not self.scale_factors or not all(math.isfinite(x) and x > 0 for x in positive):
            raise ValueError("search scale, resolution, step, and radii must be positive")
        fractions = (self.min_frame_fraction, self.min_score, self.min_margin, self.min_effective_fraction)
        if self.yaw_step_deg > 180 or not all(0 <= value <= 1 for value in fractions):
            raise ValueError("invalid yaw step, coverage, score threshold, or margin")
        if not (0 < self.scale_min < self.scale_max < math.inf
                and 0 < self.nominal_span_m * self.scale_min < self.nominal_span_m * self.scale_max < math.inf
                and all(type(steps) is int and steps >= 3
                        for steps in (self.scale_steps, self.scale_refine_steps))):
            raise ValueError("automatic scale requires 0 < min < max and at least three steps per stage")


@dataclass(frozen=True)
class Estimate:
    success: bool
    reason: str
    score: float = 0.0
    margin: float = 0.0
    x_m: float | None = None
    y_m: float | None = None
    yaw_deg: float | None = None
    span_m: float | None = None
    observation_source: str | None = None
    building_score: float | None = None
    road_score: float | None = None
    building_coverage: float | None = None
    road_coverage: float | None = None
    building_weight: float | None = None
    tracking_window: bool = False
    recovered_locally: bool = False


@dataclass(frozen=True)
class RecoveryHint:
    previous: Estimate
    reconnected: bool = False


@dataclass(frozen=True)
class MatchCandidate:
    score: float
    x_m: float
    y_m: float
    yaw_deg: float
    span_m: float


@dataclass
class MatchDiagnostics:
    """Optional search evidence in map units, including rejected hypotheses."""

    coarse_by_span: dict[float, MatchCandidate] = field(default_factory=dict)
    verified_by_span: dict[float, MatchCandidate] = field(default_factory=dict)
    candidates: tuple[MatchCandidate, ...] = ()
    runner_up: MatchCandidate | None = None


@dataclass(frozen=True)
class _ScoreGrid:
    yaw_deg: float
    span_m: float
    bounds: tuple[int, int, int, int]
    scores: np.ndarray
    building_scores: np.ndarray | None = None
    road_scores: np.ndarray | None = None

    def peak(self):
        _, score, _, (col, row) = cv2.minMaxLoc(self.scores)
        return score, col + self.bounds[0], row + self.bounds[1], self.yaw_deg, self.span_m


@dataclass(frozen=True)
class _Observation:
    labels: np.ndarray
    confidence: np.ndarray | None
    colors: tuple[int, ...]
    road: RoadObservation | None = None
    building_weight: float = 1.0
    building_coverage: float = 0.0

    @property
    def source(self):
        if self.building_weight == 1:
            return "BUILDING"
        return "ROAD_ONLY" if self.building_weight == 0 else "HYBRID"


class MapMatcher:
    def __init__(self, campus: CampusMap, search: SearchConfig = SearchConfig(),
                 colors: ColorConfig = ColorConfig(), *, _fine_roads: RoadMap | None = None):
        self.raster = MapRaster(campus, search.pixels_per_m)
        self.labels = self.raster.labels()
        self.detector = BuildingDetector(campus.palette, colors)
        if colors.hue_band_width:
            self.labels = self.detector.label_map[self.labels]
        self._map_color_bounds = {
            int(label): cv2.boundingRect(cv2.compare(self.labels, int(label), cv2.CMP_EQ))
            for label in np.unique(self.labels) if label
        }
        self.search = search
        self.roads = (RoadMap.build(self.raster, fine=_fine_roads)
                      if search.matching_mode != "building" else None)
        self.coarse = (MapMatcher(campus, replace(search, auto_scale=False,
                                                 pixels_per_m=search.pixels_per_m / 2), colors,
                                  _fine_roads=self.roads)
                       if search.auto_scale else None)

    def project_labels(self, frame_shape, candidate):
        """Project map labels into camera pixels for a verified or diagnostic candidate."""
        return self._project(self.labels, frame_shape, candidate)

    def project_roads(self, frame_shape, candidate):
        """Project the reference road region into camera pixels."""
        if self.roads is None:
            return np.zeros(frame_shape[:2], np.uint8)
        return self._project(self.roads.region, frame_shape, candidate)

    def _project(self, plane, frame_shape, candidate):
        height, width = frame_shape[:2]
        center = image_center(width, height)
        scale = candidate.span_m * self.raster.pixels_per_m / width
        transform = cv2.getRotationMatrix2D(center, candidate.yaw_deg, scale)
        transform[:, 2] += self.raster.xy_to_uv((candidate.x_m, candidate.y_m)) - center
        return cv2.warpAffine(plane, transform, (width, height),
                              flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)

    def estimate(self, frame: np.ndarray, prior_xy: tuple[float, float] | None = None, *,
                 radius_m: float | None = None, prior_yaw: float | None = None,
                 yaw_radius_deg: float = 30, prior_span_m: float | None = None,
                 diagnostics: MatchDiagnostics | None = None,
                 confidence_mask: np.ndarray | None = None,
                 timings: dict[str, float] | None = None,
                 on_labels=None, on_roads=None, prefer_tracking: bool = False,
                 recovery_hint: RecoveryHint | None = None) -> Estimate:
        """Estimate from BGR pixels, optionally narrowing xy, yaw and span around priors."""
        if timings is not None:
            segmentation_start = perf_counter()
        labels = self.detector.detect(frame, include_contours=False).labels
        if timings is not None:
            timings["hsv_ms"] = (perf_counter() - segmentation_start) * 1000
        if on_labels is not None:
            on_labels(labels)
        config = self.search
        if diagnostics is not None:
            diagnostics.coarse_by_span.clear()
            diagnostics.verified_by_span.clear()
            diagnostics.candidates = ()
            diagnostics.runner_up = None
        confidence = None if confidence_mask is None else np.asarray(confidence_mask, dtype=np.float32)
        if confidence is not None:
            if (confidence.shape != labels.shape or not np.isfinite(confidence).all()
                    or confidence.min() < 0 or confidence.max() > 1):
                raise ValueError("confidence_mask must match the image and contain finite weights in [0, 1]")
            if confidence.min() == 1:
                confidence = None
        count = cv2.countNonZero(labels)
        enough_buildings = count >= max(1, config.min_frame_fraction * labels.size)
        reliable_buildings = enough_buildings and (
            confidence is None or self._reliable_observation(labels, confidence))
        if self.roads is None and not reliable_buildings:
            reason = ("insufficient reliable observations" if enough_buildings
                      else "insufficient visible buildings")
            return Estimate(False, reason, building_coverage=count / labels.size)
        if timings is not None:
            road_start = perf_counter()
        observation = self._observation(frame, labels, confidence, count, reliable_buildings)
        if timings is not None and self.roads is not None:
            timings["road_ms"] = (perf_counter() - road_start) * 1000
        if on_roads is not None and observation.road is not None:
            on_roads(observation.road)
        if not observation.colors and observation.building_weight == 1:
            return self._with_evidence(Estimate(False, "insufficient reliable road geometry or buildings"),
                                       observation)
        if config.auto_scale and recovery_hint is not None and prior_xy is None and diagnostics is None:
            recovered = self._recover_near_prior(observation, recovery_hint, radius_m, yaw_radius_deg)
            if recovered is not None:
                return replace(recovered, recovered_locally=True)
        results = self._search_observation(observation, prior_xy, radius_m, prior_yaw, yaw_radius_deg,
                                           prior_span_m, diagnostics, prefer_tracking=prefer_tracking)
        # Keep a strong building match unchanged. A large but ambiguous patch
        # (for example, one repeated building) can still benefit from roads.
        if (config.matching_mode == "adaptive" and observation.building_weight == 1
                and results.reason in ("ambiguous pose", "low match score")
                and self._road_is_reliable(observation.road, confidence)):
            rescue_diagnostics = MatchDiagnostics() if diagnostics is not None else None
            rescued = self._search_observation(replace(observation, building_weight=0.72),
                                               prior_xy, radius_m, prior_yaw, yaw_radius_deg,
                                               prior_span_m, rescue_diagnostics)
            if rescued.success:
                if diagnostics is not None:
                    diagnostics.coarse_by_span.update(rescue_diagnostics.coarse_by_span)
                    diagnostics.verified_by_span.update(rescue_diagnostics.verified_by_span)
                    diagnostics.candidates = rescue_diagnostics.candidates
                    diagnostics.runner_up = rescue_diagnostics.runner_up
                return rescued
        return results

    def _search_observation(self, observation, prior_xy, radius_m, prior_yaw, yaw_radius_deg,
                            prior_span_m, diagnostics, *, prefer_tracking=False):
        config = self.search
        radius_m = config.local_radius_m if radius_m is None else radius_m
        bounds = self._search_bounds(prior_xy, radius_m)
        if bounds is None:
            return self._with_evidence(Estimate(False, "search area outside map"), observation)
        if config.auto_scale:
            if (prefer_tracking and diagnostics is None and prior_xy is not None
                    and prior_yaw is not None and prior_span_m is not None):
                fast = self._tracking_window(observation, bounds, prior_xy, radius_m, prior_yaw,
                                             yaw_radius_deg, prior_span_m)
                if fast is not None:
                    return fast
            grids = self._automatic_grids(observation, bounds, prior_xy, radius_m, prior_yaw,
                                          yaw_radius_deg, prior_span_m, diagnostics)
            result = self._decide(grids, diagnostics, observation)
            if result.success and prior_xy is not None and prior_span_m is not None:
                lower = min(grid.span_m for grid in grids)
                upper = max(grid.span_m for grid in grids)
                # An edge maximum inside the allowed band can be the flank of a better
                # wider or narrower fit. Let the tracker retry without the width prior.
                at_lower_limit = (result.span_m == lower
                                  and lower > config.nominal_span_m * config.scale_min)
                at_upper_limit = (result.span_m == upper
                                  and upper < config.nominal_span_m * config.scale_max)
                if at_lower_limit or at_upper_limit:
                    return replace(result, success=False, reason="span reaches local search limit",
                                   x_m=None, y_m=None, yaw_deg=None, span_m=None,
                                   observation_source=None)
            return result

        spans = [config.nominal_span_m * factor for factor in config.scale_factors]
        if prior_span_m is not None:
            spans.sort()
            closest = min(range(len(spans)), key=lambda i: abs(spans[i] - prior_span_m))
            spans = spans[max(0, closest - 1):closest + 2]
        headings = _headings(config.yaw_step_deg, prior_yaw, yaw_radius_deg)
        grids = self._score_grids(observation, spans, headings, bounds, prior_xy, radius_m)
        return self._decide(grids, diagnostics, observation)

    def _recover_near_prior(self, observation, hint, radius_m, yaw_radius):
        previous = hint.previous
        x, y, yaw, span = previous.x_m, previous.y_m, previous.yaw_deg, previous.span_m
        if not previous.success or None in (x, y, yaw, span):
            return None
        prior = (x, y)
        radius = 0.13 if radius_m is None else radius_m
        if hint.reconnected:
            radius = max(self.search.local_radius_m, 1.5 * radius)
            result = self._search_observation(observation, prior, radius, yaw, yaw_radius, span, None)
            if (self._strong_local_match(observation, result)
                    and math.dist((result.x_m, result.y_m), prior) < 0.85 * radius
                    and angle_error(result.yaw_deg, yaw) < 0.85 * yaw_radius):
                return result
            return None
        bounds = self._search_bounds(prior, radius)
        if bounds is None:
            return None
        return self._tracking_window(observation, bounds, prior, radius, yaw, yaw_radius, span)

    @staticmethod
    def _strong_local_match(observation, result):
        weight = observation.building_weight
        score_floor = 0.46 if weight == 1 else 0.59 if weight == 0 else 0.55
        margin_floor = 0.14 if weight == 0 else 0.11
        return result.success and result.score >= score_floor and result.margin >= margin_floor

    def _tracking_window(self, observation, bounds, prior_xy, radius_m, prior_yaw, yaw_radius, prior_span):
        """Verify a recent pose before the full local yaw and span sweep."""
        config = self.search
        yaw_step = min(yaw_radius, config.yaw_step_deg)
        headings = (prior_yaw + np.array([-yaw_step, 0, yaw_step])) % 360
        spans = prior_span * np.array([1 / 1.04, 1, 1.04])
        if spans[0] < config.nominal_span_m * config.scale_min or spans[-1] > config.nominal_span_m * config.scale_max:
            return None
        grids = self._score_grids(observation, spans, headings, bounds, prior_xy, radius_m)
        result = self._decide(grids, None, observation)
        # The wider sweep checks alternatives outside the recent yaw/span window.
        if (self._strong_local_match(observation, result)
                and angle_error(result.yaw_deg, prior_yaw) < 1e-6 and result.span_m == prior_span
                and math.dist((result.x_m, result.y_m), prior_xy) < 0.55 * radius_m):
            return replace(result, tracking_window=True)
        return None

    def _observation(self, frame, labels, confidence, count, reliable_buildings):
        coverage = (count / labels.size if confidence is None else
                    float(cv2.mean(confidence * (labels > 0))[0]))
        road = detect_roads(frame, labels, confidence) if self.roads is not None else None
        reliable_road = self._road_is_reliable(road, confidence)
        mode = self.search.matching_mode
        weight = 1.0
        if mode == "road":
            reliable_buildings = False
        if reliable_road:
            if not reliable_buildings:
                if confidence is not None:
                    confidence = road_match_confidence(confidence)
                if self._road_is_reliable(road, confidence):
                    weight = 0.0
            elif mode == "fixed":
                weight = 0.65
            elif coverage < 0.16:
                building_evidence = coverage / 0.11
                road_evidence = min(1.0, road.coverage / 0.14) * min(1.0, road.direction_diversity / 0.25)
                weight = float(np.clip(building_evidence / (building_evidence + road_evidence), 0.30, 0.90))
        colors = ()
        if reliable_buildings:
            counts = cv2.calcHist([labels], [0], None, [256], [0, 256]).ravel()
            colors = tuple(map(int, np.flatnonzero(counts[1:]) + 1))
        return _Observation(labels, confidence, colors, road, weight, coverage)

    def _road_is_reliable(self, road, confidence):
        return road is not None and road.informative and (
            confidence is None or cv2.mean(confidence)[0] >= self.search.min_effective_fraction)

    @staticmethod
    def _with_evidence(result, observation, building_score=None, road_score=None):
        return replace(result, observation_source=observation.source if result.success else None,
                       building_score=building_score, road_score=road_score,
                       building_coverage=observation.building_coverage,
                       road_coverage=None if observation.road is None else observation.road.coverage,
                       building_weight=(observation.building_weight
                                        if observation.colors or observation.building_weight < 1 else None))

    def _reliable_observation(self, labels, confidence):
        effective = float(confidence.mean())
        if effective < self.search.min_effective_fraction:
            return False
        mass = np.bincount(labels.ravel(), weights=confidence.ravel(), minlength=1)[1:]
        if mass.sum() < max(1, self.search.min_frame_fraction * labels.size):
            return False
        # With little usable area, a lone weak color is especially easy to match by chance.
        return effective >= 0.55 or np.count_nonzero(mass >= labels.size * 0.0025) >= 2

    def _score_grids(self, observation, spans, headings, bounds, prior_xy, radius_m, *,
                     min_foreground=None):
        """Correlate the same color IoU templates at either map resolution."""
        config = self.search
        labels, confidence = observation.labels, observation.confidence
        height, width = labels.shape
        min_foreground = config.min_foreground if min_foreground is None else min_foreground
        x0, y0, x1, y1 = bounds
        radius = math.ceil(math.hypot(width, height) / width / 2
                           * config.pixels_per_m * max(spans)) + 1
        padded, padded_foreground = self._padded_map(radius)
        area = padded[y0:y1 + 2 * radius, x0:x1 + 2 * radius]
        foreground = padded_foreground[y0:y1 + 2 * radius, x0:x1 + 2 * radius]
        use_road = observation.building_weight < 1
        templates = labels
        if use_road:
            padded_roads, padded_edges = self._padded_roads(radius)
            patch = np.s_[y0:y1 + 2 * radius, x0:x1 + 2 * radius]
            road_area, edge_area = padded_roads[patch], padded_edges[patch]
            road_mask = road_area.astype(np.uint8)
            road_rect = cv2.boundingRect(road_mask)
            road_layers = {1: (road_mask if confidence is None else road_area, road_rect)}
            channels = (labels,) if observation.colors else ()
            channels += (observation.road.region, observation.road.edges)
            templates = cv2.merge(channels)
        layers = {}
        for label in observation.colors:
            if label not in self._map_color_bounds:
                continue
            left, top, width_px, height_px = self._map_color_bounds[label]
            if (left + radius >= x1 + 2 * radius or top + radius >= y1 + 2 * radius
                    or left + width_px + radius <= x0 or top + height_px + radius <= y0):
                continue
            mask = cv2.compare(area, label, cv2.CMP_EQ)
            rectangle = cv2.boundingRect(mask)
            if not rectangle[2]:
                continue
            _, plane = cv2.threshold(mask, 0, 1, cv2.THRESH_BINARY)
            layers[label] = (plane if confidence is None else plane.astype(np.float32), rectangle)
        outside = None
        if prior_xy is not None:
            u, v = self.raster.xy_to_uv(prior_xy)
            dx = np.arange(x0, x1) - u
            dy = np.arange(y0, y1) - v
            outside = dy[:, None] ** 2 + dx ** 2 > (radius_m * config.pixels_per_m) ** 2

        for span in spans:
            scale = span * config.pixels_per_m / width
            for yaw in headings:
                transform, size, footprint = _template_geometry(width, height, radius, float(yaw), scale)
                rotated = cv2.warpAffine(templates, transform, size, flags=cv2.INTER_NEAREST)
                weights = (None if confidence is None else
                           cv2.warpAffine(confidence, transform, size, flags=cv2.INTER_NEAREST))
                if not use_road:
                    count = _mass(rotated, weights)
                    if count < min_foreground:
                        continue
                    scores = _color_iou(foreground, layers, rotated, footprint, count, weights=weights)
                    building_scores, road_scores = scores, None
                else:
                    planes = cv2.split(rotated)
                    road, edges = planes[-2:]
                    count = _mass(road, weights)
                    if count < max(3, min_foreground * 0.42):
                        continue
                    iou = _color_iou(road_area, road_layers, road, footprint, count, weights=weights)
                    proximity = _edge_agreement(edge_area, edges, weights)
                    road_scores = iou * (0.86 + 0.14 * proximity)
                    building_scores = None
                    scores = road_scores.copy() if outside is not None else road_scores
                    if observation.colors:
                        building = planes[0]
                        count = _mass(building, weights)
                        if count < max(2, min_foreground * 0.26):
                            continue
                        building_scores = _color_iou(foreground, layers, building, footprint,
                                                     count, weights=weights)
                        weight = observation.building_weight
                        scores = weight * building_scores + (1 - weight) * road_scores
                if outside is not None:
                    scores[outside] = -1
                yield _ScoreGrid(float(yaw), float(span), bounds, scores, building_scores, road_scores)

    def _automatic_grids(self, observation, bounds, prior_xy, radius_m, prior_yaw, yaw_radius, prior_span,
                         diagnostics):
        config = self.search
        minimum, maximum = (config.nominal_span_m * factor
                            for factor in (config.scale_min, config.scale_max))
        if prior_xy is not None and prior_span is not None:
            # A tracking observation usually needs only a small change from the last accepted width.
            spans = np.unique(np.clip(prior_span * np.array([0.92, 1, 1.08]), minimum, maximum))
            headings = _headings(config.yaw_step_deg, prior_yaw, yaw_radius)
            grids = list(self._score_grids(observation, spans, headings, bounds, prior_xy, radius_m))
            if not grids:
                return grids
            best = max((grid.peak() for grid in grids), key=lambda peak: peak[0])
            if best[0] > 0:
                grids.extend(self._refine(observation, self._candidate(best), bounds, prior_xy, radius_m,
                                          minimum, maximum, math.log(1.08) / 2,
                                          config.yaw_step_deg / 2, 0.05, 3,
                                          prior_yaw=prior_yaw, yaw_radius=yaw_radius))
            return grids

        spans = np.geomspace(minimum, maximum, config.scale_steps)
        yaw_step = min(30, config.yaw_step_deg * 2)
        headings = _headings(yaw_step, prior_yaw, yaw_radius)
        seeds = self._coarse_candidates(observation, spans, headings, prior_xy, radius_m, diagnostics)
        log_step = math.log(config.scale_max / config.scale_min) / (config.scale_steps - 1)
        # Preserve separate locations, headings and distinctly sized hypotheses before refinement.
        span_separation = min(math.log(SPAN_SEPARATION_RATIO), max(0.18, 2 * log_step))
        separation = config.separation_m, config.separation_deg, span_separation
        if observation.building_weight < 1:
            # Small road templates can get inflated coarse scores from a few
            # pixels. Reserve two seeds in each part of the log-span range.
            span_bands = np.geomspace(minimum, maximum, 6)
            bands = [[] for _ in range(len(span_bands) - 1)]
            for candidate in seeds:
                band = min(len(bands) - 1, int(np.searchsorted(span_bands, candidate.span_m, side="right")) - 1)
                bands[band].append(candidate)
            reserved = [candidate for band in bands for candidate in _distinct(band, 2, *separation)]
            seeds = _distinct([*reserved, *_distinct(seeds, 6, *separation)], 16, *separation)
        else:
            seeds = _distinct(seeds, 6, *separation)
        coarse_pixel_m = 1 / self.coarse.search.pixels_per_m
        # Cover the separation boundary even when the two centers round to nearby coarse pixels.
        refine_radius = max(3 * coarse_pixel_m, config.separation_m + coarse_pixel_m)
        grids = []
        for seed in seeds:
            grids.extend(self._refine(observation, seed, bounds, prior_xy, radius_m, minimum, maximum,
                                      log_step / 2, yaw_step / 2, refine_radius,
                                      config.scale_refine_steps, prior_yaw=prior_yaw,
                                      yaw_radius=yaw_radius))
        return grids

    def _coarse_candidates(self, observation, spans, headings, prior_xy, radius_m, diagnostics):
        coarse = self.coarse
        bounds = coarse._search_bounds(prior_xy, radius_m)
        if bounds is None:
            return []
        distance = math.ceil(self.search.separation_m * coarse.search.pixels_per_m) - 1
        candidates = []
        for grid in coarse._score_grids(observation, spans, headings, bounds, prior_xy, radius_m,
                                        min_foreground=max(3, math.ceil(self.search.min_foreground / 4))):
            best = coarse._candidate(grid.peak())
            if diagnostics is not None:
                _record_peak(diagnostics.coarse_by_span, best)
            if best.score <= 0:
                continue
            candidates.append(best)
            # One rotation and width can score well at more than one building cluster.
            remaining = grid.scores.copy()
            col, row = coarse.raster.xy_to_uv((best.x_m, best.y_m)).round().astype(int)
            cv2.circle(remaining, (col - bounds[0], row - bounds[1]), distance, -1, thickness=-1)
            runner_up = coarse._candidate(replace(grid, scores=remaining).peak())
            if runner_up.score > 0:
                candidates.append(runner_up)
        return candidates

    def _refine(self, observation, seed, bounds, prior_xy, radius_m, minimum, maximum,
                log_half_width, yaw_half_width, center_radius, steps, *, prior_yaw, yaw_radius):
        region = self._search_bounds((seed.x_m, seed.y_m), center_radius)
        if region is None:
            return
        tight = (max(bounds[0], region[0]), max(bounds[1], region[1]),
                 min(bounds[2], region[2]), min(bounds[3], region[3]))
        if tight[0] >= tight[2] or tight[1] >= tight[3]:
            return
        spans = seed.span_m * np.exp(np.linspace(-log_half_width, log_half_width, steps))
        spans = np.unique(np.clip(np.append(spans, seed.span_m), minimum, maximum))
        headings = (seed.yaw_deg + np.linspace(-yaw_half_width, yaw_half_width, steps)) % 360
        if prior_yaw is not None:
            window = max(self.search.yaw_step_deg / 2, yaw_radius)
            offsets = (headings - prior_yaw + 180) % 360 - 180
            headings = np.unique((prior_yaw + np.clip(offsets, -window, window)) % 360)
        yield from self._score_grids(observation, spans, headings, tight, prior_xy, radius_m)

    def _candidate(self, peak):
        score, col, row, yaw, span = peak
        x_m, y_m = self.raster.uv_to_xy((col, row))
        return MatchCandidate(score, float(x_m), float(y_m), yaw, span)

    def _decide(self, grids, diagnostics, observation):
        grids = list(grids)
        if not grids:
            reason = ("insufficient visible buildings" if observation.building_weight == 1
                      else "insufficient foreground at search resolution")
            return self._with_evidence(Estimate(False, reason), observation)
        peaks = [grid.peak() for grid in grids]
        best, grid = max(zip(peaks, grids), key=lambda pair: pair[0][0])
        score, col, row, yaw, span = best
        config = self.search
        index = row - grid.bounds[1], col - grid.bounds[0]
        building = None if grid.building_scores is None else max(0.0, float(grid.building_scores[index]))
        road = None if grid.road_scores is None else max(0.0, float(grid.road_scores[index]))
        weight = observation.building_weight
        separation_m, separation_deg, span_ratio = config.separation_m, config.separation_deg, SPAN_SEPARATION_RATIO
        min_score, min_margin = config.min_score, config.min_margin
        if weight < 1:
            # Nearby grid cells represent the same pose. Separately consider
            # spatial shifts, rotated near-symmetries, and materially different spans.
            separation_deg = min(separation_deg, 17.5)
            span_ratio = 1.25
            min_score = max(min_score, 0.58 if weight == 0 else 0.48)
            min_margin = max(min_margin, 0.095 if weight == 0 else 0.08)
        if diagnostics is not None:
            candidates = [self._candidate(peak) for peak in peaks]
            for candidate in candidates:
                _record_peak(diagnostics.verified_by_span, candidate)
            diagnostics.candidates = tuple(_distinct(candidates, 8, separation_m, separation_deg,
                                                      math.log(span_ratio)))
        low_score = score < min_score or (0 < weight < 1 and (building < 0.36 or road < 0.36))
        if low_score and diagnostics is None:
            return self._with_evidence(Estimate(False, "low match score", max(0.0, score)),
                                       observation, building, road)
        margin = max(0.0, score - self._alternative_score(grids, col, row, yaw, span, diagnostics,
                                                          separation_m, separation_deg, span_ratio))
        if low_score:
            return self._with_evidence(Estimate(False, "low match score", max(0.0, score), margin),
                                       observation, building, road)
        if margin < min_margin:
            return self._with_evidence(Estimate(False, "ambiguous pose", score, margin),
                                       observation, building, road)
        winner = self._candidate(best)
        return self._with_evidence(
            Estimate(True, "matched", score, margin, winner.x_m, winner.y_m, yaw, span),
            observation, building, road)

    @lru_cache(maxsize=8)
    def _padded_map(self, radius):
        padded = np.pad(self.labels, radius)
        return padded, (padded > 0).astype(np.float32)

    @lru_cache(maxsize=8)
    def _padded_roads(self, radius):
        return np.pad(self.roads.region.astype(np.float32), radius), np.pad(self.roads.edge_similarity, radius)

    def _search_bounds(self, prior_xy, radius_m):
        """Return half-open UV bounds for candidate camera centers on the map."""
        config = self.search
        width = math.floor(self.raster.campus.width_m * config.pixels_per_m) + 1
        height = math.floor(self.raster.campus.height_m * config.pixels_per_m) + 1
        if prior_xy is None:
            return 0, 0, width, height
        u, v = self.raster.xy_to_uv(prior_xy)
        radius = radius_m * config.pixels_per_m
        x0, y0 = max(0, math.ceil(u - radius)), max(0, math.ceil(v - radius))
        x1, y1 = min(width, math.floor(u + radius) + 1), min(height, math.floor(v + radius) + 1)
        return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None

    def _alternative_score(self, grids, col, row, yaw, span, diagnostics,
                           separation_m, separation_deg, span_ratio):
        """Find the best distinct pose, including substantially different spans in auto mode."""
        config = self.search
        separation_sq = (separation_m * config.pixels_per_m) ** 2
        masks = {}
        runner_up = 0.0
        for grid in grids:
            mask = None
            if (angle_error(grid.yaw_deg, yaw) >= separation_deg
                    or (config.auto_scale
                        and max(grid.span_m / span, span / grid.span_m) >= span_ratio)):
                candidates = grid.scores
            else:
                if grid.bounds not in masks:
                    x0, y0, x1, y1 = grid.bounds
                    v, u = np.ogrid[y0:y1, x0:x1]
                    masks[grid.bounds] = (u - col) ** 2 + (v - row) ** 2 >= separation_sq
                mask = masks[grid.bounds]
                candidates = grid.scores[mask]
            score = float(candidates.max(initial=0))
            if score > runner_up:
                runner_up = score
                if diagnostics is not None:
                    alternative = grid if mask is None else replace(grid, scores=np.where(mask, grid.scores, -1))
                    diagnostics.runner_up = self._candidate(alternative.peak())
        return runner_up


def _headings(step, prior_yaw, window):
    headings = np.arange(0, 360, step)
    if prior_yaw is None:
        return headings
    return [yaw for yaw in headings if angle_error(yaw, prior_yaw) <= max(step / 2, window)]


def _record_peak(by_span, candidate):
    best = by_span.get(candidate.span_m)
    if best is None or candidate.score > best.score:
        by_span[candidate.span_m] = candidate


def _distinct(candidates, limit, distance, angle, log_span):
    kept = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if any(math.hypot(candidate.x_m - other.x_m, candidate.y_m - other.y_m) < distance
               and angle_error(candidate.yaw_deg, other.yaw_deg) < angle
               and abs(math.log(candidate.span_m / other.span_m)) < log_span for other in kept):
            continue
        kept.append(candidate)
        if len(kept) == limit:
            break
    return kept


@lru_cache(maxsize=512)
def _template_geometry(width, height, radius, yaw, scale):
    center = image_center(width, height)
    transform = cv2.getRotationMatrix2D(center, yaw, scale)
    transform[:, 2] += np.asarray((radius, radius)) - center
    size = (2 * radius + 1,) * 2
    visible = cv2.warpAffine(np.ones((height, width), np.uint8), transform, size,
                             flags=cv2.INTER_NEAREST)
    x, y, w, h = cv2.boundingRect(visible)
    footprint = (x, y, visible[y:y + h, x:x + w].astype(np.float32))
    return transform, size, footprint


def _mass(mask, weights):
    return np.count_nonzero(mask) if weights is None else weights[mask > 0].sum()


def _edge_agreement(edge_area, edges, weights):
    x, y, w, h = cv2.boundingRect(edges)
    if not w:
        return 0.0
    rows, cols = np.subtract(edge_area.shape, edges.shape) + 1
    patch = np.s_[y:y + h, x:x + w]
    template = edges[patch].astype(np.float32)
    if weights is not None:
        template *= weights[patch]
    mass = float(template.sum())
    if not mass:
        return 0.0
    plane = edge_area[y:y + rows + h - 1, x:x + cols + w - 1]
    similarity = cv2.matchTemplate(plane, template, cv2.TM_CCORR)
    return np.clip(similarity / mass, 0, 1)


def _color_iou(foreground, layers, rotated, footprint, observed_count, *, weights=None):
    """Return color-aware intersection over union for every template center."""
    x, y, visible = footprint
    h, w = visible.shape
    if weights is not None:
        visible = weights[y:y + h, x:x + w]
    fh, fw = foreground.shape
    vh, vw = rotated.shape
    intersection = np.zeros((fh - vh + 1, fw - vw + 1), dtype=np.float32)
    left, top = intersection.shape[1], intersection.shape[0]
    right, bottom = 0, 0
    for label, (map_mask, (mx, my, mw, mh)) in layers.items():
        if not mw:
            continue
        mask = cv2.compare(rotated, int(label), cv2.CMP_EQ)
        tx, ty, tw, th = cv2.boundingRect(mask)
        if not tw:
            continue
        # Only translations whose map and observed bounding boxes overlap can score.
        x0, y0 = max(0, mx - tx - tw + 1), max(0, my - ty - th + 1)
        x1 = min(intersection.shape[1], mx + mw - tx)
        y1 = min(intersection.shape[0], my + mh - ty)
        if x0 < x1 and y0 < y1:
            region = map_mask[y0 + ty:y1 + ty + th - 1, x0 + tx:x1 + tx + tw - 1]
            patch = np.s_[ty:ty + th, tx:tx + tw]
            if weights is None:
                _, template = cv2.threshold(mask[patch], 0, 1, cv2.THRESH_BINARY)
            else:
                template = cv2.copyTo(weights[patch], mask[patch])
            intersection[y0:y1, x0:x1] += cv2.matchTemplate(region, template, cv2.TM_CCORR)
            left, top = min(left, x0), min(top, y0)
            right, bottom = max(right, x1), max(bottom, y1)
    # Without same-color overlap the score is already zero, regardless of the union.
    result = intersection.astype(np.result_type(intersection, observed_count), copy=False)
    if right == 0:
        return result
    region = foreground[top + y:bottom + y + h - 1, left + x:right + x + w - 1]
    predicted = cv2.matchTemplate(region, visible, cv2.TM_CCORR)
    scores = intersection[top:bottom, left:right]
    # Only the unweighted correlations are integral counts.
    if weights is None:
        np.rint(predicted, out=predicted)
        np.rint(scores, out=scores)
    np.maximum(predicted, 0, out=predicted)
    np.clip(scores, 0, np.minimum(predicted, observed_count), out=scores)
    predicted = predicted.astype(result.dtype, copy=False)
    predicted += observed_count
    predicted -= scores
    np.divide(scores, predicted, out=result[top:bottom, left:right])
    return result
