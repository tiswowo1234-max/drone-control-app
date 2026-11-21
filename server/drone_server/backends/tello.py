"""djitellopy(Tello) 기반 공통 백엔드 — sim(가짜 djitellopy)과 real(진짜 djitellopy)이 함께 쓴다.

핵심:
- TT는 GPS가 없으므로, 목표 지도 좌표를 '현재 위치(PositionProvider) 기준 상대 이동'(go x y z speed)으로 바꾼다.
- 한 번에 segment_cm 이하로 끊어서 보낸다 → stop/land가 최대 한 구간(약 1초) 안에 먹힌다.
- 비행 명령은 스레드 1개(executor)에서 차례로 실행한다. 상태 읽기는 별도 스레드.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..models import BackendError, Pose, Telemetry
from ..positioning import DroneFrame, PositionProvider, VisionProvider
from ..settings import TelloCfg
from .base import DroneBackend

log = logging.getLogger("drone_server.tello")

MIN_GO_CM = 20        # SDK: go x y z가 모두 -20~20이면 거부된다
MAX_GO_CM = 500       # SDK: go 한 번 최대 거리
POLL_PERIOD_S = 0.1
STALE_AFTER_S = 2.0   # 이 시간 동안 상태를 못 읽으면 연결 끊김으로 본다


def world_to_body(wx: float, wy: float, yaw_deg: float) -> tuple[float, float]:
    """기체 세계 변위 → 기체 기준 (전진, 오른쪽). yaw: 세계 +y가 0, 시계방향 +."""
    r = math.radians(yaw_deg)
    return wx * math.sin(r) + wy * math.cos(r), wx * math.cos(r) - wy * math.sin(r)


def body_to_world(fwd: float, right: float, yaw_deg: float) -> tuple[float, float]:
    r = math.radians(yaw_deg)
    return fwd * math.sin(r) + right * math.cos(r), fwd * math.cos(r) - right * math.sin(r)


def plan_step(cur: Pose, x: float, y: float, z: float, seg_cm: float, frame: DroneFrame,
              drone_yaw: float) -> tuple[tuple[int, int, int], tuple[float, float, float]] | None:
    """목표까지 남은 거리를 균등 구간으로 나눈 첫 구간.
    반환: ((전진, 오른쪽, 위) 기체 cm 정수, 그 구간의 지도 변위). 남은 거리가 SDK 최소 이동(20)보다
    작으면 도착으로 보고 None."""
    wx, wy, wz = frame.map_to_drone(x - cur.x_cm, y - cur.y_cm, z - cur.z_cm)
    fwd, right = world_to_body(wx, wy, drone_yaw)
    if max(abs(fwd), abs(right), abs(wz)) < MIN_GO_CM:
        return None
    seg = min(seg_cm, MAX_GO_CM)
    n = max(1, math.ceil(max(math.hypot(fwd, right), abs(wz)) / seg))
    step = [round(fwd / n), round(right / n), round(wz / n)]
    if max(abs(v) for v in step) < MIN_GO_CM:
        # 구간을 나누니 너무 짧아짐 → 가장 큰 성분을 최소값으로 키운다
        i = max(range(3), key=lambda k: abs(step[k]))
        step[i] = MIN_GO_CM if step[i] >= 0 else -MIN_GO_CM
    swx, swy = body_to_world(step[0], step[1], drone_yaw)
    return (step[0], step[1], step[2]), frame.drone_to_map(swx, swy, step[2])


class TelloBackend(DroneBackend):
    def __init__(self, *, name: str, cfg: TelloCfg, provider: PositionProvider, home: Pose,
                 make_cmd_tello: Callable[[], Any],
                 make_monitor_tello: Callable[[], Any] | None = None,
                 read_state: Callable[[Any], dict] | None = None,
                 interrupt: Callable[[Any], None] | None = None,
                 sim_reset: Callable[[Any], None] | None = None,
                 frame: DroneFrame | None = None,
                 camera_mode: Callable[[Any, str], None] | None = None):
        """
        make_cmd_tello:     비행 명령용 Tello 객체를 만든다 (connect 전)
        make_monitor_tello: 상태 읽기·비상정지용 별도 Tello (시뮬레이터는 명령이 끝날 때까지 응답을
                            막으므로 연결을 하나 더 쓴다). None이면 명령용 객체를 같이 쓴다.
        read_state:         monitor에서 상태 dict 읽기 (기본: get_current_state)
        interrupt:          진행 중 이동을 즉시 멈추는 함수 (실제 TT: 'stop' 명령). 없으면 구간이 끝날 때까지 기다림
        sim_reset:          시뮬레이터 비상정지 후 리셋
        frame:              기체 세계 ↔ 지도 좌표 변환 (기본: 항등 = 실제 드론)
        camera_mode:        위치 추적 카메라 모드를 드론에 반영하는 함수 (tello, mode) — real 전용
                            (streamon/downvision/streamoff). None이면 모드만 기억한다(sim).
        """
        self.frame = frame or DroneFrame()
        self.vision = provider if isinstance(provider, VisionProvider) else None
        self.name = name
        self.cfg = cfg
        self.provider = provider
        self.home = home
        self._make_cmd = make_cmd_tello
        self._make_mon = make_monitor_tello
        self._read_state = read_state or (lambda t: t.get_current_state())
        self._interrupt = interrupt
        self._sim_reset = sim_reset
        self._camera_mode = camera_mode
        self._cmd = None
        self._mon = None
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{name}-cmd")
        self._raw: dict[str, Any] = {}
        self._last_ok = 0.0
        self._flying = False
        self._poll_thread: threading.Thread | None = None
        self._stop_poll = threading.Event()
        self._ever_connected = False
        self._in_flight = 0  # 명령 스레드에서 실행 중이거나 기다리는 명령 수 (0이면 TT는 받은 명령을 다 끝낸 상태)
        self._last_cmd_t = time.monotonic()  # 마지막으로 TT에 명령을 보낸(또는 끝낸) 시각 — 유지 신호 판단용
        self._ka_pending = False
        self._ground_since: float | None = None
        self.self_landed = False  # TT가 스스로 착륙했다고 판단함 (컨트롤러가 읽고 지운다)

    # ------------------------------------------------------------ 연결·상태
    async def connect(self) -> None:
        await asyncio.to_thread(self._connect_sync)

    def _connect_sync(self) -> None:
        self._close_sync()
        cmd = self._make_cmd()
        cmd.connect()
        mon = cmd
        if self._make_mon is not None:
            mon = self._make_mon()
            mon.connect()
        self._cmd, self._mon = cmd, mon
        raw = self._read_state(mon)
        self._on_raw(raw)
        self._flying = bool(raw.get("flying", self._flying))
        if not self._ever_connected:
            self._ever_connected = True
            self.provider.reset(self._start_pose(raw))
        # 드론이 (다시) 연결될 때마다 현재 위치 추적 모드에 맞게 카메라를 맞춘다 (docs/api.md 11장).
        # 실패해도 비행 제어 연결은 유지한다 (영상은 비행 안전과 별개).
        if self._camera_mode is not None and self.vision_mode:
            try:
                self._camera_mode(cmd, self.vision_mode)
            except Exception as e:  # noqa: BLE001
                log.warning("카메라 모드(%s) 설정 실패: %s", self.vision_mode, e)
        self._stop_poll.clear()
        self._poll_thread = threading.Thread(target=self._poll, name=f"{self.name}-poll", daemon=True)
        self._poll_thread.start()

    def _start_pose(self, raw: dict) -> Pose:
        """추정 위치의 시작점: 기본은 home (드론을 home에 놓고 켠다는 가정)."""
        return self.home

    def _on_raw(self, raw: dict) -> None:
        self._raw = raw
        self._last_ok = time.monotonic()
        self.provider.update(raw)
        if "flying" in raw:  # 시뮬레이터는 참값을 준다
            self._flying = bool(raw["flying"])
        else:
            self._check_ground(raw)

    def _check_ground(self, raw: dict) -> None:
        """실제 TT: 비행 중이라고 알고 있는데 높이(h)가 바닥이면 스스로 착륙한 것으로 본다.

        TT는 배터리 부족·15초 무명령·충돌 등으로 스스로 내려앉는다. 서버가 이를 모르면 앱이 계속 '비행 중'으로
        보이고 착륙 명령은 TT가 error로 거절한다(2026-11-21 실물 시험).
        """
        lim = self.cfg.ground_h_cm
        h = raw.get("h", raw.get("tof"))
        if lim <= 0 or not self._flying or h is None:
            self._ground_since = None
            return
        now = time.monotonic()
        if float(h) > lim:
            self._ground_since = None
        elif self._ground_since is None:
            self._ground_since = now
        elif now - self._ground_since >= self.cfg.ground_s:
            log.warning("TT 높이 %s cm가 %.1f초 넘게 바닥 → 스스로 착륙한 것으로 봅니다", h, self.cfg.ground_s)
            self._flying = False
            self._ground_since = None
            self.self_landed = True

    def _on_ground(self) -> bool:
        h = self._raw.get("h", self._raw.get("tof"))
        return h is not None and self.cfg.ground_h_cm > 0 and float(h) <= self.cfg.ground_h_cm

    def _maybe_keepalive(self) -> None:
        """비행 중 명령이 keepalive_s 동안 없으면 'rc 0 0 0 0'(제자리, 응답 없음)을 명령 스레드로 보낸다."""
        ka = self.cfg.keepalive_s
        if (ka <= 0 or not self._flying or self._cmd is None or self._in_flight or self._ka_pending
                or time.monotonic() - self._last_cmd_t < ka):
            return
        self._ka_pending = True

        def keepalive():
            try:
                self._cmd.send_rc_control(0, 0, 0, 0)
            except Exception as e:  # noqa: BLE001
                log.warning("유지 신호(rc 0 0 0 0) 실패: %s", e)
            finally:
                self._last_cmd_t = time.monotonic()
                self._ka_pending = False
        try:
            self._exec.submit(keepalive)
        except RuntimeError:  # 종료 중
            self._ka_pending = False

    def _refresh(self) -> None:
        """명령 직후 상태를 바로 다시 읽는다 (다음 구간 계산이 오래된 위치·높이를 쓰지 않도록)."""
        try:
            self._on_raw(self._read_state(self._mon))
        except Exception as e:  # noqa: BLE001
            log.warning("state refresh failed: %s", e)

    def _poll(self) -> None:
        while not self._stop_poll.is_set():
            try:
                self._on_raw(self._read_state(self._mon))
                self._maybe_keepalive()
            except Exception as e:  # noqa: BLE001
                log.warning("state read failed: %s", e)
                if time.monotonic() - self._last_ok > STALE_AFTER_S:
                    return  # connect_loop가 다시 연결한다
            self._stop_poll.wait(POLL_PERIOD_S)

    def _close_sync(self) -> None:
        self._stop_poll.set()
        if self._poll_thread and self._poll_thread is not threading.current_thread():
            self._poll_thread.join(timeout=1)
        for t in {id(x): x for x in (self._cmd, self._mon) if x is not None}.values():
            try:
                close = getattr(getattr(t, "_client", None), "close", None)  # 가짜 djitellopy
                if close:
                    close()
                elif hasattr(t, "end") and self.name != "real":
                    t.end()
            except Exception:  # noqa: BLE001
                pass
        self._cmd = self._mon = None

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)
        self._exec.shutdown(wait=False, cancel_futures=True)

    def telemetry(self) -> Telemetry:
        connected = self._cmd is not None and time.monotonic() - self._last_ok < STALE_AFTER_S
        bat = self._raw.get("bat")
        return Telemetry(
            connected=connected,
            flying=self._flying,
            pos=self.provider.pose(),
            pos_source=self.provider.source,
            battery_pct=float(bat) if bat is not None else None,
            vision_z_cm=self.vision.display_z_cm() if self.vision else None,
        )

    async def apply_vision_mode(self, mode: str) -> None:
        """모드를 기억하고, 연결돼 있으면 카메라 명령을 보낸다 (비행 명령과 같은 스레드에서 차례로).
        연결 전이면 연결될 때 보낸다(_connect_sync)."""
        self.vision_mode = mode
        if self._camera_mode is None or self._cmd is None:
            return
        await self._call(lambda: self._camera_mode(self._cmd, mode))

    # ------------------------------------------------------------ 명령
    async def _call(self, fn: Callable, *args) -> Any:
        if self._cmd is None:
            raise BackendError("드론과 연결되지 않았습니다")
        loop = asyncio.get_running_loop()
        self._in_flight += 1

        def run():
            self._last_cmd_t = time.monotonic()
            try:
                return fn(*args)
            finally:
                self._last_cmd_t = time.monotonic()
                self._in_flight -= 1  # 취소돼도 명령 스레드에서 실제로 끝난 뒤에 줄인다

        try:
            return await loop.run_in_executor(self._exec, run)
        except asyncio.CancelledError:
            raise
        except BackendError:
            raise
        except Exception as e:  # noqa: BLE001 — TelloException 등
            raise BackendError(str(e)) from e

    @property
    def command_in_progress(self) -> bool:
        """TT가 아직 끝내지 않은 명령(이동 구간·이륙 등)이 있는지."""
        return self._in_flight > 0

    async def _interrupt_if_busy(self) -> None:
        """진행 중인 명령이 있을 때만 stop으로 끊고, 그 명령이 끝날 때까지 기다린다.

        호버링 중(진행 중 명령 없음)에는 아무것도 보내지 않는다: 이미 멈춰 있는 TT에 stop을 보내면
        곧바로 이어지는 land와 겹쳐 착륙이 늦거나 한 번 더 눌러야 하는 원인이 될 수 있다(2026-11-21 실물 시험).
        """
        if not self.command_in_progress:
            return
        if self._interrupt and self._mon is not None:
            log.info("진행 중인 명령이 있어 stop으로 끊고 끝나기를 기다립니다")
            await asyncio.to_thread(self._interrupt, self._mon)
        await self._drain()

    async def takeoff(self) -> None:
        if self._flying:
            return
        if self._sim_reset and str(self._raw.get("flight_mode", "")).lower() == "emergency":
            if not self.cfg.auto_reset_after_emergency:
                raise BackendError("시뮬레이터가 비상정지 상태입니다. 시뮬레이터에서 Reset을 누르세요")
            await self._call(self._sim_reset, self._cmd)
            await asyncio.sleep(0.5)
            await self._call(self._refresh)
            self.provider.reset(self._start_pose(self._raw))
        def takeoff():
            self._cmd.takeoff()
            self._flying = True
            self.self_landed = False
            self._ground_since = None
            self._refresh()
        await self._call(takeoff)

    async def move_to(self, x_cm: float, y_cm: float, z_cm: float) -> int:
        """반환: TT에 보낸 이동 구간 수 (0 = 목표가 이미 20cm 이내라 움직이지 않음)."""
        if not self._flying:
            raise BackendError("비행 중이 아닙니다")
        cur = self.provider.pose()
        if cur is None:
            raise BackendError("현재 위치를 알 수 없습니다")
        if self.cfg.max_height_cm > 0:
            z_cap = self.frame.alt_to_map(self.cfg.max_height_cm)
            if z_cm > z_cap:
                log.warning("목표 고도 %.1f cm → 기체 높이 상한 때문에 %.1f cm로 제한", z_cm, z_cap)
                z_cm = z_cap
        wd = self.frame.map_to_drone(x_cm - cur.x_cm, y_cm - cur.y_cm, z_cm - cur.z_cm)
        max_steps = math.ceil(math.hypot(*wd) / max(self.cfg.segment_cm, 1)) + 10
        log.info("move_to 목표 (%.1f, %.1f, %.1f) cm ← 현재 (%.1f, %.1f, %.1f) cm [%s], 드론 yaw %.0f°",
                 x_cm, y_cm, z_cm, cur.x_cm, cur.y_cm, cur.z_cm, self.provider.source,
                 float(self._raw.get("yaw", 0.0)))
        sent = 0
        for _ in range(max_steps):
            cur = self.provider.pose()
            plan = plan_step(cur, x_cm, y_cm, z_cm, self.cfg.segment_cm, self.frame,
                             float(self._raw.get("yaw", 0.0)))
            if plan is None:
                if sent == 0:
                    log.info("move_to: 목표가 현재 위치에서 각 방향 %d cm 이내라 이동하지 않습니다", MIN_GO_CM)
                else:
                    log.info("move_to: 도착 (구간 %d개) — 현재 (%.1f, %.1f, %.1f) cm", sent, cur.x_cm, cur.y_cm, cur.z_cm)
                return sent
            (fwd, right, up), map_delta = plan
            if self.cfg.go_axes == "sim":   # 시뮬레이터: go(a=오른쪽, b=전진, c=위)
                args = (right, fwd, up)
            else:                           # 실제 SDK: go(x=전진, y=왼쪽, z=위)
                args = (fwd, -right, up)

            def go(a=args, d=map_delta):
                self._cmd.go_xyz_speed(*a, int(self.cfg.speed_cm_s))
                # 추정 위치 갱신도 명령 스레드에서 한다: 미션이 취소돼도 이미 날아간 구간은 반영되도록
                self.provider.on_move(*d)
                self._refresh()

            await self._call(go)
            sent += 1
        log.warning("move_to: 최대 구간 수 초과, 목표 근처에서 멈춥니다")
        return sent

    async def _drain(self) -> None:
        """진행 중인 명령(최대 한 구간)이 끝날 때까지 기다린다."""
        await self._call(lambda: None)

    async def hover(self) -> None:
        await self._interrupt_if_busy()

    async def land(self) -> None:
        await self._interrupt_if_busy()
        if not self._flying:
            return
        await self._descend_before_land()

        def land():
            self._cmd.land()
            self._flying = False
        try:
            await self._call(land)
        except BackendError:
            # TT가 이미 바닥에 있으면 land를 error로 거절한다 → 실패가 아니라 '이미 착지'로 처리
            await self._call(self._refresh)
            if self._on_ground():
                log.info("land 거절됨 — TT 높이가 바닥이라 이미 착지한 것으로 봅니다")
                self._flying = False
                return
            raise

    async def _descend_before_land(self) -> None:
        """시뮬레이터는 높은 곳(20 m)에서 land를 보내면 너무 느려 시간 초과가 난다 → 먼저 내려온다."""
        target = self.cfg.land_descend_to_cm
        if target <= 0:
            return
        for _ in range(100):
            h = float(self._raw.get("h", 0))
            dz = h - target
            if dz < MIN_GO_CM:
                return
            step = int(min(dz, self.cfg.segment_cm, MAX_GO_CM))
            if step < MIN_GO_CM:
                return
            def down(d=step):
                self._cmd.go_xyz_speed(0, 0, -d, int(self.cfg.speed_cm_s))
                self._refresh()
            await self._call(down)

    async def emergency(self) -> None:
        if self._mon is None:
            raise BackendError("드론과 연결되지 않았습니다")
        # 명령 스레드를 기다리지 않고 monitor 연결로 바로 보낸다
        await asyncio.to_thread(self._mon.emergency)
        self._flying = False
