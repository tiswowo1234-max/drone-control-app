import pytest

from drone_server.safety import SafetyError, check_battery, check_target, point_in_polygon
from drone_server.settings import load_settings


def code(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except SafetyError as e:
        return e.code
    return None


def test_ok_target(settings):
    assert code(check_target, settings, 120, 90, 80) is None


@pytest.mark.parametrize("x,y", [(-1, 50), (251, 50), (50, -0.1), (50, 251)])
def test_out_of_bounds(settings, x, y):
    assert code(check_target, settings, x, y, 80) == "OUT_OF_BOUNDS"


def test_map_edges_are_inside(settings):
    assert code(check_target, settings, 0, 0, 80) is None


def test_too_far(settings):
    # home 정문 (153.2,70.2) 에서 반경 200 → (10,240)은 약 222cm
    assert code(check_target, settings, 10, 240, 80) == "TOO_FAR"
    assert code(check_target, settings, 153, 250, 80) is None  # 약 180cm


@pytest.mark.parametrize("z", [49, 151, -10])
def test_alt_limit(settings, z):
    assert code(check_target, settings, 100, 100, z) == "ALT_LIMIT"


def test_nan_is_bad_request(settings):
    assert code(check_target, settings, float("nan"), 10, 80) == "BAD_REQUEST"


def test_no_fly_zone(zone_settings):
    assert code(check_target, zone_settings, 115, 115, 80) == "NO_FLY_ZONE"
    assert code(check_target, zone_settings, 90, 115, 80) is None


def test_path_through_no_fly_zone(zone_settings):
    # (90,115) → (140,115) 직선이 금지 구역을 가로지름
    assert code(check_target, zone_settings, 140, 115, 80, from_xy=(90, 115)) == "NO_FLY_ZONE"
    assert code(check_target, zone_settings, 140, 90, 80, from_xy=(90, 90)) is None


def test_battery(settings):
    assert code(check_battery, settings, 19) == "LOW_BATTERY"
    assert code(check_battery, settings, 20) is None
    assert code(check_battery, settings, None) is None


def test_point_in_polygon():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert point_in_polygon(5, 5, sq)
    assert not point_in_polygon(15, 5, sq)


def test_real_settings_file_loads():
    s = load_settings()
    assert s.map_width_cm > 0 and s.limits.max_alt_cm > s.limits.min_alt_cm
