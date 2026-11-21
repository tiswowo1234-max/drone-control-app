"""Camera intrinsics: a calibration file (calibrate_camera.py) or a pinhole guess from the field of view."""
import json
import math
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from settings import CameraConfig


@dataclass
class CameraModel:
    K: np.ndarray  # 3x3
    dist: np.ndarray  # OpenCV distortion coefficients
    width: int
    height: int
    source: str
    hfov_deg: float | None = None  # set for the field-of-view guess, None for a real calibration

    @classmethod
    def from_fov(cls, cam: CameraConfig = CameraConfig()) -> "CameraModel":
        f = cam.width_px / 2 / math.tan(math.radians(cam.hfov_deg) / 2)
        K = np.array([[f, 0, (cam.width_px - 1) / 2], [0, f, (cam.height_px - 1) / 2], [0, 0, 1]])
        return cls(K, np.zeros(5), cam.width_px, cam.height_px, f"pinhole guess, hfov {cam.hfov_deg} deg",
                   cam.hfov_deg)

    @classmethod
    def load(cls, path: Path, warn: bool = True) -> "CameraModel":
        if not path.exists():
            if warn:
                warnings.warn(f"{path.name} not found; using a pinhole guess from CameraConfig. "
                              f"Calibrate the real camera with calibrate_camera.py before trusting distances.")
            return cls.from_fov()
        data = json.loads(path.read_text())
        return cls(np.array(data["K"], float), np.array(data["dist"], float), data["width"], data["height"],
                   f"calibration {path.name} (rms {data.get('rms_px', float('nan')):.2f} px)")

    def save(self, path: Path, rms_px: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"K": self.K.tolist(), "dist": self.dist.ravel().tolist(), "width": self.width,
                                    "height": self.height, "rms_px": rms_px}, indent=2))

    def for_size(self, width: int, height: int) -> "CameraModel":
        """Same camera at another resolution (e.g. calibrated at 1280x960, streaming 640x480)."""
        if (width, height) == (self.width, self.height):
            return self
        if self.hfov_deg is not None:  # only a guess anyway: make the same guess for this frame size
            return CameraModel.from_fov(CameraConfig(width_px=width, height_px=height, hfov_deg=self.hfov_deg))
        sx, sy = width / self.width, height / self.height
        if abs(sx - sy) > 1e-3:
            raise ValueError(f"frame {width}x{height} has another aspect ratio than the calibration "
                             f"{self.width}x{self.height}; calibrate at this resolution")
        K = self.K.copy()
        K[0, 0] *= sx
        K[1, 1] *= sy
        K[0, 2] = (K[0, 2] + 0.5) * sx - 0.5  # pixel centers sit on integers
        K[1, 2] = (K[1, 2] + 0.5) * sy - 0.5
        return CameraModel(K, self.dist, width, height, self.source + f", scaled to {width}x{height}")
