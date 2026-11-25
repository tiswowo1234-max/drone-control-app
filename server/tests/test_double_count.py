"""이중 가산 버그 (2026-11-25): 구간 이동 중 카메라 위치가 들어와 추정값을 이미 옮겨 놨는데,
구간이 끝나면 on_move가 구간 거리를 통째로 또 더하던 문제. 카메라가 끊긴 직후 위치가 최대 한 구간 튀었다."""
import pytest

from drone_server.models import Pose
from drone_server.positioning import DeadReckoningProvider, VisionProvider


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make():
    clk = Clock()
    vp = VisionProvider(DeadReckoningProvider(Pose(50, 50, 50, 0)), stale_s=1.0, clock=clk)
    return vp, clk


def test_fix_until_end_of_segment_adds_nothing_more():
    """x=50에서 오른쪽 40cm 구간(15cm/s, 약 2.7초). 카메라가 도착 직전(89cm)까지 봤다가 끊김 → 약 90이어야 한다 (예전: 129)."""
    vp, clk = make()
    vp.begin_move()
    for t, x in ((0.5, 57.5), (1.0, 65.0), (2.0, 80.0), (2.6, 89.0)):
        clk.t = t
        vp.submit(x, 50)
    clk.t = 2.67
    vp.on_move(40, 0, 0)
    clk.t = 4.0                      # 카메라 끊김 (1초 넘음) → 추정값 사용
    assert vp.source == "estimate"
    assert vp.pose().x_cm == pytest.approx(90.0, abs=1.0)


def test_fix_only_in_first_half_adds_remaining_half():
    """구간 절반 지점(70cm)에서 마지막으로 보고 끊김 → 남은 시간 비율(절반)만큼만 더해 약 90."""
    vp, clk = make()
    vp.begin_move()
    clk.t = 1.0
    vp.submit(70, 50)
    clk.t = 2.0
    vp.on_move(40, 0, 0)
    clk.t = 3.5
    assert vp.pose().x_cm == pytest.approx(90.0, abs=0.01)


def test_no_fix_during_segment_adds_full_segment():
    """구간 내내 카메라가 안 보였으면 예전처럼 구간 거리를 다 더한다."""
    vp, clk = make()
    vp.submit(50, 50)                # 출발 전 위치
    clk.t = 0.1
    vp.begin_move()
    clk.t = 2.8
    vp.on_move(40, 0, 0)
    clk.t = 4.0
    assert vp.pose().x_cm == pytest.approx(90.0)


def test_mode_switch_mid_segment_still_counts_fix():
    """구간 도중 카메라 위치를 받은 뒤 모드 전환(clear)돼도, 이미 반영된 몫은 다시 더하지 않는다."""
    vp, clk = make()
    vp.begin_move()
    clk.t = 2.0
    vp.submit(80, 50)
    vp.clear()
    clk.t = 2.0001
    vp.on_move(40, 0, 0)
    assert vp.pose().x_cm == pytest.approx(80.0, abs=0.1)


def test_on_move_without_begin_is_full_add():
    """begin_move를 부르지 않는 쪽(다른 백엔드)은 예전과 같다."""
    vp, clk = make()
    clk.t = 5.0
    vp.on_move(0, 30, 0)
    assert vp.pose().y_cm == pytest.approx(80.0)
