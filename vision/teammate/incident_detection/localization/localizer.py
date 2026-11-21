"""AprilTag localization (proposal): where is the drone camera above the 2.5 m map?

Every pasted tag has known map coordinates (data/maps/apriltag_map.json), so each detected tag
gives four 2D-3D point pairs on the map plane (z = 0). Per frame:
  1. detect tags with sub-pixel corners and keep the ids that are on the map; when some tags are
     big in the image, ignore tiny far ones (their corners are noisy)
  2. camera pose by PnP over all corners at once: IPPE (made for planar targets; it returns the two
     mirror-image solutions a flat target allows), each refined with Levenberg-Marquardt. The pair
     is told apart by reprojection error, by "camera above the map, looking down", and, when it is
     still a close call, by the previous pose
  3. outlier tags (misread id, tag pasted in the wrong place) are dropped one at a time while their
     corners reproject worse than max_tag_error_px; poses with a bad overall error are rejected
  4. output: camera position (x, y, height) in map meters, compass heading of the image's up side,
     tilt, reprojection error, and a position covariance from the PnP Jacobian
  5. PoseTracker: constant-velocity Kalman filter over frames. It smooths, gates out jumps that
     do not fit the motion, and coasts briefly through frames without tags
  6. pixels_to_map: any pixel (e.g. a found photo's corners) -> its ray meets the map plane ->
     map meters (then geo.py gives latitude/longitude)

Usage:
    python localizer.py FRAME [FRAME ...]    # print the pose for each frame
"""
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from camera_model import CameraModel
from photo_finder import TagSeen, detect_tags, detect_tags_and_quads, make_tag_detector, to_gray
from settings import LocalizerConfig, TrackerConfig
from image_io import read_image


@dataclass
class TagMap:
    """Map-frame corners (z = 0) of every pasted tag."""
    corners: dict[int, np.ndarray]  # id -> (4, 2) meters, OpenCV corner order
    size_m: float
    family: str

    @classmethod
    def load(cls, path: Path) -> "TagMap":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls({t["id"]: np.array(t["corners_xy_m"], float) for t in doc["tags"]}, doc["tag_size_m"], doc["family"])


@dataclass
class Pose:
    rvec: np.ndarray  # map -> camera rotation (Rodrigues), OpenCV solvePnP convention
    tvec: np.ndarray
    position: np.ndarray  # camera center in map meters: x, y, height above the map
    heading_deg: float  # compass direction of the image's up side on the map: 0 = north, 90 = east
    tilt_deg: float  # optical axis away from straight down
    rms_px: float
    tag_ids: list[int]
    dropped_ids: list[int]
    position_cov: np.ndarray  # 3x3, m^2

    @property
    def R(self) -> np.ndarray:
        return cv2.Rodrigues(self.rvec)[0]

    @property
    def position_std_m(self) -> np.ndarray:
        return np.sqrt(np.diag(self.position_cov))


def camera_center(rvec, tvec) -> np.ndarray:
    return -cv2.Rodrigues(rvec)[0].T @ np.asarray(tvec, float).ravel()


def heading_and_tilt(rvec) -> tuple[float, float]:
    R = cv2.Rodrigues(rvec)[0]
    up = R.T @ np.array([0.0, -1.0, 0.0])  # image up, in map coordinates
    axis = R.T @ np.array([0.0, 0.0, 1.0])  # optical axis
    heading = math.degrees(math.atan2(up[0], up[1])) % 360
    tilt = math.degrees(math.acos(float(np.clip(-axis[2], -1, 1))))
    return heading, tilt


class Localizer:
    def __init__(self, camera: CameraModel, tag_map: TagMap, cfg: LocalizerConfig = LocalizerConfig()):
        self.camera, self.tag_map, self.cfg = camera, tag_map, cfg
        self.detector = make_tag_detector(tag_map.family, refine=True)

    @classmethod
    def from_files(cls, cfg: LocalizerConfig = LocalizerConfig(), frame_size: tuple[int, int] | None = None,
                   warn: bool = True) -> "Localizer":
        camera = CameraModel.load(cfg.camera_file, warn=warn)
        if frame_size:
            camera = camera.for_size(*frame_size)
        return cls(camera, TagMap.load(cfg.tags_file), cfg)

    def detect(self, gray: np.ndarray) -> list[TagSeen]:
        return detect_tags(gray, self.detector)

    def detect_all(self, gray: np.ndarray) -> tuple[list[TagSeen], list[np.ndarray]]:
        """Tags plus the undecodable dark-bordered quads, which photo_finder uses as photo candidates."""
        return detect_tags_and_quads(gray, self.detector)

    def solve(self, tags: list[TagSeen], prior_position: np.ndarray | None = None,
              gravity_cam: np.ndarray | None = None) -> Pose | None:
        """Camera pose from the tags seen in one frame, or None if they do not give a trustworthy one.

        prior_position: last known camera position, to pick between the two planar solutions.
        gravity_cam: unit vector of "down" in camera coordinates (x right, y down, z forward), from
        the flight controller's roll/pitch and the camera mount. Strongly recommended on the drone."""
        use = [t for t in tags if t.id in self.tag_map.corners]
        side = {t.id: float(np.mean([np.linalg.norm(t.corners[i] - t.corners[i - 1]) for i in range(4)]))
                for t in use}
        if any(s >= self.cfg.min_tag_side_px for s in side.values()):
            use = [t for t in use if side[t.id] >= self.cfg.min_tag_side_px]
        if gravity_cam is not None:
            down = np.asarray(gravity_cam, float) / np.linalg.norm(gravity_cam)
            sigma = math.radians(self.cfg.gravity_prior_deg)
        elif self.cfg.level_prior_deg is not None:
            down, sigma = np.array([0.0, 0.0, 1.0]), math.radians(self.cfg.level_prior_deg)
        else:
            down, sigma = None, None
        dropped = []
        while use:
            fit = self._fit(use, prior_position, down, sigma)
            if fit is None:
                return None
            rvec, tvec, rms, per_tag, jac = fit
            worst = max(per_tag, key=per_tag.get)
            if per_tag[worst] <= self.cfg.max_tag_error_px or len(use) == 1:
                break
            dropped.append(worst)
            use = [t for t in use if t.id != worst]
        else:
            return None
        if rms > self.cfg.max_pose_error_px:
            return None
        heading, tilt = heading_and_tilt(rvec)
        cov = self._position_cov(jac, rvec, tvec, max(1.0, rms / self.cfg.pixel_noise_px))
        return Pose(rvec, tvec, camera_center(rvec, tvec), heading, tilt, rms, sorted(t.id for t in use),
                    dropped, cov)

    def pixels_to_map(self, pts_px: np.ndarray, pose: Pose) -> np.ndarray:
        """Where pixels land on the map plane (map meters); NaN for rays that never reach it."""
        pts = np.asarray(pts_px, np.float64).reshape(-1, 1, 2)
        norm = cv2.undistortPoints(pts, self.camera.K, self.camera.dist).reshape(-1, 2)
        rays = np.c_[norm, np.ones(len(norm))] @ pose.R  # camera rays rotated into the map frame
        c = pose.position
        with np.errstate(divide="ignore", invalid="ignore"):
            lam = -c[2] / rays[:, 2]
        out = c[None, :2] + lam[:, None] * rays[:, :2]
        out[~(lam > 0)] = np.nan
        return out

    def assumed_pose(self, position: np.ndarray, heading_deg: float) -> Pose:
        """A level camera at a tracked position and heading, for frames without tags (e.g. close
        over a photo after descending). Good to roughly height x tan(actual tilt)."""
        h = math.radians(heading_deg)
        up = np.array([math.sin(h), math.cos(h), 0.0])
        R = np.stack([np.array([math.cos(h), -math.sin(h), 0.0]), -up, np.array([0.0, 0.0, -1.0])])
        rvec = cv2.Rodrigues(R)[0]
        tvec = (-R @ np.asarray(position, float)).reshape(3, 1)
        return Pose(rvec, tvec, np.asarray(position, float), heading_deg, 0.0, math.nan, [], [], np.eye(3) * 1e-3)

    def footprint(self, pose: Pose) -> np.ndarray:
        """The image corners on the map: the patch of map the camera sees."""
        w, h = self.camera.width, self.camera.height
        return self.pixels_to_map(np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]), pose)

    # ---------- internals ----------

    def _points(self, tags: list[TagSeen]) -> tuple[np.ndarray, np.ndarray]:
        obj = np.concatenate([np.c_[self.tag_map.corners[t.id], np.zeros(4)] for t in tags])
        img = np.concatenate([t.corners for t in tags]).astype(np.float64)
        return obj, img

    def _fit(self, tags, prior_position, down, sigma):
        obj, img = self._points(tags)
        K, dist = self.camera.K, self.camera.dist
        try:
            n, rvecs, tvecs, _ = cv2.solvePnPGeneric(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE)
        except cv2.error:
            return None
        options = []
        for rvec, tvec in zip(rvecs[:n], tvecs[:n]):
            rvec, tvec, cost, jac = self._refine(obj, img, rvec, tvec, down, sigma)
            c = camera_center(rvec, tvec)
            axis_z = (cv2.Rodrigues(rvec)[0].T @ np.array([0.0, 0.0, 1.0]))[2]
            if c[2] <= 0 or axis_z >= 0:  # camera must be above the map, looking down at it
                continue
            proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
            err = np.linalg.norm(proj.reshape(-1, 2) - img, axis=1)
            options.append((cost, rvec, tvec, err, c, jac))
        if not options:
            return None
        options.sort(key=lambda o: o[0])
        best = options[0]
        if prior_position is not None and len(options) > 1 and options[1][0] < 2 * best[0] + 4:
            # The two planar solutions fit about equally well: the one nearer the last pose wins
            best = min(options[:2], key=lambda o: np.linalg.norm(o[4] - prior_position))
        _, rvec, tvec, err, _, jac = best
        per_tag = {t.id: float(np.sqrt(np.mean(err[4 * k:4 * k + 4] ** 2))) for k, t in enumerate(tags)}
        return rvec, tvec, float(np.sqrt(np.mean(err ** 2))), per_tag, jac

    def _refine(self, obj, img, rvec, tvec, down, sigma):
        """Levenberg-Marquardt on corner reprojection error (in units of pixel_noise_px) plus, when
        `down` is given, the angle between it and the pose's "down" in camera coordinates (units of
        sigma radians). Returns rvec, tvec, total cost and the weighted Jacobian at the optimum."""
        K, dist = self.camera.K, self.camera.dist
        s_px = self.cfg.pixel_noise_px
        world_down = np.array([0.0, 0.0, -1.0])

        def residuals(p):
            proj, jac = cv2.projectPoints(obj, p[:3], p[3:], K, dist)
            r = (proj.reshape(-1) - img.reshape(-1)) / s_px
            j = jac[:, :6] / s_px
            if down is not None:
                g = cv2.Rodrigues(p[:3])[0] @ world_down
                jg = np.zeros((3, 6))
                for i in range(3):
                    d = np.zeros(3)
                    d[i] = 1e-6
                    jg[:, i] = (cv2.Rodrigues(p[:3] + d)[0] @ world_down - cv2.Rodrigues(p[:3] - d)[0] @ world_down) / 2e-6
                r = np.r_[r, (g - down) / sigma]
                j = np.vstack([j, jg / sigma])
            return r, j

        p = np.r_[np.ravel(rvec), np.ravel(tvec)].astype(float)
        r, j = residuals(p)
        cost, lam = float(r @ r), 1e-3
        for _ in range(50):
            h = j.T @ j
            try:
                step = -np.linalg.solve(h + lam * np.diag(np.diag(h) + 1e-12), j.T @ r)
            except np.linalg.LinAlgError:
                break
            r2, j2 = residuals(p + step)
            if float(r2 @ r2) < cost:
                p, r, j, cost = p + step, r2, j2, float(r2 @ r2)
                lam = max(lam / 3, 1e-12)
                if np.linalg.norm(step) < 1e-12:
                    break
            else:
                lam *= 4
                if lam > 1e8:
                    break
        return p[:3].reshape(3, 1), p[3:].reshape(3, 1), cost, j

    def _position_cov(self, jac, rvec, tvec, scale: float) -> np.ndarray:
        """Covariance of the camera center from the weighted Jacobian (inflated when the fit is worse
        than pixel_noise_px)."""
        try:
            cov_p = scale ** 2 * np.linalg.inv(jac.T @ jac)
        except np.linalg.LinAlgError:
            return np.eye(3)
        p0 = np.r_[rvec.ravel(), tvec.ravel()]
        g = np.zeros((3, 6))
        for i in range(6):
            d = np.zeros(6)
            d[i] = 1e-6
            g[:, i] = (camera_center((p0 + d)[:3], (p0 + d)[3:]) - camera_center((p0 - d)[:3], (p0 - d)[3:])) / 2e-6
        return g @ cov_p @ g.T


@dataclass
class TrackedPose:
    position: np.ndarray  # filtered camera center, map meters
    velocity: np.ndarray  # m/s
    heading_deg: float
    position_std_m: np.ndarray
    # "tracking" (measured this frame), "coasting" (predicted), "lost" (no pose for max_coast_s; the
    # tracker then starts over, and update() returns None until the next pose)
    status: str
    rejected: bool  # this frame's measurement did not fit the motion and was ignored


class PoseTracker:
    """Constant-velocity Kalman filter on the camera position, plus a smoothed heading."""

    def __init__(self, cfg: TrackerConfig = TrackerConfig()):
        self.cfg = cfg
        self.reset()

    def reset(self) -> None:
        self.x = self.P = self.t = self.heading = self.last_measured = None
        self.updates = self.rejects = 0

    @property
    def position(self) -> np.ndarray | None:
        return None if self.x is None else self.x[:3].copy()

    def update(self, t: float, pose: Pose | None) -> TrackedPose | None:
        if self.x is None:
            if pose is None:
                return None
            self.x = np.r_[pose.position, np.zeros(3)]
            self.P = np.diag(np.r_[np.diag(pose.position_cov) + self.cfg.min_position_std_m ** 2, [0.5 ** 2] * 3])
            self.t = self.last_measured = t
            self.heading = pose.heading_deg
            self.updates = 1
            return self._out("tracking", False)

        self._predict(t)
        rejected = False
        if pose is not None:
            h = np.c_[np.eye(3), np.zeros((3, 3))]
            r = pose.position_cov + np.eye(3) * self.cfg.min_position_std_m ** 2
            y = pose.position - h @ self.x
            s = h @ self.P @ h.T + r
            if y @ np.linalg.solve(s, y) > self.cfg.gate_chi2 and self.updates >= 3:
                rejected = True
                self.rejects += 1
                if self.rejects > self.cfg.max_rejects:  # we were wrong, not the measurements: restart
                    self.reset()
                    return self.update(t, pose)
            else:
                k = self.P @ h.T @ np.linalg.inv(s)
                self.x = self.x + k @ y
                self.P = (np.eye(6) - k @ h) @ self.P
                turn = (pose.heading_deg - self.heading + 180) % 360 - 180
                self.heading = (self.heading + self.cfg.heading_smoothing * turn) % 360
                self.updates += 1
                self.rejects = 0
                self.last_measured = t
                return self._out("tracking", False)
        if t - self.last_measured > self.cfg.max_coast_s:
            out = self._out("lost", rejected)
            self.reset()  # forget the stale track: no extrapolated prior, restart at the next pose
            return out
        return self._out("coasting", rejected)

    def _predict(self, t: float) -> None:
        dt = max(t - self.t, 0.0)
        f = np.eye(6)
        f[:3, 3:] = dt * np.eye(3)
        q = self.cfg.accel_noise_m_s2 ** 2 * np.kron(np.array([[dt ** 4 / 4, dt ** 3 / 2], [dt ** 3 / 2, dt ** 2]]),
                                                       np.eye(3))
        self.x = f @ self.x
        self.P = f @ self.P @ f.T + q
        self.t = t

    def _out(self, status: str, rejected: bool) -> TrackedPose:
        return TrackedPose(self.x[:3].copy(), self.x[3:].copy(), self.heading, np.sqrt(np.diag(self.P)[:3]),
                           status, rejected)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    localizer = None
    for name in sys.argv[1:]:
        gray = read_image(name, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            print(f"{name}: cannot read")
            continue
        if localizer is None or (localizer.camera.width, localizer.camera.height) != gray.shape[::-1]:
            localizer = Localizer.from_files(frame_size=gray.shape[::-1])
        tags = localizer.detect(to_gray(gray))
        pose = localizer.solve(tags)
        if pose is None:
            print(f"{Path(name).name}: no pose (tags seen: {sorted(t.id for t in tags)})")
            continue
        x, y, z = pose.position
        sx, sy, sz = pose.position_std_m * 1000
        print(f"{Path(name).name}: x {x:.4f} y {y:.4f} m, height {z:.4f} m (+-{sx:.1f}/{sy:.1f}/{sz:.1f} mm), "
              f"heading {pose.heading_deg:.1f} deg, tilt {pose.tilt_deg:.1f} deg, rms {pose.rms_px:.2f} px, "
              f"tags {pose.tag_ids}" + (f", dropped {pose.dropped_ids}" if pose.dropped_ids else ""))


if __name__ == "__main__":
    main()
