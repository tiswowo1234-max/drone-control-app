"""v0.7 확인용 간단한 클라이언트 (앱 없이): 영상·사건·카메라 모드를 터미널에서 본다.

  - ws /ws        에 앱처럼 연결: incidents / incident / event / state.vision 변화를 출력, 1초마다 ping
  - ws /ws/video  에 연결: 받은 영상 장 수와 video_status 출력, 마지막 장을 파일로 저장(--save)
  - --switch esp32  처럼 주면 3초 뒤 set_vision_mode 명령을 보내 본다

사용 예 (server 폴더에서, 서버를 먼저 켠 뒤):
  .venv\\Scripts\\python.exe tools\\video_check.py
  .venv\\Scripts\\python.exe tools\\video_check.py --switch esp32 --seconds 15
  .venv\\Scripts\\python.exe tools\\video_check.py --save last.jpg
  .venv\\Scripts\\python.exe tools\\video_check.py --host 192.168.0.10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets


async def app_side(base: str, args, stats: dict) -> None:
    async with websockets.connect(f"{base}/ws") as ws:
        async def pinger():
            while True:
                await ws.send(json.dumps({"type": "ping", "ts": time.time()}))
                await asyncio.sleep(1)

        async def switcher():
            if args.switch:
                await asyncio.sleep(3)
                print(f"[보냄] set_vision_mode {args.switch}")
                await ws.send(json.dumps({"type": "cmd", "id": "chk-1", "cmd": "set_vision_mode", "mode": args.switch}))

        async def reader():
            last_vision = None
            async for text in ws:
                m = json.loads(text)
                t = m.get("type")
                if t == "config":
                    print(f"[config] api {m.get('api_version')}, backend {m.get('backend')}, "
                          f"vision_modes {m.get('vision_modes')}, 슬롯 {len(m.get('photo_slots') or [])}개")
                elif t == "incidents":
                    print(f"[incidents] 서버가 기억하는 사건 {len(m['items'])}개: {[i['id'] for i in m['items']]}")
                elif t == "incident":
                    i = m["incident"]
                    stats["incidents"] += 1
                    print(f"[incident] {i['id']} 슬롯 {i['slot']} {i['place']} 모드 {i['mode']} 사진 {base.replace('ws', 'http', 1)}{i['photo_url']}")
                elif t in ("event", "ack"):
                    print(f"[{t}] {m}")
                elif t == "state":
                    v = m.get("vision")
                    if v != last_vision:
                        print(f"[state.vision] {v}  (pos_source {m.get('pos_source')})")
                        last_vision = v

        await asyncio.gather(pinger(), switcher(), reader())


async def video_side(base: str, args, stats: dict) -> None:
    async with websockets.connect(f"{base}/ws/video", max_size=None) as ws:
        async for msg in ws:
            if isinstance(msg, bytes):
                stats["frames"] += 1
                stats["last"] = msg
            else:
                print(f"[video_status] {json.loads(msg)}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--switch", choices=["esp32", "tt_down"], help="3초 뒤 이 모드로 전환 명령")
    ap.add_argument("--save", help="마지막 영상 장을 저장할 파일 (예: last.jpg)")
    args = ap.parse_args()
    base = f"ws://{args.host}:{args.port}"
    stats = {"frames": 0, "incidents": 0, "last": None}

    async def ticker():
        while True:
            await asyncio.sleep(2)
            print(f"  ... 영상 {stats['frames']}장 받음")

    tasks = [asyncio.create_task(c) for c in (app_side(base, args, stats), video_side(base, args, stats), ticker())]
    done, _ = await asyncio.wait(tasks, timeout=args.seconds, return_when=asyncio.FIRST_EXCEPTION)
    for t in tasks:
        t.cancel()
    for t in done:
        if not t.cancelled() and t.exception():
            print(f"오류: {t.exception()!r} (서버가 켜져 있는지 확인)")
    print(f"끝: {args.seconds:g}초 동안 영상 {stats['frames']}장, 새 사건 {stats['incidents']}개")
    if args.save and stats["last"]:
        with open(args.save, "wb") as f:
            f.write(stats["last"])
        print(f"마지막 장 저장: {args.save}")


if __name__ == "__main__":
    asyncio.run(main())
