"""조원 코드(vision/teammate/)를 import할 수 있게 sys.path에 넣는다.

- incident_detection/localization/ : 평평한 모듈들 (localizer, photo_finder, geo, sim_camera, ...) → tt_down 모드
- localization_app/                : `localization` 패키지 (live_localization, map_matching, ...) → esp32 모드
"""
import sys

from .config import TEAMMATE_DIR

INCIDENT_DIR = TEAMMATE_DIR / "incident_detection" / "localization"
LOCAPP_DIR = TEAMMATE_DIR / "localization_app"
MAPS_DIR = TEAMMATE_DIR / "data" / "maps"
# 우리가 덧붙인 지도 자료(슬롯 보드 태그 ID 39~54 포함, vision/tools/make_slot_boards.py). 조원 원본은 그대로 둔다.
EXTRA_MAPS_DIR = TEAMMATE_DIR.parent / "data" / "maps"


def map_file(name: str, extra: bool):
    """extra(=[bridge] extra_tags)이고 우리 확장 파일이 있으면 그것을, 아니면 조원 원본을."""
    p = EXTRA_MAPS_DIR / name
    return p if extra and p.exists() else MAPS_DIR / name


def setup() -> None:
    for p in (INCIDENT_DIR, LOCAPP_DIR):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


setup()
