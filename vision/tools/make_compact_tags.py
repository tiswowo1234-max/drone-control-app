"""AprilTag 인쇄 파일(간단판): A4 한 장에 번호 + 태그만 12개씩.

조원 원본(`drone-localization-main/data/print/markers/tags/`)은 태그마다 설명·위치 그림이 있는 A4 한 장이라
39장이 필요하다. 이 파일은 같은 태그를 같은 크기로 그려 4장에 모은다.

- 태그 모양: 조원 map_layout.tag_image와 같다 — OpenCV DICT_APRILTAG_36h11, 검은 정사각형 8칸(테두리 1칸 포함),
  흰 여백 1칸. 크기는 vision/teammate/data/maps/apriltag_map.json의 tag_size_m(40mm)·paper_size_m(50mm).
- 태그 위쪽 = 번호가 적힌 쪽. 지도에 붙이는 위치·방향은 조원 layout_guide.png / map_with_tags.png를 본다.
- 회색 선 = 자르는 선(50mm 정사각형). 반드시 100%(실제 크기)로 인쇄하고 위쪽 50mm 막대를 자로 확인.

실행:  vision\\.venv\\Scripts\\python.exe vision\\tools\\make_compact_tags.py
결과:  vision\\print\\apriltags_compact.pdf (+ 확인용 첫 장 PNG)
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

VISION = Path(__file__).resolve().parents[1]
TAG_MAP = VISION / "teammate" / "data" / "maps" / "apriltag_map.json"
OUT_DIR = VISION / "print"

A4 = (210.0, 297.0)          # mm
MARGIN = 12.0                # 페이지 여백
COLS, ROWS = 3, 4            # 한 장에 12개
COL_GAP, ROW_GAP = 8.0, 4.0  # 자를 때 여유
LABEL_H = 6.0                # 태그 위 번호 줄
HEADER_H = 18.0              # 제목 + 50mm 막대


def korean_font() -> str:
    for name in ("Malgun Gothic", "NanumGothic", "Apple SD Gothic Neo", "Noto Sans CJK KR"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            return name
    return "DejaVu Sans"


def marker_bits(tag_id: int):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    img = cv2.aruco.generateImageMarker(d, tag_id, 8, borderBits=1)  # 8x8 픽셀 = 8x8칸
    return img < 128  # True = 검은 칸


def draw_tag(ax, x0: float, y0: float, tag_id: int, tag_mm: float, paper_mm: float) -> None:
    """(x0, y0) = 자르는 정사각형의 왼쪽 아래(mm). 위쪽이 태그 위쪽."""
    cell = tag_mm / 8
    q = (paper_mm - tag_mm) / 2
    bits = marker_bits(tag_id)
    for r in range(8):
        c = 0
        while c < 8:  # 가로로 이어진 검은 칸은 한 사각형으로 (PDF 이음새 방지)
            if not bits[r, c]:
                c += 1
                continue
            c2 = c
            while c2 < 8 and bits[r, c2]:
                c2 += 1
            x = x0 + q + c * cell
            y = y0 + q + (7 - r) * cell  # 그림의 0행 = 위쪽
            ax.add_patch(Rectangle((x, y), (c2 - c) * cell, cell + 0.02, fc="black", ec="none", lw=0))
            c = c2
    ax.add_patch(Rectangle((x0, y0), paper_mm, paper_mm, fill=False, ec="#9a9a9a", lw=0.4))  # 자르는 선


def page(ids: list[int], pno: int, npages: int, tag_mm: float, paper_mm: float, family: str):
    fig = plt.figure(figsize=(A4[0] / 25.4, A4[1] / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, A4[0])
    ax.set_ylim(0, A4[1])
    ax.set_aspect("equal")
    ax.axis("off")
    top = A4[1] - MARGIN
    ax.text(MARGIN, top - 4, f"AprilTag {family}  ·  검은 정사각형 {tag_mm:g} mm  ·  자르는 크기 {paper_mm:g} mm"
            f"  ·  {pno}/{npages}쪽", fontsize=9.5, weight="bold", va="center")
    ax.text(MARGIN, top - 9.5, "100%(실제 크기)로 인쇄 → 아래 막대가 50 mm인지 자로 확인 → 회색 선을 따라 자르기. "
            "번호 쪽이 태그 위쪽.", fontsize=7.5, va="center", color="#333")
    bar_y = top - 15
    ax.add_patch(Rectangle((MARGIN, bar_y), 50, 1.6, fc="black", ec="none"))
    ax.text(MARGIN + 52, bar_y + 0.8, "← 50 mm", fontsize=7.5, va="center")

    grid_w = COLS * paper_mm + (COLS - 1) * COL_GAP
    left = (A4[0] - grid_w) / 2
    y_top = top - HEADER_H
    for k, tag_id in enumerate(ids):
        r, c = divmod(k, COLS)
        x0 = left + c * (paper_mm + COL_GAP)
        y0 = y_top - LABEL_H - paper_mm - r * (LABEL_H + paper_mm + ROW_GAP)
        ax.text(x0 + paper_mm / 2, y0 + paper_mm + LABEL_H / 2, f"ID {tag_id}  ▲", fontsize=10,
                weight="bold", ha="center", va="center")
        draw_tag(ax, x0, y0, tag_id, tag_mm, paper_mm)
    return fig


def main() -> None:
    plt.rcParams["font.family"] = korean_font()
    plt.rcParams["axes.unicode_minus"] = False
    meta = json.loads(TAG_MAP.read_text(encoding="utf-8"))
    tag_mm, paper_mm = meta["tag_size_m"] * 1000, meta["paper_size_m"] * 1000
    family = meta.get("family", "tag36h11")
    ids = sorted(t["id"] for t in meta["tags"])
    per = COLS * ROWS
    chunks = [ids[i:i + per] for i in range(0, len(ids), per)]
    OUT_DIR.mkdir(exist_ok=True)
    pdf_path = OUT_DIR / "apriltags_compact.pdf"
    with PdfPages(pdf_path) as pdf:
        for p, chunk in enumerate(chunks, 1):
            fig = page(chunk, p, len(chunks), tag_mm, paper_mm, family)
            pdf.savefig(fig)
            if p == 1:  # 확인용: 첫 장을 고해상도 PNG로도 저장 (인식 검사용)
                fig.savefig(OUT_DIR / "apriltags_compact_page1_check.png", dpi=600)
            plt.close(fig)
    print(f"{pdf_path}  ({len(ids)}개, {len(chunks)}장, 태그 {tag_mm:g} mm / 종이 {paper_mm:g} mm)")


if __name__ == "__main__":
    main()
