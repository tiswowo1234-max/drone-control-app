"""uvicorn 실행 래퍼 — Ctrl+C로 확실히 꺼지게, Windows 연결 끊김 소음은 숨기게.

문제 (2026-11-11 사용자 PC, Windows + Python 3.14):
- 앱(브라우저)이 갑자기 끊기면 Windows Proactor 이벤트 루프의 _call_connection_lost()에서
  socket.shutdown()이 WinError 10054(ConnectionResetError)를 낸다. 이때 표준 라이브러리 코드가
  예외 때문에 뒷정리(sock.close(), server._detach())를 건너뛰어, asyncio 서버가 '아직 열린 연결 1개'로 착각한다.
- Python 3.12+의 Server.wait_closed()는 모든 연결이 닫힐 때까지 기다리고, uvicorn 기본값은 종료 대기
  시간 제한이 없다(timeout_graceful_shutdown=None) → Ctrl+C를 눌러도 영원히 기다리며 안 꺼진다.
해결:
1. timeout_graceful_shutdown = GRACEFUL_TIMEOUT_S: 그 시간 안에 안 닫히는 연결은 버리고 종료를 이어 간다.
2. 첫 Ctrl+C 후 HARD_EXIT_AFTER_S가 지나도 살아 있으면 프로세스를 강제 종료한다 (최후 안전장치).
3. 위 WinError 10054 로그 한 종류만 걸러 낸다 (다른 예외는 그대로 보인다).
"""
from __future__ import annotations

import logging
import os
import sys
import threading

import uvicorn

GRACEFUL_TIMEOUT_S = 2.0   # 열린 연결·작업을 기다리는 최대 시간
HARD_EXIT_AFTER_S = 4.0    # 첫 Ctrl+C 뒤 이 시간이 지나도 안 꺼지면 강제 종료

log = logging.getLogger("drone_server.serve")


class ConnectionResetNoiseFilter(logging.Filter):
    """asyncio가 내는 'Exception in callback _ProactorBasePipeTransport._call_connection_lost()' +
    ConnectionResetError(WinError 10054) 로그만 버린다. 상대가 연결을 끊었다는 뜻이라 무해하다."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, ConnectionResetError) and "_call_connection_lost" in record.getMessage():
            log.debug("ignored connection reset noise: %s", exc)
            return False
        return True


def install_log_filters() -> None:
    logging.getLogger("asyncio").addFilter(ConnectionResetNoiseFilter())


class DroneServer(uvicorn.Server):
    """Ctrl+C(또는 종료 신호)를 받으면 정해진 시간 안에 반드시 끝나는 uvicorn 서버."""

    hard_exit_after_s = HARD_EXIT_AFTER_S

    def __init__(self, config: uvicorn.Config):
        super().__init__(config)
        self._hard_timer: threading.Timer | None = None

    def handle_exit(self, sig, frame) -> None:  # noqa: ANN001 — uvicorn 시그니처
        first = not self.should_exit
        super().handle_exit(sig, frame)
        if first:
            print("\n서버를 끄는 중입니다... (몇 초 안에 꺼집니다)", flush=True)
            self.start_hard_exit_timer()

    def start_hard_exit_timer(self) -> None:
        if self._hard_timer is None:
            self._hard_timer = threading.Timer(self.hard_exit_after_s, self._force_exit)
            self._hard_timer.daemon = True
            self._hard_timer.start()

    def _force_exit(self) -> None:
        print(f"\n{self.hard_exit_after_s:g}초 안에 정상 종료되지 않아 강제로 끕니다.", flush=True)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(0)


def make_server(app, host: str, port: int, **kw) -> DroneServer:
    config = uvicorn.Config(app, host=host, port=port, log_level=kw.pop("log_level", "warning"),
                            timeout_graceful_shutdown=kw.pop("timeout_graceful_shutdown", GRACEFUL_TIMEOUT_S), **kw)
    return DroneServer(config)
