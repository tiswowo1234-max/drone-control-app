"""Common BGR frame contract for synthetic, recorded and network inputs."""

from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Condition, Event, Thread
from time import perf_counter
from typing import Protocol

import cv2
import numpy as np

from .camera_simulator import SyntheticCamera


INPUT_GAP_S = 2.0


@dataclass(frozen=True)
class CameraFrame:
    image: np.ndarray
    timestamp: float
    frame_id: int
    source: str
    received_at: float | None = None
    decode_ms: float = 0.0
    sequence: int | None = None
    connection: int = 0
    jpeg_receive_ms: float | None = None
    input_epoch: int | None = None
    observation_at: float | None = None


class LatestFrameBuffer:
    """One shared read-only image, with a separate cursor for each consumer."""

    def __init__(self, condition=None):
        self.condition = condition if condition is not None else Condition()
        self._latest = None
        self._read_id = 0
        self._finished = False
        self._last_timestamp = None
        self._epoch = 0

    def publish(self, frame):
        frame.image.setflags(write=False)
        with self.condition:
            if self._finished:
                return False
            overwritten = self._latest is not None and self._latest.frame_id > self._read_id
            if self._last_timestamp is not None and frame.timestamp - self._last_timestamp >= INPUT_GAP_S:
                self._epoch += 1
            self._last_timestamp = frame.timestamp
            self._latest = replace(frame, input_epoch=self._epoch)
            self.condition.notify_all()
            return overwritten

    def peek(self):
        with self.condition:
            return self._latest

    def wait_latest(self, after_id, timeout):
        with self.condition:
            self.condition.wait_for(
                lambda: self._finished or (self._latest is not None
                                           and self._latest.frame_id > after_id), timeout)
            if self._latest is not None and self._latest.frame_id > after_id:
                return self._latest
            return None

    def read(self, timeout=0):
        """Compatibility cursor for callers using the original single-consumer API."""
        with self.condition:
            frame = self.wait_latest(self._read_id, timeout)
            if frame is not None:
                self._read_id = frame.frame_id
            return frame

    def clear(self, *, finished=False, keep_latest=False):
        with self.condition:
            if not keep_latest:
                self._latest = None
            self._finished |= finished
            self.condition.notify_all()


class CameraSource(Protocol):
    def read(self, timeout: float = 0) -> CameraFrame | None: ...
    def close(self) -> None: ...


class SyntheticCameraSource:
    def __init__(self, camera: SyntheticCamera, samples):
        self.camera = camera
        self.samples = enumerate(samples, start=1)

    def read(self, timeout: float = 0) -> CameraFrame | None:
        item = next(self.samples, None)
        if item is None:
            return None
        index, sample = item
        return CameraFrame(self.camera.capture(sample.pose), sample.timestamp_s, index, "synthetic")

    def close(self):
        self.samples = iter(())


class VideoFileSource:
    """Read fixed-rate OpenCV video, optionally using the recorder's frame timestamps."""

    def __init__(self, path):
        import csv

        path = Path(path)
        self.capture = cv2.VideoCapture(str(path))
        if not self.capture.isOpened():
            self.capture.release()
            raise ValueError(f"cannot open video: {path}")
        self.fps = self.capture.get(cv2.CAP_PROP_FPS)
        self.frame_id = 0
        self.timestamps = []
        sidecar = path.with_suffix(".csv")
        try:
            if sidecar.exists():
                with sidecar.open(encoding="utf-8", newline="") as stream:
                    self.timestamps = [float(row["elapsed_s"]) for row in csv.DictReader(stream)]
                if not np.isfinite(self.timestamps).all() or np.any(np.diff(self.timestamps) <= 0):
                    raise ValueError("recorded frame timestamps must be finite and strictly increasing")
            if not self.timestamps and not (np.isfinite(self.fps) and self.fps > 0):
                raise ValueError("video has no valid frame rate or timestamp sidecar")
        except (KeyError, ValueError, OSError):
            self.close()
            raise

    def read(self, timeout: float = 0) -> CameraFrame | None:
        start = perf_counter()
        ok, frame = self.capture.read()
        if not ok:
            return None
        index = self.frame_id
        self.frame_id += 1
        if self.timestamps and index >= len(self.timestamps):
            raise ValueError("recorded video contains more frames than its timestamp sidecar")
        timestamp = self.timestamps[index] if self.timestamps else index / self.fps
        return CameraFrame(frame, timestamp, self.frame_id, "video",
                           decode_ms=(perf_counter() - start) * 1000)

    def close(self):
        self.capture.release()


class TimedVideoSource:
    """Replay a video on its recorded timeline into the same capacity-one slot."""

    def __init__(self, path):
        self.video = VideoFileSource(path)
        self.frames = LatestFrameBuffer()
        self.stop = Event()
        self.worker = Thread(target=self._play, name="video-receiver", daemon=True)
        self.received = 0
        self.last_received_at = None
        self.finished = False
        self.error = ""
        self.timestamps_preserved = bool(self.video.timestamps)
        self._times = deque(maxlen=60)

    def start(self):
        self.worker.start()

    def wait_latest(self, after_id, timeout=0):
        return self.frames.wait_latest(after_id, timeout)

    def peek(self):
        return self.frames.peek()

    @property
    def receive_fps(self):
        with self.frames.condition:
            times = self._times
            return ((len(times) - 1) / (times[-1] - times[0])
                    if len(times) > 1 and times[-1] > times[0] else 0.0)

    def receive_cursor(self):
        with self.frames.condition:
            return perf_counter(), self.received

    def close(self):
        self.stop.set()
        if self.worker.ident is not None:
            self.worker.join()
        else:
            self.video.close()

    def _play(self):
        try:
            origin = None
            while not self.stop.is_set():
                frame = self.video.read()
                if frame is None:
                    break
                if origin is None:
                    origin = perf_counter() - frame.timestamp
                due = origin + frame.timestamp
                while not self.stop.is_set() and (remaining := due - perf_counter()) > 0:
                    self.stop.wait(max(0.001, remaining))
                if self.stop.is_set():
                    break
                received_at = perf_counter()
                with self.frames.condition:
                    self.received = frame.frame_id
                    self.last_received_at = received_at
                    self._times.append(received_at)
                    self.frames.publish(replace(frame, received_at=received_at, observation_at=due))
        except (ValueError, OSError, cv2.error) as error:
            self.error = str(error)
        finally:
            self.video.close()
            with self.frames.condition:
                self.finished = True
                self.frames.clear(finished=True, keep_latest=True)
