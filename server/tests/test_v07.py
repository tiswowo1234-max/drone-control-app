"""API v0.7 (2026-11-21): 고도 고정, 위치 추적 모드 전환, 사건 보고, 영상 중계, /sim/truth, 안전."""
import asyncio
import base64
import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from drone_server import vision_link as vl
from drone_server.app import Hub, create_app
from drone_server.backends.mock import MockBackend
from drone_server.backends.real import set_camera_mode
from drone_server.backends.tello import TelloBackend
from drone_server.controller import Controller
from drone_server.models import Pose
from drone_server.positioning import DeadReckoningProvider, VisionProvider
from drone_server.settings import (IncidentCfg, PhotoSlot, TelloCfg, VideoCfg, VisionCfg, load_settings)
from drone_server.vision_link import VideoRelay, VisionLink

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"


def jpeg(n: int) -> bytes:
    """n번째 시험 프레임 (내용으로 몇 번째인지 구분)."""
    return b"\xff\xd8" + n.to_bytes(4, "big") + b"\x00" * 50 + b"\xff\xd9"


def b64(data: bytes = JPEG) -> str:
    return base64.b64encode(data).decode()


def cmd(cid, name, **kw):
    return {"type": "cmd", "id": cid, "cmd": name, **kw}


def recv_until(ws, kind, n=60):
    for _ in range(n):
        m = ws.receive_json()
        if m["type"] == kind:
            return m
    raise AssertionError(f"{kind} 메시지 없음")


async def wait_mode(c, mode, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if c.mode == mode and not c.busy:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"mode {c.mode!r} != {mode!r}")


def vision_settings(settings, **kw):
    return replace(settings, mock=replace(settings.mock, pos_source="vision", **kw))


# ================================================================ 설정

def recv_bridge(ws):
    """연결 프로그램 쪽 수신: 주기적인 drone_state(자세·높이)는 건너뛰고 다음 메시지."""
    while True:
        m = json.loads(ws.receive_text())
        if m.get("type") != "drone_state":
            return m


def test_settings_toml_v07_keys():
    s = load_settings()
    assert s.limits.fixed_alt is True  # 고도 값 자체는 시연 결과로 바뀌므로(80 → 40, 2026-11-23) 범위만 검사
    assert s.limits.min_alt_cm <= s.limits.default_alt_cm <= s.limits.max_alt_cm
    assert s.bridge.default_mode in ("esp32", "tt_down")
    assert [p.slot for p in s.photo_slots] == [1, 2, 3, 4]
    j = s.photo_slots[0].to_json()
    assert set(j) == {"slot", "name", "center_cm", "corners_cm"}
    assert set(j["center_cm"]) == {"x_cm", "y_cm"} and len(j["corners_cm"]) == 4
    assert s.video.max_fps > 0 and s.incident.keep >= 1


# ================================================================ 고도 고정 (4.1)
def test_fixed_alt_goto_and_return_use_default_alt(settings):
    s = replace(settings, limits=replace(settings.limits, fixed_alt=True))

    async def go():
        b = MockBackend(s)
        await b.connect()
        c = Controller(s, b)
        assert (await c.handle_cmd(cmd("1", "goto", x_cm=150, y_cm=90, z_cm=120)))["ok"]
        await wait_mode(c, "hovering")
        assert round(b.pose.z_cm) == 120
        assert (await c.handle_cmd(cmd("2", "goto", x_cm=140, y_cm=90)))["ok"]  # z 생략 → 80
        await wait_mode(c, "hovering")
        assert round(b.pose.z_cm) == 80
        assert (await c.handle_cmd(cmd("3", "goto", x_cm=150, y_cm=90, z_cm=120)))["ok"]
        await wait_mode(c, "hovering")
        # 귀환도 같은 규칙: default_alt_cm으로 home까지 간 뒤 착륙
        calls = []
        orig = b.move_to

        async def spy(x, y, z):
            calls.append((round(x, 1), round(y, 1), z))
            await orig(x, y, z)
        b.move_to = spy
        assert (await c.handle_cmd(cmd("4", "return_home")))["ok"]
        await wait_mode(c, "idle")
        assert calls == [(153.2, 70.2, 80)] and b.pose.z_cm == 0
    asyncio.run(go())


def test_not_fixed_alt_keeps_current_alt(settings):
    async def go():
        b = MockBackend(settings)
        await b.connect()
        c = Controller(settings, b)
        assert (await c.handle_cmd(cmd("1", "goto", x_cm=150, y_cm=90, z_cm=120)))["ok"]
        await wait_mode(c, "hovering")
        assert (await c.handle_cmd(cmd("2", "goto", x_cm=140, y_cm=90)))["ok"]
        await wait_mode(c, "hovering")
        assert round(b.pose.z_cm) == 120
    asyncio.run(go())


# ================================================================ 모드 전환 (11장)
def test_set_vision_mode_rules(settings):
    s = vision_settings(settings)

    async def go():
        b = MockBackend(s)
        await b.connect()
        link = VisionLink(s, b)
        c = Controller(s, b, vision_link=link)
        assert link.mode == "tt_down" and b.vision_mode == "tt_down"
        r = await c.handle_cmd(cmd("1", "set_vision_mode", mode="thermal"))
        assert r["error"] == "BAD_REQUEST"
        assert (await c.handle_cmd(cmd("2", "set_vision_mode")))["error"] == "BAD_REQUEST"
        # 카메라 값을 받은 뒤 전환하면 버리고 estimate로 이어 간다
        b.vision.submit(100.0, 120.0)
        assert b.telemetry().pos_source == "vision"
        assert (await c.handle_cmd(cmd("3", "set_vision_mode", mode="esp32")))["ok"]
        t = b.telemetry()
        assert link.mode == "esp32" and b.vision_mode == "esp32"
        assert t.pos_source == "estimate" and (t.pos.x_cm, t.pos.y_cm) == (100, 120)
        assert c.state_message()["vision"]["mode"] == "esp32"
        # 이동 중이면 BUSY
        assert (await c.handle_cmd(cmd("4", "goto", x_cm=60, y_cm=120)))["ok"]
        await asyncio.sleep(0.05)
        assert c.mode in ("taking_off", "moving")
        assert (await c.handle_cmd(cmd("5", "set_vision_mode", mode="tt_down")))["error"] == "BUSY"
        await wait_mode(c, "hovering")
        assert (await c.handle_cmd(cmd("6", "set_vision_mode", mode="tt_down")))["ok"]  # 호버링 중 OK
        assert link.mode == "tt_down"
        await link.shutdown()
    asyncio.run(go())


def test_pose_mode_check_and_z_display(settings):
    s = vision_settings(settings)
    b = MockBackend(s)
    with TestClient(create_app(s, b)) as client:
        r = client.post("/vision/pose", json={"x_m": 1.0, "y_m": 1.0, "mode": "esp32"})
        assert r.status_code == 400 and r.json()["error"] == "WRONG_MODE"
        r = client.post("/vision/pose", json={"x_m": 1.0, "y_m": 1.0, "mode": "xx"})
        assert r.status_code == 400 and r.json()["error"] == "BAD_REQUEST"
        r = client.post("/vision/pose", json={"x_m": 1.0, "y_m": 1.0, "z_m": "a"})
        assert r.status_code == 400 and r.json()["error"] == "BAD_REQUEST"
        assert client.post("/vision/pose", json={"x_m": 1.0, "y_m": 1.1, "z_m": 0.79, "mode": "tt_down"}).json()["ok"]
        with client.websocket_connect("/ws") as ws:
            st = recv_until(ws, "state")
            assert st["pos_source"] == "vision" and st["pos"]["z_cm"] == 79  # 표시용 카메라 높이
        assert b.pose.z_cm == 0  # 비행 제어용 높이는 그대로 (센서/가짜 드론 값)


def test_mode_switch_over_ws_notifies_bridge_and_state(settings):
    with TestClient(create_app(settings, MockBackend(settings))) as client:
        with client.websocket_connect("/vision/video") as bridge, client.websocket_connect("/ws") as ws:
            assert recv_bridge(bridge) == {"type": "vision_mode", "mode": "tt_down"}
            bridge.send_text(json.dumps({"type": "bridge_status", "mode": "tt_down", "camera_ok": True,
                                         "message": "TT 하단 카메라 수신 중"}))
            st = recv_until(ws, "state")
            for _ in range(20):
                if st["vision"]["bridge_mode"] == "tt_down":
                    break
                st = recv_until(ws, "state")
            assert st["vision"] == {"mode": "tt_down", "bridge_connected": True, "bridge_mode": "tt_down",
                                    "camera_ok": True, "video_live": False}
            ws.send_json(cmd("m1", "set_vision_mode", mode="esp32"))
            assert recv_until(ws, "ack") == {"type": "ack", "id": "m1", "ok": True}
            assert recv_bridge(bridge) == {"type": "vision_mode", "mode": "esp32"}
            st = recv_until(ws, "state")
            assert st["vision"]["mode"] == "esp32" and st["vision"]["bridge_mode"] == "tt_down"  # 전환 중
            bridge.send_text(json.dumps({"type": "bridge_status", "mode": "esp32", "camera_ok": False}))
            for _ in range(20):
                st = recv_until(ws, "state")
                if st["vision"]["bridge_mode"] == "esp32":
                    break
            assert st["vision"]["camera_ok"] is False
            h = client.get("/").json()["vision_link"]
            assert h["mode"] == "esp32" and h["bridge_connected"] is True
        # 연결 프로그램이 끊기면
        st = None
        with client.websocket_connect("/ws") as ws:
            for _ in range(20):
                st = recv_until(ws, "state")
                if not st["vision"]["bridge_connected"]:
                    break
            assert st["vision"]["bridge_mode"] is None and st["vision"]["mode"] == "esp32"


def test_only_controller_app_can_switch_mode(settings):
    with TestClient(create_app(settings, MockBackend(settings))) as client:
        with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
            a.send_json(cmd("x", "set_vision_mode", mode="esp32"))
            assert recv_until(a, "ack")["error"] == "BUSY"
            b.send_json(cmd("y", "set_vision_mode", mode="esp32"))
            assert recv_until(b, "ack")["ok"]


# ================================================================ real: streamon / downvision / streamoff
class FakeRealTello:
    """실제 TT 흉내: 명령 기록만. 영상 관련 메서드(get_frame_read, streamon)는 일부러 없다 → 부르면 실패."""

    def __init__(self):
        self.sent = []

    def connect(self):
        self.sent.append("command")

    def get_current_state(self):
        return {"bat": 80, "h": 0, "yaw": 0}

    def send_control_command(self, c):
        self.sent.append(c)
        return True


def make_real(tello):
    return TelloBackend(name="real", cfg=TelloCfg(), provider=DeadReckoningProvider(Pose(153.2, 70.2, 0)),
                        home=Pose(153.2, 70.2, 0), make_cmd_tello=lambda: tello,
                        read_state=lambda t: t.get_current_state(), camera_mode=set_camera_mode)


def test_real_camera_commands_on_connect_and_switch():
    async def go():
        t = FakeRealTello()
        b = make_real(t)
        b.vision_mode = "tt_down"          # 서버 시작 모드 (VisionLink가 넣는다)
        await b.connect()
        assert t.sent == ["command", "downvision 1", "streamon"]
        await b.apply_vision_mode("esp32")
        assert t.sent[-1] == "streamoff"
        await b.apply_vision_mode("tt_down")
        assert t.sent[-2:] == ["downvision 1", "streamon"]
        await b.close()
    asyncio.run(go())


def test_real_mode_stored_before_connect_and_esp32_on_connect():
    async def go():
        t = FakeRealTello()
        b = make_real(t)
        await b.apply_vision_mode("esp32")  # 연결 전: 기억만
        assert t.sent == []
        await b.connect()
        assert t.sent == ["command", "streamoff"]
        await b.close()
    asyncio.run(go())


def test_real_camera_failure_does_not_break_connection_or_cmd():
    class Failing(FakeRealTello):
        def send_control_command(self, c):
            raise RuntimeError("no response")

    async def go():
        b = make_real(Failing())
        b.vision_mode = "tt_down"
        await b.connect()                 # 카메라 명령 실패해도 연결은 유지
        assert b.telemetry().connected
        events = []

        async def emit(m):
            events.append(m)
        s = load_settings()
        link = VisionLink(s, b, emit=emit)
        c = Controller(s, b, emit=emit, vision_link=link)
        assert (await c.handle_cmd(cmd("1", "set_vision_mode", mode="esp32")))["ok"]  # ack는 바로
        for _ in range(50):
            if events:
                break
            await asyncio.sleep(0.02)
        assert events and events[0]["code"] == "CMD_FAILED"
        await b.close()
    asyncio.run(go())


def test_mock_and_sim_send_no_camera_commands(settings):
    async def go():
        b = MockBackend(settings)
        await b.apply_vision_mode("esp32")  # 기본 구현: 기억만
        assert b.vision_mode == "esp32"
    asyncio.run(go())


# ================================================================ 사건 보고 (9장, 3.5·3.6, 10.1)
def incident_settings(settings, **inc):
    slots = [PhotoSlot(i, f"S{i}", (40.0 * i, 80.0), [(0, 0)] * 4) for i in (1, 2, 3, 4)]
    geo = {"sw": {"lat": 36.0, "lon": 127.0}, "se": {"lat": 36.0, "lon": 127.01},
           "nw": {"lat": 36.01, "lon": 127.0}, "ne": {"lat": 36.01, "lon": 127.01}}
    return replace(settings, photo_slots=slots, geo=geo, incident=replace(IncidentCfg(), **inc))


def report(client, **kw):
    body = {"photo_jpeg_b64": b64(), "mode": "tt_down", "method": "slot", "slot": 4, "place": "S4",
            "x_m": 0.468, "y_m": 0.891, "drone": {"x_m": 0.4406, "y_m": 0.8312, "z_m": 0.7895},
            "ts": 1763000000.4, "detection": None}
    body.update(kw)
    return client.post("/vision/incident", json={k: v for k, v in body.items() if v is not ...})


def test_incident_flow_to_app_and_photo(settings):
    s = incident_settings(settings)
    with TestClient(create_app(s, MockBackend(s))) as client, client.websocket_connect("/ws") as ws:
        recv_until(ws, "state")
        r = report(client)
        assert r.status_code == 200 and r.json() == {"ok": True, "id": "inc-1"}
        m = recv_until(ws, "incident")["incident"]
        assert m == {"id": "inc-1", "ts": 1763000000.4, "slot": 4, "place": "S4",
                     "pos": {"x_cm": 46.8, "y_cm": 89.1}, "geo": {"lat": 36.0035640, "lon": 127.001872},
                     "drone_pos": {"x_cm": 44.1, "y_cm": 83.1, "z_cm": 79}, "photo_url": "/incidents/inc-1/photo.jpg",
                     "mode": "tt_down", "method": "slot", "detection": None}
        ev = recv_until(ws, "event")
        assert ev["code"] == "INCIDENT_NEW" and "S4" in ev["message"]
        p = client.get("/incidents/inc-1/photo.jpg")
        assert p.status_code == 200 and p.content == JPEG
        assert p.headers["content-type"] == "image/jpeg" and p.headers["access-control-allow-origin"] == "*"
        nf = client.get("/incidents/inc-99/photo.jpg")
        assert nf.status_code == 404 and nf.headers["access-control-allow-origin"] == "*"
        assert client.get("/").json()["incidents"]["count"] == 1


def test_incident_dedup_and_modes(settings):
    s = incident_settings(settings, dedup_s=30, same_place_m=0.15)
    with TestClient(create_app(s, MockBackend(s))) as client:
        assert report(client).json() == {"ok": True, "id": "inc-1"}
        assert report(client).json() == {"ok": True, "id": "inc-1", "duplicate": True}
        assert report(client, mode="esp32").json() == {"ok": True, "id": "inc-2"}  # 다른 카메라는 따로 기록
        assert report(client, slot=3).json()["id"] == "inc-3"
        # 슬롯 없는 사진 찾기 방식: same_place_m(15cm) 안이면 중복
        assert report(client, slot=None, method="photo_finder", x_m=2.0, y_m=2.0).json()["id"] == "inc-4"
        assert report(client, slot=None, method="photo_finder", x_m=2.1, y_m=2.0).json()["duplicate"] is True
        assert report(client, slot=None, method="photo_finder", x_m=1.7, y_m=2.0).json()["id"] == "inc-5"
        # 위치도 슬롯도 없으면 중복 판정 안 함
        assert report(client, slot=None, x_m=None, y_m=None).json()["id"] == "inc-6"
        assert report(client, slot=None, x_m=None, y_m=None).json()["id"] == "inc-7"


def test_incident_dedup_expires():
    from drone_server.incidents import IncidentStore
    from tests.conftest import make_settings
    t = [0.0]
    st = IncidentStore(incident_settings(make_settings(), dedup_s=30), clock=lambda: t[0])
    body = {"photo_jpeg_b64": b64(), "slot": 2}
    assert st.add(body)[1] is False
    t[0] = 29
    assert st.add(body)[1] is True
    t[0] = 31
    inc, dup = st.add(body)
    assert dup is False and inc.id == "inc-2"


def test_incident_keep_limit_and_list_on_connect(settings):
    s = incident_settings(settings, keep=3, dedup_s=0)
    with TestClient(create_app(s, MockBackend(s))) as client:
        for i in range(5):
            assert report(client, slot=None, x_m=None, y_m=None, place=f"p{i}").json()["id"] == f"inc-{i + 1}"
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "config"
            m = ws.receive_json()
            assert m["type"] == "incidents" and [i["id"] for i in m["items"]] == ["inc-5", "inc-4", "inc-3"]
        assert client.get("/incidents/inc-1/photo.jpg").status_code == 404   # 밀려난 사건
        assert client.get("/incidents/inc-3/photo.jpg").status_code == 200


def test_incident_minimal_body_and_errors(settings):
    s = incident_settings(settings, max_photo_kb=1)
    with TestClient(create_app(s, MockBackend(s))) as client:
        r = client.post("/vision/incident", json={"photo_jpeg_b64": b64()})
        assert r.json()["ok"]
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            item = ws.receive_json()["items"][0]
            assert item["slot"] is None and item["pos"] is None and item["geo"] is None
            assert item["mode"] is None and item["method"] is None and item["drone_pos"] is None
            assert isinstance(item["ts"], float)
        bad = [
            ({}, "BAD_REQUEST"),
            ({"photo_jpeg_b64": "@@@"}, "BAD_REQUEST"),
            ({"photo_jpeg_b64": b64(b"PNG....")}, "BAD_REQUEST"),
            ({"photo_jpeg_b64": b64(b"\xff\xd8" + b"0" * 2000)}, "TOO_LARGE"),
            ({"photo_jpeg_b64": b64(), "slot": 9}, "BAD_REQUEST"),
            ({"photo_jpeg_b64": b64(), "slot": True}, "BAD_REQUEST"),
            ({"photo_jpeg_b64": b64(), "mode": "x"}, "BAD_REQUEST"),
            ({"photo_jpeg_b64": b64(), "method": "x"}, "BAD_REQUEST"),
            ({"photo_jpeg_b64": b64(), "x_m": 3.0, "y_m": 1.0}, "OUT_OF_BOUNDS"),
            ({"photo_jpeg_b64": b64(), "drone": 5}, "BAD_REQUEST"),
            ({"photo_jpeg_b64": b64(), "detection": "fire"}, "BAD_REQUEST"),
        ]
        for body, code in bad:
            r = client.post("/vision/incident", json=body)
            assert r.status_code == 400 and r.json()["error"] == code, body
        r = client.post("/vision/incident", content=b"nope", headers={"Content-Type": "application/json"})
        assert r.status_code == 400 and r.json()["error"] == "BAD_REQUEST"


def test_incident_accepted_without_vision_input(settings):
    """pos_source = estimate여도 사건 보고는 받는다 (9.1)."""
    with TestClient(create_app(settings, MockBackend(settings))) as client:
        assert client.post("/vision/incident", json={"photo_jpeg_b64": b64()}).json()["ok"]


# ================================================================ 영상 (8장, 10.2)
def test_video_relay_bridge_to_viewer(settings):
    s = replace(settings, video=VideoCfg(max_fps=50, stale_s=2.0, max_frame_kb=1))
    with TestClient(create_app(s, MockBackend(s))) as client:
        with client.websocket_connect("/ws/video") as viewer:
            assert viewer.receive_json() == {"type": "video_status", "live": False, "message": "카메라 영상이 없습니다"}
            with client.websocket_connect("/vision/video") as bridge:
                bridge.receive_text()  # vision_mode
                bridge.send_bytes(b"\xff\xd8" + b"0" * 2000)   # max_frame_kb 넘음 → 버림
                bridge.send_bytes(b"not a jpeg")                # JPEG 아님 → 버림
                bridge.send_bytes(jpeg(1))
                st = viewer.receive_json()
                assert st["type"] == "video_status" and st["live"] is True
                assert viewer.receive_bytes() == jpeg(1)
                bridge.send_bytes(jpeg(2))
                assert viewer.receive_bytes() == jpeg(2)
                h = client.get("/").json()["vision_link"]
                assert h["video"]["viewers"] == 1 and h["video"]["frames_received"] == 2
                assert h["video"]["frames_dropped"] == 2 and h["video"]["last_frame_age_s"] < 1
                assert h["video_live"] is True
        # 영상을 이미 받고 있을 때 연결한 앱은 최신 장을 바로 받는다
        with client.websocket_connect("/ws/video") as viewer2:
            assert viewer2.receive_json()["live"] is True
            assert viewer2.receive_bytes() == jpeg(2)


def test_video_goes_stale(settings):
    s = replace(settings, video=VideoCfg(max_fps=50, stale_s=0.3))
    with TestClient(create_app(s, MockBackend(s))) as client:
        with client.websocket_connect("/vision/video") as bridge, client.websocket_connect("/ws/video") as viewer:
            bridge.receive_text()
            viewer.receive_json()
            bridge.send_bytes(jpeg(1))
            assert viewer.receive_json()["live"] is True
            assert viewer.receive_bytes() == jpeg(1)
            t0 = time.time()
            m = viewer.receive_json()   # stale_s 지나면 끊김 알림
            assert m == {"type": "video_status", "live": False, "message": "카메라 영상이 없습니다"}
            assert time.time() - t0 < 1.5


def test_second_bridge_replaces_first(settings):
    with TestClient(create_app(settings, MockBackend(settings))) as client:
        link = client.app.state.vision_link
        with client.websocket_connect("/vision/video") as b1:
            b1.receive_text()
            b1.send_text(json.dumps({"type": "bridge_status", "mode": "tt_down", "camera_ok": True}))
            with client.websocket_connect("/vision/video") as b2:
                assert recv_bridge(b2)["type"] == "vision_mode"
                msg = b1.receive()     # 이전 연결은 서버가 닫는다 (그 전에 온 drone_state는 건너뜀)
                while msg["type"] == "websocket.send" and '"drone_state"' in (msg.get("text") or ""):
                    msg = b1.receive()
                assert msg["type"] == "websocket.close"
                assert link.bridge is not None and link.bridge_mode is None
                b2.send_text(json.dumps({"type": "bridge_status", "mode": "tt_down", "camera_ok": True}))
                for _ in range(50):
                    if link.bridge_mode == "tt_down":
                        break
                    time.sleep(0.02)
                assert link.bridge_mode == "tt_down"
        for _ in range(50):
            if link.bridge is None:
                break
            time.sleep(0.02)
        assert link.bridge is None


class FakeWS:
    """영상 앱 흉내. send_delay_s만큼 보내기가 느리다 (None이면 영원히 멈춤)."""

    def __init__(self, send_delay_s=0.0):
        self.delay = send_delay_s
        self.frames = []
        self.texts = []
        self._closed = asyncio.Event()

    async def send_text(self, t):
        self.texts.append(json.loads(t))

    async def send_bytes(self, b):
        if self.delay is None:
            await asyncio.Event().wait()
        await asyncio.sleep(self.delay)
        self.frames.append(b)

    async def receive(self):
        await self._closed.wait()
        return {"type": "websocket.disconnect"}


def test_viewer_rate_limited_and_gets_latest():
    async def go():
        relay = VideoRelay(VideoCfg(max_fps=5, stale_s=2.0))
        ws = FakeWS()
        task = asyncio.create_task(relay.serve_viewer(ws))
        n = 0
        t_end = time.monotonic() + 1.0
        while time.monotonic() < t_end:   # 100 장/초로 1초
            n += 1
            relay.push(jpeg(n))
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.3)
        ws._closed.set()
        await asyncio.wait_for(task, 2)
        assert 4 <= len(ws.frames) <= 8, len(ws.frames)       # 초당 5장 제한
        assert ws.frames[-1] == jpeg(n)                        # 마지막에는 최신 장
        assert relay.viewers == 0
    asyncio.run(go())


def test_slow_viewer_does_not_delay_state_or_watchdog(monkeypatch, settings):
    """멈춘 영상 앱이 있어도 /ws state 전송·자동 착륙이 늦어지지 않는다 (안전 9번)."""
    monkeypatch.setattr(vl, "VIEWER_SEND_TIMEOUT_S", 0.5)
    s = replace(settings, limits=replace(settings.limits, link_timeout_s=0.5))

    async def go():
        b = MockBackend(s)
        await b.connect()
        hub = Hub()
        app_ws = FakeWS()
        hub.clients.append(app_ws)
        c = Controller(s, b, emit=hub.broadcast)
        relay = VideoRelay(VideoCfg(max_fps=30))
        stuck = [FakeWS(send_delay_s=None) for _ in range(3)]   # 영상 보내기가 영원히 안 끝나는 앱 3개
        viewers = [asyncio.create_task(relay.serve_viewer(w)) for w in stuck]
        watchdog = asyncio.create_task(c.watchdog_loop(0.05))

        async def frames():
            for i in range(1000):
                relay.push(jpeg(i))
                await asyncio.sleep(0.002)
        feeder = asyncio.create_task(frames())
        assert (await c.handle_cmd(cmd("1", "takeoff")))["ok"]
        # state 전송 간격 측정 (5Hz)
        gaps = []
        last = time.monotonic()
        for _ in range(10):
            await hub.broadcast(c.state_message())
            now = time.monotonic()
            gaps.append(now - last)
            last = now
            await asyncio.sleep(0.2)
        assert max(gaps) < 0.35, gaps
        # 앱 ping이 없으므로 자동 착륙해야 한다
        await wait_mode(c, "idle", timeout=5)
        assert any(m.get("code") == "LINK_LOST_LANDING" for m in app_ws.texts)
        # 멈춘 영상 앱은 시간 제한(VIEWER_SEND_TIMEOUT_S)으로 끊긴다
        await asyncio.wait_for(asyncio.gather(*viewers), 3)
        assert relay.viewers == 0
        for t in (feeder, watchdog):
            t.cancel()
        await asyncio.gather(feeder, watchdog, return_exceptions=True)
    asyncio.run(go())


def test_ws_state_rate_with_video_streaming(settings):
    """영상 앱이 읽지 않고 쌓아 두는 동안에도 /ws의 state는 약 5Hz로 온다."""
    with TestClient(create_app(settings, MockBackend(settings))) as client:
        with client.websocket_connect("/vision/video") as bridge, \
                client.websocket_connect("/ws/video"), client.websocket_connect("/ws") as ws:
            bridge.receive_text()
            recv_until(ws, "state")
            for i in range(100):
                bridge.send_bytes(jpeg(i))
            t0 = time.time()
            for _ in range(5):
                recv_until(ws, "state")
            assert 0.6 < time.time() - t0 < 1.6


# ================================================================ /sim/truth (12장)
def test_sim_truth_mock_keeps_real_position_separate(settings):
    s = vision_settings(settings, time_scale=40.0)
    b = MockBackend(s)
    with TestClient(create_app(s, b)) as client:
        assert client.get("/sim/truth").json() == {"ok": True, "x_m": 1.532, "y_m": 0.702, "z_m": 0, "yaw_deg": 0}
        # 카메라 추정값이 실제와 달라도 가짜 드론의 '실제' 위치는 끌려가지 않는다
        assert client.post("/vision/pose", json={"x_m": 1.6, "y_m": 0.8}).json()["ok"]
        assert (round(b.pose.x_cm), round(b.pose.y_cm)) == (160, 80)   # 추정(서버가 아는 위치)
        tr = client.get("/sim/truth").json()
        assert (tr["x_m"], tr["y_m"]) == (1.532, 0.702)
        # 이동 명령만큼은 실제로도 움직인다: 추정 (160,80) → (140,80) = 서쪽 20cm
        with client.websocket_connect("/ws") as ws:
            recv_until(ws, "state")
            ws.send_json(cmd("1", "goto", x_cm=140, y_cm=80))
            assert recv_until(ws, "ack")["ok"]
            for _ in range(100):
                st = recv_until(ws, "state")
                if st["mode"] == "hovering":
                    break
            tr = client.get("/sim/truth").json()
            assert abs(tr["x_m"] - 1.332) < 0.01 and abs(tr["y_m"] - 0.702) < 0.01 and abs(tr["z_m"] - 0.8) < 0.01


def test_sim_truth_without_vision_equals_pose(settings):
    async def go():
        b = MockBackend(settings)
        await b.connect()
        c = Controller(settings, b)
        assert (await c.handle_cmd(cmd("1", "goto", x_cm=120, y_cm=90)))["ok"]
        await wait_mode(c, "hovering")
        t = b.truth_pose()
        assert (round(t.x_cm), round(t.y_cm), round(t.z_cm)) == (120, 90, 80)
    asyncio.run(go())


def test_sim_truth_real_is_bad_request(settings):
    b = make_real(FakeRealTello())
    with TestClient(create_app(settings, b)) as client:
        r = client.get("/sim/truth")
        assert r.status_code == 400 and r.json()["ok"] is False and r.json()["error"] == "BAD_REQUEST"


def test_sim_truth_from_simulator_state():
    from tests.test_tello_backend import CAL, FakeSimTello
    from drone_server.backends.sim import SimBackend
    from drone_server.positioning import SimFrame, SimTruthProvider

    async def go():
        fake = FakeSimTello(start=(0.0, -2.5))
        prov = DeadReckoningProvider(Pose(153.2, 70.2, 0), frame=SimFrame(CAL))
        b = SimBackend(name="sim", cfg=TelloCfg(go_axes="sim"), provider=VisionProvider(prov), home=Pose(153.2, 70.2, 0),
                       make_cmd_tello=lambda: fake, read_state=lambda t: t.get_sim_debug_state(), frame=SimFrame(CAL))
        b._calib = CAL
        assert b.truth_pose() is None   # 아직 상태를 못 읽음
        await b.connect()
        want = SimTruthProvider(CAL)
        want.update(fake.get_sim_debug_state())
        got = b.truth_pose()
        assert (got.x_cm, got.y_cm) == (want.pose().x_cm, want.pose().y_cm)
        assert abs(got.x_cm - 124.7) < 1 and abs(got.y_cm - 116.6) < 1   # 시뮬레이터 시작 위치
        await b.close()
    asyncio.run(go())


# ================================================================ GET / 상태
def test_health_has_v07_fields(settings):
    with TestClient(create_app(settings, MockBackend(settings))) as client:
        h = client.get("/").json()
        assert h["api_version"] == "0.8"
        assert h["vision_link"]["mode"] == "tt_down" and h["vision_link"]["bridge_connected"] is False
        assert h["vision_link"]["video"] == {"last_frame_age_s": None, "viewers": 0, "frames_received": 0,
                                             "frames_dropped": 0, "stale_s": 2.0}
        assert h["incidents"] == {"count": 0, "total": 0, "duplicates": 0}


def test_vision_provider_clear():
    v = VisionProvider(DeadReckoningProvider(Pose(0, 0, 0)), stale_s=10)
    v.submit(50, 60, None, ts=5.0, z_cm=70)
    assert v.display_z_cm() == 70 and v.source == "vision"
    v.clear()
    assert v.source == "estimate" and v.display_z_cm() is None
    assert (v.pose().x_cm, v.pose().y_cm) == (50, 60)   # 마지막 값에서 이어 감
    assert v.submit(1, 1, ts=1.0)                         # 오래된 ts 기억도 지움 (새 모드는 새 시계)


@pytest.mark.parametrize("mode", ["esp32", "tt_down"])
def test_default_mode_from_settings(settings, mode):
    s = replace(settings, bridge=replace(settings.bridge, default_mode=mode))
    with TestClient(create_app(s, MockBackend(s))) as client, client.websocket_connect("/vision/video") as br:
        assert json.loads(br.receive_text()) == {"type": "vision_mode", "mode": mode}


# ================================================================ drone_state (자세·높이 → 연결 프로그램, 8장)
def test_bridge_receives_drone_state(settings):
    b = MockBackend(settings)
    with TestClient(create_app(settings, b)) as client:
        with client.websocket_connect("/vision/video") as bridge:
            got = None
            for _ in range(20):
                m = json.loads(bridge.receive_text())
                if m.get("type") == "drone_state":
                    got = m
                    break
            assert got is not None
            assert {"pitch_deg", "roll_deg", "yaw_deg", "tof_cm", "h_cm", "flying", "t"} <= set(got)
            assert got["pitch_deg"] == 0.0 and got["tof_cm"] == b.truth.z_cm


# ================================================================ 방향 보정 (카메라 yaw → TT 센서 보정)
def test_heading_offset_from_vision_corrects_move_direction():
    import asyncio
    from drone_server.backends.tello import TelloBackend
    from drone_server.models import Pose
    from drone_server.positioning import DeadReckoningProvider, VisionProvider
    from drone_server.settings import TelloCfg

    class T:  # 실제 TT 흉내: 켤 때 지도 위쪽에서 60° 반시계로 돌아 있었음 → 센서 0° = 지도 -60°
        def __init__(self):
            self.h, self.sensor_yaw, self.calls = 0.0, 0.0, []

        def connect(self):
            pass

        def get_current_state(self):
            return {"bat": 90, "h": self.h, "yaw": self.sensor_yaw, "tof": self.h + 10, "pitch": 0, "roll": 0}

        def takeoff(self):
            self.h = 50.0

        def go_xyz_speed(self, x, y, z, s):
            self.calls.append((x, y, z))

        def send_rc_control(self, *a):
            pass

    async def go():
        t = T()
        vis = VisionProvider(DeadReckoningProvider(Pose(100, 100, 0)))
        cfg = TelloCfg(go_axes="sdk", segment_cm=100, heading_from_vision=True, heading_min_samples=5)
        b = TelloBackend(name="real", cfg=cfg, provider=vis, home=Pose(100, 100, 0), make_cmd_tello=lambda: t)
        await b.connect()
        await b.takeoff()
        for i in range(8):  # 카메라: 실제 드론 방향은 지도 기준 -60° (=300°)
            vis.submit(100, 100, 300.0 + (i % 3 - 1) * 2.0, ts=float(i), confidence=0.9)
            await asyncio.sleep(0.12)
        assert b.heading_offset_deg is not None and abs(b.heading_offset_deg - (-60.0)) < 3
        assert b.attitude()["tof_cm"] == 60.0
        vis.submit(100, 100, 300.0, ts=100.0, confidence=0.9)
        await b.move_to(100, 160, 50)  # 지도 위쪽(+y)으로 60cm
        fwd, left = t.calls[-1][0], t.calls[-1][1]
        # 드론이 -60°를 보고 있으므로 지도 위쪽은 드론 기준 '오른쪽 앞'(오른쪽 = -left)
        assert fwd > 0 and left < 0 and abs(math.degrees(math.atan2(-left, fwd)) - 60) < 5
        await b.close()
    import math
    asyncio.run(go())
