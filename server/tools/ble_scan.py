"""차(UGV) 블루투스 찾기 시험 — 서버를 켜기 전에 이 노트북이 차를 볼 수 있는지 확인한다.

    Mac:      server/.venv/bin/python server/tools/ble_scan.py
    Windows:  server\\.venv\\Scripts\\python.exe server\\tools\\ble_scan.py

Mac은 처음 실행할 때 "터미널이 블루투스를 사용하려고 합니다" 창이 뜬다 → 허용.
(거부했거나 창 없이 꺼지면: 시스템 설정 → 개인정보 보호 및 보안 → 블루투스 → 터미널 켜기)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bleak import BleakScanner  # noqa: E402

from drone_server.settings import load_settings  # noqa: E402


async def main() -> int:
    cfg = load_settings().car
    print(f"블루투스 장치를 8초 동안 찾습니다... (찾는 차 이름: {cfg.ble_name})")
    devs = await BleakScanner.discover(timeout=8.0)
    named = sorted((d for d in devs if d.name), key=lambda d: d.name)
    print(f"장치 {len(devs)}개 발견 (이름 있는 것 {len(named)}개)")
    for d in named:
        mark = "  ← 차" if d.name == cfg.ble_name else ""
        print(f"   {d.name}   {d.address}{mark}")
    if any(d.name == cfg.ble_name for d in named):
        print("\n[성공] 차를 찾았습니다. 서버를 켜면 자동으로 연결합니다.")
        return 0
    print("\n[못 찾음] 차 전원, 폰 Police Car 웹앱 연결 해제(한 곳만 연결됨), 노트북 블루투스를 확인하세요.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
