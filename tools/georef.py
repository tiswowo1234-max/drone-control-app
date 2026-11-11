"""지도 이미지(app/assets/map.png)의 네 모서리 위도·경도를 구한다 (지오레퍼런싱).

사용법 (프로젝트 폴더에서):
    tools\\.venv\\Scripts\\python.exe tools\\georef.py

원리
- 인쇄 지도 map/KAIST_map_PRINT_2500x2500mm.eps는 KAIST_map.osm에서 만든 벡터 지도다.
  EPS 안에 각 도형의 OSM 번호가 `%%MapFeature: building way/49931562` 주석으로 들어 있고,
  바로 아래 moveto/lineto 좌표(pt, 페이지 왼쪽 아래 원점)가 그 way의 노드들이다.
- 같은 way의 EPS 꼭짓점과 OSM 노드를 순서대로 짝지어(꼭짓점 수가 같고 지도 가장자리에서
  잘리지 않은 way만) 대응점을 만든다.
- OSM 위경도를 투영(타원체 등각 / EPSG:3857 시험)하고 상사변환(회전+균일배율+이동)을
  최소제곱으로 맞춘다. 잔차가 큰 점은 한 번 걸러 내고 다시 맞춘다.
- 페이지(=지도 전체, 250x250cm)의 네 모서리와 정문 위치를 위경도로 바꾼다.

결과
- 화면에 네 모서리 위경도, 회전각, 잔차(m)를 출력 → config/settings.toml [geo]에 옮겨 적는다.
- tools/out/georef_check.png: map.png 위에 변환한 OSM 건물·도로 중심선(빨강)을 겹친 검증 이미지
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
EPS = ROOT / "map" / "KAIST_map_PRINT_2500x2500mm.eps"
OSM = ROOT / "KAIST_map.osm"
MAP_PNG = ROOT / "app" / "assets" / "map.png"
OUT_DIR = Path(__file__).resolve().parent / "out"

MAP_CM = 250.0  # 지도 한 변 실제 크기(cm)
OUTLIER_M = 1.0  # 1차 적합 후 이보다 잔차가 큰 대응점은 제외


# ---------------------------------------------------------------- 투영
#  타원체(WGS84) 위의 등각 투영(UTM 52N, EPSG:5186 한국 TM 등)의 국지 근사.
#  지도 범위(1km 미만)에서는 이들 투영과의 차이가 상사변환 + 수 mm 이하다.
LAT0, LON0 = 36.367, 127.363
_A, _E2 = 6378137.0, 6.69437999014e-3
_PHI0 = math.radians(LAT0)
_W = math.sqrt(1 - _E2 * math.sin(_PHI0) ** 2)
_M_LAT = _A * (1 - _E2) / _W**3 * math.pi / 180   # 위도 1°의 길이(m)
_M_LON = _A / _W * math.cos(_PHI0) * math.pi / 180  # 경도 1°의 길이(m)
R_EARTH = 6378137.0


def ell_fwd(ll: np.ndarray) -> np.ndarray:
    return np.column_stack([(ll[:, 0] - LON0) * _M_LON, (ll[:, 1] - LAT0) * _M_LAT])


def ell_inv(xy: np.ndarray) -> np.ndarray:
    return np.column_stack([xy[:, 0] / _M_LON + LON0, xy[:, 1] / _M_LAT + LAT0])


def merc_fwd(ll: np.ndarray) -> np.ndarray:  # EPSG:3857 (값이 커서 기준점을 빼 둔다)
    lon, lat = np.radians(ll[:, 0]), np.radians(ll[:, 1])
    y = R_EARTH * np.log(np.tan(np.pi / 4 + lat / 2))
    y0 = R_EARTH * math.log(math.tan(math.pi / 4 + _PHI0 / 2))
    return np.column_stack([R_EARTH * (lon - math.radians(LON0)), y - y0])


def merc_inv(xy: np.ndarray) -> np.ndarray:
    y0 = R_EARTH * math.log(math.tan(math.pi / 4 + _PHI0 / 2))
    lon = np.degrees(xy[:, 0] / R_EARTH) + LON0
    lat = np.degrees(2 * np.arctan(np.exp((xy[:, 1] + y0) / R_EARTH)) - np.pi / 2)
    return np.column_stack([lon, lat])


PROJECTIONS = {
    "타원체 등각(UTM/EPSG:5186 계열)": (ell_fwd, ell_inv),
    "EPSG:3857": (merc_fwd, merc_inv),
}


# ---------------------------------------------------------------- 읽기
def read_eps() -> tuple[float, list[dict], dict]:
    """EPS의 MapFeature 블록들: [{kind, ref, pts(pt, 왼쪽 아래 원점)}], 페이지 크기, 정문 중심."""
    text = EPS.read_text(encoding="latin-1")
    m = re.search(r"%%HiResBoundingBox:\s*\S+\s+\S+\s+(\S+)\s+(\S+)", text)
    size = float(m.group(1))
    feats = []
    gate = None
    blocks = re.split(r"^%%MapFeature:\s*", text, flags=re.M)[1:]
    for b in blocks:
        head, _, body = b.partition("\n")
        kind, _, ref = head.strip().partition(" ")
        body = body.split("%%MapFeature", 1)[0]
        nums = re.findall(r"^([-\d.]+) ([-\d.]+) (?:moveto|lineto)$", body, flags=re.M)
        pts = np.array(nums, float)
        if ref == "main_gate":
            # 원: 첫 점(아래쪽 끝)과 곡선 끝점들의 평균이 중심
            ends = re.findall(r"([-\d.]+) ([-\d.]+) curveto", body)
            gate = np.array(ends, float).mean(axis=0) if ends else None
            continue
        if ref.startswith("way/") and len(pts) >= 2:
            feats.append({"kind": kind, "ref": ref[4:], "pts": pts})
    return size, feats, gate


def read_osm() -> dict[str, np.ndarray]:
    """way id → 노드 위경도 배열 (lon, lat)."""
    root = ET.parse(OSM).getroot()
    nodes = {n.get("id"): (float(n.get("lon")), float(n.get("lat"))) for n in root.iter("node")}
    ways = {}
    for w in root.iter("way"):
        refs = [nd.get("ref") for nd in w.iter("nd")]
        if all(r in nodes for r in refs):
            ways[w.get("id")] = np.array([nodes[r] for r in refs], float)
    return ways


def pair_features(feats: list[dict], ways: dict, size: float):
    """EPS 도형 ↔ OSM way. 지도 가장자리에서 잘렸거나 꼭짓점 수가 다른 것은 건너뛴다.
    반환: [(feature, OSM 위경도 배열, 닫힌 도형 여부)], 건너뛴 ref 목록"""
    pairs, skipped = [], []
    for f in feats:
        ll = ways.get(f["ref"])
        p = f["pts"]
        edge = np.any((p < 0.01) | (p > size - 0.01))
        if ll is None or edge:
            skipped.append(f["ref"])
            continue
        closed = len(ll) > 2 and np.allclose(ll[0], ll[-1])
        if closed and len(p) == len(ll) - 1:  # 닫힌 way의 마지막 중복 노드
            ll = ll[:-1]
        if len(p) != len(ll):
            skipped.append(f["ref"])
            continue
        pairs.append((f, ll, closed))
    return pairs, skipped


def align_order(src: np.ndarray, dst: np.ndarray, closed: bool) -> np.ndarray:
    """EPS 꼭짓점 순서가 OSM과 시작점·방향이 다를 수 있다 → 가장 잘 맞는 순서의 dst를 반환.
    src는 이미 변환해 dst와 같은 좌표계에 있다고 본다."""
    cands = [dst, dst[::-1]]
    if closed:
        cands = [np.roll(c, k, axis=0) for c in cands for k in range(len(dst))]
    return min(cands, key=lambda c: np.linalg.norm(src - c, axis=1).sum())


# ---------------------------------------------------------------- 변환
def fit_similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """dst ≈ A @ src + t (회전+균일배율+이동). 3x3 행렬."""
    ms, md = src.mean(0), dst.mean(0)
    s, d = src - ms, dst - md
    a = (s[:, 0] * d[:, 0] + s[:, 1] * d[:, 1]).sum() / (s**2).sum()
    b = (s[:, 0] * d[:, 1] - s[:, 1] * d[:, 0]).sum() / (s**2).sum()
    A = np.array([[a, -b], [b, a]])
    t = md - A @ ms
    return np.array([[A[0, 0], A[0, 1], t[0]], [A[1, 0], A[1, 1], t[1]], [0, 0, 1]])


def apply(M: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ M[:2, :2].T + M[:2, 2]


def ground_err(inv, pred: np.ndarray, true: np.ndarray) -> np.ndarray:
    """투영 좌표 두 점 사이의 지상 거리(m)."""
    return np.linalg.norm(ell_fwd(inv(pred)) - ell_fwd(inv(true)), axis=1)


# ---------------------------------------------------------------- 검증 이미지
def draw_check(ways: dict, refs: set, M: np.ndarray, fwd, size: float, path: Path):
    """map.png 위에 OSM way들을 EPS→PNG 픽셀로 옮겨 빨간 선으로 그린다."""
    pix = pymupdf.Pixmap(str(MAP_PNG))
    W, H = pix.width, pix.height
    Minv = np.linalg.inv(M)
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_image(page.rect, filename=str(MAP_PNG))
    k = W / size
    for ref in refs:
        p = apply(Minv, fwd(ways[ref]))  # 투영 → EPS pt
        pts = [pymupdf.Point(x * k, H - y * k) for x, y in p]
        page.draw_polyline(pts, color=(1, 0, 0), width=2)
    out = page.get_pixmap(alpha=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)


# ---------------------------------------------------------------- main
def main() -> None:
    size, feats, gate = read_eps()
    ways = read_osm()
    pairs, skipped = pair_features(feats, ways, size)
    nb = sum(f["kind"] == "building" for f, _, _ in pairs)
    print(f"EPS 도형 {len(feats)}개 (페이지 {size:.2f}pt), 대응에 쓴 way {len(pairs)}개 "
          f"(건물 {nb}), 건너뜀 {len(skipped)}개")

    results = {}
    for name, (fwd, inv) in PROJECTIONS.items():
        # 1) way별 꼭짓점 평균(순서와 무관)으로 대략 맞춤
        cs = np.array([f["pts"].mean(0) for f, _, _ in pairs])
        cd = np.array([fwd(ll).mean(0) for _, ll, _ in pairs])
        M = fit_similarity(cs, cd)
        # 2) 꼭짓점 순서 맞추기 → 전체 점으로 적합, 두 번 반복
        for _ in range(2):
            src = np.vstack([f["pts"] for f, _, _ in pairs])
            dst = np.vstack([align_order(apply(M, f["pts"]), fwd(ll), c) for f, ll, c in pairs])
            M = fit_similarity(src, dst)
        e = ground_err(inv, apply(M, src), dst)
        keep = e < OUTLIER_M
        M = fit_similarity(src[keep], dst[keep])
        e = ground_err(inv, apply(M, src[keep]), dst[keep])
        results[name] = (M, keep, e)
        print(f"[{name}] 사용 {keep.sum()}점 (제외 {(~keep).sum()}) | "
              f"잔차 평균 {e.mean():.3f} m, 최대 {e.max():.3f} m")

    name = min(results, key=lambda n: results[n][2].mean())
    M, keep, e = results[name]
    fwd, inv = PROJECTIONS[name]
    A = M[:2, :2]
    rot = math.degrees(math.atan2(A[1, 0], A[0, 0]))
    m_per_pt = math.hypot(A[0, 0], A[1, 0])
    m_per_cm = m_per_pt * size / MAP_CM
    print(f"\n선택한 투영: {name}")
    print(f"  대응점 {keep.sum()}개, 잔차 평균 {e.mean():.3f} m, 최대 {e.max():.3f} m "
          f"(지도 위 평균 {e.mean() / m_per_cm * 10:.2f} mm)")
    print(f"  회전 {rot:+.3f}° (지도 +x축이 동쪽에서 반시계로 돈 각도)")
    print(f"  축척: 지도 1cm = 실제 {m_per_cm:.3f} m (약 1:{m_per_cm * 100:.0f}), "
          f"지도 전체 = 실제 {m_per_cm * MAP_CM:.1f} m 정사각형")

    def to_ll(x_pt, y_pt):
        return inv(apply(M, np.array([[x_pt, y_pt]])))[0]

    corners = {"sw": (0, 0), "se": (size, 0), "nw": (0, size), "ne": (size, size)}
    ll = {k: to_ll(*v) for k, v in corners.items()}
    print("\n네 모서리 (위도, 경도)  ※ 지도 기준 모서리이며 실제 방위와 다름")
    for k, (lon, lat) in ll.items():
        print(f"  {k}_lat = {lat:.7f}\n  {k}_lon = {lon:.7f}")

    if gate is not None:
        glon, glat = to_ll(*gate)
        print(f"\n정문(main_gate) EPS ({gate[0]:.2f}, {gate[1]:.2f})pt = 지도 "
              f"({gate[0] / size * MAP_CM:.1f}, {gate[1] / size * MAP_CM:.1f})cm → "
              f"위도 {glat:.7f}, 경도 {glon:.7f}")

    # 쌍선형 보간 검증 (앱 방식): 21x21 격자에서 실제 변환과 비교
    g = np.linspace(0, 1, 21)
    U, V = np.meshgrid(g, g)
    u, v = U.ravel(), V.ravel()
    true_ll = inv(apply(M, np.column_stack([u * size, v * size])))
    c = {k: np.array(val) for k, val in ll.items()}
    bil = ((1 - u) * (1 - v))[:, None] * c["sw"] + (u * (1 - v))[:, None] * c["se"] \
        + ((1 - u) * v)[:, None] * c["nw"] + (u * v)[:, None] * c["ne"]
    e_bil = np.linalg.norm(ell_fwd(bil) - ell_fwd(true_ll), axis=1)
    print(f"\n네 모서리 쌍선형 보간 오차: 최대 {e_bil.max() * 1000:.1f} mm")

    out = OUT_DIR / "georef_check.png"
    draw_check(ways, {f["ref"] for f in feats if f["ref"] in ways}, M, fwd, size, out)
    print(f"검증 이미지: {out}")


if __name__ == "__main__":
    main()
