"""Spatial shadow evidence and conservative per-pixel matching weights."""

from dataclasses import dataclass

import cv2
import numpy as np

from .illumination_correction import NO_PAPER, check_bgr, estimate_illumination, sample_paper


@dataclass(frozen=True)
class ShadowObservation:
    shadow_mask: np.ndarray
    confidence_mask: np.ndarray

    @property
    def effective_coverage(self):
        return float(self.confidence_mask.mean())

    @property
    def shadow_ratio(self):
        return float(self.shadow_mask.mean())


def detect_shadows(frame, *, paper=None, field=None):
    """Return a relative shadow mask and [0, 1] weights, not calibrated probabilities."""
    check_bgr(frame)
    paper = sample_paper(frame) if paper is None else paper
    if paper is None or paper is NO_PAPER or paper.bright_level < 35:
        # A uniformly dark or paper-free view has no reliable local lighting contrast.
        return ShadowObservation(np.zeros(frame.shape[:2], bool), np.ones(frame.shape[:2], np.float32))
    field = estimate_illumination(paper) if field is None else field
    ratio = field / paper.bright_level
    strength = np.clip((0.80 - ratio) / 0.55, 0, 1)
    # Black clipping removes even the neutral paper samples. Retain evidence from
    # broad clipped regions near lit paper, while discarding thin outlines.
    black = cv2.inRange(frame, (0, 0, 0), (8, 8, 8))
    if black.any():
        size = 2 * max(2, round(min(black.shape) / 36)) + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        clipped = cv2.morphologyEx(black, cv2.MORPH_OPEN, kernel)
        strength[clipped > 0] = 1
    confidence = (1 - 0.98 * strength).astype(np.float32)
    return ShadowObservation(strength > 0.08, confidence)
