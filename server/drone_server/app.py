"""FastAPI 서버 (docs/api.md v0.8).

  ws   /ws                 앱 ⇄ 서버 (config, incidents, state, ack, event, incident / cmd, ping)
  GET  /                   상태 확인 (브라우저)
  POST /vision/pose        vision/ → 위치 (7장)
  POST /vision/incident    vision/ → 사건 사진 (9장)
  ws   /vision/video       vision/ → JPEG 영상, bridge_status / 서버 → vision_mode (8·11장)
  ws   /ws/video           서버 → 앱 JPEG 영상, video_status (10.2장)
  GET  /incidents/{id}/photo.jpg   사건 사진 (10.1장)
  GET  /sim/truth          mock/sim 참값 위치 (12장, 합성 카메라용. 가짜 차가 있으면 ugv도)
  POST /vision/ugv         vision/ → 드론이 인식한 UGV(차) 지도 위치 (13장)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from . import API_VERSION
from .backends.base import DroneBackend
from .car import CAR_COMMANDS, CarController, CarLink
from .controller import Controller, make_ack, make_event
from .incidents import IncidentError, IncidentStore
from .settings import VISION_MODES, Settings
from .vision_link import VisionLink

log = logging.getLogger("drone_server.app")

SEND_TIMEOUT_S = 2.0
RECONNECT_PERIOD_S = 2.0
SHUTDOWN_STEP_TIMEOUT_S = 1.0


def make_config_message(s: Settings, backend_name: str, car: CarController | None = None) -> dict:
    L = s.limits
    return {
        "type": "config",
        "api_version": API_VERSION,
        "map": {"width_cm": s.map_width_cm, "height_cm": s.map_height_cm},
        "home": {"x_cm": s.home_x_cm, "y_cm": s.home_y_cm},
        "limits": {
            "max_radius_cm": L.max_radius_cm,
            "min_alt_cm": L.min_alt_cm,
            "max_alt_cm": L.max_alt_cm,
            "default_alt_cm": L.default_alt_cm,
            "min_battery_pct": L.min_battery_pct,
            "link_timeout_s": L.link_timeout_s,
        },
        "no_fly_zones": [{"name": z.name, "polygon_cm": [list(p) for p in z.polygon_cm]} for z in s.no_fly_zones],
        "geo": s.geo,
        "backend": backend_name,
        "vision_modes": list(VISION_MODES),
        "photo_slots": [p.to_json() for p in s.photo_slots],
        "car": car.config_json() if car is not None else None,   # (v0.8) 차 제어. 없으면 null
    }


def normalize_numbers(obj: Any) -> Any:
    """250.0 → 250 처럼 정수인 실수는 정수로 보낸다 (앱 쪽 JSON 파싱을 단순하게)."""
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, dict):
        return {k: normalize_numbers(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [normalize_numbers(v) for v in obj]
    return obj


def encode(msg: dict) -> str:
    return json.dumps(normalize_numbers(msg), ensure_ascii=False)


class Hub:
    """연결된 앱 목록. 명령은 마지막에 연결한 앱(controller)만 보낼 수 있다."""

    def __init__(self) -> None:
        self.clients: list[WebSocket] = []

    @property
    def controller(self) -> WebSocket | None:
        return self.clients[-1] if self.clients else None

    async def send(self, ws: WebSocket, msg: dict) -> bool:
        try:
            await asyncio.wait_for(ws.send_text(encode(msg)), SEND_TIMEOUT_S)
            return True
        except Exception:  # noqa: BLE001 — 끊긴 클라이언트는 수신 루프에서 정리
            return False

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self.clients):
            await self.send(ws, msg)


def create_app(settings: Settings, backend: DroneBackend, car_link: CarLink | None = None) -> FastAPI:
    """car_link: 차 연결 (car.make_car_link). None이면 차 기능 없음 (config.car = null)."""
    hub = Hub()
    link = VisionLink(settings, backend, emit=hub.broadcast)
    ctrl = Controller(settings, backend, emit=hub.broadcast, vision_link=link)
    car = CarController(settings, car_link, emit=hub.broadcast) if car_link is not None else None
    incidents = IncidentStore(settings)
    config_msg = make_config_message(settings, backend.name, car)

    def state_message() -> dict:
        msg = ctrl.state_message()
        if car is not None:
            msg["car"] = car.state_json()
        return msg
    bg_tasks: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        """HTTP 응답을 기다리게 하지 않고 뒤에서 앱들에 보낸다."""
        t = asyncio.create_task(coro)
        bg_tasks.add(t)
        t.add_done_callback(bg_tasks.discard)

    async def connect_loop() -> None:
        while True:
            if not backend.telemetry().connected:
                try:
                    await backend.connect()
                    log.info("backend %s connected", backend.name)
                except Exception as e:  # noqa: BLE001
                    log.warning("backend %s connect failed: %s", backend.name, e)
            await asyncio.sleep(RECONNECT_PERIOD_S)

    async def state_loop() -> None:
        period = 1.0 / settings.server.state_hz
        while True:
            if hub.clients:
                try:
                    await hub.broadcast(state_message())
                except Exception:  # noqa: BLE001
                    log.exception("state push failed")
            await asyncio.sleep(period)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = [asyncio.create_task(c) for c in (connect_loop(), state_loop(), ctrl.watchdog_loop())]
        if car is not None:
            car.start()
            tasks.append(asyncio.create_task(car_watchdog_loop()))
        try:
            yield
        finally:
            # 종료가 어떤 이유로든 오래 걸리지 않도록 단계마다 시간 제한을 둔다 (Ctrl+C 후 수 초 안에 종료)
            for t in tasks + list(bg_tasks):
                t.cancel()
            await asyncio.wait(tasks, timeout=SHUTDOWN_STEP_TIMEOUT_S)
            steps = (ctrl.shutdown, link.shutdown, backend.close) + ((car.shutdown,) if car is not None else ())
            for step in steps:
                try:
                    await asyncio.wait_for(step(), SHUTDOWN_STEP_TIMEOUT_S)
                except Exception as e:  # noqa: BLE001 — 시간 초과 포함, 종료는 계속한다
                    log.warning("shutdown step %s: %r", step.__qualname__, e)

    async def car_watchdog_loop(period_s: float = 0.1) -> None:
        """조이스틱 끊김·앱 끊김이면 차를 세운다. 앱이 하나도 없으면 침묵 시간을 무한대로 본다."""
        import time as _time
        while True:
            try:
                silent = _time.monotonic() - ctrl.last_alive if hub.clients else math.inf
                await car.watchdog_once(silent, settings.limits.link_timeout_s)
            except Exception:  # noqa: BLE001
                log.exception("car watchdog error")
            await asyncio.sleep(period_s)

    app = FastAPI(title="drone relay server", lifespan=lifespan)
    app.state.controller = ctrl
    app.state.hub = hub
    app.state.backend = backend
    app.state.vision_link = link
    app.state.incidents = incidents
    app.state.car = car

    @app.get("/")
    async def health() -> dict:
        """브라우저로 http://<서버IP>:8000/ 을 열어 서버가 살아 있는지 확인하는 용도."""
        st = state_message()
        vision = None
        if backend.vision is not None:
            age = backend.vision.age_s()
            vision = {"enabled": True, "last_age_s": None if age is None else round(age, 2),
                      "stale_s": settings.vision.stale_s}
        return {"ok": True, "api_version": API_VERSION, "backend": backend.name,
                "clients": len(hub.clients), "vision": vision or {"enabled": False},
                "vision_link": link.health(),
                "incidents": {"count": len(incidents), "total": incidents.total,
                              "duplicates": incidents.duplicates},
                "state": st}

    def vision_error(code: str, message: str) -> JSONResponse:
        return JSONResponse({"ok": False, "error": code, "message": message}, status_code=400)

    @app.post("/vision/pose")
    async def vision_pose(request: Request):
        """조원 위치 추정 코드 → 서버 (docs/api.md 7장). 본문 m 단위, 지도 왼쪽 아래 원점."""
        if backend.vision is None:
            return vision_error("BAD_REQUEST", "vision 입력이 꺼져 있습니다 (pos_source = \"vision\" 또는 run_server.py --vision)")
        try:
            body = await request.json()
        except ValueError:
            return vision_error("BAD_REQUEST", "JSON 형식이 아닙니다")
        if not isinstance(body, dict):
            return vision_error("BAD_REQUEST", "JSON 객체가 아닙니다")

        def num(key: str, required: bool) -> float | None:
            v = body.get(key)
            if v is None:
                if required:
                    raise ValueError(f"{key}(숫자)가 필요합니다")
                return None
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"{key}는 숫자여야 합니다")
            return float(v)

        try:
            x_m, y_m = num("x_m", True), num("y_m", True)
            yaw, conf, ts = num("yaw_deg", False), num("confidence", False), num("ts", False)
            z_m = num("z_m", False)
        except ValueError as e:
            return vision_error("BAD_REQUEST", str(e))
        mode = body.get("mode")
        if mode is not None:
            if mode not in VISION_MODES:
                return vision_error("BAD_REQUEST", f"mode는 {' | '.join(VISION_MODES)} 중 하나여야 합니다")
            if mode != link.mode:
                return vision_error("WRONG_MODE", f"서버의 위치 추적 모드는 {link.mode}입니다 (보낸 값: {mode})")
        if conf is not None and not 0 <= conf <= 1:
            return vision_error("BAD_REQUEST", "confidence는 0~1이어야 합니다")
        m = settings.vision.margin_m
        w_m, h_m = settings.map_width_cm / 100, settings.map_height_cm / 100
        if not (-m <= x_m <= w_m + m and -m <= y_m <= h_m + m):
            return vision_error("OUT_OF_BOUNDS", f"지도({w_m:g}m x {h_m:g}m, 여유 {m:g}m) 밖 좌표입니다")
        if conf is not None and conf < settings.vision.min_confidence:
            return vision_error("LOW_CONFIDENCE", f"confidence {conf:g} < {settings.vision.min_confidence:g}")
        backend.vision.submit(x_m * 100.0, y_m * 100.0, yaw, ts,
                              None if z_m is None else z_m * 100.0, confidence=conf)  # m → cm
        return {"ok": True}

    @app.post("/vision/incident")
    async def vision_incident(request: Request):
        """vision/ → 사건 사진 보고 (docs/api.md 9장). pos_source와 상관없이 항상 받는다."""
        try:
            body = await request.json()
        except ValueError:
            return vision_error("BAD_REQUEST", "JSON 형식이 아닙니다")
        try:
            inc, duplicate = incidents.add(body)
        except IncidentError as e:
            return vision_error(e.code, e.message)
        if duplicate:
            return {"ok": True, "id": inc.id, "duplicate": True}
        log.info("incident %s slot=%s mode=%s method=%s (%d bytes)", inc.id, inc.slot, inc.mode,
                 inc.data["method"], len(inc.photo))
        where = inc.data["place"] or (f"슬롯 S{inc.slot}" if inc.slot else "위치 미상")

        async def announce() -> None:
            await hub.broadcast({"type": "incident", "incident": inc.data})
            await hub.broadcast(make_event("warn", "INCIDENT_NEW", f"새 사건 사진: {where}"))
        spawn(announce())
        return {"ok": True, "id": inc.id}

    cors = {"Access-Control-Allow-Origin": "*"}

    @app.get("/incidents/{inc_id}/photo.jpg")
    async def incident_photo(inc_id: str):
        """사건 사진 (docs/api.md 10.1). Flutter web(다른 포트)에서도 읽도록 CORS *."""
        data = incidents.photo(inc_id)
        if data is None:
            return JSONResponse({"ok": False, "error": "NOT_FOUND",
                                 "message": "없는 사건입니다 (서버를 다시 켰을 수 있습니다)"},
                                status_code=404, headers=cors)
        return Response(data, media_type="image/jpeg", headers={**cors, "Cache-Control": "max-age=3600"})

    @app.get("/sim/truth")
    async def sim_truth():
        """합성 카메라용 참값 위치 (docs/api.md 12장, mock/sim 전용). 지도 m 단위."""
        if not backend.has_truth:
            return vision_error("BAD_REQUEST", f"{backend.name} 백엔드에는 참값 위치가 없습니다 (mock/sim 전용)")
        p = backend.truth_pose()
        if p is None:
            return vision_error("NOT_CONNECTED", "아직 시뮬레이터 위치를 읽지 못했습니다")
        out = {"ok": True, "x_m": round(p.x_cm / 100, 4), "y_m": round(p.y_cm / 100, 4),
               "z_m": round(p.z_cm / 100, 4), "yaw_deg": round(p.yaw_deg, 2)}
        ct = car.link.truth_map() if car is not None else None
        if ct is not None:   # 가짜 차의 실제 위치 → 합성 카메라가 UGV 태그를 그린다 (v0.8)
            out["ugv"] = {"x_m": round(ct[0] / 100, 4), "y_m": round(ct[1] / 100, 4), "yaw_deg": round(ct[2], 2)}
        return out

    @app.post("/vision/ugv")
    async def vision_ugv(request: Request):
        """vision/ → 드론 하단 카메라가 인식한 UGV 태그의 지도 위치 (docs/api.md 13.3). m 단위."""
        if car is None:
            return vision_error("BAD_REQUEST", "차 기능이 꺼져 있습니다 ([car] mode = \"off\")")
        try:
            body = await request.json()
        except ValueError:
            return vision_error("BAD_REQUEST", "JSON 형식이 아닙니다")
        if not isinstance(body, dict):
            return vision_error("BAD_REQUEST", "JSON 객체가 아닙니다")
        vals = {}
        for k in ("x_m", "y_m", "yaw_deg"):
            v = body.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                return vision_error("BAD_REQUEST", f"{k}(숫자)가 필요합니다")
            vals[k] = float(v)
        m = settings.vision.margin_m
        w_m, h_m = settings.map_width_cm / 100, settings.map_height_cm / 100
        if not (-m <= vals["x_m"] <= w_m + m and -m <= vals["y_m"] <= h_m + m):
            return vision_error("OUT_OF_BOUNDS", f"지도({w_m:g}m x {h_m:g}m, 여유 {m:g}m) 밖 좌표입니다")
        car.on_vision(vals["x_m"] * 100.0, vals["y_m"] * 100.0, vals["yaw_deg"])
        return {"ok": True}

    @app.websocket("/vision/video")
    async def vision_video(ws: WebSocket) -> None:
        """vision/ 연결 프로그램 → 영상·상태 (docs/api.md 8장). 하나만 연결."""
        await ws.accept()
        try:
            await link.serve_bridge(ws)
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/video")
    async def app_video(ws: WebSocket) -> None:
        """앱 영상 화면 (docs/api.md 10.2). 명령권·생존 신호와 무관."""
        await ws.accept()
        log.info("video viewer connected (%d)", link.video.viewers + 1)
        try:
            await link.video.serve_viewer(ws)
        except WebSocketDisconnect:
            pass
        finally:
            log.info("video viewer disconnected (%d)", link.video.viewers)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(), 1.0)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        hub.clients.append(ws)
        log.info("app connected (%d clients)", len(hub.clients))
        await hub.send(ws, config_msg)
        await hub.send(ws, {"type": "incidents", "items": incidents.items()})
        await hub.send(ws, state_message())
        # 새로 연결한 앱이 명령권을 갖는다. 아직 ping을 못 보냈으니 생존 타이머를 새로 시작한다.
        ctrl.note_alive()
        if len(hub.clients) > 1:
            await hub.broadcast(make_event("info", "CONTROLLER_CHANGED", "새로 연결한 앱이 명령 권한을 가집니다"))
        try:
            while True:
                text = await ws.receive_text()
                await handle_message(ws, text)
        except WebSocketDisconnect:
            pass
        finally:
            was_controller = ws is hub.controller
            if ws in hub.clients:
                hub.clients.remove(ws)
            log.info("app disconnected (%d clients)", len(hub.clients))
            if was_controller and hub.clients:
                # 명령권 넘겨받기 (docs/api.md 1장): 남은 앱 중 가장 최근에 연결된 앱(clients[-1])이 넘겨받는다.
                # 넘겨받는 순간 바로 자동 착륙하지 않도록 새 명령권 앱의 ping 타이머를 지금부터 센다.
                ctrl.note_alive()
                if car is not None:
                    await car.on_controller_change()
                log.info("controller handed over to remaining app (%d clients)", len(hub.clients))
                await hub.broadcast(make_event(
                    "info", "CONTROLLER_CHANGED",
                    "명령 권한을 가진 앱의 연결이 끊겨, 남은 앱(가장 최근 연결)이 명령 권한을 넘겨받았습니다"))

    async def handle_message(ws: WebSocket, text: str) -> None:
        try:
            msg: Any = json.loads(text)
        except ValueError:
            await hub.send(ws, make_event("error", "BAD_REQUEST", "JSON 형식이 아닙니다"))
            return
        if not isinstance(msg, dict):
            await hub.send(ws, make_event("error", "BAD_REQUEST", "JSON 객체가 아닙니다"))
            return
        kind = msg.get("type")
        is_controller = ws is hub.controller
        if kind == "ping":
            if is_controller:
                ctrl.note_alive()
            return
        if kind == "car_drive":   # (v0.8) 조이스틱, 응답 없음. 명령권 앱만
            if is_controller and car is not None:
                ctrl.note_alive()
                await car.drive(msg)
            return
        if kind == "cmd":
            if not is_controller:
                await hub.send(ws, make_ack(msg.get("id"), "BUSY", "다른 앱이 제어 중입니다 (마지막에 연결한 앱만 명령 가능)"))
                return
            ctrl.note_alive()
            if msg.get("cmd") in CAR_COMMANDS:
                if car is None:
                    await hub.send(ws, make_ack(msg.get("id"), "BAD_REQUEST", "이 서버는 차 기능이 꺼져 있습니다"))
                else:
                    await hub.send(ws, await car.handle_cmd(msg))
                return
            await hub.send(ws, await ctrl.handle_cmd(msg))
            return
        if "id" in msg:
            await hub.send(ws, make_ack(msg.get("id"), "BAD_REQUEST", f"알 수 없는 메시지 type: {kind!r}"))
        else:
            await hub.send(ws, make_event("error", "BAD_REQUEST", f"알 수 없는 메시지 type: {kind!r}"))

    return app
