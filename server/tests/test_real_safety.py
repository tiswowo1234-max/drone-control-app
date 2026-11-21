"""실제 TT 안전 동작 (2026-11-21 실물 시험 로그 반영) — 가짜 Tello로 검사.

A. 비행 중 명령이 없으면 유지 신호(rc 0 0 0 0) → TT의 15초 무명령 자동 착륙 방지
B. TT가 스스로 착륙하면(높이 h가 바닥) 서버가 알아채고 앱에 알림, 바닥에서 land error는 '이미 착지'
C. 목표가 20cm 이내라 움직이지 않으면 앱에 TARGET_TOO_CLOSE
"""
import asyncio
from dataclasses import replace

from drone_server.backends.tello import TelloBackend
from drone_server.controller import Controller
from drone_server.models import Pose
from drone_server.positioning import DeadReckoningProvider
from drone_server.settings import TelloCfg


class FakeRealTello:
    def __init__(self):
        self.h = 0.0
        self.calls = []
        self.land_error = False

    def connect(self):
        pass

    def get_current_state(self):
        return {"bat": 90, "h": self.h, "yaw": 0, "tof": self.h + 10}

    def takeoff(self):
        self.calls.append("takeoff")
        self.h = 80.0

    def land(self):
        if self.land_error:
            self.calls.append("land-error")
            raise RuntimeError("Command 'land' was unsuccessful for 4 tries. Latest response:\t'error'")
        self.calls.append("land")
        self.h = 0.0

    def go_xyz_speed(self, x, y, z, speed):
        self.calls.append(("go", x, y, z))

    def send_rc_control(self, a, b, c, d):
        self.calls.append(("rc", a, b, c, d))


def make(t, **cfg):
    c = TelloCfg(go_axes="sdk", segment_cm=100, keepalive_s=0.3, ground_h_cm=10.0, ground_s=0.3)
    c = replace(c, **cfg)
    return TelloBackend(name="real", cfg=c, provider=DeadReckoningProvider(Pose(153.2, 70.2, 0)),
                        home=Pose(153.2, 70.2, 0), make_cmd_tello=lambda: t)


def rcs(t):
    return [x for x in t.calls if isinstance(x, tuple) and x[0] == "rc"]


def test_keepalive_only_while_flying_and_idle():
    async def go():
        t = FakeRealTello()
        b = make(t)
        await b.connect()
        await asyncio.sleep(0.8)
        assert rcs(t) == []                     # 착지 상태에서는 보내지 않음
        await b.takeoff()
        await asyncio.sleep(1.0)
        n = len(rcs(t))
        assert 2 <= n <= 5                      # 0.3초마다 (명령이 없을 때)
        assert all(x == ("rc", 0, 0, 0, 0) for x in rcs(t))
        await b.land()
        await asyncio.sleep(0.8)
        assert len(rcs(t)) == n                 # 착륙 뒤에는 멈춤
        await b.close()
    asyncio.run(go())


def test_keepalive_off_when_zero():
    async def go():
        t = FakeRealTello()
        b = make(t, keepalive_s=0.0)
        await b.connect()
        await b.takeoff()
        await asyncio.sleep(0.8)
        assert rcs(t) == []
        await b.close()
    asyncio.run(go())


def test_self_landing_detected_and_reported(settings):
    async def go():
        t = FakeRealTello()
        b = make(t)
        await b.connect()
        events = []

        async def emit(m):
            events.append(m)

        c = Controller(settings, b, emit)
        c.note_alive()
        assert (await c.handle_cmd({"type": "cmd", "id": "1", "cmd": "takeoff"}))["ok"]
        for _ in range(100):
            await asyncio.sleep(0.02)
            if c.mode == "hovering" and not c.busy:
                break
        assert c.mode == "hovering" and b.telemetry().flying
        t.h = 0.0                               # TT가 스스로 내려앉음
        for _ in range(50):
            c.note_alive()
            await c.watchdog_once()
            await asyncio.sleep(0.05)
            if c.mode == "idle":
                break
        assert c.mode == "idle" and not b.telemetry().flying
        assert any(e.get("code") == "DRONE_LANDED_ITSELF" for e in events)
        # 이미 착지 → 착륙 명령은 아무것도 하지 않는다(실패 없음)
        ack = await c.handle_cmd({"type": "cmd", "id": "2", "cmd": "land"})
        assert ack["ok"] and "land-error" not in t.calls
        await b.close()
    asyncio.run(go())


def test_land_error_on_ground_counts_as_landed():
    async def go():
        t = FakeRealTello()
        b = make(t, ground_s=60.0)              # 자동 감지는 느리게 → land 처리 경로만 시험
        await b.connect()
        await b.takeoff()
        t.h, t.land_error = 0.0, True           # 이미 바닥인데 서버는 비행 중으로 앎
        await asyncio.sleep(0.2)
        await b.land()                          # error여도 예외 없이 '착지'
        assert not b.telemetry().flying
        await b.close()
    asyncio.run(go())


def test_land_error_in_air_still_fails():
    async def go():
        t = FakeRealTello()
        b = make(t)
        await b.connect()
        await b.takeoff()
        t.land_error = True                     # 공중에서 거절 → 진짜 실패로 알린다
        try:
            await b.land()
            raise AssertionError("실패해야 함")
        except Exception as e:  # noqa: BLE001
            assert "land" in str(e)
        assert b.telemetry().flying
        await b.close()
    asyncio.run(go())


def test_goto_too_close_tells_app(settings):
    async def go():
        t = FakeRealTello()
        b = make(t)
        await b.connect()
        events = []

        async def emit(m):
            events.append(m)

        c = Controller(settings, b, emit)
        c.note_alive()
        await c.handle_cmd({"type": "cmd", "id": "1", "cmd": "takeoff"})
        for _ in range(100):
            await asyncio.sleep(0.02)
            if c.mode == "hovering" and not c.busy:
                break
        moves = [x for x in t.calls if isinstance(x, tuple) and x[0] == "go"]
        ack = await c.handle_cmd({"type": "cmd", "id": "2", "cmd": "goto", "x_cm": 160, "y_cm": 75})  # 약 8cm
        assert ack["ok"]
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not c.busy:
                break
        assert [x for x in t.calls if isinstance(x, tuple) and x[0] == "go"] == moves  # 움직이지 않음
        assert any(e.get("code") == "TARGET_TOO_CLOSE" for e in events)
        await b.close()
    asyncio.run(go())
