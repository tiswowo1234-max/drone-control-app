"""슬롯 보드: 사진 슬롯 S1~S4 양옆에 AprilTag를 2개씩(슬롯당 4개, ID 39~54) 더 붙이는 설계 도구.

왜: 고도 50cm·가운데 영역(tt_tag_roi 0.7)에서는 슬롯 위 호버링 때 보이는 태그가 거의 없었다
(S4 근처 2개 이상 보일 확률 3%, 2026-11-23 계산). 슬롯 위에서 항상 2개 이상 보이게 한다.

설계: 슬롯(150×100mm)의 짧은 두 변에 태그 종이(50mm) 두 장씩을 맞대어 세로로 쌓는다.
      → 보드 전체 250×100mm. 태그 중심은 슬롯 중심에서 (±100, ±25)mm, 태그 위쪽 = 슬롯 위쪽(up_xy).
      보드는 한 장으로 인쇄하므로 태그·사진 자리의 상대 위치가 정확하다.

조원 원본(vision/teammate/data/maps/)은 고치지 않는다. 결과는 vision/data/maps/ 에 같은 이름으로 쓴다:
  apriltag_map.json            조원 39개 + 슬롯 태그 16개 (형식·지도 확인값 그대로)
  map_with_photos(_gray).png   합성 카메라용 지도 그림 (조원 그림 위에 슬롯 태그를 그림)
  map_with_tags(_gray).png
  slot_boards.json             슬롯별 태그 id와 위치 (안내·시험용)
연결 프로그램은 [bridge] extra_tags = true 면 이 파일들을 쓴다.

실행:  vision\\.venv\\Scripts\\python.exe vision\\tools\\make_slot_boards.py
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Polygon

VISION = Path(__file__).resolve().parents[1]
SRC = VISION / "teammate" / "data" / "maps"
OUT = VISION / "data" / "maps"
FIRST_ID = 39
PX_PER_M = 2000.0  # 조원 map_with_*.png: 5000px = 2.5m (0.5mm/px), 픽셀 (2000x, 2000(2.5-y))


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def tag_entry(tag_id, center, right, up, side, paper, slot_no, where):
    a, p = side / 2, paper / 2
    c = np.asarray(center, float)
    corners = [c - right * a + up * a, c + right * a + up * a, c + right * a - up * a, c - right * a - up * a]
    papers = [c - right * p + up * p, c + right * p + up * p, c + right * p - up * p, c - right * p - up * p]
    return {
        "id": tag_id,
        "center_xy_m": [round(float(v), 6) for v in c],
        "yaw_deg": round(math.degrees(math.atan2(right[1], right[0])), 3),  # map +x → tag +x, 반시계
        "corners_xy_m": [[round(float(x), 6), round(float(y), 6)] for x, y in corners],
        "paper_corners_xy_m": [[round(float(x), 6), round(float(y), 6)] for x, y in papers],
        "kind": "slot_board",
        "place": f"slot board S{slot_no}, {where}",
        "slot": slot_no,
    }


# 배치 방식 (슬롯 중심 기준 (right, up) 방향 태그 중심 오프셋, 단위: 종이 크기 p, 슬롯 크기 sw·sh)
#  A "sides": 짧은 두 변 바깥에 두 장씩 세로로 → 보드 (sw+2p) × sh
#  B "ends" : 긴 두 변 바깥에 두 장씩 가로로   → 보드 sw × (sh+2p)
# 기존 태그와 겹치지 않는 첫 방식을 쓴다.
LAYOUTS = {
    "sides": lambda sw, sh, p: ([(-(sw / 2 + p / 2), p / 2, "left upper"), (-(sw / 2 + p / 2), -p / 2, "left lower"),
                                 ((sw / 2 + p / 2), p / 2, "right upper"), ((sw / 2 + p / 2), -p / 2, "right lower")],
                                (sw + 2 * p, sh)),
    "ends": lambda sw, sh, p: ([(-0.8 * p, sh / 2 + p / 2, "top left"), (0.8 * p, sh / 2 + p / 2, "top right"),
                                (-0.8 * p, -(sh / 2 + p / 2), "bottom left"), (0.8 * p, -(sh / 2 + p / 2), "bottom right")],
                               (sw, sh + 2 * p)),
}


def _overlaps(entry, old_papers):
    pg = Polygon(entry["paper_corners_xy_m"])
    return [i for i, op in enumerate(old_papers) if pg.intersects(op) and pg.intersection(op).area > 1e-8]


def design(tagdoc, slotdoc):
    side, paper = tagdoc["tag_size_m"], tagdoc["paper_size_m"]
    sw, sh = slotdoc["slot_size_m"]
    old_papers = [Polygon(t["paper_corners_xy_m"]) for t in tagdoc["tags"]]
    new, boards = [], []
    tid = FIRST_ID
    for sl in slotdoc["slots"]:
        cs = np.asarray(sl["corners_xy_m"], float)  # TL, TR, BR, BL
        center = cs.mean(axis=0)
        right = unit(cs[1] - cs[0])
        up = unit(sl["up_xy"])
        for layout, make in LAYOUTS.items():
            offsets, (bw, bh) = make(sw, sh, paper)
            cand = [tag_entry(tid + k, center + right * ox + up * oy, right, up, side, paper, sl["slot"], where)
                    for k, (ox, oy, where) in enumerate(offsets)]
            if not any(_overlaps(t, old_papers) for t in cand):
                break
        else:
            raise SystemExit(f"S{sl['slot']}: 어떤 방식도 기존 태그와 겹칩니다")
        new += cand
        ids = [t["id"] for t in cand]
        tid += len(cand)
        board = [center - right * bw / 2 + up * bh / 2, center + right * bw / 2 + up * bh / 2,
                 center + right * bw / 2 - up * bh / 2, center - right * bw / 2 - up * bh / 2]
        boards.append({"slot": sl["slot"], "layout": layout, "tag_ids": ids, "center_xy_m": center.round(6).tolist(),
                       "up_xy": up.round(6).tolist(), "right_xy": right.round(6).tolist(),
                       "board_corners_xy_m": np.round(board, 6).tolist(),
                       "board_size_mm": [round(bw * 1000, 1), round(bh * 1000, 1)],
                       "slot_size_mm": [sw * 1000, sh * 1000],
                       "tag_offsets_mm": [[round(ox * 1000, 1), round(oy * 1000, 1), w] for ox, oy, w in offsets]})
    return new, boards


def check(tagdoc, new, boards):
    size = tagdoc["map_size_m"]
    old_papers = [Polygon(t["paper_corners_xy_m"]) for t in tagdoc["tags"]]
    problems = []
    for t in new:
        pg = Polygon(t["paper_corners_xy_m"])
        xs, ys = zip(*t["paper_corners_xy_m"])
        if min(xs) < 0 or min(ys) < 0 or max(xs) > size[0] or max(ys) > size[1]:
            problems.append(f"ID {t['id']} 지도 밖")
        for o, op in zip(tagdoc["tags"], old_papers):
            if pg.intersects(op) and pg.intersection(op).area > 1e-8:
                problems.append(f"ID {t['id']}가 기존 ID {o['id']}와 겹침")
    bps = [Polygon(b["board_corners_xy_m"]) for b in boards]
    for i in range(len(bps)):
        for j in range(i + 1, len(bps)):
            if bps[i].intersects(bps[j]):
                problems.append(f"보드 S{boards[i]['slot']}·S{boards[j]['slot']} 겹침")
    nearest = min(math.dist(a["center_xy_m"], b["center_xy_m"]) for a in new for b in tagdoc["tags"])
    return problems, nearest


def marker_img(tag_id, cell_px=40):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    m = cv2.aruco.generateImageMarker(d, tag_id, 8 * cell_px, borderBits=1)
    return cv2.copyMakeBorder(m, cell_px, cell_px, cell_px, cell_px, cv2.BORDER_CONSTANT, value=255)  # 10칸 = 종이


def draw_tags(img, tags):
    """조원 지도 그림 위에 태그 종이(흰 여백 포함)를 원근 변환으로 그린다."""
    out = img.copy()
    for t in tags:
        m = marker_img(t["id"])
        n = m.shape[0]
        src = np.float32([[0, 0], [n, 0], [n, n], [0, n]])
        dst = np.float32([[x * PX_PER_M, (2.5 - y) * PX_PER_M] for x, y in t["paper_corners_xy_m"]])
        H = cv2.getPerspectiveTransform(src, dst)
        h, w = out.shape[:2]
        warped = cv2.warpPerspective(m, H, (w, h), flags=cv2.INTER_AREA, borderValue=0)
        mask = cv2.warpPerspective(np.full((n, n), 255, np.uint8), H, (w, h), flags=cv2.INTER_NEAREST) > 127
        if out.ndim == 3:
            out[mask] = np.repeat(warped[mask][:, None], 3, axis=1)
        else:
            out[mask] = warped[mask]
    return out


def main():
    tagdoc = json.loads((SRC / "apriltag_map.json").read_text(encoding="utf-8"))
    slotdoc = json.loads((SRC / "slots.json").read_text(encoding="utf-8"))
    new, boards = design(tagdoc, slotdoc)
    problems, nearest = check(tagdoc, new, boards)
    for p in problems:
        print("[문제]", p)
    if problems:
        raise SystemExit("겹치거나 지도 밖인 태그가 있어 멈춥니다")
    OUT.mkdir(parents=True, exist_ok=True)
    plus = copy.deepcopy(tagdoc)
    plus["tags"] = tagdoc["tags"] + new
    plus["extra_tags_note"] = ("drone-control-app: ids 39-54 are slot-board tags added beside photo slots S1-S4 "
                               "(vision/tools/make_slot_boards.py, 2026-11-23). ids 0-38 are the teammate's layout, unchanged.")
    (OUT / "apriltag_map.json").write_text(json.dumps(plus, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "slot_boards.json").write_text(json.dumps({"boards": boards}, ensure_ascii=False, indent=1), encoding="utf-8")
    for name in ("map_with_photos.png", "map_with_photos_gray.png", "map_with_tags.png", "map_with_tags_gray.png"):
        img = cv2.imread(str(SRC / name), cv2.IMREAD_UNCHANGED)
        cv2.imwrite(str(OUT / name), draw_tags(img, new))
    print(f"슬롯 태그 {len(new)}개 (ID {new[0]['id']}~{new[-1]['id']}), 기존 태그와 최소 중심 거리 {nearest * 100:.1f}cm")
    for b in boards:
        print(f"  S{b['slot']}: ID {b['tag_ids']}  방식 {b['layout']}, 보드 {b['board_size_mm'][0]:.0f}×{b['board_size_mm'][1]:.0f}mm")
    print(f"→ {OUT}")


if __name__ == "__main__" and "--print" not in __import__("sys").argv:
    main()


# ---------------------------------------------------------------- 인쇄 파일 + 붙이는 위치 안내도

def print_boards(boards: list, tagdoc: dict, measured_100mm: float | None = None) -> Path:
    """슬롯 보드 PDF: 한 쪽에 보드 하나(가로 보드는 A4 가로, 세로 보드는 A4 세로). 보드 위쪽 = 슬롯 위쪽."""
    import make_tag_cut_sheet as cs
    tag_mm, paper_mm = tagdoc["tag_size_m"] * 1000, tagdoc["paper_size_m"] * 1000
    scale = 100.0 / measured_100mm if measured_100mm else 1.0
    pages = []
    for b in boards:
        bw, bh = b["board_size_mm"]
        sw, sh = b["slot_size_mm"]
        size = (297.0, 210.0) if bw >= bh else (210.0, 297.0)
        pg = cs.Page(scale, size)
        W, Hh = size
        top = Hh - cs.SAFE
        pg.text(cs.SAFE, top - 4, f"Slot board S{b['slot']}  -  tags {b['tag_ids'][0]}-{b['tag_ids'][-1]}  -  "
                f"board {bw:g} x {bh:g} mm", size=11, bold=True)
        pg.text(cs.SAFE, top - 9.5, "Print at ACTUAL SIZE (100%). Check the bars. Cut on the gray outline (whole board, "
                "one piece).", size=7.5)
        bar_y = top - 16
        pg.rect(cs.SAFE, bar_y, 100, 1.2)
        pg.text(cs.SAFE + 102, bar_y - 0.2, "100 mm", size=7.5)
        pg.rect(cs.SAFE + 118, bar_y, 50, 1.2)
        pg.text(cs.SAFE + 170, bar_y - 0.2, "50 mm", size=7.5)
        # 보드: 가운데 정렬, 보드 좌표 (오른쪽=+x, 위=+y), 보드 중심 = 슬롯 중심
        cx, cy = W / 2, (bar_y - 6 + 22) / 2
        pg.rect(cx - bw / 2, cy - bh / 2, bw, bh, fill=False, gray=0.55, lw_mm=0.25)  # 자르는 선
        # 사진 자리(슬롯): 점선 + 안내
        x0, y0 = cx - sw / 2, cy - sh / 2
        for (ax, ay, bx, by) in ((x0, y0, x0 + sw, y0), (x0 + sw, y0, x0 + sw, y0 + sh),
                                 (x0 + sw, y0 + sh, x0, y0 + sh), (x0, y0 + sh, x0, y0)):
            pg.line(ax, ay, bx, by, gray=0.6, lw_mm=0.3, dash=True)
        pg.text(cx, cy + 8, f"S{b['slot']} PHOTO GOES HERE", size=11, bold=True, center=True)
        pg.text(cx, cy + 2, f"{sw:g} x {sh:g} mm, photo top toward the arrow", size=7.5, center=True)
        pg.rect(cx - 0.6, cy - 16, 1.2, 10)                 # 위쪽 화살표 (막대)
        pg.text(cx, cy - 6.5, "^", size=14, bold=True, center=True)
        pg.text(cx, cy - 22, "BOARD TOP = slot 'up' direction on the map", size=7.5, center=True)
        for (ox, oy, where), tid in zip(b["tag_offsets_mm"], b["tag_ids"]):
            cs.draw_tag(pg, cx + ox - paper_mm / 2, cy + oy - paper_mm / 2, tid, tag_mm, paper_mm)
        # 아래 안내
        lines = [f"Tag IDs: " + ", ".join(f"{tid} ({w})" for (_, _, w), tid in zip(b["tag_offsets_mm"], b["tag_ids"])),
                 "Do not cut the tags apart: their positions around the photo are part of the map layout.",
                 "Paste the whole board so the dashed photo area sits exactly on slot "
                 f"S{b['slot']} (see slot_boards_guide.png), board top toward the slot's up direction."]
        for i, s in enumerate(lines):
            pg.text(cs.SAFE, cy - bh / 2 - 9 - i * 4.5, s, size=7.5)
        pages.append(pg)
    out = VISION / "print" / "slot_boards.pdf"
    out.parent.mkdir(exist_ok=True)
    cs.write_pdf(pages, out)
    return out


def guide_image(boards: list) -> Path:
    """붙이는 위치 안내도: 슬롯마다 지도(태그 붙은 그림) 50cm 사방을 잘라 보드 외곽(빨강)·사진 자리(파랑)·위쪽(화살표) 표시."""
    img = cv2.imread(str(OUT / "map_with_photos.png"), cv2.IMREAD_COLOR)
    panels = []
    half = 0.25
    for b in boards:
        c = np.asarray(b["center_xy_m"])
        x0, x1 = (c[0] - half) * PX_PER_M, (c[0] + half) * PX_PER_M
        y0, y1 = (2.5 - c[1] - half) * PX_PER_M, (2.5 - c[1] + half) * PX_PER_M
        crop = img[int(max(0, y0)):int(y1), int(max(0, x0)):int(x1)].copy()
        def px(p):
            return (int((p[0] * PX_PER_M) - max(0, x0)), int(((2.5 - p[1]) * PX_PER_M) - max(0, y0)))
        cv2.polylines(crop, [np.array([px(p) for p in b["board_corners_xy_m"]], np.int32)], True, (0, 0, 230), 5)
        up, right = np.asarray(b["up_xy"]), np.asarray(b["right_xy"])
        sw, sh = b["slot_size_mm"][0] / 2000, b["slot_size_mm"][1] / 2000
        slot = [c - right * sw + up * sh, c + right * sw + up * sh, c + right * sw - up * sh, c - right * sw - up * sh]
        cv2.polylines(crop, [np.array([px(p) for p in slot], np.int32)], True, (230, 120, 0), 4)
        cv2.arrowedLine(crop, px(c), px(c + up * 0.12), (0, 160, 0), 6, tipLength=0.25)
        crop = cv2.resize(crop, (500, 500), interpolation=cv2.INTER_AREA)
        cv2.putText(crop, f"S{b['slot']}  tags {b['tag_ids'][0]}-{b['tag_ids'][-1]}", (12, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(crop, f"S{b['slot']}  tags {b['tag_ids'][0]}-{b['tag_ids'][-1]}", (12, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 1, cv2.LINE_AA)
        panels.append(crop)
    grid = np.vstack([np.hstack(panels[0:2]), np.hstack(panels[2:4])])
    legend = np.full((70, grid.shape[1], 3), 255, np.uint8)
    cv2.putText(legend, "red = board outline (cut line), blue = photo area, green arrow = board top. 50 x 50 cm around each slot, map north up.",
                (10, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    out = VISION / "print" / "slot_boards_guide.png"
    cv2.imwrite(str(out), np.vstack([grid, legend]))
    return out


if __name__ == "__main__" and "--print" in __import__("sys").argv:
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    td = json.loads((OUT / "apriltag_map.json").read_text(encoding="utf-8"))
    bd = json.loads((OUT / "slot_boards.json").read_text(encoding="utf-8"))["boards"]
    m = None
    if "--measured-100mm" in sys.argv:
        m = float(sys.argv[sys.argv.index("--measured-100mm") + 1])
    print(print_boards(bd, td, m))
    print(guide_image(bd))
