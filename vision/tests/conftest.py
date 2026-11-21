import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore", category=UserWarning)

from bridge import config  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return config.load()


@pytest.fixture(scope="session")
def scene(settings):
    from bridge.synthetic import MapScene
    return MapScene(settings.map_w_m, "photos")
