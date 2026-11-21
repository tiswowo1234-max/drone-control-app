"""End to end: grayscale frame -> drone pose (AprilTags) -> printed photos (OpenCV) -> where each
photo is on the map and which slot -> LLM verdict (optional) -> Report with a location, plus an
operator view (camera frame next to the map with the drone, its view and the photos marked).

Uses the LLM_detects modules (detector, schema, config) for the LLM step and the files map_layout.py
wrote to data/maps/; nothing in LLM_detects imports this folder.

Usage:
    python pipeline.py --sim                  # run on data/experiments/incident_camera/sim_frames/ and compare with their ground truth
    python pipeline.py FRAME [FRAME ...]      # pose, photos and map view per frame; no LLM calls
    python pipeline.py FRAME --detect         # also ask the LLM about each photo (costs money)
    python pipeline.py --camera 0             # live view from an OpenCV camera (q quits)
Outputs go to data/experiments/incident_camera/pipeline/: <frame>_view.png, <frame>.json and photo crops.
"""
import argparse
import json
import math
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import _paths  # noqa: F401  (LLM_detects on sys.path)
from geo import MapGeo
from localizer import Localizer, Pose, PoseTracker, TrackedPose
from photo_finder import FoundPhoto, TagSeen, annotate, find_photos, rectify, to_gray
from schema import Location, Report
from settings import OUTPUTS, LocalizerConfig, MapConfig, PipelineConfig
from image_io import read_image, write_image

MAP_SIZE_M = MapConfig().size_m


@dataclass
class PhotoOnMap:
    photo: FoundPhoto
    estimated: bool = False  # placed from the tracker (no tags in this frame), not from this frame's pose
    map_corners: np.ndarray | None = None  # (4, 2) map meters; None without a pose
    slot: dict | None = None  # entry from slots.json
    latlon: tuple[float, float] | None = None
    crop_path: Path | None = None
    report: Report | None = None

    @property
    def map_center(self) -> np.ndarray | None:
        return None if self.map_corners is None else self.map_corners.mean(axis=0)

    def location(self) -> Location | None:
        if self.map_center is None:
            return None
        x, y = self.map_center
        where = ("about " if self.estimated else "") + f"map ({x:.3f}, {y:.3f}) m"
        if self.slot:
            where = f"slot S{self.slot['slot']}, {self.slot['building_name'] or 'unnamed building'}, " + where
        lat, lon = self.latlon
        return Location(name=where, latitude=round(lat, 7), longitude=round(lon, 7))


@dataclass
class FrameResult:
    tags: list[TagSeen]
    pose: Pose | None  # from this frame's tags, as solved
    tracked: TrackedPose | None
    photos: list[PhotoOnMap] = field(default_factory=list)

    @property
    def pose_rejected(self) -> bool:
        """The tracker judged this frame's pose a jump that does not fit the motion."""
        return self.pose is not None and self.tracked is not None and self.tracked.rejected

    @property
    def trusted_pose(self) -> Pose | None:
        return None if self.pose_rejected else self.pose

    @property
    def tracked_ok(self) -> TrackedPose | None:
        """The tracker's estimate, unless it has lost the drone."""
        return self.tracked if self.tracked is not None and self.tracked.status != "lost" else None


class Pipeline:
    def __init__(self, cfg: PipelineConfig = PipelineConfig(), loc_cfg: LocalizerConfig = LocalizerConfig(),
                 llm: bool = False, warn_camera: bool = True):
        self.cfg, self.loc_cfg, self.warn_camera = cfg, loc_cfg, warn_camera
        self.localizer: Localizer | None = None  # built on the first frame, for its size
        self.tracker = PoseTracker()
        self.last_photo_t: float | None = None
        self.geo = MapGeo()
        self.slots = json.loads(loc_cfg.slots_file.read_text(encoding="utf-8"))["slots"]
        base = read_image(cfg.map_view_image, cv2.IMREAD_COLOR)
        self.map_view = cv2.resize(base, (cfg.map_view_px, cfg.map_view_px), interpolation=cv2.INTER_AREA)
        self.detector = None
        if llm:
            from config import Config
            from detector import Detector  # only when asked: plain runs need no API key
            self.detector = Detector(Config())

    def process(self, frame: np.ndarray, t: float, gravity_cam: np.ndarray | None = None) -> FrameResult:
        gray = to_gray(frame)
        if self.localizer is None or (self.localizer.camera.width, self.localizer.camera.height) != gray.shape[::-1]:
            self.localizer = Localizer.from_files(self.loc_cfg, frame_size=gray.shape[::-1], warn=self.warn_camera)
        tags, quads = self.localizer.detect_all(gray)
        pose = self.localizer.solve(tags, self.tracker.position, gravity_cam)
        result = FrameResult(tags, pose, self.tracker.update(t, pose))
        where = result.trusted_pose
        if where is None and result.tracked_ok is not None:
            # No tag in view (typical right above a photo), or a pose the tracker rejected as a jump:
            # place photos from the tracked position and heading instead
            where = self.localizer.assumed_pose(result.tracked.position, result.tracked.heading_deg)
        if self.last_photo_t is not None and t - self.last_photo_t < self.cfg.photo_interval_s - 1e-6:
            return result  # photo search is due again later; this frame only updates the pose
        self.last_photo_t = t
        for p in find_photos(gray, tags=tags, marker_quads=quads):
            pom = PhotoOnMap(p, estimated=result.trusted_pose is None)
            if where is not None:
                corners = self.localizer.pixels_to_map(p.corners, where)
                if np.isfinite(corners).all():
                    pom.map_corners = corners
                    pom.slot = self.find_slot(pom.map_center)
                    pom.latlon = self.geo.map_to_latlon(*pom.map_center)
                    if not p.upright and pom.slot:
                        self.turn_like_slot(gray, pom)
            result.photos.append(pom)
        return result

    def turn_like_slot(self, gray: np.ndarray, pom: PhotoOnMap) -> None:
        """No bottom bar found: photos are pasted with their top toward the slot's `up`, so turn the
        crop until its top edge faces that way on the map."""
        up = np.array(pom.slot["up_xy"])
        c = pom.map_corners
        k = max(range(4), key=lambda k: (np.roll(c, -k, axis=0)[:2].mean(0) - np.roll(c, -k, axis=0)[2:].mean(0)) @ up)
        if k:
            pom.photo.corners = np.roll(pom.photo.corners, -k, axis=0)
            pom.map_corners = np.roll(c, -k, axis=0)
            pom.photo.crop = rectify(gray, pom.photo.corners)
        pom.photo.upright = True

    def find_slot(self, xy: np.ndarray) -> dict | None:
        best, best_d = None, math.inf
        for s in self.slots:
            poly = np.array(s["corners_xy_m"], np.float32)
            d = -cv2.pointPolygonTest(poly, (float(xy[0]), float(xy[1])), True)  # < 0 inside
            if d < best_d:
                best, best_d = s, d
        return best if best_d <= self.loc_cfg.slot_match_m else None

    def ask_llm(self, result: FrameResult, stem: str, captured_at: datetime | None) -> None:
        for k, pom in enumerate(result.photos, 1):
            pom.crop_path = self.cfg.out_dir / f"{stem}_photo{k}.png"
            write_image(pom.crop_path, pom.photo.crop)
            if self.detector is not None and pom.photo.big_enough:
                pom.report, _ = self.detector.detect(pom.crop_path, image_id=f"{stem}_photo{k}", run_id="pipeline",
                                                     captured_at=captured_at, location=pom.location())

    # ---------- operator view ----------

    def to_view(self, xy) -> np.ndarray:
        s = self.cfg.map_view_px / MAP_SIZE_M
        xy = np.asarray(xy, float)
        return np.stack([xy[..., 0] * s, (MAP_SIZE_M - xy[..., 1]) * s], axis=-1)

    def render(self, frame: np.ndarray, result: FrameResult) -> np.ndarray:
        left = annotate(frame, [p.photo for p in result.photos], result.tags)
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = left.shape[1] / 640
        for k, pom in enumerate(result.photos, 1):
            if pom.map_center is not None:
                x, y = pom.map_center
                text = (f"S{pom.slot['slot']} " if pom.slot else "") + f"({x:.2f}, {y:.2f}) m"
                bl = np.round(pom.photo.corners[3]).astype(int)
                cv2.putText(left, text, (int(bl[0]), int(bl[1]) + round(16 * scale)), font, 0.5 * scale,
                            (255, 0, 255), max(1, round(scale)))
        pose, tracked = result.trusted_pose, result.tracked_ok
        if pose is not None:
            x, y, z = pose.position
            status = (f"x {x:.3f}  y {y:.3f}  h {z:.3f} m  heading {pose.heading_deg:.0f}  tilt {pose.tilt_deg:.0f}  "
                      f"tags {pose.tag_ids}")
        elif tracked is not None:
            x, y, z = tracked.position
            why = "pose rejected as a jump" if result.pose_rejected else "no tags"
            status = f"{why}: predicted x {x:.3f}  y {y:.3f} m"
        else:
            status = "no pose (no known tag in view)"
        cv2.rectangle(left, (0, left.shape[0] - round(26 * scale)), (left.shape[1], left.shape[0]), (0, 0, 0), -1)
        cv2.putText(left, status, (8, left.shape[0] - round(8 * scale)), font, 0.5 * scale, (255, 255, 255),
                    max(1, round(scale)))

        right = self.map_view.copy()
        for s in self.slots:
            cv2.polylines(right, [np.round(self.to_view(s["corners_xy_m"])).astype(np.int32)], True, (200, 80, 0), 1)
        seen = {t.id for t in result.tags}
        for t in (self.localizer.tag_map.corners.items() if self.localizer else []):
            if t[0] in seen:
                c = self.to_view(t[1].mean(axis=0))
                cv2.circle(right, tuple(np.round(c).astype(int)), 9, (255, 140, 0), 2)
        if pose is not None:
            foot = self.localizer.footprint(pose)
            if np.isfinite(foot).all():
                cv2.polylines(right, [np.round(self.to_view(foot)).astype(np.int32)], True, (0, 200, 255), 2)
        here = pose.position if pose is not None else (tracked.position if tracked is not None else None)
        if here is not None:
            c = self.to_view(here[:2])
            heading = pose.heading_deg if pose is not None else tracked.heading_deg
            tip = c + 22 * np.array([math.sin(math.radians(heading)), -math.cos(math.radians(heading))])
            cv2.arrowedLine(right, tuple(np.round(c).astype(int)), tuple(np.round(tip).astype(int)), (0, 0, 255), 2,
                            tipLength=0.35)
            cv2.circle(right, tuple(np.round(c).astype(int)), 6, (0, 0, 255), -1)
        for k, pom in enumerate(result.photos, 1):
            if pom.map_corners is not None:
                quad = np.round(self.to_view(pom.map_corners)).astype(np.int32)
                cv2.polylines(right, [quad], True, (255, 0, 255), 2)
                cv2.putText(right, f"PHOTO {k}", tuple(quad[0] + [0, -6]), font, 0.5, (255, 0, 255), 2)
        cv2.putText(right, "map, north up", (8, 20), font, 0.55, (40, 40, 40), 1)

        h = right.shape[0]
        left = cv2.resize(left, (round(left.shape[1] * h / left.shape[0]), h), interpolation=cv2.INTER_LINEAR)
        return np.hstack([left, right])

    def summary(self, result: FrameResult) -> dict:
        pose = result.pose
        return {
            "pose_rejected_by_tracker": result.pose_rejected,
            "pose": None if pose is None else {
                "position_m": np.round(pose.position, 4).tolist(),
                "position_std_mm": np.round(pose.position_std_m * 1000, 1).tolist(),
                "heading_deg": round(pose.heading_deg, 2), "tilt_deg": round(pose.tilt_deg, 2),
                "rms_px": round(pose.rms_px, 3), "tags": pose.tag_ids, "dropped_tags": pose.dropped_ids},
            "tracker": None if result.tracked is None else {
                "status": result.tracked.status, "position_m": np.round(result.tracked.position, 4).tolist()},
            "photos": [{
                "corners_px": np.round(p.photo.corners, 1).tolist(),
                "big_enough_for_llm": p.photo.big_enough,
                "map_center_m": None if p.map_center is None else np.round(p.map_center, 4).tolist(),
                "slot": p.slot["slot"] if p.slot else None,
                "location": None if p.location() is None else p.location().model_dump(),
                "crop": p.crop_path.name if p.crop_path else None,
                "report": None if p.report is None else p.report.model_dump(mode="json"),
            } for p in result.photos],
        }


def describe(name: str, result: FrameResult) -> str:
    pose = result.pose
    line = f"{name}: "
    if pose is not None:
        x, y, z = pose.position
        line += f"pose x {x:.3f} y {y:.3f} h {z:.3f} m, heading {pose.heading_deg:.0f}, tags {pose.tag_ids}"
        if result.pose_rejected:
            line += " (rejected by the tracker as a jump)"
    else:
        line += "no pose"
    for k, p in enumerate(result.photos, 1):
        where = "unknown place (no pose)" if p.map_center is None else (
            (f"slot S{p.slot['slot']} " if p.slot else "") + f"at ({p.map_center[0]:.3f}, {p.map_center[1]:.3f}) m")
        line += f"\n  PHOTO {k} DETECTED: {where}" + ("" if p.photo.big_enough else " (too small for the LLM: fly closer)")
        if p.report is not None:
            d = p.report.detection
            line += (f"\n    LLM: {'ABNORMAL' if d.is_abnormal else 'normal'} {d.category}, urgency {d.urgency.value}, "
                     f"review {d.needs_human_review}: {d.description}")
    return line


def run_files(paths: list[Path], pipe: Pipeline, truth: dict | None = None) -> None:
    pipe.cfg.out_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for i, path in enumerate(paths):
        frame = read_image(path, cv2.IMREAD_UNCHANGED)
        if frame is None:
            print(f"{path}: cannot read")
            continue
        pipe.tracker.reset()  # separate files are not a continuous flight
        pipe.last_photo_t = None
        result = pipe.process(frame, t=float(i))
        pipe.ask_llm(result, path.stem, None)
        write_image(pipe.cfg.out_dir / f"{path.stem}_view.png", pipe.render(frame, result))
        (pipe.cfg.out_dir / f"{path.stem}.json").write_text(json.dumps(pipe.summary(result), ensure_ascii=False,
                                                                       indent=1), encoding="utf-8")
        print(describe(path.name, result))
        if truth and path.name in truth and result.pose is not None:
            gt = truth[path.name]
            err = np.linalg.norm(result.pose.position[:2] - np.array(gt["camera_xy_m"])) * 1000
            errors.append(err)
            print(f"  vs ground truth: position error {err:.1f} mm, height error "
                  f"{abs(result.pose.position[2] - gt['height_m']) * 1000:.1f} mm, photos in view {gt['photos_fully_visible']}")
    if errors:
        print(f"\nposition error over {len(errors)} frames with a pose: median {np.median(errors):.1f} mm, "
              f"max {np.max(errors):.1f} mm")
    print(f"views and JSON in {pipe.cfg.out_dir}")


def run_camera(index: int, pipe: Pipeline) -> None:
    """Live view from an OpenCV camera (USB/UVC). Not tested with the team's grayscale module."""
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SystemExit(f"cannot open camera {index}")
    t0 = time.monotonic()
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        # TODO: pass gravity_cam from the flight controller's roll/pitch for much better poses
        result = pipe.process(frame, t=time.monotonic() - t0)
        cv2.imshow("pipeline (q quits)", pipe.render(frame, result))
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("frames", nargs="*", type=Path)
    parser.add_argument("--sim", action="store_true", help="run on data/experiments/incident_camera/sim_frames/ and compare with ground truth")
    parser.add_argument("--camera", type=int)
    parser.add_argument("--detect", action="store_true", help="ask the LLM about each photo (costs money)")
    args = parser.parse_args()
    if args.camera is not None:
        run_camera(args.camera, Pipeline(llm=False))
    elif args.sim:
        sim_dir = OUTPUTS / "sim_frames"
        truth = {f["file"]: f for f in json.loads((sim_dir / "ground_truth.json").read_text())["frames"]}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # sim frames use the same pinhole guess as the localizer
            pipe = Pipeline(llm=args.detect, warn_camera=False)
        run_files(sorted(sim_dir.glob("frame_*.png")), pipe, truth)
    elif args.frames:
        run_files(args.frames, Pipeline(llm=args.detect))
    else:
        parser.error("give frame files, --sim or --camera N")


if __name__ == "__main__":
    main()
