"""중계 서버 실행.

    python run_server.py            # settings.toml [backend] mode 사용
    python run_server.py mock       # Unity 없이 가짜 드론
    python run_server.py sim        # Unity 시뮬레이터 (UnityProject.exe를 먼저 켜기)
    python run_server.py real       # 실제 RoboMaster TT
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from drone_server.app import create_app  # noqa: E402
from drone_server.backends import create_backend  # noqa: E402
from drone_server.settings import DEFAULT_SETTINGS_PATH, load_settings  # noqa: E402


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


def main() -> None:
    ap = argparse.ArgumentParser(description="드론 중계 서버")
    ap.add_argument("backend", nargs="?", choices=["mock", "sim", "real"], help="생략하면 settings.toml 값")
    ap.add_argument("--config", default=str(DEFAULT_SETTINGS_PATH), help="settings.toml 경로")
    ap.add_argument("--host", help="기본: settings.toml [server] host")
    ap.add_argument("--port", type=int, help="기본: settings.toml [server] port")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    settings = load_settings(args.config)
    kind = args.backend or settings.backend
    host = args.host or settings.server.host
    port = args.port or settings.server.port

    backend = create_backend(kind, settings)
    app = create_app(settings, backend)

    print("=" * 60)
    print(f" 드론 중계 서버  backend={kind}")
    print(f" 설정 파일: {args.config}")
    print(" 앱에 입력할 주소:")
    for ip in local_ips() or ["<이 PC의 IP>"]:
        print(f"   ws://{ip}:{port}/ws")
    print(f" 확인용(브라우저): http://127.0.0.1:{port}/")
    print(" 끄려면 이 창에서 Ctrl+C")
    print("=" * 60)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
