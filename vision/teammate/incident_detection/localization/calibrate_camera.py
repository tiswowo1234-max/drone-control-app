"""Calibrate the grayscale camera from photos of a printed chessboard, and write data/calibration/incident_camera.json.

Accurate height and position need the real focal length and lens distortion; the field-of-view
guess in settings.py (CameraConfig) is only good enough for the simulator.

How to:
  1. Print a chessboard (e.g. 10 x 7 squares of 25 mm) on stiff paper; measure the square size.
  2. Take 15-30 photos with the drone camera at the resolution you will fly with: board in
     different corners of the image, tilted up to ~45 degrees, near and far.
  3. python calibrate_camera.py photos/*.png --cols 9 --rows 6 --square-mm 25
     (cols/rows count INNER corners: a 10 x 7 square board has 9 x 6.)
An RMS reprojection error under ~0.5 px is good; above 1 px, drop blurry photos and retake.
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

from camera_model import CameraModel
from settings import LocalizerConfig
from image_io import read_image


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--cols", type=int, required=True, help="inner corners per row")
    parser.add_argument("--rows", type=int, required=True, help="inner corners per column")
    parser.add_argument("--square-mm", type=float, required=True)
    parser.add_argument("--out", type=Path, default=LocalizerConfig().camera_file)
    args = parser.parse_args()

    board = np.zeros((args.rows * args.cols, 3), np.float32)
    board[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2) * args.square_mm / 1000
    object_points, image_points, size = [], [], None
    for path in args.images:
        gray = read_image(path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            print(f"skip {path.name}: cannot read")
            continue
        if size and gray.shape[::-1] != size:
            print(f"skip {path.name}: {gray.shape[1]}x{gray.shape[0]}, others are {size[0]}x{size[1]}")
            continue
        size = gray.shape[::-1]
        found, corners = cv2.findChessboardCornersSB(gray, (args.cols, args.rows), flags=cv2.CALIB_CB_ACCURACY)
        print(f"{path.name}: {'ok' if found else 'board not found'}")
        if found:
            object_points.append(board)
            image_points.append(corners.astype(np.float32))
    if len(image_points) < 8:
        raise SystemExit(f"only {len(image_points)} usable photos; take at least 8 (15-30 is better)")

    # k3 overfits easily on ordinary (non-fisheye) small lenses, so it is held at zero
    rms, K, dist, _, _ = cv2.calibrateCamera(object_points, image_points, size, None, None,
                                             flags=cv2.CALIB_FIX_K3)
    CameraModel(K, dist.ravel(), size[0], size[1], "calibration").save(args.out, rms)
    print(f"RMS reprojection error {rms:.3f} px over {len(image_points)} photos")
    print(f"fx {K[0, 0]:.1f}  fy {K[1, 1]:.1f}  cx {K[0, 2]:.1f}  cy {K[1, 2]:.1f}  dist {np.round(dist.ravel(), 4)}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
