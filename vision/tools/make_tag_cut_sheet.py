"""AprilTag 자르기용 인쇄 파일: 태그를 빈틈없이 붙여 배치하고, 번호는 아래 '번호 배치표'에 따로 적는다.

- 태그 모양은 조원 map_layout.tag_image와 같다: OpenCV DICT_APRILTAG_36h11, 검은 정사각형 8칸(40mm),
  흰 여백 1칸(5mm) → 한 장 50mm. 크기는 vision/teammate/data/maps/apriltag_map.json에서 읽는다.
- 태그끼리 맞닿게 놓아 회색 선 한 번으로 두 장이 잘린다(자르는 선 = 50mm 격자).
- 인쇄 배율 대책
  1) PDF에 /ViewerPreferences /PrintScaling /None 을 넣어 뷰어가 기본으로 '실제 크기'로 인쇄하게 한다.
  2) 내용은 가장자리에서 15mm 이상 떨어뜨린다(프린터가 못 찍는 테두리 영향 없음).
  3) 그래도 길이가 다르면, 인쇄된 100mm 막대를 자로 잰 값을 --measured-100mm 로 주면 그만큼 키워(줄여) 다시 만든다.
     예) 97mm로 나왔으면:  python make_tag_cut_sheet.py --measured-100mm 97
- PDF는 외부 라이브러리 없이 직접 쓴다(벡터 사각형 + 기본 글꼴 Helvetica → 영문·숫자만).

실행:  vision\\.venv\\Scripts\\python.exe vision\\tools\\make_tag_cut_sheet.py [--measured-100mm 값]
결과:  vision\\print\\apriltags_cut_sheet.pdf
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

VISION = Path(__file__).resolve().parents[1]
TAG_MAP = VISION / "teammate" / "data" / "maps" / "apriltag_map.json"
OUT = VISION / "print" / "apriltags_cut_sheet.pdf"

A4 = (210.0, 297.0)   # mm
SAFE = 15.0           # 가장자리 여유(mm) — 프린터 인쇄 불가 테두리(보통 3~6mm)보다 충분히 크게
COLS, ROWS = 3, 4     # 한 장 12개 (50mm × 3 = 150mm, × 4 = 200mm)
PT = 72 / 25.4        # mm → pt


def marker_bits(tag_id: int):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    return cv2.aruco.generateImageMarker(d, tag_id, 8, borderBits=1) < 128  # True = 검은 칸, 0행 = 위


class Page:
    """mm 좌표(왼쪽 아래 원점)로 그리는 PDF 한 쪽. scale = 배율 보정(인쇄 축소를 미리 되돌림)."""

    def __init__(self, scale: float, size: tuple[float, float] = A4):
        self.ops: list[str] = []
        self.s = scale
        self.size = size  # (가로, 세로) mm — 가로 용지면 (297, 210)
        self.cx, self.cy = size[0] / 2, size[1] / 2

    def _p(self, x: float, y: float) -> tuple[float, float]:  # 페이지 가운데 기준으로 배율 적용
        return ((self.cx + (x - self.cx) * self.s) * PT, (self.cy + (y - self.cy) * self.s) * PT)

    def rect(self, x, y, w, h, fill=True, gray=0.0, lw_mm=0.2):
        px, py = self._p(x, y)
        pw, ph = w * self.s * PT, h * self.s * PT
        if fill:
            self.ops.append(f"{gray:.3f} g {px:.3f} {py:.3f} {pw:.3f} {ph:.3f} re f")
        else:
            self.ops.append(f"{gray:.3f} G {lw_mm * PT:.3f} w {px:.3f} {py:.3f} {pw:.3f} {ph:.3f} re S")

    def line(self, x1, y1, x2, y2, gray=0.55, lw_mm=0.2, dash=False):
        a, b = self._p(x1, y1)
        c, d = self._p(x2, y2)
        dash_op = "[1.5 1.5] 0 d" if dash else "[] 0 d"
        self.ops.append(f"{gray:.3f} G {lw_mm * PT:.3f} w {dash_op} {a:.3f} {b:.3f} m {c:.3f} {d:.3f} l S [] 0 d")

    def text(self, x, y, s, size=9, bold=False, center=False):
        size_s = size * self.s
        if center:  # Helvetica 평균 글자 폭 ≈ 0.55 em
            x -= len(s) * size * 0.55 / 2 / PT
        px, py = self._p(x, y)
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self.ops.append(f"0 g BT /{'F2' if bold else 'F1'} {size_s:.2f} Tf {px:.3f} {py:.3f} Td ({s}) Tj ET")

    def stream(self) -> bytes:
        return ("\n".join(self.ops)).encode("latin-1")


def draw_tag(pg: Page, x0, y0, tag_id, tag_mm, paper_mm):
    cell, q = tag_mm / 8, (paper_mm - tag_mm) / 2
    bits = marker_bits(tag_id)
    # 검은 정사각형을 통째로 칠한 뒤 흰 칸만 덮는다 → 검은 부분에 칸 사이 이음새(가는 선)가 생기지 않는다
    pg.rect(x0 + q, y0 + q, tag_mm, tag_mm)
    for r in range(8):
        c = 0
        while c < 8:
            if bits[r, c]:
                c += 1
                continue
            c2 = c
            while c2 < 8 and not bits[r, c2]:
                c2 += 1
            pg.rect(x0 + q + c * cell, y0 + q + (7 - r) * cell - 0.02, (c2 - c) * cell, cell + 0.04, gray=1.0)
            c = c2


def build_page(ids, pno, npages, tag_mm, paper_mm, scale, measured):
    pg = Page(scale)
    top = A4[1] - SAFE
    pg.text(SAFE, top - 4, f"AprilTag 36h11 cut sheet  -  black square {tag_mm:g} mm, cut size {paper_mm:g} mm"
            f"  -  page {pno}/{npages}", size=10, bold=True)
    pg.text(SAFE, top - 9.5, "Print at ACTUAL SIZE / 100% (not 'Fit'). Measure the bars: 100 mm and 50 mm. "
            "Cut on the gray lines.", size=7.5)
    if measured:
        pg.text(SAFE, top - 14, f"(scale-corrected x{scale:.4f} for a printer that printed 100 mm as {measured:g} mm)",
                size=7)
    bar_y = top - 17
    pg.rect(SAFE, bar_y, 100, 1.2)
    pg.text(SAFE + 102, bar_y - 0.2, "100 mm", size=7.5)
    pg.rect(SAFE + 118, bar_y, 50, 1.2)
    pg.text(SAFE + 170, bar_y - 0.2, "50 mm", size=7.5)

    rows = (len(ids) + COLS - 1) // COLS
    grid_w = COLS * paper_mm
    left = (A4[0] - grid_w) / 2
    grid_top = bar_y - 7
    grid_bottom = grid_top - rows * paper_mm
    # 위치 표시(바깥): 열 A~C, 행 1~4 — 아래 번호표와 짝을 맞춘다
    for c in range(COLS):
        pg.text(left + c * paper_mm + paper_mm / 2, grid_top + 2.2, "ABCDEFG"[c], size=8, bold=True, center=True)
    for r in range(rows):
        pg.text(left - 5, grid_top - r * paper_mm - paper_mm / 2 - 1, str(r + 1), size=8, bold=True)
    # 태그
    for k, tag_id in enumerate(ids):
        r, c = divmod(k, COLS)
        draw_tag(pg, left + c * paper_mm, grid_top - (r + 1) * paper_mm, tag_id, tag_mm, paper_mm)
    # 자르는 선: 맞닿은 50mm 격자 + 바깥으로 4mm 연장
    ext = 4.0
    for c in range(COLS + 1):
        x = left + c * paper_mm
        pg.line(x, grid_bottom - ext, x, grid_top + ext)
    for r in range(rows + 1):
        y = grid_top - r * paper_mm
        pg.line(left - ext, y, left + grid_w + ext, y)

    # 번호 배치표 (태그와 같은 배치)
    key_top = grid_bottom - 8
    pg.text(left, key_top, "TAG ID MAP (same layout as above, seen from the front)", size=8.5, bold=True)
    cw, ch = 22.0, 5.0
    kx = left
    ky = key_top - 4
    for c in range(COLS):
        pg.text(kx + 8 + c * cw + cw / 2, ky - 3.5, "ABCDEFG"[c], size=7.5, bold=True, center=True)
    for r in range(rows):
        yy = ky - 6 - (r + 1) * ch
        pg.text(kx + 1, yy + 1.4, str(r + 1), size=7.5, bold=True)
        for c in range(COLS):
            k = r * COLS + c
            pg.rect(kx + 8 + c * cw, yy, cw, ch, fill=False, gray=0.6, lw_mm=0.2)
            if k < len(ids):
                pg.text(kx + 8 + c * cw + cw / 2, yy + 1.4, f"ID {ids[k]}", size=8.5, bold=True, center=True)
    note_x = kx + 8 + COLS * cw + 8
    for i, s in enumerate(["Before cutting: write the ID and an up-arrow",
                           "on the BACK of each square (top of page = tag top).",
                           "Placement on the map: layout_guide.png /",
                           "map_with_tags.png in the teammate folder."]):
        pg.text(note_x, ky - 6 - i * 4.2, s, size=7)
    return pg


def write_pdf(pages: list[Page], path: Path) -> None:
    objs: list[bytes] = []

    def add(b: str | bytes) -> int:
        objs.append(b.encode("latin-1") if isinstance(b, str) else b)
        return len(objs)

    cat = add("")  # 1: Catalog (나중에 채움)
    pages_id = add("")  # 2: Pages
    f1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for pg in pages:
        w, h = pg.size[0] * PT, pg.size[1] * PT  # 쪽마다 크기(가로·세로 용지)가 다를 수 있다
        data = pg.stream()
        cid = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {w:.4f} {h:.4f}] "
                        f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> /Contents {cid} 0 R >>"))
    objs[pages_id - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
                          ).encode("latin-1")
    # PrintScaling /None: 뷰어가 인쇄 창을 열 때 기본값을 '실제 크기'로 (Acrobat 등이 따름)
    objs[cat - 1] = (f"<< /Type /Catalog /Pages {pages_id} 0 R "
                     f"/ViewerPreferences << /PrintScaling /None /PickTrayByPDFSize true >> >>").encode("latin-1")
    out = bytearray(b"%PDF-1.6\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, b in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + b + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offsets:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {cat} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--measured-100mm", type=float, help="이전 인쇄에서 100mm 막대를 자로 잰 길이(mm). 그만큼 보정한다")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    meta = json.loads(TAG_MAP.read_text(encoding="utf-8"))
    tag_mm, paper_mm = meta["tag_size_m"] * 1000, meta["paper_size_m"] * 1000
    ids = sorted(t["id"] for t in meta["tags"])
    scale = 1.0
    if a.measured_100mm:
        if not 80 <= a.measured_100mm <= 120:
            raise SystemExit("--measured-100mm는 80~120 사이여야 합니다 (100mm 막대를 잰 값)")
        scale = 100.0 / a.measured_100mm
        content_h = A4[1] - SAFE - 20.0  # 실제 내용 높이(머리말~번호표, 약 262mm). 가운데 기준으로 커진다
        if content_h * scale > A4[1] - 2 * 6:  # 프린터 인쇄 불가 테두리(약 6mm) 안에 들어가야 함
            raise SystemExit("보정 배율이 너무 커서 한 장에 들어가지 않습니다")
    per = COLS * ROWS
    chunks = [ids[i:i + per] for i in range(0, len(ids), per)]
    pages = [build_page(ch, i + 1, len(chunks), tag_mm, paper_mm, scale, a.measured_100mm)
             for i, ch in enumerate(chunks)]
    a.out.parent.mkdir(exist_ok=True)
    write_pdf(pages, a.out)
    print(f"{a.out}  ({len(ids)}개, {len(pages)}장, 태그 {tag_mm:g}mm / 종이 {paper_mm:g}mm, 배율 보정 x{scale:.4f})")


if __name__ == "__main__":
    main()
