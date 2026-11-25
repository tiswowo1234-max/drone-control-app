"""UGV 인식 (따라가기 1단계): 하단 카메라 화면에서 UGV 윗면의 AprilTag를 찾아 드론 기준 위치·방향을 잰다.

좌표·방향 약속
- 카메라 좌표 (OpenCV): x = 화면 오른쪽, y = 화면 아래, z = 카메라가 보는 쪽(바닥)
- 화면 평면 (오른쪽, 위쪽) 성분: right = tvec.x, up = -tvec.y  (m, 태그 높이에서의 실제 거리)
- 드론 앞쪽 = 화면 위쪽 + mount_yaw_deg (시계 +, [bridge] tt_mount_yaw_deg)
- UGV 앞쪽 = 태그 위쪽(인쇄물의 ▲) + [ugv] tag_yaw_deg
- 지도 yaw는 명세 기준 (지도 위쪽 0, 시계 +). 위치 계산 결과의 yaw_deg = "화면 위쪽"이 가리키는 지도 방향.

지도 위치 계산(드론 위치 + 상대 위치)은 카메라가 수평이라고 가정한다(TT 호버링 중 기울기 수 도 → 45cm 거리에서 수 cm 오차).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np

from .config import UgvConfig

log = logging.getLogger("bridge.ugv")

ARUCO_DICT = cv2.aruco.DICT_APRILTAG_36h11
MAP_TAG_IDS = range(0, 39)  # 조원 apriltag_map.json (지도 태그 0~38)


def tag_object_points(size_m: float) -> np.ndarray:
    """OpenCV aruco 모서리 순서(왼쪽 위, 오른쪽 위, 오른쪽 아래, 왼쪽 아래) = SOLVEPNP_IPPE_SQUARE 순서."""
    h = size_m / 2
    return np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], np.float64)


def image_to_body(right_m: float, up_m: float, mount_yaw_deg: float) -> tuple[float, float]:
    """화면 평면 (오른쪽, 위쪽) → 드론 (앞, 오른쪽)."""
    m = math.radians(mount_yaw_deg)
    fwd = right_m * math.sin(m) + up_m * math.cos(m)
    rgt = right_m * math.cos(m) - up_m * math.sin(m)
    return fwd, rgt


def image_to_map(right_m: float, up_m: float, image_up_yaw_deg: float) -> tuple[float, float]:
    """화면 평면 (오른쪽, 위쪽) → 지도 (dx, dy). image_up_yaw_deg = 화면 위쪽이 가리키는 지도 방향(시계 +)."""
    y = math.radians(image_up_yaw_deg)
    return (up_m * math.sin(y) + right_m * math.cos(y),
            up_m * math.cos(y) - right_m * math.sin(y))


def wrap180(deg: float) -> float:
    d = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


@dataclass
class UgvResult:
    seen: bool
    t: float                                  # 화면 시각 (time.monotonic 기준)
    status: str = ""
    corners: np.ndarray | None = None         # (4,2) 화면 픽셀 (위치 계산 화면 기준, 회전 전)
    side_px: float = 0.0                      # 화면 속 태그 한 변 (px)
    forward_m: float | None = None            # 드론(카메라) 기준 UGV 위치: 앞 +
    right_m: float | None = None              #                              오른쪽 +
    below_m: float | None = None              # 카메라 ~ 태그 높이 차
    rel_yaw_deg: float | None = None          # UGV 앞쪽 - 드론 앞쪽 (시계 +, -180~180)
    map_x_m: float | None = None              # 지도 위치 (드론 위치를 알 때만)
    map_y_m: float | None = None
    map_yaw_deg: float | None = None          # UGV 앞쪽의 지도 방향 (명세 yaw)
    top_height_m: float | None = None         # 드론 높이 - below_m = 바닥에서 태그까지 추정 높이 (설정 확인용)
    tilt_deg: float = 0.0
    rms_px: float = 0.0
    tag_ids_seen: tuple = ()

    def line(self) -> str:
        """로그 한 줄 (사람이 읽는 형식)."""
        if not self.seen:
            return f"UGV 없음 ({self.status})" if self.status else "UGV 없음"
        s = (f"UGV 앞 {self.forward_m * 100:+.1f}cm 오른쪽 {self.right_m * 100:+.1f}cm "
             f"아래 {self.below_m * 100:.1f}cm, 방향 {self.rel_yaw_deg:+.0f}° (태그 {self.side_px:.0f}px)")
        if self.map_x_m is not None:
            s += f" | 지도 ({self.map_x_m:.3f}, {self.map_y_m:.3f}) m yaw {self.map_yaw_deg:.0f}°"
        if self.top_height_m is not None:
            s += f" | 태그 높이 추정 {self.top_height_m * 100:.0f}cm"
        return s


class UgvDetector:
    def __init__(self, cfg: UgvConfig):
        if cfg.tag_id in MAP_TAG_IDS:
            raise ValueError(f"[ugv] tag_id {cfg.tag_id}는 지도 태그 번호(0~38)와 겹칩니다. 39 이상을 쓰세요")
        self.cfg = cfg
        self.obj = tag_object_points(cfg.tag_size_m)
        self._detector = None

    def _detect(self, gray: np.ndarray):
        """위치 계산이 태그를 찾지 않는 모드(esp32)일 때만 직접 찾는다."""
        if self._detector is None:
            params = cv2.aruco.DetectorParameters()
            params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
            self._detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICT), params)
        corners, ids, _ = self._detector.detectMarkers(gray)
        if ids is None:
            return []
        return [(int(i), c.reshape(4, 2)) for i, c in zip(ids.ravel(), corners)]

    def measure(self, t: float, K: np.ndarray, dist: np.ndarray, mount_yaw_deg: float,
                tags=None, image: np.ndarray | None = None, drone=None) -> UgvResult:
        """tags: 위치 계산이 이미 찾은 태그(TagSeen 목록: .id, .corners). None이면 image에서 직접 찾는다.
        drone: 위치 계산 결과(PoseResult, ok일 때) → UGV 지도 위치까지 계산."""
        if tags is None:
            if image is None:
                return UgvResult(False, t, "화면 없음")
            gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            found = self._detect(gray)
        else:
            found = [(int(tg.id), np.asarray(tg.corners, float).reshape(4, 2)) for tg in tags]
        ids = tuple(sorted(i for i, _ in found))
        mine = [c for i, c in found if i == self.cfg.tag_id]
        if not mine:
            return UgvResult(False, t, "", tag_ids_seen=ids)
        corners = max(mine, key=lambda c: cv2.contourArea(c.astype(np.float32)))  # 혹시 둘이면 큰 쪽
        side = float(np.mean([np.linalg.norm(corners[i] - corners[i - 1]) for i in range(4)]))
        ok, rvecs, tvecs, errs = cv2.solvePnPGeneric(self.obj, corners.astype(np.float64), K, dist,
                                                     flags=cv2.SOLVEPNP_IPPE_SQUARE)
        if not ok or not rvecs:
            return UgvResult(False, t, "자세 계산 실패", corners=corners, side_px=side, tag_ids_seen=ids)
        # 평면 태그는 거울상 해 두 개가 나온다 → 태그 면이 카메라를 정면으로 보는(덜 기운) 쪽 (UGV 윗면은 수평)
        best = None
        for k, (rv, tv) in enumerate(zip(rvecs, tvecs)):
            R = cv2.Rodrigues(rv)[0]
            tilt = math.degrees(math.acos(float(np.clip(-R[2, 2], -1.0, 1.0))))
            rms = float(np.ravel(errs)[k]) if errs is not None and len(np.ravel(errs)) > k else 0.0
            if tv.ravel()[2] <= 0:
                continue
            if best is None or tilt < best[2]:
                best = (R, tv.ravel(), tilt, rms)
        if best is None:
            return UgvResult(False, t, "태그가 카메라 뒤", corners=corners, side_px=side, tag_ids_seen=ids)
        R, tv, tilt, rms = best
        if tilt > self.cfg.max_tilt_deg:
            return UgvResult(False, t, f"태그 기울기 {tilt:.0f}° > {self.cfg.max_tilt_deg:.0f}° (잘못 잡음?)",
                             corners=corners, side_px=side, tilt_deg=tilt, rms_px=rms, tag_ids_seen=ids)
        right, up, below = float(tv[0]), float(-tv[1]), float(tv[2])
        fwd, rgt = image_to_body(right, up, mount_yaw_deg)
        tag_up_cw = math.degrees(math.atan2(R[0, 1], -R[1, 1]))   # 태그 위쪽이 화면 위쪽에서 시계로 돈 각도
        ugv_cw_img = tag_up_cw + self.cfg.tag_yaw_deg
        res = UgvResult(True, t, "인식", corners=corners, side_px=side, forward_m=fwd, right_m=rgt, below_m=below,
                        rel_yaw_deg=wrap180(ugv_cw_img - mount_yaw_deg), tilt_deg=tilt, rms_px=rms, tag_ids_seen=ids)
        if drone is not None and getattr(drone, "ok", False) and drone.x_m is not None and drone.yaw_deg is not None:
            dx, dy = image_to_map(right, up, drone.yaw_deg)
            res.map_x_m, res.map_y_m = drone.x_m + dx, drone.y_m + dy
            res.map_yaw_deg = (drone.yaw_deg + ugv_cw_img) % 360
            if drone.z_m is not None:
                res.top_height_m = drone.z_m - below
        return res


# ---------------------------------------------------------------- 앱 영상 표시

def draw_overlay(img: np.ndarray, u: UgvResult) -> np.ndarray:
    """앱으로 보낼 화면(회전 전)에 UGV 태그 테두리와 앞쪽 화살표를 그린다 (컬러로 바꿔서)."""
    out = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img.copy()
    if u.corners is None:
        return out
    c = u.corners.astype(np.float32)
    color = (0, 200, 0) if u.seen else (0, 0, 255)
    cv2.polylines(out, [np.round(c).astype(np.int32)], True, color, 2, cv2.LINE_AA)
    if u.seen:
        center = c.mean(axis=0)
        top = (c[0] + c[1]) / 2   # 태그 위쪽 변 가운데 (tag_yaw_deg = 0이면 UGV 앞쪽)
        tip = center + 1.6 * (top - center)
        cv2.arrowedLine(out, tuple(np.round(center).astype(int)), tuple(np.round(tip).astype(int)),
                        (0, 220, 255), 2, cv2.LINE_AA, tipLength=0.35)
    return out


def overlay_text(img: np.ndarray, u: UgvResult | None) -> np.ndarray:
    """회전·축소한 뒤의 앱 화면 왼쪽 위에 한 줄 (cv2.putText는 한글이 안 되어 영문). u가 없거나 못 봤으면 "UGV --"."""
    out = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img.copy()
    if u is not None and u.seen:
        txt = f"UGV F{u.forward_m * 100:+.0f} R{u.right_m * 100:+.0f}cm {u.rel_yaw_deg:+.0f}deg"
        color = (0, 220, 0)
    else:
        txt, color = "UGV --", (0, 0, 255)
    font, scale = cv2.FONT_HERSHEY_SIMPLEX, 0.45
    (tw, th), base = cv2.getTextSize(txt, font, scale, 1)
    cv2.rectangle(out, (2, 2), (8 + tw, 8 + th + base), (0, 0, 0), -1)   # 글자 뒤 검은 상자 (밝은 지도에서도 읽히게)
    cv2.putText(out, txt, (5, 5 + th), font, scale, color, 1, cv2.LINE_AA)
    return out


# ---------------------------------------------------------------- 합성 카메라 / 인쇄물

def tag_plate(tag_id: int, tag_px: int = 160, quiet: float = 0.25) -> np.ndarray:
    """흰 테두리가 있는 태그 판 (흑백). quiet = 태그 한 변 대비 흰 여백 비율(한쪽)."""
    tag = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(ARUCO_DICT), tag_id, tag_px, borderBits=1)
    pad = int(round(tag_px * quiet))
    return cv2.copyMakeBorder(tag, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)


def render_ugv(img: np.ndarray, K: np.ndarray, R: np.ndarray, t: np.ndarray, ugv_xy, ugv_yaw_deg: float,
               cfg: UgvConfig, quiet: float = 0.25) -> np.ndarray:
    """합성 카메라 화면에 UGV 윗면(흰 판 + 태그)을 그려 넣는다. R, t = 지도 → 카메라 (look_down)."""
    plate = tag_plate(cfg.tag_id, 160, quiet)
    n = plate.shape[0]
    half = cfg.tag_size_m * (1 + 2 * quiet) / 2
    yaw = math.radians(ugv_yaw_deg - cfg.tag_yaw_deg)   # 태그 위쪽이 가리키는 지도 방향
    f = np.array([math.sin(yaw), math.cos(yaw)])
    r = np.array([math.cos(yaw), -math.sin(yaw)])
    c = np.asarray(ugv_xy, float)
    world = [c - r * half + f * half, c + r * half + f * half, c + r * half - f * half, c - r * half - f * half]
    pts = np.array([[p[0], p[1], cfg.height_m] for p in world])
    cam = pts @ R.T + np.asarray(t, float).reshape(1, 3)
    if np.any(cam[:, 2] <= 1e-3):
        return img
    px = (cam @ K.T)
    px = px[:, :2] / px[:, 2:3]
    src = np.array([[0, 0], [n, 0], [n, n], [0, n]], np.float32) - 0.5
    H = cv2.getPerspectiveTransform(src, px.astype(np.float32))
    h, w = img.shape[:2]
    warped = cv2.warpPerspective(plate, H, (w, h), flags=cv2.INTER_AREA, borderValue=0)
    mask = cv2.warpPerspective(np.full_like(plate, 255), H, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
    warped = cv2.GaussianBlur(warped, (0, 0), 0.5)
    a = (mask.astype(np.float32) / 255.0)
    if img.ndim == 3:
        warped = cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR)
        a = a[..., None]
    out = img.astype(np.float32) * (1 - a) + warped.astype(np.float32) * a
    return np.clip(out, 0, 255).astype(np.uint8)


class UgvPath:
    """합성 카메라용 UGV 참값: 고정(radius 0) 또는 (x, y) 중심 원 주행 (반시계, 진행 방향을 바라봄)."""

    def __init__(self, x: float, y: float, yaw_deg: float = 0.0, radius_m: float = 0.0, speed_m_s: float = 0.1):
        self.x, self.y, self.yaw, self.radius, self.speed = x, y, yaw_deg, radius_m, speed_m_s
        self.t0 = None

    def __call__(self, t: float) -> tuple[float, float, float]:
        if self.radius <= 0:
            return self.x, self.y, self.yaw
        if self.t0 is None:
            self.t0 = t
        a = (t - self.t0) * self.speed / self.radius   # 라디안
        x = self.x + self.radius * math.cos(a)
        y = self.y + self.radius * math.sin(a)
        # 반시계로 돌 때 진행 방향 = (-sin a, cos a) → 명세 yaw = atan2(dx, dy)
        yaw = math.degrees(math.atan2(-math.sin(a), math.cos(a))) % 360
        return x, y, yaw
