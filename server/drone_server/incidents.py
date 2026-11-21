"""사건 보고 (docs/api.md 3.5·3.6·9·10.1장).

vision/ 연결 프로그램이 POST /vision/incident로 사진을 보내면 여기서 검사·중복 확인 후 메모리에 둔다.
- 최근 [incident] keep개만 기억한다 (사진 포함). 서버를 다시 켜면 비어 있다.
- 같은 슬롯(또는 슬롯이 없으면 same_place_m 안의 위치)이 dedup_s초 안에 다시 오면 중복.
  두 카메라 모드의 성능 비교를 위해 **모드가 다르면 중복으로 보지 않는다.**
"""
from __future__ import annotations

import base64
import binascii
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from .settings import INCIDENT_METHODS, VISION_MODES, Settings


class IncidentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Incident:
    id: str
    received_at: float          # 서버 시계 (중복 판정용)
    data: dict[str, Any]        # 앱에 보내는 형식 (3.5)
    photo: bytes

    @property
    def slot(self) -> int | None:
        return self.data["slot"]

    @property
    def mode(self) -> str | None:
        return self.data["mode"]

    @property
    def pos_cm(self) -> tuple[float, float] | None:
        p = self.data["pos"]
        return None if p is None else (p["x_cm"], p["y_cm"])


def _num(body: dict, key: str) -> float | None:
    v = body.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise IncidentError("BAD_REQUEST", f"{key}는 숫자여야 합니다")
    return float(v)


def _opt_str(body: dict, key: str, allowed: tuple[str, ...] | None = None) -> str | None:
    v = body.get(key)
    if v is None:
        return None
    if not isinstance(v, str) or (allowed is not None and v not in allowed):
        want = " | ".join(allowed) if allowed else "문자열"
        raise IncidentError("BAD_REQUEST", f"{key}는 {want}이어야 합니다")
    return v


def bilinear_geo(geo: dict | None, x_cm: float, y_cm: float, w_cm: float, h_cm: float) -> dict | None:
    """지도 cm → 위도·경도 (네 모서리 쌍선형 보간, docs/api.md 3.1). 모서리가 다 없으면 None."""
    if not geo or not all(k in geo for k in ("sw", "se", "nw", "ne")):
        return None
    u, v = x_cm / w_cm, y_cm / h_cm
    out = {}
    for k in ("lat", "lon"):
        out[k] = round((1 - u) * (1 - v) * geo["sw"][k] + u * (1 - v) * geo["se"][k]
                       + (1 - u) * v * geo["nw"][k] + u * v * geo["ne"][k], 7)
    return out


class IncidentStore:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic):
        self.s = settings
        self.cfg = settings.incident
        self._clock = clock
        self._items: deque[Incident] = deque(maxlen=max(1, int(self.cfg.keep)))  # 최신이 앞
        self._next = 1
        self.total = 0          # 서버를 켠 뒤 받은 새 사건 수 (중복 제외)
        self.duplicates = 0
        slots = [p.slot for p in settings.photo_slots]
        self._slot_numbers = set(slots) if slots else {1, 2, 3, 4}

    # ---------------------------------------------------------------- 조회
    def items(self) -> list[dict]:
        return [i.data for i in self._items]

    def photo(self, inc_id: str) -> bytes | None:
        for i in self._items:
            if i.id == inc_id:
                return i.photo
        return None

    def __len__(self) -> int:
        return len(self._items)

    # ---------------------------------------------------------------- 추가
    def add(self, body: Any) -> tuple[Incident, bool]:
        """본문 검사 → (사건, 중복 여부). 잘못된 본문이면 IncidentError."""
        if not isinstance(body, dict):
            raise IncidentError("BAD_REQUEST", "JSON 객체가 아닙니다")
        photo = self._photo(body.get("photo_jpeg_b64"))
        ts = _num(body, "ts")
        mode = _opt_str(body, "mode", VISION_MODES)
        method = _opt_str(body, "method", INCIDENT_METHODS)
        place = _opt_str(body, "place")
        slot = body.get("slot")
        if slot is not None and (isinstance(slot, bool) or not isinstance(slot, int) or slot not in self._slot_numbers):
            raise IncidentError("BAD_REQUEST", f"slot은 {sorted(self._slot_numbers)} 중 하나 또는 null이어야 합니다")

        w, h = self.s.map_width_cm, self.s.map_height_cm
        x_m, y_m = _num(body, "x_m"), _num(body, "y_m")
        pos = None
        if x_m is not None and y_m is not None:
            m = self.s.vision.margin_m
            if not (-m <= x_m <= w / 100 + m and -m <= y_m <= h / 100 + m):
                raise IncidentError("OUT_OF_BOUNDS", f"지도({w / 100:g}m x {h / 100:g}m, 여유 {m:g}m) 밖 좌표입니다")
            pos = {"x_cm": round(x_m * 100, 1), "y_cm": round(y_m * 100, 1)}

        lat, lon = _num(body, "lat"), _num(body, "lon")
        if lat is not None and lon is not None:
            geo = {"lat": lat, "lon": lon}
        elif pos is not None:  # 위경도를 안 보냈으면 지도 좌표로 계산해 준다 (표시용)
            geo = bilinear_geo(self.s.geo, pos["x_cm"], pos["y_cm"], w, h)
        else:
            geo = None

        drone = body.get("drone")
        drone_pos = None
        if drone is not None:
            if not isinstance(drone, dict):
                raise IncidentError("BAD_REQUEST", "drone은 {x_m, y_m, z_m} 객체여야 합니다")
            dx, dy, dz = _num(drone, "x_m"), _num(drone, "y_m"), _num(drone, "z_m")
            if dx is not None and dy is not None:
                drone_pos = {"x_cm": round(dx * 100, 1), "y_cm": round(dy * 100, 1),
                             "z_cm": None if dz is None else round(dz * 100, 1)}

        detection = body.get("detection")
        if detection is not None and not isinstance(detection, dict):
            raise IncidentError("BAD_REQUEST", "detection은 객체 또는 null이어야 합니다")

        now = self._clock()
        dup = self._find_duplicate(now, slot, mode, pos)
        if dup is not None:
            self.duplicates += 1
            return dup, True

        inc_id = f"inc-{self._next}"
        self._next += 1
        data = {
            "id": inc_id,
            "ts": ts if ts is not None else round(time.time(), 3),
            "slot": slot,
            "place": place,
            "pos": pos,
            "geo": geo,
            "drone_pos": drone_pos,
            "photo_url": f"/incidents/{inc_id}/photo.jpg",
            "mode": mode,
            "method": method,
            "detection": detection,
        }
        inc = Incident(inc_id, now, data, photo)
        self._items.appendleft(inc)
        self.total += 1
        return inc, False

    def _photo(self, b64: Any) -> bytes:
        if not isinstance(b64, str) or not b64:
            raise IncidentError("BAD_REQUEST", "photo_jpeg_b64(문자열)가 필요합니다")
        limit = self.cfg.max_photo_kb * 1024
        if len(b64) * 3 / 4 > limit + 3:  # 디코딩 전에 대략 걸러 낸다
            raise IncidentError("TOO_LARGE", f"사진이 {self.cfg.max_photo_kb:g} KB보다 큽니다")
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise IncidentError("BAD_REQUEST", "photo_jpeg_b64가 올바른 base64가 아닙니다") from None
        if len(data) > limit:
            raise IncidentError("TOO_LARGE", f"사진이 {self.cfg.max_photo_kb:g} KB보다 큽니다")
        if data[:2] != b"\xff\xd8":
            raise IncidentError("BAD_REQUEST", "사진이 JPEG가 아닙니다")
        return data

    def _find_duplicate(self, now: float, slot: int | None, mode: str | None,
                        pos: dict | None) -> Incident | None:
        same_cm = self.cfg.same_place_m * 100
        for i in self._items:  # 최신부터
            if now - i.received_at > self.cfg.dedup_s:
                break
            if i.mode != mode:
                continue
            if slot is not None:
                if i.slot == slot:
                    return i
            elif pos is not None and i.slot is None and i.pos_cm is not None:
                if math.hypot(i.pos_cm[0] - pos["x_cm"], i.pos_cm[1] - pos["y_cm"]) <= same_cm:
                    return i
        return None
