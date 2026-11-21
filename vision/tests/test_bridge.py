"""모드 전환(상태 초기화)·보낼 내용 형식·위경도."""
import argparse
import time

import numpy as np
import pytest

from bridge import teammate
from bridge.app import Bridge, encode_jpeg, parse_args, video_frame
from bridge.geometry import bilinear_latlon
from bridge.localizers import Esp32Localizer, TtDownLocalizer
from bridge.sources import FileSource, LocalTour
from bridge.synthetic import SyntheticCamera

SIM_FRAMES = teammate.TEAMMATE_DIR / "data" / "experiments" / "incident_camera" / "sim_frames"


class FakeVideo:
    def __init__(self):
        self.statuses = []
        self.sent_frames = 0

    def send_status(self, st):
        self.statuses.append(st)
        return True

    def start(self):
        pass

    def stop(self):
        pass


def make_bridge(settings, *extra):
    args = parse_args(["--dry-run", "--input", str(SIM_FRAMES), "--mode", "tt_down", *extra])
    b = Bridge(settings, args)
    b.video = FakeVideo()
    return b


def test_tracker_reset_discards_state(settings, scene):
    cam = SyntheticCamera(scene, 320, 240, settings.tt_hfov_deg, False, seed=1, degrade=False)
    loc = TtDownLocalizer(settings)
    assert loc.process(cam.render(0.91, 0.81, 0.8, 0), 0.0).ok
    empty = np.full((240, 320), 200, np.uint8)  # 태그 없는 화면
    assert loc.process(empty, 0.2).ok             # 추적 중이면 잠깐 예측(coasting)으로 이어 감
    loc.reset()
    assert loc.tracker.position is None
    assert not loc.process(empty, 0.4).ok         # 초기화 뒤에는 이전 위치가 남지 않는다


def test_switch_mode_replaces_everything(settings):
    b = make_bridge(settings)
    try:
        b.switch_mode("tt_down", first=True)
        first_src = b.source
        assert isinstance(b.localizer, TtDownLocalizer)
        b.capture.on_pose(0.0, 1.0, 1.0)
        b.capture.on_pose(2.0, 1.0, 1.0)
        assert b.capture.motion.still(2.0)
        b.request_mode("esp32")
        assert b.requested_mode == "esp32"
        b.switch_mode("esp32")
        assert isinstance(b.localizer, Esp32Localizer)
        assert b.source is not first_src and isinstance(b.source, FileSource)
        assert not first_src._thread.is_alive()            # 이전 입력은 멈춤
        assert not b.capture.motion.still(2.0)               # 호버링 기록 초기화
        assert b.video.statuses[-1]["mode"] == "esp32"       # bridge_status로 실제 모드 알림
        assert b.video.statuses[-1]["type"] == "bridge_status"
        # 다시 tt_down: 캐시한 위치 계산 객체도 reset (이전 추적 없음)
        b.switch_mode("tt_down")
        assert b.localizer.tracker.position is None
        # 알 수 없는 모드 → 바꾸지 않고 상태로 답함
        b.request_mode("lidar")
        assert b.requested_mode is None
        assert "알 수 없는 모드" in b.video.statuses[-1]["message"]
    finally:
        b.close()


def test_run_offline_files(settings, capsys):
    """조원 sim_frames(640x480 흑백) 폴더를 한 번 재생 — 위치가 나와야 한다."""
    b = make_bridge(settings, "--input-once", "--input-fps", "20")
    b.run()
    out = capsys.readouterr().out
    assert b.counts["frames"] >= 6
    assert b.counts["poses"] >= 3
    assert '"mode": "tt_down"' in out


def test_pose_payload(settings):
    b = make_bridge(settings)
    from bridge.localizers import PoseResult
    r = PoseResult(True, 1.234567, 0.5, 0.79, 359.95, 0.87654)
    p = b.pose_payload(r, 1763000000.123456)
    assert set(p) == {"x_m", "y_m", "z_m", "yaw_deg", "confidence", "ts", "mode"}
    assert p["mode"] == "tt_down" and p["x_m"] == 1.2346 and 0 <= p["confidence"] <= 1
    r.z_m = None
    assert "z_m" not in b.pose_payload(r, 0.0)


def test_incident_payload_and_geo(settings):
    b = make_bridge(settings)
    from bridge.localizers import PoseResult
    from bridge.slots import Capture
    s4 = settings.slots[3]
    cap = Capture(4, s4.name, s4.center_m, np.zeros((322, 480, 3), np.uint8), "slot", np.zeros((4, 2)))
    p = b.incident_payload(cap, PoseResult(True, 0.44, 0.83, 0.79, 10.0, 0.9), 1.0)
    for k in ("ts", "mode", "method", "slot", "place", "x_m", "y_m", "lat", "lon", "drone", "photo_jpeg_b64"):
        assert k in p
    assert p["detection"] is None and p["slot"] == 4 and p["method"] == "slot"
    # 조원 geo.py(UTM 역변환)와 명세 [geo] 쌍선형 보간이 같은 곳을 가리킨다
    lat, lon = bilinear_latlon(*s4.center_m, settings.map_w_m, settings.map_h_m, settings.geo_corners)
    assert abs(p["lat"] - lat) < 2e-6 and abs(p["lon"] - lon) < 2e-6
    import base64
    assert len(base64.b64decode(p["photo_jpeg_b64"])) <= settings.max_photo_kb * 1024


def test_video_frame_size(settings):
    img = np.zeros((480, 640, 3), np.uint8)
    v = video_frame(img, settings.send_width_px)
    assert v.shape[1] == settings.send_width_px and v.shape[0] == settings.send_width_px * 3 // 4
    jpg = encode_jpeg(v, settings.jpeg_quality, settings.max_frame_kb)
    assert jpg[:2] == b"\xff\xd8" and len(jpg) <= settings.max_frame_kb * 1024
    gray = np.zeros((240, 320), np.uint8)
    assert video_frame(gray, settings.send_width_px).shape == (240, 320)


def test_local_tour(settings):
    t = LocalTour([(0, 0), (1, 0)], 0.8, 0.5, 1.0)
    assert t(0.0)[:2] == (0, 0)
    assert t(0.5)[:2] == (0, 0)          # 호버링
    x, y, z, yaw = t(2.0)                  # 이동 중 (1초 지나 0.5m)
    assert x == pytest.approx(0.5) and z == 0.8


# ---------------------------------------------------------------- TT 화면 잘라내기 (사용자 확인 코드 frame[0:240, 0:320])

def test_crop_tt_frame_keeps_top_left_320x240():
    import numpy as np
    from bridge.sources import crop_tt_frame
    big = np.random.default_rng(0).integers(0, 255, (720, 960), dtype=np.uint8)  # 잡음
    big[0:240, 0:320] = 77  # 실제 영상 부분
    out, note = crop_tt_frame(big, (320, 240), True)
    assert out.shape == (240, 320) and (out == 77).all()
    assert "왼쪽 위" in note


def test_crop_tt_frame_exact_size_untouched_and_options():
    import numpy as np
    from bridge.sources import crop_tt_frame
    exact = np.zeros((240, 320), np.uint8)
    out, note = crop_tt_frame(exact, (320, 240), True)
    assert out is exact and note == ""
    big = np.zeros((480, 320), np.uint8)  # 아래쪽만 잡음인 경우
    assert crop_tt_frame(big, (320, 240), True)[0].shape == (240, 320)
    assert crop_tt_frame(big, (320, 240), False)[0].shape == (480, 320)  # 끄면 그대로
    small = np.zeros((120, 160), np.uint8)
    out, note = crop_tt_frame(small, (320, 240), True)
    assert out.shape == (120, 160) and "다름" in note


# ---------------------------------------------------------------- esp32 색·조명 보정 (조원 실물 XIAO 시험값)

def test_esp32_color_and_lighting_profile_from_settings():
    from bridge import config
    from bridge.localizers import Esp32Localizer
    s = config.load()
    real = Esp32Localizer(s)                  # 실물 카메라: 설정값 그대로
    assert real.colors.hue_shift == s.esp32_hue_shift == 3
    assert real.colors.hue_band_width == 15 and real.colors.saturation_tolerance == 78
    assert real.lighting is not None and real.lighting.corrector.config.method == "combined"
    assert real.lighting.shadow_aware is True
    syn = Esp32Localizer(s, synthetic=True)   # 합성 카메라: Hue 이동만 뺀다
    assert syn.colors.hue_shift == 0 and syn.colors.hue_band_width == 15
    assert syn.lighting is not None
