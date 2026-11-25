"""API v0.8: 차(UGV) 제어 — 좌표 변환, 위치 추적(드론 인식 + 엔코더), 가짜 차로 경로·조이스틱·안전."""
import math
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from drone_server.app import create_app
from drone_server.backends.mock import MockBackend
from drone_server.car import CarTracker, MockCarLink, OdomToMap
from drone_server.safety import SafetyError
from drone_server.settings import CarCfg


def car_settings(settings, **kw):
    return replace(settings, car=CarCfg(mode="mock", mock_start_cm=(120.0, 70.0), mock_start_yaw_deg=0.0, **kw))


def cmd(cid, name, **kw):
    return {"type": "cmd", "id": cid, "cmd": name, **kw}


def recv_until(ws, pred, n=200):
    for _ in range(n):
        m = ws.receive_json()
        if pred(m):
            return m
    raise AssertionError("원하는 메시지 없음")


# ================================================================ 좌표 변환

@pytest.mark.parametrize("yaw", [0, 37, 90, 180, 271])
def test_odom_to_map_roundtrip(yaw):
    # 차가 지도 (100, 50)cm, yaw에 있을 때 엔코더는 (300mm, -120mm, 25°)였다
    t = OdomToMap.from_pair(100, 50, yaw, 300, -120, 25)
    x, y, h = t.to_map(300, -120, 25)
    assert (x, y) == pytest.approx((100, 50)) and h == pytest.approx(yaw % 360)
    # 그 자리에서 엔코더 +X(전방)로 100mm = 지도에서 yaw 방향으로 10cm
    x2, y2, _ = t.to_map(300 + 100 * math.cos(math.radians(25)), -120 + 100 * math.sin(math.radians(25)), 25)
    a = math.radians(yaw)
    assert (x2, y2) == pytest.approx((100 + 10 * math.sin(a), 50 + 10 * math.cos(a)), abs=1e-6)
    # 역변환
    assert t.to_odom(x2, y2) == pytest.approx((300 + 100 * math.cos(math.radians(25)),
                                               -120 + 100 * math.sin(math.radians(25))), abs=1e-6)


def test_odom_left_is_map_left():
    # 차가 지도 위쪽(yaw 0)을 보고 있으면 엔코더 +Y(좌측) = 지도 왼쪽(-x)
    t = OdomToMap.from_pair(100, 100, 0, 0, 0, 0)
    assert t.to_map(0, 100, 0)[:2] == pytest.approx((90, 100))
    # 엔코더 θ +90°(좌회전) = 지도 yaw -90° = 270°
    assert t.to_map(0, 0, 90)[2] == pytest.approx(270)


def test_tracker_vision_then_odom_then_jump():
    now = [0.0]
    tr = CarTracker(CarCfg(pose_fresh_s=1.0, odom_jump_mm=300), clock=lambda: now[0])
    assert tr.pose() == (None, None)
    tr.on_vision(100, 100, 0)               # 엔코더 모름 → 기준점 없음, 인식 위치만
    assert tr.pose()[1] == "vision" and tr.anchor is None
    tr.on_odom(0, 0, 0)
    assert tr.on_vision(100, 100, 0) is True   # 이제 기준점
    now[0] = 5.0
    tr.on_odom(200, 0, 0)                   # 전방 20cm
    (x, y, yaw), src = tr.pose()
    assert src == "odom" and (x, y) == pytest.approx((100, 120))
    tr.on_odom(2000, 0, 0)                  # 갑자기 1.8m → 재부팅으로 봄
    assert tr.anchor is None and tr.anchor_lost_reason
    with pytest.raises(SafetyError) as e:
        tr.route_anchor()
    assert e.value.code == "CAR_NO_POSITION"


def test_tracker_anchor_too_old():
    now = [0.0]
    tr = CarTracker(CarCfg(route_anchor_max_s=10), clock=lambda: now[0])
    tr.on_odom(0, 0, 0)
    tr.on_vision(100, 100, 0)
    now[0] = 11
    with pytest.raises(SafetyError) as e:
        tr.route_anchor()
    assert e.value.code == "CAR_POSITION_OLD"


# ================================================================ 서버 + 가짜 차

def _client(settings, **kw):
    s = car_settings(settings, **kw)
    car = MockCarLink(s.car, time_scale=10.0, slip=0.97)
    app = create_app(s, MockBackend(s), car)
    return s, car, app


def _see_car(client):
    """드론이 차를 봤다고 알림 (가짜 차 실제 위치 = /sim/truth ugv)."""
    u = client.get("/sim/truth").json()["ugv"]
    r = client.post("/vision/ugv", json=u)
    assert r.json() == {"ok": True}
    return u


def test_config_and_state_have_car(settings):
    s, car, app = _client(settings)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        cfg = ws.receive_json()
        assert cfg["type"] == "config" and cfg["car"]["link"] == "mock" and cfg["car"]["max_points"] == 15
        st = recv_until(ws, lambda m: m["type"] == "state" and m["car"]["state"] == "READY")
        assert st["car"]["connected"] and st["car"]["pos"] is None and st["car"]["pos_source"] is None


def test_no_car_means_null_config(settings):
    with TestClient(create_app(settings, MockBackend(settings))) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["car"] is None
        ws.send_json(cmd("c1", "car_stop"))
        a = recv_until(ws, lambda m: m["type"] == "ack")
        assert a["ok"] is False and a["error"] == "BAD_REQUEST"


def test_route_needs_drone_recognition(settings):
    s, car, app = _client(settings)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        recv_until(ws, lambda m: m["type"] == "state" and m["car"]["connected"])
        ws.send_json(cmd("c1", "car_route", points_cm=[[120, 120]]))
        a = recv_until(ws, lambda m: m["type"] == "ack")
        assert a["ok"] is False and a["error"] == "CAR_NO_POSITION"


def test_route_drives_car_to_map_point(settings):
    s, car, app = _client(settings)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        recv_until(ws, lambda m: m["type"] == "state" and m["car"]["state"] == "READY")
        time.sleep(0.3)   # STATUS(엔코더) 한 번 이상
        u = _see_car(client)
        st = recv_until(ws, lambda m: m["type"] == "state" and m["car"]["pos_source"] == "vision")
        assert st["car"]["pos"]["x_cm"] == pytest.approx(u["x_m"] * 100, abs=0.2)
        # 오른쪽으로 갔다가 위로: (150, 70) → (150, 110)
        target = [[150, 70], [150, 110]]
        ws.send_json(cmd("c2", "car_route", points_cm=target, speed_mm_s=100))
        a = recv_until(ws, lambda m: m["type"] == "ack")
        assert a["ok"], a
        names = [d["cmd"] for d in car.sent if d["cmd"] != "GET_STATUS"]
        assert names[-4:] == ["STOP", "RESET_ODOM", "SET_ROUTE", "START"]   # 펌웨어: 첫 점은 원점이어야 함
        route = [d for d in car.sent if d["cmd"] == "SET_ROUTE"][-1]
        assert route["points"][0] == [0, 0]          # P0 = 원점(방금 RESET_ODOM한 지금 자리)
        assert route["points"][1] == [0, -300]       # 차는 위(yaw 0)를 봄 → 지도 오른쪽 30cm = 엔코더 우측(-Y) 300mm
        st = recv_until(ws, lambda m: m["type"] == "state" and m["car"]["route"] is not None)
        assert st["car"]["route"]["points_cm"][-1] == [150, 110]
        # 끝날 때까지 (가짜 차 10배속, 경로 70cm @ 100mm/s → 실제 약 1초 + 회전). 펌웨어는 끝나면 ROUTE_READY
        recv_until(ws, lambda m: m["type"] == "event" and m["code"] == "CAR_ROUTE_DONE", n=400)
        assert car.state == "ROUTE_READY" and car.wp == 2
        st = recv_until(ws, lambda m: m["type"] == "state")
        assert st["car"]["route"] is None
        tx, ty, _ = car.truth_map()
        # 엔코더는 정확히 갔다고 믿지만 바퀴가 3% 미끄러져 실제로는 조금 덜 간다
        assert math.hypot(tx - 150, ty - 110) < 5.0
        # 드론이 다시 보면 실제 위치로 맞춰진다
        u2 = _see_car(client)
        recv_until(ws, lambda m: m["type"] == "state" and m["car"]["pos_source"] == "vision"
                   and abs(m["car"]["pos"]["y_cm"] - u2["y_m"] * 100) < 0.5)


def test_route_rejects_out_of_map(settings):
    s, car, app = _client(settings)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        recv_until(ws, lambda m: m["type"] == "state" and m["car"]["connected"])
        time.sleep(0.3)
        _see_car(client)
        ws.send_json(cmd("c1", "car_route", points_cm=[[248, 100]]))   # 가장자리 5cm 안
        a = recv_until(ws, lambda m: m["type"] == "ack")
        assert a["error"] == "OUT_OF_BOUNDS"
        ws.send_json(cmd("c2", "car_route", points_cm=[[100, 100]] * 16))
        assert recv_until(ws, lambda m: m["type"] == "ack")["error"] == "BAD_REQUEST"


def test_joystick_drive_and_release_stop(settings):
    s, car, app = _client(settings, drive_timeout_s=0.3)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        recv_until(ws, lambda m: m["type"] == "state" and m["car"]["connected"])
        for _ in range(4):
            ws.send_json({"type": "car_drive", "v_mm_s": 500, "omega_rad_s": -9})   # 최대값으로 잘림
            time.sleep(0.1)
        drives = [d for d in car.sent if d["cmd"] == "DRIVE"]
        assert drives and drives[-1] == {"cmd": "DRIVE", "v_mm_s": 100, "omega_rad_s": -1.5}
        assert car.state == "MANUAL"
        time.sleep(0.6)   # 손 뗌(명령 끊김) → 서버가 STOP
        assert car.sent[-1]["cmd"] == "STOP" and car.state == "READY"


def test_estop_blocks_drive_until_cleared(settings):
    s, car, app = _client(settings)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        recv_until(ws, lambda m: m["type"] == "state" and m["car"]["connected"])
        ws.send_json(cmd("c1", "car_estop"))
        recv_until(ws, lambda m: m["type"] == "event" and m["code"] == "CAR_ESTOP")   # 알림이 ack보다 먼저 온다
        assert recv_until(ws, lambda m: m["type"] == "ack")["ok"]
        n = len(car.sent)
        ws.send_json({"type": "car_drive", "v_mm_s": 50, "omega_rad_s": 0})
        time.sleep(0.4)   # 서버가 ESTOP 상태(STATUS)를 알고 DRIVE를 보내지 않는다
        ws.send_json({"type": "car_drive", "v_mm_s": 50, "omega_rad_s": 0})
        time.sleep(0.1)
        assert car.state == "ESTOP"
        assert not [d for d in car.sent[n + 1:] if d["cmd"] == "DRIVE"]
        ws.send_json(cmd("c2", "car_clear_estop"))
        assert recv_until(ws, lambda m: m["type"] == "ack")["ok"]
        assert car.state == "READY"


def test_only_controller_app_can_drive(settings):
    s, car, app = _client(settings)
    with TestClient(app) as client, client.websocket_connect("/ws") as old, client.websocket_connect("/ws") as new:
        recv_until(old, lambda m: m["type"] == "state" and m["car"]["connected"])
        old.send_json({"type": "car_drive", "v_mm_s": 50, "omega_rad_s": 0})
        old.send_json(cmd("c1", "car_stop"))
        assert recv_until(old, lambda m: m["type"] == "ack")["error"] == "BUSY"
        assert not [d for d in car.sent if d["cmd"] == "DRIVE"]


def test_app_link_lost_stops_moving_car(settings):
    s, car, app = _client(settings)
    s = replace(s, limits=replace(s.limits, link_timeout_s=0.5))
    app = create_app(s, MockBackend(s), car)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            recv_until(ws, lambda m: m["type"] == "state" and m["car"]["connected"])
            time.sleep(0.3)
            _see_car(client)
            ws.send_json(cmd("c1", "car_route", points_cm=[[120, 200]], speed_mm_s=30))
            assert recv_until(ws, lambda m: m["type"] == "ack")["ok"]
            ws.send_json({"type": "ping"})   # 실제 앱처럼 살아 있음을 알림 (이 시험은 끊김 시간을 0.5초로 줄임)
            time.sleep(0.15)
            assert car.state == "RUNNING"
        # 앱이 사라짐 → 서버가 차를 세운다 (경로는 남아 있어 펌웨어 상태는 ROUTE_READY)
        for _ in range(30):
            if not car.running:
                break
            time.sleep(0.1)
        assert car.state == "ROUTE_READY" and [d for d in car.sent if d["cmd"] != "GET_STATUS"][-1]["cmd"] == "STOP"


def test_vision_ugv_validation(settings):
    s, car, app = _client(settings)
    with TestClient(app) as client:
        assert client.post("/vision/ugv", json={"x_m": 1.0}).status_code == 400
        r = client.post("/vision/ugv", json={"x_m": 9.0, "y_m": 1.0, "yaw_deg": 0})
        assert r.json()["error"] == "OUT_OF_BOUNDS"
    with TestClient(create_app(settings, MockBackend(settings))) as client:
        assert client.post("/vision/ugv", json={"x_m": 1, "y_m": 1, "yaw_deg": 0}).status_code == 400


def test_ble_send_line_chunks_like_pwa():
    """BLE 전송: 한 줄 JSON + 줄바꿈을 chunk_bytes(18)씩 응답 있는 쓰기로 (PWA sendRawLine과 같음)."""
    import asyncio

    from drone_server.car import BleCarLink

    class FakeClient:
        def __init__(self):
            self.writes = []

        async def write_gatt_char(self, uuid, data, response):
            assert response is True
            self.writes.append((uuid, bytes(data)))

    link = BleCarLink(CarCfg(mode="ble"))
    link.client, link.connected = FakeClient(), True
    line = '{"cmd":"SET_ROUTE","points":[[0,0],[0,-300]],"max_speed_mm_s":60}'
    asyncio.run(link.send_line(line))
    w = link.client.writes
    assert all(u == CarCfg().rx_uuid for u, _ in w) and all(len(d) <= 18 for _, d in w)
    assert b"".join(d for _, d in w) == (line + "\n").encode()


# ================================================================ 펌웨어(OpenRB_PoliceCar.ino)와 맞추기

def test_mock_firmware_rejects_route_not_starting_at_origin():
    """펌웨어: SET_ROUTE 첫 점이 (0,0)에서 50mm 넘게 떨어지면 조용히 무시 → START해도 안 움직임."""
    import asyncio
    car = MockCarLink(CarCfg(mode="mock"))
    car.connected = True
    car.x = 400.0   # 차가 원점에서 40cm 움직인 상태
    asyncio.run(car.send_line('{"cmd":"SET_ROUTE","points":[[400,0],[800,0]],"max_speed_mm_s":60}'))
    asyncio.run(car.send_line('{"cmd":"START"}'))
    assert car.state == "READY" and not car.running
    asyncio.run(car.send_line('{"cmd":"RESET_ODOM"}'))
    asyncio.run(car.send_line('{"cmd":"SET_ROUTE","points":[[0,0],[400,0]],"max_speed_mm_s":60}'))
    asyncio.run(car.send_line('{"cmd":"START"}'))
    assert car.state == "RUNNING"


def test_tracker_reset_keeps_map_pose_and_ignores_stale_status():
    now = [0.0]
    tr = CarTracker(CarCfg(), clock=lambda: now[0])
    tr.on_odom(1000, 200, 30)
    tr.on_vision(150, 120, 60)
    here = tr.pose()[0]
    tr.expect_reset(here)
    tr.on_odom(1000, 200, 30)          # 재설정 전에 보낸 STATUS → 버림 (차 재시작으로 오해하지 않음)
    assert tr.reset_pending and tr.anchor_lost_reason is None
    tr.on_odom(0, 0, 0)                # 재설정 후
    assert not tr.reset_pending
    now[0] = 5                          # 드론 인식은 오래됨 → 기준점 + 엔코더
    (x, y, yaw), src = tr.pose()
    assert src == "odom" and (x, y) == pytest.approx((150, 120)) and yaw == pytest.approx(60)
    tr.on_odom(100, 0, 0)              # 새 원점 기준 전방 10cm = 지도 yaw 60° 방향
    (x, y, _), _ = tr.pose()
    assert (x, y) == pytest.approx((150 + 10 * math.sin(math.radians(60)), 120 + 10 * math.cos(math.radians(60))))


def test_route_works_after_car_moved_with_joystick(settings):
    """예전 서버는 P0 = 지금 엔코더 위치를 보내서, 차가 원점을 떠난 뒤에는 펌웨어가 경로를 조용히 버렸다."""
    s, car, app = _client(settings)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        recv_until(ws, lambda m: m["type"] == "state" and m["car"]["state"] == "READY")
        time.sleep(0.4)
        _see_car(client)
        for _ in range(8):   # 조이스틱으로 앞으로 (10배속 → 약 40cm)
            ws.send_json({"type": "car_drive", "v_mm_s": 100, "omega_rad_s": 0})
            time.sleep(0.05)
        time.sleep(0.6)
        assert math.hypot(car.x, car.y) > 100   # 엔코더 원점에서 10cm 넘게 떠남
        _see_car(client)                         # 드론이 다시 봄
        tx, ty, _ = car.truth_map()
        target = [[tx + 30, ty]]
        ws.send_json(cmd("r1", "car_route", points_cm=target, speed_mm_s=100))
        assert recv_until(ws, lambda m: m["type"] == "ack")["ok"]
        recv_until(ws, lambda m: m["type"] == "event" and m["code"] == "CAR_ROUTE_DONE", n=400)
        fx, fy, _ = car.truth_map()
        assert math.hypot(fx - target[0][0], fy - target[0][1]) < 5.0


def test_route_not_started_is_reported(settings):
    """펌웨어는 경로를 거부해도 오류를 안 보낸다 → 서버가 RUNNING이 안 되는 걸 보고 알린다."""
    s, car, app = _client(settings, route_start_timeout_s=0.8)
    car.ignore_start = True
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        recv_until(ws, lambda m: m["type"] == "state" and m["car"]["state"] == "READY")
        time.sleep(0.4)
        _see_car(client)
        ws.send_json(cmd("r1", "car_route", points_cm=[[120, 120]]))
        assert recv_until(ws, lambda m: m["type"] == "ack")["ok"]
        e = recv_until(ws, lambda m: m["type"] == "event" and m["code"] == "CAR_ROUTE_NOT_STARTED", n=400)
        assert "ROUTE_READY" in e["message"]
        st = recv_until(ws, lambda m: m["type"] == "state")
        assert st["car"]["route"] is None


def test_reset_without_reply_fails_cleanly(settings):
    """차가 RESET_ODOM에 답하지 않으면(예: 주행 중이라 무시) 경로를 보내지 않고 CAR_NO_RESPONSE."""
    s, car, app = _client(settings, reset_wait_s=0.5)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        recv_until(ws, lambda m: m["type"] == "state" and m["car"]["state"] == "READY")
        time.sleep(0.4)
        _see_car(client)
        car.x = 999.0   # 원점 재설정이 안 먹은 것처럼 (STATUS가 원점 근처로 안 옴)
        orig = car.send_line

        async def no_reset(line):
            if '"RESET_ODOM"' in line:
                return
            await orig(line)
        car.send_line = no_reset
        ws.send_json(cmd("r1", "car_route", points_cm=[[120, 120]]))
        a = recv_until(ws, lambda m: m["type"] == "ack")
        assert a["error"] == "CAR_NO_RESPONSE"
        assert not [d for d in car.sent if d["cmd"] in ("SET_ROUTE", "START")]
