"""WebSocket 프로토콜 (docs/api.md) — FastAPI TestClient 사용."""
import json
import time

from fastapi.testclient import TestClient

from drone_server.app import create_app
from drone_server.backends.mock import MockBackend


def recv_until(ws, kind, n=50):
    for _ in range(n):
        m = ws.receive_json()
        if m["type"] == kind:
            return m
    raise AssertionError(f"{kind} 메시지 없음")


def make_client(settings):
    return TestClient(create_app(settings, MockBackend(settings)))


def test_config_first_then_state(settings):
    with make_client(settings) as client, client.websocket_connect("/ws") as ws:
        cfg = ws.receive_json()
        assert cfg["type"] == "config" and cfg["api_version"] == "0.5"
        assert cfg["map"] == {"width_cm": 250, "height_cm": 250}
        assert cfg["home"] == {"x_cm": 153.2, "y_cm": 70.2}
        assert set(cfg["limits"]) == {"max_radius_cm", "min_alt_cm", "max_alt_cm", "default_alt_cm",
                                      "min_battery_pct", "link_timeout_s"}
        assert cfg["no_fly_zones"] == [] and cfg["backend"] == "mock" and "geo" in cfg
        st = recv_until(ws, "state")
        assert set(st) == {"type", "ts", "connected", "flying", "mode", "pos", "pos_source", "target", "battery_pct"}
        assert st["mode"] == "idle" and st["target"] is None
        assert set(st["pos"]) == {"x_cm", "y_cm", "z_cm", "yaw_deg"}


def test_state_rate_about_5hz(settings):
    with make_client(settings) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        recv_until(ws, "state")
        t0 = time.time()
        for _ in range(5):
            recv_until(ws, "state")
        assert 0.6 < time.time() - t0 < 1.6


def test_cmd_ack_and_errors(settings):
    with make_client(settings) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        recv_until(ws, "state")  # 백엔드 연결 대기
        time.sleep(0.1)
        ws.send_json({"type": "cmd", "id": "c-1", "cmd": "goto", "x_cm": 999, "y_cm": 10})
        a = recv_until(ws, "ack")
        assert a == {"type": "ack", "id": "c-1", "ok": False, "error": "OUT_OF_BOUNDS",
                     "message": "지도 경계 밖 좌표입니다"}
        ws.send_json({"type": "cmd", "id": "c-2", "cmd": "fly_to_moon"})
        assert recv_until(ws, "ack")["error"] == "BAD_REQUEST"
        ws.send_json({"type": "cmd", "id": "c-3", "cmd": "emergency"})
        assert recv_until(ws, "ack")["error"] == "NOT_CONFIRMED"
        ws.send_json({"type": "cmd", "id": "c-4", "cmd": "takeoff"})
        assert recv_until(ws, "ack") == {"type": "ack", "id": "c-4", "ok": True}
        ws.send_json({"type": "ping", "ts": time.time()})
        ws.send_text("this is not json")
        e = recv_until(ws, "event")
        assert e["level"] == "error" and e["code"] == "BAD_REQUEST"
        st = recv_until(ws, "state")
        assert st["mode"] in ("taking_off", "hovering")


def test_only_last_client_can_command(settings):
    with make_client(settings) as client:
        with client.websocket_connect("/ws") as ws1:
            ws1.receive_json()
            with client.websocket_connect("/ws") as ws2:
                assert ws2.receive_json()["type"] == "config"
                ws1.send_json({"type": "cmd", "id": "a", "cmd": "takeoff"})
                a = recv_until(ws1, "ack")
                assert a["ok"] is False and a["error"] == "BUSY"
                ws2.send_json({"type": "cmd", "id": "b", "cmd": "land"})
                assert recv_until(ws2, "ack")["ok"] is True
                # 둘 다 state는 받는다
                recv_until(ws1, "state")
                recv_until(ws2, "state")


def test_health_endpoint(settings):
    with make_client(settings) as client:
        r = client.get("/")
        assert r.status_code == 200 and r.json()["ok"] is True


def test_numbers_are_json_ints_when_integral(settings):
    from drone_server.app import encode
    assert json.loads(encode({"a": 250.0, "b": [1.5, 2.0]})) == {"a": 250, "b": [1.5, 2]}
    assert '"a": 250,' in encode({"a": 250.0, "z": 1})
