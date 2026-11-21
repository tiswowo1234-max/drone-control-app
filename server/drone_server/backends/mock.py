"""mock 백엔드 — Unity 없이 순수 Python으로 드론을 흉내 낸다.

이륙·이동·착륙에 실제처럼 시간이 걸리고, 배터리가 줄고, 위치가 직선으로 보간된다.
속도·배터리 값은 settings.toml [mock]에서 바꾼다.

위치가 두 개다 (v0.7):
  self.pose  = 서버가 '안다고 생각하는' 위치 (명령 누적 추정). vision 값이 오면 그 값으로 옮겨진다.
  self.truth = 가짜 드론이 '실제로 있는' 위치. 이동 명령만큼만 움직이고 vision 값에 끌려가지 않는다.
               GET /sim/truth가 이 값을 주고, 합성 카메라(vision/)가 이 위치에서 보이는 화면을 그린다.
vision 입력을 쓰지 않으면 두 값은 항상 같다.
"""
from __future__ import annotations

import asyncio
import math
import time

from ..models import BackendError, Pose, Telemetry
from ..positioning import PositionProvider, VisionProvider
from ..settings import Settings
from .base import DroneBackend

TICK_S = 0.05


class MockBackend(DroneBackend):
    name = "mock"
    has_truth = True

    def __init__(self, settings: Settings):
        self.s = settings
        self.cfg = settings.mock
        self.pose = Pose(settings.home_x_cm, settings.home_y_cm, 0.0, 0.0)
        self.truth = Pose(settings.home_x_cm, settings.home_y_cm, 0.0, 0.0)
        self.battery = float(self.cfg.battery_start_pct)
        self.flying = False
        self.connected = False
        self._last_drain = time.monotonic()
        if self.cfg.pos_source == "vision":
            self.vision = VisionProvider(_MockPoseProvider(self), settings.vision.stale_s)

    # --- 내부 ---
    def _drain(self) -> None:
        now = time.monotonic()
        dt_min = (now - self._last_drain) / 60.0 * self.cfg.time_scale
        self._last_drain = now
        rate = self.cfg.battery_drain_flying_pct_per_min if self.flying else self.cfg.battery_drain_idle_pct_per_min
        self.battery = max(0.0, self.battery - rate * dt_min)

    async def _animate(self, x: float, y: float, z: float, speed: float) -> None:
        """현재 위치에서 (x, y, z)까지 speed(cm/s)로 직선 이동. cancel되면 그 자리에서 멈춘다."""
        speed *= self.cfg.time_scale
        while True:
            self._drain()
            dx, dy, dz = x - self.pose.x_cm, y - self.pose.y_cm, z - self.pose.z_cm
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            step = speed * TICK_S
            k = 1.0 if dist <= step else step / dist
            self._shift(dx * k, dy * k, dz * k)
            if dist <= step:
                self.pose.x_cm, self.pose.y_cm, self.pose.z_cm = x, y, z
                return
            await asyncio.sleep(TICK_S)

    def _shift(self, dx: float, dy: float, dz: float) -> None:
        """추정 위치와 실제 위치를 같은 변위만큼 움직인다 (명령한 만큼 실제로 움직였다고 가정)."""
        for p in (self.pose, self.truth):
            p.x_cm += dx
            p.y_cm += dy
            p.z_cm = max(0.0, p.z_cm + dz)

    def _require(self) -> None:
        if not self.connected:
            raise BackendError("드론과 연결되지 않았습니다")

    # --- 인터페이스 ---
    async def connect(self) -> None:
        self.connected = True

    def telemetry(self) -> Telemetry:
        self._drain()
        return Telemetry(
            connected=self.connected,
            flying=self.flying,
            pos=self.vision.pose() if self.vision else Pose(self.pose.x_cm, self.pose.y_cm, self.pose.z_cm, self.pose.yaw_deg),
            pos_source=self.vision.source if self.vision else "estimate",
            battery_pct=round(self.battery),
            vision_z_cm=self.vision.display_z_cm() if self.vision else None,
        )

    def truth_pose(self) -> Pose | None:
        t = self.truth
        return Pose(t.x_cm, t.y_cm, t.z_cm, t.yaw_deg)

    async def takeoff(self) -> None:
        self._require()
        if self.flying:
            return
        self.flying = True
        await self._animate(self.pose.x_cm, self.pose.y_cm, self.cfg.takeoff_height_cm,
                            self.cfg.vertical_speed_cm_s)

    async def move_to(self, x_cm: float, y_cm: float, z_cm: float) -> None:
        self._require()
        if not self.flying:
            raise BackendError("비행 중이 아닙니다")
        # 고도 변경만이면 수직 속도, 아니면 수평 속도로 (단순화)
        horizontal = math.hypot(x_cm - self.pose.x_cm, y_cm - self.pose.y_cm) > 1
        speed = self.cfg.speed_cm_s if horizontal else self.cfg.vertical_speed_cm_s
        await self._animate(x_cm, y_cm, z_cm, speed)

    async def hover(self) -> None:
        self._require()  # 이동은 컨트롤러가 작업을 cancel해서 이미 멈춘 상태

    async def land(self) -> None:
        self._require()
        if not self.flying:
            return
        await self._animate(self.pose.x_cm, self.pose.y_cm, 0.0, self.cfg.vertical_speed_cm_s)
        self.flying = False

    async def emergency(self) -> None:
        self._require()
        self.flying = False
        self.pose.z_cm = self.truth.z_cm = 0.0  # 모터 정지 → 그대로 추락


class _MockPoseProvider(PositionProvider):
    """mock + vision용 fallback: 가짜 드론의 자체 위치를 그대로 쓰고,
    vision 값이 오면(reset) 가짜 드론의 위치를 그 값으로 옮긴다 → 다음 이동은 vision 위치에서 시작."""

    def __init__(self, backend: MockBackend):
        self.b = backend

    @property
    def source(self) -> str:
        return "estimate"

    def pose(self) -> Pose | None:
        p = self.b.pose
        return Pose(p.x_cm, p.y_cm, p.z_cm, p.yaw_deg)

    def reset(self, pose: Pose) -> None:
        self.b.pose.x_cm, self.b.pose.y_cm = pose.x_cm, pose.y_cm
