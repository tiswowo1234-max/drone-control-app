"""서버 동작 확인용 간단한 WebSocket 클라이언트 (앱 없이 테스트할 때).

    .venv\\Scripts\\python.exe tools\\ws_check.py                    # 기본 시나리오
    .venv\\Scripts\\python.exe tools\\ws_check.py --url ws://192.168.0.10:8000/ws
    .venv\\Scripts\\python.exe tools\\ws_check.py --goto 150 100      # 원하는 좌표로

시나리오: config 받기 → 금지 좌표 goto(거부 확인) → goto → 도착 대기 → return_home → 착륙 대기.
1초마다 ping을 보낸다 (안 보내면 서버가 자동 착륙시킨다).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    ap.add_argument("--goto", nargs=2, type=float, metavar=("X_CM", "Y_CM"), default=(120, 90))
    ap.add_argument("--timeout", type=float, default=90)
    args = ap.parse_args()

    async with websockets.connect(args.url) as ws:
        acks: dict[str, dict] = {}
        last_state: dict = {}

        async def pinger():
            while True:
                await ws.send(json.dumps({"type": "ping", "ts": time.time()}))
                await asyncio.sleep(1)

        async def reader():
            nonlocal last_state
            n = 0
            async for text in ws:
                m = json.loads(text)
                t = m.get("type")
                if t == "state":
                    last_state = m
                    n += 1
                    if n % 5 == 1:  # 1초에 한 줄만 출력
                        p = m["pos"] or {}
                        print(f"  state mode={m['mode']:<10} pos=({p.get('x_cm')}, {p.get('y_cm')}, {p.get('z_cm')}) "
                              f"bat={m['battery_pct']} target={m['target']} src={m['pos_source']}")
                elif t == "ack":
                    acks[m["id"]] = m
                    print("  ACK  ", json.dumps(m, ensure_ascii=False))
                else:
                    print(f"  {t.upper():5}", json.dumps(m, ensure_ascii=False))

        async def cmd(cid: str, **kw) -> dict:
            await ws.send(json.dumps({"type": "cmd", "id": cid, **kw}))
            for _ in range(50):
                if cid in acks:
                    return acks[cid]
                await asyncio.sleep(0.1)
            raise RuntimeError(f"ack 없음: {cid}")

        async def wait_mode(modes: set[str], what: str):
            t0 = time.time()
            await asyncio.sleep(0.5)
            while last_state.get("mode") not in modes:
                if time.time() - t0 > args.timeout:
                    raise RuntimeError(f"시간 초과: {what}")
                await asyncio.sleep(0.2)

        tasks = [asyncio.create_task(pinger()), asyncio.create_task(reader())]
        await asyncio.sleep(1)
        print("\n[1] 지도 밖 좌표 → OUT_OF_BOUNDS 거부되어야 함")
        a = await cmd("t-1", cmd="goto", x_cm=9999, y_cm=10)
        assert not a["ok"] and a["error"] == "OUT_OF_BOUNDS", a
        print("\n[2] emergency confirm 없음 → NOT_CONFIRMED")
        a = await cmd("t-2", cmd="emergency")
        assert not a["ok"] and a["error"] == "NOT_CONFIRMED", a
        x, y = args.goto
        print(f"\n[3] goto ({x}, {y}) → 이륙 후 이동")
        a = await cmd("t-3", cmd="goto", x_cm=x, y_cm=y)
        if not a["ok"]:
            raise SystemExit(f"goto 거부됨: {a}")
        await wait_mode({"hovering"}, "goto 도착")
        print("\n[4] return_home → 귀환 후 착륙")
        a = await cmd("t-4", cmd="return_home")
        await wait_mode({"idle"}, "귀환·착륙")
        print("\n확인 완료: config/state/ack/event가 정상입니다.")
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
