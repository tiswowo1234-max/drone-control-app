"""UGV(미니 경찰차) 제어 — docs/api.md v0.8 13장.

조원 police-car-controller(PWA, Web Bluetooth)와 **같은 BLE 명령**을 서버가 대신 보낸다. 차 펌웨어는 바꾸지 않는다.
  서버 → 차 (RX 특성, 한 줄 JSON + "\\n", chunk_bytes씩):
    {"cmd":"DRIVE","v_mm_s":v,"omega_rad_s":w}  {"cmd":"SET_ROUTE","points":[[x,y],...],"max_speed_mm_s":s}
    {"cmd":"START"} {"cmd":"STOP"} {"cmd":"ESTOP"} {"cmd":"CLEAR_ESTOP"} {"cmd":"GET_STATUS"}
  차 → 서버 (TX 알림): {"type":"STATUS","state":..,"x_mm":..,"y_mm":..,"theta_deg":..,"wp":..,"n":..,"left_rpm":..,"right_rpm":..}
                       {"type":"ERROR","reason":..}  {"type":"XIAO_STATUS","camera":..}
  차 좌표(엔코더, mm): 원점 = 차가 원점을 잡은 곳, +X 전방, +Y 좌측, θ 반시계(θ=0이 +X).

차 구조 (2026-11-25 펌웨어 확인): 서버 ─BLE─▶ ESP32-CAM(중계만, 이름은 그대로 PoliceCar-XIAO) ─UART─▶ OpenRB-150(주행 전부).
  OpenRB 펌웨어 사실: 상태 READY · MANUAL · ROUTE_READY · RUNNING · ESTOP · MOTOR_ERROR · LINK_LOST,
  SET_ROUTE는 첫 점이 (0,0)에서 50mm 넘게 떨어지면 조용히 무시(→ 서버는 경로마다 RESET_ODOM 후 P0 = [0,0]),
  경로 끝 → RUNNING에서 ROUTE_READY로(wp = n-1 그대로), DRIVE 0.3초 끊기면 정지, STATUS 0.3초마다,
  잘못된 명령에 ERROR를 보내지 않음(→ 서버가 RUNNING이 안 되면 스스로 알아챔).

차 위치 (CarTracker)
  - 드론이 UGV 태그를 보면(POST /vision/ugv, 지도 cm·yaw) 그 위치를 쓰고, 그때의 차 엔코더 값과 짝지어
    "엔코더 좌표 → 지도 좌표" 변환(기준점)을 만든다.
  - 드론이 못 보는 동안은 기준점 + 지금 엔코더 값으로 지도 위치를 이어 간다 (pos_source = "odom").
  - 지도에서 누른 경로점도 같은 기준점으로 엔코더 좌표(mm)로 바꿔 SET_ROUTE로 보낸다.
"""
from __future__ import annotations

import abc
import asyncio
import contextlib
import json
import logging
import math
import time
from typing import Any, Awaitable, Callable

from .safety import SafetyError
from .settings import CarCfg, Settings

log = logging.getLogger("drone_server.car")

EventSink = Callable[[dict], Awaitable[None]]
LineSink = Callable[[str], None]

CAR_COMMANDS = ("car_stop", "car_estop", "car_clear_estop", "car_route")
BAD_STATES = ("ESTOP", "MOTOR_ERROR")
MOVING_STATES = ("MANUAL", "RUNNING")
RESET_ODOM_TOL_MM = 50.0  # 펌웨어: 경로 첫 점은 원점에서 이 안이어야 한다
LINE_GAP_S = 0.05         # 경로 명령 사이 간격 (ESP32-CAM 명령 대기열이 4줄)
STATUS_POLL_S = 1.0       # 이 시간 동안 STATUS가 없으면 GET_STATUS를 보낸다
RECONNECT_S = 2.0


def wrap180(d: float) -> float:
    d = (d + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


# ====================================================================== 좌표 변환

class OdomToMap:
    """차 엔코더 좌표(mm, +X 전방, +Y 좌측, θ 반시계) → 지도(cm, x 오른쪽, y 위쪽, yaw 위쪽 0·시계 +).

    alpha_deg = 엔코더 +X축이 가리키는 지도 방향(yaw 기준). origin = 엔코더 원점의 지도 위치(cm).
    """

    def __init__(self, origin_x_cm: float, origin_y_cm: float, alpha_deg: float):
        self.ox, self.oy, self.alpha = origin_x_cm, origin_y_cm, alpha_deg % 360.0
        a = math.radians(self.alpha)
        self.ex = (math.sin(a), math.cos(a))     # 엔코더 +X 방향 (지도 단위벡터)
        self.ey = (-math.cos(a), math.sin(a))    # 엔코더 +Y(좌측) 방향

    @classmethod
    def from_pair(cls, map_x_cm: float, map_y_cm: float, map_yaw_deg: float,
                  odom_x_mm: float, odom_y_mm: float, odom_theta_deg: float) -> "OdomToMap":
        """같은 순간의 지도 자세(드론 인식)와 엔코더 자세로 변환을 만든다."""
        alpha = map_yaw_deg + odom_theta_deg           # 지도 yaw(시계) = alpha - θ(반시계)
        a = math.radians(alpha)
        ex, ey = (math.sin(a), math.cos(a)), (-math.cos(a), math.sin(a))
        xo, yo = odom_x_mm / 10.0, odom_y_mm / 10.0
        return cls(map_x_cm - xo * ex[0] - yo * ey[0], map_y_cm - xo * ex[1] - yo * ey[1], alpha)

    def to_map(self, x_mm: float, y_mm: float, theta_deg: float) -> tuple[float, float, float]:
        x, y = x_mm / 10.0, y_mm / 10.0
        return (self.ox + x * self.ex[0] + y * self.ey[0], self.oy + x * self.ex[1] + y * self.ey[1],
                (self.alpha - theta_deg) % 360.0)

    def to_odom(self, x_cm: float, y_cm: float) -> tuple[float, float]:
        dx, dy = x_cm - self.ox, y_cm - self.oy
        return ((dx * self.ex[0] + dy * self.ex[1]) * 10.0, (dx * self.ey[0] + dy * self.ey[1]) * 10.0)


class CarTracker:
    """차 지도 위치 = 드론 인식(신선하면) 또는 마지막 인식 기준점 + 엔코더."""

    def __init__(self, cfg: CarCfg, clock: Callable[[], float] = time.monotonic):
        self.cfg, self._clock = cfg, clock
        self.odom: tuple[float, float, float] | None = None   # (x_mm, y_mm, theta_deg)
        self.vision: tuple[float, float, float] | None = None  # (x_cm, y_cm, yaw_deg)
        self.vision_at: float | None = None
        self.anchor: OdomToMap | None = None
        self.anchor_at: float | None = None
        self.anchor_lost_reason: str | None = None   # 기준점을 버렸으면 이유 (이벤트용, 한 번 읽으면 지움)
        self._reset_pose: tuple[float, float, float] | None = None   # 원점 재설정을 기다리는 중: 그때의 지도 자세

    # ------------------------------------------------ 서버가 보낸 RESET_ODOM (경로마다)
    def expect_reset(self, map_pose: tuple[float, float, float]) -> None:
        """RESET_ODOM을 보내기 직전. 새 원점(엔코더 0)이 map_pose가 되도록, 원점 근처 STATUS가 올 때 기준점을 다시 잡는다.
        그 전에 오는 STATUS(재설정 전 값)는 버린다 → 갑자기 바뀐 엔코더 값을 '차 재시작'으로 오해하지 않게."""
        self._reset_pose = (float(map_pose[0]), float(map_pose[1]), float(map_pose[2]) % 360.0)

    def cancel_reset(self) -> None:
        self._reset_pose = None

    @property
    def reset_pending(self) -> bool:
        return self._reset_pose is not None

    def on_odom(self, x_mm: float, y_mm: float, theta_deg: float) -> None:
        if self._reset_pose is not None:
            if math.hypot(x_mm, y_mm) > RESET_ODOM_TOL_MM:
                return   # 아직 재설정 전 값
            mx, my, myaw = self._reset_pose
            self._reset_pose = None
            self.odom = (x_mm, y_mm, theta_deg)
            self.anchor = OdomToMap.from_pair(mx, my, myaw, x_mm, y_mm, theta_deg)
            self.anchor_at = self.anchor_at if self.anchor_at is not None else self._clock()
            return
        if self.odom is not None and self.anchor is not None:
            jump = math.hypot(x_mm - self.odom[0], y_mm - self.odom[1])
            if jump > self.cfg.odom_jump_mm:   # 차 재부팅·원점 재설정 → 옛 기준점은 틀린다
                self.anchor = self.anchor_at = None
                self.anchor_lost_reason = f"차 엔코더 위치가 갑자기 {jump:.0f}mm 바뀌었습니다 (차 재시작?)"
        self.odom = (x_mm, y_mm, theta_deg)

    def on_vision(self, x_cm: float, y_cm: float, yaw_deg: float) -> bool:
        """드론 인식 한 번. 엔코더 값이 있으면 기준점을 새로 잡는다. 처음 잡았으면 True."""
        now = self._clock()
        self.vision, self.vision_at = (x_cm, y_cm, yaw_deg % 360.0), now
        if self.odom is None:
            return False
        first = self.anchor is None
        self.anchor = OdomToMap.from_pair(x_cm, y_cm, yaw_deg, *self.odom)
        self.anchor_at = now
        return first

    def seen_age_s(self) -> float | None:
        return None if self.vision_at is None else self._clock() - self.vision_at

    def pose(self) -> tuple[tuple[float, float, float] | None, str | None]:
        """(지도 x_cm, y_cm, yaw_deg), 출처("vision" | "odom" | None)."""
        age = self.seen_age_s()
        if age is not None and age <= self.cfg.pose_fresh_s and self.vision is not None:
            return self.vision, "vision"
        if self.anchor is not None and self.odom is not None:
            return self.anchor.to_map(*self.odom), "odom"
        return None, None

    def route_anchor(self) -> OdomToMap:
        """경로를 엔코더 좌표로 바꿀 기준점. 없거나 너무 오래됐으면 SafetyError."""
        if self.anchor is None or self.odom is None:
            raise SafetyError("CAR_NO_POSITION", "드론이 아직 차를 인식하지 못했습니다. 드론을 차 위로 보내 태그를 보게 하세요")
        age = self._clock() - (self.anchor_at or 0.0)
        if age > self.cfg.route_anchor_max_s:
            raise SafetyError("CAR_POSITION_OLD", f"드론이 차를 마지막으로 본 지 {age:.0f}초가 지나 위치를 믿기 어렵습니다 "
                                                  f"(최대 {self.cfg.route_anchor_max_s:g}초). 드론으로 차를 다시 보세요")
        return self.anchor


# ====================================================================== 연결 (BLE / 가짜 차)

class CarLinkError(RuntimeError):
    pass


class CarLink(abc.ABC):
    name = "car"

    def __init__(self) -> None:
        self.connected = False
        self.message = "연결 준비 중"

    @abc.abstractmethod
    async def run(self, on_line: LineSink) -> None:
        """연결을 유지한다 (끊기면 다시 시도). 차가 보낸 한 줄마다 on_line."""

    @abc.abstractmethod
    async def send_line(self, line: str) -> None:
        """한 줄 JSON 전송. 연결 안 됨·실패면 CarLinkError."""

    async def close(self) -> None:
        pass

    def truth_map(self) -> tuple[float, float, float] | None:
        """가짜 차의 실제 지도 위치 (합성 카메라용). 실제 차는 None."""
        return None


class BleCarLink(CarLink):
    """bleak로 차의 XIAO ESP32-S3에 연결 (Windows 노트북 내장 블루투스)."""
    name = "ble"

    def __init__(self, cfg: CarCfg):
        super().__init__()
        self.cfg = cfg
        self.client = None
        self._write_lock = asyncio.Lock()
        self._rx = bytearray()

    async def run(self, on_line: LineSink) -> None:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            self.message = "bleak가 설치되지 않았습니다 (server\\.venv에서 pip install -r requirements.txt)"
            log.error(self.message)
            await asyncio.Event().wait()
            return
        cfg = self.cfg
        while True:
            try:
                self.message = f"차 찾는 중 ({cfg.ble_address or cfg.ble_name})"
                if cfg.ble_address:
                    dev = await BleakScanner.find_device_by_address(cfg.ble_address, timeout=8.0)
                else:
                    dev = await BleakScanner.find_device_by_filter(
                        lambda d, ad: (d.name == cfg.ble_name or ad.local_name == cfg.ble_name
                                       or cfg.service_uuid.lower() in [u.lower() for u in ad.service_uuids]),
                        timeout=8.0)
                if dev is None:
                    self.message = f"차({cfg.ble_name})를 찾지 못했습니다 — 차 전원, 블루투스, 다른 폰의 PWA 연결 여부 확인"
                    await asyncio.sleep(RECONNECT_S)
                    continue
                gone = asyncio.Event()
                client = BleakClient(dev, disconnected_callback=lambda _c: gone.set())
                self.message = f"{dev.name or dev.address} 연결 중"
                await client.connect(timeout=15.0)
                self._rx.clear()

                def on_notify(_char, data: bytearray) -> None:
                    for b in data:
                        if b == 0x0A:
                            if self._rx:
                                on_line(self._rx.decode("utf-8", "replace"))
                            self._rx.clear()
                        elif b != 0x0D and len(self._rx) < 1024:
                            self._rx.append(b)

                await client.start_notify(cfg.tx_uuid, on_notify)
                self.client, self.connected = client, True
                self.message = f"연결됨: {dev.name or dev.address}"
                log.info("car BLE connected: %s (%s)", dev.name, dev.address)
                await gone.wait()
                log.warning("car BLE disconnected")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 블루투스 오류는 다양하다. 다시 시도
                self.message = f"BLE 오류: {e}"
                log.warning("car BLE error: %r", e)
            finally:
                self.connected = False
                c, self.client = self.client, None
                if c is not None:
                    with contextlib.suppress(Exception):
                        await c.disconnect()
            await asyncio.sleep(RECONNECT_S)

    async def send_line(self, line: str) -> None:
        client = self.client
        if client is None or not self.connected:
            raise CarLinkError("차와 BLE로 연결되어 있지 않습니다")
        data = (line + "\n").encode("utf-8")
        n = max(1, self.cfg.chunk_bytes)
        async with self._write_lock:   # 한 줄이 다른 줄과 섞이지 않게
            try:
                for i in range(0, len(data), n):
                    await client.write_gatt_char(self.cfg.rx_uuid, data[i:i + n], response=True)
            except Exception as e:  # noqa: BLE001
                raise CarLinkError(f"BLE 전송 실패: {e}") from e

    async def close(self) -> None:
        c = self.client
        if c is not None:
            with contextlib.suppress(Exception):
                await self.send_line(json.dumps({"cmd": "STOP"}))
            with contextlib.suppress(Exception):
                await c.disconnect()


class MockCarLink(CarLink):
    """가짜 차: 조원 펌웨어(OpenRB_PoliceCar.ino, 2026-11-25 받음)의 명령 처리를 그대로 흉내 낸다.

    - 상태: ESTOP > MOTOR_ERROR > LINK_LOST > RUNNING > MANUAL > ROUTE_READY > READY (펌웨어 currentState 순서)
    - SET_ROUTE: 주행·수동·비상정지 중이면, 점 2~16개가 아니면, **첫 점이 (0,0)에서 50mm 넘게 떨어지면 조용히 무시**
    - START: 경로가 있어야 RUNNING. 경로 끝에 닿으면 RUNNING만 꺼지고 ROUTE_READY로 돌아감(wp = n-1 그대로)
    - DRIVE: 0.3초(MANUAL_COMMAND_TIMEOUT_MS) 안에 다음 DRIVE가 없으면 멈춤
    - RESET_ODOM·CLEAR_ESTOP: 주행·수동 중이면 무시. 잘못된 명령에 ERROR를 보내지 않는다(펌웨어도 안 보냄)
    - STATUS: 0.3초마다 + GET_STATUS 때

    실제 바퀴는 미끄러지므로 실제 이동량을 엔코더 값의 slip배로 둔다 → 드론 인식으로 보정되는 모습을 시험할 수 있다.
    """
    name = "mock"
    DT = 0.05
    STATUS_PERIOD_S = 0.3      # TELEMETRY_PERIOD_MS
    DRIVE_TIMEOUT_S = 0.3      # MANUAL_COMMAND_TIMEOUT_MS (실제 시간 — 시험 배속으로 나누지 않음)
    WAYPOINT_TOL_MM = 35.0
    FINAL_TOL_MM = 25.0
    P0_TOL_MM = 50.0
    MAX_WAYPOINTS = 16

    def __init__(self, cfg: CarCfg, time_scale: float = 1.0, slip: float = 0.97):
        super().__init__()
        self.cfg, self.time_scale, self.slip = cfg, time_scale, slip
        self.estop = self.running = self.manual = self.route_loaded = False
        self.x = self.y = self.th = 0.0          # 엔코더 (mm, rad)
        x0, y0 = cfg.mock_start_cm
        self.tx, self.ty, self.tyaw = x0, y0, cfg.mock_start_yaw_deg   # 실제 지도 위치 (cm, yaw)
        self.v = self.w = 0.0
        self.route: list[tuple[float, float]] = []
        self.wp = 1
        self.route_speed = cfg.route_speed_mm_s
        self.last_drive = 0.0
        self.ignore_start = False                # 시험용: START를 무시하는 차 (주행 시작 안 됨 감지 시험)
        self.sent: list[dict] = []               # 시험용: 받은 명령 기록
        self._on_line: LineSink | None = None

    @property
    def state(self) -> str:
        if self.estop:
            return "ESTOP"
        if self.running:
            return "RUNNING"
        if self.manual:
            return "MANUAL"
        return "ROUTE_READY" if self.route_loaded else "READY"

    async def run(self, on_line: LineSink) -> None:
        self._on_line = on_line
        self.connected, self.message = True, "가짜 차(mock) 연결됨"
        last_status = time.monotonic()
        while True:
            self.step(self.DT * self.time_scale)
            now = time.monotonic()
            if now - last_status >= self.STATUS_PERIOD_S:
                last_status = now
                self._status()
            await asyncio.sleep(self.DT)

    def _status(self) -> None:
        if self._on_line is None:
            return
        rpm = self.v / (2 * math.pi * 45.0) * 60.0
        turn = self.w * 68.0 / (2 * math.pi * 45.0) * 60.0
        self._on_line(json.dumps({
            "type": "STATUS", "state": self.state, "x_mm": round(self.x, 2), "y_mm": round(self.y, 2),
            "theta_deg": round(math.degrees(self.th), 2), "wp": self.wp, "n": len(self.route),
            "left_rpm": round(rpm - turn, 2), "right_rpm": round(rpm + turn, 2), "estop": self.estop}))

    def step(self, dt: float) -> None:
        """dt초 진행 (빨리 돌릴 때도 조향이 안정하게 0.02초씩 나눠 계산)."""
        n = max(1, math.ceil(dt / 0.02))
        for _ in range(n):
            self._substep(dt / n)

    def _substep(self, dt: float) -> None:
        if self.manual and time.monotonic() - self.last_drive > self.DRIVE_TIMEOUT_S:
            self.manual = False
        if self.running:
            self._follow_route()
        if self.estop or not (self.running or self.manual):
            self.v = self.w = 0.0
        # 엔코더 (x 전방, y 좌측, θ 반시계)
        self.x += self.v * math.cos(self.th) * dt
        self.y += self.v * math.sin(self.th) * dt
        self.th = math.atan2(math.sin(self.th + self.w * dt), math.cos(self.th + self.w * dt))
        # 실제 지도 위치 (yaw 시계 = -반시계)
        d = self.v * dt * self.slip / 10.0
        a = math.radians(self.tyaw)
        self.tx += d * math.sin(a)
        self.ty += d * math.cos(a)
        self.tyaw = (self.tyaw - math.degrees(self.w * dt)) % 360.0

    def _follow_route(self) -> None:
        last = len(self.route) - 1
        while self.wp < last and math.hypot(self.route[self.wp][0] - self.x,
                                            self.route[self.wp][1] - self.y) <= self.WAYPOINT_TOL_MM:
            self.wp += 1
        gx, gy = self.route[self.wp]
        if self.wp == last and math.hypot(gx - self.x, gy - self.y) <= self.FINAL_TOL_MM:
            self.running = False          # 펌웨어: routeLoaded는 그대로 → ROUTE_READY, wp는 n-1
            self.v = self.w = 0.0
            return
        err = math.atan2(gy - self.y, gx - self.x) - self.th
        err = math.atan2(math.sin(err), math.cos(err))
        self.w = max(-1.8, min(1.8, 2.0 * err))
        self.v = self.route_speed * max(0.0, math.cos(err)) if abs(err) < 0.6 else 0.0

    async def send_line(self, line: str) -> None:
        if not self.connected:
            raise CarLinkError("가짜 차가 아직 켜지지 않았습니다")
        d = json.loads(line)
        self.sent.append(d)
        cmd = d.get("cmd")
        if cmd == "STOP":
            self.running = self.manual = False
        elif cmd == "ESTOP":
            self.estop, self.running, self.manual = True, False, False
        elif cmd == "CLEAR_ESTOP":
            if not (self.running or self.manual):
                self.estop = False
        elif cmd == "RESET_ODOM":
            if not (self.running or self.manual):
                self.x = self.y = self.th = 0.0
                if self.route_loaded:
                    self.wp = 1
        elif cmd == "SET_ROUTE":
            self._load_route(d)
        elif cmd == "START":
            if self.route_loaded and not self.estop and not self.ignore_start:
                self.manual, self.wp, self.running = False, 1, True
        elif cmd == "DRIVE":
            if not self.estop:
                self.running, self.manual = False, True
                self.v = max(-100.0, min(100.0, float(d.get("v_mm_s", 0))))
                self.w = max(-1.5, min(1.5, float(d.get("omega_rad_s", 0))))
                self.last_drive = time.monotonic()
        elif cmd == "GET_STATUS":
            self._status()

    def _load_route(self, d: dict) -> None:
        if self.running or self.manual or self.estop:
            return
        pts = d.get("points")
        if not isinstance(pts, list) or not 2 <= len(pts) <= self.MAX_WAYPOINTS:
            return
        route = [(float(p[0]), float(p[1])) for p in pts]
        if math.hypot(*route[0]) > self.P0_TOL_MM:   # "A route is relative to the odometry origin"
            return
        self.route, self.wp, self.route_loaded = route, 1, True
        self.route_speed = max(30.0, min(100.0, float(d.get("max_speed_mm_s", 100.0))))

    def truth_map(self) -> tuple[float, float, float]:
        return self.tx, self.ty, self.tyaw


def make_car_link(settings: Settings, time_scale: float = 1.0) -> CarLink | None:
    mode = settings.car.mode
    if mode == "ble":
        return BleCarLink(settings.car)
    if mode == "mock":
        return MockCarLink(settings.car, time_scale)
    return None


# ====================================================================== 명령·상태

def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _ack(cmd_id: Any, error: str | None = None, message: str | None = None) -> dict:
    a: dict[str, Any] = {"type": "ack", "id": cmd_id, "ok": error is None}
    if error is not None:
        a["error"], a["message"] = error, message or error
    return a


class CarController:
    def __init__(self, settings: Settings, link: CarLink, emit: EventSink | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.s, self.cfg, self.link, self._emit, self._clock = settings, settings.car, link, emit, clock
        self.tracker = CarTracker(self.cfg, clock)
        self.status: dict | None = None
        self.status_at: float | None = None
        self.route_map: list[tuple[float, float]] | None = None   # 보낸 경로 (지도 cm, 앱 표시용)
        self.route_sent_at: float | None = None   # START를 보낸 시각
        self.route_seen_running = False           # 보낸 경로로 RUNNING이 된 적 있는지
        self.drive_at: float | None = None       # 마지막 조이스틱 명령 시각 (None = 조이스틱 안 씀)
        self.last_error: str | None = None
        self._was_connected: bool | None = None
        self._tasks: list[asyncio.Task] = []
        self._app_stop_sent = False

    # ------------------------------------------------------------------ 수명
    def start(self) -> None:
        self._tasks = [asyncio.create_task(self.link.run(self.on_line), name="car-link"),
                       asyncio.create_task(self._poll_loop(), name="car-poll")]

    async def shutdown(self) -> None:
        for t in self._tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.link.close(), 1.0)

    async def event(self, level: str, code: str, message: str) -> None:
        log.info("car event %s %s %s", level, code, message)
        if self._emit:
            await self._emit({"type": "event", "level": level, "code": code, "message": message})

    # ------------------------------------------------------------------ 입력
    def on_line(self, line: str) -> None:
        try:
            d = json.loads(line)
        except ValueError:
            log.debug("car rx (not json): %s", line)
            return
        if not isinstance(d, dict):
            return
        kind = d.get("type")
        if kind == "STATUS":
            self.status, self.status_at = d, self._clock()
            x, y, th = _num(d.get("x_mm")), _num(d.get("y_mm")), _num(d.get("theta_deg"))
            if x is not None and y is not None and th is not None:
                self.tracker.on_odom(x, y, th)
            if self.route_map is not None and self.route_sent_at is not None:
                if d.get("state") == "RUNNING":
                    self.route_seen_running = True
                elif self.route_seen_running:   # RUNNING → 다른 상태 = 경로 끝(펌웨어는 ROUTE_READY로 돌아감) 또는 정지
                    ended_ok = d.get("state") == "ROUTE_READY"
                    self._clear_route()
                    if ended_ok:
                        self._spawn(self.event("info", "CAR_ROUTE_DONE", "차가 경로 끝에 도착했습니다"))
        elif kind == "ERROR":
            self.last_error = str(d.get("reason", "unknown"))
            self._spawn(self.event("warn", "CAR_ERROR", f"차 오류: {self.last_error}"))
        else:
            log.info("car rx %s", line)

    def on_vision(self, x_cm: float, y_cm: float, yaw_deg: float) -> None:
        first = self.tracker.on_vision(x_cm, y_cm, yaw_deg)
        if first:
            self._spawn(self.event("info", "CAR_LOCATED", "드론이 차를 인식했습니다 — 지도에 차 위치를 표시합니다"))

    def _spawn(self, coro) -> None:
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:   # 이벤트 루프 밖(시험)
            coro.close()

    async def _send(self, obj: dict, quiet: bool = False) -> None:
        try:
            await self.link.send_line(json.dumps(obj, separators=(",", ":")))
        except CarLinkError as e:
            raise SafetyError("CAR_NOT_CONNECTED", str(e)) from e
        if not quiet:
            log.info("car tx %s", obj)

    # ------------------------------------------------------------------ 앱 명령
    async def handle_cmd(self, msg: dict) -> dict:
        cmd_id, cmd = msg.get("id"), msg.get("cmd")
        if not isinstance(cmd_id, (str, int)) or isinstance(cmd_id, bool) or cmd_id == "":
            return _ack(cmd_id, "BAD_REQUEST", "id(문자열)가 필요합니다")
        if cmd not in CAR_COMMANDS:
            return _ack(cmd_id, "BAD_REQUEST", f"알 수 없는 차 명령입니다: {cmd!r}")
        try:
            await getattr(self, f"_c_{cmd}")(msg)
        except SafetyError as e:
            log.info("car cmd %s rejected: %s %s", cmd, e.code, e.message)
            return _ack(cmd_id, e.code, e.message)
        return _ack(cmd_id)

    def _require_link(self) -> None:
        if not self.link.connected:
            raise SafetyError("CAR_NOT_CONNECTED", f"차와 연결되어 있지 않습니다 ({self.link.message})")

    def _clear_route(self) -> None:
        self.route_map, self.route_sent_at, self.route_seen_running = None, None, False

    async def _c_car_stop(self, msg: dict) -> None:
        self._require_link()
        self.drive_at = None
        self._clear_route()
        await self._send({"cmd": "STOP"})

    async def _c_car_estop(self, msg: dict) -> None:
        self._require_link()
        self.drive_at = None
        self._clear_route()
        await self._send({"cmd": "ESTOP"})
        await self.event("warn", "CAR_ESTOP", "차 비상정지: 해제하기 전까지 주행하지 않습니다")

    async def _c_car_clear_estop(self, msg: dict) -> None:
        self._require_link()
        await self._send({"cmd": "CLEAR_ESTOP"})

    async def _c_car_route(self, msg: dict) -> None:
        pts_raw = msg.get("points_cm")
        if not isinstance(pts_raw, list) or not pts_raw:
            raise SafetyError("BAD_REQUEST", "points_cm(경로점 목록)가 필요합니다")
        if len(pts_raw) > self.cfg.max_points - 1:
            raise SafetyError("BAD_REQUEST", f"경로점은 최대 {self.cfg.max_points - 1}개입니다 (시작점 제외)")
        pts: list[tuple[float, float]] = []
        m = self.cfg.map_margin_cm
        for p in pts_raw:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise SafetyError("BAD_REQUEST", "경로점은 [x_cm, y_cm] 형식이어야 합니다")
            x, y = _num(p[0]), _num(p[1])
            if x is None or y is None:
                raise SafetyError("BAD_REQUEST", "경로점 좌표는 숫자여야 합니다")
            if not (m <= x <= self.s.map_width_cm - m and m <= y <= self.s.map_height_cm - m):
                raise SafetyError("OUT_OF_BOUNDS", f"경로점 ({x:.0f}, {y:.0f})cm가 지도 밖(가장자리 {m:g}cm 포함)입니다")
            pts.append((x, y))
        speed = _num(msg.get("speed_mm_s")) or self.cfg.route_speed_mm_s
        speed = max(30.0, min(self.cfg.max_speed_mm_s, speed))   # 펌웨어(PWA) 범위 30~100
        self._require_link()
        st = (self.status or {}).get("state")
        if st in BAD_STATES:
            raise SafetyError("CAR_ESTOP", f"차 상태가 {st}입니다. 비상정지를 해제한 뒤 보내세요")
        self.tracker.route_anchor()   # 드론이 본 적 있는지·너무 오래되지 않았는지
        self.drive_at = None
        self._clear_route()
        # 펌웨어는 경로 첫 점이 엔코더 원점(0,0) 근처여야 받는다 → 멈추고, 지금 자리를 원점으로 다시 잡은 뒤 보낸다
        await self._send({"cmd": "STOP"})
        await asyncio.sleep(LINE_GAP_S * 2)   # 멈춘 뒤 자리 (STOP 직후 바퀴가 조금 더 돌 수 있음)
        here, _src = self.tracker.pose()      # 드론이 지금 보면 그 위치, 아니면 기준점 + 엔코더
        if here is None:
            raise SafetyError("CAR_NO_POSITION", "차 위치를 알 수 없습니다. 드론으로 차를 다시 보세요")
        self.tracker.expect_reset(here)
        await self._send({"cmd": "RESET_ODOM"})
        deadline = self._clock() + self.cfg.reset_wait_s
        while self.tracker.reset_pending and self._clock() < deadline:
            await asyncio.sleep(LINE_GAP_S)
            if self.tracker.reset_pending:
                await self._send({"cmd": "GET_STATUS"}, quiet=True)
                await asyncio.sleep(LINE_GAP_S * 2)
        if self.tracker.reset_pending:
            self.tracker.cancel_reset()
            raise SafetyError("CAR_NO_RESPONSE", f"차가 원점 재설정(RESET_ODOM)에 {self.cfg.reset_wait_s:g}초 안에 응답하지 않았습니다. "
                                                 "차 상태(주행 중·조이스틱 중이면 무시됨)와 연결을 확인하세요")
        anchor = self.tracker.anchor
        odom_pts = [[0, 0]] + [[round(v) for v in anchor.to_odom(x, y)] for x, y in pts]
        await self._send({"cmd": "SET_ROUTE", "points": odom_pts, "max_speed_mm_s": round(speed)})
        await asyncio.sleep(LINE_GAP_S)
        await self._send({"cmd": "START"})
        self.route_map = [(here[0], here[1])] + pts
        self.route_sent_at, self.route_seen_running = self._clock(), False
        log.info("car route: map %s → odom %s", [(round(x), round(y)) for x, y in pts], odom_pts)

    async def drive(self, msg: dict) -> None:
        """`car_drive` (응답 없음, 0.12초마다): 조이스틱 속도를 그대로 DRIVE로."""
        v, w = _num(msg.get("v_mm_s")), _num(msg.get("omega_rad_s"))
        if v is None or w is None or not self.link.connected:
            return
        if (self.status or {}).get("state") in BAD_STATES:
            return
        v = max(-self.cfg.max_speed_mm_s, min(self.cfg.max_speed_mm_s, v))
        w = max(-self.cfg.max_omega_rad_s, min(self.cfg.max_omega_rad_s, w))
        self._clear_route()
        self.drive_at = self._clock()
        with contextlib.suppress(SafetyError):
            await self._send({"cmd": "DRIVE", "v_mm_s": round(v), "omega_rad_s": round(w, 2)}, quiet=True)

    # ------------------------------------------------------------------ 감시
    def moving(self) -> bool:
        st = (self.status or {}).get("state")
        return self.drive_at is not None or st in MOVING_STATES or self.route_map is not None

    async def watchdog_once(self, app_silent_s: float, link_timeout_s: float) -> None:
        c = self.link.connected
        if self._was_connected is not None and c != self._was_connected:
            if c:
                await self.event("info", "CAR_CONNECTED", "차와 연결되었습니다")
            else:
                self.drive_at = None
                await self.event("error", "CAR_DISCONNECTED", "차와 연결이 끊겼습니다")
        self._was_connected = c
        reason = self.tracker.anchor_lost_reason
        if reason:
            self.tracker.anchor_lost_reason = None
            await self.event("warn", "CAR_POSITION_LOST", f"{reason} — 드론으로 차를 다시 인식하세요")
        if not c:
            return
        now = self._clock()
        if (self.route_map is not None and self.route_sent_at is not None and not self.route_seen_running
                and now - self.route_sent_at > self.cfg.route_start_timeout_s):
            st = (self.status or {}).get("state")
            self._clear_route()
            await self.event("warn", "CAR_ROUTE_NOT_STARTED",
                             f"차가 경로 주행을 시작하지 않았습니다 (차 상태 {st}). 차가 경로를 거부했을 수 있습니다")
        if self.drive_at is not None and now - self.drive_at > self.cfg.drive_timeout_s:
            self.drive_at = None   # 조이스틱 손 뗌·앱 끊김
            with contextlib.suppress(SafetyError):
                await self._send({"cmd": "STOP"})
        if app_silent_s > link_timeout_s:
            if self.moving() and not self._app_stop_sent:
                self._app_stop_sent = True
                self.drive_at = None
                self._clear_route()
                with contextlib.suppress(SafetyError):
                    await self._send({"cmd": "STOP"})
                await self.event("warn", "CAR_LINK_LOST_STOP", f"앱 연결이 {link_timeout_s:g}초 이상 끊겨 차를 멈췄습니다")
        else:
            self._app_stop_sent = False

    async def on_controller_change(self) -> None:
        """명령권 앱이 바뀌면 조이스틱 주행은 멈춘다 (경로 주행은 그대로)."""
        if self.drive_at is not None:
            self.drive_at = None
            with contextlib.suppress(SafetyError):
                await self._send({"cmd": "STOP"})

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(STATUS_POLL_S)
            if self.link.connected and (self.status_at is None or self._clock() - self.status_at > STATUS_POLL_S):
                with contextlib.suppress(SafetyError):
                    await self._send({"cmd": "GET_STATUS"}, quiet=True)

    # ------------------------------------------------------------------ 출력
    def config_json(self) -> dict:
        c = self.cfg
        return {"link": self.link.name, "max_speed_mm_s": c.max_speed_mm_s, "max_omega_rad_s": c.max_omega_rad_s,
                "route_speed_mm_s": c.route_speed_mm_s, "max_points": c.max_points - 1,
                "map_margin_cm": c.map_margin_cm}

    def state_json(self) -> dict:
        st = self.status or {}
        pose, src = self.tracker.pose()
        age = self.tracker.seen_age_s()
        n, wp = st.get("n"), st.get("wp")
        return {
            "connected": self.link.connected,
            "link_message": self.link.message,
            "state": st.get("state") if self.link.connected else None,
            "pos": None if pose is None else {"x_cm": round(pose[0], 1), "y_cm": round(pose[1], 1),
                                              "yaw_deg": round(pose[2], 1)},
            "pos_source": src,
            "seen_age_s": None if age is None else round(age, 1),
            "route": None if self.route_map is None else {
                "points_cm": [[round(x, 1), round(y, 1)] for x, y in self.route_map],
                "wp": wp if isinstance(wp, int) else None, "n": n if isinstance(n, int) else None},
            "driving": self.drive_at is not None,
        }
