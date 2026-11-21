"""슬롯 투영·자르기 조건과 중복 막기 (명세 9장)."""
import dataclasses

import cv2
import numpy as np
import pytest

from bridge.geometry import nadir_view
from bridge.slots import MotionHistory, PhotoFinderCapture, SlotCapture, upright_corners
from bridge.synthetic import SyntheticCamera


def slot(settings, n):
    return next(s for s in settings.slots if s.slot == n)


def hover(cap, x, y, t0, t1, step=0.1):
    t = t0
    while t <= t1 + 1e-9:
        cap.on_pose(t, x, y)
        t += step
    return t1


def test_settings_slots_have_up(settings):
    assert len(settings.slots) == 4
    for s in settings.slots:
        assert s.up_xy is not None
        c = upright_corners(s)
        up = np.asarray(s.up_xy)
        # 위 두 모서리가 아래 두 모서리보다 up 쪽
        assert c[:2].mean(0) @ up > c[2:].mean(0) @ up
        # 위왼쪽 → 위오른쪽 = 오른쪽 방향 (up을 시계 90°)
        right = np.array([up[1], -up[0]])
        assert (c[1] - c[0]) @ right > 0


def test_projection_center(settings):
    s4 = slot(settings, 4)
    v = nadir_view(*s4.center_m, 0.0, 1.0, 320, 240, 0.8)
    px = v.project(np.array([s4.center_m]))[0]
    assert np.allclose(px, [159.5, 119.5], atol=1e-6)
    back = v.pixels_to_map(px[None])[0]
    assert np.allclose(back, s4.center_m, atol=1e-9)


def test_conditions(settings):
    s4 = slot(settings, 4)
    cap = SlotCapture(settings)
    img = np.zeros((240, 320), np.uint8)
    # 1) 슬롯이 화면 밖
    far = nadir_view(2.0, 2.0, 0.0, 1.0, 320, 240)
    hover(cap, 2.0, 2.0, 0, 2)
    assert cap.check(img, far, 2.0) == []
    # 2) 화면 안이지만 너무 작음 (span 넓게 = 높이 비행)
    cap = SlotCapture(settings)
    small = nadir_view(*s4.center_m, 0.0, 3.0, 320, 240)
    hover(cap, *s4.center_m, 0, 2)
    ev = {d["slot"]: d for d in cap.evaluate(small, 2.0)}
    assert not ev[4]["ok"] and "작음" in ev[4]["reason"]
    # 3) 충분히 크지만 아직 still_s 동안 지켜보지 않음
    cap = SlotCapture(settings)
    good = nadir_view(*s4.center_m, 0.0, 0.8, 320, 240)
    cap.on_pose(0.0, *s4.center_m)
    assert cap.check(img, good, 0.0) == []
    # 4) 움직이는 중
    cap = SlotCapture(settings)
    for i in range(30):
        cap.on_pose(i * 0.1, s4.center_m[0] + 0.01 * i, s4.center_m[1])
    ev = {d["slot"]: d for d in cap.evaluate(good, 2.9)}
    assert not ev[4]["ok"] and "움직이는" in ev[4]["reason"]
    # 5) 호버링 → 보고
    cap = SlotCapture(settings)
    hover(cap, *s4.center_m, 0, settings.still_s + 0.2)
    caps = cap.check(img, good, settings.still_s + 0.2)
    assert [c.slot for c in caps] == [4]
    c = caps[0]
    assert c.crop.shape[1] == settings.crop_width_px
    assert abs(c.crop.shape[1] / c.crop.shape[0] - 1.5) < 0.02  # 150 x 100 mm 슬롯
    assert c.place == s4.name


def test_margin(settings):
    s4 = slot(settings, 4)
    cap = SlotCapture(dataclasses.replace(settings, min_slot_px=1))
    hover(cap, *s4.center_m, 0, 2)
    # 슬롯이 화면 오른쪽 끝에 걸치게: 카메라를 왼쪽으로
    v = nadir_view(s4.center_m[0] - 0.33, s4.center_m[1], 0.0, 0.8, 320, 240)
    ev = {d["slot"]: d for d in cap.evaluate(v, 2.0)}
    assert ev[4]["reason"] == "화면 밖"


def test_cooldown(settings):
    s4 = slot(settings, 4)
    cap = SlotCapture(settings)
    img = np.zeros((240, 320), np.uint8)
    v = nadir_view(*s4.center_m, 0.0, 0.8, 320, 240)
    t = hover(cap, *s4.center_m, 0, 2)
    assert len(cap.check(img, v, t)) == 1
    hover(cap, *s4.center_m, 2, 3)
    assert cap.check(img, v, 3.0) == []                      # 같은 슬롯은 쉬는 중
    t2 = 2 + settings.report_cooldown_s + 0.1
    hover(cap, *s4.center_m, 3, t2)
    assert len(cap.check(img, v, t2)) == 1                   # report_cooldown_s 뒤 다시
    cap.reset()                                               # 모드 전환: 이동 기록만 버림
    assert not cap.motion.still(t2)
    assert cap.last_sent[4] == pytest.approx(t2)


def test_motion_history():
    m = MotionHistory(1.0, 0.05)
    for i in range(11):
        m.add(i * 0.1, 0.0, 0.0)
    assert m.still(1.0)
    m.add(1.1, 0.06, 0.0)
    assert not m.still(1.1)
    for i in range(12, 24):
        m.add(i * 0.1, 0.06, 0.0)
    assert m.still(2.3)


@pytest.mark.parametrize("yaw", [0.0, 90.0, 200.0])
def test_crop_is_upright(settings, scene, yaw):
    """드론 방향이 달라도 잘라낸 사진은 슬롯 up이 위 → 서로 같아야 한다."""
    s3 = slot(settings, 3)
    cam = SyntheticCamera(scene, 640, 480, 60.0, True, degrade=False)

    def crop(y):
        cap = SlotCapture(settings)
        hover(cap, *s3.center_m, 0, 2)
        img = cam.render(*s3.center_m, 0.5, y)
        out = cap.check(img, cam.view(*s3.center_m, 0.5, y), 2.0)
        assert len(out) == 1
        return cv2.cvtColor(out[0].crop, cv2.COLOR_BGR2GRAY).astype(np.float32)

    ref, got = crop(0.0 if yaw else 45.0), crop(yaw)
    corr = np.corrcoef(ref.ravel(), got.ravel())[0, 1]
    flipped = np.corrcoef(ref.ravel(), got[::-1, ::-1].ravel())[0, 1]
    assert corr > 0.9 and corr > flipped + 0.2, (corr, flipped)


def test_photo_finder_dedup(settings, monkeypatch):
    """photo_finder 방식: 슬롯 안 사진은 슬롯별 cooldown, 슬롯 밖 사진은 same_place_m 안이면 같은 사진."""
    import photo_finder
    from photo_finder import FoundPhoto

    view = nadir_view(1.25, 1.25, 0.0, 1.0, 320, 240)
    s2 = slot(settings, 2)

    def fake(points_m, size_px=60):
        def finder(gray, tags=None, marker_quads=None):
            out = []
            for xy in points_m:
                c = view.project(np.array([xy]))[0]
                q = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]], float) * size_px / 2 + c
                out.append(FoundPhoto(q, 0.5, 0.1, 0.8, True, True, np.zeros((size_px, size_px), np.uint8), True))
            return out
        return finder

    img = np.zeros((240, 320), np.uint8)
    cap = PhotoFinderCapture(settings)
    monkeypatch.setattr(photo_finder, "find_photos", fake([(1.0, 1.0)]))
    assert len(cap.check(img, view, 0.0)) == 1                        # 슬롯 밖, 처음
    monkeypatch.setattr(photo_finder, "find_photos", fake([(1.0 + settings.same_place_m / 2, 1.0)]))
    assert cap.check(img, view, 1.0) == []                             # 같은 곳 → 중복
    monkeypatch.setattr(photo_finder, "find_photos", fake([(1.0 + settings.same_place_m * 2, 1.0)]))
    assert len(cap.check(img, view, 2.0)) == 1                         # 다른 곳
    # 너무 작은 사진 (min_slot_px 미만)은 보고하지 않음
    monkeypatch.setattr(photo_finder, "find_photos", fake([(1.6, 1.6)], size_px=int(settings.min_slot_px) - 2))
    assert cap.check(img, view, 3.0) == []
    # 슬롯 안 사진 → slot 번호, 슬롯 cooldown
    v2 = nadir_view(*s2.center_m, 0.0, 1.0, 320, 240)
    view = v2
    monkeypatch.setattr(photo_finder, "find_photos", fake([s2.center_m]))
    out = cap.check(img, v2, 4.0)
    assert [c.slot for c in out] == [2] and out[0].crop.shape[1] == settings.crop_width_px
    assert cap.check(img, v2, 5.0) == []
