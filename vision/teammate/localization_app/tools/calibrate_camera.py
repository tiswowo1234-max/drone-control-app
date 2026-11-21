"""Collect, calibrate, and measure the ground footprint of a camera."""

import argparse
from pathlib import Path
import sys

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from localization.camera_stream import XiaoCameraSource


def read_image(path):
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot decode {path}")
    return image


def write_image(path, image):
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError(f"cannot encode {path}")
    path.write_bytes(encoded.tobytes())


def collect(url, directory, count):
    directory.mkdir(parents=True, exist_ok=True)
    source = XiaoCameraSource(url)
    source.start()
    saved = 0
    try:
        while saved < count:
            frame = source.read(timeout=0.2)
            if frame is None:
                status = source.status()
                if status.state == "failed":
                    raise ValueError(status.error)
            else:
                cv2.imshow("Chessboard: SPACE save, ESC quit", frame.image)
            key = cv2.waitKey(10) & 0xff
            if key == 27:
                break
            if key == 32 and frame is not None:
                path = directory / f"board-{frame.timestamp:.6f}.png"
                write_image(path, frame.image)
                saved += 1
                print(f"{saved}/{count}: {path}")
    finally:
        source.close()
        cv2.destroyAllWindows()


def calibrate(directory, pattern, square_mm, output, preview):
    if min(pattern) < 3 or not np.isfinite(square_mm) or square_mm <= 0:
        raise ValueError("specify at least 3×3 inner corners and a positive square size")
    model = np.zeros((pattern[0] * pattern[1], 3), np.float32)
    model[:, :2] = np.mgrid[:pattern[0], :pattern[1]].T.reshape(-1, 2) * square_mm
    corners = []
    size = sample = None
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp"):
            continue
        image = read_image(path)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        image_size = gray.shape[::-1]
        if size is not None and size != image_size:
            raise ValueError("calibration images must have the same resolution")
        size = image_size
        found, points = cv2.findChessboardCornersSB(gray, pattern)
        if found:
            corners.append(points)
            sample = image
    if len(corners) < 10:
        raise ValueError(f"found corners in {len(corners)} images; at least 10 views are required")
    rms, matrix, distort, *_ = cv2.calibrateCamera([model] * len(corners), corners, size, None, None)
    if not np.isfinite(rms) or not np.isfinite(matrix).all() or not np.isfinite(distort).all():
        raise ValueError("calibration did not converge")
    if output.suffix.lower() != ".npz":
        raise ValueError("calibration output must end in .npz")
    np.savez(output, camera_matrix=matrix, dist_coeffs=distort, image_size=size,
             rms_reprojection_px=rms)
    if preview:
        write_image(preview, np.hstack((sample, cv2.undistort(sample, matrix, distort))))
    print(f"{len(corners)} views, {size}, RMS reprojection error {rms:.3f} px")
    print(matrix)
    print(f"distortion: {distort.ravel()}")


def ground_span(image_width, length_m, x1, x2):
    """Extrapolate a known horizontal ground segment across a nadir frame."""
    if (image_width < 2 or not np.isfinite((length_m, x1, x2)).all() or length_m <= 0
            or not 0 <= x1 < image_width or not 0 <= x2 < image_width or x1 == x2):
        raise ValueError("enter positive ground length and distinct x coordinates inside the frame")
    return length_m * image_width / abs(x2 - x1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    capture = sub.add_parser("collect")
    capture.add_argument("--url", required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--count", type=int, default=15)
    fit = sub.add_parser("fit")
    fit.add_argument("--images", type=Path, required=True)
    fit.add_argument("--corners", type=int, nargs=2, required=True)
    fit.add_argument("--square-mm", type=float, required=True)
    fit.add_argument("--output", type=Path, required=True)
    fit.add_argument("--preview", type=Path)
    span = sub.add_parser("span")
    span.add_argument("--image", type=Path, required=True, help="oriented, optionally undistorted image")
    span.add_argument("--length-m", type=float, required=True)
    span.add_argument("--x", nargs=2, type=float, required=True, help="horizontal endpoints in pixels")
    args = parser.parse_args()
    try:
        if args.mode == "collect":
            if args.count < 1:
                raise ValueError("--count must be positive")
            collect(args.url, args.output, args.count)
        elif args.mode == "fit":
            calibrate(args.images, tuple(args.corners), args.square_mm, args.output, args.preview)
        else:
            value = ground_span(read_image(args.image).shape[1], args.length_m, *args.x)
            print(f"nominal_span_m: {value:.6f}")
    except (OSError, ValueError, cv2.error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
