"""종료 경로: Ctrl+C(should_exit) 후 몇 초 안에 끝나는지, WinError 10054 로그 필터.

실제 Ctrl+C(콘솔 CTRL_C_EVENT)는 여기서 보내지 않는다. uvicorn의 Ctrl+C 처리기(handle_exit)가 하는 일인
should_exit = True와 같은 경로를 시험한다.
"""
import asyncio
import json
import logging
import socket
import sys
import threading
import time

import websockets

from drone_server.app import create_app
from drone_server.backends.mock import MockBackend
from drone_server.serve import ConnectionResetNoiseFilter, DroneServer, make_server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _LeakedTransport:
    """Windows에서 WinError 10054 때문에 asyncio 서버에서 떼어지지 못한 연결을 흉내 낸다."""
    def close(self):
        pass

    def abort(self):
        pass


def start_server(settings, leak: bool):
    port = free_port()
    srv = make_server(create_app(settings, MockBackend(settings)), "127.0.0.1", port)
    th = threading.Thread(target=srv.run, daemon=True)  # 메인 스레드가 아니라 신호 처리기는 안 걸린다
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started
    if leak:
        # 실제 버그 재현: _call_connection_lost()가 예외로 끝나 server._detach()가 안 불린 상태
        srv._leaked = _LeakedTransport()  # _clients는 WeakSet이라 강한 참조를 들고 있어야 한다
        srv.servers[0]._attach(srv._leaked)
    return srv, th, port


async def _connect_and_fly(port):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/ws")
    await ws.send(json.dumps({"type": "cmd", "id": "1", "cmd": "goto", "x_cm": 60, "y_cm": 200}))
    await asyncio.sleep(0.5)
    return ws


def test_shutdown_finishes_quickly_with_leaked_connection_and_flying_client(settings):
    srv, th, port = start_server(settings, leak=True)
    loop = asyncio.new_event_loop()
    ws = loop.run_until_complete(_connect_and_fly(port))
    t0 = time.time()
    srv.should_exit = True  # Ctrl+C와 같은 효과
    th.join(timeout=10)
    dt = time.time() - t0
    loop.run_until_complete(ws.close())
    loop.close()
    assert not th.is_alive(), "서버가 종료되지 않음"
    assert dt < 4.0, f"종료에 {dt:.1f}s 걸림"


def test_without_timeout_the_leak_would_hang(settings):
    """원인 확인: 종료 대기 시간 제한이 없으면(uvicorn 기본값) 새는 연결 하나로 영원히 기다린다."""
    port = free_port()
    srv = make_server(create_app(settings, MockBackend(settings)), "127.0.0.1", port,
                      timeout_graceful_shutdown=None)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    leaked = _LeakedTransport()
    srv.servers[0]._attach(leaked)
    srv.should_exit = True
    th.join(timeout=3)
    hung = th.is_alive()
    srv.force_exit = True  # 정리 (두 번째 Ctrl+C와 같음)
    th.join(timeout=5)
    if sys.version_info >= (3, 12):
        assert hung, "Python 3.12+에서는 시간 제한 없이 멈춰야 원인 설명이 맞다"


def test_vision_mode_shutdown(settings):
    from dataclasses import replace
    s = replace(settings, mock=replace(settings.mock, pos_source="vision"))
    srv, th, port = start_server(s, leak=False)
    t0 = time.time()
    srv.should_exit = True
    th.join(timeout=10)
    assert not th.is_alive() and time.time() - t0 < 4.0


def test_first_ctrl_c_arms_hard_exit_timer(monkeypatch, settings):
    srv = make_server(create_app(settings, MockBackend(settings)), "127.0.0.1", free_port())
    fired = threading.Event()
    monkeypatch.setattr(DroneServer, "_force_exit", lambda self: fired.set())
    srv.hard_exit_after_s = 0.2
    srv.handle_exit(2, None)  # SIGINT
    assert srv.should_exit and srv._hard_timer is not None and srv._hard_timer.daemon
    assert fired.wait(2), "강제 종료 타이머가 동작하지 않음"
    srv.handle_exit(2, None)  # 두 번째 Ctrl+C → uvicorn force_exit
    assert srv.force_exit


def _record(msg, exc):
    return logging.LogRecord("asyncio", logging.ERROR, __file__, 1, msg, None, (type(exc), exc, None))


def test_connection_reset_noise_filtered_only():
    f = ConnectionResetNoiseFilter()
    noise = ConnectionResetError(10054, "현재 연결은 원격 호스트에 의해 강제로 끊겼습니다")
    msg = "Exception in callback _ProactorBasePipeTransport._call_connection_lost()"
    assert f.filter(_record(msg, noise)) is False
    assert f.filter(_record("Exception in callback something_else()", noise)) is True  # 다른 콜백은 그대로
    assert f.filter(_record(msg, RuntimeError("boom"))) is True                         # 다른 예외는 그대로
    plain = logging.LogRecord("asyncio", logging.INFO, __file__, 1, "hello", None, None)
    assert f.filter(plain) is True
