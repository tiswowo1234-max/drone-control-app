"""UGV 따라가기 (API v0.8 13.5): follow_start → rc 속도 명령으로 차 뒤를 따라감.

가짜 드론(mock) + '가짜 카메라'(드론 참값 위치와 차 위치로 드론 기준 상대 위치를 계산해 on_ugv_relative로 넣음).
"""
import asyncio
import math
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from drone_server.app import create_app
from drone_server.backends.mock import MockBackend
from drone_server.controller import Controller, follow_velocity
from drone_server.settings import FollowCfg, Limits, MockCfg


def run(coro):
    return asyncio.run(coro)


def cmd(cid, name, **kw):
    return {"type": "cmd", "id": cid, "cmd": name, **kw}


def follow_settings(settings, **kw):
    # 가짜 드론 4배속. 제어 주기 0.1초(실제 시간) 동안 4배로 움직이므로 gain도 그에 맞춰 작게
    f = FollowCfg(gain_per_s=2.0, **kw)
    return replace(settings, mock=MockCfg(time_scale=4.0), follow=f)


class FakeCar:
    """지도 위 차 (cm, yaw 위쪽 0·시계 +). vx, vy = 지도 속도 cm/s (실제 시간)."""

    def __init__(self, x, y, yaw=0.0, vx=0.0, vy=0.0):
        self.x, self.y, self.yaw, self.vx, self.vy = x, y, yaw, vx, vy
        self.visible = True

    def relative_to(self, drone):
        """드론(참값) 기준 (앞, 오른쪽, 차 방향 - 드론 방향)."""
        dx, dy = self.x - drone.x_cm, self.y - drone.y_cm
        r = math.radians(drone.yaw_deg)
        fwd = dx * math.sin(r) + dy * math.cos(r)
        right = dx * math.cos(r) - dy * math.sin(r)
        rel = (self.yaw - drone.yaw_deg + 180) % 360 - 180
        return fwd, right, rel

    def behind(self, d):
        a = math.radians(self.yaw)
        return self.x - d * math.sin(a), self.y - d * math.cos(a)


async def setup(settings, start=None):
    b = MockBackend(settings)
    await b.connect()
    b.flying = True
    for p in (b.pose, b.truth):
        p.z_cm = 80.0
        if start:
            p.x_cm, p.y_cm = start
    events = []

    async def emit(m):
        events.append(m)

    return b, Controller(settings, b, emit), events


async def camera(c, b, car, period=0.05):
    """가짜 카메라: 차가 보이면 상대 위치를 컨트롤러에 넣고, 차를 움직인다."""
    while True:
        await asyncio.sleep(period)
        car.x += car.vx * period
        car.y += car.vy * period
        b.telemetry()   # 가짜 드론 속도 적분
        if car.visible:
            c.on_ugv_relative(*car.relative_to(b.truth))


def codes(events):
    return [e["code"] for e in events]


# ================================================================ 속도 계산

def test_follow_velocity_math():
    f = FollowCfg(distance_cm=12, gain_per_s=1.0, max_speed_cm_s=100, deadband_cm=3)
    # 차가 앞 40cm, 같은 방향 → 목표(차 뒤 12cm)는 앞 28cm
    assert follow_velocity(40, 0, 0, f) == pytest.approx((28, 0))
    # 이미 목표 근처 → 0
    assert follow_velocity(13, 1, 0, f) == (0.0, 0.0)
    # 차가 오른쪽을 봄(+90°), 드론 기준 오른쪽 20cm → 목표 = 오른쪽 8cm
    assert follow_velocity(0, 20, 90, f) == pytest.approx((0, 8), abs=1e-9)
    # 차가 드론 쪽을 봄(180°), 앞 20cm → 목표 = 앞 32cm (차 '뒤'는 더 멀리)
    assert follow_velocity(20, 0, 180, f) == pytest.approx((32, 0), abs=1e-9)
    # 속도 상한
    vf, vr = follow_velocity(200, 200, 0, replace(f, max_speed_cm_s=15))
    assert math.hypot(vf, vr) == pytest.approx(15)


# ================================================================ 시작 조건

def test_follow_start_rejects(settings):
    async def go():
        s = follow_settings(settings)
        b, c, ev = await setup(s)
        b.flying = False
        ack = await c.handle_cmd(cmd("1", "follow_start"))
        assert ack["error"] == "NOT_FLYING"
        b.flying = True
        ack = await c.handle_cmd(cmd("2", "follow_start"))
        assert ack["error"] == "UGV_NOT_SEEN"           # 한 번도 못 봄
        c.on_ugv_relative(30, 0, 0)
        c.ugv_rel_at -= 5                               # 5초 전에 봄
        assert (await c.handle_cmd(cmd("3", "follow_start")))["error"] == "UGV_NOT_SEEN"
        c2 = Controller(replace(s, follow=replace(s.follow, enabled=False)), b)
        assert (await c2.handle_cmd(cmd("4", "follow_start")))["error"] == "BAD_REQUEST"
        assert c.mode == "idle" and not c.busy
    run(go())


# ================================================================ 따라가기

def test_follow_reaches_point_behind_stopped_car(settings):
    async def go():
        s = follow_settings(settings)
        b, c, ev = await setup(s)
        car = FakeCar(150, 110, yaw=0)                  # 드론(153, 70)에서 앞 40cm쯤
        cam = asyncio.create_task(camera(c, b, car))
        try:
            await asyncio.sleep(0.1)
            assert (await c.handle_cmd(cmd("1", "follow_start")))["ok"]
            assert c.mode == "following"
            await asyncio.sleep(2.0)
            assert "FOLLOW_START" in codes(ev)
            tx, ty = car.behind(s.follow.distance_cm)
            assert math.hypot(b.truth.x_cm - tx, b.truth.y_cm - ty) < s.follow.deadband_cm + 1.5
            assert c.mode == "following"
            assert b.truth.z_cm == pytest.approx(80.0)    # 고도는 그대로
        finally:
            cam.cancel()
            await c.shutdown()
    run(go())


def test_follow_tracks_moving_car_any_heading(settings):
    async def go():
        s = follow_settings(settings)
        b, c, ev = await setup(s)
        car = FakeCar(170, 90, yaw=90, vx=8 / 4, vy=0)  # 오른쪽(+x)을 보고 오른쪽으로 감 (지도 2cm/s x 4배속 = 드론 시간 8cm/s)
        cam = asyncio.create_task(camera(c, b, car))
        try:
            await asyncio.sleep(0.1)
            assert (await c.handle_cmd(cmd("1", "follow_start")))["ok"]
            await asyncio.sleep(3.0)
            tx, ty = car.behind(s.follow.distance_cm)
            # 움직이는 차: 비례 제어라 조금 뒤처지지만 차 뒤쪽 근처에 붙어 있어야 한다
            assert math.hypot(b.truth.x_cm - tx, b.truth.y_cm - ty) < 6.0
            assert b.truth.x_cm < car.x                   # 차 '뒤'(왼쪽)에 있다
        finally:
            cam.cancel()
            await c.shutdown()
    run(go())


def test_follow_lost_car_coasts_then_hovers(settings):
    async def go():
        s = follow_settings(settings, lost_s=0.3, coast_s=0.8)
        b, c, ev = await setup(s)
        car = FakeCar(150, 160, yaw=0)                  # 멀리 → 계속 움직이는 중
        cam = asyncio.create_task(camera(c, b, car))
        try:
            await asyncio.sleep(0.1)
            assert (await c.handle_cmd(cmd("1", "follow_start")))["ok"]
            await asyncio.sleep(0.3)
            assert b._vel[0] > 0
            car.visible = False
            await asyncio.sleep(0.5)                    # lost_s 지남 → 절반 속도로 천천히
            # 마지막으로 본 차 위치 쪽(앞)으로, 속도 상한의 coast_scale배 이하
            assert c.mode == "following" and b._vel[0] > 0
            assert math.hypot(b._vel[0], b._vel[1]) <= s.follow.max_speed_cm_s * s.follow.coast_scale + 1e-6
            await asyncio.sleep(0.6)                    # coast_s 지남 → 멈춤
            assert c.mode == "hovering" and not c.busy
            assert b._vel == (0.0, 0.0, 0.0, 0.0)
            assert "UGV_LOST" in codes(ev)
        finally:
            cam.cancel()
            await c.shutdown()
    run(go())


def test_lost_car_seeks_last_seen_position(settings):
    """차가 옆(왼쪽)으로 빠져나가 안 보이면, 드론은 하던 대로 앞으로 가지 않고 마지막으로 본 차 쪽(왼쪽)으로 간다."""
    async def go():
        s = follow_settings(settings, lost_s=0.2, coast_s=3.0)
        b, c, ev = await setup(s)
        car = FakeCar(153.2, 120, yaw=0)               # 앞 50cm
        cam = asyncio.create_task(camera(c, b, car))
        try:
            await asyncio.sleep(0.1)
            assert (await c.handle_cmd(cmd("1", "follow_start")))["ok"]
            await asyncio.sleep(0.1)
            car.x -= 30                                 # 왼쪽으로 30cm 훌쩍 이동한 순간 한 번 보이고
            await asyncio.sleep(0.06)
            car.visible = False                         # 화면 밖으로 사라짐
            await asyncio.sleep(0.6)
            assert c.mode == "following" and b._vel[1] < 0   # 왼쪽(-오른쪽)으로 찾아간다
            x0 = b.truth.x_cm
            await asyncio.sleep(0.5)
            assert b.truth.x_cm < x0
        finally:
            cam.cancel()
            await c.shutdown()
    run(go())


def test_stop_and_land_interrupt_follow(settings):
    async def go():
        s = follow_settings(settings)
        b, c, ev = await setup(s)
        car = FakeCar(150, 160, yaw=0)
        cam = asyncio.create_task(camera(c, b, car))
        try:
            await asyncio.sleep(0.1)
            assert (await c.handle_cmd(cmd("1", "follow_start")))["ok"]
            await asyncio.sleep(0.3)
            assert b._vel[0] > 0
            assert (await c.handle_cmd(cmd("2", "stop")))["ok"]
            await asyncio.sleep(0.05)
            assert c.mode == "hovering" and b._vel == (0.0, 0.0, 0.0, 0.0)
            y = b.truth.y_cm
            await asyncio.sleep(0.3)
            assert b.truth.y_cm == pytest.approx(y)      # 멈춰 있다
            assert (await c.handle_cmd(cmd("3", "follow_start")))["ok"]
            await asyncio.sleep(0.2)
            assert (await c.handle_cmd(cmd("4", "land")))["ok"]
            for _ in range(100):
                if c.mode == "idle":
                    break
                await asyncio.sleep(0.05)
            assert c.mode == "idle" and not b.flying
        finally:
            cam.cancel()
            await c.shutdown()
    run(go())


def test_follow_stops_at_flight_radius(settings):
    async def go():
        s = follow_settings(replace(settings, limits=replace(settings.limits, max_radius_cm=40)))
        b, c, ev = await setup(s)
        car = FakeCar(153.2, 160, yaw=0)                # 이륙 지점에서 90cm (반경 40cm 밖)
        cam = asyncio.create_task(camera(c, b, car))
        try:
            await asyncio.sleep(0.1)
            assert (await c.handle_cmd(cmd("1", "follow_start")))["ok"]
            await asyncio.sleep(2.0)
            d = math.hypot(b.truth.x_cm - s.home_x_cm, b.truth.y_cm - s.home_y_cm)
            assert d <= 40 + 1.0
            assert "FOLLOW_EDGE" in codes(ev) and c.mode == "following"
        finally:
            cam.cancel()
            await c.shutdown()
    run(go())


def test_app_link_lost_lands_while_following(settings):
    async def go():
        s = follow_settings(replace(settings, limits=replace(settings.limits, link_timeout_s=0.3)))
        b, c, ev = await setup(s)
        car = FakeCar(150, 160, yaw=0)
        cam = asyncio.create_task(camera(c, b, car))
        try:
            await asyncio.sleep(0.1)
            assert (await c.handle_cmd(cmd("1", "follow_start")))["ok"]
            await asyncio.sleep(0.5)                    # 앱 ping 없음
            await c.watchdog_once()
            assert "LINK_LOST_LANDING" in codes(ev) and c.mode == "landing"
            assert b._vel == (0.0, 0.0, 0.0, 0.0)
        finally:
            cam.cancel()
            await c.shutdown()
    run(go())


# ================================================================ HTTP 통로

def test_vision_ugv_rel_endpoint(settings):
    app = create_app(settings, MockBackend(settings))
    with TestClient(app) as client:
        r = client.post("/vision/ugv_rel", json={"forward_m": 0.2, "right_m": -0.05})
        assert r.json()["error"] == "BAD_REQUEST"
        r = client.post("/vision/ugv_rel", json={"forward_m": 5.0, "right_m": 0, "rel_yaw_deg": 0})
        assert r.json()["error"] == "OUT_OF_BOUNDS"
        r = client.post("/vision/ugv_rel", json={"forward_m": 0.2, "right_m": -0.05, "rel_yaw_deg": 30.0,
                                                 "ts": 1.0, "mode": "tt_down"})
        assert r.json() == {"ok": True}
        ctrl = app.state.controller
        assert ctrl.ugv_rel == pytest.approx((20.0, -5.0, 30.0)) and ctrl.ugv_rel_age() < 1.0


def test_follow_start_over_websocket(settings):
    """앱 명령 흐름: 착지 상태에서 follow_start → NOT_FLYING ack (명세 4.1)."""
    with TestClient(create_app(settings, MockBackend(settings))) as client, client.websocket_connect("/ws") as ws:
        for _ in range(50):
            if ws.receive_json()["type"] == "state":
                break
        ws.send_json(cmd("c1", "follow_start"))
        for _ in range(50):
            m = ws.receive_json()
            if m["type"] == "ack":
                break
        assert m["id"] == "c1" and m["ok"] is False and m["error"] == "NOT_FLYING"
