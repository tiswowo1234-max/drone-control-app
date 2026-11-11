"""config/settings.toml 읽기.

코드에 숫자를 직접 쓰지 않기 위해, 조정 가능한 값은 모두 여기서 settings.toml을 읽어 온다.
settings.toml에 없는 서버 내부용 값만 아래 기본값(DEFAULTS)을 쓴다.
"""
from __future__ import annotations

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


@dataclass(frozen=True)
class SimCalibration:
    origin_x_m: float = 0.0
    origin_z_m: float = 0.0
    scale_cm_per_m: float = 100.0
    map_x_from: str = "+x"
    map_y_from: str = "+z"
    yaw_offset_deg: float = 0.0

    def sim_to_map(self, sx: float, sz: float) -> tuple[float, float]:
        """시뮬레이터 평면 좌표(m) → 지도 cm."""
        def axis(spec: str) -> float:
            v = sx - self.origin_x_m if spec[1] == "x" else sz - self.origin_z_m
            return v if spec[0] == "+" else -v
        return axis(self.map_x_from) * self.scale_cm_per_m, axis(self.map_y_from) * self.scale_cm_per_m


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
    try:
        return {
            "sw": {"lat": float(table["sw_lat"]), "lon": float(table["sw_lon"])},
            "ne": {"lat": float(table["ne_lat"]), "lon": float(table["ne_lon"])},
        }
    except (KeyError, TypeError, ValueError):
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
