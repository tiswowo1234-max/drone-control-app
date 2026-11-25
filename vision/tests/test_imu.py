"""드론 기울기(중력 방향)·거리 센서 높이 검사 (2026-11-23)."""
import dataclasses
import math

import numpy as np

from bridge.geometry import gravity_in_camera, look_down
from bridge.localizers import TtDownLocalizer
from bridge.synthetic import SyntheticCamera


def test_level_drone_gravity_is_optical_axis():
    assert np.allclose(gravity_in_camera(0, 0, -90), [0, 0, 1])


def test_gravity_matches_synthetic_tilt_for_tt_mount():
    # 합성 카메라가 화면 위쪽(TT: 드론 오른쪽)으로 τ 기울면 드론 roll = -τ
    for tau in (6.0, -9.0):
        R, _ = look_down(1.0, 1.0, 0.5, 40.0, tau)
        g_true = R @ np.array([0, 0, -1.0])
        assert np.degrees(np.arccos(np.clip(g_true @ gravity_in_camera(0, -tau, -90), -1, 1))) < 0.01


def test_pitch_nose_up_moves_gravity_toward_drone_back():
    # TT 장착: 화면 오른쪽 = 드론 뒤쪽 → 기수를 들면 중력이 화면 오른쪽(+x) 성분을 갖는다
    g = gravity_in_camera(10, 0, -90)
    assert g[0] > 0.1 and abs(g[1]) < 1e-9


def _tilted_case(settings, scene, tau=8.0, tof_err=0.0):
    s = dataclasses.replace(settings, tt_tag_roi=1.0, tt_tof_check_cm=15.0)
    cam = SyntheticCamera(scene, s.tt_width_px, s.tt_height_px, s.tt_hfov_deg, False, seed=1, degrade=False)
    img = cam.render(0.91, 0.81, 0.5, 30.0, tau)
    drone = {"pitch_deg": 0.0, "roll_deg": -tau, "tof_cm": 50.0 / math.cos(math.radians(tau)) + tof_err}
    return TtDownLocalizer(s).process(img, 0.0, drone)


def test_imu_used_and_position_good_when_tilted(settings, scene):
    r = _tilted_case(settings, scene)
    assert r.ok and r.imu_used and r.height_reject is None
    assert math.hypot(r.x_m - 0.91, r.y_m - 0.81) < 0.03


def test_height_mismatch_rejects_pose(settings, scene):
    r = _tilted_case(settings, scene, tof_err=40.0)
    assert r.height_reject is not None and not r.ok


def test_no_drone_state_falls_back_to_level_prior(settings, scene):
    s = dataclasses.replace(settings, tt_tag_roi=1.0)
    cam = SyntheticCamera(scene, s.tt_width_px, s.tt_height_px, s.tt_hfov_deg, False, seed=1, degrade=False)
    r = TtDownLocalizer(s).process(cam.render(0.91, 0.81, 0.5, 30.0), 0.0, None)
    assert r.ok and not r.imu_used
