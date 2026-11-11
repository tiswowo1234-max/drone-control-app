"""config/settings.toml 읽기.

코드에 숫자를 직접 쓰지 않기 위해, 조정 가능한 값은 모두 여기서 settings.toml을 읽어 온다.
settings.toml에 없는 서버 내부용 값만 아래 기본값(DEFAULTS)을 쓴다.
"""
from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = REPO_ROOT / "config" / "settings.toml"


@dataclass(frozen=True)
class NoFlyZone:
    name: str
    polygon_cm: list[tuple[float, float]]


@dataclass(frozen=True)
class Limits:
    max_radius_cm: float
    min_alt_cm: float
    max_alt_cm: float
    default_alt_cm: float
    min_battery_pct: float
    link_timeout_s: float


@dataclass(frozen=True)
class ServerCfg:
    host: str = "0.0.0.0"
    port: int = 8000
    state_hz: float = 5.0


@dataclass(frozen=True)
class MockCfg:
    speed_cm_s: float = 50.0          # 수평 이동 속도
    vertical_speed_cm_s: float = 40.0  # 이착륙·고도 변경 속도
    takeoff_height_cm: float = 100.0   # TT는 이륙하면 약 1m에서 멈춘다
    battery_start_pct: float = 100.0
    battery_drain_flying_pct_per_min: float = 6.0
    battery_drain_idle_pct_per_min: float = 0.5
    time_scale: float = 1.0            # 테스트에서 빠르게 돌릴 때 > 1


@dataclass(frozen=True)
class TelloCfg:
    """sim/real 공통 (djitellopy 기반) 비행 설정."""
    speed_cm_s: int = 50          # go_xyz_speed 속도 (10..100)
    segment_cm: float = 50.0      # 한 번에 보내는 최대 이동 거리 (짧을수록 stop이 빨리 먹힘)
    arrive_tol_cm: float = 20.0   # TT go 명령 최소 이동량이 20cm라 그보다 가까우면 도착으로 본다
    pos_source: str = "estimate"  # sim: estimate | sim_truth / real: estimate | mission_pad
    go_axes: str = "sdk"          # sdk: go(x=전진, y=왼쪽) / sim: go(a=오른쪽, b=전진) — 시뮬레이터 차이
    host: str = "127.0.0.1"       # sim 전용
    port: int = 9911              # sim 전용
    simulator_python_dir: str = "RoboMasterTT-Simulator beta/Python"  # sim 전용 (가짜 djitellopy 위치)
    auto_reset_after_emergency: bool = True  # sim 전용
    max_height_cm: float = 0.0  # 기체 높이 상한(기체 cm). 0이면 없음. 시뮬레이터는 높이 센서가 약 29.8 m에서 멈춘다
    land_descend_to_cm: float = 0.0  # 착륙 전에 이 높이(기체 cm)까지 go로 먼저 내려온다. 0이면 바로 land


@dataclass(frozen=True)
class SimCalibration:
    """시뮬레이터 좌표(m) ↔ 지도 좌표(cm).

    1) 시뮬레이터 평면 (x, z)에서 origin을 빼고, map_x_from/map_y_from 축을 골라 (a, b)를 만든다.
       (거울 반전이 있으면 축 부호로 표현: 예) a = -x)
    2) (a, b)를 rotation_deg만큼 돌리고 scale_cm_per_m을 곱하면 지도 cm.
         map_x = scale * ( a*cos + b*sin ),  map_y = scale * ( -a*sin + b*cos )
    높이도 같은 배율을 쓴다: 지도 z(cm) = 시뮬레이터 높이(m) * scale_cm_per_m
    """
    origin_x_m: float = 0.0
    origin_z_m: float = 0.0
    scale_cm_per_m: float = 100.0
    map_x_from: str = "+x"
    map_y_from: str = "+z"
    rotation_deg: float = 0.0

    @staticmethod
    def _axis(spec: str) -> tuple[float, float]:
        sign = 1.0 if spec[0] == "+" else -1.0
        return (sign, 0.0) if spec[1] == "x" else (0.0, sign)

    def matrix(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """시뮬레이터 (dx, dz) m → 지도 (dx, dy) cm 선형 변환 2x2."""
        ax, bx = self._axis(self.map_x_from), self._axis(self.map_y_from)
        c, s = math.cos(math.radians(self.rotation_deg)), math.sin(math.radians(self.rotation_deg))
        k = self.scale_cm_per_m
        r0 = (k * (c * ax[0] + s * bx[0]), k * (c * ax[1] + s * bx[1]))
        r1 = (k * (-s * ax[0] + c * bx[0]), k * (-s * ax[1] + c * bx[1]))
        return r0, r1

    def sim_delta_to_map(self, dx_m: float, dz_m: float) -> tuple[float, float]:
        r0, r1 = self.matrix()
        return r0[0] * dx_m + r0[1] * dz_m, r1[0] * dx_m + r1[1] * dz_m

    def map_delta_to_sim(self, dx_cm: float, dy_cm: float) -> tuple[float, float]:
        (a, b), (c, d) = self.matrix()
        det = a * d - b * c
        return (d * dx_cm - b * dy_cm) / det, (-c * dx_cm + a * dy_cm) / det

    def sim_to_map(self, sx: float, sz: float) -> tuple[float, float]:
        """시뮬레이터 평면 좌표(m) → 지도 cm."""
        return self.sim_delta_to_map(sx - self.origin_x_m, sz - self.origin_z_m)

    def map_to_sim(self, x_cm: float, y_cm: float) -> tuple[float, float]:
        dx, dz = self.map_delta_to_sim(x_cm, y_cm)
        return dx + self.origin_x_m, dz + self.origin_z_m

    def sim_yaw_to_map(self, yaw_deg: float) -> float:
        """시뮬레이터 yaw(+z가 0, 시계방향 +) → 지도 yaw(지도 위쪽 0, 시계방향 +)."""
        r = math.radians(yaw_deg)
        mx, my = self.sim_delta_to_map(math.sin(r), math.cos(r))
        return math.degrees(math.atan2(mx, my))


@dataclass(frozen=True)
class Settings:
    map_width_cm: float
    map_height_cm: float
    home_x_cm: float
    home_y_cm: float
    limits: Limits
    no_fly_zones: list[NoFlyZone]
    geo: dict[str, Any] | None
    backend: str
    server: ServerCfg = field(default_factory=ServerCfg)
    mock: MockCfg = field(default_factory=MockCfg)
    sim: TelloCfg = field(default_factory=TelloCfg)
    real: TelloCfg = field(default_factory=TelloCfg)
    sim_calibration: SimCalibration = field(default_factory=SimCalibration)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _sub(cls, table: dict[str, Any] | None, base=None):
    """dataclass 기본값 위에 toml 테이블 값을 덮어쓴다 (모르는 키는 무시)."""
    base = base or cls()
    if not table:
        return base
    names = cls.__dataclass_fields__.keys()
    kwargs = {k: getattr(base, k) for k in names}
    kwargs.update({k: v for k, v in table.items() if k in names})
    return cls(**kwargs)


def _geo(table: dict[str, Any] | None) -> dict[str, Any] | None:
    if not table:
        return None
    """[geo] → config.geo. 명세의 sw/ne에 더해, 있으면 se/nw(이미지 기준 네 모서리)와
    rotation_deg도 보낸다 — 지도가 30° 회전돼 있어 쌍선형 보간에 네 모서리가 필요하다."""
    try:
        geo: dict[str, Any] = {}
        for corner in ("sw", "se", "nw", "ne"):
            if f"{corner}_lat" in table and f"{corner}_lon" in table:
                geo[corner] = {"lat": float(table[f"{corner}_lat"]), "lon": float(table[f"{corner}_lon"])}
        if "sw" not in geo or "ne" not in geo:
            return None
        if "rotation_deg" in table:
            geo["rotation_deg"] = float(table["rotation_deg"])
        return geo
    except (TypeError, ValueError):
        return None


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path) if path else DEFAULT_SETTINGS_PATH
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    lim = raw["limits"]
    limits = Limits(
        max_radius_cm=float(lim["max_radius_cm"]),
        min_alt_cm=float(lim["min_alt_cm"]),
        max_alt_cm=float(lim["max_alt_cm"]),
        default_alt_cm=float(lim["default_alt_cm"]),
        min_battery_pct=float(lim["min_battery_pct"]),
        link_timeout_s=float(lim["link_timeout_s"]),
    )
    zones = [
        NoFlyZone(name=str(z.get("name", "")), polygon_cm=[(float(p[0]), float(p[1])) for p in z["polygon_cm"]])
        for z in raw.get("no_fly_zones", [])
    ]
    real_defaults = TelloCfg(go_axes="sdk")
    sim_defaults = TelloCfg(go_axes="sim")
    s = Settings(
        map_width_cm=float(raw["map"]["width_cm"]),
        map_height_cm=float(raw["map"]["height_cm"]),
        home_x_cm=float(raw["home"]["x_cm"]),
        home_y_cm=float(raw["home"]["y_cm"]),
        limits=limits,
        no_fly_zones=zones,
        geo=_geo(raw.get("geo")),
        backend=str(raw.get("backend", {}).get("mode", "sim")),
        server=_sub(ServerCfg, raw.get("server")),
        mock=_sub(MockCfg, raw.get("mock")),
        sim=_sub(TelloCfg, raw.get("sim"), sim_defaults),
        real=_sub(TelloCfg, raw.get("real"), real_defaults),
        sim_calibration=_sub(SimCalibration, raw.get("sim_calibration")),
        raw=raw,
    )
    validate(s)
    return s


def validate(s: Settings) -> None:
    """설정값끼리 모순이 있으면 서버를 켜기 전에 알려 준다."""
    L = s.limits
    errs = []
    if not (0 <= s.home_x_cm <= s.map_width_cm and 0 <= s.home_y_cm <= s.map_height_cm):
        errs.append("home이 지도 밖에 있습니다")
    if not (L.min_alt_cm <= L.default_alt_cm <= L.max_alt_cm):
        errs.append("default_alt_cm이 min_alt_cm~max_alt_cm 범위 밖입니다")
    for cfg_name in ("sim", "real"):
        c: TelloCfg = getattr(s, cfg_name)
        if c.go_axes not in ("sdk", "sim"):
            errs.append(f"[{cfg_name}] go_axes는 'sdk' 또는 'sim'이어야 합니다")
        if not 10 <= c.speed_cm_s <= 100:
            errs.append(f"[{cfg_name}] speed_cm_s는 10~100이어야 합니다")
    for spec in (s.sim_calibration.map_x_from, s.sim_calibration.map_y_from):
        if spec not in ("+x", "-x", "+z", "-z"):
            errs.append(f"[sim_calibration] 축 설정 '{spec}'이 잘못됐습니다 (+x -x +z -z)")
    if errs:
        raise ValueError("settings.toml 오류: " + "; ".join(errs))
