"""명령 처리·안전 검사·비행 상태(mode) 관리.

- 명령(cmd)을 받으면 검사 후 바로 ack를 돌려주고, 실제 비행은 '미션' 작업(asyncio Task)으로 실행한다.
- 새 명령이 오면 진행 중 미션을 취소한다. stop/land/emergency는 언제든 우선 처리된다.
- 감시 루프: 앱 ping 끊김 → 자동 착륙, 배터리 경고, 드론 연결 변화 알림.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Awaitable, Callable

from .backends.base import DroneBackend
from .safety import SafetyError, check_battery, check_target, clamp_alt
from .settings import VISION_MODES, Settings
from .tags import load_tags, nearest_tag

log = logging.getLogger("drone_server.controller")

COMMANDS = ("takeoff", "goto", "stop", "land", "return_home", "emergency", "set_vision_mode")
FLIGHT_MODES = ("taking_off", "hovering", "moving", "returning", "landing")

EventSink = Callable[[dict], Awaitable[None]]


def make_ack(cmd_id: Any, error: str | None = None, message: str | None = None) -> dict:
    ack: dict[str, Any] = {"type": "ack", "id": cmd_id, "ok": error is None}
    if error is not None:
        ack["error"] = error
        ack["message"] = message or error
    return ack


def make_event(level: str, code: str, message: str) -> dict:
    return {"type": "event", "level": level, "code": code, "message": message}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


class Controller:
    def __init__(self, settings: Settings, backend: DroneBackend, emit: EventSink | None = None,
                 vision_link: Any = None):
        self.s = settings
        self.b = backend
        self._emit = emit
        self.vision_link = vision_link  # VisionLink (위치 추적 모드, docs/api.md 11장). None이면 state.vision 없음
        self.mode = "idle"
        self.target: tuple[float, float] | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.last_alive = time.monotonic()
        self._low_batt_warned = False
        self._was_connected: bool | None = None
        # 초기 정렬 (2026-11-25): 연결 후 첫 이륙 때 가장 가까운 태그 위에서 위치·방향을 확정한다
        self.aligned = False
        self._tags = load_tags(settings) if settings.align.enabled else []

    # ------------------------------------------------------------------ 출력
    async def event(self, level: str, code: str, message: str) -> None:
        log.info("event %s %s %s", level, code, message)
        if self._emit:
            await self._emit(make_event(level, code, message))

    def state_message(self) -> dict:
        t = self.b.telemetry()
        pos = t.pos.to_json() if t.pos else None
        if pos is not None and t.vision_z_cm is not None:
            pos["z_cm"] = round(t.vision_z_cm, 1)  # 카메라 추정 높이는 표시에만 (7.2 z_m)
        msg = {
            "type": "state",
            "ts": round(time.time(), 3),
            "connected": t.connected,
            "flying": t.flying,
            "mode": self.mode,
            "pos": pos,
            "pos_source": t.pos_source,
            "target": {"x_cm": round(self.target[0], 1), "y_cm": round(self.target[1], 1)} if self.target else None,
            "battery_pct": None if t.battery_pct is None else int(round(t.battery_pct)),
        }
        if self.vision_link is not None:
            msg["vision"] = self.vision_link.state()
        return msg

    def _default_z(self, t) -> float:
        """z_cm을 생략한 goto·귀환의 고도 (docs/api.md 4.1).
        fixed_alt = true → 항상 default_alt_cm (v0.7, 시연 전 고도 고정)
        false → 비행 중이면 현재 고도를 min~max로 맞춘 값, 착지 상태면 default_alt_cm"""
        if self.s.limits.fixed_alt or not (t.flying and t.pos):
            return self.s.limits.default_alt_cm
        return clamp_alt(self.s, t.pos.z_cm)

    def note_alive(self) -> None:
        """제어 앱의 ping(또는 명령)을 받았을 때 호출."""
        self.last_alive = time.monotonic()

    # ------------------------------------------------------------------ 미션
    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _cancel_mission(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001 — 취소/실패 모두 여기서 정리
                pass

    def _start(self, coro: Awaitable[None]) -> None:
        self._task = asyncio.create_task(self._run(coro))

    async def _run(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("mission failed")
            flying = self.b.telemetry().flying
            self.mode = "hovering" if flying else "idle"
            self.target = None
            await self.event("error", "CMD_FAILED", f"명령 실행 실패: {e}")

    async def _m_takeoff(self) -> None:
        self.mode = "taking_off"
        await self.b.takeoff()
        await self._align(self.s.limits.default_alt_cm)
        p = self.b.telemetry().pos
        if p is not None:
            await self.b.move_to(p.x_cm, p.y_cm, self.s.limits.default_alt_cm)
        self.mode = "hovering"

    async def _m_goto(self, x: float, y: float, z: float) -> None:
        self.target = (x, y)
        if not self.b.telemetry().flying:
            self.mode = "taking_off"
            await self.b.takeoff()
            await self._align(z)
        self.mode = "moving"
        sent = await self.b.move_to(x, y, z)
        self.mode = "hovering"
        self.target = None
        if sent == 0:
            await self.event("info", "TARGET_TOO_CLOSE",
                             "목표가 현재 위치에서 20cm 이내라 이동하지 않았습니다 (드론 최소 이동 거리)")

    async def _m_land(self) -> None:
        self.mode = "landing"
        self.target = None
        await self.b.land()
        self.mode = "idle"

    async def _m_return_home(self, z: float) -> None:
        self.mode = "returning"
        self.target = (self.s.home_x_cm, self.s.home_y_cm)
        await self.b.move_to(self.s.home_x_cm, self.s.home_y_cm, z)
        await self._m_land()

    async def _align(self, z: float) -> None:
        """초기 정렬: 이륙 직후 가장 가까운 AprilTag 위로 가서, 카메라 위치(와 real이면 방향 보정값)가
        충분히 모일 때까지 호버링한다. 성공하면 다시 연결될 때까지 하지 않는다. 실패해도 비행은 계속한다.

        왜: 서버는 '드론을 home에 지도 위쪽을 보게 놓고 켰다'고 가정하고 시작한다. 놓은 위치·방향이 조금만 틀려도
        첫 이동이 어긋나므로(2026-11-25 실물: 지나침), 출동 전에 태그 바로 위(왜곡이 가장 작은 화면 가운데)에서
        위치와 방향을 확정한다. mode는 'taking_off'로 둔다(앱에는 '이륙 중', 그동안 goto는 BUSY)."""
        c = self.s.align
        v = self.b.vision
        if self.aligned or not c.enabled or v is None or not self._tags:
            return
        t_end = time.monotonic() + c.wait_fix_s
        while not v.fresh() and time.monotonic() < t_end:   # 이륙 직후 첫 카메라 위치를 잠깐 기다린다
            await asyncio.sleep(0.1)
        p = self.b.telemetry().pos
        if p is None:
            await self.event("warn", "ALIGN_SKIPPED", "현재 위치를 몰라 초기 정렬을 건너뜁니다")
            return
        tag, dist = nearest_tag(self._tags, p.x_cm, p.y_cm)
        if dist > c.max_tag_dist_cm:
            await self.event("warn", "ALIGN_SKIPPED",
                             f"가장 가까운 태그(ID {tag.id})가 {dist:.0f}cm 떨어져 있어 초기 정렬을 건너뜁니다")
            return
        await self.event("info", "ALIGN_START", f"초기 정렬: 태그 ID {tag.id} 위로 가서 위치·방향을 맞춥니다")
        log.info("초기 정렬: 현재 (%.1f, %.1f) cm [%s] → 태그 ID %d (%.1f, %.1f) cm, 거리 %.1f cm",
                 p.x_cm, p.y_cm, self.b.telemetry().pos_source, tag.id, tag.x_cm, tag.y_cm, dist)
        prev_target, self.target = self.target, (tag.x_cm, tag.y_cm)
        try:
            await self.b.move_to(tag.x_cm, tag.y_cm, z)
            seq0 = v.seq
            t_end = time.monotonic() + c.timeout_s
            n, heading = 0, None
            while time.monotonic() < t_end:
                n = v.fixes_since(seq0, c.min_confidence)
                heading = self.b.heading_ready()
                if n >= c.min_fixes and (not c.require_heading or heading is not False):
                    self.aligned = True
                    p = self.b.telemetry().pos
                    off = getattr(self.b, "heading_offset_deg", None)
                    note = "" if off is None else f", 방향 보정 {off:+.0f}°"
                    where = "" if p is None else f" 위치 ({p.x_cm:.0f}, {p.y_cm:.0f}) cm"
                    await self.event("info", "ALIGN_DONE", f"초기 정렬 완료: 태그 ID {tag.id}{where}{note}")
                    return
                await asyncio.sleep(0.1)
            why = f"카메라 위치 {n}/{c.min_fixes}개" + (", 방향 미확정" if heading is False else "")
            await self.event("warn", "ALIGN_FAILED",
                             f"초기 정렬 실패: 태그 ID {tag.id} 위에서 {c.timeout_s:g}초 동안 {why} — 추정 위치로 계속합니다")
        finally:
            self.target = prev_target

    async def _m_hover(self) -> None:
        await self.b.hover()
        self.mode = "hovering"

    # ------------------------------------------------------------------ 명령
    async def handle_cmd(self, msg: dict) -> dict:
        cmd_id = msg.get("id")
        if not isinstance(cmd_id, (str, int)) or isinstance(cmd_id, bool) or cmd_id == "":
            return make_ack(cmd_id, "BAD_REQUEST", "id(문자열)가 필요합니다")
        cmd = msg.get("cmd")
        if cmd not in COMMANDS:
            return make_ack(cmd_id, "BAD_REQUEST", f"알 수 없는 명령입니다: {cmd!r}")
        async with self._lock:
            try:
                await getattr(self, f"_c_{cmd}")(msg)
            except SafetyError as e:
                log.info("cmd %s rejected: %s", cmd, e.code)
                return make_ack(cmd_id, e.code, e.message)
        log.info("cmd %s accepted", cmd)
        return make_ack(cmd_id)

    def _require_connected(self):
        t = self.b.telemetry()
        if not t.connected:
            raise SafetyError("NOT_CONNECTED", "드론과 연결되지 않았습니다")
        return t

    def _require_not_landing(self, t) -> None:
        if self.mode == "landing":
            raise SafetyError("BUSY", "착륙 중에는 처리할 수 없습니다")
        if self.mode == "emergency" and t.flying:
            raise SafetyError("BUSY", "비상 정지 중입니다")

    async def _c_takeoff(self, msg: dict) -> None:
        t = self._require_connected()
        self._require_not_landing(t)
        if self.mode == "taking_off":
            return  # 이미 이륙 중
        if t.flying:
            return  # 이미 비행 중 → 할 일 없음
        check_battery(self.s, t.battery_pct)
        self.note_alive()
        await self._cancel_mission()
        self.mode, self.target = "taking_off", None
        self._start(self._m_takeoff())

    async def _c_goto(self, msg: dict) -> None:
        x, y = _num(msg.get("x_cm")), _num(msg.get("y_cm"))
        if x is None or y is None:
            raise SafetyError("BAD_REQUEST", "x_cm, y_cm(숫자)가 필요합니다")
        z_raw = msg.get("z_cm")
        if z_raw is None:
            z = None
        else:
            z = _num(z_raw)
            if z is None:
                raise SafetyError("BAD_REQUEST", "z_cm은 숫자여야 합니다")
        t = self._require_connected()
        if z is None:
            z = self._default_z(t)
        from_xy = (t.pos.x_cm, t.pos.y_cm) if t.pos else None
        check_target(self.s, x, y, z, from_xy)
        self._require_not_landing(t)
        if self.mode == "taking_off":
            raise SafetyError("BUSY", "이륙 중입니다. 잠시 뒤 다시 보내 주세요")
        check_battery(self.s, t.battery_pct)
        self.note_alive()
        await self._cancel_mission()
        log.info("goto 목표 (%.1f, %.1f, %.1f) cm, 현재 %s", x, y, z,
                 f"({t.pos.x_cm:.1f}, {t.pos.y_cm:.1f}, {t.pos.z_cm:.1f}) cm [{t.pos_source}]" if t.pos else "모름")
        self.target = (x, y)
        self.mode = "moving" if t.flying else "taking_off"
        self._start(self._m_goto(x, y, z))

    async def _c_stop(self, msg: dict) -> None:
        t = self._require_connected()
        if self.mode == "landing":
            raise SafetyError("BUSY", "착륙 중에는 멈출 수 없습니다")
        await self._cancel_mission()
        self.target = None
        if t.flying and self.mode != "emergency":
            self.mode = "hovering"
            self._start(self._m_hover())

    async def _c_land(self, msg: dict) -> None:
        t = self._require_connected()
        if self.mode in ("landing", "emergency") or not t.flying:
            return
        await self._cancel_mission()
        self.mode, self.target = "landing", None
        self._start(self._m_land())

    async def _c_return_home(self, msg: dict) -> None:
        t = self._require_connected()
        self._require_not_landing(t)
        if not t.flying:
            await self.event("info", "ALREADY_LANDED", "착지 상태라 귀환할 필요가 없습니다")
            return
        if self.mode == "taking_off":
            raise SafetyError("BUSY", "이륙 중입니다. 잠시 뒤 다시 보내 주세요")
        z = self._default_z(t)
        await self._cancel_mission()
        self.mode, self.target = "returning", (self.s.home_x_cm, self.s.home_y_cm)
        self._start(self._m_return_home(z))

    async def _c_emergency(self, msg: dict) -> None:
        if msg.get("confirm") is not True:
            raise SafetyError("NOT_CONFIRMED", "비상 정지는 confirm: true가 필요합니다")
        self._require_connected()
        # 진행 중 미션 취소를 기다리지 않고 모터부터 끈다
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
        await self.b.emergency()
        self.mode = "emergency"
        self.target = None
        await self.event("warn", "EMERGENCY_STOP", "비상 정지: 모터를 껐습니다")

    async def _c_set_vision_mode(self, msg: dict) -> None:
        """위치 추적 카메라 전환 (docs/api.md 11장). 착지(idle)·호버링 중에만.
        비상 정지 뒤 드론이 바닥에 있을 때(emergency + 착지)도 안전하므로 받는다."""
        mode = msg.get("mode")
        if mode not in VISION_MODES:
            raise SafetyError("BAD_REQUEST", f"mode는 {' | '.join(VISION_MODES)} 중 하나여야 합니다")
        if self.vision_link is None:
            raise SafetyError("BAD_REQUEST", "이 서버는 위치 추적 모드 전환을 지원하지 않습니다")
        t = self.b.telemetry()
        settled = self.mode in ("idle", "hovering") or (self.mode == "emergency" and not t.flying)
        if not settled:
            raise SafetyError("BUSY", "착지 상태나 호버링 중에만 카메라를 바꿀 수 있습니다")
        self.vision_link.set_mode(mode)

    # ------------------------------------------------------------------ 감시
    async def watchdog_once(self) -> None:
        t = self.b.telemetry()
        if self._was_connected is not None and t.connected != self._was_connected:
            if t.connected:
                self.aligned = False  # 다시 연결되면 TT 방향 센서 0°가 바뀌므로 다시 정렬한다
                await self.event("info", "DRONE_CONNECTED", "드론과 연결되었습니다")
            else:
                await self.event("error", "DRONE_DISCONNECTED", "드론과 연결이 끊겼습니다")
        self._was_connected = t.connected
        if not t.connected:
            return

        silent = time.monotonic() - self.last_alive
        if t.flying and self.mode not in ("landing", "emergency") and silent > self.s.limits.link_timeout_s:
            await self.event("warn", "LINK_LOST_LANDING",
                             f"앱 연결이 {self.s.limits.link_timeout_s:g}초 이상 끊겨 자동 착륙합니다")
            async with self._lock:
                await self._cancel_mission()
                self.mode, self.target = "landing", None
                self._start(self._m_land())

        if t.flying and t.battery_pct is not None and t.battery_pct < self.s.limits.min_battery_pct:
            if not self._low_batt_warned:
                self._low_batt_warned = True
                await self.event("warn", "LOW_BATTERY",
                                 f"배터리 {t.battery_pct:.0f}%: 귀환 또는 착륙하세요")
        elif not t.flying:
            self._low_batt_warned = False

        # 드론이 스스로 내려앉은 경우 (실제 TT: 배터리 부족·15초 무명령·충돌 등 — 백엔드가 높이로 판단)
        if getattr(self.b, "self_landed", False):
            self.b.self_landed = False
            if self.mode not in ("landing", "emergency", "idle"):
                async with self._lock:
                    await self._cancel_mission()
                    self.mode, self.target = "idle", None
                await self.event("warn", "DRONE_LANDED_ITSELF",
                                 "드론이 스스로 착륙했습니다 (배터리 부족·명령 끊김·충돌 등). 상태를 확인하세요")
        if not t.flying and not self.busy and self.mode in FLIGHT_MODES:
            self.mode = "idle"
            self.target = None

    async def watchdog_loop(self, period_s: float = 0.2) -> None:
        while True:
            try:
                await self.watchdog_once()
            except Exception:  # noqa: BLE001
                log.exception("watchdog error")
            await asyncio.sleep(period_s)

    async def shutdown(self) -> None:
        await self._cancel_mission()
