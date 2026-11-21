"""Record raw BGR input frames with their original input time and tracking metadata."""

from dataclasses import asdict, replace
from pathlib import Path
import csv
import json

import cv2

from .live_localization import LiveResult


class CameraRecorder:
    fields = ("frame_id", "elapsed_s", "timestamp", "width", "height", "source",
              "sequence", "result")

    def __init__(self, path, settings):
        self.path = Path(path)
        if self.path.suffix.lower() != ".avi":
            raise ValueError("recording path must end in .avi")
        outputs = [self.path, self.path.with_suffix(".csv"), self.path.with_suffix(".json")]
        if any(path.exists() for path in outputs):
            raise ValueError("recording output already exists; choose a new basename")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.writer = self.file = None

    def write(self, frame, result=None):
        if isinstance(result, LiveResult) and result.diagnostic is not None:
            result = replace(result, diagnostic=None)
        result_json = json.dumps(asdict(result)) if result is not None else ""
        height, width = frame.image.shape[:2]
        size = width, height
        if self.writer is None:
            writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"MJPG"), 10, size)
            if not writer.isOpened():
                writer.release()
                raise ValueError(f"cannot create recording: {self.path}")
            self.writer = writer
            self.size, self.origin = size, frame.timestamp
            with self.path.with_suffix(".json").open("x", encoding="utf-8") as stream:
                json.dump({"container_fps": 10, "input_resolution": list(size),
                           "settings": self.settings}, stream, indent=2)
            self.file = self.path.with_suffix(".csv").open("x", encoding="utf-8", newline="")
            self.csv = csv.DictWriter(self.file, fieldnames=self.fields)
            self.csv.writeheader()
        if size != self.size:
            raise ValueError("input size changed; start a new recording for the new resolution")
        self.writer.write(frame.image)
        self.csv.writerow(dict(frame_id=frame.frame_id, elapsed_s=frame.timestamp - self.origin,
                               timestamp=frame.timestamp, width=width, height=height,
                               source=frame.source, sequence=frame.sequence,
                               result=result_json))

    def close(self):
        if self.writer is not None:
            self.writer.release()
        if self.file is not None:
            self.file.close()
