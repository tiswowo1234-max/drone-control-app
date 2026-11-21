"""Simulated grayscale drone camera over the finished map, checked against ground truth.

Renders what the camera would see from a known pose (pinhole camera, blur, motion blur, uneven
light, sensor noise, JPEG), from the map with tags and example photos made by
map_layout.py. Then measures:
  - AprilTags: share of fully visible tags decoded, and wrong ids
  - localization (localizer.py): position, height and heading error, share of frames with a pose
  - photo finder (photo_finder.py): share of fully visible photos found, false finds,
    and how far the photo's map position (pixels_to_map) is from where it really is
  - tracking (PoseTracker) along a continuous flight
and writes sample grayscale frames to try the other scripts on.

This is a stand-in for real flights, not a replacement: the real camera's noise, lens distortion,
motion blur and the real print (gloss, wrinkles, color-to-gray response) will differ.

Usage:
    python sim_camera.py                     # full check -> data/experiments/incident_camera/sim_report.md, sample frames -> data/experiments/incident_camera/sim_frames/
    python sim_camera.py --quick             # fewer frames
"""
import argparse
import dataclasses
import json
import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from shapely.geometry import LineString

from camera_model import CameraModel
from localizer import Localizer, PoseTracker, TagMap
# [drone-control-app 수정 2026-11-21] map_layout은 LLM_detects(config, dataset)를 import하므로 복사하지 않았다.
# 원본: import map_layout as ml / from map_layout import PX_PER_MM. scene_from_configs()만 map_layout이 필요하다.
PX_PER_MM = 2.0  # resolution of the map renders (map_layout.PX_PER_MM과 같은 값)
from photo_finder import find_photos
from settings import OUTPUTS, CameraConfig, LocalizerConfig, MapConfig, PhotoFinderConfig, SlotConfig, TagConfig
from image_io import read_image, write_image

FLOOR_GRAY = 95  # what the camera sees beyond the map's edge
MAX_TILT_DEG = 10  # the camera is fixed pointing down, so its tilt is the drone's roll/pitch


@dataclass
class Scene:
    levels: list[np.ndarray]  # grayscale map render; level k is 2**k times coarser
    size_m: float
    tag_map: TagMap
    photos: list[tuple[int, np.ndarray]]  # (slot number, picture corners TL, TR, BR, BL in map meters)


def make_scene(plane: np.ndarray, size_m: float, tag_map: TagMap, photos) -> Scene:
    levels = [plane]
    while min(levels[-1].shape) > 600:
        levels.append(cv2.resize(levels[-1], None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))
    return Scene(levels, size_m, tag_map, photos)


def load_scene(map_cfg: MapConfig = MapConfig(), loc_cfg: LocalizerConfig = LocalizerConfig()) -> Scene:
    """The scene exactly as map_layout.py last wrote it (map_with_photos_gray.png + apriltag_map.json + slots.json)."""
    plane = read_image(map_cfg.out_dir / "map_with_photos_gray.png", cv2.IMREAD_GRAYSCALE)
    if plane is None:
        raise SystemExit("run map_layout.py first")
    slots = json.loads(loc_cfg.slots_file.read_text(encoding="utf-8"))["slots"]
    photos = [(s["slot"], np.array(s["example_picture_corners_xy_m"])) for s in slots]
    return make_scene(plane, map_cfg.size_m, TagMap.load(loc_cfg.tags_file), photos)


def scene_from_configs(map_cfg=MapConfig(), tag_cfg=TagConfig(), slot_cfg=SlotConfig(),
                       frame_mm: float | None = None, white_mm: float | None = None) -> Scene:
    """A what-if scene (other tag size, frame, margin, ...) built straight from map_layout, no files written."""
    import map_layout as ml  # [수정] 늦은 import (이 복사본에는 map_layout이 없다)
    _, slots, tags, _ = ml.build(map_cfg, tag_cfg, slot_cfg)
    plane = ml.render_finished_map(map_cfg, tag_cfg, slot_cfg, tags, slots, gray=True, with_photos=True,
                                   frame_mm=frame_mm, white_mm=white_mm)
    tag_map = TagMap({t.id: t.corners(tag_cfg.size_mm / 1000) for t in tags}, tag_cfg.size_mm / 1000, tag_cfg.family)
    photos = []
    for s in slots:
        hw = read_image(ml.photo_path(s.photo_id), cv2.IMREAD_GRAYSCALE).shape
        photos.append((s.number, ml.photo_piece(s, hw, slot_cfg, frame_mm, white_mm).picture))
    return make_scene(plane, map_cfg.size_m, tag_map, photos)


# ---------- camera ----------

def look_down(x: float, y: float, height: float, heading_deg: float, tilt_deg: float):
    """Map-to-camera rotation R and translation t for a camera at (x, y, height) whose image top points
    to `heading_deg` (compass) and whose optical axis leans that way by `tilt_deg` from straight down."""
    h, tau = math.radians(heading_deg), math.radians(tilt_deg)
    up = np.array([math.sin(h), math.cos(h), 0.0])
    right = np.array([math.cos(h), -math.sin(h), 0.0])
    down = np.array([0.0, 0.0, -1.0])
    axis = down * math.cos(tau) + up * math.sin(tau)
    image_down = -up * math.cos(tau) + down * math.sin(tau)
    R = np.stack([right, image_down, axis])
    return R, -R @ np.array([x, y, height])


def project(cam: CameraModel, R, t, xy: np.ndarray) -> np.ndarray:
    """Map points (z = 0) -> pixels; NaN for points behind the camera."""
    pts = np.c_[xy, np.zeros(len(xy))] @ R.T + t
    with np.errstate(divide="ignore", invalid="ignore"):
        px = (pts @ cam.K.T)[:, :2] / pts[:, 2:3]
    px[pts[:, 2] <= 0] = np.nan
    return px


def fully_visible(px: np.ndarray, cam: CameraModel, margin: float = 3) -> bool:
    return bool(np.all(np.isfinite(px)) and px[:, 0].min() >= margin and px[:, 1].min() >= margin
                and px[:, 0].max() <= cam.width - 1 - margin and px[:, 1].max() <= cam.height - 1 - margin)


@dataclass
class Degrade:
    blur_px: tuple[float, float] = (0.5, 1.0)  # optics, Gaussian sigma
    motion_px: tuple[float, float] = (0.0, 3.0)  # motion blur length, applied to 40 % of frames
    gain: tuple[float, float] = (0.75, 1.1)
    offset: tuple[float, float] = (-15.0, 10.0)
    vignette: tuple[float, float] = (0.0, 0.25)
    noise: tuple[float, float] = (2.0, 5.0)  # Gaussian sensor noise, gray levels
    jpeg_quality: tuple[int, int] = (60, 90)


def render(scene: Scene, cam: CameraModel, R, t, rng: np.random.Generator,
           degrade: Degrade | None = Degrade()) -> np.ndarray:
    center = -R.T @ t
    gsd = center[2] / max(R[2] @ np.array([0, 0, -1.0]), 0.3) / cam.K[0, 0]  # m per camera pixel, roughly
    plane_m = 1 / (PX_PER_MM * 1000)
    level = int(np.clip(math.floor(math.log2(max(gsd / 2 / plane_m, 1))), 0, len(scene.levels) - 1))
    img_l = scene.levels[level]
    s = PX_PER_MM * 1000 / 2 ** level  # level pixels per map meter
    to_map = np.array([[1 / s, 0, 0.5 / s], [0, -1 / s, scene.size_m - 0.5 / s], [0, 0, 1]])
    ss = 2  # render at twice the size, then average down (anti-aliasing)
    k = cam.K.copy()
    k[:2, :2] *= ss
    k[0, 2], k[1, 2] = (k[0, 2] + 0.5) * ss - 0.5, (k[1, 2] + 0.5) * ss - 0.5
    hom = k @ np.c_[R[:, 0], R[:, 1], t] @ to_map
    img = cv2.warpPerspective(img_l, hom, (cam.width * ss, cam.height * ss), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=FLOOR_GRAY)
    img = cv2.resize(img, (cam.width, cam.height), interpolation=cv2.INTER_AREA).astype(np.float32)
    if degrade is None:
        return np.clip(img, 0, 255).astype(np.uint8)
    u = lambda r: rng.uniform(*r)
    img = cv2.GaussianBlur(img, (0, 0), u(degrade.blur_px))
    if rng.random() < 0.4:
        length = u(degrade.motion_px)
        if length >= 1:
            n = int(math.ceil(length)) * 2 + 1
            kern = np.zeros((n, n), np.float32)
            a = rng.uniform(0, math.pi)
            c = n // 2
            cv2.line(kern, (round(c - math.cos(a) * length / 2), round(c - math.sin(a) * length / 2)),
                     (round(c + math.cos(a) * length / 2), round(c + math.sin(a) * length / 2)), 1.0, 1)
            img = cv2.filter2D(img, -1, kern / kern.sum())
    yy, xx = np.mgrid[0:cam.height, 0:cam.width]
    r2 = ((xx - cam.width / 2) ** 2 + (yy - cam.height / 2) ** 2) / ((cam.width / 2) ** 2 + (cam.height / 2) ** 2)
    img = img * u(degrade.gain) * (1 - u(degrade.vignette) * r2) + u(degrade.offset)
    img = img + rng.normal(0, u(degrade.noise), img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(*degrade.jpeg_quality))])
    return cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)


# ---------- checks ----------

def quad_iou(a: np.ndarray, b: np.ndarray) -> float:
    inter, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
    union = abs(cv2.contourArea(a.astype(np.float32))) + abs(cv2.contourArea(b.astype(np.float32))) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class FrameResult:
    height: float
    tags_visible: int
    tags_decoded: int  # visible tags decoded with the right id
    wrong_ids: int  # ids reported that are not on the map or not in view
    pose: bool
    pos_err_mm: float = math.nan
    height_err_mm: float = math.nan
    heading_err_deg: float = math.nan
    photos_visible: int = 0
    photos_found: int = 0
    photo_loose: int = 0  # a real photo (maybe partly in view) found with an outline too far off (IoU < 0.6)
    photo_false: int = 0  # something that is not a photo at all
    photo_iou: list[float] = field(default_factory=list)
    photo_upright: list[bool] = field(default_factory=list)  # flagged upright and really upright
    photo_wrong_up: int = 0  # flagged upright but turned wrong (the costly mistake)
    photo_map_err_mm: list[float] = field(default_factory=list)
    modes: dict = field(default_factory=dict)  # localization mode -> (pose found, position err, height err)


def imu_gravity(R: np.ndarray, rng: np.random.Generator, noise_deg: float = 1.0) -> np.ndarray:
    """What an IMU would report as "down" in camera coordinates: the true direction, ~noise_deg off."""
    g = R @ np.array([0.0, 0.0, -1.0])
    axis = rng.normal(size=3)
    axis -= (axis @ g) * g
    axis /= np.linalg.norm(axis)
    return cv2.Rodrigues(axis * math.radians(noise_deg) * rng.normal())[0] @ g


def pose_error(pose, R, t) -> tuple[bool, float, float]:
    if pose is None:
        return False, math.nan, math.nan
    center = -R.T @ t
    return (True, float(np.linalg.norm(pose.position[:2] - center[:2]) * 1000),
            float(abs(pose.position[2] - center[2]) * 1000))


def check_frame(scene, cam, localizer: Localizer, finder_cfg, gray, R, t, prior=None, gravity=None,
                modes: dict | None = None):
    """Run the tag detector, localizer and photo finder on one frame and score them.

    modes: name -> (Localizer, gravity or None), extra localization variants scored on the same tags."""
    center = -R.T @ t
    in_view = {i: project(cam, R, t, c) for i, c in scene.tag_map.corners.items()}
    visible = {i for i, px in in_view.items() if fully_visible(px, cam)}
    tags, quads = localizer.detect_all(gray)
    ids = [tg.id for tg in tags]
    partly = {i for i, px in in_view.items() if np.isfinite(px).all()
              and (px[:, 0] > -5).any() and (px[:, 0] < cam.width + 5).any()
              and (px[:, 1] > -5).any() and (px[:, 1] < cam.height + 5).any()}
    res = FrameResult(center[2], len(visible), len(visible & set(ids)), sum(i not in partly for i in ids), False)
    pose = localizer.solve(tags, prior, gravity)
    for name, (other, g) in (modes or {}).items():
        res.modes[name] = pose_error(other.solve(tags, prior, g), R, t)
    if pose is not None:
        res.pose = True
        res.pos_err_mm = float(np.linalg.norm(pose.position[:2] - center[:2]) * 1000)
        res.height_err_mm = float(abs(pose.position[2] - center[2]) * 1000)
        true_heading = math.degrees(math.atan2(*(R.T @ np.array([0.0, -1.0, 0.0]))[:2])) % 360
        res.heading_err_deg = abs((pose.heading_deg - true_heading + 180) % 360 - 180)
    projected = [(q, project(cam, R, t, q)) for _, q in scene.photos]  # (map corners, pixel corners)
    truth = [(q, px) for q, px in projected if fully_visible(px, cam)]
    anywhere = [px for _, px in projected if np.isfinite(px).all()]  # also photos only partly in view
    found = find_photos(gray, finder_cfg, tags, quads)
    res.photos_visible = len(truth)
    matched = set()
    for p in found:
        ious = [quad_iou(p.corners, px) for _, px in truth]
        k = int(np.argmax(ious)) if ious else -1
        if k >= 0 and ious[k] >= 0.6 and k not in matched:
            matched.add(k)
            res.photo_iou.append(ious[k])
            gt_px = truth[k][1]  # TL, TR, BR, BL of the picture as printed
            # Upright means the crop's top-left sits on the picture's top-left, not on any other corner
            on_top_left = int(np.argmin(np.linalg.norm(gt_px - p.corners[0], axis=1))) == 0
            res.photo_upright.append(bool(p.upright and on_top_left))
            res.photo_wrong_up += int(p.upright and not on_top_left)
            if pose is not None:
                where = localizer.pixels_to_map(p.center[None], pose)[0]
                res.photo_map_err_mm.append(float(np.linalg.norm(where - truth[k][0].mean(0)) * 1000))
        elif max((quad_iou(p.corners, px) for px in anywhere), default=0.0) > 0.2:
            res.photo_loose += 1
        else:
            res.photo_false += 1
    res.photos_found = len(matched)
    return res, pose


def random_views(scene, cam, localizer, finder_cfg, n, rng, heights, max_tilt, around_photos=False,
                 modes: dict | None = None):
    """modes: name -> (Localizer, use_imu) to compare on the same frames."""
    results = []
    for k in range(n):
        if around_photos:
            _, q = scene.photos[k % len(scene.photos)]
            x, y = q.mean(0) + rng.uniform(-0.08, 0.08, 2)
        else:
            x, y = rng.uniform(0.15, scene.size_m - 0.15, 2)
        R, t = look_down(x, y, rng.uniform(*heights), rng.uniform(0, 360), rng.uniform(0, max_tilt))
        gray = render(scene, cam, R, t, rng)
        extra = {name: (loc, imu_gravity(R, rng) if imu else None) for name, (loc, imu) in (modes or {}).items()}
        results.append(check_frame(scene, cam, localizer, finder_cfg, gray, R, t, modes=extra)[0])
    return results


def flight(scene, cam, localizer, finder_cfg, seconds, fps, rng):
    """A smooth Lissajous flight over the map, frame by frame through the localizer and tracker."""
    tracker = PoseTracker()
    raw, tracked, states = [], [], []
    started = False
    for i in range(int(seconds * fps)):
        t_s = i / fps
        x = scene.size_m / 2 + 0.85 * math.sin(0.11 * t_s)
        y = scene.size_m / 2 + 0.85 * math.sin(0.17 * t_s + 0.5)
        vx, vy = 0.85 * 0.11 * math.cos(0.11 * t_s), 0.85 * 0.17 * math.cos(0.17 * t_s + 0.5)
        height = 0.8 + 0.2 * math.sin(0.3 * t_s)
        heading = math.degrees(math.atan2(vx, vy)) % 360
        R, t = look_down(x, y, height, heading, 5 + 4 * math.sin(0.5 * t_s))
        gray = render(scene, cam, R, t, rng)
        res, pose = check_frame(scene, cam, localizer, finder_cfg, gray, R, t, tracker.position)
        tp = tracker.update(t_s, pose)
        raw.append(res.pos_err_mm if res.pose else math.nan)
        started = started or tp is not None
        if tp is None or tp.status == "lost":
            tracked.append(math.nan)
            states.append("lost" if started else "none")  # after a loss the tracker restarts empty
        else:
            tracked.append(float(np.linalg.norm(tp.position[:2] - np.array([x, y])) * 1000))
            states.append(tp.status)
    return np.array(raw), np.array(tracked), states


def follow_roads(scene, cam, localizer, rng, speed_m_s=0.25, fps=10, height=0.6, min_road_m=0.4):
    """Fly along every road longer than min_road_m (map features), heading along the road, and
    count frames with a pose from tags and with a tracked pose. Returns (raw errors mm, tracked errors mm)."""
    features = json.loads(MapConfig().features.read_text(encoding="utf-8"))
    raw, tracked = [], []
    t_s = 0.0
    for road in features["roads"]:
        line = LineString(road["centerline_xy_m"])
        if line.length < min_road_m:
            continue
        tracker = PoseTracker()  # each road is a separate flight
        for d in np.arange(0, line.length, speed_m_s / fps):
            p = np.array(line.interpolate(d).coords[0])
            ahead = np.array(line.interpolate(min(d + 0.02, line.length)).coords[0])
            if np.linalg.norm(ahead - p) < 1e-6:
                continue
            heading = math.degrees(math.atan2(*(ahead - p))) % 360
            R, t = look_down(p[0], p[1], height, heading, rng.uniform(0, 5))
            pose = localizer.solve(localizer.detect(render(scene, cam, R, t, rng)), tracker.position)
            tp = tracker.update(t_s, pose)
            t_s += 1 / fps
            raw.append(float(np.linalg.norm(pose.position[:2] - p) * 1000) if pose is not None else math.nan)
            ok = tp is not None and tp.status in ("tracking", "coasting")
            tracked.append(float(np.linalg.norm(tp.position[:2] - p) * 1000) if ok else math.nan)
    return np.array(raw), np.array(tracked)


# ---------- report ----------

def pct(a, b):
    return f"{a / b:.0%} ({a}/{b})" if b else "n/a"


def stat(values):
    v = np.array([x for x in values if np.isfinite(x)])
    return f"{np.median(v):.1f} / {np.percentile(v, 90):.1f}" if len(v) else "n/a"


def view_table(results, bins) -> list[str]:
    lines = ["| height (m) | frames | tags decoded | wrong ids | pose found | position err mm (median / p90) | "
             "height err mm | heading err deg | photos found | loose outline / not a photo |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for lo, hi in bins:
        rs = [r for r in results if lo <= r.height < hi]
        if not rs:
            continue
        lines.append(
            f"| {lo:.2f}-{hi:.2f} | {len(rs)} | {pct(sum(r.tags_decoded for r in rs), sum(r.tags_visible for r in rs))} | "
            f"{sum(r.wrong_ids for r in rs)} | {pct(sum(r.pose for r in rs), len(rs))} | "
            f"{stat([r.pos_err_mm for r in rs])} | {stat([r.height_err_mm for r in rs])} | "
            f"{stat([r.heading_err_deg for r in rs])} | "
            f"{pct(sum(r.photos_found for r in rs), sum(r.photos_visible for r in rs))} | "
            f"{sum(r.photo_loose for r in rs)} / {sum(r.photo_false for r in rs)} |")
    return lines


def mode_table(results, bins, names) -> list[str]:
    lines = ["| height (m) | " + " | ".join(f"{n}: pose found, position err mm (median / p90)" for n in names) + " |",
             "|---|" + "---|" * len(names)]
    for lo, hi in bins:
        rs = [r for r in results if lo <= r.height < hi]
        if rs:
            cells = [f"{pct(sum(r.modes[n][0] for r in rs), len(rs))}, {stat([r.modes[n][1] for r in rs])}"
                     for n in names]
            lines.append(f"| {lo:.2f}-{hi:.2f} | " + " | ".join(cells) + " |")
    return lines


def iou_stat(values) -> str:
    v = np.array(values)
    return f"median {np.median(v):.2f}, worst 10 % below {np.percentile(v, 10):.2f}" if len(v) else "n/a"


def save_samples(scene, cam, n, rng, out_dir) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.png"):
        old.unlink()
    truth = []
    for k in range(n):
        if k % 2 == 0:  # every other sample looks at a photo
            _, q = scene.photos[(k // 2) % len(scene.photos)]
            x, y = q.mean(0) + rng.uniform(-0.05, 0.05, 2)
            height = rng.uniform(0.25, 0.35)
        else:
            x, y = rng.uniform(0.3, scene.size_m - 0.3, 2)
            height = rng.uniform(0.6, 1.1)
        heading, tilt = rng.uniform(0, 360), rng.uniform(0, 12)
        R, t = look_down(x, y, height, heading, tilt)
        gray = render(scene, cam, R, t, rng)
        name = f"frame_{k:02d}.png"
        write_image(out_dir / name, gray)
        truth.append({"file": name, "camera_xy_m": [round(x, 4), round(y, 4)], "height_m": round(height, 4),
                      "heading_deg": round(heading, 2), "tilt_deg": round(tilt, 2),
                      "photos_fully_visible": [n for n, q in scene.photos
                                               if fully_visible(project(cam, R, t, q), cam)],
                      "tags_fully_visible": sorted(i for i, c in scene.tag_map.corners.items()
                                                   if fully_visible(project(cam, R, t, c), cam))})
    (out_dir / "ground_truth.json").write_text(json.dumps({
        "camera": {"width": cam.width, "height": cam.height, "K": np.round(cam.K, 3).tolist(),
                   "note": "pinhole, no distortion; the same guess localizer.py uses without incident_camera.json"},
        "frames": truth}, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--seed", type=int, default=340)
    args = parser.parse_args()
    n_random, n_photo, flight_s = (60, 24, 20) if args.quick else (400, 160, 90)

    rng = np.random.default_rng(args.seed)
    cam = CameraModel.from_fov(CameraConfig())
    scene = load_scene()
    localizer = Localizer(cam, scene.tag_map)  # default: weak level prior
    no_prior = Localizer(cam, scene.tag_map, dataclasses.replace(LocalizerConfig(), level_prior_deg=None))
    modes = {"tags only": (no_prior, False), "level prior (default)": (localizer, False),
             "IMU roll/pitch (1 deg noise)": (localizer, True)}
    finder_cfg = PhotoFinderConfig()
    t0 = time.time()
    rand = random_views(scene, cam, localizer, finder_cfg, n_random, rng, (0.4, 1.3), MAX_TILT_DEG, modes=modes)
    near = random_views(scene, cam, localizer, finder_cfg, n_photo, rng, (0.3, 0.9), MAX_TILT_DEG, around_photos=True)
    raw, tracked, states = flight(scene, cam, localizer, finder_cfg, flight_s, 10, rng)
    # Along the roads, with and without the road tags (same flights, same random draws)
    no_road = scene_from_configs(tag_cfg=dataclasses.replace(TagConfig(), road_tags=False))
    road_runs = {}
    for label, sc in (("building tags only", no_road), ("building + road tags", scene)):
        road_runs[label] = follow_roads(sc, cam, Localizer(cam, sc.tag_map), np.random.default_rng(args.seed + 1),
                                        fps=5 if args.quick else 10)
    save_samples(scene, cam, args.samples, rng, OUTPUTS / "sim_frames")

    bins = [(0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.1), (1.1, 1.31)]
    cam_cfg = CameraConfig()
    lines = [
        "# Simulated grayscale camera check",
        "",
        f"Camera: {cam_cfg.width_px}x{cam_cfg.height_px}, horizontal FOV {cam_cfg.hfov_deg} deg (pinhole guess from "
        f"settings.py CameraConfig). Frames get blur, motion blur, uneven light, noise and JPEG.",
        f"Map: {len(scene.tag_map.corners)} tags of {scene.tag_map.size_m * 1000:g} mm, {len(scene.photos)} example "
        f"photos in their slots. Seed {args.seed}, {time.time() - t0:.0f} s.",
        "",
        f"## Random views over the whole map (drone tilt up to {MAX_TILT_DEG} deg)",
        "",
        *view_table(rand, bins),
        "",
        "Tags count as visible when all four corners are in the frame. Wrong ids are ids reported for tags "
        "that are not in view at all (misreads).",
        "",
        "### Localization with and without a tilt prior (same frames)",
        "",
        *mode_table(rand, bins, list(modes)),
        "",
        "With one or two small tags in view, tilt is poorly determined and height x tilt error becomes position "
        "error. A weak 'camera looks about straight down' prior (default) removes the flipped solutions; the "
        "flight controller's roll/pitch (pass gravity_cam to Localizer.solve) does much better.",
        "",
        f"## Views aimed at the example photos (drone tilt up to {MAX_TILT_DEG} deg)",
        "",
        *view_table(near, bins),
        "",
        f"- Picture outline vs. truth (IoU): {iou_stat([i for r in near for i in r.photo_iou])}",
        f"- Crops turned the right way up (thick bottom bar found): "
        f"{pct(sum(u for r in near for u in r.photo_upright), sum(len(r.photo_upright) for r in near))}; "
        f"flagged upright but wrong: {sum(r.photo_wrong_up for r in near)} (without the bar, pipeline.py turns "
        f"the crop by its slot when the pose is known)",
        f"- Photo position on the map from the pose: error median / p90 "
        f"{stat([e for r in near for e in r.photo_map_err_mm])} mm",
        "",
        f"## Continuous flight ({flight_s} s at 10 fps, height 0.6-1.0 m, Lissajous path)",
        "",
        f"- Frames with a pose from tags: {pct(int(np.isfinite(raw).sum()), len(raw))}, position error "
        f"median / p90 {stat(raw)} mm",
        f"- Tracker (Kalman): tracking {states.count('tracking')}, coasting {states.count('coasting')}, "
        f"lost {states.count('lost')}, not started {states.count('none')} frames; error median / p90 {stat(tracked)} mm",
        "",
        "## Flights along the roads (0.6 m high, 0.25 m/s, heading along the road)",
        "",
        "| tags on the map | frames with a pose from tags | position error mm (median / p90) | "
        "frames with a tracked pose | tracked error mm (median / p90) |",
        "|---|---|---|---|---|",
        *[f"| {label} | {pct(int(np.isfinite(r).sum()), len(r))} | {stat(r)} | {pct(int(np.isfinite(tr).sum()), len(tr))} "
          f"| {stat(tr)} |" for label, (r, tr) in road_runs.items()],
        "",
        f"Sample frames: [sim_frames/](sim_frames/) ({args.samples} grayscale PNGs + ground_truth.json).",
    ]
    report = "\n".join(lines)
    (OUTPUTS / "sim_report.md").write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
