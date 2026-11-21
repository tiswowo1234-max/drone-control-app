"""yaw 기준 확인: 합성 화면(참값 yaw = 명세 기준, 지도 위쪽 0·시계 +)을 조원 코드에 넣어 본다.

결과(2026-11-21): localization_app(esp32)은 '북쪽 기준 반시계' → (-값) mod 360,
incident_detection(tt_down)의 heading은 '나침반(시계)' → 그대로. 모듈마다 기준이 다르다.
"""
import math

import numpy as np
import pytest

from bridge.geometry import look_down, yaw_from_ccw, yaw_from_compass
from bridge.localizers import Esp32Localizer, TtDownLocalizer
from bridge.synthetic import SyntheticCamera


def ang(a, b):
    return abs((a - b + 180) % 360 - 180)


def test_conversions():
    assert yaw_from_ccw(30) == 330
    assert yaw_from_ccw(0) == 0
    assert yaw_from_ccw(-90) == 90
    assert yaw_from_compass(370) == 10


def test_look_down_is_clockwise():
    # yaw 90 (시계) → 화면 위쪽이 지도 +x(오른쪽)를 본다
    R, _ = look_down(1, 1, 1, 90)
    image_up_in_map = R.T @ np.array([0, -1.0, 0])
    assert np.allclose(image_up_in_map, [1, 0, 0], atol=1e-9)


def test_teammate_localization_app_yaw_is_ccw():
    """조원 camera_simulator(localization_app)의 Pose.yaw_deg = 반시계: 같은 화면을 우리 look_down(시계)과 비교."""
    from bridge import teammate  # noqa: F401
    from localization.camera_simulator import SyntheticCamera as TmCam, CameraConfig, Pose
    from localization.map_loader import CampusMap, MapRaster
    import cv2
    raster = MapRaster(CampusMap.load(), 320)
    cam = TmCam(raster, CameraConfig(width_px=160, height_px=120))
    M = cam.map_transform(Pose(1.2, 1.2, 30.0))  # 카메라 픽셀 → 지도 픽셀 (v 아래쪽)
    up_px = M[:, :2] @ np.array([0, -1.0])       # 화면 위쪽 방향 (지도 픽셀)
    up_map = np.array([up_px[0], -up_px[1]])      # 지도 m (y 위쪽)
    cw = math.degrees(math.atan2(up_map[0], up_map[1])) % 360
    assert ang(cw, 330) < 1e-6  # 반시계 30° = 시계 330°


@pytest.mark.parametrize("yaw", [30.0, 100.0, 250.0])
def test_tt_down_yaw(settings, scene, yaw):
    cam = SyntheticCamera(scene, settings.tt_width_px, settings.tt_height_px, settings.tt_hfov_deg, False,
                          seed=1, degrade=False)
    loc = TtDownLocalizer(settings)
    # 태그가 많이 보이는 곳 (0.91, 0.81), 높이를 조금 낮춰 확실히 인식
    res = loc.process(cam.render(0.91, 0.81, 0.6, yaw), 0.0)
    assert res.ok, res.status
    assert ang(res.yaw_deg, yaw) < 3, (res.yaw_deg, yaw)


@pytest.mark.parametrize("yaw", [30.0, 100.0, 250.0])
def test_esp32_yaw(settings, scene, yaw):
    cam = SyntheticCamera(scene, settings.esp32_width_px, settings.esp32_height_px, 68.0, True, seed=1, degrade=False)
    loc = Esp32Localizer(settings, synthetic=True)
    res = None
    for i in range(3):
        res = loc.process(cam.render(1.0, 1.6, settings.default_alt_m, yaw), 0.2 * i)
    assert res.ok, res.status
    assert ang(res.yaw_deg, yaw) < 6, (res.yaw_deg, yaw)
    assert math.hypot(res.x_m - 1.0, res.y_m - 1.6) < 0.05


# ---------------------------------------------------------------- 카메라 장착 각도 (2026-11-21 실물: TT 화면이 앞쪽 기준 반시계 90°)

def test_tt_mount_yaw_reports_drone_heading(settings, scene):
    """합성 카메라를 실물처럼 -90° 돌려 달고 그리면, 보내는 yaw는 드론 앞쪽 방향이어야 한다."""
    import dataclasses
    from types import SimpleNamespace
    from bridge.app import Bridge
    s = dataclasses.replace(settings, tt_mount_yaw_deg=-90.0)
    assert s.mount_yaw("tt_down") == -90.0
    drone_yaw = 40.0
    cam = SyntheticCamera(scene, s.tt_width_px, s.tt_height_px, s.tt_hfov_deg, False, seed=1, degrade=False)
    loc = TtDownLocalizer(s)
    img = cam.render(0.91, 0.81, 0.6, (drone_yaw - s.mount_yaw("tt_down")) % 360)  # SyntheticSource와 같은 방식
    res = loc.process(img, 0.0)
    assert res.ok, res.status
    fake = SimpleNamespace(s=s, mode="tt_down")
    assert ang(Bridge.drone_yaw(fake, res), drone_yaw) < 3


def test_video_frame_forward_up_rotation():
    import numpy as np
    from bridge.app import video_frame
    img = np.zeros((240, 320), np.uint8)
    img[:, :20] = 255  # 화면 왼쪽 띠 = 드론 앞쪽 (장착 -90°)
    out = video_frame(img, 320, -90.0, True)
    assert out.shape == (320, 240)            # 시계 방향 90° → 세로 화면
    assert out[:20, :].mean() > 200            # 앞쪽(왼쪽 띠)이 위로
    assert video_frame(img, 320, -90.0, False).shape == (240, 320)  # 끄면 그대로
    assert video_frame(img, 160, 0.0, True).shape == (120, 160)     # 긴 변 기준 축소
