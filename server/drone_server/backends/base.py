"""드론 백엔드 인터페이스.

컨트롤러(controller.py)는 이 인터페이스만 사용한다. mock / sim / real은 이것을 구현한다.
모든 비행 메서드는 동작이 **끝날 때까지** 기다리는 async 함수이고,
컨트롤러가 asyncio 작업을 cancel()하면 가능한 한 빨리 멈춰야 한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Telemetry


class DroneBackend(ABC):
    name: str = "?"  # config 메시지의 backend 값
    vision = None    # VisionProvider (pos_source = "vision"일 때). POST /vision/pose가 여기로 값을 넣는다

    @abstractmethod
    async def connect(self) -> None:
        """드론/시뮬레이터에 연결. 실패하면 예외. (서버가 주기적으로 재시도한다)"""

    async def close(self) -> None:
        pass

    @abstractmethod
    def telemetry(self) -> Telemetry:
        """현재 상태 (캐시된 값, 즉시 반환)."""

    @abstractmethod
    async def takeoff(self) -> None:
        """이륙해서 기체 기본 높이에 호버링할 때까지 기다린다."""

    @abstractmethod
    async def move_to(self, x_cm: float, y_cm: float, z_cm: float) -> None:
        """지도 좌표로 이동. 안전 검사는 컨트롤러가 이미 끝낸 상태로 호출된다."""

    @abstractmethod
    async def hover(self) -> None:
        """진행 중 이동을 멈추고 제자리 호버링."""

    @abstractmethod
    async def land(self) -> None:
        """제자리 착륙, 착지할 때까지 기다린다."""

    @abstractmethod
    async def emergency(self) -> None:
        """모터 즉시 정지. 다른 명령이 진행 중이어도 바로 보내야 한다."""
