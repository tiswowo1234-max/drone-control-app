"""지도 이미지(app/assets/map.png)의 네 모서리 위도·경도를 구한다 (지오레퍼런싱).

사용법 (프로젝트 폴더에서):
    tools\\.venv\\Scripts\\python.exe tools\\georef.py

원리
- 인쇄 지도 PDF(KAIST_2500mm_Map.pdf)는 KAIST_map.osm을 QGIS로 임의 크롭한 것이라
  정확한 범위를 모른다. 그래서 양쪽에 모두 있는 '건물'을 기준점으로 맞춘다.
- PDF: 회색으로 채워진 벡터 도형 = 건물 → 중심점(면적 가중)
- OSM: building 태그가 있는 way/relation → 위경도를 투영(EPSG:3857 / 4326 둘 다 시험)
- 건물 1쌍마다 '면적비로 배율 + 중심점으로 평행이동' 가설을 세워 가장 많이 맞는 대응을
  찾고(RANSAC 비슷한 방식), 대응점들로 상사변환·어파인변환을 최소제곱으로 맞춘다.
- 잘라 낸 지도 영역(tools/pdf_to_png.py와 같은 방식)의 네 모서리를 변환해 위경도를 낸다.

결과
- 화면에 네 모서리 위경도, 잔차(m), 회전각 등을 출력 → config/settings.toml [geo]에 옮겨 적는다.
- tools/out/georef_check.png: map.png 위에 변환한 OSM 건물 외곽선(빨강)을 겹친 검증 이미지
"""
from __future__ import annotations

import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdf_to_png import content_bbox  # noqa: E402  (map.png와 똑같은 잘라 내기 영역)

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "KAIST_2500mm_Map.pdf"
OSM = ROOT / "KAIST_map.osm"
MAP_PNG = ROOT / "app" / "assets" / "map.png"
OUT_DIR = Path(__file__).resolve().parent / "out"

BUILDING_GRAY = 0.588  # PDF 건물 채움색 (150/255)
R_EARTH = 6378137.0


# ---------------------------------------------------------------- 공통 도형
def poly_area_centroid(pts: np.ndarray) -> tuple[float, np.ndarray]:
    o = pts.mean(axis=0)  # 투영 좌표는 값이 커서(~1e7) 그대로 계산하면 정밀도가 무너진다
    x, y = pts[:, 0] - o[0], pts[:, 1] - o[1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    a = cross.sum() / 2
    if abs(a) < 1e-12:
        return 0.0, o
    cx = ((x + x1) * cross).sum() / (6 * a)
    cy = ((y + y1) * cross).sum() / (6 * a)
    return abs(a), np.array([cx, cy]) + o


def rings_area_centroid(rings: list[np.ndarray]) -> tuple[float, np.ndarray]:
    """여러 고리(외곽/구멍 구분 없이 큰 것이 외곽이라 가정)의 면적·중심."""
    parts = [poly_area_centroid(r) for r in rings if len(r) >= 3]
    parts.sort(key=lambda t: -t[0])
    if not parts:
        return 0.0, np.zeros(2)
    outer_a, outer_c = parts[0]
    a_sum, c_sum = outer_a, outer_a * outer_c
    for a, c in parts[1:]:  # 나머지는 구멍으로 보고 뺀다(대부분 건물엔 없음)
        a_sum -= a
        c_sum = c_sum - a * c
    if a_sum <= 0:
        return outer_a, outer_c
    return a_sum, c_sum / a_sum


def principal_angle(rings: list[np.ndarray]) -> float:
    """가장 큰 고리 꼭짓점들의 주축 방향(라디안, 0~π)."""
    r = max(rings, key=lambda q: poly_area_centroid(q)[0])
    d = r - r.mean(0)
    cov = d.T @ d
    return 0.5 * math.atan2(2 * cov[0, 1], cov[0, 0] - cov[1, 1])


# ---------------------------------------------------------------- PDF 건물
def pdf_buildings(page: pymupdf.Page) -> list[dict]:
    out = []
    for d in page.get_drawings():
        fill = d.get("fill")
        if not fill or abs(fill[0] - BUILDING_GRAY) > 0.01 or abs(fill[0] - fill[2]) > 0.01:
            continue
        rings, cur, last = [], [], None
        for it in d["items"]:
            if it[0] == "re":
                if cur:
                    rings.append(cur)
                r = it[1]
                rings.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
                cur, last = [], None
                continue
            if it[0] == "qu":
                q = it[1]
                rings.append([(p.x, p.y) for p in (q.ul, q.ur, q.lr, q.ll)])
                cur, last = [], None
                continue
            p0, p1 = it[1], it[-1]  # 'l': (p0,p1)  'c': (p0,c1,c2,p1)
            if last is None or abs(p0.x - last.x) > 1e-3 or abs(p0.y - last.y) > 1e-3:
                if cur:
                    rings.append(cur)
                cur = [(p0.x, p0.y)]
            cur.append((p1.x, p1.y))
            last = p1
        if cur:
            rings.append(cur)
        rings = [np.array(r, float) for r in rings if len(r) >= 3]
        if not rings:
            continue
        # PDF는 y가 아래로 증가 → 위쪽 +y로 뒤집어 수학 좌표계로 쓴다
        rings_up = [np.column_stack([r[:, 0], -r[:, 1]]) for r in rings]
        a, c = rings_area_centroid(rings_up)
        if a > 0:
            out.append({"rings": rings_up, "area": a, "c": c})
    return out


# ---------------------------------------------------------------- OSM 건물
def osm_buildings() -> list[dict]:
    root = ET.parse(OSM).getroot()
    nodes = {n.get("id"): (float(n.get("lon")), float(n.get("lat"))) for n in root.iter("node")}
    ways = {}
    for w in root.iter("way"):
        refs = [nd.get("ref") for nd in w.iter("nd")]
        tags = {t.get("k"): t.get("v") for t in w.iter("tag")}
        ways[w.get("id")] = (refs, tags)

    def ring(refs):
        pts = [nodes[r] for r in refs if r in nodes]
        return np.array(pts, float) if len(pts) >= 3 and len(pts) == len(refs) else None

    out = []
    for wid, (refs, tags) in ways.items():
        if "building" in tags:
            r = ring(refs)
            if r is not None:
                out.append({"id": f"w{wid}", "name": tags.get("name", ""), "rings_ll": [r]})
    for rel in root.iter("relation"):
        tags = {t.get("k"): t.get("v") for t in rel.iter("tag")}
        if "building" not in tags:
            continue
        rings = []
        for m in rel.iter("member"):
            if m.get("type") == "way" and m.get("ref") in ways:
                r = ring(ways[m.get("ref")][0])
                if r is not None:
                    rings.append(r)
        if rings:
            out.append({"id": f"r{rel.get('id')}", "name": tags.get("name", ""), "rings_ll": rings})
    return out


# ---------------------------------------------------------------- 투영
def merc_fwd(ll: np.ndarray) -> np.ndarray:  # EPSG:3857
    lon, lat = np.radians(ll[:, 0]), np.radians(ll[:, 1])
    return np.column_stack([R_EARTH * lon, R_EARTH * np.log(np.tan(np.pi / 4 + lat / 2))])


def merc_inv(xy: np.ndarray) -> np.ndarray:
    lon = np.degrees(xy[:, 0] / R_EARTH)
    lat = np.degrees(2 * np.arctan(np.exp(xy[:, 1] / R_EARTH)) - np.pi / 2)
    return np.column_stack([lon, lat])


#  타원체(WGS84) 위의 등각 투영(UTM 52N, EPSG:5186 한국 중부원점 TM 등).
#  지도 범위(1km 미만)에서는 접평면 근사와 이들 투영의 차이가 상사변환(회전+배율)
#  + 수 mm 이하이므로, 어느 투영인지 몰라도 이 근사로 충분하다.
LAT0, LON0 = 36.367, 127.363
_A, _E2 = 6378137.0, 6.69437999014e-3
_PHI0 = math.radians(LAT0)
_W = math.sqrt(1 - _E2 * math.sin(_PHI0) ** 2)
_M_LAT = _A * (1 - _E2) / _W**3 * math.pi / 180   # 위도 1°의 길이(m)
_M_LON = _A / _W * math.cos(_PHI0) * math.pi / 180  # 경도 1°의 길이(m)


def ell_fwd(ll: np.ndarray) -> np.ndarray:
    return np.column_stack([(ll[:, 0] - LON0) * _M_LON, (ll[:, 1] - LAT0) * _M_LAT])


def ell_inv(xy: np.ndarray) -> np.ndarray:
    return np.column_stack([xy[:, 0] / _M_LON + LON0, xy[:, 1] / _M_LAT + LAT0])


PROJECTIONS = {
    "타원체 등각(UTM/EPSG:5186 계열)": (ell_fwd, ell_inv),
    "EPSG:3857": (merc_fwd, merc_inv),
    "EPSG:4326": (lambda ll: ll.copy(), lambda xy: xy.copy()),  # 경위도를 그대로 평면에 그린 경우
}


# ---------------------------------------------------------------- 변환 맞추기
def fit_similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """dst ≈ A @ src + t (회전+균일배율+이동). 3x3 행렬 반환."""
    ms, md = src.mean(0), dst.mean(0)
    s, d = src - ms, dst - md
    a = (s[:, 0] * d[:, 0] + s[:, 1] * d[:, 1]).sum() / (s**2).sum()
    b = (s[:, 0] * d[:, 1] - s[:, 1] * d[:, 0]).sum() / (s**2).sum()
    A = np.array([[a, -b], [b, a]])
    t = md - A @ ms
    return np.array([[A[0, 0], A[0, 1], t[0]], [A[1, 0], A[1, 1], t[1]], [0, 0, 1]])


def fit_affine(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    X = np.column_stack([src, np.ones(len(src))])
    P, *_ = np.linalg.lstsq(X, dst, rcond=None)
    return np.vstack([P.T, [0, 0, 1]])


def apply(M: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ M[:2, :2].T + M[:2, 2]


def match(pdf_c: np.ndarray, osm_c: np.ndarray, M: np.ndarray, tol: float):
    """변환 M(PDF→투영)으로 옮긴 PDF 중심과 가장 가까운 OSM 중심을 짝짓는다(상호 최근접)."""
    p = apply(M, pdf_c)
    D = np.linalg.norm(p[:, None, :] - osm_c[None, :, :], axis=2)
    j = D.argmin(1)
    i_back = D.argmin(0)
    pairs = [(i, j[i]) for i in range(len(p)) if D[i, j[i]] < tol and i_back[j[i]] == i]
    return pairs


def solve(pdf: list[dict], osm: list[dict], proj: str):
    fwd, _ = PROJECTIONS[proj]
    for b in osm:
        b["rings"] = [fwd(r) for r in b["rings_ll"]]
        b["area"], b["c"] = rings_area_centroid(b["rings"])
    pdf_c = np.array([b["c"] for b in pdf])
    osm_c = np.array([b["c"] for b in osm])
    pdf_a = np.array([b["area"] for b in pdf])
    osm_a = np.array([b["area"] for b in osm])

    # 1) 가설: PDF 건물 i ↔ OSM 건물 j, 배율 = sqrt(면적비),
    #    회전 = 두 건물 주축 방향 차이(+0/90/180/270°). QGIS 레이아웃에서 지도를 회전시켰을 수 있다.
    pdf_ang = [principal_angle(b["rings"]) for b in pdf]
    osm_ang = [principal_angle(b["rings"]) for b in osm]
    best = (0, None)
    big_osm = np.argsort(-osm_a)[:40]
    for i in np.argsort(-pdf_a)[:30]:
        for j in big_osm:
            s = math.sqrt(osm_a[j] / pdf_a[i])
            for k in range(4):
                th = osm_ang[j] - pdf_ang[i] + k * math.pi / 2
                A = s * np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
                t = osm_c[j] - A @ pdf_c[i]
                M = np.array([[A[0, 0], A[0, 1], t[0]], [A[1, 0], A[1, 1], t[1]], [0, 0, 1]])
                n = len(match(pdf_c, osm_c, M, s * 250))  # 주축 각도 오차를 감안해 넉넉히(PDF 250pt)
                if n > best[0]:
                    best = (n, M)
    M = best[1]
    # 2) 반복: 짝 → 상사변환 → 다시 짝
    scale = math.sqrt(abs(np.linalg.det(M[:2, :2])))
    for tol_pt in (250, 150, 80, 40, 25, 25):
        pairs = match(pdf_c, osm_c, M, tol_pt * scale)
        if len(pairs) < 3:
            break
        I, J = np.array(pairs).T
        M = fit_similarity(pdf_c[I], osm_c[J])
        scale = math.sqrt(abs(np.linalg.det(M[:2, :2])))
    pairs = match(pdf_c, osm_c, M, 25 * scale)
    I, J = np.array(pairs).T
    return pdf_c[I], osm_c[J], pairs


def ground_m(inv, pred: np.ndarray, true: np.ndarray) -> np.ndarray:
    """투영 좌표 두 점 사이의 지상 거리(m) — 위경도로 되돌린 뒤 타원체 접평면에서 잰다."""
    return np.linalg.norm(ell_fwd(inv(pred)) - ell_fwd(inv(true)), axis=1)


# ---------------------------------------------------------------- 검증 이미지
def draw_check(osm: list[dict], M: np.ndarray, clip: pymupdf.Rect, path: Path):
    """map.png 위에 OSM 건물을 PDF→PNG 픽셀 좌표로 옮겨 빨간 선으로 그린다."""
    pix = pymupdf.Pixmap(str(MAP_PNG))
    W, H = pix.width, pix.height
    Minv = np.linalg.inv(M)
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_image(page.rect, filename=str(MAP_PNG))
    k = W / clip.width
    for b in osm:
        for r in b["rings"]:
            p = apply(Minv, r)  # 투영 → PDF(위쪽 +y)
            px = np.column_stack([(p[:, 0] - clip.x0) * k, (-p[:, 1] - clip.y0) * k])
            pts = [pymupdf.Point(*q) for q in px] + [pymupdf.Point(*px[0])]
            page.draw_polyline(pts, color=(1, 0, 0), width=3)
    out = page.get_pixmap(alpha=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)


# ---------------------------------------------------------------- main
def main() -> None:
    page = pymupdf.open(PDF)[0]
    clip = content_bbox(page)
    pdf = pdf_buildings(page)
    osm = osm_buildings()
    print(f"PDF 건물 {len(pdf)}개, OSM 건물 {len(osm)}개, 지도 영역(PDF pt) {clip}")

    results = {}
    for proj, (fwd, inv) in PROJECTIONS.items():
        src, dst, pairs = solve(pdf, [dict(b) for b in osm], proj)
        Ms, Ma = fit_similarity(src, dst), fit_affine(src, dst)
        rs = ground_m(inv, apply(Ms, src), dst)
        ra = ground_m(inv, apply(Ma, src), dst)
        results[proj] = (len(pairs), rs, ra, Ms, Ma)
        print(f"[{proj}] 대응 {len(pairs)}쌍 | 상사변환 잔차 평균 {rs.mean():.2f} m, 최대 {rs.max():.2f} m "
              f"| 어파인 잔차 평균 {ra.mean():.2f} m, 최대 {ra.max():.2f} m")

    # 상사변환(회전+균일 배율+이동)이 가장 잘 맞는 투영을 QGIS가 쓴 것으로 본다
    # (QGIS 출력은 투영 평면을 그대로 회전·축소한 것이므로 상사변환이어야 한다)
    proj = min(results, key=lambda p: (-results[p][0], results[p][1].mean()))
    n, rs, ra, Ms, Ma = results[proj]
    fwd, inv = PROJECTIONS[proj]
    for b in osm:
        b["rings"] = [fwd(r) for r in b["rings_ll"]]

    A = Ms[:2, :2]
    rot = math.degrees(math.atan2(A[1, 0], A[0, 0]))
    print(f"\n선택한 투영: {proj}  (대응 {n}쌍)")
    print(f"  상사변환 잔차 평균 {rs.mean():.3f} m, 최대 {rs.max():.3f} m")
    print(f"  회전 {rot:+.3f}° (지도 이미지의 오른쪽(+x)이 동쪽에서 반시계로 {rot:.1f}° 돌아간 방향)")

    # 잘라 낸 지도 영역의 네 모서리 (PDF는 y 아래로 증가 → 위쪽 +y로 뒤집음)
    corners_pdf = {
        "sw": (clip.x0, -clip.y1), "se": (clip.x1, -clip.y1),
        "nw": (clip.x0, -clip.y0), "ne": (clip.x1, -clip.y0),
    }
    ll = {k: inv(apply(Ms, np.array([v])))[0] for k, v in corners_pdf.items()}
    print("\n네 모서리 (위도, 경도)  ※ 이미지 기준 왼쪽아래=sw, 오른쪽위=ne (실제 방위와 다름)")
    for k, (lon, lat) in ll.items():
        print(f"  {k}: {lat:.7f}, {lon:.7f}")

    gx = np.linalg.norm(ell_fwd(np.array([ll["se"]])) - ell_fwd(np.array([ll["sw"]])))
    gy = np.linalg.norm(ell_fwd(np.array([ll["nw"]])) - ell_fwd(np.array([ll["sw"]])))
    print(f"지도 영역 실제 크기 약 {gx:.1f} m x {gy:.1f} m, 축척 약 1:{gx / 2.5:.0f} (가로 250cm 기준)")

    # 앱 보간 방식별 오차: 지도 위 21x21 격자에서 실제 위경도와 비교
    g = np.linspace(0, 1, 21)
    U, V = np.meshgrid(g, g)
    u, v = U.ravel(), V.ravel()
    pts_pdf = np.column_stack([clip.x0 + u * clip.width, -clip.y1 + v * clip.height])
    true_ll = inv(apply(Ms, pts_pdf))
    c = {k: np.array(val) for k, val in ll.items()}
    lin = np.column_stack([c["sw"][0] + u * (c["ne"][0] - c["sw"][0]),
                           c["sw"][1] + v * (c["ne"][1] - c["sw"][1])])
    bil = ((1 - u) * (1 - v))[:, None] * c["sw"] + (u * (1 - v))[:, None] * c["se"] \
        + ((1 - u) * v)[:, None] * c["nw"] + (u * v)[:, None] * c["ne"]
    e_lin = np.linalg.norm(ell_fwd(lin) - ell_fwd(true_ll), axis=1)
    e_bil = np.linalg.norm(ell_fwd(bil) - ell_fwd(true_ll), axis=1)
    print(f"\nsw/ne 두 점 선형 보간 오차: 평균 {e_lin.mean():.1f} m, 최대 {e_lin.max():.1f} m")
    print(f"네 모서리 쌍선형 보간 오차: 평균 {e_bil.mean():.4f} m, 최대 {e_bil.max():.4f} m")

    big = np.argsort(-rs)[:5]
    print("잔차 큰 대응점(m):", ", ".join(f"{rs[i]:.2f}" for i in big))

    out = OUT_DIR / "georef_check.png"
    draw_check(osm, Ms, clip, out)
    print(f"\n검증 이미지: {out}")


if __name__ == "__main__":
    main()
