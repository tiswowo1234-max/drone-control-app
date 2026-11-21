"""Find printed neutral road ink against the local paper brightness."""

from dataclasses import dataclass

import cv2
import numpy as np


MIN_ROAD_CONFIDENCE = 0.55


def road_match_confidence(confidence: np.ndarray) -> np.ndarray:
    """Exclude large unusable edge regions from the road IoU union."""
    weak = cv2.compare(confidence, MIN_ROAD_CONFIDENCE, cv2.CMP_LT)
    if not (weak[0].any() or weak[-1].any() or weak[:, 0].any() or weak[:, -1].any()):
        return confidence
    radius = max(2, round(min(weak.shape) / 60))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1,) * 2)
    broad = cv2.morphologyEx(weak, cv2.MORPH_OPEN, kernel)
    _, components, stats, _ = cv2.connectedComponentsWithStats(broad)
    border = np.unique(np.concatenate((components[0], components[-1], components[:, 0], components[:, -1])))
    invalid = np.zeros(len(stats), dtype=bool)
    invalid[border] = stats[border, cv2.CC_STAT_AREA] >= 0.015 * weak.size
    invalid[0] = False
    if not invalid.any():
        return confidence
    # Road detection already rejects these pixels. Projected road pixels in
    # the same regions must also have zero weight when comparing the union.
    return np.where(invalid[components], np.float32(0), confidence)


@dataclass(frozen=True)
class RoadObservation:
    region: np.ndarray
    edges: np.ndarray
    coverage: float
    direction_diversity: float

    @property
    def informative(self) -> bool:
        return 0.018 <= self.coverage <= 0.57 and self.direction_diversity >= 0.085


def detect_roads(image: np.ndarray, buildings: np.ndarray,
                 confidence: np.ndarray | None = None) -> RoadObservation:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # A separable closing spans typical road widths at QVGA. In a broad shadow,
    # the interior becomes its own local paper reference.
    size = max(13, (round(min(gray.shape) * 0.22) | 1))
    radius = size // 2
    padded = cv2.copyMakeBorder(gray, radius, radius, radius, radius, cv2.BORDER_REPLICATE)
    paper = cv2.morphologyEx(padded, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))
    paper = paper[radius:-radius, radius:-radius]
    contrast = cv2.subtract(paper, gray)
    blue, green, red = cv2.split(image)
    maximum = cv2.max(cv2.max(blue, green), red)
    minimum = cv2.min(cv2.min(blue, green), red)
    chroma = cv2.subtract(maximum, minimum)
    neutral = chroma <= np.maximum(15, maximum * 0.14)
    possible = neutral & (gray >= 65) & (contrast >= np.maximum(12, paper * 0.056))
    # JPEG and color thresholding leave a neutral fringe around colored buildings.
    building_fringe = cv2.dilate(buildings, np.ones((5, 5), np.uint8))
    possible &= (contrast <= paper * 0.56) & (building_fringe == 0)
    if confidence is not None:
        possible &= confidence >= MIN_ROAD_CONFIDENCE
    region = possible.astype(np.uint8)
    # Remove isolated JPEG pixels and thin building outlines while retaining
    # stripes at least two camera pixels wide.
    kernel = np.ones((2, 2), np.uint8)
    region = cv2.erode(region, kernel, anchor=(0, 0), borderType=cv2.BORDER_REPLICATE)
    region = cv2.dilate(region, kernel, anchor=(1, 1),
                        borderType=cv2.BORDER_REPLICATE) & possible.astype(np.uint8)
    edges = region - cv2.erode(region, np.ones((3, 3), np.uint8))
    edges[:2] = edges[-2:] = 0
    edges[:, :2] = edges[:, -2:] = 0
    effective = region.mean() if confidence is None else cv2.mean(confidence * region)[0]
    return RoadObservation(region, edges, float(effective), direction_diversity(region))


def direction_diversity(region: np.ndarray) -> float:
    """Minor/major eigenvalue ratio of interior boundary-normal directions."""
    smoothed = cv2.GaussianBlur(region.astype(np.float32), (5, 5), 1.0)
    gx = cv2.Sobel(smoothed, cv2.CV_32F, 1, 0)[3:-3, 3:-3]
    gy = cv2.Sobel(smoothed, cv2.CV_32F, 0, 1)[3:-3, 3:-3]
    xx, xy, yy = cv2.sumElems(gx * gx)[0], cv2.sumElems(gx * gy)[0], cv2.sumElems(gy * gy)[0]
    spread = np.hypot(xx - yy, 2 * xy)
    total = xx + yy
    return float((total - spread) / (total + spread)) if total else 0.0
