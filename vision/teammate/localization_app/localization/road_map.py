"""Static layers for the gray ink visible on the printed map."""

from collections import defaultdict
from dataclasses import dataclass

import cv2
import numpy as np

from .map_loader import DEFAULT_MAP, MapRaster, _vertices, hex_to_bgr
from .paths import DATA_DIR


PRINT_PREVIEW = DATA_DIR / "print" / "campus" / "KAIST_map_PRINT_preview.png"
REFERENCE_PREVIEW = DEFAULT_MAP.with_name("KAIST_model_reference_2p5m.png")


@dataclass(frozen=True)
class RoadMap:
    region: np.ndarray
    edges: np.ndarray
    edge_similarity: np.ndarray
    centerline: np.ndarray
    junctions_xy_m: tuple[tuple[float, float], ...]
    from_print: bool

    @classmethod
    def build(cls, raster: MapRaster, *, fine: "RoadMap | None" = None) -> "RoadMap":
        from_print = False
        if fine is not None:
            region = _resample(fine.region, raster.size)
            from_print = fine.from_print
        else:
            reference = None
            if raster.campus.source == DEFAULT_MAP.resolve():
                for path in (PRINT_PREVIEW, REFERENCE_PREVIEW):
                    if path.is_file():
                        reference = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
                        if reference is not None:
                            break
            from_print = reference is not None
            if reference is None:
                reference = raster.render()
            region = np.zeros(reference.shape[:2], np.uint8)
            for color in {hex_to_bgr(road["display_color"]) for road in raster.campus.roads}:
                region |= cv2.inRange(reference, color, color)
            region = _resample(region, raster.size)
        edges = region - cv2.erode(region, np.ones((3, 3), np.uint8))
        distance = cv2.distanceTransform(1 - edges, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        tolerance_px = max(1.5, 0.024 * raster.pixels_per_m)
        edge_similarity = np.maximum(0, 1 - distance / tolerance_px)
        centerline = np.zeros_like(region)
        branches = defaultdict(set)
        for road in raster.campus.roads:
            points = road["centerline_xy_m"]
            cv2.polylines(centerline, [_vertices(raster.xy_to_uv, points)], False, 1, shift=8)
            vertices = [tuple(round(component, 5) for component in point) for point in points]
            for start, end in zip(vertices, vertices[1:]):
                if start != end:
                    branches[start].add(end)
                    branches[end].add(start)
        junctions = tuple(sorted(point for point, neighbors in branches.items() if len(neighbors) >= 3))
        return cls(region, edges, edge_similarity, centerline, junctions, from_print)


def _resample(mask, size):
    mask = (mask > 0).astype(np.float32)
    return (cv2.resize(mask, size, interpolation=cv2.INTER_AREA) >= 0.40).astype(np.uint8)
