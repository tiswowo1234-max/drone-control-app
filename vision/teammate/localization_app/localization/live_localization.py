"""Adapt timestamped frames to the existing global matcher or tracker."""

from dataclasses import dataclass
from time import perf_counter

import cv2
import numpy as np

from .apriltag_detector import TagDetection, detect_tags, make_tag_detector
from .camera_interface import INPUT_GAP_S
from .kalman_filter import FilterConfig
from .lighting_pipeline import diagnostic_panel
from .localization_fusion import LocalizationObservation, from_map
from .map_matching import Estimate, RecoveryHint
from .road_debug import road_panel, road_views
from .road_detection import detect_roads
from .tracking_manager import TrackingConfig, TrackingManager, TrackingResult, TrackingState


@dataclass(frozen=True)
class LightingStats:
    method: str
    shadow_aware: bool
    effective_coverage: float | None
    shadow_ratio: float | None
    preprocess_ms: float
    match_ms: float


@dataclass(frozen=True)
class LiveResult:
    measurement: Estimate | None
    tracking: TrackingResult | None
    processing_ms: float
    frame_aspect: float | None = None
    lighting: LightingStats | None = None
    diagnostic: np.ndarray | None = None
    preprocessing_ms: float | None = None
    lighting_ms: float | None = None
    hsv_ms: float | None = None
    match_mode: str = "none"
    matching_ms: float | None = None
    tracking_ms: float | None = None
    localization_ms: float | None = None
    diagnostic_ms: float | None = None
    projected_xy_m: tuple[float, float] | None = None
    projected_timestamp: float | None = None
    road_ms: float | None = None
    tag_detections: tuple[TagDetection, ...] = ()
    tag_observation: LocalizationObservation | None = None
    tag_detection_ms: float | None = None
    tag_pose_ms: float | None = None
    fusion_ms: float | None = None
    tag_setup: str | None = None
    map_observation: LocalizationObservation | None = None
    localization_mode: str = "map"


class LiveLocalization:
    def __init__(self, matcher, preprocess, *, global_only=False, stale_s=INPUT_GAP_S, lighting=None,
                 road_debug=False, localization_mode="map", tag_localizer=None, map_period_ms=0):
        self.matcher = matcher
        self.preprocess = preprocess
        self.global_only = global_only
        self.stale_s = stale_s
        self.tracker = None
        self.last_input = None
        self._last_observation = None
        self.lighting = lighting
        self.road_debug = road_debug
        self.localization_mode = localization_mode
        self.requested_mode = localization_mode
        self.map_period_s = map_period_ms / 1000
        self.last_map_attempt = None
        self.tag_localizer = tag_localizer
        self.tag_detector = make_tag_detector(tag_localizer.layout.family) if tag_localizer else None
        self.tag_setup = None
        if tag_localizer:
            calibration = "calibrated" if preprocess.config.calibration_path else "AprilTag planar approximation"
            size = (f"tag {tag_localizer.layout.size_m * tag_localizer.scale * 1000:.2f} mm on print"
                    if tag_localizer.scale is not None else "physical print width missing")
            self.tag_setup = f"{calibration} | {size}"

    def request_mode(self, mode):
        """Apply a GUI mode change at the next frame boundary in the worker."""
        if self.tag_localizer is not None and not self.global_only:
            self.requested_mode = mode

    def process(self, frame):
        start = perf_counter()
        requested = self.requested_mode
        if requested != self.localization_mode:
            self.localization_mode = requested
            self.last_input = self._last_observation = None
        tags, tag_observation = (), None
        tag_detection_ms = tag_pose_ms = None
        map_enabled = self.localization_mode != "apriltag"
        show_roads = self.road_debug and map_enabled
        tag_setup = self.tag_setup if self.localization_mode != "map" else None
        calibration = calibration_error = None
        if self.tag_detector is not None and self.localization_mode != "map":
            size = frame.image.shape[1], frame.image.shape[0]
            try:
                calibration = self.preprocess.raw_calibration(size)
            except ValueError as error:
                calibration_error = tag_setup = str(error)
        if self.tag_detector is not None:
            tag_start = perf_counter()
            layout = self.tag_localizer.layout
            tags = detect_tags(frame.image, self.tag_detector, layout.family, frame.frame_id, frame.timestamp)
            tag_detection_ms = (perf_counter() - tag_start) * 1000
            if tags and self.localization_mode != "map":
                pose_start = perf_counter()
                if calibration_error:
                    tag_observation = LocalizationObservation("APRILTAG", frame.frame_id, frame.timestamp).reject(
                        calibration_error)
                else:
                    tag_observation = self.tag_localizer.solve(tags, size, calibration, self.preprocess.image_axes)
                tag_pose_ms = (perf_counter() - pose_start) * 1000
        preprocess_start = perf_counter()
        if not map_enabled:
            original = frame.image
        elif calibration_error:
            original = self.preprocess.apply(frame.image, rectify=False)
        else:
            original = self.preprocess.apply(frame.image)
        preprocessed = perf_counter()
        observation = self.lighting.apply(original) if map_enabled and self.lighting is not None else None
        corrected = perf_counter()
        image = original if observation is None else observation.image
        aspect = image.shape[0] / image.shape[1]
        if not map_enabled and self.preprocess.image_axes[0, 0] == 0:
            aspect = 1 / aspect
        confidence = None if observation is None else observation.confidence
        if tags and map_enabled:
            excluded = layout.paper_mask(tags, frame.image.shape)
            excluded = self.preprocess.transform_mask(excluded, image.shape, rectify=calibration_error is None)
            confidence = np.where(excluded, 0.0, 1.0 if confidence is None else confidence).astype(np.float32)
        search_start = perf_counter()
        tracking = None
        timings = {}
        debug_labels, debug_roads = [], []
        options = {}
        if show_roads or (observation is not None and self.lighting.debug):
            options["on_labels"] = debug_labels.append
        if show_roads:
            options["on_roads"] = debug_roads.append
        if self.global_only:
            if confidence is not None:
                options["confidence_mask"] = confidence
            measurement = self.matcher.estimate(image, timings=timings, **options)
            map_observation = from_map(measurement, frame.frame_id, frame.timestamp,
                                       FilterConfig().measurement_sigma_m, TrackingConfig().map_yaw_sigma_deg)
            mode = "global"
            match_total = (perf_counter() - search_start) * 1000
        else:
            key = frame.connection, frame.image.shape, frame.input_epoch
            recovery_hint = None
            if (self.last_input is None or key != self.last_input[0]
                    or (frame.input_epoch is None and frame.timestamp - self.last_input[1] >= self.stale_s)):
                recovery_hint = self._recent_recovery_hint(frame, original) if map_enabled else None
                self._last_observation = None
                self.tracker = TrackingManager(self.matcher)
                self.last_map_attempt = None
            self.last_input = key, frame.timestamp
            if recovery_hint is not None:
                options["recovery_hint"] = recovery_hint
            prefer_tag = (self.map_period_s > 0 and self.tracker.state == TrackingState.TRACKING
                          and self.last_map_attempt is not None
                          and frame.timestamp - self.last_map_attempt < self.map_period_s)
            tracking = self.tracker.step(image, frame.timestamp, confidence_mask=confidence,
                                         timings=timings, frame_id=frame.frame_id,
                                         tag_observation=tag_observation, map_enabled=map_enabled,
                                         prefer_tag=prefer_tag, **options)
            measurement = tracking.measurement
            if measurement is not None:
                self.last_map_attempt = frame.timestamp
            map_observation = tracking.map_observation
            if (tracking.map_observation is not None and tracking.map_observation.accepted
                    and measurement.span_m is not None):
                thumb = cv2.resize(original, (32, 24), interpolation=cv2.INTER_AREA)
                self._last_observation = frame.timestamp, frame.source, thumb, measurement
            mode, match_total = tracking.attempt, tracking.match_ms
        elapsed = perf_counter()
        hsv_ms = timings.get("hsv_ms")
        road_ms = timings.get("road_ms")
        matching_ms = max(0.0, match_total - (hsv_ms or 0.0) - (road_ms or 0.0)) if mode != "none" else None
        tracking_ms = tracking.filter_ms if tracking else None
        stats = panel = None
        diagnostic_ms = None
        if observation is not None:
            shadow = observation.shadow
            effective = (shadow.effective_coverage if self.lighting.shadow_aware else 1.0) if shadow else None
            stats = LightingStats(self.lighting.corrector.config.method, self.lighting.shadow_aware,
                                  effective, shadow.shadow_ratio if shadow else None,
                                  (search_start - preprocess_start) * 1000,
                                  match_total)
            if self.lighting.debug and not show_roads:
                diagnostic_start = perf_counter()
                panel = diagnostic_panel(original, observation, self.matcher.detector,
                                         self.matcher.detector.label_palette,
                                         labels=debug_labels[0] if debug_labels else None)
                diagnostic_ms = (perf_counter() - diagnostic_start) * 1000
        if show_roads:
            diagnostic_start = perf_counter()
            labels = (debug_labels[0] if debug_labels else
                      self.matcher.detector.detect(image, include_contours=False).labels)
            road = debug_roads[0] if debug_roads else detect_roads(image, labels, confidence)
            accepted = measurement and measurement.success and (
                tracking is None or (tracking.map_observation is not None and tracking.map_observation.accepted))
            projection = (self.matcher.project_roads(image.shape, measurement) if accepted
                          else np.zeros(image.shape[:2], np.uint8))
            panel = road_panel(road_views(original, self.matcher.detector.label_palette, labels, road, projection))
            diagnostic_ms = (perf_counter() - diagnostic_start) * 1000
        projected_xy = projected_at = None
        if tracking is not None and tracking.xy_m is not None and frame.received_at is not None:
            # Output projection leaves the posterior at the observation epoch for the next step.
            projected_at = perf_counter()
            observed_at = frame.received_at if frame.observation_at is None else frame.observation_at
            lag = max(0.0, projected_at - observed_at)
            projected_xy = tuple(x + lag * v for x, v in zip(tracking.xy_m, tracking.velocity_mps))
        return LiveResult(
            measurement, tracking, (perf_counter() - start) * 1000, aspect, stats, panel,
            preprocessing_ms=(preprocessed - preprocess_start) * 1000,
            lighting_ms=(corrected - preprocessed) * 1000 if observation is not None else None,
            hsv_ms=hsv_ms, match_mode=mode, matching_ms=matching_ms, tracking_ms=tracking_ms,
            localization_ms=(elapsed - start) * 1000,
            diagnostic_ms=diagnostic_ms, projected_xy_m=projected_xy,
            projected_timestamp=projected_at,
            road_ms=road_ms,
            tag_detections=tags, tag_observation=tracking.tag_observation if tracking else tag_observation,
            tag_detection_ms=tag_detection_ms, tag_pose_ms=tag_pose_ms,
            fusion_ms=tracking.fusion_ms if tracking else None, tag_setup=tag_setup,
            map_observation=map_observation, localization_mode=self.localization_mode,
        )

    def _recent_recovery_hint(self, frame, image):
        """Require a recent observation from the same camera and image geometry."""
        if self.last_input is None or self._last_observation is None:
            return None
        connection, shape, _ = self.last_input[0]
        observed_at, source, thumbnail, measurement = self._last_observation
        if frame.source != source or frame.image.shape != shape:
            return None
        reconnected = frame.connection != connection
        max_age = self.stale_s + (2 if reconnected else 1)
        if not 0 < frame.timestamp - observed_at <= max_age:
            return None
        current = cv2.resize(image, (32, 24), interpolation=cv2.INTER_AREA)
        difference = cv2.norm(thumbnail, current, cv2.NORM_L1) / thumbnail.size
        return RecoveryHint(measurement, reconnected) if difference <= (45 if reconnected else 12) else None
