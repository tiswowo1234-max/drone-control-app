"""중계 서버 실행.

    python run_server.py            # settings.toml [backend] mode 사용
    python run_server.py mock       # Unity 없이 가짜 드론
    python run_server.py sim        # Unity 시뮬레이터 (UnityProject.exe를 먼저 켜기)
    python run_server.py real       # 실제 RoboMaster TT
    python run_server.py mock --vision   # 위치를 POST /vision/pose 값으로 표시 (tools/fake_vision.py로 시험)
    python run_server.py real --car off  # 차(UGV) 기능 끔. --car mock = 가짜 차, --car ble = 실제 차 (기본 [car] mode)
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from drone_server.app import create_app  # noqa: E402
from drone_server.backends import create_backend  # noqa: E402
from drone_server.car import make_car_link  # noqa: E402
from drone_server.serve import install_log_filters, make_server, run_until_stopped  # noqa: E402
from drone_server.settings import DEFAULT_SETTINGS_PATH, load_settings, validate  # noqa: E402


def local_ips() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # 기본 경로로 나가는 IP (실제로 패킷을 보내지는 않음)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


LOG_DIR = Path(__file__).resolve().parent / "logs"


def add_log_file(kind: str) -> Path | None:
    """화면에 나오는 로그를 server/logs/server-날짜.log에도 남긴다 (실물 시험 뒤 원인 분석용).

    하루에 파일 하나, 실행할 때마다 이어 쓴다. 실행 시작마다 구분선을 넣는다. 파일을 못 만들어도 서버는 계속 돈다.
    """
    import datetime as dt
    import os
    if "PYTEST_CURRENT_TEST" in os.environ:  # 자동 테스트가 서버를 켤 때는 실제 로그를 어지럽히지 않는다
        return None
    try:
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / f"server-{dt.date.today():%Y%m%d}.log"
        h = logging.FileHandler(path, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
                                         datefmt="%Y-%m-%d %H:%M:%S"))
        logging.getLogger().addHandler(h)
        logging.getLogger().info("===== 서버 시작 (backend=%s, 인자=%s) =====", kind, " ".join(sys.argv[1:]))
        return path
    except OSError as e:
        logging.getLogger().warning("로그 파일을 만들지 못했습니다: %s", e)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="드론 중계 서버")
    ap.add_argument("backend", nargs="?", choices=["mock", "sim", "real"], help="생략하면 settings.toml 값")
    ap.add_argument("--config", default=str(DEFAULT_SETTINGS_PATH), help="settings.toml 경로")
    ap.add_argument("--host", help="기본: settings.toml [server] host")
    ap.add_argument("--port", type=int, help="기본: settings.toml [server] port")
    ap.add_argument("--vision", action="store_true",
                    help="카메라 위치 추정 입력(POST /vision/pose)을 켠다 = 선택한 백엔드의 pos_source를 vision으로")
    ap.add_argument("--car", choices=["ble", "mock", "off"],
                    help="차(UGV) 연결: ble = 실제 차(블루투스), mock = 가짜 차, off = 끔 (기본 [car] mode)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    log_path = add_log_file(args.backend or "default")
    install_log_filters()
    settings = load_settings(args.config)
    kind = args.backend or settings.backend
    if args.vision:
        settings = replace(settings, **{kind: replace(getattr(settings, kind), pos_source="vision")})
        validate(settings)
    if args.car:
        settings = replace(settings, car=replace(settings.car, mode=args.car))
    host = args.host or settings.server.host
    port = args.port or settings.server.port

    backend = create_backend(kind, settings)
    car_link = make_car_link(settings, settings.mock.time_scale if settings.car.mode == "mock" else 1.0)
    app = create_app(settings, backend, car_link)

    print("=" * 60)
    print(f" 드론 중계 서버  backend={kind}")
    print(f" 설정 파일: {args.config}")
    print(" 앱에 입력할 주소:")
    for ip in local_ips() or ["<이 PC의 IP>"]:
        print(f"   ws://{ip}:{port}/ws")
    print(f" 확인용(브라우저): http://127.0.0.1:{port}/")
    if log_path:
        print(f" 로그 파일: {log_path}")
    if getattr(settings, kind).pos_source == "vision":
        print(f" 위치 입력(vision) 켜짐: POST http://<이 PC IP>:{port}/vision/pose  (tools/fake_vision.py로 시험)")
    print(f" 위치 추적 카메라 모드(처음): {settings.bridge.default_mode}  (앱에서 esp32 / tt_down 전환)")
    print(f" vision/ 연결 프로그램: ws://<이 PC IP>:{port}/vision/video, POST /vision/incident")
    car_text = {"ble": f"실제 차 (블루투스 '{settings.car.ble_address or settings.car.ble_name}' 자동 연결)",
                "mock": "가짜 차(mock)", "off": "끔"}[settings.car.mode]
    print(f" 차(UGV): {car_text}  — 위치 입력 POST /vision/ugv")
    if settings.car.mode == "ble":
        print("   ※ 폰의 police-car-controller(PWA)가 차에 연결돼 있으면 서버가 연결할 수 없습니다 (BLE는 한 곳만)")
    print(" 끄려면 이 창에서 Ctrl+C")
    print("=" * 60)
    return run_until_stopped(make_server(app, host, port))


if __name__ == "__main__":
    sys.exit(main())
