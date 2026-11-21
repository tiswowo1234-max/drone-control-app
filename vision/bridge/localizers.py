"""모드별 위치 계산 — 조원 코드를 그대로 부르고, 결과를 명세 기준(m, yaw 시계)으로 바꾼다.

- tt_down: incident_detection `Localizer`(AprilTag PnP) + `PoseTracker`(칼만)
- esp32  : localization_app `LiveLocalization`(건물·도로 색 정합 + AprilTag 융합, 칼만)
모드를 바꾸면 새 객체를 만든다(이전 모드의 추적 상태를 버림). reset()도 같은 일을 한다.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import time
import warnings
from dataclasses import dataclass

import cv2
import numpy as np

from . import teammate
from .config import Settings
from .geometry import CameraView, nadir_view, yaw_from_ccw, yaw_from_compass

log = logging.getLogger("bridge.localizers")


@dataclass
class PoseResult:
    ok: bool
    x_m: float | None = None
    y_m: float | None = None
    z_m: float | None = None
    yaw_deg: float | None = None   # 명세 기준 (지도 위쪽 0, 시계 +)
    confidence: float = 0.0
    status: str = ""               # 사람이 읽는 상태 (tracking / coasting / ...)
    measured: bool = False         # 이 장에서 실제로 측정했는지 (아니면 예측값)
    view: CameraView | None = None  # 슬롯 투영용: 이 장이 지도 어디를 보는지
    tags: list | None = None        # 이 장에서 본 AprilTag (photo_finder용)
    quads: list | None = None
    ms: float = 0.0


def _conf_from_std(std_m: float, scale_m: float = 0.1) -> float:
    """위치 표준편차 → 0~1 신뢰도 (std 0 → 1, scale_m 이상 → 0.05)."""
    if std_m is None or not math.isfinite(std_m):
        return 0.05
    return float(np.clip(1.0 - std_m / scale_m, 0.05, 1.0))


def to_gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------- tt_down

class TtDownLocalizer:
    mode = "tt_down"

    def __init__(self, settings: Settings, upscale: int = 1, level_view: bool = False):
        from camera_model import CameraModel
        from localizer import Localizer, PoseTracker, TagMap
        from settings import CameraConfig, LocalizerConfig
        self.s, self.upscale, self.level_view = settings, max(1, int(upscale)), level_view
        cam_path = settings.tt_camera_path()
        if cam_path is not None and cam_path.exists():
            base = CameraModel.load(cam_path, warn=False)
            self.camera_note = f"보정 파일 {cam_path.name}"
        else:
            if cam_path is not None:
                log.warning("[tt_down] tt_camera_file %s 이 없어 시야각 %.0f°로 추정합니다", cam_path, settings.tt_hfov_deg)
            base = CameraModel.from_fov(CameraConfig(settings.tt_width_px, settings.tt_height_px, settings.tt_hfov_deg))
            self.camera_note = f"시야각 추정 {settings.tt_hfov_deg:g}°"
        self._base = base
        self._CameraModel, self._Localizer, self._PoseTracker = CameraModel, Localizer, PoseTracker
        self.cfg = LocalizerConfig(tags_file=teammate.MAPS_DIR / "apriltag_map.json",
                                   slots_file=teammate.MAPS_DIR / "slots.json")
        self.tag_map = TagMap.load(self.cfg.tags_file)
        self._loc_by_size: dict[tuple[int, int], object] = {}
        self.tracker = PoseTracker()

    def reset(self) -> None:
        self.tracker = self._PoseTracker()

    def _localizer(self, w: int, h: int):
        """(w,h) = 원본 화면 크기. upscale이면 확대한 화면용 카메라 모델."""
        key = (w, h)
        if key not in self._loc_by_size:
            cam = self._base.for_size(w, h)
            if self.upscale > 1:
                u = self.upscale
                K = cam.K.copy()
                K[:2, :2] *= u
                K[0, 2], K[1, 2] = (K[0, 2] + 0.5) * u - 0.5, (K[1, 2] + 0.5) * u - 0.5
                cam = self._CameraModel(K, cam.dist, w * u, h * u, cam.source + f", x{u}", cam.hfov_deg)
            self._loc_by_size[key] = (self._Localizer(cam, self.tag_map, self.cfg), cam)
        return self._loc_by_size[key]

    def process(self, image: np.ndarray, t: float) -> PoseResult:
        t0 = time.perf_counter()
        gray = to_gray(image)
        h, w = gray.shape
        loc, cam = self._localizer(w, h)
        g = cv2.resize(gray, None, fx=self.upscale, fy=self.upscale, interpolation=cv2.INTER_CUBIC) \
            if self.upscale > 1 else gray
        tags, quads = loc.detect_all(g)
        pose = loc.solve(tags, self.tracker.position)
        tracked = self.tracker.update(t, pose)
        if self.upscale > 1:  # 원본 화면 좌표로 되돌림 (슬롯·사진 찾기용)
            u = self.upscale
            for tg in tags:
                tg.corners = (tg.corners + 0.5) / u - 0.5
            quads = [(q + 0.5) / u - 0.5 for q in quads]
        res = PoseResult(False, tags=tags, quads=quads)
        if tracked is None or tracked.status == "lost":
            res.status = "위치 없음 (보이는 태그 없음)" if pose is None else "추적 재시작"
            res.ms = (time.perf_counter() - t0) * 1000
            return res
        # 슬롯 투영용 자세: 칼만 추적 위치·방향 + 수평 카메라 가정 (조원 assumed_pose).
        # 한 장의 PnP 자세는 작은 태그 하나일 때 기울기가 크게 틀려 투영이 수십 px 튀었다(시험 결과, README 참고).
        where = loc.assumed_pose(tracked.position, tracked.heading_deg) if self.level_view else (
            pose if pose is not None and not tracked.rejected else loc.assumed_pose(tracked.position, tracked.heading_deg))
        # 슬롯 투영은 원본 화면 기준 카메라 모델로
        K0 = self._base.for_size(w, h)
        view = CameraView(K0.K, K0.dist, where.R, np.asarray(where.tvec, float).reshape(3), w, h)
        x, y, z = (float(v) for v in tracked.position)
        std = float(np.max(tracked.position_std_m[:2]))
        conf = _conf_from_std(std)
        if tracked.status != "tracking":
            conf = min(conf, 0.3)
        res.ok, res.x_m, res.y_m, res.z_m = True, x, y, z
        res.yaw_deg = yaw_from_compass(tracked.heading_deg)
        res.confidence, res.measured, res.view = conf, tracked.status == "tracking", view
        res.status = tracked.status + (f" 태그 {pose.tag_ids}" if pose is not None else "")
        res.ms = (time.perf_counter() - t0) * 1000
        return res


# ---------------------------------------------------------------- esp32

class Esp32Localizer:
    mode = "esp32"
    SENDABLE = ("TRACKING", "PREDICT_ONLY")

    def __init__(self, settings: Settings, synthetic: bool = False):
        from localization.apriltag_localization import AprilTagConfig, AprilTagLocalizer, TagMap
        from localization.camera_preprocessing import DEFAULT_CAMERA_CONFIG, PhysicalCameraConfig
        from localization.camera_simulator import CameraConfig, footprint_width
        from localization.image_processing import ColorConfig
        from localization.map_loader import CampusMap
        from localization.map_matching import MapMatcher, SearchConfig
        self.s = settings
        mode = settings.esp32_localization
        if mode not in ("map", "apriltag", "fusion"):
            raise ValueError(f"[bridge] esp32_localization은 map/apriltag/fusion: {mode}")
        self.loc_mode = mode
        # 조원 카메라 설정(방향·렌즈 보정). 크기는 settings.toml 값을 쓴다
        pcfg = PhysicalCameraConfig.load(DEFAULT_CAMERA_CONFIG)
        self.hfov = settings.esp32_hfov_deg if settings.esp32_hfov_deg else CameraConfig().hfov_deg
        self.hfov_known = settings.esp32_hfov_deg is not None
        span = pcfg.nominal_span_m or footprint_width(settings.default_alt_m, self.hfov)
        self.pcfg = dataclasses.replace(pcfg, size=(settings.esp32_width_px, settings.esp32_height_px), nominal_span_m=span)
        if pcfg.rotation or pcfg.flip_horizontal or pcfg.flip_vertical:
            log.warning("[esp32] camera_config.yaml에 회전/뒤집기가 있습니다. 슬롯 사진 자르기는 회전 없는 화면을 가정합니다")
        self.span = span
        campus = CampusMap.load()
        # 고도 고정(80cm)이어도 실제 높이는 흔들리므로 자동 배율 탐색을 켠다. 건물+도로 적응형 정합(조원 권장 --hybrid-matching)
        # 색 보정: 조원이 실물 XIAO + 지도 표본에서 찾은 값(설정 [bridge] esp32_hue_* 등).
        # Hue 이동은 **실물 카메라의 색 틀어짐**을 되돌리는 값이라, 색이 정확한 합성 카메라에는 쓰지 않는다
        # (합성 화면 시험: Hue 이동 3을 넣으면 36장 중 23장만 성공, 빼면 36/36. 묶음·채도·조명 보정은 영향 없음).
        hue_shift = 0 if synthetic else settings.esp32_hue_shift
        if synthetic and settings.esp32_hue_shift:
            log.info("[esp32] 합성 카메라라 esp32_hue_shift(%d)는 쓰지 않습니다 (실물 카메라 전용)", settings.esp32_hue_shift)
        self.colors = ColorConfig(settings.esp32_hue_tolerance, settings.esp32_saturation_tolerance,
                                  settings.esp32_value_tolerance, hue_shift=hue_shift,
                                  hue_band_width=settings.esp32_hue_band_width)
        self.matcher = MapMatcher(campus, SearchConfig(nominal_span_m=span, auto_scale=True, matching_mode="adaptive"),
                                  self.colors)
        # 조명·그림자 보정: 조원 start_xiao_camera.ps1 -Localize 기본(--illumination-method combined --shadow-aware)
        from localization.illumination_correction import IlluminationConfig
        from localization.lighting_pipeline import LightingProcessor
        method = settings.esp32_illumination
        self.lighting = (LightingProcessor(IlluminationConfig(method), shadow_aware=settings.esp32_shadow_aware)
                         if method != "none" or settings.esp32_shadow_aware else None)
        self.tag_localizer = (AprilTagLocalizer(TagMap.load(campus), 1.0, AprilTagConfig())
                              if mode != "map" else None)  # 1.0 = 인쇄 지도 폭 2.5 m / 지도 2.5 m
        self.live = None
        self.frame_id = 0
        self.last_t = None
        self.reset()

    def reset(self) -> None:
        from localization.camera_preprocessing import CameraPreprocessor
        from localization.live_localization import LiveLocalization
        self.live = LiveLocalization(self.matcher, CameraPreprocessor(self.pcfg), localization_mode=self.loc_mode,
                                     tag_localizer=self.tag_localizer, lighting=self.lighting)
        self.last_t = None

    def process(self, image: np.ndarray, t: float) -> PoseResult:
        from localization.camera_interface import CameraFrame
        t0 = time.perf_counter()
        bgr = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if self.last_t is not None and t <= self.last_t:  # 조원 추적기는 시각이 꼭 늘어나야 한다
            t = self.last_t + 1e-3
        self.last_t = t
        self.frame_id += 1
        r = self.live.process(CameraFrame(bgr, t, self.frame_id, "bridge", received_at=time.perf_counter()))
        tr = r.tracking
        res = PoseResult(False, tags=None)
        res.ms = (time.perf_counter() - t0) * 1000
        if tr is None or tr.xy_m is None or tr.yaw_deg is None or tr.state.value not in self.SENDABLE:
            res.status = (tr.state.value if tr else "위치 없음") + (f" ({tr.reason})" if tr and tr.reason else "")
            return res
        h, w = bgr.shape[:2]
        x, y = (float(v) for v in tr.xy_m)
        yaw = yaw_from_ccw(tr.yaw_deg)
        span = float(tr.span_m) if tr.span_m else self.span
        z = span / (2 * math.tan(math.radians(self.hfov) / 2))
        res.ok, res.x_m, res.y_m, res.yaw_deg = True, x, y, yaw
        res.z_m = z if self.hfov_known else None  # 시야각을 모르면 높이는 보내지 않는다
        res.view = nadir_view(x, y, yaw, span, w, h, z)
        res.measured = tr.state.value == "TRACKING"
        conf = _conf_from_std(tr.sigma_m)
        res.confidence = conf if res.measured else min(conf, 0.3)
        res.status = f"{tr.state.value} {tr.observation_source or ''}".strip()
        return res


def make_localizer(mode: str, settings: Settings, tt_upscale: int = 1, synthetic: bool = False):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if mode == "tt_down":
            return TtDownLocalizer(settings, tt_upscale)
        if mode == "esp32":
            return Esp32Localizer(settings, synthetic)
    raise ValueError(mode)
