"""TelloBackend(sim/real 공통) + 시뮬레이터 좌표 보정 — Unity 없이 가짜 Tello로 검사."""
import asyncio
import math
from dataclasses import replace

import pytest

from drone_server.backends.tello import TelloBackend, plan_step
from drone_server.models import Pose
from drone_server.positioning import DeadReckoningProvider, DroneFrame, SimFrame, SimTruthProvider
from drone_server.settings import SimCalibration, TelloCfg, load_settings

CAL = SimCalibration(origin_x_m=35.565, origin_z_m=-35.809, scale_cm_per_m=3.504,
                     map_x_from="-x", map_y_from="+z", rotation_deg=0.03)


class FakeSimTello:
    """Unity 시뮬레이터 흉내: go(a=오른쪽, b=전진, c=위) 시뮬레이터 cm, 세계 좌표 m."""

    def __init__(self, start=(0.0, -2.5), yaw=0.0):
        self.x, self.z = start
        self.h = 0.0
        self.yaw = yaw
        self.flying = False
        self.mode = "Grounded"
        self.calls = []

    def connect(self):
        pass

    def get_sim_debug_state(self):
        return {"bat": 90, "h": self.h, "yaw": self.yaw, "world_x": self.x, "world_z": self.z,
                "flying": self.flying, "flight_mode": self.mode}

    def takeoff(self):
        self.calls.append(("takeoff",))
        self.flying, self.h, self.mode = True, 108.0, "Flying"

    def land(self):
        self.calls.append(("land",))
        self.flying, self.h, self.mode = False, 0.0, "Grounded"

    def emergency(self):
        self.calls.append(("emergency",))
        self.flying, self.h, self.mode = False, 0.0, "Emergency"

    def go_xyz_speed(self, a, b, c, speed):
        assert all(isinstance(v, int) for v in (a, b, c, speed))
        assert max(abs(a), abs(b), abs(c)) >= 20 and all(abs(v) <= 500 for v in (a, b, c))
        self.calls.append(("go", a, b, c))
        r = math.radians(self.yaw)
        right, fwd = a / 100, b / 100
        self.x += fwd * math.sin(r) + right * math.cos(r)
        self.z += fwd * math.cos(r) - right * math.sin(r)
        self.h += c


def make_backend(tello, provider, cfg=None, frame=None):
    cfg = cfg or TelloCfg(go_axes="sim", segment_cm=200, speed_cm_s=100)

    def reset(t):
        t.x, t.z, t.h, t.mode, t.flying = 0.0, -2.5, 0.0, "Grounded", False

    return TelloBackend(name="sim", cfg=cfg, provider=provider, home=Pose(153.2, 70.2, 0),
                        make_cmd_tello=lambda: tello, make_monitor_tello=None,
                        read_state=lambda t: t.get_sim_debug_state(), sim_reset=reset,
                        frame=frame or SimFrame(CAL))


def test_calibration_corners_and_roundtrip():
    for sim, want in [((35.565, -35.809), (0, 0)), ((-35.628, -35.772), (250, 0)),
                      ((35.592, 35.697), (0, 250)), ((-35.601, 35.733), (250, 250))]:
        x, y = CAL.sim_to_map(*sim)
        assert abs(x - want[0]) < 1.5 and abs(y - want[1]) < 1.5, (sim, x, y)
    x, y = CAL.map_to_sim(*CAL.sim_to_map(3.0, -4.0))
    assert abs(x - 3.0) < 1e-9 and abs(y + 4.0) < 1e-9


def test_calibration_is_mirrored_yaw():
    # 시뮬레이터 yaw 0(+z=북) → 지도 yaw 약 0°, 시계방향 회전은 지도에서 반시계(거울 반전)
    assert abs(CAL.sim_yaw_to_map(0) - 0.03) < 0.01
    assert CAL.sim_yaw_to_map(10) < CAL.sim_yaw_to_map(0)


def test_settings_file_calibration_matches():
    c = load_settings().sim_calibration
    assert c.map_x_from == "-x" and abs(c.scale_cm_per_m - 3.5) < 0.1


def test_plan_step_identity_frame():
    cur = Pose(0, 0, 80, 0)
    (fwd, right, up), d = plan_step(cur, 30, 100, 80, 50, DroneFrame(), 0)
    assert up == 0 and fwd > right > 0 and math.hypot(fwd, right) <= 50
    assert plan_step(cur, 10, 10, 85, 50, DroneFrame(), 0) is None  # 20cm 미만 = 도착


def test_plan_step_rotated_drone():
    # 드론이 동쪽(yaw 90)을 보고 있으면 지도 +x 이동은 '전진'
    (fwd, right, up), _ = plan_step(Pose(0, 0, 80, 90), 40, 0, 80, 100, DroneFrame(), 90)
    assert fwd == 40 and right == 0


@pytest.mark.parametrize("source", ["estimate", "sim_truth"])
def test_sim_goto_reaches_map_target(source):
    async def go():
        t = FakeSimTello()
        prov = SimTruthProvider(CAL) if source == "sim_truth" else DeadReckoningProvider(Pose(0, 0, 0), SimFrame(CAL))
        b = make_backend(t, prov)
        b._start_pose = lambda raw: (lambda p: (p.update(raw), p.pose())[1])(SimTruthProvider(CAL))
        await b.connect()
        start = b.telemetry().pos
        assert abs(start.x_cm - 124.7) < 0.5 and abs(start.y_cm - 116.6) < 0.5
        await b.takeoff()
        await b.move_to(100, 60, 80)
        x, y = CAL.sim_to_map(t.x, t.z)
        assert abs(x - 100) < 1 and abs(y - 60) < 1           # 실제(시뮬) 위치
        assert abs(t.h / 100 * CAL.scale_cm_per_m - 80) < 1    # 고도도 지도 배율
        p = b.telemetry().pos
        assert abs(p.x_cm - 100) < 1 and abs(p.y_cm - 60) < 1  # 추정 위치
        assert all(max(abs(c) for c in call[1:]) <= 200 for call in t.calls if call[0] == "go")
        await b.close()
    asyncio.run(go())


def test_real_axes_use_sdk_convention():
    """실제 SDK go(x=전진, y=왼쪽): 북쪽을 보는 드론이 지도 +x(오른쪽)로 가려면 y가 음수."""
    class RealLike(FakeSimTello):
        def go_xyz_speed(self, x, y, z, speed):
            self.calls.append(("go", x, y, z))

        def get_current_state(self):
            return {"bat": 90, "h": self.h, "yaw": 0}

    async def go():
        t = RealLike()
        b = TelloBackend(name="real", cfg=TelloCfg(go_axes="sdk", segment_cm=100), provider=DeadReckoningProvider(Pose(20, 20, 0)),
                         home=Pose(20, 20, 0), make_cmd_tello=lambda: t)
        await b.connect()
        await b.takeoff()
        t.h = 80
        await asyncio.sleep(0.25)
        await b.move_to(80, 20, 80)
        assert t.calls[-1] == ("go", 0, -60, 0)
        assert abs(b.telemetry().pos.x_cm - 80) < 1e-6
        await b.close()
    asyncio.run(go())


def test_sim_height_cap():
    async def go():
        t = FakeSimTello()
        cfg = TelloCfg(go_axes="sim", segment_cm=500, speed_cm_s=100, max_height_cm=2800)
        b = make_backend(t, SimTruthProvider(CAL), cfg)
        await b.connect()
        await b.takeoff()
        await b.move_to(124, 117, 140)  # 140 cm = 시뮬 40 m → 28 m로 제한
        assert t.h <= 2800 and t.h > 2700
        await b.close()
    asyncio.run(go())


def test_land_descends_first_and_emergency_reset():
    async def go():
        t = FakeSimTello()
        cfg = TelloCfg(go_axes="sim", segment_cm=500, speed_cm_s=100, land_descend_to_cm=100)
        b = make_backend(t, SimTruthProvider(CAL), cfg)
        await b.connect()
        await b.takeoff()
        t.h = 2000
        await asyncio.sleep(0.25)
        await b.land()
        downs = [c for c in t.calls if c[0] == "go"]
        assert downs and all(c[1] == 0 and c[2] == 0 and c[3] < 0 for c in downs)
        assert t.calls[-1] == ("land",) and not b.telemetry().flying
        await b.takeoff()
        await b.emergency()
        await asyncio.sleep(0.25)
        assert t.mode == "Emergency" and not b.telemetry().flying
        await b.takeoff()  # 자동 리셋 후 이륙
        assert t.mode == "Flying" and (t.x, t.z) == (0.0, -2.5)
        await b.close()
    asyncio.run(go())


def test_cancelled_move_still_counts_flown_segment():
    """stop으로 미션이 취소돼도 이미 보낸 구간은 추정 위치에 반영돼야 한다."""
    class Slow(FakeSimTello):
        def go_xyz_speed(self, *a):
            import time
            time.sleep(0.2)
            super().go_xyz_speed(*a)

    async def go():
        t = Slow()
        prov = DeadReckoningProvider(Pose(0, 0, 0), SimFrame(CAL))
        b = make_backend(t, prov)
        b._start_pose = lambda raw: (lambda p: (p.update(raw), p.pose())[1])(SimTruthProvider(CAL))
        await b.connect()
        await b.takeoff()
        task = asyncio.create_task(b.move_to(30, 30, 80))
        await asyncio.sleep(0.3)
        task.cancel()
        await b.hover()  # 진행 중 구간이 끝날 때까지 기다림
        est = b.telemetry().pos
        x, y = CAL.sim_to_map(t.x, t.z)
        assert abs(est.x_cm - x) < 0.5 and abs(est.y_cm - y) < 0.5
        await b.close()
    asyncio.run(go())


def test_settings_validation_rejects_bad_axes():
    from drone_server.settings import validate
    s = load_settings()
    with pytest.raises(ValueError):
        validate(replace(s, sim_calibration=replace(s.sim_calibration, map_x_from="x")))
