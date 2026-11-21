"""AprilTag localization with calibrated PnP or a 2D fit for near-nadir input."""

from collections import Counter
from dataclasses import dataclass, replace
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path

import cv2
import numpy as np

from .apriltag_detector import DICTIONARIES
from .localization_fusion import LocalizationObservation, yaw_difference
from .paths import MAPS_DIR


DEFAULT_TAG_MAP = MAPS_DIR / "apriltag_map.json"


@dataclass(frozen=True)
class Tag:
    corners: np.ndarray
    paper_corners: np.ndarray


@dataclass(frozen=True)
class TagMap:
    family: str
    size_m: float
    tags: dict[int, Tag]
    map_size: tuple[float, float]

    @classmethod
    def load(cls, campus, path=DEFAULT_TAG_MAP):
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        size = (campus.width_m, campus.height_m)
        if (doc["coordinate_frame"] != "kaist_map" or tuple(doc["map_size_m"]) != size
                or campus.source is None
                or doc["reference_sha256"] != hashlib.sha256(campus.source.read_bytes()).hexdigest()):
            raise ValueError("AprilTag layout does not match the selected base map")
        family, side, paper = doc["family"], float(doc["tag_size_m"]), float(doc["paper_size_m"])
        if (family not in DICTIONARIES or doc["opencv_dictionary"] != DICTIONARIES[family]
                or not 0 < side <= paper <= min(size)):
            raise ValueError("invalid AprilTag family or model tag/paper size")
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICTIONARIES[family]))
        tags = {}
        for entry in doc["tags"]:
            tag_id = entry["id"]
            if type(tag_id) is not int or not 0 <= tag_id < len(dictionary.bytesList) or tag_id in tags:
                raise ValueError(f"invalid or duplicate AprilTag ID {tag_id}")
            corners = np.asarray(entry["corners_xy_m"], dtype=float)
            paper_corners = np.asarray(entry["paper_corners_xy_m"], dtype=float)
            for quad, length in ((corners, side), (paper_corners, paper)):
                contour = quad.astype(np.float32)
                if (quad.shape != (4, 2) or not np.isfinite(quad).all()
                        or np.any(quad < 0) or np.any(quad > size)
                        or not cv2.isContourConvex(contour) or cv2.contourArea(contour, oriented=True) >= 0
                        or not np.allclose(np.linalg.norm(quad - np.roll(quad, 1, axis=0), axis=1),
                                           length, atol=2e-6)
                        or not np.allclose(np.linalg.norm(quad[:2] - quad[2:], axis=1),
                                           math.sqrt(2) * length, atol=2e-6)):
                    raise ValueError(f"invalid corners for AprilTag {tag_id}")
            center = corners.mean(axis=0)
            edge = corners[1] - corners[0]
            rotation = math.degrees(math.atan2(edge[1], edge[0]))
            if (not np.allclose(center, entry["center_xy_m"], atol=2e-6)
                    or not np.allclose(paper_corners, center + (corners - center) * paper / side, atol=4e-6)
                    or abs(yaw_difference(rotation, entry["yaw_deg"])) > 0.01):
                raise ValueError(f"inconsistent center, paper or rotation for AprilTag {tag_id}")
            tags[tag_id] = Tag(corners, paper_corners)
        if not tags:
            raise ValueError("AprilTag layout is empty")
        return cls(family, side, tags, size)

    def paper_mask(self, detections, shape):
        mask = np.zeros(shape[:2], np.uint8)
        for detection in detections:
            corners = np.asarray(detection.corners_px, np.float32)
            if corners.shape != (4, 2) or not np.isfinite(corners).all() or not cv2.isContourConvex(corners):
                continue
            tag = self.tags.get(detection.tag_id) if detection.tag_family == self.family else None
            if tag is not None:
                transform = cv2.getPerspectiveTransform(tag.corners.astype(np.float32), corners)
                paper = cv2.perspectiveTransform(tag.paper_corners[None].astype(np.float32), transform)[0]
                if np.isfinite(paper).all() and np.max(np.abs(paper - corners)) < max(shape[:2]) / 2:
                    corners = paper
            cv2.fillConvexPoly(mask, np.rint(corners).astype(np.int32), 1)
        return cv2.dilate(mask, np.ones((3, 3), np.uint8))


@dataclass(frozen=True)
class AprilTagConfig:
    min_side_px: float = 14.0
    edge_margin_px: int = 3
    max_side_ratio: float = 2.8
    max_tag_error_px: float = 2.5
    max_pose_error_px: float = 2.0
    pixel_sigma: float = 0.5
    position_floor_m: float = 0.015
    max_position_sigma_m: float = 0.13
    yaw_floor_deg: float = 3.0
    max_tilt_deg: float = 55.0
    planar_position_floor_m: float = 0.08
    planar_yaw_floor_deg: float = 18.0


@dataclass
class _Pose:
    center: np.ndarray
    rotation: np.ndarray  # map -> camera
    rvec: np.ndarray
    tvec: np.ndarray  # centered physical map -> camera
    errors: np.ndarray
    inliers: np.ndarray


class AprilTagLocalizer:
    def __init__(self, layout, physical_per_map, config=AprilTagConfig()):
        self.layout, self.scale, self.config = layout, physical_per_map, config
        if physical_per_map is not None and (not math.isfinite(physical_per_map) or physical_per_map <= 0):
            raise ValueError("AprilTag print scale must be positive and finite")

    def solve(self, detections, size, calibration, image_axes=((1., 0.), (0., 1.))):
        """Estimate a map position from raw image corners, expressed in model meters."""
        if not detections:
            return None
        first = detections[0]
        metrics = dict(detected_ids=tuple(t.tag_id for t in detections), rejected_tags={})
        observation = LocalizationObservation("APRILTAG", first.frame_id, first.timestamp, quality_metrics=metrics)
        counts = Counter(t.tag_id for t in detections)
        usable = []
        for detection in detections:
            reason = self._quality_reason(detection, size, counts, first)
            if reason:
                metrics["rejected_tags"][detection.tag_id] = reason
            else:
                usable.append(detection)
        if not usable:
            return observation.reject("no usable tag corners")
        if calibration is None:
            return self._solve_planar(observation, usable, size, image_axes)
        if self.scale is None:
            return observation.reject("physical print width missing")

        K, dist = calibration
        image = np.asarray([t.corners_px for t in usable], dtype=float)
        points = np.asarray([self.layout.tags[t.tag_id].corners for t in usable])
        origin = points.reshape(-1, 2).mean(axis=0)
        model = np.pad((points - origin) * self.scale, ((0, 0), (0, 0), (0, 1)))
        try:
            fit, reason = self._consensus(model, image, K, dist, usable)
            if fit is None:
                return observation.reject(reason)
            x, y = fit.center[:2] / self.scale + origin
            # A small overhang allows the lens to observe tags along an edge of the print.
            if np.any(np.array([x, y]) < -0.15) or np.any(np.array([x, y]) > np.array(self.layout.map_size) + 0.15):
                return observation.reject("camera center outside map bounds")
            rms = float(np.sqrt(np.mean(fit.errors[fit.inliers] ** 2)))
            yaw = self._yaw(fit.rotation, image_axes)
            covariance, yaw_sigma = self._covariance(fit, model, K, dist, rms, image_axes)
            variances = np.linalg.eigvalsh(covariance)
            if not np.isfinite(variances).all() or variances[0] <= 0 or not math.isfinite(yaw_sigma):
                return observation.reject("invalid AprilTag covariance")
            sigma = math.sqrt(variances[-1])
            if sigma > self.config.max_position_sigma_m:
                return observation.reject("AprilTag position uncertainty exceeds limit")
            span, center_offset = self._horizontal_footprint(fit, K, size, image_axes)
        except (cv2.error, np.linalg.LinAlgError, ValueError) as error:
            return observation.reject(f"invalid AprilTag pose geometry ({type(error).__name__})")
        metrics.update(used_ids=tuple(t.tag_id for t, use in zip(usable, fit.inliers) if use),
                       dropped_ids=tuple(t.tag_id for t, use in zip(usable, fit.inliers) if not use),
                       reprojection_px=rms, height_m=float(fit.center[2] / self.scale),
                       height_physical_m=float(fit.center[2]), tag_size_physical_m=self.layout.size_m * self.scale,
                       tilt_deg=math.degrees(math.acos(np.clip(-fit.rotation[2, 2], -1, 1))),
                       position_sigma_m=sigma, orientation_sigma_deg=yaw_sigma,
                       image_center_offset_m=center_offset)
        # Store the optical-frame pose for same-frame overlay. Origin is in model meters.
        metrics.update(rvec=tuple(fit.rvec.ravel()), tvec=tuple(fit.tvec.ravel()), origin=tuple(origin))
        return LocalizationObservation("APRILTAG", first.frame_id, first.timestamp, float(x), float(y),
                                       yaw, span, tuple(map(tuple, covariance)), yaw_sigma, metrics)

    def _solve_planar(self, observation, detections, size, image_axes):
        """Fit one similarity across all marker corners for a nearly downward camera."""
        metrics = observation.quality_metrics
        metrics["pose_model"] = "planar"
        corners = np.asarray([self.layout.tags[tag.tag_id].corners for tag in detections])
        pixels = np.asarray([tag.corners_px for tag in detections])
        # Reflect the map's north axis into image coordinates so a complex gain
        # expresses just rotation and uniform scale, without camera intrinsics.
        model = corners[..., 0] - 1j * corners[..., 1]
        image = pixels[..., 0] + 1j * pixels[..., 1]
        model_origin, image_origin = model.mean(), image.mean()
        centered = model - model_origin
        gain = np.vdot(centered, image - image_origin) / np.vdot(centered, centered).real
        if not np.isfinite(gain) or abs(gain) <= 0:
            return observation.reject("invalid planar AprilTag scale")

        errors = np.abs(centered * gain + image_origin - image)
        rms = float(np.sqrt(np.mean(errors ** 2)))
        error_limit = min(self.config.max_pose_error_px, 0.08 * min(tag.min_side_px for tag in detections))
        score = max(0., 1. - rms / error_limit)
        metrics.update(reprojection_px=rms, corner_score=score)
        if rms > error_limit or np.sqrt(np.mean(errors ** 2, axis=1)).max() > self.config.max_tag_error_px:
            return observation.reject("tag corners disagree with a planar AprilTag fit")

        raw_center = (np.asarray(size) - 1) / 2
        center = (model_origin + (complex(*raw_center) - image_origin) / gain).conjugate()
        xy = np.array([center.real, center.imag])
        if np.any(xy < -0.15) or np.any(xy > np.asarray(self.layout.map_size) + 0.15):
            return observation.reject("planar AprilTag center outside map bounds")
        right, down = np.asarray(image_axes)
        up = (complex(*(-down)) / gain).conjugate()
        yaw = math.degrees(math.atan2(-up.real, up.imag)) % 360
        span = float(np.abs(right) @ (np.asarray(size) - 1) / abs(gain))
        # Residuals capture corner consistency, while the floor covers the
        # unmeasured lens distortion and tilt even for a pixel-perfect fit.
        sigma = max(self.config.planar_position_floor_m, 0.12 * span) + 0.03 * (1 - score)
        yaw_sigma = self.config.planar_yaw_floor_deg + 8. * (1 - score)
        if sigma > self.config.max_position_sigma_m:
            return observation.reject("planar AprilTag position uncertainty exceeds limit")
        metrics.update(used_ids=tuple(tag.tag_id for tag in detections),
                       position_sigma_m=sigma, orientation_sigma_deg=yaw_sigma)
        return replace(observation, x_m=float(xy[0]), y_m=float(xy[1]), yaw_deg=yaw, span_m=span,
                       covariance=((sigma ** 2, 0.), (0., sigma ** 2)), yaw_sigma_deg=yaw_sigma)

    def _quality_reason(self, detection, size, counts, first):
        if (detection.frame_id, detection.timestamp) != (first.frame_id, first.timestamp):
            return "different frame"
        if detection.tag_family != self.layout.family or detection.tag_id not in self.layout.tags:
            return "unknown family or ID"
        if counts[detection.tag_id] > 1:
            return "duplicate ID in frame"
        corners = np.asarray(detection.corners_px, np.float32)
        if (corners.shape != (4, 2) or not np.isfinite(corners).all()
                or not cv2.isContourConvex(corners) or cv2.contourArea(corners, oriented=True) <= 0):
            return "invalid corner geometry"
        if np.any(corners < self.config.edge_margin_px) or np.any(corners > np.asarray(size) - 1 - self.config.edge_margin_px):
            return "too close to image edge"
        side = np.linalg.norm(corners - np.roll(corners, 1, axis=0), axis=1)
        if side.min() < self.config.min_side_px:
            return "tag too small"
        if side.max() / side.min() > self.config.max_side_ratio or cv2.contourArea(corners) < .45 * side.min() ** 2:
            return "tag excessively foreshortened"
        return None

    def _candidates(self, model, image, K, dist, indices):
        obj, pixels = model[indices].reshape(-1, 3), image[indices].reshape(-1, 2)
        _, rotations, translations, _ = cv2.solvePnPGeneric(obj, pixels, K, dist, flags=cv2.SOLVEPNP_IPPE)
        for rvec, tvec in zip(rotations, translations):
            if not np.isfinite(rvec).all() or not np.isfinite(tvec).all():
                continue
            rvec, tvec = cv2.solvePnPRefineLM(obj, pixels, K, dist, rvec, tvec)
            rotation = cv2.Rodrigues(rvec)[0]
            # X_cam = R X_print + t. The optical center in the centered print frame is -Rᵀt.
            center = -rotation.T @ tvec.ravel()
            if (not np.isfinite(center).all() or center[2] <= 0
                    or -rotation[2, 2] < math.cos(math.radians(self.config.max_tilt_deg))
                    or np.any(obj @ rotation[2, :] + tvec[2, 0] <= 0)):
                continue
            projection, _ = cv2.projectPoints(model.reshape(-1, 3), rvec, tvec, K, dist)
            errors = np.linalg.norm(projection.reshape(-1, 4, 2) - image, axis=2)
            inliers = np.sqrt(np.mean(errors ** 2, axis=1)) <= self.config.max_tag_error_px
            yield _Pose(center, rotation, rvec, tvec, errors, inliers)

    def _consensus(self, model, image, K, dist, tags):
        count = len(tags)
        indices = np.arange(count)
        options = list(self._candidates(model, image, K, dist, indices))

        def rank(fit):
            return -int(fit.inliers.sum()), float(np.mean(fit.errors[fit.inliers] ** 2)) if fit.inliers.any() else math.inf

        # A tag can be correctly decoded yet pasted in the wrong spot. Pair seeds
        # recover a spatial majority without letting a bad tag dominate the all-corner fit.
        if count >= 3 and not any(fit.inliers.all() and rank(fit)[1] <= self.config.max_pose_error_px ** 2 for fit in options):
            seeds = sorted(indices, key=lambda i: tags[i].min_side_px, reverse=True)[:8]
            for pair in combinations(seeds, 2):
                options.extend(self._candidates(model, image, K, dist, list(pair)))
        minimum = 1 if count == 1 else count // 2 + 1
        qualified = [fit for fit in options if fit.inliers.sum() >= minimum]
        if not qualified:
            return None, "AprilTags lack a consistent pose"
        best = min(qualified, key=rank)
        if not best.inliers.all():
            refined = self._candidates(model, image, K, dist, best.inliers)
            qualified.extend(fit for fit in refined if fit.inliers.sum() >= minimum)
            best = min(qualified, key=rank)
        rms = math.sqrt(rank(best)[1])
        if rms > self.config.max_pose_error_px:
            return None, "AprilTag reprojection error exceeds limit"
        for other in qualified:
            if (other.inliers.sum() == best.inliers.sum() and math.sqrt(rank(other)[1]) <= rms + 0.12
                    and np.linalg.norm(best.center[:2] - other.center[:2]) / self.scale > 0.05):
                return None, "ambiguous planar AprilTag pose"
        return best, None

    @staticmethod
    def _yaw(rotation, image_axes):
        up = rotation.T @ np.r_[-np.asarray(image_axes)[1], 0.]
        return math.degrees(math.atan2(-up[0], up[1])) % 360

    def _covariance(self, fit, model, K, dist, rms, image_axes):
        _, jac = cv2.projectPoints(model[fit.inliers].reshape(-1, 3), fit.rvec, fit.tvec, K, dist)
        jac = jac[:, :6]
        cov_params = max(rms, self.config.pixel_sigma) ** 2 * np.linalg.inv(jac.T @ jac)
        parameters = np.r_[fit.rvec.ravel(), fit.tvec.ravel()]
        derivative = np.zeros((3, 6))
        for i in range(6):
            step = np.zeros(6)
            step[i] = 1e-6 if i < 3 else self.scale * 1e-6
            a, b = parameters + step, parameters - step
            ra, rb = cv2.Rodrigues(a[:3])[0], cv2.Rodrigues(b[:3])[0]
            derivative[:2, i] = ((-ra.T @ a[3:] + rb.T @ b[3:]) / (2 * step[i] * self.scale))[:2]
            derivative[2, i] = yaw_difference(self._yaw(ra, image_axes), self._yaw(rb, image_axes)) / (2 * step[i])
        covariance = derivative @ cov_params @ derivative.T
        xy = (covariance[:2, :2] + covariance[:2, :2].T) / 2 + np.eye(2) * self.config.position_floor_m ** 2
        yaw_sigma = math.sqrt(max(0.0, covariance[2, 2]) + self.config.yaw_floor_deg ** 2)
        return xy, yaw_sigma

    def _horizontal_footprint(self, fit, K, size, image_axes):
        width, height = size
        right = np.asarray(image_axes)[0]
        offset = right * (np.asarray(size) - 1) / 2
        middle = np.array([width - 1, height - 1]) / 2
        points = np.stack([middle - offset, middle + offset, middle])
        # Map matching uses the rectified frame, whose output intrinsics are also K.
        normalized = cv2.undistortPoints(points.reshape(3, 1, 2), K, None).reshape(3, 2)
        rays = np.c_[normalized, np.ones(3)] @ fit.rotation
        valid = rays[:, 2] < -1e-5
        offsets = np.zeros((3, 2), dtype=float)
        offsets[valid] = -fit.center[2] * rays[valid, :2] / rays[valid, 2, None] / self.scale
        span = float(np.linalg.norm(offsets[1] - offsets[0])) if valid[:2].all() else None
        center_offset = tuple(map(float, offsets[2])) if valid[2] else None
        return span, center_offset
