"""명령 처리·안전 검사·비행 상태(mode) 관리.

- 명령(cmd)을 받으면 검사 후 바로 ack를 돌려주고, 실제 비행은 '미션' 작업(asyncio Task)으로 실행한다.
- 새 명령이 오면 진행 중 미션을 취소한다. stop/land/emergency는 언제든 우선 처리된다.
- 감시 루프: 앱 ping 끊김 → 자동 착륙, 배터리 경고, 드론 연결 변화 알림.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from dataclasses import replace
from typing import Any, Awaitable, Callable

from .backends.base import DroneBackend
from .safety import SafetyError, check_battery, check_target, clamp_alt
from .settings import VISION_MODES, FollowCfg, Settings
from .tags import load_tags, nearest_tag

log = logging.getLogger("drone_server.controller")

COMMANDS = ("takeoff", "goto", "stop", "land", "return_home", "emergency", "set_vision_mode", "follow_start")
FLIGHT_MODES = ("taking_off", "hovering", "moving", "returning", "landing", "following")

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


def follow_velocity(fwd_cm: float, right_cm: float, rel_yaw_deg: float, f: FollowCfg) -> tuple[float, float]:
    """UGV 따라가기 속도 (드론 기준 앞·오른쪽 cm/s).

    fwd_cm, right_cm = 드론 하단 카메라가 본 차 태그의 드론 기준 위치, rel_yaw_deg = 차 앞쪽 - 드론 앞쪽 (시계 +).
    목표 = 차 뒤 distance_cm 지점. 거기까지의 거리에 비례한 속도(gain_per_s), 상한 max_speed_cm_s,
    deadband_cm 안이면 0 (작은 흔들림에 드론이 떨지 않게)."""
    th = math.radians(rel_yaw_deg)
    ef = fwd_cm - f.distance_cm * math.cos(th)     # 차 앞쪽 = 드론 기준 (cos θ, sin θ)
    er = right_cm - f.distance_cm * math.sin(th)
    if math.hypot(ef, er) < f.deadband_cm:
        return 0.0, 0.0
    vf, vr = ef * f.gain_per_s, er * f.gain_per_s
    n = math.hypot(vf, vr)
    if n > f.max_speed_cm_s:
        vf, vr = vf * f.max_speed_cm_s / n, vr * f.max_speed_cm_s / n
    return vf, vr


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
        # UGV 따라가기: vision이 보낸 '드론 기준 차 위치' 최신 값 (POST /vision/ugv_rel)
        self.ugv_rel: tuple[float, float, float] | None = None   # (앞 cm, 오른쪽 cm, 차 방향 - 드론 방향 °)
        self.ugv_rel_at: float | None = None

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

    def on_ugv_relative(self, fwd_cm: float, right_cm: float, rel_yaw_deg: float) -> None:
        """드론 하단 카메라가 차 태그를 봄 (드론 기준 상대 위치)."""
        self.ugv_rel, self.ugv_rel_at = (fwd_cm, right_cm, rel_yaw_deg), time.monotonic()

    def ugv_rel_age(self) -> float | None:
        return None if self.ugv_rel_at is None else time.monotonic() - self.ugv_rel_at

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

    def _edge_guard(self, vf: float, vr: float) -> tuple[float, float, bool]:
        """지금 속도로 edge_lookahead_s 뒤 위치가 지도 경계·반경·금지 구역 밖이면 속도 0. (속도, 막았는지)
        이미 밖에 있으면 이륙 지점 쪽으로 가까워지는 움직임만 허용한다."""
        if vf == 0.0 and vr == 0.0:
            return vf, vr, False
        p = self.b.telemetry().pos
        if p is None:
            return vf, vr, False
        la = self.s.follow.edge_lookahead_s
        r = math.radians(p.yaw_deg)
        nx = p.x_cm + (vf * math.sin(r) + vr * math.cos(r)) * la
        ny = p.y_cm + (vf * math.cos(r) - vr * math.sin(r)) * la
        z = self.s.limits.default_alt_cm
        try:
            check_target(self.s, nx, ny, z, (p.x_cm, p.y_cm))
            return vf, vr, False
        except SafetyError:
            pass
        try:
            check_target(self.s, p.x_cm, p.y_cm, z)
        except SafetyError:   # 이미 밖: 안쪽(이륙 지점 쪽)으로 가는 것만
            hx, hy = self.s.home_x_cm, self.s.home_y_cm
            if math.hypot(nx - hx, ny - hy) < math.hypot(p.x_cm - hx, p.y_cm - hy):
                return vf, vr, False
        return 0.0, 0.0, True

    async def _m_follow(self) -> None:
        """UGV 따라가기 (rc 속도 명령). stop·land·emergency·앱 끊김 착륙은 이 미션을 취소해 우선 처리된다.

        차가 보이면(lost_s 안) 차 뒤 distance_cm 지점으로 간다. 잠깐 놓치면(coast_s까지) **마지막으로 본 차 위치 쪽으로**
        천천히 간다 — 차는 보통 화면 가장자리로 빠져나가며 사라지므로 그쪽으로 가면 다시 보인다.
        놓친 뒤 드론이 움직인 거리는 보낸 속도로 계산해 뺀다 (기수는 고정이라 드론 기준 좌표가 그대로 유지된다)."""
        f = self.s.follow
        self.mode, self.target = "following", None
        edge_warned = False
        seen_at = None                 # 마지막으로 쓴 관측 시각
        moved = [0.0, 0.0]             # 그 관측 뒤 드론이 움직인 거리 (드론 기준 앞·오른쪽 cm)
        vf = vr = 0.0
        t_prev = time.monotonic()
        await self.event("info", "FOLLOW_START", f"차 따라가기를 시작합니다 (차 뒤 {f.distance_cm:g}cm)")
        try:
            while True:
                now = time.monotonic()
                moved[0] += vf * (now - t_prev)
                moved[1] += vr * (now - t_prev)
                t_prev = now
                age = self.ugv_rel_age()
                if self.ugv_rel_at != seen_at:
                    seen_at, moved = self.ugv_rel_at, [0.0, 0.0]
                if age is not None and age <= f.lost_s and self.ugv_rel is not None:
                    fwd, right, rel = self.ugv_rel
                    vf, vr = follow_velocity(fwd - moved[0], right - moved[1], rel, f)
                elif age is not None and age <= f.coast_s and self.ugv_rel is not None:
                    # 잠깐 놓침: 마지막으로 본 차 위치(차 뒤가 아니라 차 자체) 쪽으로 천천히
                    seek = replace(f, distance_cm=0.0, max_speed_cm_s=f.max_speed_cm_s * f.coast_scale)
                    vf, vr = follow_velocity(self.ugv_rel[0] - moved[0], self.ugv_rel[1] - moved[1], 0.0, seek)
                else:
                    log.info("따라가기: 차를 %.1f초 동안 못 봐서 멈춥니다", age if age is not None else -1)
                    await self.event("warn", "UGV_LOST",
                                     f"차를 {f.coast_s:g}초 동안 못 봐서 따라가기를 멈추고 제자리에 떠 있습니다")
                    vf = vr = 0.0
                    break
                vf, vr, blocked = self._edge_guard(vf, vr)
                if blocked and not edge_warned:
                    await self.event("warn", "FOLLOW_EDGE", "지도 경계·비행 반경에 닿아 그 방향으로는 따라가지 않습니다")
                edge_warned = blocked
                await self.b.set_velocity(vf, vr, 0.0, 0.0)
                await asyncio.sleep(f.period_s)
        finally:
            with contextlib.suppress(Exception):   # 취소(stop·착륙)·실패 때도 반드시 멈춘다
                await self.b.set_velocity(0.0, 0.0, 0.0, 0.0)
        self.mode = "hovering"

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

    async def _c_follow_start(self, msg: dict) -> None:
        """UGV 따라가기 시작 (v0.8). 비행(호버링) 중이고, 드론이 차 태그를 막 봤을 때만. 멈춤은 stop."""
        f = self.s.follow
        if not f.enabled:
            raise SafetyError("BAD_REQUEST", "따라가기 기능이 꺼져 있습니다 ([follow] enabled)")
        t = self._require_connected()
        self._require_not_landing(t)
        if self.mode == "taking_off":
            raise SafetyError("BUSY", "이륙 중입니다. 잠시 뒤 다시 보내 주세요")
        if not t.flying:
            raise SafetyError("NOT_FLYING", "먼저 이륙해서 드론을 차 위로 보내 주세요")
        if self.mode == "following":
            return   # 이미 따라가는 중
        age = self.ugv_rel_age()
        if age is None or age > f.start_max_age_s:
            raise SafetyError("UGV_NOT_SEEN", "드론 카메라에 차가 보이지 않습니다. 드론을 차 위로 보낸 뒤 다시 누르세요")
        check_battery(self.s, t.battery_pct)
        self.note_alive()
        await self._cancel_mission()
        self.mode, self.target = "following", None
        self._start(self._m_follow())

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
