from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Pose:
    """지도 좌표계 (docs/api.md 2장): cm, 원점=지도 왼쪽 아래, yaw는 북쪽 0·시계방향 +."""
    x_cm: float
    y_cm: float
    z_cm: float
    yaw_deg: float = 0.0

    def to_json(self) -> dict:
        return {
            "x_cm": round(self.x_cm, 1),
            "y_cm": round(self.y_cm, 1),
            "z_cm": round(self.z_cm, 1),
            "yaw_deg": round(self.yaw_deg, 1),
        }


@dataclass
class Telemetry:
    connected: bool
    flying: bool
    pos: Pose | None
    pos_source: str
    battery_pct: float | None


class BackendError(RuntimeError):
    """드론/시뮬레이터가 명령을 실패했을 때."""
