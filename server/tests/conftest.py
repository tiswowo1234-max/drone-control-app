import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drone_server.settings import Limits, MockCfg, NoFlyZone, Settings  # noqa: E402


def make_settings(**over) -> Settings:
    """테스트용 고정 설정 (settings.toml 값이 바뀌어도 테스트가 흔들리지 않게)."""
    s = Settings(
        map_width_cm=250, map_height_cm=180, home_x_cm=20, home_y_cm=20,
        limits=Limits(max_radius_cm=200, min_alt_cm=50, max_alt_cm=150, default_alt_cm=80,
                      min_battery_pct=20, link_timeout_s=3),
        no_fly_zones=[], geo=None, backend="mock",
        mock=MockCfg(time_scale=40.0),
    )
    return replace(s, **over)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def zone_settings() -> Settings:
    return make_settings(no_fly_zones=[NoFlyZone("box", [(100, 100), (130, 100), (130, 130), (100, 130)])])
