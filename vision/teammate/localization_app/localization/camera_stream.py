"""Receive bounded multipart JPEG streams and retain a single pending BGR frame."""

from dataclasses import dataclass
from collections import deque
from email.message import Message
import http.client
import math
import socket
from threading import Condition, Event, Thread
from time import perf_counter
from urllib.parse import urlsplit

import cv2
import numpy as np

from .camera_interface import CameraFrame, LatestFrameBuffer

MAX_LINE = 8192
MAX_FRAME = 4 * 1024 * 1024


def _line(stream):
    line = stream.readline(MAX_LINE + 1)
    if not line:
        raise EOFError("stream ended")
    if len(line) > MAX_LINE:
        raise ValueError("multipart header line too long")
    return line


def mjpeg_parts(content_type, stream, *, with_timing=False):
    """Yield lower-case part headers and payloads from an HTTP response body."""
    mime = Message()
    mime["Content-Type"] = content_type
    boundary = mime.get_boundary()
    if mime.get_content_type() != "multipart/x-mixed-replace" or not boundary:
        raise ValueError("expected a multipart MJPEG response with a boundary")
    marker = b"--" + boundary.encode("ascii")
    closing = marker + b"--"
    pending = None
    while True:
        delimiter = pending if pending is not None else _line(stream).rstrip(b"\r\n")
        pending = None
        while not delimiter:
            delimiter = _line(stream).rstrip(b"\r\n")
        if delimiter == closing:
            return
        if delimiter != marker:
            raise ValueError("unexpected multipart boundary")
        part_start = perf_counter() if with_timing else None
        headers = {}
        for _ in range(32):
            line = _line(stream)
            if line in (b"\r\n", b"\n"):
                break
            key, value = line.decode("latin-1").split(":", 1)
            headers[key.strip().lower()] = value.strip()
        else:
            raise ValueError("too many multipart headers")
        if "content-length" in headers:
            size = int(headers["content-length"])
            if not 0 < size <= MAX_FRAME:
                raise ValueError("invalid multipart Content-Length")
            payload = stream.read(size)
            if len(payload) != size:
                raise EOFError("truncated JPEG part")
        else:
            data = bytearray()
            while True:
                line = stream.readline(MAX_FRAME - len(data) + 1)
                if not line:
                    raise EOFError("truncated boundary-delimited part")
                delim = line.rstrip(b"\r\n")
                if delim in (marker, closing):
                    pending = delim
                    payload = bytes(data.removesuffix(b"\r\n"))
                    if len(payload) == len(data):
                        payload = payload.removesuffix(b"\n")
                    break
                data.extend(line)
                if len(data) >= MAX_FRAME:
                    raise ValueError("boundary-delimited part too large")
        if with_timing:
            yield headers, payload, (perf_counter() - part_start) * 1000
        else:
            yield headers, payload


@dataclass(frozen=True)
class StreamStatus:
    state: str
    error: str
    received: int
    overwritten: int
    source_gaps: int | None
    decode_errors: int
    disconnects: int
    reconnections: int
    connection: int
    receive_fps: float
    last_received_at: float | None


class _CameraConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        # HTTPConnection can detach the socket for a Connection: close response.
        self.stream_socket = self.sock


class XiaoCameraSource:
    """HTTP receiver with finite I/O timeouts and consecutive retry limit."""

    def __init__(self, url, *, timeout_s=2.0, retry_s=0.5, max_retries=5):
        address = urlsplit(url)
        if address.scheme != "http" or not address.hostname or address.username or address.password:
            raise ValueError("camera URL must be an HTTP stream URL without credentials")
        self.host, self.port = address.hostname, address.port
        self.path = address.path or "/"
        if address.query:
            self.path += "?" + address.query
        if (not all(math.isfinite(value) and value > 0 for value in (timeout_s, retry_s))
                or max_retries < 0):
            raise ValueError("stream timeouts must be positive and retry count nonnegative")
        self.timeout_s, self.retry_s, self.max_retries = timeout_s, retry_s, max_retries
        self._condition, self._stop = Condition(), Event()
        self._worker = None
        self._connection = None
        self._frames = LatestFrameBuffer(self._condition)
        self._state, self._error = "idle", ""
        self._received = self._overwritten = self._gaps = self._decode_errors = 0
        self._disconnects = self._connections = 0
        self._times = deque(maxlen=60)
        self._last_received = None
        self._session = self._sequence = None
        self._has_sequence = False

    @property
    def running(self):
        return self._worker is not None and self._worker.is_alive()

    def start(self):
        if self._worker is not None:
            raise RuntimeError("camera receiver already started")
        self._worker = Thread(target=self._receive, name="xiao-receiver", daemon=True)
        self._worker.start()

    def read(self, timeout: float = 0) -> CameraFrame | None:
        return self._frames.read(timeout)

    def wait_latest(self, after_id, timeout=0):
        return self._frames.wait_latest(after_id, timeout)

    @property
    def finished(self):
        with self._condition:
            return self._state in ("failed", "closed")

    def peek(self) -> CameraFrame | None:
        """Read the current frame for display without advancing a cursor."""
        return self._frames.peek()

    def receive_cursor(self):
        with self._condition:
            return perf_counter(), self._received

    def status(self):
        with self._condition:
            fps = ((len(self._times) - 1) / (self._times[-1] - self._times[0])
                   if len(self._times) > 1 and self._times[-1] > self._times[0] else 0.0)
            return StreamStatus(self._state, self._error, self._received, self._overwritten,
                                self._gaps if self._has_sequence else None, self._decode_errors,
                                self._disconnects, max(0, self._connections - 1),
                                self._connections, fps, self._last_received)

    def close(self):
        self._stop.set()
        with self._condition:
            stream_socket = getattr(self._connection, "stream_socket", None)
        if stream_socket is not None:
            try:
                stream_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._worker is not None:
            self._worker.join(self.timeout_s + 1)
        self._set_state("closed")

    def _set_state(self, state, error=""):
        with self._condition:
            self._state, self._error = state, error
            if state != "streaming":
                self._frames.clear(finished=state in ("failed", "closed"))
                self._times.clear()
            self._condition.notify_all()

    def _receive(self):
        retries = 0
        while not self._stop.is_set():
            with self._condition:
                before = self._received
            self._set_state("connecting" if retries == 0 else "reconnecting", self._error)
            connection = _CameraConnection(self.host, self.port, timeout=self.timeout_s)
            with self._condition:
                self._connection = connection
            try:
                connection.request("GET", self.path, headers={"Accept": "multipart/x-mixed-replace"})
                if self._stop.is_set():
                    return
                with connection.getresponse() as response:
                    if response.status != 200:
                        raise ValueError(f"camera returned HTTP {response.status}")
                    bad_frames = 0
                    parts = mjpeg_parts(response.getheader("Content-Type", ""), response,
                                        with_timing=True)
                    for headers, data, receive_ms in parts:
                        if self._stop.is_set():
                            return
                        bad_frames = 0 if self._consume(headers, data, receive_ms) else bad_frames + 1
                        if bad_frames >= 8:
                            raise ValueError("eight consecutive JPEG decode failures")
                raise EOFError("camera closed the multipart stream")
            except (OSError, http.client.HTTPException, ValueError, EOFError) as error:
                if self._stop.is_set():
                    break
                with self._condition:
                    if self._state == "streaming":
                        self._disconnects += 1
                    retries = 1 if self._received > before else retries + 1
                if retries > self.max_retries:
                    self._set_state("failed", str(error))
                    return
                self._set_state("reconnecting", str(error))
            finally:
                connection.close()
                with self._condition:
                    self._connection = None
            self._stop.wait(self.retry_s)

    def _consume(self, headers, data, receive_ms=None):
        received = perf_counter()
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "image/jpeg":
            raise ValueError("multipart part must have Content-Type image/jpeg")
        session = headers.get("x-stream-session")
        sequence = headers.get("x-frame-id")
        sequence = int(sequence) if sequence is not None else None
        if sequence is not None and not 0 <= sequence <= 0xffffffff:
            raise ValueError("invalid camera frame id")
        decode_start = perf_counter()
        try:
            image = (cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                     if data.startswith(b"\xff\xd8") else None)
        except cv2.error:
            image = None
        decode_ms = (perf_counter() - decode_start) * 1000
        with self._condition:
            if self._stop.is_set():
                return False
            if sequence is not None:
                self._has_sequence = True
                if self._sequence is not None and session == self._session:
                    if sequence > self._sequence:
                        self._gaps += sequence - self._sequence - 1
                    elif self._sequence >= 0xfffffff0 and sequence <= 15:
                        self._gaps += (sequence - self._sequence - 1) % (1 << 32)
                self._sequence, self._session = sequence, session
            else:
                self._sequence = None
            if image is None:
                self._decode_errors += 1
                return False
            if self._state != "streaming":
                self._connections += 1
            self._state, self._error = "streaming", ""
            if self._last_received is not None and received <= self._last_received:
                received = math.nextafter(self._last_received, math.inf)
            self._last_received = received
            self._times.append(received)
            self._received += 1
            self._overwritten += self._frames.publish(
                CameraFrame(image, received, self._received, "xiao", received,
                            decode_ms, sequence, self._connections, receive_ms))
            return True
