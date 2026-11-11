from __future__ import annotations

from ..settings import Settings
from .base import DroneBackend


def create_backend(kind: str, settings: Settings) -> DroneBackend:
    """kind: mock | sim | real. sim/real은 필요할 때만 import한다 (djitellopy 충돌 방지)."""
    if kind == "mock":
        from .mock import MockBackend
        return MockBackend(settings)
    if kind == "sim":
        from .sim import create_sim_backend
        return create_sim_backend(settings)
    if kind == "real":
        from .real import create_real_backend
        return create_real_backend(settings)
    raise ValueError(f"알 수 없는 백엔드: {kind} (mock | sim | real)")
