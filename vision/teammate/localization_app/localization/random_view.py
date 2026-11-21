"""Paired blur experiments using the same simulated exposure and global matcher."""

from dataclasses import asdict, dataclass
import json
import math
from numbers import Integral
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from .camera_simulator import CameraConfig, Pose, SyntheticCamera
from .map_loader import MapRaster
from .map_matching import Estimate, MapMatcher, angle_error
from simulation.lighting import LIGHTING_CASES, simulate_lighting


@dataclass(frozen=True)
class BlurConfig:
    kernel_size: int = 9
    sigma: float = 2.0

    def __post_init__(self):
        if (not isinstance(self.kernel_size, Integral) or isinstance(self.kernel_size, bool)
                or self.kernel_size < 1 or self.kernel_size % 2 == 0):
            raise ValueError("blur kernel size must be a positive odd integer")
        if not math.isfinite(self.sigma) or self.sigma < 0:
            raise ValueError("blur sigma must be finite and nonnegative")

    def apply(self, frame: np.ndarray) -> np.ndarray:
        """Blur a BGR image. Sigma 0 uses the OpenCV kernel-derived sigma."""
        return cv2.GaussianBlur(frame, (self.kernel_size, self.kernel_size), sigmaX=self.sigma)


@dataclass(frozen=True)
class ViewSample:
    pose: Pose
    frame: np.ndarray
    coverage: float
    attempts: int
    seed: int
    index: int
    footprint_xy_m: np.ndarray


@dataclass(frozen=True)
class ViewScore:
    estimate: Estimate
    processing_ms: float
    position_error_m: float | None
    yaw_error_deg: float | None


def score_estimate(estimate: Estimate, truth: Pose, elapsed_ms: float) -> ViewScore:
    """Measure position in meters and wrapped yaw in degrees for accepted estimates."""
    if not estimate.success:
        return ViewScore(estimate, elapsed_ms, None, None)
    return ViewScore(estimate, elapsed_ms,
                     math.hypot(estimate.x_m - truth.x_m, estimate.y_m - truth.y_m),
                     angle_error(estimate.yaw_deg, truth.yaw_deg))


@dataclass(frozen=True)
class ViewComparison:
    sample: ViewSample
    blur: BlurConfig
    blurred_frame: np.ndarray
    original: ViewScore
    blurred: ViewScore
    lighting_case: str | None = None

    @property
    def variant_label(self):
        return "Blurred" if self.lighting_case is None else f"{self.lighting_case.capitalize()} + blur"

    def record(self) -> dict:
        """Return JSON-safe metrics; missing estimates are represented by null."""
        record = {
            "seed": self.sample.seed, "sample_index": self.sample.index,
            "sampling_attempts": self.sample.attempts, "building_coverage": self.sample.coverage,
            "ground_truth": asdict(self.sample.pose), "blur": asdict(self.blur),
            "footprint_xy_m": self.sample.footprint_xy_m.tolist(),
            "original": asdict(self.original), "blurred": asdict(self.blurred),
        }
        if self.lighting_case:
            record["lighting_case"] = self.lighting_case
        return record


class RandomViewExperiment:
    def __init__(self, raster: MapRaster, config: CameraConfig, matcher: MapMatcher, *,
                 seed: int | None = None, min_building_coverage: float = 0.05,
                 max_attempts: int = 100, lighting_case: str | None = None):
        if not 0 <= min_building_coverage <= 1:
            raise ValueError("minimum building coverage must be in [0, 1]")
        if not isinstance(max_attempts, Integral) or max_attempts < 1:
            raise ValueError("maximum sampling attempts must be a positive integer")
        if lighting_case is not None and lighting_case not in LIGHTING_CASES:
            raise ValueError("unknown synthetic lighting case")
        self.camera = SyntheticCamera(raster, config)
        self.matcher = matcher
        self.buildings = (raster.labels() != 0).astype(np.uint8)
        self.min_coverage = min_building_coverage
        self.max_attempts = max_attempts
        self.lighting_case = lighting_case
        self.reseed(seed)

    def reseed(self, seed: int | None):
        """Restart the independent pose and camera-noise streams."""
        sequence = np.random.SeedSequence(seed)
        self.seed = int(sequence.entropy)
        pose_seed, noise_seed = sequence.spawn(2)
        self.rng = np.random.default_rng(pose_seed)
        self.camera.rng = np.random.default_rng(noise_seed)
        self.sample_index = 0

    def sample(self) -> ViewSample:
        """Uniformly propose poses and reject those below geometric building coverage."""
        raster = self.camera.raster
        width, height = self.camera.config.width_px, self.camera.config.height_px
        corners = np.array([[-0.5, -0.5], [width - 0.5, -0.5],
                            [width - 0.5, height - 0.5], [-0.5, height - 0.5]])
        best_coverage = 0.0
        for attempt in range(1, self.max_attempts + 1):
            pose = Pose(*map(float, self.rng.uniform(
                (0, 0, 0), (raster.campus.width_m, raster.campus.height_m, 360))))
            transform = self.camera.map_transform(pose)
            mask = cv2.warpAffine(self.buildings, transform, (width, height),
                                  flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
            coverage = np.count_nonzero(mask) / mask.size
            if coverage >= self.min_coverage:
                self.sample_index += 1
                footprint = raster.uv_to_xy(corners @ transform[:, :2].T + transform[:, 2])
                return ViewSample(pose, self.camera.capture(pose), coverage, attempt,
                                  self.seed, self.sample_index, footprint)
            best_coverage = max(best_coverage, coverage)
        raise ValueError(f"no view reached {self.min_coverage:.1%} building coverage in "
                         f"{self.max_attempts} attempts (best {best_coverage:.1%})")

    def _measure(self, frame: np.ndarray, pose: Pose) -> ViewScore:
        start = perf_counter()
        estimate = self.matcher.estimate(frame)
        return score_estimate(estimate, pose, (perf_counter() - start) * 1000)

    def run(self, blur: BlurConfig = BlurConfig()) -> ViewComparison:
        """Compare one seeded random frame and its blurred copy independently."""
        sample = self.sample()
        blurred = self._variant(sample, blur)
        return ViewComparison(sample, blur, blurred, self._measure(sample.frame, sample.pose),
                              self._measure(blurred, sample.pose), self.lighting_case)

    def reblur(self, previous: ViewComparison, blur: BlurConfig) -> ViewComparison:
        """Re-evaluate only the new blur, retaining the exact original and its result."""
        sample = previous.sample
        blurred = self._variant(sample, blur)
        return ViewComparison(sample, blur, blurred, previous.original,
                              self._measure(blurred, sample.pose), self.lighting_case)

    def _variant(self, sample, blur):
        frame = sample.frame
        if self.lighting_case is not None:
            phase = (sample.index % 8) / 7
            frame, _ = simulate_lighting(frame, self.lighting_case, phase=phase, seed=sample.seed)
        return blur.apply(frame)


def summarize(scores: list[ViewScore]) -> dict:
    """Count successes over all inputs and compute errors on accepted inputs."""
    accepted = [score for score in scores if score.estimate.success]
    return {
        "accepted": len(accepted), "total": len(scores),
        "success_rate": len(accepted) / len(scores) if scores else None,
        "rmse_m": math.sqrt(sum(s.position_error_m ** 2 for s in accepted) / len(accepted))
        if accepted else None,
        "mean_yaw_error_deg": sum(s.yaw_error_deg for s in accepted) / len(accepted)
        if accepted else None,
        "mean_processing_ms": sum(s.processing_ms for s in scores) / len(scores) if scores else None,
    }


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
