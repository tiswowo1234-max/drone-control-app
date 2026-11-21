"""Render synchronized road diagnostics for live and recorded observations."""

import cv2
import numpy as np


def road_views(original, palette, labels, road, projection):
    colors = np.asarray(((0, 0, 0), *palette), np.uint8)
    observed = road.region.astype(bool)
    projected = projection.astype(bool)
    overlay = original.copy()
    for mask, color in ((observed, (230, 110, 0)), (projected, (0, 190, 255)),
                        (observed & projected, (40, 220, 70))):
        overlay[mask] = np.rint(original[mask] * 0.47 + np.asarray(color) * 0.53).astype(np.uint8)
    return {
        "Original": original,
        "Building mask": colors[labels],
        "Road mask": road.region * 255,
        "Road edges": road.edges * 255,
        "Road projection": projection * 255,
        "Hybrid overlay": overlay,
    }


def road_panel(views):
    tiles = []
    for name, pixels in views.items():
        tile = cv2.cvtColor(pixels, cv2.COLOR_GRAY2BGR) if pixels.ndim == 2 else pixels.copy()
        cv2.putText(tile, name, (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(tile, name, (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(tile)
    return np.vstack((np.hstack(tiles[:3]), np.hstack(tiles[3:])))
