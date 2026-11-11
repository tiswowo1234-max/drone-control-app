"""sim 백엔드 — 조원이 만든 Unity 시뮬레이터(UnityProject.exe, 127.0.0.1:9911)에
가짜 djitellopy(RoboMasterTT-Simulator beta/Python/simulated)로 연결한다.

시뮬레이터에서 실측한 동작 (2026-11-11):
- 연결을 여러 개 열 수 있다. 비행 명령은 끝난 뒤에 응답하고, 그 사이 다른 비행 명령
  (land, rc 포함)은 "Flight command already in progress"로 거부된다. emergency만 즉시 먹힌다.
- go a b c: a=오른쪽, b=전진, c=위 (실제 SDK는 x=전진, y=왼쪽) → [sim] go_axes = "sim"
- 비상정지 뒤에는 'reset' 요청 전까지 이륙이 거부된다.
"""
from __future__ import annotations

import sys
import types
from typing import Any

from ..models import Pose
from ..positioning import DeadReckoningProvider, SimFrame, SimTruthProvider, VisionProvider
from ..settings import REPO_ROOT, Settings
from .tello import TelloBackend


def _import_fake_djitellopy(python_dir: str):
    sim_dir = (REPO_ROOT / python_dir / "simulated").resolve()
    if not (sim_dir / "djitellopy").is_dir():
        raise RuntimeError(f"가짜 djitellopy를 찾을 수 없습니다: {sim_dir}")
    if str(sim_dir) not in sys.path:
        sys.path.insert(0, str(sim_dir))
    # 영상 기능용 cv2/numpy가 없어도 제어는 되도록 빈 모듈로 대체 (영상은 MVP에 불필요)
    for mod in ("cv2", "numpy"):
        try:
            __import__(mod)
        except ImportError:
            sys.modules[mod] = types.ModuleType(mod)
    import djitellopy  # noqa: PLC0415
    if not str(getattr(djitellopy, "__file__", "")).startswith(str(sim_dir)):
        raise RuntimeError(f"다른 djitellopy가 먼저 import됨: {djitellopy.__file__}")
    return djitellopy


class SimBackend(TelloBackend):
    def _start_pose(self, raw: dict) -> Pose:
        # 시뮬레이터는 드론 시작 위치가 고정이다. 보정값으로 그 위치를 지도 좌표로 바꿔
        # 추정의 출발점으로 쓴다 (실제 드론은 home에 놓고 켠다).
        if "world_x" in raw and "world_z" in raw:
            truth = SimTruthProvider(self._calib)
            truth.update(raw)
            p = truth.pose()
            if p is not None:
                return p
        return self.home


def create_sim_backend(settings: Settings) -> SimBackend:
    cfg = settings.sim
    dji = _import_fake_djitellopy(cfg.simulator_python_dir)
    home = Pose(settings.home_x_cm, settings.home_y_cm, 0.0, 0.0)
    calib = settings.sim_calibration
    if cfg.pos_source == "sim_truth":
        provider = SimTruthProvider(calib)
    elif cfg.pos_source == "estimate":
        provider = DeadReckoningProvider(home, frame=SimFrame(calib))
    elif cfg.pos_source == "vision":
        provider = VisionProvider(DeadReckoningProvider(home, frame=SimFrame(calib)), settings.vision.stale_s)
    else:
        raise ValueError(f"[sim] pos_source는 estimate | sim_truth | vision: {cfg.pos_source!r}")

    def make() -> Any:
        return dji.Tello(host=cfg.host, command_port=cfg.port)

    def reset(t: Any) -> None:
        r = t._client.request("reset")
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "reset 실패")

    b = SimBackend(
        name="sim", cfg=cfg, provider=provider, home=home,
        make_cmd_tello=make, make_monitor_tello=make,
        read_state=lambda t: t.get_sim_debug_state(),
        interrupt=None,  # 시뮬레이터는 진행 중 명령을 끊을 수 없다 → 구간이 끝나길 기다림
        sim_reset=reset,
        frame=SimFrame(calib),
    )
    b._calib = calib
    return b
