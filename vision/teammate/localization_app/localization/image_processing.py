"""Extract palette-colored building masks and contours from uint8 camera images."""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ColorConfig:
    hue_tolerance: int = 7  # OpenCV hue units, where 180 wraps to 0
    saturation_tolerance: int = 55
    value_tolerance: int = 95
    morph_size: int = 3
    min_area_px: int = 12
    hue_shift: int = 0  # Added to observed hue in OpenCV's 0..179 range
    hue_band_width: int = 0  # 0 keeps the individual map palette labels


@dataclass
class BuildingObservation:
    labels: np.ndarray
    contours: dict[int, list[np.ndarray]]


class BuildingDetector:
    def __init__(self, palette: tuple, config: ColorConfig = ColorConfig()):
        if not 1 <= len(palette) <= 255:
            raise ValueError("expected 1 to 255 building colors")
        if not (1 <= config.morph_size and config.morph_size % 2 and config.min_area_px >= 1):
            raise ValueError("morph_size must be positive and odd; min_area_px must be positive")
        if not (0 <= config.hue_tolerance <= 90 and 0 <= config.saturation_tolerance <= 255
                and 0 <= config.value_tolerance <= 255):
            raise ValueError("HSV tolerances must fit their OpenCV channel ranges")
        if type(config.hue_shift) is not int or not -90 <= config.hue_shift <= 90:
            raise ValueError("hue_shift must be an integer from -90 to 90")
        if type(config.hue_band_width) is not int or not 0 <= config.hue_band_width <= 90:
            raise ValueError("hue_band_width must be an integer from 0 to 90")
        self.config = config
        hsv = cv2.cvtColor(np.asarray([palette], np.uint8), cv2.COLOR_BGR2HSV)[0].astype(int)
        self.label_map = np.arange(len(palette) + 1, dtype=np.uint8)
        self.label_palette = tuple(palette)
        if config.hue_band_width:
            bands = hsv[:, 0] // config.hue_band_width
            _, representative, index = np.unique(bands, return_index=True, return_inverse=True)
            self.label_map[1:] = index + 1
            self.label_palette = tuple(palette[i] for i in representative)
        corrected_hues = (np.arange(180) + config.hue_shift) % 180
        difference = np.abs(corrected_hues[:, None] - hsv[:, 0])
        circular = np.minimum(difference, 180 - difference)
        nearest = np.argmin(circular, axis=1)
        self.color_for_hue = np.zeros((3, 256), np.uint8)
        self.color_for_hue[0, :180] = np.where(
            circular[np.arange(180), nearest] <= config.hue_tolerance, self.label_map[nearest + 1], 0)
        self.color_for_hue[1:, :180] = hsv[nearest, 1:].T
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                               (config.morph_size, config.morph_size))

    def detect(self, frame: np.ndarray, *, rgb: bool = False,
               include_contours: bool = True) -> BuildingObservation:
        """Return cleaned color labels and outer contours in input-image pixels."""
        if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("expected a uint8 BGR or RGB image with three channels")
        config = self.config
        hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV if rgb else cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        label_lut, sat_lut, val_lut = self.color_for_hue
        sat_error = cv2.absdiff(sat, cv2.LUT(hue, sat_lut))
        val_error = cv2.absdiff(val, cv2.LUT(hue, val_lut))
        valid = cv2.bitwise_and(cv2.inRange(sat_error, 0, int(config.saturation_tolerance)),
                                cv2.inRange(val_error, 0, int(config.value_tolerance)))
        raw = cv2.bitwise_and(cv2.LUT(hue, label_lut), valid)
        labels = np.zeros_like(raw)
        contours = {}
        present = cv2.calcHist([raw], [0], None, [256], [0, 256]).ravel()
        height, width = raw.shape
        # The four morphology passes cannot reach past four kernel radii.
        halo = 2 * config.morph_size
        for label in np.flatnonzero(present[1:]) + 1:
            mask = cv2.compare(raw, int(label), cv2.CMP_EQ)
            x, y, w, h = cv2.boundingRect(mask)
            left, top = max(0, x - halo), max(0, y - halo)
            right, bottom = min(width, x + w + halo), min(height, y + h + halo)
            mask = mask[top:bottom, left:right]
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel)
            x, y, w, h = cv2.boundingRect(mask)
            if not w:
                continue
            _, components, stats, _ = cv2.connectedComponentsWithStats(mask[y:y + h, x:x + w])
            keep = stats[:, cv2.CC_STAT_AREA] >= config.min_area_px
            keep[0] = False
            if not keep.any():
                continue
            cleaned = keep[components]
            x, y = x + left, y + top
            tile = np.s_[y:y + h, x:x + w]
            output = labels[tile]
            # Prefer the original observed color when closed masks touch.
            output[cleaned & ((output == 0) | (raw[tile] == label))] = label
            if include_contours:
                outlines, _ = cv2.findContours(cleaned.astype(np.uint8), cv2.RETR_EXTERNAL,
                                                cv2.CHAIN_APPROX_SIMPLE, offset=(x, y))
                contours[int(label)] = outlines
        return BuildingObservation(labels, contours)
