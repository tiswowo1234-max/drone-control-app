"""안전 검사 — 서버가 최종 방어선 (docs/api.md 6장). 앱의 검사를 믿지 않는다."""
from __future__ import annotations

import math

from .settings import Settings


class SafetyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xc:
                inside = not inside
    return inside


def check_target(s: Settings, x: float, y: float, z: float,
                 from_xy: tuple[float, float] | None = None) -> None:
    """목표 좌표 검사. 문제가 있으면 SafetyError를 던진다."""
    for v in (x, y, z):
        if not math.isfinite(v):
            raise SafetyError("BAD_REQUEST", "좌표가 숫자가 아닙니다")
    if not (0 <= x <= s.map_width_cm and 0 <= y <= s.map_height_cm):
        raise SafetyError("OUT_OF_BOUNDS", "지도 경계 밖 좌표입니다")
    for zone in s.no_fly_zones:
        if point_in_polygon(x, y, zone.polygon_cm):
            raise SafetyError("NO_FLY_ZONE", f"금지 구역({zone.name}) 안의 좌표입니다")
    if math.hypot(x - s.home_x_cm, y - s.home_y_cm) > s.limits.max_radius_cm:
        raise SafetyError("TOO_FAR", f"이륙 지점에서 {s.limits.max_radius_cm:g}cm보다 먼 좌표입니다")
    if not (s.limits.min_alt_cm <= z <= s.limits.max_alt_cm):
        raise SafetyError("ALT_LIMIT",
                          f"고도는 {s.limits.min_alt_cm:g}~{s.limits.max_alt_cm:g}cm 사이여야 합니다")
    # 직선 경로가 금지 구역을 지나가는지 (지도 경계·원형 반경은 볼록이라 끝점 검사로 충분)
    if from_xy is not None and s.no_fly_zones:
        fx, fy = from_xy
        steps = max(1, int(math.hypot(x - fx, y - fy) / 5))
        for i in range(1, steps):
            px, py = fx + (x - fx) * i / steps, fy + (y - fy) * i / steps
            for zone in s.no_fly_zones:
                if point_in_polygon(px, py, zone.polygon_cm):
                    raise SafetyError("NO_FLY_ZONE", f"경로가 금지 구역({zone.name})을 지나갑니다")


def check_battery(s: Settings, battery_pct: float | None) -> None:
    if battery_pct is not None and battery_pct < s.limits.min_battery_pct:
        raise SafetyError("LOW_BATTERY",
                          f"배터리 {battery_pct:.0f}%: {s.limits.min_battery_pct:g}% 미만이라 출동할 수 없습니다")


def clamp_alt(s: Settings, z: float) -> float:
    return min(max(z, s.limits.min_alt_cm), s.limits.max_alt_cm)
