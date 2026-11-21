"""Ideal nadir camera on a flat model with configurable image perturbations."""

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .map_loader import BACKGROUND, MapRaster


@dataclass(frozen=True)
class Pose:
    """Position in model meters and yaw in degrees counterclockwise from north."""
    x_m: float
    y_m: float
    yaw_deg: float


@dataclass(frozen=True)
class CameraConfig:
    width_px: int = 240
    height_px: int = 180
    height_m: float = 0.50
    hfov_deg: float = 68.0
    noise_std: float = 0.0
    brightness: float = 1.0
    channel_gains: tuple[float, float, float] = (1.0, 1.0, 1.0)  # BGR

    def __post_init__(self):
        if min(self.width_px, self.height_px) < 2:
            raise ValueError("camera dimensions must be at least 2 pixels")
        footprint_width(self.height_m, self.hfov_deg)
        gains = (self.brightness, self.noise_std, *self.channel_gains)
        if not np.isfinite(gains).all() or self.noise_std < 0 or min(self.brightness, *self.channel_gains) <= 0:
            raise ValueError("brightness and channel gains must be positive; noise must be nonnegative")

    @property
    def footprint_m(self) -> float:
        return footprint_width(self.height_m, self.hfov_deg)


def footprint_width(height_m: float, hfov_deg: float) -> float:
    """Return the horizontal ground span in meters for a nadir pinhole camera."""
    if not math.isfinite(height_m) or height_m <= 0 or not 0 < hfov_deg < 180:
        raise ValueError("height must be positive and horizontal FOV must be in (0, 180)")
    return 2 * height_m * math.tan(math.radians(hfov_deg) / 2)


def image_center(width: int, height: int) -> tuple[float, float]:
    """Return the optical center in pixel-center coordinates."""
    return (width - 1) / 2, (height - 1) / 2


def perturb(frame: np.ndarray, config: CameraConfig, rng: np.random.Generator) -> np.ndarray:
    """Apply brightness, BGR gains, and independent Gaussian noise in pixel units."""
    if config.brightness == 1 and config.noise_std == 0 and config.channel_gains == (1, 1, 1):
        return frame
    values = frame.astype(np.float32) * (config.brightness * np.asarray(config.channel_gains))
    if config.noise_std:
        values += rng.normal(0, config.noise_std, frame.shape)
    return np.clip(np.rint(values), 0, 255).astype(np.uint8)


class SyntheticCamera:
    def __init__(self, raster: MapRaster, config: CameraConfig, seed: int = 42):
        self.raster = raster
        self.scene = raster.render()
        self.config = config
        self.rng = np.random.default_rng(seed)

    def map_transform(self, pose: Pose) -> np.ndarray:
        """Return the affine transform from camera pixels to map pixels."""
        config = self.config
        center = image_center(config.width_px, config.height_px)
        scale = config.footprint_m * self.raster.pixels_per_m / config.width_px
        affine = cv2.getRotationMatrix2D(center, pose.yaw_deg, scale)
        affine[:, 2] += self.raster.xy_to_uv((pose.x_m, pose.y_m)) - center
        return affine

    def capture(self, pose: Pose) -> np.ndarray:
        """Render one BGR frame. The returned array carries no pose metadata."""
        config = self.config
        frame = cv2.warpAffine(self.scene, self.map_transform(pose), (config.width_px, config.height_px),
                               flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderValue=BACKGROUND)
        return perturb(frame, config, self.rng)
