"""카메라 입력. 모든 입력은 '가장 최근 한 장'(LatestFrame)만 남긴다 — 처리가 느려도 밀리지 않는다.

- SyntheticSource : 합성 카메라 (참값 = 서버 GET /sim/truth 또는 내장 순회 경로)
- MjpegSource     : XIAO ESP32 (HTTP MJPEG, 조원 camera_stream.XiaoCameraSource 재사용)
- UdpH264Source   : TT 하단 카메라 (UDP 11111 H.264 → OpenCV/FFmpeg). 흑백으로 바꾸고 크기를 확인한다
- FileSource      : 영상 파일 또는 사진 폴더 (오프라인 시험)
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import teammate  # noqa: F401

log = logging.getLogger("bridge.sources")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp"}


@dataclass
class Frame:
    id: int
    image: np.ndarray  # 흑백 (H,W) 또는 BGR (H,W,3)
    ts: float          # 유닉스 초 (찍은/받은 시각)
    mono: float        # time.monotonic() 기준 같은 시각
    truth: tuple | None = None  # 합성 카메라: (x, y, z, yaw) 참값 (시험·출력용)


class LatestFrame:
    def __init__(self):
        self._cond = threading.Condition()
        self._frame: Frame | None = None
        self._next_id = 1

    def publish(self, image: np.ndarray, ts: float | None = None, truth=None) -> None:
        with self._cond:
            self._frame = Frame(self._next_id, image, ts if ts is not None else time.time(), time.monotonic(), truth)
            self._next_id += 1
            self._cond.notify_all()

    def latest(self) -> Frame | None:
        with self._cond:
            return self._frame

    def wait_newer(self, after_id: int, timeout: float) -> Frame | None:
        with self._cond:
            self._cond.wait_for(lambda: self._frame is not None and self._frame.id > after_id, timeout)
            f = self._frame
            return f if f is not None and f.id > after_id else None


class Source:
    """공통: start/stop, status() → (camera_ok, 메시지)."""
    kind = "source"

    def __init__(self):
        self.frames = LatestFrame()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = (False, "시작 전")
        self._lock = threading.Lock()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_safe, name=f"src-{self.kind}", daemon=True)
        self._thread.start()

    def stop(self, wait: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(wait)

    def status(self) -> tuple[bool, str]:
        with self._lock:
            ok, msg = self._status
        f = self.frames.latest()
        if ok and (f is None or time.monotonic() - f.mono > 2.0):
            return False, msg + " (2초 넘게 새 화면 없음)"
        return ok, msg

    def _set(self, ok: bool, msg: str) -> None:
        with self._lock:
            changed = self._status != (ok, msg)
            self._status = (ok, msg)
        if changed:
            (log.info if ok else log.warning)("[%s] %s", self.kind, msg)

    def _run_safe(self):
        try:
            self._run()
        except Exception as e:  # 입력 하나가 죽어도 프로그램 전체는 살아 있게
            log.exception("[%s] 입력 오류", self.kind)
            self._set(False, f"입력 오류: {e}")

    def _run(self):
        raise NotImplementedError


# ---------------------------------------------------------------- 합성 카메라

class ServerTruth:
    """GET /sim/truth (명세 12장)."""

    def __init__(self, server_url: str, timeout: float = 0.5):
        import requests
        self.session = requests.Session()
        self.url = server_url + "/sim/truth"
        self.timeout = timeout
        self.error = ""

    def __call__(self, t: float):
        try:
            r = self.session.get(self.url, timeout=self.timeout)
            d = r.json()
        except Exception as e:
            self.error = f"서버 /sim/truth에 연결할 수 없습니다 ({type(e).__name__})"
            return None
        if not d.get("ok"):
            self.error = f"/sim/truth 거부: {d.get('error')} {d.get('message', '')}".strip()
            return None
        self.error = ""
        return (float(d["x_m"]), float(d["y_m"]), float(d.get("z_m") or 0.0), float(d.get("yaw_deg") or 0.0))


class LocalTour:
    """서버 없이 시험할 때의 참값: 지정 위치에 고정(--truth) 또는 이륙 지점 → 각 슬롯 위 호버링 → … 순회(--tour)."""

    def __init__(self, waypoints: list[tuple[float, float]], z_m: float, speed_m_s: float, hover_s: float,
                 yaw_deg: float = 0.0):
        self.waypoints, self.z, self.speed, self.hover, self.yaw = waypoints, z_m, speed_m_s, hover_s, yaw_deg
        self.t0 = None
        self.error = ""
        legs = []
        for a, b in zip(waypoints, waypoints[1:] + waypoints[:1]):
            legs.append((a, b, math.dist(a, b) / speed_m_s if speed_m_s > 0 else 0.0))
        self.legs = legs
        self.period = sum(self.hover + d for _, _, d in legs) or 1.0

    def __call__(self, t: float):
        if self.t0 is None:
            self.t0 = t
        if len(self.waypoints) == 1:
            x, y = self.waypoints[0]
            return x, y, self.z, self.yaw
        u = (t - self.t0) % self.period
        for a, b, d in self.legs:
            if u < self.hover:
                return a[0], a[1], self.z, self.yaw
            u -= self.hover
            if u < d:
                k = u / d
                return a[0] + k * (b[0] - a[0]), a[1] + k * (b[1] - a[1]), self.z, self.yaw
            u -= d
        a = self.waypoints[0]
        return a[0], a[1], self.z, self.yaw


class SyntheticSource(Source):
    kind = "synthetic"

    def __init__(self, camera, truth, fps: float, label: str, mount_yaw_deg: float = 0.0):
        super().__init__()
        self.camera, self.truth, self.fps, self.label = camera, truth, fps, label
        self.mount_yaw_deg = mount_yaw_deg  # 실물과 같은 장착 각도로 그린다 (카메라 위쪽 = 드론 앞쪽 - 이 값)

    def _run(self):
        period = 1.0 / self.fps
        nxt = time.monotonic()
        while not self._stop.is_set():
            pose = self.truth(time.monotonic())
            if pose is None:
                self._set(False, f"합성 카메라: {getattr(self.truth, 'error', '') or '참값 없음'}")
            else:
                x, y, z, yaw = pose
                if z <= 0.02:  # 바닥(착지): 카메라가 바닥에 붙어 있으면 아무것도 안 보인다 — 그래도 그린다(실제와 같게)
                    z = 0.02
                img = self.camera.render(x, y, z, (yaw - self.mount_yaw_deg) % 360)
                self.frames.publish(img, time.time(), truth=(x, y, z, yaw))
                self._set(True, f"합성 카메라 {self.label} ({self.camera.width}x{self.camera.height} "
                                f"{'컬러' if self.camera.color else '흑백'})")
            nxt += period
            delay = nxt - time.monotonic()
            if delay < -period:
                nxt = time.monotonic()
            self._stop.wait(max(0.0, delay))


# ---------------------------------------------------------------- XIAO ESP32 (MJPEG)

class MjpegSource(Source):
    kind = "esp32"

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def _run(self):
        if not self.url:
            self._set(False, "ESP32 카메라 주소가 없습니다 ([bridge] esp32_camera_url 또는 --esp32-url)")
            self._stop.wait()
            return
        from localization.camera_stream import XiaoCameraSource  # 조원 수신기 (최신 한 장 슬롯, 재시도)
        while not self._stop.is_set():
            try:
                src = XiaoCameraSource(self.url)
            except ValueError as e:
                self._set(False, f"ESP32 카메라 주소 오류: {e} (예: http://192.168.0.50:81/stream)")
                self._stop.wait()
                return
            src.start()
            last = 0
            try:
                while not self._stop.is_set():
                    f = src.wait_latest(last, 0.5)
                    st = src.status()
                    if f is not None:
                        last = f.frame_id
                        self.frames.publish(f.image)
                        h, w = f.image.shape[:2]
                        self._set(True, f"ESP32 카메라 수신 중 ({w}x{h}, {st.receive_fps:.0f}장/초)")
                    elif st.state != "streaming":
                        self._set(False, f"ESP32 카메라 연결 중… ({st.state} {st.error})".strip())
                    if src.finished:
                        break
            finally:
                src.close()
            self._stop.wait(2.0)  # 실패 → 잠시 후 다시


# ---------------------------------------------------------------- TT 하단 카메라 (UDP H.264)

def crop_tt_frame(gray: np.ndarray, expect: tuple[int, int], crop_top_left: bool) -> tuple[np.ndarray, str]:
    """TT 하단 카메라 화면에서 실제 영상 부분만 남긴다.

    TT는 downvision 1일 때도 디코딩된 화면이 320x240보다 크게 나오고, 실제 영상은 **왼쪽 위 320x240**뿐이며
    나머지는 잡음이다(사용자 제공 확인 코드 `frame[0:240, 0:320]`, 2026-11-21). 그래서 화면이 설정 크기보다 크면
    왼쪽 위만 잘라 쓴다. 돌려주는 문자열은 상태 표시용 설명("" = 설정 크기와 같음).
    """
    h, w = gray.shape[:2]
    ew, eh = expect
    if (w, h) == (ew, eh):
        return gray, ""
    if crop_top_left and w >= ew and h >= eh:
        return gray[0:eh, 0:ew], f", 원본 {w}x{h}에서 왼쪽 위 {ew}x{eh}만 사용"
    return gray, f", 설정 {ew}x{eh}과 다름!"


class UdpH264Source(Source):
    kind = "tt_down"

    def __init__(self, url: str, expect: tuple[int, int], crop_top_left: bool = True):
        super().__init__()
        self.url, self.expect, self.crop_top_left = url, expect, crop_top_left
        self._warned_size = None

    def ffmpeg_url(self) -> str:
        if self.url.startswith("udp://") and "?" not in self.url:
            # 패킷이 몰려도 끊기지 않게 (FFmpeg udp 옵션)
            return self.url + "?overrun_nonfatal=1&fifo_size=50000000"
        return self.url

    def _run(self):
        # FFmpeg가 영상이 없을 때 오래 막히지 않도록 (마이크로초)
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "timeout;3000000")
        while not self._stop.is_set():
            self._set(False, f"TT 하단 카메라 영상 기다리는 중 ({self.url}) — 서버가 streamon·downvision 1을 보냈는지, "
                             f"방화벽에서 UDP 11111이 열렸는지 확인")
            cap = cv2.VideoCapture(self.ffmpeg_url(), cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                self._stop.wait(2.0)
                continue
            fails = 0
            while not self._stop.is_set():
                ok, img = cap.read()
                if not ok or img is None:
                    fails += 1
                    if fails > 20:
                        break
                    continue
                fails = 0
                gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                h, w = gray.shape
                gray, size_note = crop_tt_frame(gray, self.expect, self.crop_top_left)
                if size_note and self._warned_size != (w, h):
                    self._warned_size = (w, h)
                    if gray.shape[:2] == (self.expect[1], self.expect[0]):
                        log.info("[tt_down] 영상 크기 %dx%d → 왼쪽 위 %dx%d만 사용 (나머지는 잡음)", w, h, *self.expect)
                    else:
                        log.warning("[tt_down] 영상 크기 %dx%d — 설정은 %dx%d (downvision 1이 아니면 전방 카메라일 수 있음)",
                                    w, h, *self.expect)
                self.frames.publish(np.ascontiguousarray(gray))
                self._set(True, f"TT 하단 카메라 수신 중 ({gray.shape[1]}x{gray.shape[0]} 흑백{size_note})")
            cap.release()


# ---------------------------------------------------------------- 파일 (오프라인)

class FileSource(Source):
    kind = "file"

    def __init__(self, path: Path, fps: float, loop: bool = True, gray: bool = False):
        super().__init__()
        self.path, self.fps, self.loop, self.gray = Path(path), fps, loop, gray
        self.finished = threading.Event()

    def _frames_iter(self):
        from image_io import read_image  # 한글 경로도 읽힘 (조원 image_io)
        if self.path.is_dir():
            files = sorted(p for p in self.path.iterdir() if p.suffix.lower() in IMAGE_EXT)
            if not files:
                raise FileNotFoundError(f"사진이 없습니다: {self.path}")
            for p in files:
                img = read_image(p, cv2.IMREAD_GRAYSCALE if self.gray else cv2.IMREAD_COLOR)
                if img is not None:
                    yield img
        else:
            cap = cv2.VideoCapture(str(self.path))
            if not cap.isOpened():
                raise FileNotFoundError(f"영상을 열 수 없습니다: {self.path}")
            try:
                while True:
                    ok, img = cap.read()
                    if not ok:
                        break
                    yield cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if self.gray else img
            finally:
                cap.release()

    def _run(self):
        period = 1.0 / self.fps
        while not self._stop.is_set():
            n = 0
            for img in self._frames_iter():
                if self._stop.is_set():
                    return
                self.frames.publish(img)
                n += 1
                self._set(True, f"파일 입력 {self.path.name} ({img.shape[1]}x{img.shape[0]})")
                self._stop.wait(period)
            if not self.loop or n == 0:
                self._set(False, f"파일 입력 끝: {self.path.name}")
                self.finished.set()
                self._stop.wait()
                return
