"""드론 백엔드 인터페이스.

컨트롤러(controller.py)는 이 인터페이스만 사용한다. mock / sim / real은 이것을 구현한다.
모든 비행 메서드는 동작이 **끝날 때까지** 기다리는 async 함수이고,
컨트롤러가 asyncio 작업을 cancel()하면 가능한 한 빨리 멈춰야 한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import BackendError, Pose, Telemetry


class DroneBackend(ABC):
    name: str = "?"  # config 메시지의 backend 값
    vision = None    # VisionProvider (pos_source = "vision"일 때). POST /vision/pose가 여기로 값을 넣는다
    vision_mode: str | None = None  # 위치 추적 카메라 모드 (docs/api.md 11장). 연결될 때 이 모드로 카메라를 맞춘다
    has_truth = False               # GET /sim/truth 지원 여부 (mock, sim)

    async def apply_vision_mode(self, mode: str) -> None:
        """위치 추적 카메라 모드를 드론에 반영한다 (real: streamon/downvision/streamoff).
        기본(mock/sim)은 모드만 기억하고 드론에는 아무것도 보내지 않는다."""
        self.vision_mode = mode

    def truth_pose(self) -> Pose | None:
        """시험용 참값 위치 (지도 cm, docs/api.md 12장). 모르면 None. has_truth = False면 쓰지 않는다."""
        return None

    def heading_ready(self) -> bool | None:
        """카메라로 방향 보정값이 정해졌는지 (초기 정렬 완료 조건). 보정을 쓰지 않는 백엔드는 None."""
        return None

    def attitude(self) -> dict | None:
        """드론 자세·높이 센서 값 (연결 프로그램에 drone_state로 전달, docs/api.md 8장).
        {"pitch_deg", "roll_deg", "yaw_deg", "tof_cm", "h_cm", "flying"} 중 아는 것만. 모르면 None."""
        return None

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
    async def move_to(self, x_cm: float, y_cm: float, z_cm: float) -> int | None:
        """지도 좌표로 이동. 안전 검사는 컨트롤러가 이미 끝낸 상태로 호출된다.
        반환: 보낸 이동 구간 수(0 = 이미 목표 근처라 움직이지 않음), 모르면 None."""

    async def set_velocity(self, fwd_cm_s: float, right_cm_s: float, up_cm_s: float = 0.0,
                           yaw_deg_s: float = 0.0) -> None:
        """속도 명령 (드론 기준: 앞 +, 오른쪽 +, 위 +, 시계 회전 +). 응답을 기다리지 않고 바로 돌아온다.
        UGV 따라가기처럼 0.1초마다 계속 보내는 용도. 멈출 때는 반드시 0을 보낸다.
        이 속도로 움직인 만큼 추정 위치도 옮긴다."""
        raise BackendError(f"{self.name} 백엔드는 속도 명령을 지원하지 않습니다")

    @abstractmethod
    async def hover(self) -> None:
        """진행 중 이동을 멈추고 제자리 호버링."""

    @abstractmethod
    async def land(self) -> None:
        """제자리 착륙, 착지할 때까지 기다린다."""

    @abstractmethod
    async def emergency(self) -> None:
        """모터 즉시 정지. 다른 명령이 진행 중이어도 바로 보내야 한다."""
