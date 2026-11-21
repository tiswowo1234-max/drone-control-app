# KAIST patrol drone: localization + incident detection

A drone with a downward-facing grayscale camera flies over a 2.5 m × 2.5 m printed model of the KAIST campus. For every incident photo pasted on the model, the system reports what is happening and where:

1. **Localization:** AprilTags pasted on the map give the drone's position (x, y, height) and heading in map meters.
2. **Photo finding:** printed incident photos are found in the camera frame, and each one is placed on the map: map coordinates, photo slot, and real latitude/longitude.
3. **Incident detection:** an LLM judges each photo: normal or abnormal, category, urgency, and a description.
4. **Report:** the verdict, the photo, and its location go to the campus police app as one JSON bundle.

Localization runs on every camera frame (about 4 ms at 640×480), and the photo search runs every 0.2 s (about 35 ms). The LLM verdict takes 5–10 s per photo and is outside the real-time loop.

## How the system is built

### Physical setup

| Item | Design |
|---|---|
| Map | The team's 2.5 m color base map (buildings, major roads, main gate from OpenStreetMap), printed as it is. A grayscale camera cannot tell its building colors apart, so localization does not depend on the colors. |
| AprilTags | 39 tags (tag36h11, 40 mm black square on 50 mm paper), pasted only where two printed edges meet. Each paper is laid against both edges, so its position and rotation are exact without measuring. Ids 0–27 go in square building corners, ids 28–38 in corners between road edges. |
| Incident photos | Printed with a black frame (12 mm under the picture, 4 mm elsewhere) and a 4 mm white margin, and pasted in four 150 × 100 mm slots (S1–S4) over large buildings. The frame makes a photo stand out on the flat map, and the thick bottom bar tells which way is up. |
| Camera | Grayscale and fixed pointing straight down. The default model is 640×480 with a 60° horizontal field of view until a calibration file replaces it. |

### Software layout

| Folder | Contents |
|---|---|
| [`data/`](../data) | All coordinates and images: OSM source, base map, building/road/tag/slot coordinates, map renders, print files, test photos |
| [`localization/`](localization) | Base map from OSM, tag and slot layout, print files, tag detection, drone pose, photo finding, photo-to-map placement, the end-to-end pipeline, simulator, camera calibration |
| [`LLM_detects/`](LLM_detects) | Incident detection: photo → LLM verdict → Report, and its evaluation on the test photos |

Repository-relative data paths apply throughout this guide.

```text
data/gis/KAIST_map.osm -> map_tools/build_campus_map.py -> data/maps/ (features, reference, preview)
data/maps/ + data/photos/ -> localization/map_layout.py -> data/maps/ (tag layout, slots, renders)
                                                        -> data/print/markers/ (tag and photo PDFs)
camera frames + data/maps/ -> localization/pipeline.py -> LLM_detects/ (optional) -> report
```

The incident project uses its own [Python requirements](requirements.txt). The camera simulator and pipeline save their results under `data/experiments/incident_camera/`. Classifier runs and the spend log live under `data/experiments/photo_classifier/`.

### Per-frame data flow

```
grayscale frame
  ├─ AprilTag detection ─► drone pose (localizer.py, ~4 ms) ─► Kalman tracker
  └─ photo finder (photo_finder.py, ~35 ms, every 0.2 s)
         └─ photo corners ─► map plane (pose or tracked pose) ─► slot S1–S4 ─► latitude/longitude (pipeline.py)
                └─ upright crop ─► LLM (LLM_detects/detector.py, 5–10 s) ─► Report JSON
```

## Base map and compatibility with the team's map code

The shared [campus-map builder](../map_tools/build_campus_map.py) generates the reference raster, labeled preview and map features from OpenStreetMap.

| Topic | How it works |
|---|---|
| Source | `data/gis/KAIST_map.osm` (© OpenStreetMap contributors) |
| Map content | Buildings (`building=*`); major roads (`highway=secondary`, `unclassified`, and `service` except parking aisles); the main gate as the mean of the four OSM entrance nodes named KAIST 정문 |
| Projection | WGS84 → UTM zone 52N (EPSG:32652) → the OSM bounding box scaled uniformly into 2.5 m and centered |
| Coordinate frame | Every file and module uses this frame: meters on the printed map, origin at the lower-left corner, +x east, +y north. Heights are meters above the map surface (z = 0). |
| Features JSON | `buildings[]` (`id`, `name`, `ref`, `color`, `polygon_xy_m`, `centroid_xy_m`), `roads[]` (`centerline_xy_m`, `highway`, `display_linewidth_pt`, ...), `landmarks.main_gate` (`xy_m`, source nodes with lat/lon). `map_layout.py` reads this format as it is. |
| Printed raster | `data/maps/KAIST_model_reference_2p5m.png` is the script's reference raster (2000 px = 2.5 m) exactly as it was printed. The incident comparison command writes a separate copy under `data/experiments/incident_camera/osm_map/`. Re-running it reproduces every building, road and gate coordinate of `data/maps/KAIST_model_features_2p5m.json`. |
| Road edges | Roads are OSM centerlines drawn `display_linewidth_pt` wide on a 720 pt figure that becomes 2.5 m. `map_layout.py` uses the same conversion, so road-edge tags match the printed road edges. |
| Real-world position | `localization/geo.py` inverts the projection using the same OSM bounds, and reproduces the main-gate nodes within a few micrometers. 1 mm on the map is about 0.29 m on campus. |
| New files | `apriltag_map.json` and `slots.json` follow the features JSON's style (`*_xy_m` fields in map meters), so code that reads one reads the others the same way. |

Limits of the base map:

- Roads are OSM centerlines; their drawn width is a display choice, not the real road width.
- Building colors are identification colors, not the real roof colors.
- The gate marker sits at the mean of the entrance nodes, not at a surveyed point.

## `data/`

| Path | Contents |
|---|---|
| `gis/KAIST_map.osm` | OpenStreetMap source of the campus |
| `maps/KAIST_model_reference_2p5m.png` | The printed color map: the base map's reference raster, 2000 px = 2.5 m |
| `maps/KAIST_model_features_2p5m.json` | Buildings, roads and main gate of the base map |
| `maps/KAIST_model_map_2p5m_roads_gate.png` | The base map with road names, gate and legend, for people |
| `maps/apriltag_map.json` | Every AprilTag: id, corners of the black square (OpenCV order), paper corners, yaw, building/road, where to paste it |
| `maps/slots.json` | Photo slots S1–S4: corners, up direction, building, and the example photo's frame and picture corners |
| `maps/layout_guide.png` | Pasting plan: red = building tags, orange = road tags, blue = photo slots |
| `maps/map_with_tags*.png`, `maps/map_with_photos*.png` | The finished map with tags (and example photos), color and grayscale, 0.5 mm per pixel |
| `print/markers/` | PDFs to print at 100 %: `tags/tag_00.pdf`–`tag_38.pdf`, `all_tags.pdf`, `photos/slot_1.pdf`–`slot_4.pdf` |
| `photos/photo_unlabeled/`, `photos/photo_labeled/`, `photos/labels.csv` | The 18 test photos and their labels (evaluation only) |
| `photos/photo_gray/` | Grayscale copies of the test photos |

## Getting started

From the repository root with Python 3.11:

```bash
python -m pip install -r incident_detection/requirements.txt
python incident_detection/localization/pipeline.py --sim
```

The sample frames already include ground truth. The following commands rebuild the comparison map, tag layout and simulator report as needed:

```bash
python incident_detection/localization/osm_map_roads_gate_2p5m.py
python incident_detection/localization/map_layout.py
python incident_detection/localization/sim_camera.py
```

For model calls, configure `OPENAI_API_KEY` in `incident_detection/LLM_detects/.env` or the environment. Both commands below use the API:

```bash
python incident_detection/localization/pipeline.py --sim --detect
python incident_detection/LLM_detects/evaluate.py --image-dir photo_gray
```

Korean map labels need a Korean font (Apple SD Gothic Neo, NanumGothic, Malgun Gothic or Noto Sans CJK KR); without one, Matplotlib may draw them as boxes.

## Field setup

The print files are in `data/print/markers/`, and the pasting plan is `data/maps/layout_guide.png`.

- **AprilTags (39):**
  - Ids 0–27 go in building corners; push the paper into the corner just inside the dark outline.
  - Ids 28–38 go in corners between road edges; lay one paper edge along the longer road edge and slide it into the corner until it touches the other edge.
  - Every page has a close-up of its spot.
  - Print at 100 % and check the 50 mm bar on the page with a ruler.
- **Incident photos:**
  - Print every photo in the same format: picture, black frame (thick at the bottom), white margin.
  - Paste each with its top toward the slot's up direction shown on the page.
  - The four example prints (S1–S4) are ready to use, and any other photo printed in this format fits any slot.

## More

- [`localization/README.md`](localization/README.md): base map, map preparation, photo finder, localization, pipeline, simulator
- [`LLM_detects/README.md`](LLM_detects/README.md): incident detection and its evaluation
- [Simulator report](../data/experiments/incident_camera/sim_report.md): full simulator results

Map data © OpenStreetMap contributors.
