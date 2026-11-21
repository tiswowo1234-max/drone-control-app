"""Read and write OpenCV images using filesystem paths that may contain Korean characters."""
from pathlib import Path

import cv2
import numpy as np


def read_image(path: Path | str, flags: int) -> np.ndarray | None:
    try:
        encoded = Path(path).read_bytes()
    except OSError:
        return None
    if not encoded:
        return None
    return cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), flags)


def write_image(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise OSError(f"Cannot encode image for {path}")
    path.write_bytes(encoded.tobytes())
