"""OpenCV AprilTag decoding adapted from the detection project's photo_finder.py."""

from dataclasses import dataclass

import cv2
import numpy as np


DICTIONARIES = {family: f"DICT_APRILTAG_{family[3:]}" for family in
                ("tag16h5", "tag25h9", "tag36h10", "tag36h11")}


@dataclass(frozen=True)
class TagDetection:
    tag_id: int
    tag_family: str
    corners_px: tuple  # canonical marker TL, TR, BR, BL, even when rotated in the image
    frame_id: int
    timestamp: float

    @property
    def center_px(self):
        return tuple(np.mean(self.corners_px, axis=0))

    @property
    def min_side_px(self):
        corners = np.asarray(self.corners_px)
        return float(np.min(np.linalg.norm(corners - np.roll(corners, 1, axis=0), axis=1)))


def make_tag_detector(family):
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICTIONARIES[family]))
    return cv2.aruco.ArucoDetector(dictionary, params)


def detect_tags(image, detector, family, frame_id, timestamp):
    if image.ndim == 3:
        code = cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        image = cv2.cvtColor(image, code)
    corners, ids, _ = detector.detectMarkers(image)
    if ids is None:
        return ()
    return tuple(TagDetection(int(i), family, tuple(tuple(map(float, p)) for p in c.reshape(4, 2)),
                              frame_id, timestamp) for i, c in zip(ids.ravel(), corners))
