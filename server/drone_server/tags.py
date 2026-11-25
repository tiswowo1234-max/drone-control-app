"""AprilTag 지도 위치 읽기 (초기 정렬용, 2026-11-25).

vision/ 연결 프로그램이 위치 계산에 쓰는 것과 **같은 태그 지도 파일**을 읽는다.
  - 기본: 조원 원본 vision/teammate/data/maps/apriltag_map.json (ID 0~38)
  - [bridge] extra_tags = true: 우리 확장판 vision/data/maps/apriltag_map.json (슬롯 보드 ID 39~54 포함)
  - [align] tags_file: 직접 지정 (프로젝트 루트 기준 상대 경로 또는 절대 경로)
파일 좌표는 m(지도 왼쪽 아래 원점) → 여기서 cm로 바꾼다.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from .settings import REPO_ROOT, Settings

log = logging.getLogger("drone_server.tags")

TEAMMATE_TAGS = Path("vision/teammate/data/maps/apriltag_map.json")
EXTRA_TAGS = Path("vision/data/maps/apriltag_map.json")


@dataclass(frozen=True)
class Tag:
    id: int
    x_cm: float
    y_cm: float


def tags_path(s: Settings) -> Path:
    if s.align.tags_file:
        p = Path(s.align.tags_file)
        return p if p.is_absolute() else REPO_ROOT / p
    extra = bool(s.raw.get("bridge", {}).get("extra_tags", False))
    if extra and (REPO_ROOT / EXTRA_TAGS).exists():
        return REPO_ROOT / EXTRA_TAGS
    return REPO_ROOT / TEAMMATE_TAGS


def load_tags(s: Settings) -> list[Tag]:
    """태그 위치 목록. 파일이 없거나 읽을 수 없으면 빈 목록(정렬을 건너뛴다)."""
    p = tags_path(s)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [Tag(int(t["id"]), float(t["center_xy_m"][0]) * 100.0, float(t["center_xy_m"][1]) * 100.0)
                for t in data["tags"]]
    except (OSError, ValueError, KeyError, TypeError, IndexError) as e:
        log.warning("태그 지도를 읽지 못했습니다 (%s): %s — 초기 정렬을 건너뜁니다", p, e)
        return []


def nearest_tag(tags: list[Tag], x_cm: float, y_cm: float) -> tuple[Tag, float] | None:
    """(가장 가까운 태그, 거리 cm). 태그가 없으면 None."""
    if not tags:
        return None
    t = min(tags, key=lambda t: math.hypot(t.x_cm - x_cm, t.y_cm - y_cm))
    return t, math.hypot(t.x_cm - x_cm, t.y_cm - y_cm)
