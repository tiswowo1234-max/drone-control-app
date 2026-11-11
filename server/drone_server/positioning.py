"""위치 공급자 (PositionProvider).

드론 백엔드(sim/real)는 "지금 지도 좌표로 어디에 있는가"를 이 인터페이스에 묻는다.
위치를 얻는 방법만 갈아 끼우면 되도록 분리했다.

  DeadReckoningProvider  명령 누적 추정 (pos_source = "estimate")  — GPS 없는 TT 기본값
  SimTruthProvider       시뮬레이터 디버그 참값 (pos_source = "sim_truth") — 시뮬레이터 전용
  MissionPadProvider     미션패드 측정 (pos_source = "mission_pad") — 조원이 완성할 자리

조원이 새 방식을 넣는 방법은 server/README.md "위치 공급자 끼워 넣기" 참고.
"""
from __future__ import annotations

import math
import threading
from abc import ABC, abstractmethod
from typing import Any

from .models import Pose
from .settings import SimCalibration


class DroneFrame:
    """드론이 움직이는 '기체 세계 좌표'(cm) ↔ 지도 좌표(cm) 변환.

    실제 드론: 지도 위를 그대로 날므로 항등 변환 (드론을 지도 위쪽(+y)을 보게 놓고 켠다는 가정).
    시뮬레이터: 세계가 지도와 축척·회전·거울 반전이 다르므로 SimFrame을 쓴다.
    기체 세계 좌표의 (x, y)는 yaw 0일 때 (오른쪽, 전진) 방향, z는 높이.
    """

    def map_to_drone(self, dx: float, dy: float, dz: float) -> tuple[float, float, float]:
        return dx, dy, dz

    def drone_to_map(self, wx: float, wy: float, wz: float) -> tuple[float, float, float]:
        return wx, wy, wz

    def yaw_to_map(self, yaw_deg: float) -> float:
        return yaw_deg

    def alt_to_map(self, h_cm: float) -> float:
        return h_cm


class SimFrame(DroneFrame):
    """시뮬레이터 세계 (x, z, 높이; 명령 단위 cm = 시뮬레이터 cm) ↔ 지도 cm."""

    def __init__(self, calib: SimCalibration):
        self.c = calib

    def map_to_drone(self, dx, dy, dz):
        sx, sz = self.c.map_delta_to_sim(dx, dy)
        return sx * 100.0, sz * 100.0, dz / self.c.scale_cm_per_m * 100.0

    def drone_to_map(self, wx, wy, wz):
        mx, my = self.c.sim_delta_to_map(wx / 100.0, wy / 100.0)
        return mx, my, wz / 100.0 * self.c.scale_cm_per_m

    def yaw_to_map(self, yaw_deg):
        return self.c.sim_yaw_to_map(yaw_deg)

    def alt_to_map(self, h_cm):
        return h_cm / 100.0 * self.c.scale_cm_per_m


class PositionProvider(ABC):
    @property
    @abstractmethod
    def source(self) -> str:
        """state.pos_source 값: estimate | mission_pad | sim_truth"""

    @abstractmethod
    def pose(self) -> Pose | None:
        """지도 좌표계의 현재 위치. 모르면 None."""

    def reset(self, pose: Pose) -> None:
        """위치를 알고 있는 값으로 맞춘다 (서버 시작·시뮬레이터 리셋 때 home 등)."""

    def update(self, raw_state: dict[str, Any]) -> None:
        """텔레메트리를 읽을 때마다(약 10Hz) SDK 상태 dict를 넘겨준다.
        실제 TT: bat, h, yaw, mid, x, y, z, mpry ... / 시뮬레이터: + world_x, world_y, world_z"""

    def on_move(self, dx_cm: float, dy_cm: float, dz_cm: float) -> None:
        """상대 이동 명령 하나가 성공적으로 끝났을 때 (지도 좌표계 변위)."""


class DeadReckoningProvider(PositionProvider):
    """보낸 이동 명령을 더해 가는 추정. 높이·yaw는 센서값(h, yaw)을 쓴다."""

    def __init__(self, start: Pose, frame: DroneFrame | None = None):
        self._lock = threading.Lock()
        self._pose = Pose(start.x_cm, start.y_cm, start.z_cm, start.yaw_deg)
        self.frame = frame or DroneFrame()

    @property
    def source(self) -> str:
        return "estimate"

    def pose(self) -> Pose | None:
        with self._lock:
            p = self._pose
            return Pose(p.x_cm, p.y_cm, p.z_cm, p.yaw_deg)

    def reset(self, pose: Pose) -> None:
        with self._lock:
            self._pose = Pose(pose.x_cm, pose.y_cm, pose.z_cm, pose.yaw_deg)

    def update(self, raw: dict[str, Any]) -> None:
        with self._lock:
            if "h" in raw:
                self._pose.z_cm = self.frame.alt_to_map(float(raw["h"]))
            if "yaw" in raw:
                self._pose.yaw_deg = normalize_yaw(self.frame.yaw_to_map(float(raw["yaw"])))

    def on_move(self, dx_cm: float, dy_cm: float, dz_cm: float) -> None:
        with self._lock:
            self._pose.x_cm += dx_cm
            self._pose.y_cm += dy_cm


class SimTruthProvider(PositionProvider):
    """시뮬레이터 get_sim_debug_state()의 world_x/world_z를 [sim_calibration]으로 지도 cm로 바꾼다."""

    def __init__(self, calib: SimCalibration):
        self.calib = calib
        self.frame = SimFrame(calib)
        self._pose: Pose | None = None

    @property
    def source(self) -> str:
        return "sim_truth"

    def pose(self) -> Pose | None:
        return self._pose

    def update(self, raw: dict[str, Any]) -> None:
        if "world_x" not in raw or "world_z" not in raw:
            return
        x, y = self.calib.sim_to_map(float(raw["world_x"]), float(raw["world_z"]))
        z = self.frame.alt_to_map(float(raw.get("h", 0.0)))
        yaw = normalize_yaw(self.frame.yaw_to_map(float(raw.get("yaw", 0.0))))
        self._pose = Pose(x, y, z, yaw)


class MissionPadProvider(PositionProvider):
    """미션패드 기반 위치 — **조원이 완성할 자리** (현재는 동작 가능한 단순 버전).

    TT는 미션패드를 보면 상태에 mid(패드 번호, 없으면 -1)와 x, y, z(패드 기준 cm)를 준다.
    여기서는 settings.toml [[mission_pads]]의 패드 위치(지도 cm)와 회전을 이용해
      지도 위치 = 패드 위치 + 회전(패드 기준 x, y)
    로 바꾸고, 패드가 안 보이면 명령 누적 추정(fallback)으로 넘어간다.
    TODO(조원): 패드 좌표축 방향 실측, 필터링(튀는 값 제거), 패드 간 전환 처리.
    """

    def __init__(self, pads: dict[int, dict[str, float]], fallback: DeadReckoningProvider):
        self.pads = pads          # {mid: {"x_cm":..., "y_cm":..., "rot_deg":...}}
        self.fallback = fallback
        self._last_pad_pose: Pose | None = None

    @property
    def source(self) -> str:
        return "mission_pad" if self._last_pad_pose is not None else "estimate"

    def pose(self) -> Pose | None:
        return self._last_pad_pose or self.fallback.pose()

    def reset(self, pose: Pose) -> None:
        self.fallback.reset(pose)

    def on_move(self, dx_cm: float, dy_cm: float, dz_cm: float) -> None:
        self.fallback.on_move(dx_cm, dy_cm, dz_cm)

    def update(self, raw: dict[str, Any]) -> None:
        self.fallback.update(raw)
        mid = int(raw.get("mid", -1))
        pad = self.pads.get(mid)
        if pad is None:
            self._last_pad_pose = None
            return
        r = math.radians(pad.get("rot_deg", 0.0))
        px, py = float(raw.get("x", 0)), float(raw.get("y", 0))
        x = pad["x_cm"] + px * math.cos(r) - py * math.sin(r)
        y = pad["y_cm"] + px * math.sin(r) + py * math.cos(r)
        fb = self.fallback.pose()
        self._last_pad_pose = Pose(x, y, float(raw.get("h", fb.z_cm if fb else 0)), fb.yaw_deg if fb else 0.0)
        # 패드로 측정한 값으로 추정값도 보정해 두면 패드를 놓쳐도 덜 틀어진다
        self.fallback.reset(self._last_pad_pose)


def normalize_yaw(deg: float) -> float:
    """-180 < yaw <= 180"""
    d = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d
