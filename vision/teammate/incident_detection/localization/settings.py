"""All settings of the localization folder: the printed map (AprilTags, photo slots), the camera
guess, the photo finder, the localizer, the tracker and the pipeline.

Coordinates, map images, print files and test photos are in the repository's data/ directory. The LLM settings stay
in LLM_detects/config.py."""
from dataclasses import dataclass
from pathlib import Path

import _paths

MAP_DIR = _paths.DATA / "maps"
OUTPUTS = _paths.DATA / "experiments" / "incident_camera"


@dataclass(frozen=True)
class MapConfig:
    # The color base map used for the 2.5 m x 2.5 m print.
    # Map frame: meters, origin at the lower-left corner, +x east (right), +y north (up).
    image: Path = MAP_DIR / "KAIST_model_reference_2p5m.png"
    # Buildings, roads and main gate behind that print, made by map_tools/build_campus_map.py from KAIST_map.osm
    features: Path = MAP_DIR / "KAIST_model_features_2p5m.json"
    size_m: float = 2.5
    # Building outlines are drawn 0.55 pt wide on a 10 in figure that becomes 2.5 m, so 1.91 mm on the print.
    # Tags and photos sit just inside that dark line.
    outline_width_mm: float = 1.91
    out_dir: Path = MAP_DIR  # apriltag_map.json, slots.json and the map renders
    print_dir: Path = _paths.DATA / "print" / "markers"


@dataclass(frozen=True)
class TagConfig:
    family: str = "tag36h11"  # OpenCV DICT_APRILTAG_36h11; same ids as the official AprilTag library
    size_mm: float = 40.0  # edge of the black square, the size pose estimation needs
    quiet_zone_cells: int = 1  # white border around the black square, in tag cells (cell = size_mm / 8)
    # Tags go only into building corners this close to 90 degrees, so two printed edges fix the paper
    max_corner_error_deg: float = 2.0
    min_spacing_m: float = 0.10  # center to center
    max_tags: int = 60
    first_id: int = 0
    # Road tags, for flights along roads: in corners of the open map where road edges meet
    # (ids follow the building tags, so adding them never renumbers those)
    road_tags: bool = True
    # Road edges rarely meet at exactly 90 deg, so a road tag lies along one edge and is pushed into
    # the corner until it touches the other edge, which fixes its place for any angle in this range
    road_corner_angle_deg: tuple[float, float] = (60.0, 120.0)
    max_road_tags: int = 24
    road_cover_radius_m: float = 0.25  # a road point counts as covered within this distance of a tag
    min_road_gain_m: float = 0.1  # stop when the next road tag would cover less new road than this

    @property
    def paper_mm(self) -> float:
        return self.size_mm * (8 + 2 * self.quiet_zone_cells) / 8


@dataclass(frozen=True)
class SlotConfig:
    # Places where incident photos are pasted, each centered over a large building. 150 x 100 mm on the
    # 2.5 m map is about 43 x 29 m of real campus. Most buildings are narrower than that on the map, so
    # a slot may hang over open map next to its building, but never over another building or a tag.
    count: int = 4
    width_mm: float = 150.0
    height_mm: float = 100.0
    min_cover: float = 0.6  # at least this fraction of the slot lies on its own building
    min_spacing_m: float = 0.6  # slot center to slot center, to spread them over the map
    margin_mm: float = 15.0  # clear gap from a slot to other buildings and to any tag paper
    # Each photo is printed like a fiducial marker: picture, then a black frame, then a white margin.
    # The margin keeps the frame from touching dark building outlines, so its edge is always a clean
    # quadrilateral for the camera (the same reason AprilTags have a white quiet zone).
    frame_mm: float = 4.0
    # The frame is thicker under the picture (like a Polaroid), so the camera can tell which way is
    # up whatever the drone's heading and turn the crop upright before the LLM sees it.
    bottom_bar_mm: float = 12.0
    white_margin_mm: float = 4.0
    # data/photos/photo_unlabeled ids used for the example layout and print files, one per group (A fire,
    # B restricted area, C road, D person down); any photo can go into any slot
    example_ids: tuple[str, ...] = ("3", "12", "4", "9")


@dataclass(frozen=True)
class CameraConfig:
    # Assumed grayscale camera for sim_camera.py. Replace with the real module's numbers.
    width_px: int = 640
    height_px: int = 480
    hfov_deg: float = 60.0


@dataclass(frozen=True)
class PhotoFinderConfig:
    min_area_frac: float = 0.004  # of the frame area
    max_area_frac: float = 0.9
    # Long side / short side of the frame outline. Below the minimum (nearly square, like an undecoded
    # tag) a quad counts only if its thick bottom bar is found; above the maximum it never counts.
    aspect_range: tuple[float, float] = (1.08, 2.4)
    max_angle_error_deg: float = 25.0  # corner angles of the quadrilateral, away from 90
    # A pixel is "textured" when its local standard deviation is above max(min_std, noise_factor * noise)
    texture_min_std: float = 6.0
    texture_noise_factor: float = 4.0
    min_inside_texture: float = 0.35  # photos are busy inside ...
    max_outside_texture: float = 0.65  # ... and the map around them is mostly flat (a sign inside a photo is not)
    # Only accept photos printed like SlotConfig says: dark frame band inside the edge, white margin
    # outside it. Turn off to also try unframed prints (more false finds on map junctions).
    require_frame: bool = True
    edge_margin_px: int = 8  # ignore quads this close to the image border: probably cut off
    max_ring_texture: float = 0.2  # nested outlines: a flatter ring between them means "same photo, less floor"
    # The white margin's brightness relative to the local average (paper white is the brightest thing around)
    min_margin_brightness: float = 0.85
    # Smaller crops (picture only, frame cut off) are flagged "fly closer" and not sent to the LLM. In the
    # simulator ~100 px crops were too blurry for it; 120 px needs about 0.33 m height with a 640x480,
    # 60 deg camera and the 150 x 100 mm slot. A sharper or higher-resolution camera relaxes this.
    min_short_side_px: int = 120
    out_dir: Path = OUTPUTS / "photo_finder"


@dataclass(frozen=True)
class LocalizerConfig:
    # Written by calibrate_camera.py. Without it the camera is a distortion-free pinhole guessed from
    # CameraConfig (hfov_deg), which is fine for the simulator but not for real flights.
    camera_file: Path = _paths.DATA / "calibration" / "incident_camera.json"
    tags_file: Path = MAP_DIR / "apriltag_map.json"
    slots_file: Path = MAP_DIR / "slots.json"
    max_tag_error_px: float = 2.5  # a tag whose corners reproject worse than this is dropped as an outlier
    max_pose_error_px: float = 2.0  # poses with a worse overall RMS reprojection error are rejected
    min_tag_side_px: float = 12.0  # smaller tags give unstable poses; ignore them when bigger ones exist
    pixel_noise_px: float = 0.5  # assumed corner noise, for the pose covariance
    slot_match_m: float = 0.05  # a photo within this distance of a slot counts as that slot
    # Tilt prior. One or two small tags pin down position along the image but hardly the camera's
    # tilt, and height x tilt error = position error (the planar solution can even flip). So:
    # with IMU roll/pitch passed to Localizer.solve (gravity_cam), tilt is held within gravity_prior_deg;
    # without it, a "camera looks straight down" prior of level_prior_deg (None = off). The camera is
    # fixed pointing down, so its tilt is only the drone's roll/pitch; in the simulator 5 deg beat
    # 15 deg for drone tilts up to 10 deg (worst 10 % of position errors 56-60 mm instead of 82-89 mm).
    gravity_prior_deg: float = 1.5
    level_prior_deg: float | None = 5.0


@dataclass(frozen=True)
class TrackerConfig:
    accel_noise_m_s2: float = 3.0  # how hard the drone can change velocity (process noise)
    min_position_std_m: float = 0.003  # floor for the measurement noise
    gate_chi2: float = 16.3  # 3-DOF chi-square at 99.9 %: farther measurements are treated as outliers
    max_rejects: int = 5  # after this many outliers in a row, restart from the next measurement
    max_coast_s: float = 1.0  # without measurements for longer than this, the pose is lost
    heading_smoothing: float = 0.5  # 0 = keep old heading, 1 = take the new one


@dataclass(frozen=True)
class PipelineConfig:
    out_dir: Path = OUTPUTS / "pipeline"
    map_view_px: int = 720  # side of the map panel in the operator view
    # Localization runs on every frame (about 4 ms at 640x480); the photo finder costs about 35 ms,
    # so it runs at most this often to keep the localization rate at the camera's frame rate
    photo_interval_s: float = 0.2
    map_view_image: Path = MAP_DIR / "map_with_photos.png"
