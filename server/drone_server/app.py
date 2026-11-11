"""FastAPI WebSocket 서버: ws://0.0.0.0:8000/ws (docs/api.md)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from . import API_VERSION
from .backends.base import DroneBackend
from .controller import Controller, make_ack, make_event
from .settings import Settings

log = logging.getLogger("drone_server.app")

SEND_TIMEOUT_S = 2.0
RECONNECT_PERIOD_S = 2.0


def make_config_message(s: Settings, backend_name: str) -> dict:
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


def create_app(settings: Settings, backend: DroneBackend) -> FastAPI:
    hub = Hub()
    ctrl = Controller(settings, backend, emit=hub.broadcast)
    config_msg = make_config_message(settings, backend.name)

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
                    await hub.broadcast(ctrl.state_message())
                except Exception:  # noqa: BLE001
                    log.exception("state push failed")
            await asyncio.sleep(period)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = [asyncio.create_task(c) for c in (connect_loop(), state_loop(), ctrl.watchdog_loop())]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await ctrl.shutdown()
            await backend.close()

    app = FastAPI(title="drone relay server", lifespan=lifespan)
    app.state.controller = ctrl
    app.state.hub = hub
    app.state.backend = backend

    @app.get("/")
    async def health() -> dict:
        """브라우저로 http://<서버IP>:8000/ 을 열어 서버가 살아 있는지 확인하는 용도."""
        st = ctrl.state_message()
        vision = None
        if backend.vision is not None:
            age = backend.vision.age_s()
            vision = {"enabled": True, "last_age_s": None if age is None else round(age, 2),
                      "stale_s": settings.vision.stale_s}
        return {"ok": True, "api_version": API_VERSION, "backend": backend.name,
                "clients": len(hub.clients), "vision": vision or {"enabled": False}, "state": st}

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
        except ValueError as e:
            return vision_error("BAD_REQUEST", str(e))
        if conf is not None and not 0 <= conf <= 1:
            return vision_error("BAD_REQUEST", "confidence는 0~1이어야 합니다")
        m = settings.vision.margin_m
        w_m, h_m = settings.map_width_cm / 100, settings.map_height_cm / 100
        if not (-m <= x_m <= w_m + m and -m <= y_m <= h_m + m):
            return vision_error("OUT_OF_BOUNDS", f"지도({w_m:g}m x {h_m:g}m, 여유 {m:g}m) 밖 좌표입니다")
        if conf is not None and conf < settings.vision.min_confidence:
            return vision_error("LOW_CONFIDENCE", f"confidence {conf:g} < {settings.vision.min_confidence:g}")
        backend.vision.submit(x_m * 100.0, y_m * 100.0, yaw, ts)  # m → cm
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        hub.clients.append(ws)
        log.info("app connected (%d clients)", len(hub.clients))
        await hub.send(ws, config_msg)
        await hub.send(ws, ctrl.state_message())
        if len(hub.clients) > 1:
            await hub.broadcast(make_event("info", "CONTROLLER_CHANGED", "새로 연결한 앱이 명령 권한을 가집니다"))
        try:
            while True:
                text = await ws.receive_text()
                await handle_message(ws, text)
        except WebSocketDisconnect:
            pass
        finally:
            if ws in hub.clients:
                hub.clients.remove(ws)
            log.info("app disconnected (%d clients)", len(hub.clients))

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
        if kind == "cmd":
            if not is_controller:
                await hub.send(ws, make_ack(msg.get("id"), "BUSY", "다른 앱이 제어 중입니다 (마지막에 연결한 앱만 명령 가능)"))
                return
            ctrl.note_alive()
            await hub.send(ws, await ctrl.handle_cmd(msg))
            return
        if "id" in msg:
            await hub.send(ws, make_ack(msg.get("id"), "BAD_REQUEST", f"알 수 없는 메시지 type: {kind!r}"))
        else:
            await hub.send(ws, make_event("error", "BAD_REQUEST", f"알 수 없는 메시지 type: {kind!r}"))

    return app
