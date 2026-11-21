"""Photometric corrections for BGR images of a printed map."""

from dataclasses import dataclass
import math

import cv2
import numpy as np


ILLUMINATION_METHODS = ("none", "clahe", "constancy", "field", "combined")
NO_PAPER = object()


@dataclass(frozen=True)
class IlluminationConfig:
    method: str = "none"
    clip_limit: float = 2.0
    tile_grid_size: tuple[int, int] = (8, 8)

    def __post_init__(self):
        if self.method not in ILLUMINATION_METHODS:
            raise ValueError("invalid illumination method")
        if not math.isfinite(self.clip_limit) or self.clip_limit <= 0:
            raise ValueError("CLAHE clip limit must be positive and finite")
        if len(self.tile_grid_size) != 2 or any(type(n) is not int or n < 1 for n in self.tile_grid_size):
            raise ValueError("CLAHE tile grid must contain two positive integers")


@dataclass(frozen=True)
class PaperSamples:
    luminance: np.ndarray
    mask: np.ndarray
    bright_level: float


def check_bgr(frame):
    if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
        raise ValueError("expected a nonempty uint8 BGR image")


def sample_paper(frame):
    """Find near-neutral local highlights, rejecting narrow gray markings where possible."""
    light = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    blue, green, red = cv2.split(frame)
    high = cv2.max(cv2.max(blue, green), red)
    low = cv2.min(cv2.min(blue, green), red)
    neutral = (high >= 8) & ((high - low) <= 0.30 * high + 2)
    size = 2 * max(2, round(min(light.shape) / 50)) + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    local_max = cv2.dilate(np.where(neutral, light, 0), kernel)
    anchors = neutral & (light >= 0.86 * local_max)
    if np.count_nonzero(anchors) < max(12, light.size * 0.03):
        return None
    return PaperSamples(light, anchors, float(np.percentile(light[anchors], 88)))


def estimate_illumination(paper):
    """Estimate low-frequency light from paper samples, in observed intensity units."""
    if paper is None:
        return None
    height, width = paper.luminance.shape
    original_size = width, height
    size = (width + 1) // 2, (height + 1) // 2
    sigma = max(0.75, min(height, width) / 36)
    support = paper.mask.astype(np.float32)
    total = cv2.resize(paper.luminance * support, size, interpolation=cv2.INTER_AREA)
    density = cv2.resize(support, size, interpolation=cv2.INTER_AREA)
    total = cv2.GaussianBlur(total, (0, 0), sigma)
    density = cv2.GaussianBlur(density, (0, 0), sigma)
    # Extend the estimate into larger colored buildings from nearby paper.
    missing = density < 0.12
    if missing.any():
        longer_total = cv2.GaussianBlur(total, (0, 0), 1.7 * sigma)
        longer_density = cv2.GaussianBlur(density, (0, 0), 1.7 * sigma)
        total[missing], density[missing] = longer_total[missing], longer_density[missing]
    field = np.divide(total, density, out=np.full_like(total, paper.bright_level), where=density >= 0.025)
    return cv2.resize(field, original_size, interpolation=cv2.INTER_LINEAR)


class IlluminationCorrector:
    def __init__(self, config=IlluminationConfig()):
        self.config = config
        self.clahe = (cv2.createCLAHE(config.clip_limit, config.tile_grid_size)
                      if config.method == "clahe" else None)

    def apply(self, frame, *, paper=None, field=None):
        check_bgr(frame)
        method = self.config.method
        if method == "none":
            return frame
        if self.clahe is not None:
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            lab[..., 0] = self.clahe.apply(lab[..., 0])
            return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        if paper is None:
            paper = sample_paper(frame)
        if paper is None or paper is NO_PAPER:
            return frame

        values = frame.astype(np.float32)
        if method in ("constancy", "combined"):
            # Use the brighter near-neutral paper pixels as a common color reference.
            bright = paper.mask & (paper.luminance >= 0.95 * paper.bright_level)
            # An 8-bit histogram gives the exact per-channel median without
            # copying and partitioning all highlight pixels in each frame.
            count = np.count_nonzero(bright)
            if 0 < count <= 2 ** 24:
                positions = ((count + 1) // 2, count // 2 + 1)
                white = np.empty(3, np.float32)
                for channel in range(3):
                    counts = cv2.calcHist([frame], [channel], bright.view(np.uint8), [256], [0, 256])
                    values_at_middle = np.searchsorted(np.cumsum(counts, dtype=np.int64), positions)
                    white[channel] = values_at_middle.mean()
            else:
                white = np.median(frame[bright], axis=0).astype(np.float32)
            spread = (white.max() - white.min()) / max(white.max(), 1)
            if spread > 0.04:
                values *= np.clip(white.mean() / np.maximum(white, 1), 0.72, 1.45)
            if paper.bright_level < 219:
                values *= min(2.8, 237 / max(paper.bright_level, 1))
        if method in ("field", "combined"):
            field = estimate_illumination(paper) if field is None else field
            gain = np.clip(0.96 * paper.bright_level / np.maximum(field, 1), 1.0, 2.2)
            values *= gain[..., None]

        # Limit amplification without clipping just one channel and shifting its hue.
        high = np.maximum(np.maximum(values[..., 0], values[..., 1]), values[..., 2])
        values *= np.minimum(1.0, 255 / np.maximum(high, 1))[..., None]
        return np.rint(values).astype(np.uint8)
