"""Find printed incident photos in grayscale camera frames with OpenCV, and mark them as detected.

What the camera sees on the map, and how a photo stands out:
  - the map print: flat-colored buildings, roads and thin lines, so almost no texture,
  - AprilTags: busy but square with an even border; the tag detector decodes them and they are
    masked out,
  - incident photos: printed like a marker (picture, black frame that is thicker under the picture,
    white margin), so a busy quadrilateral with a clean near-black border, on calm map.

Steps per frame:
  1. grayscale; run the AprilTag detector once: decoded tags are masked out, and the dark-bordered
     quads it found but could not decode are the main photo candidates
  2. texture map: local standard deviation above a noise-aware threshold
  3. more candidates from contours of near-black, edge (Canny), dark (adaptive threshold) and busy
     regions
  4. snap every candidate's sides onto the image edges; keep quads with a sane shape (corner angles,
     aspect ratio, area) that are busy inside and framed (near-black band inside the edge, white
     margin outside)
  5. nested candidates (building outline > photo frame > picture > a sign inside it): step inward
     while the ring between two is flat floor, never across the photo's own black frame
  6. find the thick bottom bar, cut the frame off and rectify the picture upright. A near-square
     frame counts only if the bar is found, since an undecoded tag is square too.

Usage:
    python photo_finder.py FRAME [FRAME ...]    # annotated copies and crops -> data/experiments/incident_camera/photo_finder/
    python photo_finder.py FRAME --detect       # also send each big-enough crop to the LLM (costs money)
    python photo_finder.py --camera 0           # live view with the overlay (q quits, s saves crops)
"""
import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from settings import PhotoFinderConfig, TagConfig
from image_io import read_image, write_image

ARUCO_DICTS = {
    "tag16h5": cv2.aruco.DICT_APRILTAG_16h5,
    "tag25h9": cv2.aruco.DICT_APRILTAG_25h9,
    "tag36h10": cv2.aruco.DICT_APRILTAG_36h10,
    "tag36h11": cv2.aruco.DICT_APRILTAG_36h11,
}
GREEN, YELLOW, BLUE, GRAY = (40, 200, 40), (0, 210, 255), (255, 140, 0), (150, 150, 150)  # BGR


@dataclass
class TagSeen:
    id: int
    corners: np.ndarray  # (4, 2) frame pixels, OpenCV order: top-left, top-right, bottom-right, bottom-left


@dataclass
class FoundPhoto:
    # (4, 2) frame pixels, top-left, top-right, bottom-right, bottom-left of the crop. For framed
    # prints this is the picture inside the frame; otherwise the outline that was found.
    corners: np.ndarray
    inside_texture: float  # fraction of busy pixels inside
    outside_texture: float  # fraction of busy pixels in a ring around it (tags excluded)
    frame_dark: float  # fraction of near-black pixels in the band just inside the outline (the frame)
    framed: bool
    upright: bool  # the thick bottom bar was found, so the crop is the right way up
    crop: np.ndarray  # rectified grayscale picture (if not upright: long side horizontal, maybe upside down)
    big_enough: bool  # crop's short side >= min_short_side_px; otherwise fly closer before asking the LLM
    # Where this photo is on the map is not worked out here: pipeline.py takes the camera pose from
    # the AprilTags in the same frame (localizer.py, data/maps/apriltag_map.json), maps `corners` onto the
    # map plane, looks up the slot (data/maps/slots.json) and fills Report.location.

    @property
    def center(self) -> np.ndarray:
        return self.corners.mean(axis=0)

    @property
    def size_px(self) -> tuple[int, int]:
        return self.crop.shape[1], self.crop.shape[0]


# ---------- AprilTags ----------

def make_tag_detector(family: str = TagConfig().family, refine: bool = False) -> cv2.aruco.ArucoDetector:
    params = cv2.aruco.DetectorParameters()
    if refine:  # sub-pixel corners, for pose estimation
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICTS[family]), params)


def detect_tags_and_quads(gray: np.ndarray, detector: cv2.aruco.ArucoDetector | None = None):
    """Decoded tags, plus the dark-bordered quads the detector found but could not decode as tags."""
    corners, ids, rejected = (detector or make_tag_detector()).detectMarkers(gray)
    tags = [] if ids is None else [TagSeen(int(i), c.reshape(4, 2)) for i, c in zip(ids.ravel(), corners)]
    return tags, [r.reshape(4, 2) for r in rejected]


def detect_tags(gray: np.ndarray, detector: cv2.aruco.ArucoDetector | None = None) -> list[TagSeen]:
    return detect_tags_and_quads(gray, detector)[0]


# ---------- geometry ----------

def to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY if frame.shape[2] == 4 else cv2.COLOR_BGR2GRAY)


def scaled(quad: np.ndarray, s: float) -> np.ndarray:
    c = quad.mean(axis=0)
    return c + (quad - c) * s


def quad_area(q: np.ndarray) -> float:
    return abs(cv2.contourArea(q.astype(np.float32)))


def overlap(a: np.ndarray, b: np.ndarray) -> float:
    """Intersection area over the smaller quad's area."""
    inter, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
    return inter / max(min(quad_area(a), quad_area(b)), 1e-9)


def order_corners(q: np.ndarray) -> np.ndarray:
    """Clockwise on screen from top-left, with a long side on top and the top side nearer the frame top."""
    c = q.mean(axis=0)
    q = q[np.argsort(np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0]))]  # y points down: ascending = clockwise
    side = lambda i: np.linalg.norm(q[(i + 1) % 4] - q[i])
    if side(0) + side(2) < side(1) + side(3):
        q = np.roll(q, -1, axis=0)
    if q[0, 1] + q[1, 1] > q[2, 1] + q[3, 1]:
        q = np.roll(q, -2, axis=0)
    return q


def shape_ok(q: np.ndarray, frame_shape, cfg: PhotoFinderConfig) -> bool:
    h, w = frame_shape[:2]
    m = cfg.edge_margin_px
    if q[:, 0].min() < m or q[:, 1].min() < m or q[:, 0].max() > w - 1 - m or q[:, 1].max() > h - 1 - m:
        return False  # at the image border: part of the photo (or a tag) is probably cut off
    if not cfg.min_area_frac * w * h <= quad_area(q) <= cfg.max_area_frac * w * h:
        return False
    for i in range(4):
        a, b = q[i - 1] - q[i], q[(i + 1) % 4] - q[i]
        cos = a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)
        if abs(math.degrees(math.acos(np.clip(cos, -1, 1))) - 90) > cfg.max_angle_error_deg:
            return False
    return aspect(q) <= cfg.aspect_range[1]  # too square is decided later: it needs the bottom bar


def aspect(q: np.ndarray) -> float:
    """Long side / short side of a quad (averaging opposite sides)."""
    sides = [np.linalg.norm(q[(i + 1) % 4] - q[i]) for i in range(4)]
    width, height = (sides[0] + sides[2]) / 2, (sides[1] + sides[3]) / 2
    return max(width, height) / max(min(width, height), 1e-9)


# ---------- texture ----------

def noise_sigma(gray: np.ndarray) -> float:
    """Sensor noise estimate (Immerkaer 1996); edges push it up a little, which is the safe side."""
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
    conv = cv2.filter2D(gray.astype(np.float32), -1, k)[1:-1, 1:-1]
    return math.sqrt(math.pi / 2) * float(np.abs(conv).mean()) / 6


def texture_map(gray: np.ndarray, cfg: PhotoFinderConfig) -> np.ndarray:
    """Boolean map of busy pixels: local standard deviation (5x5, after light blur) above the noise."""
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 0.8)
    mean = cv2.blur(g, (5, 5))
    std = np.sqrt(np.maximum(cv2.blur(g * g, (5, 5)) - mean * mean, 0))
    # A 0.8 px Gaussian keeps about 0.35 of white noise's standard deviation
    threshold = max(cfg.texture_min_std, cfg.texture_noise_factor * 0.35 * noise_sigma(gray))
    return std > threshold


def region_mean(values: np.ndarray, valid: np.ndarray, outer: np.ndarray, inner: np.ndarray | None = None) -> float:
    """Mean of `values` over pixels inside `outer` (and outside `inner`) where `valid` is set."""
    h, w = values.shape
    x0, y0 = np.maximum(np.floor(outer.min(axis=0)).astype(int), 0)
    x1, y1 = min(int(np.ceil(outer[:, 0].max())) + 1, w), min(int(np.ceil(outer[:, 1].max())) + 1, h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.fillPoly(mask, [np.round(outer - [x0, y0]).astype(np.int32)], 1)
    if inner is not None:
        cv2.fillPoly(mask, [np.round(inner - [x0, y0]).astype(np.int32)], 0)
    sel = (mask > 0) & valid[y0:y1, x0:x1]
    return float(values[y0:y1, x0:x1][sel].mean()) if sel.any() else 0.0


# ---------- candidates ----------

def quads_from_mask(mask: np.ndarray, min_area: float):
    """Rough quads per contour: a 4-point simplification of its hull and its minimum-area rectangle.

    The rectangle helps when dark parts of a photo touch its frame and make the contour ragged."""
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        hull = cv2.convexHull(contour)
        if cv2.contourArea(hull) < min_area:
            continue
        perimeter = cv2.arcLength(hull, True)
        for eps in (0.02, 0.035, 0.05, 0.08):
            approx = cv2.approxPolyDP(hull, eps * perimeter, True)
            if len(approx) == 4:
                yield approx.reshape(4, 2).astype(np.float32)
                break
            if len(approx) < 4:
                break
        yield cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)


def line_intersection(p1, d1, p2, d2) -> np.ndarray | None:
    a = np.array([d1, -d2]).T
    if abs(np.linalg.det(a)) < 1e-9:
        return None
    s = np.linalg.solve(a, p2 - p1)[0]
    return p1 + s * d1


def snap_to_edges(q: np.ndarray, gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    """Move each side of a rough quad onto the strongest parallel edge nearby (robust line fit), and
    take the corners where the fitted lines meet. Sides without a clear edge stay where they are."""
    lines = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        length = float(np.linalg.norm(b - a))
        d = (b - a) / max(length, 1e-9)
        n = np.array([-d[1], d[0]])
        reach = max(3.0, 0.05 * length)
        along = a + np.linspace(0.1, 0.9, 24)[:, None] * (b - a)
        offsets = np.arange(-reach, reach + 1e-9, 0.5)
        pts = (along[:, None, :] + offsets[None, :, None] * n).astype(np.float32)
        sample = lambda img: cv2.remap(img, pts[..., 0], pts[..., 1], cv2.INTER_LINEAR)
        response = np.abs(sample(gx) * n[0] + sample(gy) * n[1])  # gradient across the side only
        best = response.argmax(axis=1)
        strength = response[np.arange(len(along)), best]
        keep = strength > max(20.0, 0.3 * float(np.median(strength)))
        if keep.sum() < 8:
            lines.append((a, d))
            continue
        edge = pts[np.arange(len(along)), best][keep]
        vx, vy, x0, y0 = cv2.fitLine(edge, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0]), np.array([vx, vy])))
    out = []
    for i in range(4):
        corner = line_intersection(*lines[i - 1], *lines[i])
        if corner is None or np.linalg.norm(corner - q[i]) > 0.15 * np.linalg.norm(q[(i + 1) % 4] - q[i - 1]):
            return q  # the fit went somewhere else entirely: keep the rough quad
        out.append(corner)
    return np.array(out, np.float32)


def find_photos(frame: np.ndarray, cfg: PhotoFinderConfig = PhotoFinderConfig(),
                tags: list[TagSeen] | None = None, marker_quads: list[np.ndarray] | None = None) -> list[FoundPhoto]:
    """Printed photos fully inside the frame, largest first. Pass `tags`/`marker_quads` from
    detect_tags_and_quads() if you already ran the tag detector on this frame."""
    gray = to_gray(frame)
    h, w = gray.shape
    if tags is None or marker_quads is None:
        seen, quads = detect_tags_and_quads(gray)
        tags = seen if tags is None else tags
        marker_quads = quads if marker_quads is None else marker_quads
    # Tag areas (with their white quiet zone) are neither photo nor map: remove them everywhere
    tag_mask = np.zeros((h, w), np.uint8)
    for t in tags:
        cv2.fillPoly(tag_mask, [np.round(scaled(t.corners, 1.35)).astype(np.int32)], 1)
    not_tag = tag_mask == 0

    texture = texture_map(gray, cfg) & not_tag
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    block = max(15, (min(h, w) // 12) | 1)
    dark = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, 12)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    tex_f = texture.astype(np.float32)
    min_area = cfg.min_area_frac * w * h

    # Near-black only: the printed frame is much darker than any map color, while the adaptive
    # `dark` mask also takes in gray buildings next to white floor and can swallow the frame
    k = max(31, (min(h, w) // 4) | 1)
    relative = blur.astype(np.float32) / np.maximum(cv2.blur(blur, (k, k)).astype(np.float32), 1)  # vs local mean
    black = (relative < 0.55).astype(np.uint8) * 255
    black_f = (black > 0).astype(np.float32)

    # Rough outlines: dark-bordered quads the tag detector proposed but could not decode (a framed
    # photo is exactly that), then contours of near-black, edge, dark and busy regions. Every outline
    # is snapped onto the image edges.
    rough = [q.reshape(4, 2).astype(np.float32) for q in marker_quads]
    for mask in (black, cv2.dilate(cv2.Canny(blur, 40, 120), np.ones((3, 3), np.uint8)), dark,
                 cv2.morphologyEx(texture.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))):
        rough += list(quads_from_mask(np.where(not_tag, mask, 0).astype(np.uint8), min_area))

    candidates = []
    for q in rough:
        q = order_corners(snap_to_edges(order_corners(q), gx, gy))
        if not shape_ok(q, gray.shape, cfg) or any(overlap(q, t.corners) > 0.3 for t in tags):
            continue
        inside = region_mean(tex_f, np.ones_like(not_tag), scaled(q, 0.8))
        if inside < cfg.min_inside_texture:
            continue
        # The printed frame: a near-black band just inside the edge and the white margin just outside.
        # Building outlines are thinner and turn gray once the camera is a little higher.
        # The margin is paper white, brighter than its surroundings; content around a dark-edged
        # detail inside a picture is not.
        frame_dark = region_mean(black_f, not_tag, q, scaled(q, 0.94))
        margin_dark = region_mean(black_f, not_tag, scaled(q, 1.05), q)
        margin_bright = region_mean(relative, not_tag, scaled(q, 1.05), q)
        framed = frame_dark >= 0.5 and margin_dark <= 0.3 and margin_bright >= cfg.min_margin_brightness
        outside = region_mean(tex_f, not_tag, scaled(q, 1.3), scaled(q, 1.08))
        if not framed:
            # Unframed: calm surroundings must rule out windows and signs inside a photo, which are
            # busy all around. (Framed ones are next to buildings and tags too often for this test;
            # for them the nesting rule below handles details inside a picture.)
            if cfg.require_frame or outside > cfg.max_outside_texture:
                continue
        # Near-square: a framed photo whose picture is nearly as tall as wide (the bar adds height),
        # or an undecoded tag. Only the photo has the bottom bar, which is checked further down.
        square = aspect(q) < cfg.aspect_range[0]
        if square and not framed:
            continue
        candidates.append((q, inside, outside, frame_dark, framed, square))

    # Overlapping candidates describe one thing, often nested: a building outline around a photo, the
    # photo's frame, a sign inside the picture. Start from the largest and step to the biggest quad
    # nested inside while the ring between them is flat (then the outer one is just the inner one
    # plus empty floor); stop when the ring is busy (then the inner one is a detail of the picture).
    candidates.sort(key=lambda c: quad_area(c[0]), reverse=True)
    used = [False] * len(candidates)
    kept = []
    for i, first in enumerate(candidates):
        if used[i]:
            continue
        group = [j for j in range(i, len(candidates)) if not used[j] and overlap(candidates[j][0], first[0]) > 0.2]
        for j in group:
            used[j] = True
        cur = first
        while True:
            inner = [candidates[j] for j in group if quad_area(candidates[j][0]) < 0.97 * quad_area(cur[0])
                     and overlap(candidates[j][0], cur[0]) > 0.9]
            if not inner:
                break
            nested = max(inner, key=lambda c: quad_area(c[0]))
            if region_mean(black_f, not_tag, cur[0], nested[0]) > 0.5:
                break  # the ring is the photo's own black frame: keep its outer edge
            ring = region_mean(tex_f, not_tag, scaled(cur[0], 0.95), scaled(nested[0], 1.08))
            if ring >= cfg.max_ring_texture:
                break
            cur = nested
        kept.append(cur)

    photos = []
    for q, inside, outside, frame_dark, framed, square in kept:
        picture, upright = upright_picture(relative, q) if framed else (q, False)
        if square and not upright:
            continue  # square, evenly bordered: an undecoded tag, not a photo
        crop = rectify(gray, picture)
        photos.append(FoundPhoto(picture, inside, outside, frame_dark, framed, upright, crop,
                                 min(crop.shape) >= cfg.min_short_side_px))
    return photos


def rectify(gray: np.ndarray, q: np.ndarray) -> np.ndarray:
    side = lambda i: np.linalg.norm(q[(i + 1) % 4] - q[i])
    cw, ch = max(2, round((side(0) + side(2)) / 2)), max(2, round((side(1) + side(3)) / 2))
    dst = np.float32([[0, 0], [cw - 1, 0], [cw - 1, ch - 1], [0, ch - 1]])
    return cv2.warpPerspective(gray, cv2.getPerspectiveTransform(q.astype(np.float32), dst), (cw, ch),
                               flags=cv2.INTER_LINEAR)


def upright_picture(relative: np.ndarray, q: np.ndarray) -> tuple[np.ndarray, bool]:
    """Find the black frame on each side of a framed photo, cut it off, and turn the photo so the
    thick bar (printed under the picture) is at the bottom.

    `q` is the frame's outer edge. Works on a slightly enlarged rectified view of `relative`
    (brightness / local mean), scanning in from each side to the first black band. Returns the
    picture's corners (TL, TR, BR, BL, upright when the bar was found) and whether it was found."""
    big = scaled(q, 1.04)
    side = lambda a, i: np.linalg.norm(a[(i + 1) % 4] - a[i])
    w = max(16, round((side(big, 0) + side(big, 2)) / 2))
    h = max(16, round((side(big, 1) + side(big, 3)) / 2))
    m = cv2.getPerspectiveTransform(big.astype(np.float32), np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]))
    black = cv2.warpPerspective(relative, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=1.0) < 0.55

    def band(strip: np.ndarray):
        """(start, end) depth of the first band that is black along almost the whole side. strip is
        (depth from the edge inward, position along the side). Dark picture content rarely runs
        along a whole side; when it does, it widens that side's band (handled below)."""
        full = strip.mean(axis=1) >= 0.85
        idx = np.flatnonzero(full)
        if len(idx) == 0:
            return None
        a = idx[0]
        after = np.flatnonzero(~full[a:])
        return float(a), float(a + (after[0] if len(after) else len(full) - a))

    dh, dw = max(3, int(h * 0.4)), max(3, int(w * 0.4))
    cols, rows = slice(int(w * 0.15), int(w * 0.85)), slice(int(h * 0.15), int(h * 0.85))
    bands = [band(black[:dh, cols]),               # top: rows going down
             band(black[rows, ::-1][:, :dw].T),    # right: columns going left
             band(black[::-1, cols][:dh]),         # bottom: rows going up
             band(black[rows, :dw].T)]             # left: columns going right
    if any(b is None for b in bands):
        return q, False
    thick = [end - start for start, end in bands]
    ends = [end for _, end in bands]
    pic = np.float32([[ends[3], ends[0]], [w - 1 - ends[1], ends[0]],
                      [w - 1 - ends[1], h - 1 - ends[2]], [ends[3], h - 1 - ends[2]]])
    if pic[1, 0] - pic[0, 0] < 4 or pic[3, 1] - pic[0, 1] < 4:
        return q, False
    picture = cv2.perspectiveTransform(pic.reshape(-1, 1, 2), np.linalg.inv(m)).reshape(4, 2)
    # The bar must stand out from all three other sides, and those three (plain frame) must agree.
    # Dark picture content along one edge widens that side; if it rivals the bar or makes the
    # other three disagree, the bottom is not known.
    k = int(np.argmax(thick))
    others = sorted(t for i, t in enumerate(thick) if i != k)
    if thick[k] < 2 or thick[k] < 1.8 * others[-1] or others[-1] > 1.6 * others[0] + 1:
        return picture, False
    return np.roll(picture, -(k - 2), axis=0), True  # the thick side becomes the bottom (side 2)


# ---------- display ----------

def annotate(frame: np.ndarray, photos: list[FoundPhoto], tags: list[TagSeen]) -> np.ndarray:
    """Color copy of the frame with tags (blue), photos (green, yellow when too small) and a status banner."""
    out = cv2.cvtColor(to_gray(frame), cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame[..., :3].copy()
    s = out.shape[1] / 640  # text scale relative to a 640 px wide frame
    font, thick = cv2.FONT_HERSHEY_SIMPLEX, max(1, round(2 * s))
    for t in tags:
        cv2.polylines(out, [np.round(t.corners).astype(np.int32)], True, BLUE, thick)
        cv2.putText(out, str(t.id), tuple(np.round(t.corners.mean(0)).astype(int)), font, 0.45 * s, BLUE, thick)
    for k, p in enumerate(photos, 1):
        color = GREEN if p.big_enough else YELLOW
        cv2.polylines(out, [np.round(p.corners).astype(np.int32)], True, color, thick + 1)
        label = f"PHOTO {k}" + ("" if p.big_enough else " (fly closer)")
        x, y = np.round(p.corners[0]).astype(int)
        cv2.putText(out, label, (x, max(y - 6, 14)), font, 0.55 * s, color, thick)
    banner = f"PHOTO DETECTED x{len(photos)}" if photos else "no photo"
    cv2.rectangle(out, (0, 0), (out.shape[1], round(30 * s)), GREEN if photos else GRAY, -1)
    cv2.putText(out, banner + f"   tags: {len(tags)}", (8, round(21 * s)), font, 0.65 * s, (0, 0, 0), thick)
    return out


# ---------- CLI ----------

def describe(p: FoundPhoto) -> str:
    w, h = p.size_px
    return (f"center ({p.center[0]:.0f}, {p.center[1]:.0f}) px, crop {w}x{h} px, "
            f"{'framed' if p.framed else 'unframed'}, texture in {p.inside_texture:.2f} / out {p.outside_texture:.2f}"
            + ("" if p.big_enough else "  [too small for the LLM: fly closer]"))


def run_on_files(paths: list[Path], cfg: PhotoFinderConfig, send_to_llm: bool) -> None:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    detector = None
    if send_to_llm:
        from config import Config
        from detector import Detector  # imported only here: plain detection must not need an API key
        detector = Detector(Config())
    for path in paths:
        frame = read_image(path, cv2.IMREAD_UNCHANGED)
        if frame is None:
            print(f"{path}: cannot read")
            continue
        tags, quads = detect_tags_and_quads(to_gray(frame))
        photos = find_photos(frame, cfg, tags, quads)
        write_image(cfg.out_dir / f"{path.stem}_annotated.png", annotate(frame, photos, tags))
        status = f"PHOTO DETECTED x{len(photos)}" if photos else "no photo"
        print(f"{path.name}: {status}; tags seen {sorted(t.id for t in tags)}")
        for k, p in enumerate(photos, 1):
            crop_path = cfg.out_dir / f"{path.stem}_photo{k}.png"
            write_image(crop_path, p.crop)
            print(f"  photo {k}: {describe(p)} -> {crop_path.name}")
            # map position: python pipeline.py FRAME runs this finder together with the localizer
            if detector and p.big_enough:
                report, usage = detector.detect(crop_path, image_id=f"{path.stem}_photo{k}", run_id="photo_finder")
                d = report.detection
                crop_path.with_suffix(".json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
                print(f"    LLM: {'ABNORMAL' if d.is_abnormal else 'normal'} {d.category} urgency={d.urgency.value} "
                      f"review={d.needs_human_review} (${usage.cost_usd:.5f})")


def run_live(camera: int, cfg: PhotoFinderConfig) -> None:
    """Live overlay from an OpenCV camera (USB/UVC). Not tested with the team's grayscale module."""
    cap = cv2.VideoCapture(camera)
    if not cap.isOpened():
        sys.exit(f"cannot open camera {camera}")
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    detector, saved = make_tag_detector(), 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        tags, quads = detect_tags_and_quads(to_gray(frame), detector)
        photos = find_photos(frame, cfg, tags, quads)
        cv2.imshow("photo_finder (q quits, s saves crops)", annotate(frame, photos, tags))
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("s"):
            for p in photos:
                saved += 1
                write_image(cfg.out_dir / f"live_photo{saved}.png", p.crop)
            print(f"saved {len(photos)} crop(s)")
    cap.release()
    cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("frames", nargs="*", type=Path)
    parser.add_argument("--camera", type=int, help="OpenCV camera index for a live view")
    parser.add_argument("--detect", action="store_true", help="send each big-enough crop to the LLM (costs money)")
    args = parser.parse_args()
    cfg = PhotoFinderConfig()
    if args.camera is not None:
        run_live(args.camera, cfg)
    elif args.frames:
        run_on_files(args.frames, cfg, args.detect)
    else:
        parser.error("give frame files or --camera N")


if __name__ == "__main__":
    main()
