"""연결 프로그램 본체: 카메라 → 위치 계산 → 서버 (위치 · 사건 사진 · 실시간 영상 · 모드 전환).

스레드
- 입력(카메라)        : 최신 한 장만 보관 (sources.py)
- 영상 송신           : send_fps마다 최신 장을 JPEG로 → ws /vision/video (위치 계산이 느려도 멈추지 않음)
- HTTP 송신           : 위치·사건 POST (server_link.py)
- 처리(이 파일 main) : 최신 장만 골라 위치 계산 → 위치 POST(초당 최대 pose_send_hz) → 슬롯 촬영 판단(photo_search_period_s마다)
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import signal
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import config as cfgmod
from . import teammate
from .localizers import make_localizer
from .server_link import HttpSender, VideoLink, server_backend, short
from .slots import make_capture
from .sources import FileSource, LocalTour, MjpegSource, ServerTruth, SyntheticSource, UdpH264Source

log = logging.getLogger("bridge")
MODE_LABEL = {"esp32": "ESP32 카메라", "tt_down": "TT 하단 카메라"}


def encode_jpeg(img: np.ndarray, quality: int, max_kb: float | None = None, min_quality: int = 30) -> bytes:
    q = int(quality)
    while True:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
        if not ok:
            raise ValueError("JPEG 인코딩 실패")
        data = buf.tobytes()
        if max_kb is None or len(data) <= max_kb * 1024 or q <= min_quality:
            return data
        q = max(min_quality, q - 10)


_ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def video_frame(img: np.ndarray, width: int, mount_yaw_deg: float = 0.0, forward_up: bool = False) -> np.ndarray:
    """앱으로 보낼 화면. forward_up이면 드론 앞쪽이 화면 위로 오게 돌린다(위치 계산용 화면은 돌리지 않는다).

    드론 앞쪽 = 카메라 화면 위쪽 + mount_yaw_deg(시계 +) → 화면을 시계 방향으로 -mount_yaw_deg 돌리면 앞쪽이 위.
    """
    if forward_up:
        cw = round(-mount_yaw_deg) % 360
        if cw in _ROTATE:
            img = cv2.rotate(img, _ROTATE[cw])
    h, w = img.shape[:2]
    if max(w, h) > width:  # 세로로 돌린 화면도 긴 변을 width 이하로
        k = width / max(w, h)
        img = cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    return img


class Bridge:
    def __init__(self, s: cfgmod.Settings, args):
        self.s, self.args = s, args
        self.dry = bool(args.dry_run)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.mode = args.mode or s.default_mode
        self.requested_mode: str | None = None
        self.synthetic = self._decide_synthetic()
        self.scene = None
        self.sources: dict[str, object] = {}
        self.source = None
        self.localizers: dict[str, object] = {}
        self.localizer = None
        self.capture = make_capture(s)
        self.geo = None
        self.sender = HttpSender(s.server_url, self.dry)
        self.video = VideoLink(s.server_url, self.request_mode, self.status_dict, self.dry)
        self.last_status = None
        self.last_pose_post = -1e9
        self.last_photo_check = -1e9
        self.last_print = -1e9
        self.last_slot_print = -1e9
        self.counts = {"frames": 0, "poses": 0, "incidents": 0}
        self.last_result = None
        self.record_dir = Path(args.record_dir) if args.record_dir else None
        self.last_record = -1e9
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
        self.save_dir = Path(args.save_dir) if args.save_dir else None
        if self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 설정 판단

    def _decide_synthetic(self) -> bool:
        want = self.args.synthetic or self.s.synthetic_camera
        if self.args.input:
            return False
        if want == "on":
            return True
        if want == "off":
            return False
        if self.args.truth or self.args.tour:
            return True
        backend = server_backend(self.s.server_url)
        if backend in ("mock", "sim"):
            log.info("서버 backend = %s → 합성 카메라 사용 (명세 12장)", backend)
            return True
        if backend is None:
            log.warning("서버(%s)에 연결할 수 없어 실제 카메라를 씁니다 (합성 카메라는 --synthetic on --tour로 시험)",
                        self.s.server_url)
        return False

    def _truth(self):
        a = self.args
        if a.truth:
            x, y = a.truth[0], a.truth[1]
            z = a.truth[2] if len(a.truth) > 2 else self.s.default_alt_m
            yaw = a.truth[3] if len(a.truth) > 3 else 0.0
            return LocalTour([(x, y)], z, 0.0, 0.0, yaw)
        if a.tour:
            pts = [self.s.home_m] + [sl.center_m for sl in self.s.slots]
            return LocalTour(pts, self.s.default_alt_m, a.tour_speed, a.tour_hover)
        return ServerTruth(self.s.server_url)

    # ------------------------------------------------------------ 모드

    def _make_source(self, mode: str):
        s, a = self.s, self.args
        if a.input:
            return FileSource(Path(a.input), a.input_fps or s.send_fps, loop=not a.input_once, gray=(mode == "tt_down"))
        if self.synthetic:
            from .synthetic import MapScene, SyntheticCamera
            if self.scene is None:
                self.scene = MapScene(s.map_w_m, a.synthetic_map)
            w, h = s.frame_size(mode)
            if mode == "esp32":
                from localization.camera_simulator import CameraConfig
                hfov = s.esp32_hfov_deg or CameraConfig().hfov_deg
            else:
                hfov = s.tt_hfov_deg
            cam = SyntheticCamera(self.scene, w, h, hfov, color=(mode == "esp32"), seed=a.seed)
            fps = max(s.send_fps, s.pose_send_hz)
            truth = self._truth()
            label = f"({mode}, 참값: {'서버 /sim/truth' if isinstance(truth, ServerTruth) else '내장 경로'})"
            return SyntheticSource(cam, truth, fps, label, s.mount_yaw(mode))
        if mode == "esp32":
            return MjpegSource(a.esp32_url or s.esp32_camera_url)
        return UdpH264Source(s.tt_video_url, (s.tt_width_px, s.tt_height_px), s.tt_crop_top_left)

    def request_mode(self, mode) -> None:
        """서버 vision_mode (ws 스레드에서 호출) → 처리 스레드가 다음 장 경계에서 바꾼다."""
        if mode not in cfgmod.MODES:
            log.warning("서버가 알 수 없는 모드를 보냈습니다: %r", mode)
            st = self.status_dict()
            st["message"] = f"알 수 없는 모드 {mode!r} — {st['message']}"
            self.video.send_status(st)
            return
        with self.lock:
            self.requested_mode = mode

    def switch_mode(self, mode: str, first: bool = False) -> None:
        """입력 카메라와 위치 계산을 통째로 바꾼다 (이전 모드의 추적 상태는 버림)."""
        with self.lock:
            if self.requested_mode == mode:
                self.requested_mode = None
        if self.source is not None:
            self.source.stop()
        self.mode = mode
        if mode not in self.localizers:
            log.info("[%s] 위치 계산 준비 중…", mode)
            self.localizers[mode] = make_localizer(mode, self.s, self.args.tt_upscale, self.synthetic)
        self.localizer = self.localizers[mode]
        self.localizer.reset()
        self.capture.reset()
        self.last_result = None
        self.source = self._make_source(mode)
        self.source.start()
        log.info("%s 모드 %s: %s", "시작" if first else "전환 →", mode, MODE_LABEL[mode])
        self.push_status(force=True)

    def status_dict(self) -> dict:
        if self.source is None:
            return {"type": "bridge_status", "mode": self.mode, "camera_ok": False, "message": "준비 중"}
        ok, msg = self.source.status()
        return {"type": "bridge_status", "mode": self.mode, "camera_ok": bool(ok), "message": msg}

    def push_status(self, force: bool = False) -> None:
        st = self.status_dict()
        key = (st["mode"], st["camera_ok"], st["message"])
        if force or key != self.last_status:
            self.last_status = key
            self.video.send_status(st)

    # ------------------------------------------------------------ 영상

    def _video_loop(self):
        period = 1.0 / self.s.send_fps
        last_id, nxt = 0, time.monotonic()
        stat_t, n, nbytes, size = time.monotonic(), 0, 0, None
        while not self.stop_event.is_set():
            src = self.source
            f = src.frames.latest() if src is not None else None
            if f is not None and f.id != last_id:
                last_id = f.id
                img = video_frame(f.image, self.s.send_width_px, self.s.mount_yaw(self.mode), self.s.video_forward_up)
                jpg = encode_jpeg(img, self.s.jpeg_quality, self.s.max_frame_kb)
                size = (img.shape[1], img.shape[0], "흑백" if img.ndim == 2 else "컬러")
                if self.dry:
                    self.video.sent_frames += 1
                if self.dry or self.video.send_jpeg(jpg):
                    n += 1
                    nbytes += len(jpg)
            now = time.monotonic()
            if self.dry and now - stat_t >= self.args.print_every and n:
                print(f"[dry-run] ws /vision/video 바이너리 JPEG {size[0]}x{size[1]} {size[2]}, "
                      f"평균 {nbytes / n / 1024:.1f}KB × {n / (now - stat_t):.1f}장/초", flush=True)
                stat_t, n, nbytes = now, 0, 0
            elif not self.dry and now - stat_t >= 10:
                stat_t, n, nbytes = now, 0, 0
            nxt += period
            d = nxt - time.monotonic()
            if d < -period:
                nxt = time.monotonic()
            self.stop_event.wait(max(0.0, d))

    # ------------------------------------------------------------ 처리

    def _geo(self):
        if self.geo is None:
            from geo import MapGeo  # 조원 geo.py (지도 m → 위도·경도)
            self.geo = MapGeo(cfgmod.PROJECT_DIR / "KAIST_map.osm", self.s.map_w_m)
        return self.geo

    def drone_yaw(self, res) -> float:
        """위치 계산이 주는 yaw는 카메라 화면 위쪽 방향 → 장착 각도를 더해 드론 앞쪽 방향으로 바꾼다."""
        return (res.yaw_deg + self.s.mount_yaw(self.mode)) % 360

    def pose_payload(self, res, ts: float) -> dict:
        p = {"x_m": round(res.x_m, 4), "y_m": round(res.y_m, 4), "yaw_deg": round(self.drone_yaw(res), 1),
             "confidence": round(res.confidence, 3), "ts": round(ts, 3), "mode": self.mode}
        if res.z_m is not None:
            p["z_m"] = round(res.z_m, 3)
        return p

    def incident_payload(self, cap, res, ts: float) -> dict:
        lat, lon = self._geo().map_to_latlon(*cap.center_m)
        drone = {"x_m": round(res.x_m, 4), "y_m": round(res.y_m, 4)}
        if res.z_m is not None:
            drone["z_m"] = round(res.z_m, 4)
        jpg = encode_jpeg(cap.crop, self.s.jpeg_quality, self.s.max_photo_kb)
        return {"ts": round(ts, 3), "mode": self.mode, "method": cap.method, "slot": cap.slot, "place": cap.place,
                "x_m": round(cap.center_m[0], 4), "y_m": round(cap.center_m[1], 4),
                "lat": round(lat, 7), "lon": round(lon, 7), "drone": drone,
                "photo_jpeg_b64": base64.b64encode(jpg).decode("ascii"), "detection": None}

    def record(self, f) -> None:
        """--record-dir: 받은 원본 화면을 record_every초마다 PNG로 저장 (카메라 보정·오프라인 시험용)."""
        if self.record_dir is None or f.mono - self.last_record < self.args.record_every:
            return
        self.last_record = f.mono
        h, w = f.image.shape[:2]
        path = self.record_dir / f"{self.mode}_{w}x{h}_{time.strftime('%Y%m%d_%H%M%S')}_{f.id:05d}.png"
        ok, buf = cv2.imencode(".png", f.image)
        if ok:
            path.write_bytes(buf.tobytes())
            log.info("저장: %s", path.name)

    def process_frame(self, f) -> None:
        self.record(f)
        res = self.localizer.process(f.image, f.mono)
        self.last_result = res
        self.counts["frames"] += 1
        now = time.monotonic()
        self.capture.on_pose(f.mono, res.x_m if res.ok else None, res.y_m if res.ok else None)
        if res.ok and now - self.last_pose_post >= 1.0 / self.s.pose_send_hz:
            self.last_pose_post = now
            payload = self.pose_payload(res, f.ts)
            self.sender.send_pose(payload)
            self.counts["poses"] += 1
            if self.dry and now - self.last_print >= self.args.print_every:
                self.last_print = now
                truth = ""
                if f.truth is not None:
                    tx, ty, tz, tyaw = f.truth
                    err = np.hypot(res.x_m - tx, res.y_m - ty) * 100
                    dyaw = (self.drone_yaw(res) - tyaw + 180) % 360 - 180
                    truth = f"  | 참값 ({tx:.3f}, {ty:.3f}) yaw {tyaw:.0f} → 오차 {err:.1f}cm, yaw {dyaw:+.1f}°"
                print(f"[dry-run] POST /vision/pose {json.dumps(payload)}  ({res.status}, {res.ms:.0f}ms){truth}",
                      flush=True)
        if now - self.last_photo_check >= self.s.photo_search_period_s:
            self.last_photo_check = now
            # 이 장에서 실제로 측정한 자세만 (예측값·추적기가 튄 값으로 자르면 사진이 어긋난다 — 시험 결과)
            if res.ok and res.measured and self.capture.method == "slot" and (self.dry or self.args.verbose)                     and now - self.last_slot_print >= self.args.print_every:
                seen = [d for d in self.capture.evaluate(res.view, f.mono) if d["reason"] not in ("화면 밖", "카메라 뒤")]
                if seen:
                    self.last_slot_print = now
                    print("[슬롯] " + ", ".join(f"S{d['slot']} {'조건 충족' if d['ok'] else d['reason']}" for d in seen),
                          flush=True)
            if res.ok and res.measured:
                if self.capture.method == "photo_finder":
                    caps = self.capture.check(f.image, res.view, f.mono, res.tags, res.quads)
                else:
                    caps = self.capture.check(f.image, res.view, f.mono)
                for cap in caps:
                    payload = self.incident_payload(cap, res, f.ts)
                    self.counts["incidents"] += 1
                    log.info("사건 사진: 슬롯 %s (%s), 사진 %dx%d %s", cap.slot, cap.place, cap.crop.shape[1],
                             cap.crop.shape[0], cap.info)
                    if self.save_dir:
                        name = f"{time.strftime('%H%M%S')}_{self.mode}_S{cap.slot}.jpg"
                        (self.save_dir / name).write_bytes(base64.b64decode(payload["photo_jpeg_b64"]))
                        log.info("  저장: %s", self.save_dir / name)
                    self.sender.send_incident(payload)

    def _print_status(self):
        ok, msg = self.source.status() if self.source else (False, "")
        r = self.last_result
        pose = "위치 없음" if r is None or not r.ok else f"({r.x_m:.3f}, {r.y_m:.3f}) m yaw {r.yaw_deg:.0f}° {r.status}"
        st = self.sender.stats
        reply = self.sender.last_pose_reply
        log.info("[%s] 카메라 %s %s | %s | 위치 보냄 %d (ok %d) 서버응답 %s | 영상 %d장 | 사건 %d",
                 self.mode, "OK" if ok else "없음", msg, pose, st["pose_sent"], st["pose_ok"], reply,
                 self.video.sent_frames, self.counts["incidents"])

    def run(self) -> None:
        self.sender.start()
        self.switch_mode(self.mode, first=True)
        self.video.start()
        threading.Thread(target=self._video_loop, name="video-out", daemon=True).start()
        t_end = time.monotonic() + self.args.seconds if self.args.seconds else None
        last_id, last_status_print = 0, time.monotonic()
        try:
            while not self.stop_event.is_set():
                with self.lock:
                    req, self.requested_mode = self.requested_mode, None
                if req is not None:
                    if req != self.mode:
                        self.switch_mode(req)
                        last_id = 0
                    else:
                        self.push_status(force=True)
                f = self.source.frames.wait_newer(last_id, 0.3)
                if f is not None:
                    last_id = f.id
                    self.process_frame(f)
                self.push_status()
                now = time.monotonic()
                if not self.dry and now - last_status_print >= self.args.status_every:
                    last_status_print = now
                    self._print_status()
                if t_end is not None and now >= t_end:
                    break
                if isinstance(self.source, FileSource) and self.source.finished.is_set():
                    break
        finally:
            self.close()

    def close(self):
        self.stop_event.set()
        if self.source is not None:
            self.source.stop(1.0)
        self.video.stop()
        self.sender.stop()
        log.info("종료: 처리 %d장, 위치 %d번, 사건 %d건, 영상 %d장", self.counts["frames"], self.counts["poses"],
                 self.counts["incidents"], self.video.sent_frames)


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m bridge",
                                description="카메라 → 조원 위치 인식 → 중계 서버 연결 프로그램 (docs/api.md 7~12장)")
    p.add_argument("--settings", default=str(cfgmod.DEFAULT_SETTINGS), help="설정 파일 (기본 config/settings.toml)")
    p.add_argument("--server", help="서버 주소 (기본 [bridge] server_url), 예: http://127.0.0.1:8000")
    p.add_argument("--mode", choices=cfgmod.MODES, help="처음 모드 (기본 [bridge] default_mode). 서버가 vision_mode를 보내면 그걸 따른다")
    p.add_argument("--esp32-url", help="XIAO 영상 주소 (기본 [bridge] esp32_camera_url), 예: http://192.168.0.50:81/stream")
    p.add_argument("--synthetic", choices=("auto", "on", "off"), help="합성 카메라 (기본 [bridge] synthetic_camera)")
    p.add_argument("--synthetic-map", choices=("photos", "tags"), default="photos",
                   help="합성 카메라 지도 그림: photos = 태그+슬롯 사진(기본), tags = 태그만")
    p.add_argument("--truth", type=float, nargs="+", metavar="V",
                   help="서버 없이 합성 카메라 참값 고정: X Y [Z YAW] (m, 도)")
    p.add_argument("--tour", action="store_true", help="서버 없이 합성 카메라: 이륙 지점 → 슬롯 S1~S4 위 호버링 순회")
    p.add_argument("--tour-speed", type=float, default=0.3, help="--tour 이동 속도 m/s")
    p.add_argument("--tour-hover", type=float, default=4.0, help="--tour 슬롯 위에서 머무는 초")
    p.add_argument("--input", help="오프라인 시험: 영상 파일 또는 사진 폴더")
    p.add_argument("--input-fps", type=float, help="--input 재생 속도 (기본 [video] send_fps)")
    p.add_argument("--input-once", action="store_true", help="--input을 한 번만 재생하고 끝냄")
    p.add_argument("--tt-upscale", type=int, default=1,
                   help="tt_down: 태그 찾기 전 화면 확대 배수 (2 = 작은 태그 인식률↑, 오차는 조금↑. README 참고)")
    p.add_argument("--dry-run", action="store_true", help="서버에 아무것도 보내지 않고 보낼 내용을 출력")
    p.add_argument("--save-dir", help="잘라낸 사건 사진을 이 폴더에도 저장")
    p.add_argument("--record-dir", help="받은 카메라 원본 화면을 이 폴더에 PNG로 저장 (카메라 보정용)")
    p.add_argument("--record-every", type=float, default=1.0, help="--record-dir 저장 간격(초)")
    p.add_argument("--seconds", type=float, help="이 시간 뒤 자동 종료")
    p.add_argument("--seed", type=int, help="합성 카메라 잡음 시드")
    p.add_argument("--print-every", type=float, default=1.0, help="--dry-run 출력 간격(초)")
    p.add_argument("--status-every", type=float, default=5.0, help="상태 한 줄 출력 간격(초)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    s = cfgmod.load(args.settings)
    if args.server:
        import dataclasses
        s = dataclasses.replace(s, server_url=args.server.rstrip("/"))
    if args.truth and len(args.truth) not in (2, 3, 4):
        raise SystemExit("--truth는 X Y [Z YAW]")
    log.info("설정 %s, 서버 %s%s", s.source, s.server_url, " (dry-run: 보내지 않음)" if args.dry_run else "")
    bridge = Bridge(s, args)

    def stop(*_):
        log.info("종료 중… (Ctrl+C)")
        bridge.stop_event.set()
    signal.signal(signal.SIGINT, stop)
    bridge.run()
