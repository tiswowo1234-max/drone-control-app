"""Run one consumer against the newest frame and measure per-frame outcomes."""

from collections import Counter, defaultdict, deque
import csv
from dataclasses import asdict, dataclass
import json
import logging
import math
from pathlib import Path
from threading import Event, Lock, Thread
from time import perf_counter

import numpy as np

from .camera_interface import CameraFrame
from .live_localization import LiveResult
from .tracking_manager import TrackingState


@dataclass(frozen=True)
class FrameEvent:
    frame: CameraFrame
    status: str
    selected_at: float
    skipped: int
    started_at: float | None = None
    ended_at: float | None = None
    result: LiveResult | None = None

    @property
    def continuity(self):
        return self.frame.connection, self.frame.input_epoch, self.frame.image.shape

    @property
    def frame_age_ms(self):
        if self.frame.received_at is None:
            return None
        return (self.selected_at - self.frame.received_at) * 1000

    @property
    def processing_ms(self):
        if self.started_at is not None and self.ended_at is not None:
            return (self.ended_at - self.started_at) * 1000
        return None

    @property
    def receive_to_result_ms(self):
        if self.ended_at is not None and self.frame.received_at is not None:
            return (self.ended_at - self.frame.received_at) * 1000
        return None

    def stages(self):
        stages = {"decode": self.frame.decode_ms, "jpeg_receive": self.frame.jpeg_receive_ms}
        if self.frame.jpeg_receive_ms is not None:
            stages["jpeg_receive_decode"] = self.frame.jpeg_receive_ms + self.frame.decode_ms
        if self.frame.observation_at is not None and self.frame.received_at is not None:
            stages["replay_delivery_lag"] = (self.frame.received_at - self.frame.observation_at) * 1000
        if self.result is not None:
            stages.update(preprocessing=self.result.preprocessing_ms, hsv=self.result.hsv_ms,
                          road=self.result.road_ms,
                          lighting=self.result.lighting_ms, tracking=self.result.tracking_ms,
                          localization=self.result.localization_ms, diagnostic=self.result.diagnostic_ms,
                          tag_detection=self.result.tag_detection_ms, tag_pose=self.result.tag_pose_ms,
                          fusion=self.result.fusion_ms)
            if self.result.match_mode in ("global", "local"):
                stages[f"{self.result.match_mode}_matching"] = self.result.matching_ms
        return {name: value for name, value in stages.items() if value is not None}


class FrameScheduler:
    """Select a fresh frame after each start-to-start wait, without queuing work."""

    def __init__(self, source, process, *, period_ms=0, max_frame_age_ms=300,
                 on_event=None, recorder=None, after_id=0, deadline=None):
        if not math.isfinite(period_ms) or period_ms < 0:
            raise ValueError("localization period must be nonnegative and finite")
        if not math.isfinite(max_frame_age_ms) or max_frame_age_ms <= 0:
            raise ValueError("maximum frame age must be positive and finite")
        self.source, self.process = source, process
        self.period_s = period_ms / 1000
        self.max_frame_age_ms = max_frame_age_ms
        self.on_event, self.recorder = on_event, recorder
        self.after_id, self.deadline = after_id, deadline
        self.stop = Event()
        self.done = Event()
        self.worker = Thread(target=self._run, name="frame-localization", daemon=True)
        self.latest = None
        self.latest_processed = None
        self.latest_attempt = None
        self.error = ""

    def start(self):
        self.worker.start()

    def close(self):
        self.stop.set()
        if self.worker.ident is not None:
            self.worker.join()

    def _run(self):
        after_id = self.after_id
        deadline = self.deadline if self.deadline is not None else math.inf
        next_start = 0.0
        try:
            while not self.stop.is_set():
                now = perf_counter()
                due = min(next_start, deadline)
                if now < due:
                    # A sub-millisecond timeout can round down to an immediate
                    # return on Windows. Still recheck the clock before selecting.
                    self.stop.wait(max(0.001, due - now))
                    continue
                if now >= deadline:
                    break
                frame = self.source.wait_latest(after_id, timeout=min(0.05, deadline - now))
                if frame is None:
                    if self.source.finished:
                        break
                    continue
                if self.stop.is_set():
                    break
                now = perf_counter()
                if now >= deadline:
                    break
                skipped = frame.frame_id - after_id - 1
                after_id = frame.frame_id
                if (self.process is not None and frame.received_at is not None
                        and (now - frame.received_at) * 1000 > self.max_frame_age_ms):
                    event = FrameEvent(frame, "stale", now, skipped)
                    logging.getLogger(__name__).warning(
                        "Rejected stale frame %s (%.1f ms)", frame.frame_id, event.frame_age_ms)
                else:
                    next_start = now + self.period_s
                    result = self.process(frame) if self.process is not None else None
                    event = FrameEvent(frame, "processed", now, skipped, now, perf_counter(), result)
                    if result is not None and result.measurement is not None:
                        self.latest_attempt = event.continuity, frame.frame_id, result.measurement
                    self.latest_processed = event
                    if self.recorder is not None:
                        self.recorder.write(frame, result)
                if self.on_event is not None:
                    self.on_event(event)
                self.latest = event
        except Exception as error:
            self.error = str(error) or type(error).__name__
            logging.getLogger(__name__).exception("Frame worker stopped")
        finally:
            self.done.set()


def _distribution(values):
    if not values:
        return None
    return dict(count=len(values), mean=float(np.mean(values)), median=float(np.median(values)),
                p95=float(np.percentile(values, 95)), max=float(np.max(values)))


class PerformanceStats:
    """Counters over the run and optional rolling timing samples for a long-lived GUI."""

    def __init__(self, *, sample_limit=None, ground_truth=None, truth_yaw=None):
        self.samples = defaultdict(lambda: deque(maxlen=sample_limit))
        self.completed = deque(maxlen=60)
        self.counts = Counter()
        self.lock = Lock()
        self.previous_state = self.continuity = None
        self.loss_started = None
        self.ground_truth, self.truth_yaw = ground_truth, truth_yaw

    def continue_after(self, event):
        """Carry the warmup tracking state into a new sample window."""
        if event is not None and event.result is not None and event.result.tracking is not None:
            self.continuity = event.continuity
            self.previous_state = event.result.tracking.state

    def add(self, event):
        with self.lock:
            self.counts["skipped"] += event.skipped
            if event.status == "stale":
                self.counts["stale"] += 1
                return
            if event.status == "drained":
                self.counts["drained"] += 1
                return
            result = event.result
            if result is None:
                return
            self.counts["processed"] += 1
            continuity = event.continuity
            previous = self.previous_state
            if self.continuity != continuity:
                self.completed.clear()
                previous = None
                self.loss_started = None
            self.completed.append(event.ended_at)
            stages = event.stages()
            stages.update(processing=event.processing_ms, frame_age=event.frame_age_ms,
                          receive_to_result=event.receive_to_result_ms)
            for stage, value in stages.items():
                if value is not None:
                    self.samples[stage].append(value)
            measurement, tracking = result.measurement, result.tracking
            if measurement is not None:
                self.counts["attempts"] += 1
                self.counts["matches"] += measurement.success
            used = tracking.measurement_used if tracking is not None else bool(
                measurement is not None and measurement.success)
            self.counts["accepted"] += used
            if result.tag_detections:
                self.counts["tag_visible"] += 1
            else:
                self.counts["tag_absent_accepted"] += used
            if used:
                self.counts[f"accepted:{tracking.observation_source if tracking else 'MAP'}"] += 1
            if used and (self.ground_truth is not None or self.truth_yaw is not None):
                absolute = (tracking.correction if tracking is not None else None) or measurement
                wrong = False
                if self.ground_truth is not None:
                    position_error = math.dist((absolute.x_m, absolute.y_m), self.ground_truth)
                    self.samples["position_error_m"].append(position_error)
                    wrong = position_error > 0.05
                if self.truth_yaw is not None:
                    yaw_error = abs((absolute.yaw_deg - self.truth_yaw + 180) % 360 - 180)
                    self.samples["yaw_error_deg"].append(yaw_error)
                    wrong |= yaw_error > 15
                self.counts["truth_accepted"] += 1
                self.counts["wrong_accepted"] += wrong
            state = tracking.state if tracking is not None else None
            if state == TrackingState.RELOCALIZING and previous != state:
                self.counts["losses"] += 1
                self.loss_started = event.frame.timestamp
            if tracking is not None and tracking.relocalized and self.loss_started is not None:
                self.samples["relocalization_s"].append(event.frame.timestamp - self.loss_started)
                self.loss_started = None
            self.previous_state, self.continuity = state, continuity

    def add_gui_time(self, elapsed_ms):
        with self.lock:
            self.samples["gui_render"].append(elapsed_ms)

    def display(self, now, *, stale_s):
        with self.lock:
            times = self.completed
            fps = ((len(times) - 1) / (times[-1] - times[0])
                   if len(times) > 1 and times[-1] > times[0] and now - times[-1] < stale_s else 0.0)
            global_times = self.samples["global_matching"]
            local_times = self.samples["local_matching"]
            return (fps, self.counts["skipped"], self.counts["stale"],
                    global_times[-1] if global_times else None, local_times[-1] if local_times else None,
                    tuple(self.samples["processing"])[-60:])

    def summary(self, received, elapsed_s):
        with self.lock:
            counts = self.counts.copy()
            samples = {name: tuple(values) for name, values in self.samples.items() if values}
        processed, stale = counts["processed"], counts["stale"]
        attempts = counts["attempts"]
        drained = counts["drained"]
        skipped = max(0, received - processed - stale - drained)
        keys = ("jpeg_receive", "decode", "jpeg_receive_decode", "preprocessing", "hsv", "road",
                "lighting", "global_matching", "local_matching", "tracking", "localization", "diagnostic",
                "tag_detection", "tag_pose", "fusion", "replay_delivery_lag", "gui_render")
        return dict(
            elapsed_s=elapsed_s, camera_receive_fps=received / elapsed_s,
            localization_fps=processed / elapsed_s, received_frame_count=received,
            processed_frame_count=processed, stale_frame_count=stale, skipped_frame_count=skipped,
            drained_frame_count=drained,
            skipped_frame_ratio=skipped / received if received else None,
            matching_attempt_count=attempts, matching_success_count=counts["matches"],
            matching_success_rate=counts["matches"] / attempts if attempts else None,
            accepted_observation_count=counts["accepted"],
            accepted_observation_rate=counts["accepted"] / processed if processed else None,
            accepted_by_source={key.split(":", 1)[1]: count for key, count in counts.items()
                                if key.startswith("accepted:")},
            tag_detected_frame_count=counts["tag_visible"],
            no_tag_accepted_count=counts["tag_absent_accepted"],
            tracking_loss_count=counts["losses"],
            relocalization_s=_distribution(samples.get("relocalization_s")),
            truth_accepted_count=counts["truth_accepted"],
            wrong_accepted_count=counts["wrong_accepted"] if counts["truth_accepted"] else None,
            wrong_accepted_rate=counts["wrong_accepted"] / counts["truth_accepted"]
            if counts["truth_accepted"] else None,
            processing_ms=_distribution(samples.get("processing")),
            frame_age_ms=_distribution(samples.get("frame_age")),
            receive_to_result_ms=_distribution(samples.get("receive_to_result")),
            position_error_m=_distribution(samples.get("position_error_m")),
            position_rmse_m=(math.sqrt(np.mean(np.square(samples["position_error_m"])))
                             if "position_error_m" in samples else None),
            yaw_error_deg=_distribution(samples.get("yaw_error_deg")),
            stages_ms={name: _distribution(samples[name]) for name in keys if name in samples},
        )


class PerformanceLog:
    fields = (
        "frame_id", "source", "connection", "input_epoch", "observation_timestamp", "receive_timestamp",
        "selected_timestamp", "processing_start_timestamp", "processing_end_timestamp",
        "status", "skipped_before", "frame_age_ms", "processing_ms", "receive_to_result_ms",
        "jpeg_receive_ms", "decode_ms", "jpeg_receive_decode_ms", "preprocessing_ms", "hsv_ms", "road_ms",
        "lighting_ms", "global_matching_ms", "local_matching_ms", "tracking_ms",
        "localization_ms", "diagnostic_ms", "replay_delivery_lag_ms",
        "tag_detection_ms", "tag_pose_ms", "fusion_ms",
        "match_mode", "tracking_state", "measurement_used", "localization_result",
    )

    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("x", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.file, fieldnames=self.fields)
        self.writer.writeheader()

    def write(self, event):
        frame, result = event.frame, event.result
        tracking = result.tracking if result is not None else None
        details = {}
        if result is not None:
            details = {key: asdict(value) if value is not None else None for key, value in (
                ("measurement", result.measurement), ("tracking", tracking),
                ("map_observation", result.map_observation), ("tag_observation", result.tag_observation))}
            details["tag_ids"] = [tag.tag_id for tag in result.tag_detections]
            details["tag_setup"] = result.tag_setup
            details["localization_mode"] = result.localization_mode
            details["projected_xy_m"] = result.projected_xy_m
            details["projected_timestamp"] = result.projected_timestamp
            if result.lighting is not None:
                details["lighting"] = asdict(result.lighting)
        self.writer.writerow(dict(
            frame_id=frame.frame_id, source=frame.source, connection=frame.connection,
            input_epoch=frame.input_epoch,
            observation_timestamp=frame.timestamp, receive_timestamp=frame.received_at,
            selected_timestamp=event.selected_at, processing_start_timestamp=event.started_at,
            processing_end_timestamp=event.ended_at, status=event.status,
            skipped_before=event.skipped, frame_age_ms=event.frame_age_ms,
            processing_ms=event.processing_ms, receive_to_result_ms=event.receive_to_result_ms,
            match_mode=result.match_mode if result is not None else None,
            tracking_state=tracking.state.value if tracking is not None else None,
            measurement_used=tracking.measurement_used if tracking is not None else (
                result.measurement.success if result is not None and result.measurement is not None
                else None),
            localization_result=json.dumps(details) if details else None,
            **{f"{name}_ms": value for name, value in event.stages().items()},
        ))

    def close(self):
        self.file.close()
