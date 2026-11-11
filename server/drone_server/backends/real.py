"""real 백엔드 — 실제 RoboMaster TT (진짜 djitellopy, `pip install djitellopy`).

아직 실기체로 검증하지 않은 '자리'다. 구조는 sim과 같고(TelloBackend), 차이는:
- 상태는 TT가 UDP 8890으로 계속 보내 주므로 같은 Tello 객체로 읽는다 (연결 1개).
- 진행 중 이동은 SDK 3.0 'stop' 명령으로 즉시 멈출 수 있다 (interrupt).
- 위치는 PositionProvider로 갈아 끼운다: estimate(기본) 또는 mission_pad(조원 구현).
"""
from __future__ import annotations

from typing import Any

from ..models import Pose
from ..positioning import DeadReckoningProvider, MissionPadProvider, PositionProvider, VisionProvider
from ..settings import Settings
from .tello import TelloBackend


def make_position_provider(settings: Settings) -> PositionProvider:
    home = Pose(settings.home_x_cm, settings.home_y_cm, 0.0, 0.0)
    dr = DeadReckoningProvider(home)
    src = settings.real.pos_source
    if src == "estimate":
        return dr
    if src == "vision":
        return VisionProvider(dr, settings.vision.stale_s)
    if src == "mission_pad":
        pads = {
            int(p["mid"]): {"x_cm": float(p["x_cm"]), "y_cm": float(p["y_cm"]), "rot_deg": float(p.get("rot_deg", 0))}
            for p in settings.raw.get("mission_pads", [])
        }
        if not pads:
            raise ValueError("pos_source = mission_pad 인데 settings.toml에 [[mission_pads]]가 없습니다")
        return MissionPadProvider(pads, dr)
    raise ValueError(f"[real] pos_source는 estimate | mission_pad | vision: {src!r}")


def create_real_backend(settings: Settings) -> TelloBackend:
    try:
        from djitellopy import Tello  # noqa: PLC0415 — 실제 라이브러리
    except ImportError as e:
        raise RuntimeError("실제 드론용 djitellopy가 없습니다: .venv\\Scripts\\python.exe -m pip install djitellopy") from e

    cfg = settings.real
    provider = make_position_provider(settings)
    use_pads = cfg.pos_source == "mission_pad"

    def make() -> Any:
        t = Tello()
        orig_connect = t.connect

        def connect(*a, **kw):
            orig_connect(*a, **kw)
            if use_pads:
                t.enable_mission_pads()
                t.set_mission_pad_detection_direction(0)  # 0 = 아래쪽만
        t.connect = connect
        return t

    def stop_now(t: Any) -> None:
        t.send_command_without_return("stop")  # SDK 3.0: 언제든 제자리 호버링

    return TelloBackend(
        name="real", cfg=cfg, provider=provider,
        home=Pose(settings.home_x_cm, settings.home_y_cm, 0.0, 0.0),
        make_cmd_tello=make, make_monitor_tello=None,
        read_state=lambda t: t.get_current_state(),
        interrupt=stop_now,
    )
