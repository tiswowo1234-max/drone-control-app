"""조원 코드(vision/teammate/)를 import할 수 있게 sys.path에 넣는다.

- incident_detection/localization/ : 평평한 모듈들 (localizer, photo_finder, geo, sim_camera, ...) → tt_down 모드
- localization_app/                : `localization` 패키지 (live_localization, map_matching, ...) → esp32 모드
"""
import sys

from .config import TEAMMATE_DIR

INCIDENT_DIR = TEAMMATE_DIR / "incident_detection" / "localization"
LOCAPP_DIR = TEAMMATE_DIR / "localization_app"
MAPS_DIR = TEAMMATE_DIR / "data" / "maps"


def setup() -> None:
    for p in (INCIDENT_DIR, LOCAPP_DIR):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


setup()
