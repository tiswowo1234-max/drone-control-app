"""vision/ 연결 프로그램과의 연결: 위치 추적 모드(11장) + 실시간 영상 중계(8장, 10.2장).

  VisionLink  현재 모드, 연결 프로그램(ws /vision/video) 상태, 모드 전환
  VideoRelay  가장 최근 JPEG 한 장을 기억하고, 영상을 보는 앱(ws /ws/video)마다 따로 보낸다

안전 원칙 (docs/api.md 10.2): 영상은 비행 안전 기능과 별개다.
- 영상 앱마다 자기 코루틴에서 보낸다. 느린 앱은 자기 영상만 늦어지고(최신 장만 보냄)
  state 전송(/ws), ping 감시, 자동 착륙에는 영향이 없다.
- 프레임을 받는 일은 "최신 장 바꿔 끼우기" 한 번이라 이벤트 루프를 붙잡지 않는다.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any, Awaitable, Callable

from .backends.base import DroneBackend
from .settings import VISION_MODES, Settings, VideoCfg

log = logging.getLogger("drone_server.vision_link")

BRIDGE_SEND_TIMEOUT_S = 2.0    # 연결 프로그램에 vision_mode를 보낼 때
VIEWER_SEND_TIMEOUT_S = 5.0    # 영상 앱 한 장 보내기가 이보다 오래 걸리면 그 앱 연결을 닫는다
VIEWER_STATUS_CHECK_S = 0.5    # 새 장이 없어도 이 주기로 '영상 끊김'을 확인한다

EventSink = Callable[[dict], Awaitable[None]]


def _is_jpeg(data: bytes) -> bool:
    return len(data) >= 4 and data[:2] == b"\xff\xd8"


class VideoRelay:
    def __init__(self, cfg: VideoCfg, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self._clock = clock
        self.latest: bytes | None = None
        self.seq = 0                    # 받은 장 번호 (새 장마다 +1)
        self.last_at: float | None = None
        self.received = 0
        self.dropped = 0                # 너무 크거나 JPEG가 아니라 버린 장
        self.viewers = 0
        self._new = asyncio.Event()

    # ---------------------------------------------------------------- 입력
    def push(self, data: bytes) -> bool:
        """연결 프로그램이 보낸 한 장. 너무 크거나 JPEG가 아니면 버리고 False."""
        if len(data) > self.cfg.max_frame_kb * 1024 or not _is_jpeg(data):
            self.dropped += 1
            return False
        self.latest = data
        self.seq += 1
        self.received += 1
        self.last_at = self._clock()
        ev, self._new = self._new, asyncio.Event()
        ev.set()  # 기다리던 영상 앱들을 깨운다
        return True

    def age_s(self) -> float | None:
        return None if self.last_at is None else self._clock() - self.last_at

    def live(self) -> bool:
        age = self.age_s()
        return age is not None and age <= self.cfg.stale_s

    async def wait_new(self, seen_seq: int, timeout: float) -> None:
        if self.seq != seen_seq:
            return
        ev = self._new
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(ev.wait(), timeout)

    # ---------------------------------------------------------------- 출력 (앱 하나)
    @staticmethod
    def status_message(live: bool) -> dict:
        return {"type": "video_status", "live": live,
                "message": "카메라 영상 수신 중" if live else "카메라 영상이 없습니다"}

    async def serve_viewer(self, ws: Any) -> None:
        """영상 앱 하나에 보내기 (ws: send_text/send_bytes/receive를 가진 WebSocket).
        앱이 끊기거나 너무 느리면 돌아온다."""
        self.viewers += 1
        try:
            receiver = asyncio.create_task(self._drain_incoming(ws))
            sender = asyncio.create_task(self._send_loop(ws))
            done, pending = await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for t in done:
                if not t.cancelled() and t.exception() is not None:
                    log.info("video viewer closed: %r", t.exception())
        finally:
            self.viewers -= 1

    @staticmethod
    async def _drain_incoming(ws: Any) -> None:
        """앱은 보내는 메시지가 없다. 끊김을 알아채기 위해서만 읽는다."""
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return

    async def _send_loop(self, ws: Any) -> None:
        min_gap = 1.0 / self.cfg.max_fps
        live = self.live()
        await asyncio.wait_for(ws.send_text(json.dumps(self.status_message(live), ensure_ascii=False)),
                               VIEWER_SEND_TIMEOUT_S)
        seen = self.seq if not live else self.seq - 1  # 살아 있으면 지금 장을 바로 보낸다
        last_sent = 0.0
        while True:
            await self.wait_new(seen, VIEWER_STATUS_CHECK_S)
            now_live = self.live()
            if now_live != live:
                live = now_live
                await asyncio.wait_for(ws.send_text(json.dumps(self.status_message(live), ensure_ascii=False)),
                                       VIEWER_SEND_TIMEOUT_S)
            if self.seq == seen or self.latest is None or not live:
                continue
            wait = last_sent + min_gap - time.monotonic()
            if wait > 0:  # 초당 max_fps 제한. 기다리는 동안 들어온 장은 건너뛰고 최신 장만 보낸다
                await asyncio.sleep(wait)
            seen, frame = self.seq, self.latest
            await asyncio.wait_for(ws.send_bytes(frame), VIEWER_SEND_TIMEOUT_S)
            last_sent = time.monotonic()


class VisionLink:
    """위치 추적 모드와 연결 프로그램(하나) 상태."""

    def __init__(self, settings: Settings, backend: DroneBackend, emit: EventSink | None = None):
        self.s = settings
        self.b = backend
        self._emit = emit
        self.mode = settings.bridge.default_mode
        self.b.vision_mode = self.mode   # 드론이 연결되면 이 모드로 카메라를 맞춘다
        self.video = VideoRelay(settings.video)
        self.bridge: Any = None          # 연결 프로그램의 WebSocket
        self.bridge_mode: str | None = None
        self.camera_ok: bool | None = None
        self.bridge_message: str | None = None
        self.bridge_connected_at: float | None = None
        self._tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------------- 상태
    def state(self) -> dict:
        """state.vision (docs/api.md 3.2)."""
        return {"mode": self.mode, "bridge_connected": self.bridge is not None,
                "bridge_mode": self.bridge_mode, "camera_ok": self.camera_ok,
                "video_live": self.video.live()}

    def health(self) -> dict:
        age = self.video.age_s()
        return {**self.state(), "bridge_message": self.bridge_message,
                "video": {"last_frame_age_s": None if age is None else round(age, 2),
                          "viewers": self.video.viewers, "frames_received": self.video.received,
                          "frames_dropped": self.video.dropped, "stale_s": self.s.video.stale_s}}

    # ---------------------------------------------------------------- 모드 전환
    def set_mode(self, mode: str) -> None:
        """모드를 바꾼다 (검사는 컨트롤러가 이미 함). 오래 걸리는 일(연결 프로그램·드론에 알리기)은
        뒤에서 따로 실행한다 → 명령 처리(ack)와 stop 등 다른 명령을 붙잡지 않는다."""
        if mode not in VISION_MODES:
            raise ValueError(mode)
        changed = mode != self.mode
        self.mode = mode
        self.b.vision_mode = mode
        if changed and self.b.vision is not None:
            self.b.vision.clear()  # 이전 모드 위치를 버린다 → 새 값이 올 때까지 estimate
        log.info("vision mode → %s%s", mode, "" if changed else " (same)")
        self._spawn(self._notify_bridge(self.bridge))
        self._spawn(self._apply_backend(mode))

    def _spawn(self, coro: Awaitable[None]) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _apply_backend(self, mode: str) -> None:
        try:
            await self.b.apply_vision_mode(mode)
        except Exception as e:  # noqa: BLE001
            log.warning("apply vision mode %s failed: %s", mode, e)
            if self._emit:
                await self._emit({"type": "event", "level": "error", "code": "CMD_FAILED",
                                  "message": f"드론 카메라 전환({mode}) 실패: {e}"})

    async def _notify_bridge(self, ws: Any) -> None:
        if ws is None:
            return
        try:
            await asyncio.wait_for(ws.send_text(json.dumps({"type": "vision_mode", "mode": self.mode})),
                                   BRIDGE_SEND_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — 끊긴 연결은 수신 루프에서 정리
            log.info("vision_mode send failed: %r", e)

    # ---------------------------------------------------------------- 연결 프로그램
    async def serve_bridge(self, ws: Any) -> None:
        """ws /vision/video 한 연결 (accept 이후). 연결 프로그램은 하나만: 새 연결이 오면 이전 것을 닫는다."""
        old, self.bridge = self.bridge, ws
        self.bridge_mode = self.camera_ok = self.bridge_message = None
        self.bridge_connected_at = time.monotonic()
        if old is not None:
            log.info("new vision bridge connected → closing the previous one")
            self._spawn(self._close_quietly(old))
        log.info("vision bridge connected")
        await self._notify_bridge(ws)
        sender = asyncio.create_task(self._drone_state_loop(ws))
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is not None:
                    self.video.push(data)
                    continue
                text = msg.get("text")
                if text is not None and ws is self.bridge:
                    self._on_bridge_text(text)
        finally:
            sender.cancel()
            if self.bridge is ws:
                self.bridge = None
                self.bridge_mode = self.camera_ok = None
                self.bridge_message = "연결 프로그램 연결 끊김"
                log.info("vision bridge disconnected")

    async def _drone_state_loop(self, ws: Any, period_s: float = 0.1) -> None:
        """드론 자세·높이를 연결 프로그램에 보낸다 (docs/api.md 8장 drone_state).
        위치 계산이 기울기(중력 방향)와 거리 센서 높이를 쓴다. 느리면 건너뛴다(보내기 0.5초 제한)."""
        while True:
            att = self.b.attitude()
            if att is not None:
                msg = {"type": "drone_state", "t": round(time.time(), 3), **att}
                try:
                    await asyncio.wait_for(ws.send_text(json.dumps(msg)), 0.5)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — 끊긴 연결은 수신 루프에서 정리
                    return
            await asyncio.sleep(period_s)

    def _on_bridge_text(self, text: str) -> None:
        try:
            m = json.loads(text)
        except ValueError:
            log.info("bridge sent non-JSON text (ignored)")
            return
        if not isinstance(m, dict) or m.get("type") != "bridge_status":
            return
        mode = m.get("mode")
        self.bridge_mode = mode if mode in VISION_MODES else None
        cam = m.get("camera_ok")
        self.camera_ok = cam if isinstance(cam, bool) else None
        msg = m.get("message")
        self.bridge_message = msg if isinstance(msg, str) else None
        log.info("bridge_status mode=%s camera_ok=%s %s", self.bridge_mode, self.camera_ok, self.bridge_message or "")

    @staticmethod
    async def _close_quietly(ws: Any) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.close(code=1000, reason="replaced by a new connection"), 2.0)

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=1.0)
