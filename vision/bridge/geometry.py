"""좌표·방향 변환과 카메라 투영 (위치 계산과 슬롯 촬영이 같이 쓴다).

방향(yaw) 기준 — 시험(tests/test_yaw.py)으로 확인한 결과:
- 명세(docs/api.md 2·7장): 지도 위쪽(+y)이 0, **시계 방향** +.
- 조원 localization_app (esp32): `Pose.yaw_deg`, `TrackingResult.yaw_deg` = "북쪽 기준 **반시계**"
  → 명세 yaw = (-조원값) mod 360  (명세 7.1의 변환 그대로)
- 조원 incident_detection (tt_down): `Pose.heading_deg` = "화면 위쪽이 가리키는 나침반 방위(0=북, 90=동)" = 이미 **시계** 방향
  → 명세 yaw = 조원값 mod 360  (부호를 바꾸면 안 된다)
여기서 yaw는 "카메라 화면 위쪽"이 가리키는 방향이다. 드론 앞쪽과 같으려면 카메라 화면 위쪽이 드론 앞을 향하게 달아야 한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


def yaw_from_ccw(deg: float) -> float:
    """조원 '북쪽 기준 반시계' → 명세 '위쪽 기준 시계'."""
    return (-float(deg)) % 360.0


def yaw_from_compass(deg: float) -> float:
    """조원 '나침반(시계)' → 명세 (같은 기준)."""
    return float(deg) % 360.0


def gravity_in_camera(pitch_deg: float, roll_deg: float, mount_yaw_deg: float) -> np.ndarray:
    """드론 기울기(pitch 기수 위 +, roll 오른쪽 아래 +) → 하향 카메라 좌표(x 오른쪽, y 아래, z 광축)의 '아래' 단위벡터.

    몸체 좌표 FRD(x 앞, y 오른쪽, z 아래)에서 중력 = (-sin p, sin r cos p, cos r cos p).
    카메라 장착: 드론 앞쪽 = 화면 위쪽 + mount_yaw_deg(시계 +) → 화면 위쪽은 앞쪽에서 시계 방향 -mount 만큼.
    TT 하단 카메라(mount -90°): 화면 위쪽 = 드론 오른쪽, 화면 오른쪽 = 드론 뒤쪽.
    """
    p, r = math.radians(pitch_deg), math.radians(roll_deg)
    g = np.array([-math.sin(p), math.sin(r) * math.cos(p), math.cos(r) * math.cos(p)])
    a = math.radians(-mount_yaw_deg)
    top = np.array([math.cos(a), math.sin(a)])                      # (앞, 오른쪽) 성분
    right = np.array([math.cos(a + math.pi / 2), math.sin(a + math.pi / 2)])
    v = np.array([g[:2] @ right, -(g[:2] @ top), g[2]])
    return v / np.linalg.norm(v)


def look_down(x: float, y: float, height: float, yaw_cw_deg: float, tilt_deg: float = 0.0):
    """지도→카메라 회전 R, 이동 t. 화면 위쪽이 yaw_cw_deg(명세 yaw) 방향을 보는 하향 카메라.
    조원 sim_camera.look_down과 같은 식(heading = 나침반 방위 = 명세 yaw)."""
    h, tau = math.radians(yaw_cw_deg), math.radians(tilt_deg)
    up = np.array([math.sin(h), math.cos(h), 0.0])
    right = np.array([math.cos(h), -math.sin(h), 0.0])
    down = np.array([0.0, 0.0, -1.0])
    axis = down * math.cos(tau) + up * math.sin(tau)
    image_down = -up * math.cos(tau) + down * math.sin(tau)
    R = np.stack([right, image_down, axis])
    return R, -R @ np.array([x, y, height], float)


@dataclass
class CameraView:
    """한 장의 화면이 지도 어디를 보는지: 지도 점(m, z=0) → 화면 픽셀."""
    K: np.ndarray
    dist: np.ndarray
    R: np.ndarray  # 지도 → 카메라
    t: np.ndarray
    width: int
    height: int

    def project(self, xy_m) -> np.ndarray:
        """(N,2) 지도 m → (N,2) 픽셀. 카메라 뒤쪽 점은 NaN."""
        xy = np.asarray(xy_m, float).reshape(-1, 2)
        pts = np.c_[xy, np.zeros(len(xy))]
        cam = pts @ self.R.T + self.t.reshape(1, 3)
        out = np.full((len(xy), 2), np.nan)
        ok = cam[:, 2] > 1e-6
        if ok.any():
            rvec = cv2.Rodrigues(self.R)[0]
            px, _ = cv2.projectPoints(pts[ok], rvec, self.t.reshape(3, 1), self.K, self.dist)
            out[ok] = px.reshape(-1, 2)
        return out

    def pixels_to_map(self, px) -> np.ndarray:
        pts = np.asarray(px, np.float64).reshape(-1, 1, 2)
        norm = cv2.undistortPoints(pts, self.K, self.dist).reshape(-1, 2)
        rays = np.c_[norm, np.ones(len(norm))] @ self.R
        c = -self.R.T @ self.t.reshape(3)
        with np.errstate(divide="ignore", invalid="ignore"):
            lam = -c[2] / rays[:, 2]
        out = c[None, :2] + lam[:, None] * rays[:, :2]
        out[~(lam > 0)] = np.nan
        return out


def nadir_view(x: float, y: float, yaw_cw_deg: float, span_m: float, width: int, height: int,
               height_m: float | None = None) -> CameraView:
    """위치·방향·화면 폭(span, 지도 m)만 아는 경우(esp32 지도 정합)의 수직 하향 핀홀 카메라.
    높이는 아무 값이나 써도 투영 결과가 같다(평면 + 수직). 초점거리 f = width·h/span."""
    h = height_m if height_m and height_m > 0 else 1.0
    f = width * h / span_m
    K = np.array([[f, 0, (width - 1) / 2], [0, f, (height - 1) / 2], [0, 0, 1.0]])
    R, t = look_down(x, y, h, yaw_cw_deg)
    return CameraView(K, np.zeros(5), R, t, width, height)


def pinhole_K(width: int, height: int, hfov_deg: float) -> np.ndarray:
    f = width / 2 / math.tan(math.radians(hfov_deg) / 2)
    return np.array([[f, 0, (width - 1) / 2], [0, f, (height - 1) / 2], [0, 0, 1.0]])


def bilinear_latlon(x_m: float, y_m: float, map_w_m: float, map_h_m: float, corners: dict) -> tuple[float, float]:
    """명세 [geo]의 네 모서리 쌍선형 보간 (조원 geo.py 결과 검증용)."""
    u, v = x_m / map_w_m, y_m / map_h_m
    w = {"sw": (1 - u) * (1 - v), "se": u * (1 - v), "nw": (1 - u) * v, "ne": u * v}
    return (sum(w[k] * corners[k][0] for k in w), sum(w[k] * corners[k][1] for k in w))
