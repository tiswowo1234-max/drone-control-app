"""config/settings.toml 읽기 — 연결 프로그램이 쓰는 값만 모아 둔다 (숫자를 코드에 직접 쓰지 않는다)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

VISION_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = VISION_DIR.parent
DEFAULT_SETTINGS = PROJECT_DIR / "config" / "settings.toml"
TEAMMATE_DIR = VISION_DIR / "teammate"
MODES = ("esp32", "tt_down")


@dataclass(frozen=True)
class Slot:
    slot: int
    name: str
    center_m: tuple[float, float]
    corners_m: tuple[tuple[float, float], ...]  # settings.toml 순서 그대로 (조원 slots.json과 같음)
    up_xy: tuple[float, float] | None = None    # 조원 slots.json의 up_xy (사진 위쪽 방향, 지도 기준 단위 벡터)


@dataclass(frozen=True)
class UgvConfig:
    """[ugv] UGV 따라가기 (설정 파일에 섹션이 없으면 꺼진 상태)."""
    enabled: bool = False
    tag_id: int = 100
    tag_size_m: float = 0.08
    height_m: float = 0.15
    tag_yaw_deg: float = 0.0
    follow_distance_m: float = 0.12
    max_tilt_deg: float = 25.0
    log_every_s: float = 1.0
    max_height_err_m: float = 0.08


@dataclass(frozen=True)
class Settings:
    map_w_m: float
    map_h_m: float
    home_m: tuple[float, float]
    default_alt_m: float
    geo_corners: dict  # sw/se/nw/ne -> (lat, lon)
    # [video]
    send_fps: float
    send_width_px: int
    jpeg_quality: int
    max_frame_kb: int
    # [incident]
    method: str
    min_slot_px: float
    slot_margin_px: float
    still_s: float
    still_cm: float
    crop_width_px: int
    report_cooldown_s: float
    same_place_m: float
    max_photo_kb: int
    detect: bool
    # [bridge]
    server_url: str
    default_mode: str
    pose_send_hz: float
    synthetic_camera: str
    esp32_camera_url: str
    esp32_localization: str
    esp32_width_px: int
    esp32_height_px: int
    esp32_hfov_deg: float | None  # 설정에 없으면 None (요청 사항: 병합 세션에 키 추가 요청)
    # esp32 색·조명 보정 (조원 실물 XIAO 시험값, start_xiao_camera.ps1 -Localize와 같은 구성)
    esp32_hue_shift: int
    esp32_hue_band_width: int
    esp32_hue_tolerance: int
    esp32_saturation_tolerance: int
    esp32_value_tolerance: int
    esp32_illumination: str
    esp32_shadow_aware: bool
    tt_video_url: str
    tt_width_px: int
    tt_height_px: int
    tt_crop_top_left: bool
    tt_tag_roi: float
    extra_tags: bool
    tt_use_imu: bool
    tt_pitch_sign: float
    tt_roll_sign: float
    tt_tof_check_cm: float
    tt_tof_offset_cm: float
    drone_state_max_age_s: float
    tt_mount_yaw_deg: float
    esp32_mount_yaw_deg: float
    video_forward_up: bool
    tag_log: bool
    tag_log_every_s: float
    tilt_log_every_s: float
    tt_hfov_deg: float
    tt_camera_file: str
    photo_search_period_s: float
    slots: tuple[Slot, ...] = field(default_factory=tuple)
    source: Path | None = None
    ugv: UgvConfig = field(default_factory=UgvConfig)

    def tt_camera_path(self) -> Path | None:
        if not self.tt_camera_file:
            return None
        p = Path(self.tt_camera_file)
        return p if p.is_absolute() else PROJECT_DIR / p

    def mount_yaw(self, mode: str) -> float:
        """카메라 장착 각도: 드론 앞쪽 방향 = 카메라 화면 위쪽 방향 + 이 값 (도, 시계 방향 +)."""
        return self.esp32_mount_yaw_deg if mode == "esp32" else self.tt_mount_yaw_deg

    def frame_size(self, mode: str) -> tuple[int, int]:
        return (self.esp32_width_px, self.esp32_height_px) if mode == "esp32" else (self.tt_width_px, self.tt_height_px)


def _slots_up(teammate_slots: Path) -> dict[int, tuple[float, float]]:
    """조원 slots.json에서 슬롯별 up_xy (settings.toml에는 없음)."""
    import json
    try:
        doc = json.loads(teammate_slots.read_text(encoding="utf-8"))
        return {int(s["slot"]): tuple(map(float, s["up_xy"])) for s in doc["slots"]}
    except (OSError, ValueError, KeyError):
        return {}


def load(path: Path | str = DEFAULT_SETTINGS) -> Settings:
    path = Path(path)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    m, home, lim, geo = raw["map"], raw["home"], raw["limits"], raw["geo"]
    video, inc, br = raw["video"], raw["incident"], raw["bridge"]
    ups = _slots_up(TEAMMATE_DIR / "data" / "maps" / "slots.json")
    slots = []
    for s in raw.get("photo_slots", []):
        n = int(s["slot"])
        slots.append(Slot(n, str(s["name"]), (s["center_cm"][0] / 100, s["center_cm"][1] / 100),
                          tuple((c[0] / 100, c[1] / 100) for c in s["corners_cm"]), ups.get(n)))
    mode = str(br["default_mode"])
    if mode not in MODES:
        raise ValueError(f"[bridge] default_mode는 {MODES} 중 하나여야 합니다: {mode}")
    return Settings(
        map_w_m=m["width_cm"] / 100, map_h_m=m["height_cm"] / 100,
        home_m=(home["x_cm"] / 100, home["y_cm"] / 100),
        default_alt_m=lim["default_alt_cm"] / 100,
        geo_corners={k: (geo[f"{k}_lat"], geo[f"{k}_lon"]) for k in ("sw", "se", "nw", "ne")},
        send_fps=float(video["send_fps"]), send_width_px=int(video["send_width_px"]),
        jpeg_quality=int(video["jpeg_quality"]), max_frame_kb=int(video["max_frame_kb"]),
        method=str(inc["method"]), min_slot_px=float(inc["min_slot_px"]), slot_margin_px=float(inc["slot_margin_px"]),
        still_s=float(inc["still_s"]), still_cm=float(inc["still_cm"]), crop_width_px=int(inc["crop_width_px"]),
        report_cooldown_s=float(inc["report_cooldown_s"]), same_place_m=float(inc["same_place_m"]),
        max_photo_kb=int(inc["max_photo_kb"]), detect=bool(inc.get("detect", False)),
        server_url=str(br["server_url"]).rstrip("/"), default_mode=mode, pose_send_hz=float(br["pose_send_hz"]),
        synthetic_camera=str(br["synthetic_camera"]), esp32_camera_url=str(br["esp32_camera_url"]),
        esp32_localization=str(br["esp32_localization"]),
        esp32_width_px=int(br["esp32_width_px"]), esp32_height_px=int(br["esp32_height_px"]),
        esp32_hfov_deg=float(br["esp32_hfov_deg"]) if "esp32_hfov_deg" in br else None,
        esp32_hue_shift=int(br.get("esp32_hue_shift", 0)),
        esp32_hue_band_width=int(br.get("esp32_hue_band_width", 0)),
        esp32_hue_tolerance=int(br.get("esp32_hue_tolerance", 7)),
        esp32_saturation_tolerance=int(br.get("esp32_saturation_tolerance", 55)),
        esp32_value_tolerance=int(br.get("esp32_value_tolerance", 95)),
        esp32_illumination=str(br.get("esp32_illumination", "none")),
        esp32_shadow_aware=bool(br.get("esp32_shadow_aware", False)),
        tt_video_url=str(br["tt_video_url"]), tt_width_px=int(br["tt_width_px"]), tt_height_px=int(br["tt_height_px"]),
        tt_crop_top_left=bool(br.get("tt_crop_top_left", True)),
        tt_tag_roi=min(1.0, max(0.1, float(br.get("tt_tag_roi", 1.0)))),
        extra_tags=bool(br.get("extra_tags", False)),
        tt_use_imu=bool(br.get("tt_use_imu", True)),
        tt_pitch_sign=float(br.get("tt_pitch_sign", 1.0)),
        tt_roll_sign=float(br.get("tt_roll_sign", 1.0)),
        tt_tof_check_cm=float(br.get("tt_tof_check_cm", 0.0)),
        tt_tof_offset_cm=float(br.get("tt_tof_offset_cm", 0.0)),
        drone_state_max_age_s=float(br.get("drone_state_max_age_s", 0.3)),
        tt_mount_yaw_deg=float(br.get("tt_mount_yaw_deg", 0.0)),
        esp32_mount_yaw_deg=float(br.get("esp32_mount_yaw_deg", 0.0)),
        video_forward_up=bool(br.get("video_forward_up", True)),
        tag_log=bool(br.get("tag_log", False)),
        tag_log_every_s=float(br.get("tag_log_every_s", 0.5)),
        tilt_log_every_s=float(br.get("tilt_log_every_s", 1.0)),
        tt_hfov_deg=float(br["tt_hfov_deg"]), tt_camera_file=str(br.get("tt_camera_file", "")),
        photo_search_period_s=float(br["photo_search_period_s"]),
        slots=tuple(slots), source=path, ugv=_ugv(raw.get("ugv")),
    )


def _ugv(u: dict | None) -> UgvConfig:
    if not u:
        return UgvConfig()
    cfg = UgvConfig(
        enabled=bool(u.get("enabled", True)), tag_id=int(u["tag_id"]),
        tag_size_m=float(u["tag_size_cm"]) / 100, height_m=float(u.get("height_cm", 0.0)) / 100,
        tag_yaw_deg=float(u.get("tag_yaw_deg", 0.0)),
        follow_distance_m=float(u.get("follow_distance_cm", 12.0)) / 100,
        max_tilt_deg=float(u.get("max_tilt_deg", 25.0)), log_every_s=float(u.get("log_every_s", 1.0)),
        max_height_err_m=float(u.get("max_height_err_cm", 8.0)) / 100)
    if cfg.tag_size_m <= 0:
        raise ValueError("[ugv] tag_size_cm는 0보다 커야 합니다")
    return cfg
