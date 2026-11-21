# localization: map preparation, drone pose, photo finding, pipeline

Everything from the OSM source to the LLM:

- the base color map from OpenStreetMap (the team's map script)
- where the AprilTags and photo slots go on the printed map, and the print files for them
- the drone's pose from the tags in each frame, smoothed by a tracker
- printed incident photos found in the frame and placed on the map (map coordinates, slot, latitude/longitude)
- the end-to-end pipeline that hands each upright photo crop and its location to `../LLM_detects`

Coordinates and images are read from and written to `../../data/`. How the base map is made and how every module stays compatible with it is described in "Base map and compatibility with the team's map code" in the [top-level README](../README.md).

## Files

| File | Role |
|---|---|
| `settings.py` | All settings: `MapConfig`, `TagConfig`, `SlotConfig` (printed map), `CameraConfig` (camera guess), `PhotoFinderConfig`, `LocalizerConfig`, `TrackerConfig`, `PipelineConfig` |
| `osm_map_roads_gate_2p5m.py` | Comparison entry point for [the shared campus-map builder](../../map_tools/build_campus_map.py) |
| `map_layout.py` | Chooses tag spots and photo slots, renders the finished map, writes the print PDFs |
| `photo_finder.py` | AprilTag detection and the printed-photo finder for grayscale frames; on-screen overlay |
| `localizer.py` | `Localizer` (tags → camera pose), `PoseTracker` (Kalman filter), `pixels_to_map` (image → map plane) |
| `pipeline.py` | Per-frame loop: pose → photos → map position, slot, latitude/longitude → LLM → `Report`; operator view |
| `geo.py` | Map meters ↔ latitude/longitude, the inverse of the base-map projection |
| `camera_model.py` | Camera intrinsics from `../../data/calibration/incident_camera.json`, or a pinhole guess from `CameraConfig` |
| `calibrate_camera.py` | Chessboard calibration → `../../data/calibration/incident_camera.json` |
| `sim_camera.py` | Simulated grayscale camera over the finished map, checked against ground truth |
| `_paths.py` | Locates `../../data/` and `../LLM_detects/`; puts `LLM_detects` on the import path |
| `../../data/experiments/incident_camera/` | `osm_map/` (regenerated base map), `pipeline/` (views, JSON, crops), `photo_finder/`, `sim_frames/` (12 sample frames + ground truth), `sim_report.md` |

The pipeline imports the classifier schema and loads the detector when `--detect` is supplied. Both folders use `LLM_detects/image_io.py` for OpenCV image files on Unicode paths. The layout generator also uses `LLM_detects/dataset.py` to find example photos.

## Commands

Run these commands from `incident_detection/localization/`. Each script resolves its default assets relative to the repository.

```bash
python osm_map_roads_gate_2p5m.py        # base map from OSM → ../../data/experiments/incident_camera/osm_map/
python map_layout.py                     # layout + renders → ../../data/maps/, print PDFs → ../../data/print/markers/
python pipeline.py --sim                 # pipeline on ../../data/experiments/incident_camera/sim_frames/, compared with ground truth
python pipeline.py FRAME.png [...]       # pipeline on frame files (no LLM calls)
python pipeline.py FRAME.png --detect    # with LLM verdicts (about $0.0003 per photo)
python pipeline.py --camera 0            # live view from an OpenCV camera (q quits)
python photo_finder.py FRAME.png [...]   # photo finder only → ../../data/experiments/incident_camera/photo_finder/
python photo_finder.py --camera 0        # live photo finder (q quits, s saves crops)
python localizer.py FRAME.png [...]      # pose only
python sim_camera.py [--quick]           # simulator check (about 80 s) → ../../data/experiments/incident_camera/sim_report.md
python calibrate_camera.py photos/*.png --cols 9 --rows 6 --square-mm 25
python geo.py                            # self-check against the main-gate coordinates
```

## Base map (`osm_map_roads_gate_2p5m.py`)

The script reads `../../data/gis/KAIST_map.osm` and builds the 2.5 m model map:

1. **Projection:** WGS84 → UTM zone 52N, then the OSM bounding box is scaled uniformly into 2.5 m and centered.
2. **Buildings:** every `building=*` way and the outer ways of building multipolygons, clipped to the map. Each building gets a golden-angle identification color.
3. **Roads:** `highway=secondary`, `unclassified` and `service` (parking aisles excluded), kept as centerlines with a display width per class.
4. **Main gate:** the mean of the OSM entrance nodes named KAIST 정문. The source nodes are kept with their latitude/longitude.
5. **Outputs:** the features JSON, a label-free reference raster (2000 × 2000 px, the gate as a red circle), and a labeled preview for people.

The printed map was made from these outputs, and `../../data/maps/` keeps them (`KAIST_model_reference_2p5m.png` is the reference raster). New runs write to `../../data/experiments/incident_camera/osm_map/` so those files stay exactly as printed.

## Map preparation (`map_layout.py`)

The printed color map stays as it is; tags and photos are pasted on top. Every spot is chosen so that a person can paste the paper exactly by hand: the paper is pushed against printed edges instead of being measured.

**Building tags (ids 0–27).**
- Candidates are outer building corners within 90° ± 2° whose two sides are both longer than the 50 mm paper.
- The paper sits just inside the dark building outline (1.91 mm wide on the print).
- Tags are spread over the map by farthest-point choice, at least 10 cm apart, never on or next to a photo slot.

**Road tags (ids 28–38, numbered after the building tags).**
- Candidates are corners of the open map (map minus roads minus buildings) where road edges, or a road edge and a building outline, meet at 60–120°.
- Road edges come from the original centerlines buffered by their printed width.
- The paper lies along the longer edge and slides into the corner until it touches the other edge, which fixes it for any angle in that range. It never covers the road.
- Tags are added one at a time where they bring the most road length within 25 cm of a tag, until a new tag would add less than 10 cm.

**Photo slots (S1–S4).**
- Each slot is 150 × 100 mm (about 43 × 29 m of real campus), centered over a large building and lined up with its walls.
- At least 60 % of a slot lies on its own building. A slot may hang over open map, but never over another building, a tag, or the gate marker.
- Slots are at least 0.6 m apart, to spread them over the map.

**Photo print format.** Picture, black frame (4 mm, 12 mm under the picture), and a 4 mm white margin. The white margin keeps the frame from touching dark building outlines, so its outer edge is always a clean quadrilateral for the camera.

**Outputs.**

| File | Contents |
|---|---|
| `../../data/maps/apriltag_map.json` | Header: `coordinate_frame`, `map_size_m`, `reference_sha256` (of the features JSON), `family`, `tag_size_m`, `paper_size_m`, frame and corner-order notes. Per tag: `id`, `corners_xy_m` (black square, OpenCV order TL, TR, BR, BL), `center_xy_m`, `yaw_deg`, `paper_corners_xy_m`, `kind` (`building`/`road`), `place`, `corner_xy_m`, `building_index`, `building_osm_id` |
| `../../data/maps/slots.json` | Per slot: `slot`, `corners_xy_m`, `center_xy_m`, `up_xy`, `building_index`, `building_name`, `example_photo` (relative to repository `data/`), and the example's piece, frame and picture corners |
| `../../data/maps/layout_guide.png` | Pasting plan |
| `../../data/maps/map_with_tags*.png`, `map_with_photos*.png` | Finished map renders, color and grayscale, 0.5 mm per pixel |
| `../../data/print/markers/` | One A4 page per tag at exact size with a close-up of its spot, all tags in one PDF, and the example photos at slot size |

OpenCV and pupil-apriltags read the same tag36h11 ids. pupil-apriltags returns the corners in a different order (TR, TL, BL, BR), so a consumer using it must reorder them before matching `corners_xy_m`.

## Photo finder (`photo_finder.py`)

The map print is flat-colored and almost texture-free, tags are square with an even border, and photos are busy quadrilaterals with a near-black frame. Per frame:

1. Run the AprilTag detector once. Decoded tags are masked out; dark-bordered quadrilaterals it found but could not decode become photo candidates.
2. Build a texture map: local standard deviation above a noise-aware threshold.
3. Add candidates from contours of near-black, edge (Canny), adaptive-dark and textured regions.
4. Snap each candidate's sides onto image edges, then keep quadrilaterals with sane corner angles, aspect ratio and area that are busy inside, have a near-black band inside the outline, and have a white margin outside it.
5. Among nested candidates (building outline > photo frame > picture > a sign inside the picture), step inward while the ring between two outlines is flat floor, but never across the photo's own black frame.
6. Find the thick bottom bar, cut the frame off and rectify the picture upright. A near-square frame counts only if its bar is found, since an undecoded tag is square too.

A crop whose short side is under 120 px is flagged "fly closer" and not sent to the LLM. With the default 640×480, 60° camera that means flying at about 0.33 m or lower over a slot.

## Localization (`localizer.py`)

```python
from localizer import Localizer, PoseTracker
loc = Localizer.from_files(frame_size=(640, 480))       # ../../data/calibration/incident_camera.json (or the guess) + ../../data/maps/apriltag_map.json
tracker = PoseTracker()
tags = loc.detect(gray)
pose = loc.solve(tags, tracker.position, gravity_cam)   # gravity_cam: "down" in camera axes from roll/pitch (optional)
tracked = tracker.update(t_seconds, pose)
pose.position, pose.heading_deg, pose.tilt_deg, pose.position_std_m
loc.pixels_to_map(pixels, pose)                         # image points → map meters
```

Each tag gives four 2D–3D point pairs on the map plane (z = 0). All visible tags are solved together:

1. **Detection:** sub-pixel tag corners; ids that are not on the map are ignored, and tiny far tags are dropped when bigger ones are in view.
2. **Pose:** IPPE gives the two mirror solutions a flat target allows; each is refined with Levenberg–Marquardt. The pair is told apart by reprojection error, by "camera above the map, looking down", and in close calls by the previous position.
3. **Tilt prior:** with one or two small tags, tilt is poorly determined, and height × tilt error becomes position error. The camera is fixed pointing down, so the refinement holds the tilt near vertical (5°). If the flight controller's roll/pitch is passed as `gravity_cam`, the tilt is held to it instead (1.5°).
4. **Outliers:** tags whose corners reproject worse than 2.5 px are dropped one at a time; a pose whose RMS error exceeds 2 px is rejected.
5. **Output:** camera position (x, y, height) in map meters, heading of the image's up side (0 = north, 90 = east), tilt, RMS error, tag ids used and dropped, and a position covariance from the Jacobian.

**`PoseTracker`** is a constant-velocity Kalman filter. It gates out measurements that do not fit the motion (chi-square, 99.9 %), coasts on predictions for up to 1 s without tags, and then reports `lost` and starts over.

**Camera model.** Without `../../data/calibration/incident_camera.json` the camera is a distortion-free pinhole guessed from `CameraConfig`, which is enough for the simulator but not for real distances. For real flights, print a chessboard, take 15–30 photos at the flight resolution, and run `calibrate_camera.py`.

## Pipeline (`pipeline.py`)

Per frame:

1. Detect tags and solve the pose (about 4 ms at 640×480), then update the tracker.
2. Run the photo finder (about 35 ms) at most every `photo_interval_s` (0.2 s), so localization keeps up with the camera frame rate.
3. Place each photo on the map: its four corners are cast as rays onto the map plane.
   - The frame's own pose is used when the tracker accepts it.
   - Otherwise the tracked position and heading are used, for example right above a photo where no tag is in view; such placements are marked "about".
4. The photo's map center gives its slot (inside or within 5 cm of `slots.json` corners) and its latitude/longitude (`geo.py`).
5. Without the bottom bar, the crop is turned so that its top faces the slot's `up_xy`.
6. With `--detect`, each crop that is big enough goes to `LLM_detects` with its `Location` (slot, building, map coordinates, latitude/longitude); the `Report` lands in `../../data/experiments/incident_camera/pipeline/<frame>.json`. Live camera mode runs without the LLM.

The operator view (`../../data/experiments/incident_camera/pipeline/<frame>_view.png`) shows the camera frame with tags, photos and their map coordinates on the left, and the map with the drone, its field of view and the photos on the right.

## Geographic coordinates (`geo.py`)

`geo.py` reads the `<bounds>` of `../../data/gis/KAIST_map.osm` and undoes the base map's projection (UTM zone 52N, uniform scale, centering), so any map point becomes a real campus position. Its self-check reproduces the main-gate nodes of the features JSON within a few micrometers. 1 mm on the map is about 0.29 m on campus.

## Simulator (`sim_camera.py`)

The simulator renders what a pinhole camera at a known pose sees of the finished grayscale map, with blur, motion blur, uneven light, sensor noise and JPEG. It then checks every stage against ground truth:

- tag decoding and misreads
- pose error, with no tilt prior, the default prior, and IMU roll/pitch
- photo finding, upright crops and photo map position
- tracking along a continuous flight
- flights along the roads, with and without the road tags

Results for the default 640×480, 60° camera and drone tilt up to 10° (full tables in `../../data/experiments/incident_camera/sim_report.md`):

| Check | Result |
|---|---|
| Tag id misreads | 0 |
| Drone position error | median 6–10 mm; with IMU roll/pitch the worst 10 % stay within 12–39 mm |
| Frames with a pose, continuous flight over the map | 86 % |
| Frames with a pose, flights along roads | 85 % from tags, 92 % with tracking (53 % and 64 % with building tags only) |
| Photos found, 0.3–0.9 m above them | 86–100 %, no false photos |
| Photo position on the map | median 3 mm, 90 % within 8 mm |
| Processing time (Apple Silicon laptop, 640×480) | pose about 4 ms, photo finder about 35 ms |

The real camera, lens and print (gloss, wrinkles, color-to-gray response) differ from the simulation, so these numbers need to be checked on the real map. Over open areas with no corners to paste tags on, such as the long diagonal road across the field, the drone gets no pose from tags and relies on the tracker's prediction.
