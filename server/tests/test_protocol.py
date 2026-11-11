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
        assert cfg["type"] == "config" and cfg["api_version"] == "0.6"
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


# ---------------------------------------------------------------- 명령권 넘겨받기 (api v0.6 1장, 4.2)
def _fast_link_settings(settings, timeout=0.6):
    from dataclasses import replace
    return replace(settings, limits=replace(settings.limits, link_timeout_s=timeout),
                   mock=replace(settings.mock, time_scale=40.0))


def _wait_state(ws, pred, n=60):
    for _ in range(n):
        m = ws.receive_json()
        if m["type"] == "state" and pred(m):
            return m
    raise AssertionError("조건에 맞는 state 없음")


def _events(ws, code, n=30):
    for _ in range(n):
        m = ws.receive_json()
        if m["type"] == "event" and m["code"] == code:
            return m
    raise AssertionError(f"event {code} 없음")


def test_controller_handover_keeps_flying_and_accepts_commands(settings):
    s = _fast_link_settings(settings, timeout=0.6)
    b = MockBackend(s)
    with TestClient(create_app(s, b)) as client:
        with client.websocket_connect("/ws") as ws1:
            ws1.receive_json()
            with client.websocket_connect("/ws") as ws2:
                ws2.receive_json()
                ws2.send_json({"type": "cmd", "id": "go", "cmd": "takeoff"})
                assert recv_until(ws2, "ack")["ok"]
                _wait_state(ws1, lambda m: m["mode"] == "hovering")
            # ws2(명령권 앱) 끊김 → ws1이 넘겨받음
            ev = _events(ws1, "CONTROLLER_CHANGED")
            assert ev["level"] == "info"
            # 넘겨받은 직후 0.6초(link_timeout) 넘게 ping을 보내도 착륙하지 않는다
            for _ in range(8):
                ws1.send_json({"type": "ping", "ts": time.time()})
                time.sleep(0.15)
            st = _wait_state(ws1, lambda m: True)
            assert st["flying"] and st["mode"] == "hovering"
            # 남은 앱의 명령이 받아들여진다
            ws1.send_json({"type": "cmd", "id": "g", "cmd": "goto", "x_cm": 120, "y_cm": 90})
            assert recv_until(ws1, "ack")["ok"] is True


def test_handover_app_that_stops_pinging_triggers_landing(settings):
    s = _fast_link_settings(settings, timeout=0.6)
    with TestClient(create_app(s, MockBackend(s))) as client:
        with client.websocket_connect("/ws") as ws1:
            ws1.receive_json()
            with client.websocket_connect("/ws") as ws2:
                ws2.receive_json()
                ws2.send_json({"type": "cmd", "id": "go", "cmd": "takeoff"})
                recv_until(ws2, "ack")
                _wait_state(ws1, lambda m: m["mode"] == "hovering")
            _events(ws1, "CONTROLLER_CHANGED")
            # 넘겨받은 앱이 ping을 안 보냄 → link_timeout 뒤 자동 착륙
            ev = _events(ws1, "LINK_LOST_LANDING", n=60)
            assert ev["level"] == "warn"
            _wait_state(ws1, lambda m: m["mode"] == "idle" and not m["flying"])


def test_all_apps_gone_lands_after_timeout(settings):
    s = _fast_link_settings(settings, timeout=0.6)
    b = MockBackend(s)
    with TestClient(create_app(s, b)) as client:
        with client.websocket_connect("/ws") as ws1:
            ws1.receive_json()
            with client.websocket_connect("/ws") as ws2:
                ws2.receive_json()
                ws2.send_json({"type": "cmd", "id": "go", "cmd": "takeoff"})
                recv_until(ws2, "ack")
                _wait_state(ws2, lambda m: m["mode"] == "hovering")
        # 둘 다 끊김
        for _ in range(60):
            if not b.flying:
                break
            time.sleep(0.1)
        assert not b.flying
        assert client.app.state.controller.mode == "idle"


def test_handover_goes_to_most_recent_remaining_app(settings):
    """앱 3개: 명령권 앱(ws3)이 끊기면 가장 최근 연결인 ws2가 넘겨받고, ws1은 여전히 BUSY."""
    s = _fast_link_settings(settings, timeout=5)
    with TestClient(create_app(s, MockBackend(s))) as client:
        with client.websocket_connect("/ws") as ws1:
            ws1.receive_json()
            with client.websocket_connect("/ws") as ws2:
                ws2.receive_json()
                with client.websocket_connect("/ws") as ws3:
                    ws3.receive_json()
                _events(ws2, "CONTROLLER_CHANGED")
                ws1.send_json({"type": "cmd", "id": "a", "cmd": "land"})
                assert recv_until(ws1, "ack")["error"] == "BUSY"  # ws1은 여전히 권한 없음
                ws2.send_json({"type": "cmd", "id": "b", "cmd": "land"})
                assert recv_until(ws2, "ack")["ok"] is True
