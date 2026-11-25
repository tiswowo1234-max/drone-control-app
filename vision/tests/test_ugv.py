"""UGV 인식 (따라가기 1단계): 합성 카메라에 UGV 태그를 그려 넣고 상대 위치·방향·지도 위치를 확인한다."""
import math

import numpy as np
import pytest

from bridge.localizers import make_localizer
from bridge.synthetic import SyntheticCamera
from bridge.ugv import UgvDetector, image_to_body, image_to_map, render_ugv, wrap180


def test_image_to_body_mount_minus_90():
    # TT 실물: 드론 앞쪽 = 화면 왼쪽 (tt_mount_yaw_deg = -90) → 화면 위쪽 = 드론 오른쪽
    f, r = image_to_body(-0.1, 0.0, -90)   # 화면 왼쪽 10cm
    assert f == pytest.approx(0.1) and r == pytest.approx(0.0, abs=1e-9)
    f, r = image_to_body(0.0, 0.1, -90)    # 화면 위쪽 10cm
    assert f == pytest.approx(0.0, abs=1e-9) and r == pytest.approx(0.1)
    f, r = image_to_body(0.0, 0.1, 0)      # 장착 0°: 화면 위쪽 = 앞
    assert f == pytest.approx(0.1) and r == pytest.approx(0.0, abs=1e-9)


def test_image_to_map_yaw():
    assert image_to_map(0.0, 0.1, 0) == pytest.approx((0.0, 0.1))     # 화면 위쪽이 지도 위쪽
    assert image_to_map(0.0, 0.1, 90) == pytest.approx((0.1, 0.0))    # 화면 위쪽이 지도 오른쪽(동)
    assert image_to_map(0.1, 0.0, 90) == pytest.approx((0.0, -0.1))   # 그때 화면 오른쪽 = 지도 아래쪽


def test_rejects_map_tag_id(settings):
    from dataclasses import replace
    with pytest.raises(ValueError):
        UgvDetector(replace(settings.ugv, tag_id=5))


def _frame(settings, scene, drone, ugv, seed=0):
    """drone = (x, y, z, 드론 앞쪽 yaw), ugv = (x, y, yaw). 실물과 같은 장착 각도로 그린다."""
    s, mount = settings, settings.tt_mount_yaw_deg
    cam = SyntheticCamera(scene, s.tt_width_px, s.tt_height_px, s.tt_hfov_deg, color=False, seed=seed)
    x, y, z, yaw = drone
    cam_yaw = (yaw - mount) % 360
    img = cam.render(x, y, z, cam_yaw)
    v = cam.view(x, y, z, cam_yaw)
    return render_ugv(img, v.K, v.R, v.t, ugv[:2], ugv[2], s.ugv)


def _expected(drone, ugv, height):
    """참값으로 계산한 드론 기준 (앞, 오른쪽, 아래, 상대 방향)."""
    x, y, z, yaw = drone
    dx, dy = ugv[0] - x, ugv[1] - y
    a = math.radians(yaw)
    fwd = dx * math.sin(a) + dy * math.cos(a)
    rgt = dx * math.cos(a) - dy * math.sin(a)
    return fwd, rgt, z - height, wrap180(ugv[2] - yaw)


CASES = [  # (드론 x, y, z, yaw), (UGV x, y, yaw) — 50cm 비행, UGV가 드론 앞·옆·뒤 여러 곳
    ((1.20, 1.20, 0.50, 0.0), (1.20, 1.32, 0.0)),     # 바로 앞 12cm, 같은 방향 (따라가기 기본 자세)
    ((1.20, 1.20, 0.50, 0.0), (1.28, 1.25, 45.0)),
    ((0.80, 1.60, 0.50, 90.0), (0.95, 1.55, 120.0)),
    ((1.50, 0.80, 0.50, 200.0), (1.42, 0.70, 170.0)),
    ((1.00, 1.00, 0.50, -30.0), (0.95, 1.06, -90.0)),
]


@pytest.mark.parametrize("drone,ugv", CASES)
def test_relative_pose_synthetic(settings, scene, drone, ugv):
    det = UgvDetector(settings.ugv)
    img = _frame(settings, scene, drone, ugv)
    loc = make_localizer("tt_down", settings)
    K, dist = loc.camera_matrix(img.shape[1], img.shape[0])
    u = det.measure(0.0, K, dist, settings.tt_mount_yaw_deg, image=img)
    assert u.seen, u.status
    fwd, rgt, below, rel = _expected(drone, ugv, settings.ugv.height_m)
    assert u.forward_m == pytest.approx(fwd, abs=0.015)
    assert u.right_m == pytest.approx(rgt, abs=0.015)
    assert u.below_m == pytest.approx(below, abs=0.02)
    assert abs(wrap180(u.rel_yaw_deg - rel)) < 4.0
    assert u.side_px > 35   # 8cm 태그, 35cm 거리 → 약 47px


def test_map_position_with_localizer(settings, scene):
    """지도 위치 계산(조원 AprilTag 위치)과 같이 돌려서 UGV 지도 위치까지. UGV 태그가 위치 계산을 망치지 않는지도."""
    det = UgvDetector(settings.ugv)
    loc = make_localizer("tt_down", settings)
    mount = settings.tt_mount_yaw_deg
    errs, yaw_errs, n_pose = [], [], 0
    for k, (drone, ugv) in enumerate(CASES):
        loc.reset()
        for i in range(8):   # 같은 자리 여러 장 (추적기가 자리 잡게)
            img = _frame(settings, scene, drone, ugv, seed=100 * k + i)
            res = loc.process(img, i * 0.1)
            K, dist = loc.camera_matrix(img.shape[1], img.shape[0])
            u = det.measure(i * 0.1, K, dist, mount, tags=res.tags, drone=res if res.ok else None)
            assert u.seen
            if res.ok:
                n_pose += 1
                assert "100" not in (res.status or "")   # UGV 태그(100번)는 위치 계산에 안 쓰임
            if u.map_x_m is not None:
                errs.append(math.hypot(u.map_x_m - ugv[0], u.map_y_m - ugv[1]))
                yaw_errs.append(abs(wrap180(u.map_yaw_deg - ugv[2])))
    assert n_pose >= len(CASES) * 4, "UGV 태그가 있어도 지도 위치가 대부분 나와야 함"
    assert errs, "지도 위치가 한 번도 안 나옴"
    assert float(np.median(errs)) < 0.04
    assert float(np.median(yaw_errs)) < 5.0


def test_not_seen(settings, scene):
    det = UgvDetector(settings.ugv)
    cam = SyntheticCamera(scene, settings.tt_width_px, settings.tt_height_px, settings.tt_hfov_deg, color=False, seed=1)
    img = cam.render(1.2, 1.2, 0.5, 90)
    loc = make_localizer("tt_down", settings)
    K, dist = loc.camera_matrix(img.shape[1], img.shape[0])
    assert not det.measure(0.0, K, dist, -90, image=img).seen
