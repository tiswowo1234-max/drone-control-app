"""지도 PDF를 앱에서 쓸 PNG 이미지로 변환한다.

사용법 (프로젝트 폴더에서):
    tools\\.venv\\Scripts\\python.exe tools\\pdf_to_png.py

결과: app/assets/map.png (지도 영역만, 가로 2500px)
- PDF 둘레의 흰 여백은 잘라 낸다. 앱 좌표 (0,0)은 잘라 낸 이미지의 왼쪽 아래다.
- 지도 PDF가 바뀌면 이 스크립트를 다시 실행하면 된다.
"""
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "KAIST_2500mm_Map.pdf"
DST = ROOT / "app" / "assets" / "map.png"
TARGET_WIDTH_PX = 2500
WHITE_THRESHOLD = 245  # 이 값보다 밝은 픽셀은 여백으로 본다


def content_bbox(page: pymupdf.Page) -> pymupdf.Rect:
    """저해상도로 렌더링해 흰색이 아닌 영역의 경계(PDF 좌표)를 찾는다."""
    scale = 1000 / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    w, h, n, s = pix.width, pix.height, pix.n, pix.samples

    def is_content(x: int, y: int) -> bool:
        i = (y * w + x) * n
        return min(s[i], s[i + 1], s[i + 2]) < WHITE_THRESHOLD

    rows = [y for y in range(h) if any(is_content(x, y) for x in range(0, w, 2))]
    cols = [x for x in range(w) if any(is_content(x, y) for y in range(0, h, 2))]
    x0, x1, y0, y1 = cols[0], cols[-1] + 1, rows[0], rows[-1] + 1
    # 경계 픽셀 1개만큼 안쪽으로 들여 반올림 오차로 흰 줄이 남지 않게 한다
    return pymupdf.Rect((x0 + 1) / scale, (y0 + 1) / scale, (x1 - 1) / scale, (y1 - 1) / scale)


def main() -> None:
    doc = pymupdf.open(SRC)
    page = doc[0]
    clip = content_bbox(page)
    zoom = TARGET_WIDTH_PX / clip.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
    DST.parent.mkdir(parents=True, exist_ok=True)
    pix.save(DST)
    print(f"page {page.rect.width:.0f}x{page.rect.height:.0f}pt, map area {clip}")
    print(f"saved {DST} ({pix.width}x{pix.height}px, ratio {pix.width / pix.height:.4f})")


if __name__ == "__main__":
    main()
