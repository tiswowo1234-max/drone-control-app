"""2026-11-25 기능: 초기 정렬(가장 가까운 태그 위에서 위치·방향 확정) + 출동 방향 바라보기."""
import asyncio
from dataclasses import replace

from drone_server.backends.mock import MockBackend
from drone_server.backends.tello import TelloBackend
from drone_server.controller import Controller
from drone_server.models import Pose
from drone_server.positioning import DeadReckoningProvider, SimFrame, VisionProvider
from drone_server.settings import AlignCfg, SimCalibration, TelloCfg
from drone_server.tags import Tag, nearest_tag


# ---------------------------------------------------------------- 출동 방향 바라보기 (real)
class FakeYawTello:
    """yaw를 기억하고 cw/ccw로 바뀌는 가짜 TT (yaw: 시계 방향 +)."""

    def __init__(self, yaw=0.0):
        self.h, self.yaw, self.calls = 0.0, yaw, []

    def connect(self):
        pass

    def get_current_state(self):
        return {"bat": 90, "h": self.h, "yaw": self.yaw, "tof": self.h + 10}

    def takeoff(self):
        self.calls.append("takeoff")
        self.h = 50.0

    def land(self):
        self.calls.append("land")
        self.h = 0.0

    def go_xyz_speed(self, x, y, z, speed):
        self.calls.append(("go", x, y, z))

    def rotate_clockwise(self, d):
        self.calls.append(("cw", d))
        self.yaw = (self.yaw + d + 180) % 360 - 180

    def rotate_counter_clockwise(self, d):
        self.calls.append(("ccw", d))
        self.yaw = (self.yaw - d + 180) % 360 - 180

    def send_rc_control(self, *a):
        pass


def make_real(t, frame=None, **cfg):
    c = replace(TelloCfg(go_axes="sdk", segment_cm=50, face_target=True, face_settle_s=0.0), **cfg)
    start = Pose(100, 100, 0)
    return TelloBackend(name="real", cfg=c, provider=DeadReckoningProvider(start, frame=frame), home=start,
                        make_cmd_tello=lambda: t, frame=frame)


def turns(t):
    return [x for x in t.calls if isinstance(x, tuple) and x[0] in ("cw", "ccw")]


def gos(t):
    return [x for x in t.calls if isinstance(x, tuple) and x[0] == "go"]


def fly(b, coro):
    async def go():
        await b.connect()
        await b.takeoff()
        r = await coro(b)
        await b.close()
        return r
    return asyncio.run(go())


def test_faces_target_then_moves_forward():
    t = FakeYawTello(yaw=0.0)                       # 지도 위쪽을 보고 있음
    b = make_real(t)
    fly(b, lambda b: b.move_to(200, 100, 50))       # 목표는 오른쪽(동쪽) 100cm
    assert turns(t) == [("cw", 90)]
    assert gos(t) and all(abs(y) <= 1 and x >= 20 for _, x, y, _ in gos(t))   # 왼쪽 성분 없이 전진만
    assert sum(x for _, x, _, _ in gos(t)) == 100


def test_turns_counter_clockwise_the_short_way():
    t = FakeYawTello(yaw=0.0)
    b = make_real(t)
    fly(b, lambda b: b.move_to(30, 130, 50))        # 왼쪽 위 (약 -67°)
    assert turns(t) and turns(t)[0][0] == "ccw" and 60 <= turns(t)[0][1] <= 75


def test_no_turn_when_close_or_already_facing():
    t = FakeYawTello(yaw=0.0)
    b = make_real(t)
    fly(b, lambda b: b.move_to(105, 200, 50))       # 거의 정면(약 3°)
    assert turns(t) == []
    t2 = FakeYawTello(yaw=0.0)
    b2 = make_real(t2)
    fly(b2, lambda b: b.move_to(125, 100, 50))      # 25cm < face_min_dist_cm 30
    assert turns(t2) == []


def test_face_target_off_and_sim_frame_never_turn():
    t = FakeYawTello()
    fly(make_real(t, face_target=False), lambda b: b.move_to(200, 100, 50))
    assert turns(t) == []
    t2 = FakeYawTello()
    fly(make_real(t2, frame=SimFrame(SimCalibration(scale_cm_per_m=100.0))), lambda b: b.move_to(200, 100, 50))
    assert turns(t2) == []                           # 시뮬레이터(좌우 반전 좌표)에서는 돌지 않음


def test_uses_camera_heading_offset():
    t = FakeYawTello(yaw=0.0)
    b = make_real(t)

    async def move(b):
        b.heading_offset_deg = 90.0                  # 카메라: 실제 기수는 지도 기준 +90° (동쪽). 연결 때 지워지므로 이후에 설정
        return await b.move_to(200, 100, 50)         # 동쪽 목표 → 이미 바라보고 있음
    fly(b, move)
    assert turns(t) == []
    assert gos(t) and all(abs(y) <= 1 for _, _, y, _ in gos(t))


# ---------------------------------------------------------------- 초기 정렬 (mock + vision)
TAG = Tag(37, 147.1, 91.1)


async def setup(settings, **align):
    s = replace(settings, mock=replace(settings.mock, pos_source="vision"),
                align=replace(AlignCfg(enabled=True, wait_fix_s=0.2, min_fixes=3, timeout_s=1.0), **align))
    b = MockBackend(s)
    await b.connect()
    events = []

    async def emit(m):
        events.append(m)

    c = Controller(s, b, emit)
    c._tags = [TAG, Tag(5, 20.0, 230.0)]
    return b, c, events


def codes(ev):
    return [e["code"] for e in ev if e.get("type") == "event"]


async def feed(b, stop, conf=0.9):
    """가짜 카메라: 드론 참값 위치를 0.05초마다 보낸다."""
    while not stop.is_set():
        p = b.truth
        b.vision.submit(p.x_cm, p.y_cm, 0.0, None, None, confidence=conf)
        await asyncio.sleep(0.05)


async def wait_idle(c, timeout=10.0):
    for _ in range(int(timeout / 0.02)):
        if not c.busy:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("mission not finished")


def test_align_goes_to_nearest_tag_once(settings):
    async def go():
        b, c, ev = await setup(settings)
        stop = asyncio.Event()
        task = asyncio.create_task(feed(b, stop))
        await c.handle_cmd({"type": "cmd", "id": "1", "cmd": "takeoff"})
        await wait_idle(c)
        assert codes(ev)[:2] == ["ALIGN_START", "ALIGN_DONE"] and c.aligned
        assert abs(b.truth.x_cm - TAG.x_cm) < 1 and abs(b.truth.y_cm - TAG.y_cm) < 1   # 태그 바로 위
        assert c.mode == "hovering"
        await c.handle_cmd({"type": "cmd", "id": "2", "cmd": "land"})
        await wait_idle(c)
        n = len(codes(ev))
        await c.handle_cmd({"type": "cmd", "id": "3", "cmd": "takeoff"})   # 두 번째 이륙: 다시 하지 않음
        await wait_idle(c)
        assert "ALIGN_START" not in codes(ev)[n:]
        stop.set()
        await task
    asyncio.run(go())


def test_align_then_goto_continues_to_target(settings):
    async def go():
        b, c, ev = await setup(settings)
        stop = asyncio.Event()
        task = asyncio.create_task(feed(b, stop))
        ack = await c.handle_cmd({"type": "cmd", "id": "1", "cmd": "goto", "x_cm": 120, "y_cm": 150})
        assert ack["ok"]
        await wait_idle(c)
        assert "ALIGN_DONE" in codes(ev)
        assert abs(b.truth.x_cm - 120) < 1 and abs(b.truth.y_cm - 150) < 1
        stop.set()
        await task
    asyncio.run(go())


def test_align_fails_without_camera_but_keeps_flying(settings):
    async def go():
        b, c, ev = await setup(settings)
        await c.handle_cmd({"type": "cmd", "id": "1", "cmd": "takeoff"})
        await wait_idle(c)
        assert "ALIGN_FAILED" in codes(ev) and not c.aligned
        assert b.flying and c.mode == "hovering"
    asyncio.run(go())


def test_align_ignores_low_confidence_fixes(settings):
    async def go():
        b, c, ev = await setup(settings)
        stop = asyncio.Event()
        task = asyncio.create_task(feed(b, stop, conf=0.3))
        await c.handle_cmd({"type": "cmd", "id": "1", "cmd": "takeoff"})
        await wait_idle(c)
        assert "ALIGN_FAILED" in codes(ev)
        stop.set()
        await task
    asyncio.run(go())


def test_align_skipped_when_tag_far(settings):
    async def go():
        b, c, ev = await setup(settings, max_tag_dist_cm=10.0)
        await c.handle_cmd({"type": "cmd", "id": "1", "cmd": "takeoff"})
        await wait_idle(c)
        assert "ALIGN_SKIPPED" in codes(ev) and "ALIGN_START" not in codes(ev)
    asyncio.run(go())


def test_align_disabled_or_no_vision_does_nothing(settings):
    async def go():
        s = replace(settings, align=AlignCfg(enabled=False))
        b = MockBackend(s)
        await b.connect()
        c = Controller(s, b)
        await c.handle_cmd({"type": "cmd", "id": "1", "cmd": "takeoff"})
        await wait_idle(c)
        assert not c.aligned and c.mode == "hovering"
    asyncio.run(go())


def test_reconnect_resets_alignment(settings):
    async def go():
        b, c, ev = await setup(settings)
        c.aligned = True
        await c.watchdog_once()
        b.connected = False
        await c.watchdog_once()
        b.connected = True
        await c.watchdog_once()
        assert not c.aligned
    asyncio.run(go())


def test_nearest_tag():
    assert nearest_tag([], 0, 0) is None
    t, d = nearest_tag([TAG, Tag(5, 20.0, 230.0)], 153.2, 70.2)
    assert t.id == 37 and 21 < d < 23


def test_vision_fix_counting():
    v = VisionProvider(DeadReckoningProvider(Pose(0, 0, 0)), stale_s=1.0)
    s0 = v.seq
    v.submit(1, 1, None, None, None, confidence=0.9)
    v.submit(2, 2, None, None, None, confidence=0.2)
    v.submit(3, 3, None, None, None, confidence=None)
    assert v.fixes_since(s0, 0.6) == 2 and v.fixes_since(s0, 0.0) == 3 and v.fixes_since(v.seq, 0.0) == 0
