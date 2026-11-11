"""Controller + MockBackend 흐름 (time_scale로 빠르게)."""
import asyncio
from dataclasses import replace

from drone_server.backends.mock import MockBackend
from drone_server.controller import Controller


def run(coro):
    return asyncio.run(coro)


async def setup(settings):
    b = MockBackend(settings)
    await b.connect()
    events = []

    async def emit(m):
        events.append(m)

    return b, Controller(settings, b, emit), events


async def wait_mode(c, mode, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if c.mode == mode and not c.busy:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"mode {c.mode!r} != {mode!r}")


def cmd(cid, name, **kw):
    return {"type": "cmd", "id": cid, "cmd": name, **kw}


def test_goto_from_ground_then_return_home(settings):
    async def go():
        b, c, ev = await setup(settings)
        ack = await c.handle_cmd(cmd("1", "goto", x_cm=120, y_cm=90))
        assert ack == {"type": "ack", "id": "1", "ok": True}
        await asyncio.sleep(0.05)
        assert c.mode in ("taking_off", "moving") and c.target == (120, 90)
        await wait_mode(c, "hovering")
        p = b.telemetry().pos
        assert (round(p.x_cm), round(p.y_cm), round(p.z_cm)) == (120, 90, 80)
        assert c.target is None
        c.note_alive()
        assert (await c.handle_cmd(cmd("2", "return_home")))["ok"]
        await wait_mode(c, "idle")
        t = b.telemetry()
        assert not t.flying and round(t.pos.x_cm) == 20 and round(t.pos.y_cm) == 20 and t.pos.z_cm == 0
    run(go())


def test_takeoff_goes_to_default_alt(settings):
    async def go():
        b, c, _ = await setup(settings)
        assert (await c.handle_cmd(cmd("1", "takeoff")))["ok"]
        await wait_mode(c, "hovering")
        assert round(b.telemetry().pos.z_cm) == 80
    run(go())


def test_goto_keeps_current_alt_when_z_omitted(settings):
    async def go():
        b, c, _ = await setup(settings)
        await c.handle_cmd(cmd("1", "goto", x_cm=50, y_cm=50, z_cm=120))
        await wait_mode(c, "hovering")
        await c.handle_cmd(cmd("2", "goto", x_cm=60, y_cm=60))
        await wait_mode(c, "hovering")
        assert round(b.telemetry().pos.z_cm) == 120
    run(go())


def test_stop_interrupts_move(settings):
    async def go():
        s = replace(settings, mock=replace(settings.mock, time_scale=2.0))
        b, c, _ = await setup(s)
        b.flying = True
        b.pose.z_cm = 80
        await c.handle_cmd(cmd("1", "goto", x_cm=180, y_cm=120))
        await asyncio.sleep(0.3)
        assert c.mode == "moving"
        ack = await c.handle_cmd(cmd("2", "stop"))
        assert ack["ok"]
        await wait_mode(c, "hovering")
        p1 = b.telemetry().pos
        await asyncio.sleep(0.2)
        p2 = b.telemetry().pos
        assert (p1.x_cm, p1.y_cm) == (p2.x_cm, p2.y_cm)  # 멈춰 있음
        assert p1.x_cm < 170 and c.target is None
    run(go())


def test_land_interrupts_move(settings):
    async def go():
        s = replace(settings, mock=replace(settings.mock, time_scale=2.0))
        b, c, _ = await setup(s)
        b.flying, b.pose.z_cm = True, 80
        await c.handle_cmd(cmd("1", "goto", x_cm=180, y_cm=120))
        await asyncio.sleep(0.2)
        assert (await c.handle_cmd(cmd("2", "land")))["ok"]
        assert c.mode == "landing"
        # 착륙 중 goto는 BUSY
        assert (await c.handle_cmd(cmd("3", "goto", x_cm=50, y_cm=50)))["error"] == "BUSY"
        await wait_mode(c, "idle")
        assert not b.flying and b.pose.x_cm < 170
    run(go())


def test_emergency_requires_confirm_and_stops_immediately(settings):
    async def go():
        b, c, ev = await setup(settings)
        b.flying, b.pose.z_cm = True, 80
        await c.handle_cmd(cmd("1", "goto", x_cm=180, y_cm=120))
        assert (await c.handle_cmd(cmd("2", "emergency")))["error"] == "NOT_CONFIRMED"
        assert (await c.handle_cmd(cmd("3", "emergency", confirm="yes")))["error"] == "NOT_CONFIRMED"
        assert b.flying
        ack = await c.handle_cmd(cmd("4", "emergency", confirm=True))
        assert ack["ok"] and c.mode == "emergency" and not b.flying and b.pose.z_cm == 0
        assert any(e["code"] == "EMERGENCY_STOP" for e in ev)
        # 비상 정지 뒤에도 다시 출동 가능
        assert (await c.handle_cmd(cmd("5", "goto", x_cm=60, y_cm=60)))["ok"]
        await wait_mode(c, "hovering")
    run(go())


def test_low_battery_refuses_dispatch(settings):
    async def go():
        b, c, _ = await setup(settings)
        b.battery = 15
        for name in ("takeoff", "goto"):
            ack = await c.handle_cmd(cmd("x", name, x_cm=50, y_cm=50))
            assert ack["error"] == "LOW_BATTERY", ack
        assert c.mode == "idle" and not b.flying
    run(go())


def test_low_battery_still_allows_return_and_land(settings):
    async def go():
        b, c, ev = await setup(settings)
        b.flying, b.pose.z_cm = True, 80
        b.pose.x_cm = 60
        b.battery = 10
        await c.watchdog_once()
        assert any(e["code"] == "LOW_BATTERY" for e in ev)
        assert (await c.handle_cmd(cmd("1", "return_home")))["ok"]
        await wait_mode(c, "idle")
    run(go())


def test_not_connected(settings):
    async def go():
        b = MockBackend(settings)  # connect() 안 함
        c = Controller(settings, b)
        for name in ("takeoff", "goto", "stop", "land", "return_home"):
            ack = await c.handle_cmd(cmd("x", name, x_cm=50, y_cm=50))
            assert ack["error"] == "NOT_CONNECTED", (name, ack)
        assert (await c.handle_cmd(cmd("y", "emergency", confirm=True)))["error"] == "NOT_CONNECTED"
    run(go())


def test_safety_rejections_reach_ack(settings):
    async def go():
        b, c, _ = await setup(settings)
        cases = [
            (dict(x_cm=-5, y_cm=10), "OUT_OF_BOUNDS"),
            (dict(x_cm=240, y_cm=170), "TOO_FAR"),
            (dict(x_cm=50, y_cm=50, z_cm=300), "ALT_LIMIT"),
            (dict(x_cm="50", y_cm=50), "BAD_REQUEST"),
            (dict(x_cm=50), "BAD_REQUEST"),
            (dict(x_cm=True, y_cm=50), "BAD_REQUEST"),
        ]
        for kw, err in cases:
            ack = await c.handle_cmd(cmd("x", "goto", **kw))
            assert ack["ok"] is False and ack["error"] == err, (kw, ack)
            assert ack["message"]
        assert not b.flying
    run(go())


def test_link_timeout_auto_lands(settings):
    async def go():
        s = replace(settings, limits=replace(settings.limits, link_timeout_s=0.3))
        b, c, ev = await setup(s)
        await c.handle_cmd(cmd("1", "takeoff"))
        await wait_mode(c, "hovering")
        await asyncio.sleep(0.4)  # ping 없음
        await c.watchdog_once()
        assert c.mode == "landing"
        assert any(e["code"] == "LINK_LOST_LANDING" and e["level"] == "warn" for e in ev)
        await wait_mode(c, "idle")
        assert not b.flying
    run(go())


def test_ping_keeps_flight_alive(settings):
    async def go():
        s = replace(settings, limits=replace(settings.limits, link_timeout_s=0.3))
        b, c, ev = await setup(s)
        await c.handle_cmd(cmd("1", "takeoff"))
        await wait_mode(c, "hovering")
        for _ in range(6):
            c.note_alive()
            await asyncio.sleep(0.1)
            await c.watchdog_once()
        assert c.mode == "hovering" and b.flying
    run(go())


def test_new_goto_replaces_current(settings):
    async def go():
        s = replace(settings, mock=replace(settings.mock, time_scale=4.0))
        b, c, _ = await setup(s)
        b.flying, b.pose.z_cm = True, 80
        await c.handle_cmd(cmd("1", "goto", x_cm=200, y_cm=20))
        await asyncio.sleep(0.2)
        await c.handle_cmd(cmd("2", "goto", x_cm=20, y_cm=150))
        assert c.target == (20, 150)
        await wait_mode(c, "hovering")
        p = b.telemetry().pos
        assert (round(p.x_cm), round(p.y_cm)) == (20, 150)
    run(go())
