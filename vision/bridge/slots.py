"""사건 사진 보고 (명세 9장).

- SlotCapture (method = "slot", 기본): 슬롯 네 모서리를 현재 카메라 자세로 화면에 투영 →
  ① 위치 있음 ② 네 모서리 모두 화면 안(slot_margin_px) ③ 짧은 변 ≥ min_slot_px ④ still_s 동안 이동 ≤ still_cm
  이면 슬롯 up 방향이 위로 오게 원근 변환해 crop_width_px 폭으로 자른다. 같은 슬롯은 report_cooldown_s에 한 번.
- PhotoFinderCapture (method = "photo_finder"): 조원 photo_finder.find_photos로 사진을 찾아 보고
  (조원 big_enough 대신 min_slot_px 기준, 슬롯 밖 사진은 same_place_m로 중복 판단).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import teammate  # noqa: F401
from .config import Settings, Slot
from .geometry import CameraView


@dataclass
class Capture:
    slot: int | None
    place: str | None
    center_m: tuple[float, float]
    crop: np.ndarray
    method: str
    corners_px: np.ndarray
    info: dict = field(default_factory=dict)


class MotionHistory:
    """최근 위치 기록 → '호버링 중인가'."""

    def __init__(self, still_s: float, still_m: float):
        self.still_s, self.still_m = still_s, still_m
        self.hist: deque[tuple[float, float, float]] = deque()

    def reset(self):
        self.hist.clear()

    def add(self, t: float, x: float, y: float):
        self.hist.append((t, x, y))
        while len(self.hist) > 2 and self.hist[1][0] <= t - self.still_s:
            self.hist.popleft()  # 창(still_s)보다 오래된 것은 하나만 남긴다(창 전체를 덮는지 판단용)

    def still(self, t: float) -> bool:
        if not self.hist or self.hist[0][0] > t - self.still_s + 1e-9:
            return False  # 아직 still_s만큼 지켜보지 못함
        _, x, y = self.hist[-1]
        return all(math.hypot(px - x, py - y) <= self.still_m for _, px, py in self.hist)

    def moved_m(self) -> float:
        if not self.hist:
            return math.nan
        _, x, y = self.hist[-1]
        return max(math.hypot(px - x, py - y) for _, px, py in self.hist)


def upright_corners(slot: Slot) -> np.ndarray:
    """슬롯 모서리를 (위왼쪽, 위오른쪽, 아래오른쪽, 아래왼쪽) 순서로. 위 = slot.up_xy (없으면 settings 순서 그대로)."""
    c = np.asarray(slot.corners_m, float)
    if slot.up_xy is None:
        return c
    up = np.asarray(slot.up_xy, float)
    up /= np.linalg.norm(up)
    right = np.array([up[1], -up[0]])  # 위쪽을 시계 방향으로 90° 돌린 것
    ctr = c.mean(axis=0)
    rel = c - ctr
    top = rel @ up > 0
    left = rel @ right < 0
    order = []
    for want_top, want_left in ((True, True), (True, False), (False, False), (False, True)):
        idx = np.flatnonzero((top == want_top) & (left == want_left))
        if len(idx) != 1:
            return c  # 사각형이 이상하면 그대로
        order.append(idx[0])
    return c[order]


def slot_size_m(slot: Slot) -> tuple[float, float]:
    c = upright_corners(slot)
    return float(np.linalg.norm(c[1] - c[0])), float(np.linalg.norm(c[3] - c[0]))


def rectify_to(image: np.ndarray, quad_px: np.ndarray, width: int, height: int) -> np.ndarray:
    dst = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    H = cv2.getPerspectiveTransform(np.asarray(quad_px, np.float32), dst)
    return cv2.warpPerspective(image, H, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


class SlotCapture:
    method = "slot"

    def __init__(self, s: Settings):
        self.s = s
        self.motion = MotionHistory(s.still_s, s.still_cm / 100.0)
        self.last_sent: dict[int, float] = {}
        self.corners = {sl.slot: upright_corners(sl) for sl in s.slots}

    def reset(self):
        """모드 전환: 이동 기록만 버린다(같은 슬롯 중복 막기 기록은 유지)."""
        self.motion.reset()

    def on_pose(self, t: float, x: float | None, y: float | None):
        if x is None:
            self.motion.reset()
        else:
            self.motion.add(t, x, y)

    def evaluate(self, view: CameraView, t: float) -> list[dict]:
        """슬롯마다 조건 판정 (출력·시험용). 투영 픽셀과 이유를 돌려준다."""
        out = []
        still = self.motion.still(t)
        for sl in self.s.slots:
            px = view.project(self.corners[sl.slot])
            d = {"slot": sl.slot, "px": px, "ok": False, "reason": ""}
            m = self.s.slot_margin_px
            if not np.isfinite(px).all():
                d["reason"] = "카메라 뒤"
            elif (px[:, 0].min() < m or px[:, 1].min() < m or px[:, 0].max() > view.width - 1 - m
                  or px[:, 1].max() > view.height - 1 - m):
                d["reason"] = "화면 밖"
            else:
                sides = [float(np.linalg.norm(px[(i + 1) % 4] - px[i])) for i in range(4)]
                d["short_px"] = min(sides)
                if min(sides) < self.s.min_slot_px:
                    d["reason"] = f"작음 {min(sides):.0f}px < {self.s.min_slot_px:g}px"
                elif not still:
                    moved = self.motion.moved_m() * 100
                    d["reason"] = (f"움직이는 중 (이동 {moved:.1f}cm > {self.s.still_cm:g}cm)" if moved > self.s.still_cm
                                   else f"호버링 확인 중 ({self.s.still_s:g}초 지켜봄)")
                elif t - self.last_sent.get(sl.slot, -math.inf) < self.s.report_cooldown_s:
                    d["reason"] = "최근 보고함 (cooldown)"
                else:
                    d["ok"] = True
            out.append(d)
        return out

    def check(self, image: np.ndarray, view: CameraView | None, t: float) -> list[Capture]:
        if view is None:
            return []
        caps = []
        for d in self.evaluate(view, t):
            if not d["ok"]:
                continue
            sl = next(s for s in self.s.slots if s.slot == d["slot"])
            w_m, h_m = slot_size_m(sl)
            cw = self.s.crop_width_px
            ch = max(2, round(cw * h_m / w_m))
            crop = rectify_to(image, d["px"], cw, ch)
            self.last_sent[sl.slot] = t
            caps.append(Capture(sl.slot, sl.name, sl.center_m, crop, self.method, d["px"],
                                {"short_px": round(d["short_px"], 1)}))
        return caps


class PhotoFinderCapture:
    method = "photo_finder"

    def __init__(self, s: Settings):
        from settings import LocalizerConfig
        self.s = s
        self.slot_match_m = LocalizerConfig().slot_match_m
        self.last_sent: dict[int, float] = {}
        self.recent: list[tuple[float, float, float]] = []  # 슬롯 밖 사진 (t, x, y)

    def reset(self):
        pass

    def on_pose(self, t, x, y):
        pass

    def _slot_of(self, xy) -> Slot | None:
        best, best_d = None, math.inf
        for sl in self.s.slots:
            d = -cv2.pointPolygonTest(np.asarray(sl.corners_m, np.float32), (float(xy[0]), float(xy[1])), True)
            if d < best_d:
                best, best_d = sl, d
        return best if best_d <= self.slot_match_m else None

    def check(self, image: np.ndarray, view: CameraView | None, t: float, tags=None, quads=None) -> list[Capture]:
        from photo_finder import find_photos, rectify
        if view is None:
            return []
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        caps = []
        for p in find_photos(gray, tags=tags, marker_quads=quads):
            crop_px = min(p.crop.shape[:2])
            if crop_px < self.s.min_slot_px:
                continue  # 조원 big_enough 대신 min_slot_px
            corners_m = view.pixels_to_map(p.corners)
            if not np.isfinite(corners_m).all():
                continue
            center = corners_m.mean(axis=0)
            sl = self._slot_of(center)
            corners_px = p.corners
            if sl is not None and not p.upright and sl.up_xy is not None:
                # 아래 굵은 띠를 못 찾음: 슬롯 up 쪽 변이 위로 오게 돌린다 (조원 pipeline.turn_like_slot과 같은 방법)
                up = np.asarray(sl.up_xy)
                k = max(range(4), key=lambda k: (np.roll(corners_m, -k, 0)[:2].mean(0) - np.roll(corners_m, -k, 0)[2:].mean(0)) @ up)
                corners_px = np.roll(corners_px, -k, axis=0)
            if sl is not None:
                if t - self.last_sent.get(sl.slot, -math.inf) < self.s.report_cooldown_s:
                    continue
                self.last_sent[sl.slot] = t
            else:
                self.recent = [r for r in self.recent if t - r[0] < self.s.report_cooldown_s]
                if any(math.hypot(center[0] - rx, center[1] - ry) <= self.s.same_place_m for _, rx, ry in self.recent):
                    continue
                self.recent.append((t, float(center[0]), float(center[1])))
            crop = rectify(image, np.asarray(corners_px, np.float32))
            if crop.shape[1] != self.s.crop_width_px:
                h = max(2, round(crop.shape[0] * self.s.crop_width_px / crop.shape[1]))
                crop = cv2.resize(crop, (self.s.crop_width_px, h), interpolation=cv2.INTER_CUBIC)
            caps.append(Capture(sl.slot if sl else None, sl.name if sl else None, (float(center[0]), float(center[1])),
                                crop, self.method, corners_px, {"upright": bool(p.upright), "crop_px": crop_px}))
        return caps


def make_capture(s: Settings):
    if s.method == "slot":
        return SlotCapture(s)
    if s.method == "photo_finder":
        return PhotoFinderCapture(s)
    raise ValueError(f"[incident] method는 slot/photo_finder: {s.method}")
