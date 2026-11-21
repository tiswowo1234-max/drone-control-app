"""Use only BGR frames, capture times, and optional externally supplied priors."""

from dataclasses import dataclass, replace
from enum import Enum
import math
from time import perf_counter

from .kalman_filter import FilterConfig, PositionFilter
from .localization_fusion import LocalizationObservation, from_map, observations_agree, select_observation, yaw_difference
from .map_matching import Estimate, MapMatcher, RecoveryHint


class TrackingState(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    GLOBAL_SEARCH = "GLOBAL_SEARCH"
    TRACKING = "TRACKING"
    PREDICT_ONLY = "PREDICT_ONLY"
    RELOCALIZING = "RELOCALIZING"


@dataclass(frozen=True)
class TrackingConfig:
    max_misses: int = 3
    retry_interval_s: float = 0.6
    max_sigma_m: float = 0.28
    min_radius_m: float = 0.13
    max_radius_m: float = 0.40
    yaw_radius_deg: float = 25.0
    yaw_growth_deg_s: float = 35.0
    innovation_limit: float = 9.21
    max_speed_mps: float = 0.9
    position_slack_m: float = 0.06
    max_prediction_s: float = 1.2
    map_yaw_sigma_deg: float = 12.0
    max_map_center_offset_m: float = 0.05
    tag_recovery_interval_s: float = 0.65
    tag_recovery_radius_m: float = 0.12
    tag_recovery_yaw_deg: float = 25.0

    def __post_init__(self):
        values = (self.retry_interval_s, self.max_sigma_m, self.min_radius_m, self.max_radius_m,
                  self.yaw_radius_deg, self.yaw_growth_deg_s, self.innovation_limit,
                  self.max_speed_mps, self.position_slack_m, self.max_prediction_s,
                  self.map_yaw_sigma_deg, self.max_map_center_offset_m, self.tag_recovery_interval_s,
                  self.tag_recovery_radius_m, self.tag_recovery_yaw_deg)
        if self.max_misses < 1 or not all(math.isfinite(v) and v > 0 for v in values):
            raise ValueError("tracking limits must be positive and finite")
        if self.max_radius_m < self.min_radius_m:
            raise ValueError("max_radius_m must be at least min_radius_m")


@dataclass(frozen=True)
class TrackingResult:
    timestamp_s: float
    state: TrackingState
    attempt: str
    measurement: Estimate | None
    measurement_used: bool
    reason: str
    xy_m: tuple[float, float] | None
    velocity_mps: tuple[float, float] | None
    yaw_deg: float | None
    sigma_m: float | None
    relocalized: bool
    match_ms: float
    filter_ms: float
    total_ms: float
    span_m: float | None = None
    observation_source: str | None = None
    map_observation: LocalizationObservation | None = None
    tag_observation: LocalizationObservation | None = None
    correction: LocalizationObservation | None = None
    fusion_ms: float | None = None


class TrackingManager:
    def __init__(self, matcher: MapMatcher, config: TrackingConfig = TrackingConfig(),
                 filter_config: FilterConfig = FilterConfig(), *, initial_xy=None):
        self.matcher = matcher
        self.config = config
        self.filter_config = filter_config
        self.filter = PositionFilter(initial_xy, filter_config) if initial_xy is not None else None
        self.state = TrackingState.UNINITIALIZED
        self.timestamp = None
        self.started_at = None
        self.last_global = None
        self.last_observed = None
        self.yaw = None
        self.span = None
        self.misses = 0
        self._local_updates = 0
        self._next_tracking_trial = 2
        self.last_frame_id = None
        self._tag_recovery = None

    def step(self, frame, timestamp_s: float, *, confidence_mask=None,
             timings=None, on_labels=None, on_roads=None, recovery_hint: RecoveryHint | None = None,
             frame_id=None, tag_observation=None, map_enabled=True, prefer_tag=False) -> TrackingResult:
        """Process one frame at a strictly increasing capture timestamp in seconds."""
        started = perf_counter()
        if not math.isfinite(timestamp_s) or (self.timestamp is not None and timestamp_s <= self.timestamp):
            raise ValueError("capture timestamps must be finite and strictly increasing")
        if frame_id is not None:
            if self.last_frame_id is not None and frame_id <= self.last_frame_id:
                raise ValueError("frame IDs must be strictly increasing")
            self.last_frame_id = frame_id
        previous_time, self.timestamp = self.timestamp, timestamp_s
        if self.started_at is None:
            self.started_at = timestamp_s
        filter_time = perf_counter()
        if self.filter is not None and previous_time is not None:
            self.filter.predict(timestamp_s - previous_time)
        if tag_observation is not None and (
                frame_id is None or (tag_observation.frame_id, tag_observation.timestamp) != (frame_id, timestamp_s)):
            tag_observation = tag_observation.reject("tag observation belongs to another frame")
        skip_map = (prefer_tag and tag_observation is not None and tag_observation.accepted
                    and not tag_observation.approximate and self._absolute_plausible(tag_observation, timestamp_s))
        filter_ms = (perf_counter() - filter_time) * 1000

        relocalizing = self.state == TrackingState.RELOCALIZING
        attempt = "local"
        measurement = None
        search_start = perf_counter()
        options = {} if confidence_mask is None else {"confidence_mask": confidence_mask}
        if timings is not None:
            options["timings"] = timings
        if on_labels is not None:
            options["on_labels"] = on_labels
        if on_roads is not None:
            options["on_roads"] = on_roads
        if not map_enabled or skip_map:
            attempt = "none"
        elif self.filter is None or relocalizing:
            if self.last_global is None or timestamp_s - self.last_global >= self.config.retry_interval_s - 1e-9:
                attempt = "global"
                if self.filter is None and recovery_hint is not None:
                    options["recovery_hint"] = recovery_hint
                    options["radius_m"] = self.config.min_radius_m
                measurement = self.matcher.estimate(frame, **options)
                if measurement.recovered_locally:
                    attempt = "local"
                else:
                    self.last_global = timestamp_s
            else:
                attempt = "none"
        else:
            sigma = self.filter.position_sigma_m
            radius = min(self.config.max_radius_m, max(self.config.min_radius_m, 3 * sigma))
            age = 0 if self.last_observed is None else timestamp_s - self.last_observed[0]
            yaw_window = self.config.yaw_radius_deg + self.config.yaw_growth_deg_s * age
            # Refresh the full local search periodically to recheck yaw/span alternatives.
            if (self._local_updates >= self._next_tracking_trial and self._local_updates % 8 != 0
                    and age <= 0.35 and radius <= 0.16):
                options["prefer_tracking"] = True
            # A miss releases the width prior. The auto matcher then searches its full
            # span range locally before the state machine releases the position prior.
            measurement = self.matcher.estimate(
                frame, self.filter.position, radius_m=radius, prior_yaw=self.yaw,
                yaw_radius_deg=yaw_window, prior_span_m=self.span if self.misses == 0 else None,
                **options,
            )
        match_ms = (perf_counter() - search_start) * 1000 if measurement is not None else 0.0

        filter_time = perf_counter()
        reason = measurement.reason if measurement is not None else "waiting for global retry"
        used = False
        map_observation = None
        if frame_id is not None and measurement is not None:
            map_observation = from_map(measurement, frame_id, timestamp_s, self.filter_config.measurement_sigma_m,
                                       self.config.map_yaw_sigma_deg)
        correction = None
        fusion_ms = None
        if tag_observation is not None:
            fusion_start = perf_counter()
            pending = self._tag_recovery
            self._tag_recovery = None
            recover = False
            tag_plausible = tag_observation.accepted and self._absolute_plausible(tag_observation, timestamp_s)
            if tag_observation.accepted and relocalizing and not tag_plausible:
                if (pending is not None and 0 < timestamp_s - pending.timestamp <= self.config.tag_recovery_interval_s
                        and math.dist(tag_observation.xy, pending.xy) <= self.config.tag_recovery_radius_m
                        and abs(yaw_difference(tag_observation.yaw_deg, pending.yaw_deg)) <= self.config.tag_recovery_yaw_deg):
                    recover = True
                else:
                    self._tag_recovery = tag_observation
                    if (not tag_observation.approximate and map_observation is not None
                            and map_observation.accepted and attempt == "global"
                            and not observations_agree(map_observation, tag_observation)):
                        map_observation = map_observation.reject("global match conflicts with pending AprilTag recovery")
                    tag_observation = tag_observation.reject("awaiting a second consistent AprilTag recovery frame")
            if (tag_plausible or recover) and map_observation is not None and map_observation.accepted:
                offset = tag_observation.quality_metrics.get("image_center_offset_m")
                if offset is not None and math.hypot(*offset) <= self.config.max_map_center_offset_m:
                    map_observation = replace(
                        map_observation, x_m=map_observation.x_m - offset[0], y_m=map_observation.y_m - offset[1],
                        quality_metrics={**map_observation.quality_metrics, "raw_image_center_xy_m": map_observation.xy},
                    )
                elif "image_center_offset_m" in tag_observation.quality_metrics:
                    map_observation = map_observation.reject("map image center differs from camera center under tilt")

            def gate(observation):
                if observation.source == "MAP":
                    return attempt == "global" or self.filter is None or self._plausible(observation.xy, timestamp_s)
                if observation.source == "APRILTAG":
                    return recover or tag_plausible
                return recover or self._absolute_plausible(observation, timestamp_s)

            correction, map_observation, tag_observation, reason = select_observation(
                map_observation, tag_observation, gate, confirmed_tag_recovery=recover)
            fusion_ms = (perf_counter() - fusion_start) * 1000
            if correction is not None:
                xy = correction.xy
                is_map = correction.source == "MAP"
                covariance = None if is_map else correction.covariance
                if self.filter is None or (is_map and attempt == "global") or (recover and not is_map):
                    self.filter = PositionFilter(xy, self.filter_config, position_covariance=covariance)
                else:
                    self.filter.update(xy, covariance)
                used = True
        elif measurement is not None and measurement.success:
            self._tag_recovery = None
            xy = (measurement.x_m, measurement.y_m)
            if attempt == "global" or self.filter is None:
                self.filter = PositionFilter(xy, self.filter_config)
                used = True
            elif self._plausible(xy, timestamp_s):
                self.filter.update(xy)
                used = True
            else:
                reason = "measurement rejected by motion gate"
            if map_observation is not None:
                if used:
                    correction = map_observation
                else:
                    map_observation = map_observation.reject(reason)
        else:
            self._tag_recovery = None
            if not map_enabled:
                reason = "no AprilTags detected"

        if used:
            self.state = TrackingState.TRACKING
            self.last_observed = timestamp_s, self.filter.position
            active = correction if correction is not None else measurement
            self.yaw, self.span = active.yaw_deg, active.span_m
            self.misses = 0
            map_used = measurement is not None and (map_observation is None or map_observation.accepted)
            self._local_updates = self._local_updates + 1 if attempt == "local" and map_used else 0
            if map_used and options.get("prefer_tracking") and not measurement.tracking_window:
                # Avoid repeating unsuccessful narrow searches during sustained changes.
                self._next_tracking_trial = self._local_updates + 2
        elif self.filter is None:
            self.state = TrackingState.GLOBAL_SEARCH
        else:
            self._local_updates = 0
            self._next_tracking_trial = 2
            self.misses += 1
            lost = self.misses >= self.config.max_misses or self.filter.position_sigma_m >= self.config.max_sigma_m
            self.state = TrackingState.RELOCALIZING if relocalizing or lost else TrackingState.PREDICT_ONLY

        xy = self.filter.position if self.filter else None
        velocity = self.filter.velocity if self.filter else None
        sigma = self.filter.position_sigma_m if self.filter else None
        yaw, span = self.yaw, self.span
        source = None
        if used:
            self._tag_recovery = None
            if correction is not None and correction.source != "MAP":
                source = correction.source
            else:
                source = measurement.observation_source or "BUILDING"
        elif xy is not None:
            observed_at = self.started_at if self.last_observed is None else self.last_observed[0]
            if sigma >= self.config.max_sigma_m or timestamp_s - observed_at >= self.config.max_prediction_s:
                xy = velocity = yaw = span = None
                self.state = TrackingState.RELOCALIZING
                reason += " / prediction expired"
            else:
                source = "PREDICTION_ONLY"
        filter_ms += (perf_counter() - filter_time) * 1000 - (fusion_ms or 0)
        return TrackingResult(timestamp_s, self.state, attempt, measurement, used, reason, xy, velocity,
                              yaw, sigma, relocalizing and used, match_ms, filter_ms,
                              (perf_counter() - started) * 1000, span, source,
                              map_observation, tag_observation, correction, fusion_ms)

    def _plausible(self, xy, timestamp_s):
        if self.filter.innovation_squared(xy) > self.config.innovation_limit:
            return False
        if self.last_observed is None:
            return True
        observed_time, position = self.last_observed
        limit = self.config.position_slack_m + self.config.max_speed_mps * (timestamp_s - observed_time)
        return math.dist(xy, position) <= limit

    def _absolute_plausible(self, observation, timestamp_s):
        if self.filter is None:
            return True
        if self.filter.innovation_squared(observation.xy, observation.covariance) > self.config.innovation_limit:
            return False
        if self.last_observed is None:
            return True
        observed_time, position = self.last_observed
        age = timestamp_s - observed_time
        limit = self.config.position_slack_m + self.config.max_speed_mps * age
        yaw_limit = self.config.yaw_radius_deg + self.config.yaw_growth_deg_s * age
        return (math.dist(observation.xy, position) <= limit
                and (self.yaw is None or abs(yaw_difference(observation.yaw_deg, self.yaw)) <= yaw_limit))
