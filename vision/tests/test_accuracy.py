"""합성 화면에서 위치 오차 측정 — tt_down 320×240, 고도 80cm (요청 측정값).

pytest -s 로 실행하면 측정값을 출력한다. 기준은 느슨하게(회귀 확인용).
측정 결과(2026-11-21, 시드 0, 200장, 시야각 60° 추정, 조원 sim_camera 열화 포함)는 README 7장에 적었다.
"""
import math

import numpy as np
import pytest

from bridge.localizers import Esp32Localizer, TtDownLocalizer
from bridge.synthetic import SyntheticCamera


def tags_in_view(view, tag_map, w, h, margin=2):
    n = 0
    for c in tag_map.corners.values():
        px = view.project(c)
        if np.isfinite(px).all() and px.min() >= margin and px[:, 0].max() <= w - 1 - margin and px[:, 1].max() <= h - 1 - margin:
            n += 1
    return n


def measure_tt(settings, scene, z, n=200, upscale=1, seed=0):
    rng = np.random.default_rng(seed)
    w, h = settings.tt_width_px, settings.tt_height_px
    cam = SyntheticCamera(scene, w, h, settings.tt_hfov_deg, False, seed=seed)
    loc = TtDownLocalizer(settings, upscale)
    visible = decoded = posed = 0
    errs, zerrs, side = [], [], []
    for i in range(n):
        x, y, yaw = rng.uniform(0.3, 2.2), rng.uniform(0.3, 2.2), rng.uniform(0, 360)
        img = cam.render(x, y, z, yaw)
        view = cam.view(x, y, z, yaw)
        visible += tags_in_view(view, loc.tag_map, w, h)
        loc.reset()  # 한 장씩 독립 (추적 없이)
        res = loc.process(img, 0.0)
        decoded += sum(1 for t in res.tags if t.id in loc.tag_map.corners)
        for t in res.tags:
            side.append(np.mean([np.linalg.norm(t.corners[k] - t.corners[k - 1]) for k in range(4)]))
        if res.ok:
            posed += 1
            errs.append(math.hypot(res.x_m - x, res.y_m - y))
            zerrs.append(abs(res.z_m - z))
    return dict(frames=n, tags_visible=visible, tags_decoded=decoded, decode_rate=decoded / max(visible, 1),
                pose_rate=posed / n, err_med_cm=np.median(errs) * 100, err_p90_cm=np.percentile(errs, 90) * 100,
                z_err_med_cm=np.median(zerrs) * 100, tag_side_px=np.median(side) if side else math.nan)


def fmt(name, m):
    return (f"{name}: 화면에 온전히 보인 태그 {m['tags_visible']}개 중 인식 {m['tags_decoded']}개 ({m['decode_rate']:.0%}), "
            f"태그 한 변 중앙값 {m['tag_side_px']:.1f}px, 위치가 나온 장 {m['pose_rate']:.0%}, "
            f"위치 오차 중앙값 {m['err_med_cm']:.1f}cm / 90% {m['err_p90_cm']:.1f}cm, 높이 오차 중앙값 {m['z_err_med_cm']:.1f}cm")


def test_tt_down_320x240_80cm(settings, scene):
    m = measure_tt(settings, scene, settings.default_alt_m)
    print("\n" + fmt(f"tt_down 320x240 고도 {settings.default_alt_m * 100:.0f}cm", m))
    assert m["pose_rate"] > 0.15
    assert m["err_med_cm"] < 6


@pytest.mark.slow
def test_tt_down_compare(settings, scene):
    for z in (0.6, 0.8, 1.0):
        for up in (1, 2):
            m = measure_tt(settings, scene, z, n=150, upscale=up)
            print("\n" + fmt(f"tt_down 고도 {z * 100:.0f}cm 확대 x{up}", m))


def test_esp32_hover(settings, scene):
    """esp32(컬러 320x240, 시야각 68° 가정) 고도 80cm, 슬롯 S2·S3·S4 위에서 추적 오차."""
    rng = np.random.default_rng(1)
    errs = []
    for sl in settings.slots[1:]:
        cam = SyntheticCamera(scene, settings.esp32_width_px, settings.esp32_height_px, 68.0, True, seed=2)
        loc = Esp32Localizer(settings, synthetic=True)
        yaw = rng.uniform(0, 360)
        for i in range(8):
            x, y = sl.center_m[0] + rng.normal(0, 0.01), sl.center_m[1] + rng.normal(0, 0.01)
            r = loc.process(cam.render(x, y, settings.default_alt_m, yaw), 0.2 * i)
            if r.ok and r.measured:
                errs.append(math.hypot(r.x_m - x, r.y_m - y))
    print(f"\nesp32 슬롯 위 호버링: 위치 {len(errs)}/24장, 오차 중앙값 {np.median(errs) * 100:.1f}cm")
    assert len(errs) >= 15 and np.median(errs) < 0.03
