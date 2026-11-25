"""합성 카메라 (명세 12장): 드론 참값 위치에서 조원 지도 그림을 보이는 대로 그린다.

- tt_down: 흑백 320×240 (조원 sim_camera.render 그대로 — 흐림·잡음·JPEG 등 열화 포함)
- esp32  : 컬러 esp32_width×esp32_height (sim_camera.render와 같은 원근 변환, 컬러용 가벼운 열화)
지도 그림: 조원 data/maps/map_with_photos(_gray).png (AprilTag + 슬롯 사진). 사진 없는 판은 --synthetic-map tags.
"""
from __future__ import annotations

import math
import threading

import cv2
import numpy as np

from . import teammate  # noqa: F401  (sys.path)
from .geometry import CameraView, look_down, pinhole_K

import sim_camera  # 조원 incident_detection/localization/sim_camera.py
from camera_model import CameraModel
from image_io import read_image

FLOOR_BGR = (sim_camera.FLOOR_GRAY,) * 3


class MapScene:
    """지도 그림 피라미드 (흑백·컬러를 처음 쓸 때 한 번씩 읽는다)."""

    def __init__(self, size_m: float, kind: str = "photos", extra: bool = False):
        if kind not in ("photos", "tags"):
            raise ValueError("synthetic map은 photos 또는 tags")
        self.size_m, self.kind, self.extra = size_m, kind, extra  # extra: 슬롯 보드 태그가 그려진 그림
        self._levels: dict[bool, list[np.ndarray]] = {}
        self._lock = threading.Lock()

    def levels(self, color: bool) -> list[np.ndarray]:
        with self._lock:
            if color not in self._levels:
                name = f"map_with_{self.kind}" + ("" if color else "_gray") + ".png"
                path = teammate.map_file(name, self.extra)
                img = read_image(path, cv2.IMREAD_COLOR if color else cv2.IMREAD_GRAYSCALE)
                if img is None:
                    raise FileNotFoundError(path)
                levels = [img]
                while min(levels[-1].shape[:2]) > 600:
                    levels.append(cv2.resize(levels[-1], None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))
                self._levels[color] = levels
            return self._levels[color]


class SyntheticCamera:
    def __init__(self, scene: MapScene, width: int, height: int, hfov_deg: float, color: bool,
                 seed: int | None = None, degrade: bool = True):
        self.scene, self.width, self.height, self.color = scene, width, height, color
        self.K = pinhole_K(width, height, hfov_deg)
        self.hfov_deg = hfov_deg
        self.rng = np.random.default_rng(seed)
        self.degrade = degrade

    def view(self, x: float, y: float, z: float, yaw_deg: float, tilt_deg: float = 0.0) -> CameraView:
        R, t = look_down(x, y, z, yaw_deg, tilt_deg)
        return CameraView(self.K, np.zeros(5), R, t, self.width, self.height)

    def render(self, x: float, y: float, z: float, yaw_deg: float, tilt_deg: float = 0.0) -> np.ndarray:
        """yaw_deg = 명세 yaw(지도 위쪽 기준 시계). 결과: 흑백 (H,W) 또는 컬러 BGR (H,W,3)."""
        R, t = look_down(x, y, z, yaw_deg, tilt_deg)
        if not self.color:
            levels = self.scene.levels(False)
            sc = sim_camera.Scene(levels, self.scene.size_m, None, [])
            cam = CameraModel(self.K, np.zeros(5), self.width, self.height, "synthetic", self.hfov_deg)
            return sim_camera.render(sc, cam, R, t, self.rng, sim_camera.Degrade() if self.degrade else None)
        return self._render_color(R, t)

    def _render_color(self, R, t) -> np.ndarray:
        # sim_camera.render와 같은 원근 변환 (컬러 3채널)
        levels = self.scene.levels(True)
        px_per_mm = sim_camera.PX_PER_MM
        center = -R.T @ t
        gsd = center[2] / max(R[2] @ np.array([0, 0, -1.0]), 0.3) / self.K[0, 0]
        plane_m = 1 / (px_per_mm * 1000)
        level = int(np.clip(math.floor(math.log2(max(gsd / 2 / plane_m, 1))), 0, len(levels) - 1))
        img_l = levels[level]
        s = px_per_mm * 1000 / 2 ** level
        size_m = self.scene.size_m
        to_map = np.array([[1 / s, 0, 0.5 / s], [0, -1 / s, size_m - 0.5 / s], [0, 0, 1]])
        ss = 2
        k = self.K.copy()
        k[:2, :2] *= ss
        k[0, 2], k[1, 2] = (k[0, 2] + 0.5) * ss - 0.5, (k[1, 2] + 0.5) * ss - 0.5
        hom = k @ np.c_[R[:, 0], R[:, 1], t] @ to_map
        img = cv2.warpPerspective(img_l, hom, (self.width * ss, self.height * ss), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=FLOOR_BGR)
        img = cv2.resize(img, (self.width, self.height), interpolation=cv2.INTER_AREA)
        if not self.degrade:
            return img
        f = cv2.GaussianBlur(img.astype(np.float32), (0, 0), self.rng.uniform(0.4, 0.8))
        f = f * self.rng.uniform(0.9, 1.05) + self.rng.normal(0, 2.0, f.shape)
        img = np.clip(f, 0, 255).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
