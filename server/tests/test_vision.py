"""카메라 위치 추정 입력 (docs/api.md 7장): POST /vision/pose, VisionProvider."""
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from drone_server.app import create_app
from drone_server.backends.mock import MockBackend
from drone_server.models import Pose
from drone_server.positioning import DeadReckoningProvider, VisionProvider
from drone_server.settings import VisionCfg


def vision_settings(settings, **vision):
    return replace(settings, mock=replace(settings.mock, pos_source="vision", time_scale=1.0),
                   vision=replace(VisionCfg(), **vision))


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- VisionProvider
def test_fresh_vision_used_then_stale_falls_back_to_dead_reckoning():
    clk = Clock()
    dr = DeadReckoningProvider(Pose(0, 0, 0))
    v = VisionProvider(dr, stale_s=1.0, clock=clk)
    assert v.source == "estimate" and v.pose().x_cm == 0  # 아직 vision 없음
    v.update({"h": 80, "yaw": 15})
    assert v.submit(120.0, 40.0, None)
    p = v.pose()
    assert v.source == "vision" and (p.x_cm, p.y_cm, p.z_cm, p.yaw_deg) == (120, 40, 80, 15)  # yaw 없으면 센서 yaw
    v.on_move(10, 5, 0)          # vision이 신선한 동안의 이동은 기준점 위에 쌓인다
    assert v.pose().x_cm == 120  # 신선한 동안은 vision 값 그대로
    clk.t += 1.5                 # stale_s 초과
    p = v.pose()
    assert v.source == "estimate" and (p.x_cm, p.y_cm) == (130, 45)  # 마지막 vision + 이후 이동
    v.submit(50.0, 60.0, 90.0)   # 새 값 → 기준점도 갱신
    assert v.source == "vision" and v.pose().yaw_deg == 90
    clk.t += 2
    assert (v.pose().x_cm, v.pose().y_cm) == (50, 60)


def test_out_of_order_ts_ignored():
    v = VisionProvider(DeadReckoningProvider(Pose(0, 0, 0)), clock=Clock())
    assert v.submit(10, 10, ts=5.0)
    assert not v.submit(99, 99, ts=4.0)
    assert v.pose().x_cm == 10


# ---------------------------------------------------------------- HTTP
def test_post_converts_m_to_cm_and_state_follows(settings):
    s = vision_settings(settings)
    with TestClient(create_app(s, MockBackend(s))) as client:
        r = client.post("/vision/pose", json={"x_m": 0.2, "y_m": 2.33, "yaw_deg": 45.0, "confidence": 0.9, "ts": time.time()})
        assert r.status_code == 200 and r.json() == {"ok": True}
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # config
            st = ws.receive_json()
            assert st["type"] == "state" and st["pos_source"] == "vision"
            assert st["pos"]["x_cm"] == 20 and st["pos"]["y_cm"] == 233 and st["pos"]["yaw_deg"] == 45
        assert client.get("/").json()["vision"]["enabled"] is True


def next_state(ws, pred, n=40):
    """조건을 만족하는 state가 올 때까지 읽는다 (5Hz → n=40은 약 8초)."""
    for _ in range(n):
        m = ws.receive_json()
        if m["type"] == "state" and pred(m):
            return m
    raise AssertionError("조건에 맞는 state 없음")


def test_mock_vision_state_pos_tracks_vision_then_falls_back(settings):
    s = vision_settings(settings, stale_s=0.4)
    b = MockBackend(s)
    with TestClient(create_app(s, b)) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        for x in (0.5, 0.6, 0.7):
            assert client.post("/vision/pose", json={"x_m": x, "y_m": 1.0}).json()["ok"]
            st = next_state(ws, lambda m, x=x: m["pos"]["x_cm"] == round(x * 100, 1))
            assert st["pos_source"] == "vision" and st["pos"]["y_cm"] == 100
        st = next_state(ws, lambda m: m["pos_source"] == "estimate")  # stale_s 지나면
        assert (st["pos"]["x_cm"], st["pos"]["y_cm"]) == (70, 100)  # 가짜 드론이 마지막 vision 위치로 옮겨져 있음


def test_out_of_bounds_rejected_with_margin(settings):
    s = vision_settings(settings, margin_m=0.1)
    with TestClient(create_app(s, MockBackend(s))) as client:
        assert client.post("/vision/pose", json={"x_m": 2.55, "y_m": -0.05}).json()["ok"]  # 여유 안
        for body in ({"x_m": 2.61, "y_m": 1.0}, {"x_m": 1.0, "y_m": -0.2}, {"x_m": 3.0, "y_m": 3.0}):
            r = client.post("/vision/pose", json=body)
            assert r.status_code == 400 and r.json()["error"] == "OUT_OF_BOUNDS", body


def test_low_confidence_rejected(settings):
    s = vision_settings(settings, min_confidence=0.5)
    with TestClient(create_app(s, MockBackend(s))) as client:
        r = client.post("/vision/pose", json={"x_m": 1.0, "y_m": 1.0, "confidence": 0.3})
        assert r.status_code == 400 and r.json()["error"] == "LOW_CONFIDENCE"
        assert client.post("/vision/pose", json={"x_m": 1.0, "y_m": 1.0, "confidence": 0.5}).json()["ok"]
        assert client.post("/vision/pose", json={"x_m": 1.0, "y_m": 1.0}).json()["ok"]  # confidence 생략 = 받음


def test_bad_requests(settings):
    s = vision_settings(settings)
    with TestClient(create_app(s, MockBackend(s))) as client:
        for body in ({"x_m": 1.0}, {"x_m": "1", "y_m": 1}, {"x_m": True, "y_m": 1}, {"x_m": 1, "y_m": 1, "confidence": 2}, [1, 2]):
            r = client.post("/vision/pose", json=body)
            assert r.status_code == 400 and r.json()["error"] == "BAD_REQUEST", body
        r = client.post("/vision/pose", content=b"not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 400 and r.json()["error"] == "BAD_REQUEST"


def test_vision_disabled_returns_bad_request(settings):
    with TestClient(create_app(settings, MockBackend(settings))) as client:
        r = client.post("/vision/pose", json={"x_m": 1.0, "y_m": 1.0})
        assert r.status_code == 400 and r.json()["error"] == "BAD_REQUEST"


def test_mock_goto_starts_from_vision_position(settings):
    """vision 위치에서 출발해 목표로 간다 (가짜 드론 위치가 vision 값으로 옮겨지므로)."""
    import asyncio
    from drone_server.controller import Controller

    async def go():
        s = vision_settings(settings)
        s = replace(s, mock=replace(s.mock, time_scale=40.0))
        b = MockBackend(s)
        await b.connect()
        c = Controller(s, b)
        b.vision.submit(100.0, 150.0)
        assert (b.pose.x_cm, b.pose.y_cm) == (100, 150)
        assert (await c.handle_cmd({"type": "cmd", "id": "1", "cmd": "goto", "x_cm": 120, "y_cm": 150}))["ok"]
        for _ in range(250):
            if c.mode == "hovering" and not c.busy:
                break
            await asyncio.sleep(0.02)
        assert round(b.pose.x_cm) == 120 and round(b.pose.y_cm) == 150
    asyncio.run(go())


def test_run_server_vision_flag_validates():
    from drone_server.settings import load_settings, validate
    s = load_settings()
    for kind in ("mock", "sim", "real"):
        validate(replace(s, **{kind: replace(getattr(s, kind), pos_source="vision")}))
