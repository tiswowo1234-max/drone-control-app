"""Read the existing campus geometry and rasterize it without reprocessing OSM."""

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .paths import MAPS_DIR


DEFAULT_MAP = MAPS_DIR / "KAIST_model_features_2p5m.json"
BACKGROUND = (233, 241, 237)  # BGR


def hex_to_bgr(color: str) -> tuple[int, int, int]:
    """Convert a JSON #RRGGBB color to an OpenCV BGR triplet."""
    return tuple(int(color[i:i + 2], 16) for i in (5, 3, 1))


@dataclass
class CampusMap:
    width_m: float
    height_m: float
    buildings: list[dict]
    roads: list[dict]
    landmarks: dict
    source: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path = DEFAULT_MAP) -> "CampusMap":
        """Load features in model meters with an origin at the southwest corner."""
        with Path(path).open(encoding="utf-8") as stream:
            data = json.load(stream)
        metadata = data["metadata"]
        return cls(metadata["model_width_m"], metadata["model_height_m"],
                   data["buildings"], data["roads"], data["landmarks"], Path(path).resolve())

    @property
    def palette(self) -> tuple:
        return tuple(dict.fromkeys(hex_to_bgr(b["color"]) for b in self.buildings))

    def paint(self, size: tuple[int, int], project, pixels_per_m: float) -> np.ndarray:
        """Draw the scene in BGR using a supplied xy meters to uv pixels projection."""
        width, height = size
        image = np.full((height, width, 3), BACKGROUND, dtype=np.uint8)
        for road in self.roads:
            vertices = _vertices(project, road["centerline_xy_m"])
            # Match the style of the original 10-inch reference drawing.
            thickness = road["display_linewidth_pt"] * self.width_m / 720
            cv2.polylines(image, [vertices], False, (253, 253, 253),
                          max(1, round((thickness + 2.2 * self.width_m / 720) * pixels_per_m)),
                          shift=8)
            cv2.polylines(image, [vertices], False, hex_to_bgr(road["display_color"]),
                          max(1, round(thickness * pixels_per_m)), shift=8)
        for building in self.buildings:
            cv2.fillPoly(image, [_vertices(project, building["polygon_xy_m"])],
                         hex_to_bgr(building["color"]), shift=8)
        for landmark in self.landmarks.values():
            center = _vertices(project, [landmark["xy_m"]])[0]
            cv2.circle(image, center, round(0.016 * pixels_per_m * 256),
                       (63, 52, 221), -1, shift=8)
        return image


def _vertices(project, points) -> np.ndarray:
    """Convert projected UV coordinates to OpenCV fixed-point coordinates."""
    return np.rint(project(points) * 256).astype(np.int32)


class MapRaster:
    def __init__(self, campus: CampusMap, pixels_per_m: float):
        if not np.isfinite(pixels_per_m) or pixels_per_m <= 0:
            raise ValueError("map resolution must be positive")
        self.campus = campus
        self.pixels_per_m = pixels_per_m
        self.size = (int(np.ceil(campus.width_m * pixels_per_m)) + 1,
                     int(np.ceil(campus.height_m * pixels_per_m)) + 1)

    def xy_to_uv(self, points) -> np.ndarray:
        """Convert (..., 2) Cartesian model meters to north-up (column, row) pixels."""
        return (np.asarray(points) * (1, -1) + (0, self.campus.height_m)) * self.pixels_per_m

    def uv_to_xy(self, points) -> np.ndarray:
        """Convert (..., 2) (column, row) pixels back to model meters."""
        return (np.asarray(points) / self.pixels_per_m - (0, self.campus.height_m)) * (1, -1)

    def render(self) -> np.ndarray:
        """Render the display map in BGR."""
        return self.campus.paint(self.size, self.xy_to_uv, self.pixels_per_m)

    def labels(self) -> np.ndarray:
        """Render building labels 1..N in palette order. Label 0 is background."""
        labels = np.zeros(self.size[::-1], dtype=np.uint8)
        indices = {color: i for i, color in enumerate(self.campus.palette, start=1)}
        for building in self.campus.buildings:
            cv2.fillPoly(labels, [_vertices(self.xy_to_uv, building["polygon_xy_m"])],
                         indices[hex_to_bgr(building["color"])], shift=8)
        return labels
