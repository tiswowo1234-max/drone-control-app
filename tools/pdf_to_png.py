"""지도 PDF를 앱에서 쓸 PNG 이미지로 변환한다.

사용법 (프로젝트 폴더에서):
    tools\\.venv\\Scripts\\python.exe tools\\pdf_to_png.py

입력: map/KAIST_map_PRINT_preview.pdf
      (인쇄용 원본 map/KAIST_map_PRINT_2500x2500mm.eps 와 같은 내용의 미리보기 PDF)
결과: app/assets/map.png (2500x2500px, 1px = 1mm)

- 확정 지도(2026-11-11)는 페이지 전체가 지도다(흰 바탕 포함, 여백 없음).
  그래서 자르지 않고 페이지 전체를 변환한다. 앱 좌표 (0,0)은 페이지 왼쪽 아래다.
- 지도가 바뀌면 이 스크립트를 다시 실행하면 된다.
"""
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "map" / "KAIST_map_PRINT_preview.pdf"
DST = ROOT / "app" / "assets" / "map.png"
TARGET_WIDTH_PX = 2500


def main() -> None:
    doc = pymupdf.open(SRC)
    page = doc[0]
    zoom = TARGET_WIDTH_PX / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    DST.parent.mkdir(parents=True, exist_ok=True)
    pix.save(DST)
    print(f"page {page.rect.width:.1f}x{page.rect.height:.1f}pt")
    print(f"saved {DST} ({pix.width}x{pix.height}px, ratio {pix.width / pix.height:.4f})")


if __name__ == "__main__":
    main()
