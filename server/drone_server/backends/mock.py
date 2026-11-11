"""mock 백엔드 — Unity 없이 순수 Python으로 드론을 흉내 낸다.

이륙·이동·착륙에 실제처럼 시간이 걸리고, 배터리가 줄고, 위치가 직선으로 보간된다.
속도·배터리 값은 settings.toml [mock]에서 바꾼다.
"""
from __future__ import annotations

import asyncio
import math
import time

from ..models import BackendError, Pose, Telemetry
from ..settings import Settings
from .base import DroneBackend

TICK_S = 0.05


class MockBackend(DroneBackend):
    name = "mock"

    def __init__(self, settings: Settings):
        self.s = settings
        self.cfg = settings.mock
        self.pose = Pose(settings.home_x_cm, settings.home_y_cm, 0.0, 0.0)
        self.battery = float(self.cfg.battery_start_pct)
        self.flying = False
        self.connected = False
        self._last_drain = time.monotonic()

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
            if dist <= step:
                self.pose.x_cm, self.pose.y_cm, self.pose.z_cm = x, y, z
                return
            k = step / dist
            self.pose.x_cm += dx * k
            self.pose.y_cm += dy * k
            self.pose.z_cm += dz * k
            await asyncio.sleep(TICK_S)

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
            pos=Pose(self.pose.x_cm, self.pose.y_cm, self.pose.z_cm, self.pose.yaw_deg),
            pos_source="estimate",
            battery_pct=round(self.battery),
        )

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
        self.pose.z_cm = 0.0  # 모터 정지 → 그대로 추락
