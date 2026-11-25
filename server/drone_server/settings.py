"""config/settings.toml 읽기.

코드에 숫자를 직접 쓰지 않기 위해, 조정 가능한 값은 모두 여기서 settings.toml을 읽어 온다.
settings.toml에 없는 서버 내부용 값만 아래 기본값(DEFAULTS)을 쓴다.
"""
from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = REPO_ROOT / "config" / "settings.toml"

# 위치 추적 카메라 모드 (docs/api.md 11장): esp32 = XIAO ESP32 컬러, tt_down = TT 하단 카메라(흑백 320x240)
VISION_MODES = ("esp32", "tt_down")
INCIDENT_METHODS = ("slot", "photo_finder")


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
    fixed_alt: bool = False   # true = z_cm 없는 goto·귀환은 항상 default_alt_cm (v0.7, 시연 전 고도 고정)


@dataclass(frozen=True)
class ServerCfg:
    host: str = "0.0.0.0"
    port: int = 8000
    state_hz: float = 5.0


@dataclass(frozen=True)
class VisionCfg:
    """카메라 위치 추정 입력 (docs/api.md 7장, POST /vision/pose)."""
    stale_s: float = 1.0          # 마지막 값을 받은 뒤 이 시간이 지나면 명령 누적 추정으로 넘어간다
    min_confidence: float = 0.0   # 이보다 낮은 confidence는 버린다 (LOW_CONFIDENCE)
    margin_m: float = 0.1         # 지도 경계 밖 이만큼까지는 받아 준다 (추정 오차 여유)


@dataclass(frozen=True)
class VideoCfg:
    """실시간 영상 (docs/api.md 8·10.2장). send_* 값은 vision/ 연결 프로그램용이라 서버는 쓰지 않는다."""
    max_frame_kb: float = 300.0   # 이보다 큰 장은 버린다
    max_fps: float = 10.0         # 앱 하나에 보내는 초당 최대 장수
    stale_s: float = 2.0          # 이 시간 동안 새 장이 없으면 "영상 끊김"


@dataclass(frozen=True)
class IncidentCfg:
    """사건 보고 (docs/api.md 9장). 서버가 쓰는 값만."""
    same_place_m: float = 0.15    # 슬롯이 없을 때 이 거리 안이면 같은 사건
    dedup_s: float = 30.0         # 같은 슬롯/장소가 이 시간 안에 다시 오면 중복
    keep: int = 20                # 메모리에 두는 최근 사건 수
    max_photo_kb: float = 500.0   # 사진 한 장 최대 크기 (디코딩 후)


@dataclass(frozen=True)
class BridgeCfg:
    """vision/ 연결 프로그램 (docs/api.md 11장). 서버는 default_mode만 쓴다."""
    default_mode: str = "tt_down"


@dataclass(frozen=True)
class CarCfg:
    """[car] UGV(미니 경찰차) BLE 제어. mode = ble | mock | off."""
    mode: str = "off"
    ble_name: str = "PoliceCar-XIAO"
    ble_address: str = ""
    service_uuid: str = "8f9a0001-6c2e-4c1a-9d21-55a0a1110000"
    rx_uuid: str = "8f9a0002-6c2e-4c1a-9d21-55a0a1110000"
    tx_uuid: str = "8f9a0003-6c2e-4c1a-9d21-55a0a1110000"
    chunk_bytes: int = 18
    max_speed_mm_s: float = 100.0
    max_omega_rad_s: float = 1.5
    route_speed_mm_s: float = 60.0
    max_points: int = 16
    drive_timeout_s: float = 0.5
    pose_fresh_s: float = 1.0
    route_anchor_max_s: float = 120.0
    map_margin_cm: float = 5.0
    odom_jump_mm: float = 300.0
    reset_wait_s: float = 1.5
    route_start_timeout_s: float = 2.0
    mock_start_cm: tuple[float, float] = (120.0, 70.0)
    mock_start_yaw_deg: float = 0.0


@dataclass(frozen=True)
class PhotoSlot:
    """사건 사진 슬롯 S1~S4 ([[photo_slots]], 지도 cm)."""
    slot: int
    name: str
    center_cm: tuple[float, float]
    corners_cm: list[tuple[float, float]]

    def to_json(self) -> dict:
        return {"slot": self.slot, "name": self.name,
                "center_cm": {"x_cm": self.center_cm[0], "y_cm": self.center_cm[1]},
                "corners_cm": [list(p) for p in self.corners_cm]}


@dataclass(frozen=True)
class MockCfg:
    speed_cm_s: float = 50.0          # 수평 이동 속도
    vertical_speed_cm_s: float = 40.0  # 이착륙·고도 변경 속도
    takeoff_height_cm: float = 100.0   # TT는 이륙하면 약 1m에서 멈춘다
    battery_start_pct: float = 100.0
    battery_drain_flying_pct_per_min: float = 6.0
    battery_drain_idle_pct_per_min: float = 0.5
    time_scale: float = 1.0            # 테스트에서 빠르게 돌릴 때 > 1
    pos_source: str = "estimate"      # estimate = 가짜 드론 자체 위치 / vision = POST /vision/pose 값


@dataclass(frozen=True)
class TelloCfg:
    """sim/real 공통 (djitellopy 기반) 비행 설정."""
    speed_cm_s: int = 50          # go_xyz_speed 속도 (10..100)
    segment_cm: float = 50.0      # 한 번에 보내는 최대 이동 거리 (짧을수록 stop이 빨리 먹힘)
    arrive_tol_cm: float = 20.0   # TT go 명령 최소 이동량이 20cm라 그보다 가까우면 도착으로 본다
    pos_source: str = "estimate"  # sim: estimate | sim_truth | vision / real: estimate | mission_pad | vision
    go_axes: str = "sdk"          # sdk: go(x=전진, y=왼쪽) / sim: go(a=오른쪽, b=전진) — 시뮬레이터 차이
    host: str = "127.0.0.1"       # sim 전용
    port: int = 9911              # sim 전용
    simulator_python_dir: str = "RoboMasterTT-Simulator beta/Python"  # sim 전용 (가짜 djitellopy 위치)
    auto_reset_after_emergency: bool = True  # sim 전용
    max_height_cm: float = 0.0  # 기체 높이 상한(기체 cm). 0이면 없음. 시뮬레이터는 높이 센서가 약 29.8 m에서 멈춘다
    land_descend_to_cm: float = 0.0  # 착륙 전에 이 높이(기체 cm)까지 go로 먼저 내려온다. 0이면 바로 land
    keepalive_s: float = 0.0    # real: 비행 중 이 시간 동안 명령이 없으면 'rc 0 0 0 0'을 보낸다(TT는 15초 무명령이면 스스로 착륙). 0이면 끔
    ground_h_cm: float = 0.0    # real: 비행 중인데 TT 높이(h)가 이 값 이하로 ground_s초 이어지면 '스스로 착륙'으로 본다. 0이면 끔
    ground_s: float = 1.0
    alt_source: str = "h"       # real: 고도 = "h"(TT 이륙 기준 추정) / "tof"(거리 센서, 하한·범위 밖이면 h)
    tof_min_cm: float = 15.0     # 이보다 작은 tof는 믿지 않음(센서 하한 약 10cm)
    tof_max_cm: float = 400.0
    heading_from_vision: bool = False   # real: 카메라로 잰 방향으로 TT 방향 센서를 보정해 이동 방향을 계산
    heading_min_samples: int = 5        # 이만큼 모이면 보정 시작
    heading_samples: int = 20           # 최근 표본 수 (원형 평균)
    heading_max_spread_deg: float = 10.0  # 표본 흔들림(원형 표준편차)이 이보다 크면 보정값을 바꾸지 않음
    heading_min_confidence: float = 0.6   # 이보다 신뢰도 낮은 카메라 값(추적 예측값 등)은 표본에서 뺌
    # 출동 방향 바라보기 (2026-11-25): 이동 전에 목표 쪽으로 기수를 돌린 뒤 주로 '전진'으로 이동한다.
    # 실제 드론(항등 좌표)에서만 동작한다 — 시뮬레이터는 좌우 반전 좌표라 쓰지 않는다.
    face_target: bool = False
    face_min_dist_cm: float = 30.0      # 남은 수평 거리가 이보다 짧으면 돌지 않는다
    face_min_deg: float = 10.0          # 목표 방향과 기수 차이가 이보다 작으면 돌지 않는다
    face_settle_s: float = 0.5          # 회전 뒤 센서·카메라가 안정될 때까지 기다리는 시간


@dataclass(frozen=True)
class AlignCfg:
    """초기 정렬 (2026-11-25): 연결 후 첫 이륙 때 가장 가까운 AprilTag 위로 가서 위치·방향을 확정한다."""
    enabled: bool = False
    tags_file: str = ""           # 비우면 vision 연결 프로그램과 같은 태그 지도(조원 원본, [bridge] extra_tags면 확장판)
    max_tag_dist_cm: float = 80.0  # 가장 가까운 태그가 이보다 멀면 정렬하지 않는다
    wait_fix_s: float = 3.0       # 이륙 후 첫 카메라 위치를 기다리는 시간 (없으면 추정 위치로 태그를 고른다)
    min_fixes: int = 5            # 태그 위에서 이만큼 카메라 위치를 받아야 정렬 완료
    min_confidence: float = 0.6   # 이보다 신뢰도가 낮은 카메라 위치(추적 예측값 등)는 세지 않는다
    require_heading: bool = True  # real: 방향 보정값(heading_from_vision)이 정해져야 완료
    timeout_s: float = 10.0       # 태그 위에서 이 시간 안에 완료하지 못하면 실패로 알리고 계속한다


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
    vision: VisionCfg = field(default_factory=VisionCfg)
    video: VideoCfg = field(default_factory=VideoCfg)
    incident: IncidentCfg = field(default_factory=IncidentCfg)
    bridge: BridgeCfg = field(default_factory=BridgeCfg)
    align: AlignCfg = field(default_factory=AlignCfg)
    photo_slots: list[PhotoSlot] = field(default_factory=list)
    car: CarCfg = field(default_factory=CarCfg)
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


def _photo_slots(items: list[dict[str, Any]]) -> list[PhotoSlot]:
    out = []
    for it in items:
        c = it["center_cm"]
        out.append(PhotoSlot(slot=int(it["slot"]), name=str(it.get("name", f"S{it['slot']}")),
                             center_cm=(float(c[0]), float(c[1])),
                             corners_cm=[(float(p[0]), float(p[1])) for p in it.get("corners_cm", [])]))
    return out


def _car(table: dict[str, Any] | None) -> CarCfg:
    c = _sub(CarCfg, table)
    st = c.mock_start_cm
    return replace(c, mock_start_cm=(float(st[0]), float(st[1])))


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
        fixed_alt=bool(lim.get("fixed_alt", False)),
    )
    zones = [
        NoFlyZone(name=str(z.get("name", "")), polygon_cm=[(float(p[0]), float(p[1])) for p in z["polygon_cm"]])
        for z in raw.get("no_fly_zones", [])
    ]
    real_defaults = TelloCfg(go_axes="sdk", keepalive_s=5.0, ground_h_cm=10.0, ground_s=1.0, heading_from_vision=True,
                             alt_source="tof", face_target=True)
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
        vision=_sub(VisionCfg, raw.get("vision")),
        video=_sub(VideoCfg, raw.get("video")),
        incident=_sub(IncidentCfg, raw.get("incident")),
        bridge=_sub(BridgeCfg, raw.get("bridge")),
        align=_sub(AlignCfg, raw.get("align")),
        photo_slots=_photo_slots(raw.get("photo_slots", [])),
        car=_car(raw.get("car")),
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
    allowed = {"mock": ("estimate", "vision"), "sim": ("estimate", "sim_truth", "vision"),
               "real": ("estimate", "mission_pad", "vision")}
    for name, ok in allowed.items():
        src = getattr(s, name).pos_source
        if src not in ok:
            errs.append(f"[{name}] pos_source '{src}'는 {' | '.join(ok)} 중 하나여야 합니다")
    if s.vision.stale_s <= 0 or s.vision.margin_m < 0:
        errs.append("[vision] stale_s > 0, margin_m >= 0 이어야 합니다")
    if s.bridge.default_mode not in VISION_MODES:
        errs.append(f"[bridge] default_mode는 {' | '.join(VISION_MODES)} 중 하나여야 합니다")
    if s.video.max_fps <= 0 or s.video.stale_s <= 0 or s.video.max_frame_kb <= 0:
        errs.append("[video] max_fps, stale_s, max_frame_kb는 0보다 커야 합니다")
    if s.incident.keep < 1 or s.incident.max_photo_kb <= 0:
        errs.append("[incident] keep >= 1, max_photo_kb > 0 이어야 합니다")
    if len({p.slot for p in s.photo_slots}) != len(s.photo_slots):
        errs.append("[[photo_slots]] slot 번호가 겹칩니다")
    for spec in (s.sim_calibration.map_x_from, s.sim_calibration.map_y_from):
        if spec not in ("+x", "-x", "+z", "-z"):
            errs.append(f"[sim_calibration] 축 설정 '{spec}'이 잘못됐습니다 (+x -x +z -z)")
    c = s.car
    if c.mode not in ("ble", "mock", "off"):
        errs.append("[car] mode는 ble | mock | off 중 하나여야 합니다")
    if not (0 < c.max_speed_mm_s <= 200 and 0 < c.max_omega_rad_s <= 3 and 0 < c.route_speed_mm_s <= c.max_speed_mm_s):
        errs.append("[car] 속도 값이 이상합니다 (0 < route_speed_mm_s <= max_speed_mm_s <= 200, max_omega_rad_s <= 3)")
    if c.max_points < 2 or c.chunk_bytes < 1 or c.drive_timeout_s <= 0:
        errs.append("[car] max_points >= 2, chunk_bytes >= 1, drive_timeout_s > 0 이어야 합니다")
    if errs:
        raise ValueError("settings.toml 오류: " + "; ".join(errs))
