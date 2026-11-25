"""UGV 윗면에 붙일 AprilTag 인쇄물 만들기 (A4 PDF + PNG, 실제 크기).

    cd vision
    .venv\\Scripts\\python tools\\make_ugv_tag.py              # config/settings.toml [ugv]의 번호·크기
    .venv\\Scripts\\python tools\\make_ugv_tag.py --id 101 --size-mm 60

인쇄할 때 꼭: **실제 크기(100%)**, "페이지에 맞춤" 끄기. 인쇄 후 검은 사각형 한 변을 자로 재서
설정 [ugv] tag_size_cm에 그 값을 넣는다 (거리 계산이 이 값에 비례). 10cm 눈금자도 같이 재 보면 배율 확인이 된다.
화살표(FRONT)가 UGV 앞쪽을 보게 붙인다. 점선대로 자르면 흰 여백이 남는데, 여백은 태그 인식에 필요하니 자르지 않는다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import config as cfgmod  # noqa: E402
from bridge.ugv import ARUCO_DICT, MAP_TAG_IDS  # noqa: E402

DPI = 600
A4_MM = (210.0, 297.0)


def mm(v: float) -> int:
    return int(round(v / 25.4 * DPI))


def main(argv=None):
    s = cfgmod.load()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--id", type=int, default=s.ugv.tag_id, help=f"태그 번호 (기본 [ugv] tag_id = {s.ugv.tag_id})")
    p.add_argument("--size-mm", type=float, default=s.ugv.tag_size_m * 1000,
                   help=f"검은 사각형 한 변 mm (기본 [ugv] tag_size_cm = {s.ugv.tag_size_m * 100:g}cm)")
    p.add_argument("--margin-mm", type=float, default=15.0, help="태그 둘레 흰 여백 mm (태그 한 칸 이상 필요)")
    p.add_argument("--out", default=str(cfgmod.VISION_DIR / "tools" / "out"), help="저장 폴더")
    a = p.parse_args(argv)
    if a.id in MAP_TAG_IDS:
        raise SystemExit(f"태그 {a.id}는 지도 태그(0~38)와 번호가 겹칩니다")
    module_mm = a.size_mm / 8   # tag36h11 + 검은 테두리 = 8칸
    if a.margin_mm < module_mm:
        raise SystemExit(f"흰 여백이 너무 좁습니다 ({a.margin_mm}mm < 태그 한 칸 {module_mm:.1f}mm)")

    W, H = mm(A4_MM[0]), mm(A4_MM[1])
    page = np.full((H, W), 255, np.uint8)
    side = mm(a.size_mm)
    tag = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(ARUCO_DICT), a.id, side, borderBits=1)
    cx, cy = W // 2, mm(110)
    x0, y0 = cx - side // 2, cy - side // 2
    page[y0:y0 + side, x0:x0 + side] = tag

    # 자르는 선(점선): 흰 여백 + 위쪽 화살표 띠
    m, arrow_h = mm(a.margin_mm), mm(14)
    l, r, t, b = x0 - m, x0 + side + m, y0 - m - arrow_h, y0 + side + m
    dash = mm(3)
    for xx in range(l, r, 2 * dash):
        cv2.line(page, (xx, t), (min(xx + dash, r), t), 140, 3)
        cv2.line(page, (xx, b), (min(xx + dash, r), b), 140, 3)
    for yy in range(t, b, 2 * dash):
        cv2.line(page, (l, yy), (l, min(yy + dash, b)), 140, 3)
        cv2.line(page, (r, yy), (r, min(yy + dash, b)), 140, 3)
    # 화살표: 흰 여백 바깥(태그와 한 칸 이상 떨어짐) — UGV 앞쪽
    ay0, ay1 = y0 - m - mm(2), t + mm(2)
    cv2.arrowedLine(page, (cx, ay0), (cx, ay1), 0, mm(1.2), cv2.LINE_AA, tipLength=0.45)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(page, "FRONT", (cx + mm(4), (ay0 + ay1) // 2 + mm(2)), font, DPI / 300 * 0.9, 0, mm(0.4), cv2.LINE_AA)

    # 안내문·10cm 눈금자 (영문: OpenCV 글꼴은 한글이 안 됨)
    lines = [f"UGV TAG  tag36h11 id {a.id}",
             f"Black square side must measure {a.size_mm:g} mm -> put the measured value in [ugv] tag_size_cm",
             "Print at ACTUAL SIZE (100%), turn OFF 'fit to page'. Keep the white margin.",
             "Mount with FRONT arrow pointing to the UGV's front."]
    y = mm(200)
    for i, txt in enumerate(lines):
        cv2.putText(page, txt, (mm(15), y + i * mm(9)), font, DPI / 300 * (0.9 if i == 0 else 0.6), 0,
                    mm(0.35 if i == 0 else 0.25), cv2.LINE_AA)
    ry = mm(250)
    rx0, rx1 = mm(55), mm(155)
    cv2.line(page, (rx0, ry), (rx1, ry), 0, mm(0.4))
    for k in range(11):
        xk = rx0 + mm(10 * k)
        cv2.line(page, (xk, ry), (xk, ry - mm(5 if k % 5 == 0 else 3)), 0, mm(0.3))
    cv2.putText(page, "100 mm (check print scale)", (rx0, ry + mm(8)), font, DPI / 300 * 0.6, 0, mm(0.25), cv2.LINE_AA)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"ugv_tag_{a.id}_{a.size_mm:g}mm"
    png = out / f"{stem}.png"
    pdf = out / f"{stem}.pdf"
    from PIL import Image   # matplotlib과 함께 설치됨
    img = Image.fromarray(page)
    img.save(png, dpi=(DPI, DPI))
    img.save(pdf, "PDF", resolution=DPI)   # PDF 페이지 = A4 실제 크기
    print(f"저장: {pdf}\n      {png}\n태그 {a.id}, 한 변 {a.size_mm:g}mm (한 칸 {module_mm:.1f}mm), 흰 여백 {a.margin_mm:g}mm")
    print("인쇄: 실제 크기(100%), '페이지에 맞춤' 끄기 → 검은 사각형 한 변을 자로 재서 [ugv] tag_size_cm에 넣기")


if __name__ == "__main__":
    main()
