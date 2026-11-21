"""Map meters <-> real-world latitude/longitude, the inverse of how the 2.5 m map was made.

map_tools/build_campus_map.py projected OSM (WGS84) to UTM zone 52N, then scaled the OSM bounding box
uniformly into 2.5 m and centered it. Undoing those steps turns a point on the map into the real
campus position, so a report can carry real coordinates.

Only the <bounds> element of data/gis/KAIST_map.osm is read.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from pyproj import Transformer

import _paths

UTM_52N = "EPSG:32652"
WGS84 = "EPSG:4326"


class MapGeo:
    def __init__(self, osm_file: Path = _paths.DATA / "gis" / "KAIST_map.osm", size_m: float = 2.5):
        bounds = None
        for _, elem in ET.iterparse(osm_file, events=("start",)):
            if elem.tag == "bounds":
                bounds = {k: float(v) for k, v in elem.attrib.items()}
                break
        if bounds is None:
            raise ValueError(f"no <bounds> in {osm_file}")
        self._to_utm = Transformer.from_crs(WGS84, UTM_52N, always_xy=True)
        self._to_wgs = Transformer.from_crs(UTM_52N, WGS84, always_xy=True)
        # Same steps as the map script: projected bounding-box corners, uniform scale, centered
        corners = [self._to_utm.transform(lon, lat) for lon in (bounds["minlon"], bounds["maxlon"])
                   for lat in (bounds["minlat"], bounds["maxlat"])]
        self.min_e = min(e for e, _ in corners)
        self.min_n = min(n for _, n in corners)
        real_w = max(e for e, _ in corners) - self.min_e
        real_h = max(n for _, n in corners) - self.min_n
        self.scale = size_m / max(real_w, real_h)  # map meters per real meter
        self.offset_x = (size_m - self.scale * real_w) / 2
        self.offset_y = (size_m - self.scale * real_h) / 2

    def map_to_latlon(self, x: float, y: float) -> tuple[float, float]:
        e = self.min_e + (x - self.offset_x) / self.scale
        n = self.min_n + (y - self.offset_y) / self.scale
        lon, lat = self._to_wgs.transform(e, n)
        return lat, lon

    def latlon_to_map(self, lat: float, lon: float) -> tuple[float, float]:
        e, n = self._to_utm.transform(lon, lat)
        return self.offset_x + (e - self.min_e) * self.scale, self.offset_y + (n - self.min_n) * self.scale

    def real_m(self, map_m: float) -> float:
        """A distance on the map in real campus meters."""
        return map_m / self.scale


if __name__ == "__main__":
    # Self-check against the main gate nodes stored with both lat/lon and map coordinates
    import json
    geo = MapGeo()
    from settings import MapConfig
    features = json.loads(MapConfig().features.read_text(encoding="utf-8"))
    print(f"scale {geo.scale:.8f} map m per real m (features file: "
          f"{features['metadata']['scale_model_per_real']:.8f}); 1 mm on the map = {geo.real_m(0.001):.3f} m")
    for node in features["landmarks"]["main_gate"]["source_nodes"]:
        x, y = geo.latlon_to_map(node["lat"], node["lon"])
        lat, lon = geo.map_to_latlon(*node["xy_m"])
        print(f"{node['id']}: map error {abs(x - node['xy_m'][0]) * 1e6:.1f} / {abs(y - node['xy_m'][1]) * 1e6:.1f} um, "
              f"round trip {abs(lat - node['lat']) * 1e7:.2f} / {abs(lon - node['lon']) * 1e7:.2f} e-7 deg")
