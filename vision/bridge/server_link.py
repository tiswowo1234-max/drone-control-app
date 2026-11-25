"""중계 서버와 주고받기.

- HttpSender : POST /vision/pose(명세 7장), /vision/incident(9장). 별도 스레드 — 서버가 느리거나 꺼져도 처리가 멈추지 않는다.
               위치는 '보낼 것 중 가장 최근 하나'만 남기고, 사건은 순서대로 보낸다.
- VideoLink  : ws /vision/video (8장). 끊기면 다시 연결. 서버의 vision_mode를 받아 콜백, bridge_status를 보낸다.
- dry_run    : 아무것도 보내지 않고 보낼 내용을 출력한다.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from urllib.parse import urlsplit

log = logging.getLogger("bridge.server")


def ws_url(server_url: str, path: str) -> str:
    u = urlsplit(server_url)
    scheme = "wss" if u.scheme == "https" else "ws"
    return f"{scheme}://{u.netloc}{path}"


def short(payload: dict) -> str:
    """출력용: 사진 base64는 길이만."""
    d = dict(payload)
    if "photo_jpeg_b64" in d:
        d["photo_jpeg_b64"] = f"<JPEG base64 {len(d['photo_jpeg_b64'])}자>"
    return json.dumps(d, ensure_ascii=False)


class RateLimitedWarn:
    def __init__(self, every_s: float = 10.0):
        self.every, self.last = every_s, {}

    def __call__(self, key: str, msg: str, *args):
        now = time.monotonic()
        if now - self.last.get(key, -1e9) >= self.every:
            self.last[key] = now
            log.warning(msg, *args)


class HttpSender:
    def __init__(self, server_url: str, dry_run: bool = False, timeout: float = 2.0, printer=print):
        self.url, self.dry, self.timeout, self.print = server_url, dry_run, timeout, printer
        self._pose = None
        self._ugv = None   # (v0.8) 드론이 인식한 UGV 지도 위치 — 위치처럼 최신 값 하나만
        self._ugv_rel = None   # (v0.8) 드론 기준 UGV 상대 위치 (따라가기) — 최신 값 하나만
        self._incidents: queue.Queue = queue.Queue(maxsize=20)
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self.warn = RateLimitedWarn()
        self.stats = {"pose_sent": 0, "pose_ok": 0, "pose_err": 0, "incident_sent": 0, "incident_ok": 0,
                      "ugv_sent": 0, "ugv_ok": 0, "ugv_rel_sent": 0, "ugv_rel_ok": 0}
        self.last_pose_reply = None
        self.last_incident_reply = None
        self._session = None
        self._thread = threading.Thread(target=self._run, name="http-sender", daemon=True)

    def start(self):
        if not self.dry:
            import requests
            self._session = requests.Session()
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread.is_alive():
            self._thread.join(3)

    def send_pose(self, payload: dict):
        with self._cond:
            self._pose = payload
            self._cond.notify_all()

    def send_ugv(self, payload: dict):
        with self._cond:
            self._ugv = payload
            self._cond.notify_all()

    def send_ugv_rel(self, payload: dict):
        with self._cond:
            self._ugv_rel = payload
            self._cond.notify_all()

    def send_incident(self, payload: dict):
        try:
            self._incidents.put_nowait(payload)
        except queue.Full:
            self.warn("incq", "사건 보고가 밀려 하나를 버립니다 (서버 응답이 느림)")
        with self._cond:
            self._cond.notify_all()

    def _run(self):
        while not self._stop.is_set():
            with self._cond:
                self._cond.wait_for(lambda: self._stop.is_set() or self._pose is not None
                                    or self._ugv is not None or self._ugv_rel is not None
                                    or not self._incidents.empty(), 0.5)
                pose, self._pose = self._pose, None
                ugv, self._ugv = self._ugv, None
                ugv_rel, self._ugv_rel = self._ugv_rel, None
            if pose is not None:
                self._post("/vision/pose", pose, "pose")
            if ugv_rel is not None:   # 따라가기는 지연이 짧을수록 좋아 먼저 보낸다
                self._post("/vision/ugv_rel", ugv_rel, "ugv_rel")
            if ugv is not None:
                self._post("/vision/ugv", ugv, "ugv")
            while not self._incidents.empty():
                try:
                    inc = self._incidents.get_nowait()
                except queue.Empty:
                    break
                self._post("/vision/incident", inc, "incident")

    def _post(self, path: str, payload: dict, kind: str):
        self.stats[f"{kind}_sent"] += 1
        if self.dry:
            if kind in ("incident", "ugv"):
                self.print(f"[dry-run] POST {path} {short(payload)}")
            elif kind == "pose":
                self.last_pose_reply = {"dry_run": True}
            return
        try:
            r = self._session.post(self.url + path, json=payload, timeout=self.timeout)
            try:
                body = r.json()
            except ValueError:
                body = {"ok": False, "status": r.status_code, "text": r.text[:200]}
        except Exception as e:
            self.warn(f"conn{path}", "서버(%s)에 보낼 수 없습니다: %s — 서버가 켜져 있는지 확인 (계속 시도합니다)",
                      self.url, type(e).__name__)
            if kind == "pose":
                self.stats["pose_err"] += 1
            return
        if kind == "pose":
            self.last_pose_reply = body
        elif kind != "ugv_rel":
            self.last_incident_reply = body
        if r.status_code == 404:
            self.warn(f"404{path}", "서버에 %s 통로가 아직 없습니다 (HTTP 404) — 서버 업데이트 필요", path)
        elif isinstance(body, dict) and body.get("ok"):
            self.stats[f"{kind}_ok"] += 1
            if kind == "incident":
                log.info("사건 보고 접수: %s", body)
        else:
            if kind == "pose":
                self.stats["pose_err"] += 1
            self.warn(f"rej{path}{body.get('error') if isinstance(body, dict) else ''}",
                      "서버가 %s 거부: %s", path, body)


class VideoLink:
    """ws /vision/video. on_mode(mode) 콜백. send_jpeg(bytes), send_status(dict)."""

    def __init__(self, server_url: str, on_mode, status_fn, dry_run: bool = False, retry_s: float = 2.0,
                 on_drone_state=None):
        self.url = ws_url(server_url, "/vision/video")
        self.on_mode, self.status_fn, self.dry, self.retry_s = on_mode, status_fn, dry_run, retry_s
        self.on_drone_state = on_drone_state  # 서버가 0.1초마다 보내는 드론 자세·높이 (8장 drone_state)
        self._ws = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.connected = threading.Event()
        self.warn = RateLimitedWarn()
        self.sent_frames = 0
        self.sent_bytes = 0
        self._thread = threading.Thread(target=self._run, name="video-ws", daemon=True)

    def start(self):
        if not self.dry:
            self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._thread.is_alive():
            self._thread.join(3)

    def _run(self):
        import websocket  # websocket-client
        while not self._stop.is_set():
            try:
                ws = websocket.create_connection(self.url, timeout=3)
            except Exception as e:
                msg = getattr(e, "status_code", None)
                if msg in (403, 404):
                    self.warn("ws404", "서버에 ws /vision/video 통로가 아직 없습니다 (HTTP %s) — 영상·모드 전환 없이 계속", msg)
                else:
                    self.warn("wsconn", "영상 통로(%s)에 연결할 수 없습니다: %s (계속 시도합니다)", self.url, type(e).__name__)
                self._stop.wait(self.retry_s)
                continue
            log.info("서버 영상 통로 연결됨: %s", self.url)
            ws.settimeout(0.5)
            with self._lock:
                self._ws = ws
            self.connected.set()
            self.send_status(self.status_fn())
            try:
                while not self._stop.is_set():
                    try:
                        op, data = ws.recv_data()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if op == websocket.ABNF.OPCODE_CLOSE:
                        break
                    if op == websocket.ABNF.OPCODE_TEXT:
                        self._on_text(data.decode("utf-8", "replace"))
            except Exception as e:
                if not self._stop.is_set():
                    self.warn("wsdrop", "영상 통로가 끊겼습니다 (%s) — 다시 연결합니다", type(e).__name__)
            finally:
                self.connected.clear()
                with self._lock:
                    self._ws = None
                try:
                    ws.close()
                except Exception:
                    pass
            self._stop.wait(self.retry_s)

    def _on_text(self, text: str):
        try:
            msg = json.loads(text)
        except ValueError:
            log.warning("서버가 보낸 텍스트가 JSON이 아닙니다: %r", text[:100])
            return
        if isinstance(msg, dict) and msg.get("type") == "vision_mode":
            self.on_mode(msg.get("mode"))
        elif isinstance(msg, dict) and msg.get("type") == "drone_state":
            if self.on_drone_state is not None:
                self.on_drone_state(msg)
        else:
            log.debug("서버 메시지 무시: %s", msg)

    def _send(self, data, binary: bool) -> bool:
        import websocket
        with self._lock:
            ws = self._ws
            if ws is None:
                return False
            try:
                ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY if binary else websocket.ABNF.OPCODE_TEXT)
                return True
            except Exception as e:
                self.warn("wssend", "영상 통로로 보내기 실패: %s", type(e).__name__)
                return False

    def send_jpeg(self, jpeg: bytes) -> bool:
        ok = self._send(jpeg, True)
        if ok:
            self.sent_frames += 1
            self.sent_bytes += len(jpeg)
        return ok

    def send_status(self, status: dict) -> bool:
        if self.dry:
            print(f"[dry-run] ws /vision/video 텍스트 {json.dumps(status, ensure_ascii=False)}")
            return True
        return self._send(json.dumps(status, ensure_ascii=False), False)


def server_backend(server_url: str, timeout: float = 1.0) -> str | None:
    """GET / → backend 이름 (mock/sim/real). 서버가 없으면 None."""
    try:
        import requests
        d = requests.get(server_url + "/", timeout=timeout).json()
        return d.get("backend")
    except Exception:
        return None
