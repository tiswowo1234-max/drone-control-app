"""시뮬레이터 좌표 ↔ 지도 좌표 보정값 계산 → settings.toml [sim_calibration]에 넣을 값을 출력.

원리
  시뮬레이터 장면(UnityProject_Data/level0)의 건물 오브젝트 이름에 OSM way ID가 들어 있다
  (예: Building_49931903_0_Roof). 건물 메시(정적 배칭 → 세계 좌표)의 중심과 KAIST_map.osm 건물 중심을
  짝지어 '시뮬레이터 = 닮음변환(OSM 미터)'을 맞추고, [geo]의 지도 네 모서리 위경도를 시뮬레이터
  좌표로 옮겨 원점·배율·회전을 구한다.

실행 (시뮬레이터 빌드가 바뀌었을 때만 다시 하면 된다)
  py -3.14 -m venv %TEMP%\\upy
  %TEMP%\\upy\\Scripts\\python.exe -m pip install UnityPy numpy
  %TEMP%\\upy\\Scripts\\python.exe server\\tools\\sim_calibrate.py
"""
from __future__ import annotations

import argparse
import math
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def sim_buildings(data_dir: Path) -> dict[str, tuple[float, float]]:
    import UnityPy  # noqa: PLC0415
    from UnityPy.helpers.MeshHelper import MeshHandler  # noqa: PLC0415

    env = UnityPy.load(str(data_dir))
    cache: dict = {}
    out = {}
    for obj in env.objects:
        if obj.type.name != "GameObject":
            continue
        g = obj.read()
        m = re.match(r"Building_(\d+)_0_Roof$", g.m_Name)  # relation(멀티폴리곤)은 제외
        if not m:
            continue
        mesh = rend = None
        for c in g.m_Component:
            co = c.component.read() if hasattr(c, "component") else c.read()
            if type(co).__name__ == "MeshFilter":
                mesh = co.m_Mesh
            elif type(co).__name__ == "MeshRenderer":
                rend = co
        if mesh is None or rend is None:
            continue
        if mesh.path_id not in cache:
            mo = mesh.read()
            h = MeshHandler(mo)
            h.process()
            cache[mesh.path_id] = (mo, h.m_Vertices)
        mo, vs = cache[mesh.path_id]
        sbi = rend.m_StaticBatchInfo
        subs = mo.m_SubMeshes[sbi.firstSubMesh: sbi.firstSubMesh + sbi.subMeshCount] if sbi.subMeshCount else mo.m_SubMeshes
        pts = [v for sm in subs for v in vs[sm.firstVertex: sm.firstVertex + sm.vertexCount]]
        xs, zs = [p[0] for p in pts], [p[2] for p in pts]
        out[m.group(1)] = ((min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "RoboMasterTT-Simulator beta" / "UnityProject_Data"))
    ap.add_argument("--osm", default=str(ROOT / "KAIST_map.osm"))
    ap.add_argument("--settings", default=str(ROOT / "config" / "settings.toml"))
    args = ap.parse_args()

    sim = sim_buildings(Path(args.data))
    root = ET.parse(args.osm).getroot()
    b = root.find("bounds")
    lat0 = (float(b.get("minlat")) + float(b.get("maxlat"))) / 2
    lon0 = (float(b.get("minlon")) + float(b.get("maxlon"))) / 2
    R = 6378137.0

    def en(lat: float, lon: float) -> tuple[float, float]:
        return math.radians(lon - lon0) * R * math.cos(math.radians(lat0)), math.radians(lat - lat0) * R

    nodes = {n.get("id"): (float(n.get("lat")), float(n.get("lon"))) for n in root.iter("node")}
    ways = {w.get("id"): [nd.get("ref") for nd in w.iter("nd")] for w in root.iter("way")}
    A, B = [], []
    for wid, c in sim.items():
        pts = [en(*nodes[r]) for r in ways.get(wid, []) if r in nodes]
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            A.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
            B.append(c)
    A, B = np.array(A), np.array(B)

    def fit(a: np.ndarray, bb: np.ndarray):
        a, bb = a[:, 0] + 1j * a[:, 1], bb[:, 0] + 1j * bb[:, 1]
        am, bm = a.mean(), bb.mean()
        k = np.vdot(a - am, bb - bm) / np.vdot(a - am, a - am)
        return k, bm - k * am, np.abs(k * (a - am) + bm - bb)

    best = None
    for mirror in (False, True):
        AA = A.copy()
        if mirror:
            AA[:, 0] *= -1
        k, t, res = fit(AA, B)
        keep = res < max(1.5, np.median(res) * 4)
        k, t, res = fit(AA[keep], B[keep])
        print(f"{'거울반전' if mirror else '반전없음'}: 배율 {abs(k):.4f}, 회전 {math.degrees(np.angle(k)):.2f}°, "
              f"잔차 평균 {res.mean():.2f} m / 최대 {res.max():.2f} m ({keep.sum()}쌍)")
        if best is None or res.mean() < best[3]:
            best = (mirror, k, t, res.mean())
    mirror, k, t, _ = best

    def to_sim(lat: float, lon: float) -> np.ndarray:
        e, n = en(lat, lon)
        z = k * complex(-e if mirror else e, n) + t
        return np.array([z.real, z.imag])

    with open(args.settings, "rb") as f:
        s = tomllib.load(f)
    g, W, H = s["geo"], s["map"]["width_cm"], s["map"]["height_cm"]
    c = {q: to_sim(g[f"{q}_lat"], g[f"{q}_lon"]) for q in ("sw", "se", "nw", "ne")}
    ex, ey = c["se"] - c["sw"], c["nw"] - c["sw"]
    ax = -1 if mirror else 1  # a축 = 동쪽 = (거울반전이면 -x)
    theta = math.degrees(math.atan2(ex[1], ax * ex[0]))
    scale = (W / np.linalg.norm(ex) + H / np.linalg.norm(ey)) / 2
    print("지도 모서리(시뮬레이터 m):", {q: tuple(round(float(v), 3) for v in p) for q, p in c.items()})
    print("\n[sim_calibration]")
    print(f"origin_x_m = {c['sw'][0]:.3f}")
    print(f"origin_z_m = {c['sw'][1]:.3f}")
    print(f"scale_cm_per_m = {scale:.3f}")
    print(f'map_x_from = "{"-x" if mirror else "+x"}"')
    print('map_y_from = "+z"')
    print(f"rotation_deg = {theta:.2f}")


if __name__ == "__main__":
    main()
