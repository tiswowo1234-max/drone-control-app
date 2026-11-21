"""가짜 영상 연결 프로그램 — vision/ 연결 프로그램 대신 서버에 시험 영상을 보낸다 (docs/api.md 8·11장).

하는 일:
  1) ws://<서버>/vision/video 에 연결한다 (연결 프로그램 자리).
  2) 움직이는 시험 그림을 JPEG로 만들어 초당 --fps장 보낸다.
       esp32 모드   = 컬러 320x240 (XIAO 흉내)
       tt_down 모드 = 흑백 320x240 (TT 하단 카메라 흉내)
  3) 서버가 {"type": "vision_mode"}를 보내면 그 모드로 바꾸고 {"type": "bridge_status"}로 답한다
     → 앱에서 카메라 전환 버튼을 누르면 영상 색이 바뀌는 것으로 모드 전환을 확인할 수 있다.

사용 예 (server 폴더에서, 서버를 먼저 켠 뒤):
  .venv\\Scripts\\python.exe tools\\fake_video.py
  .venv\\Scripts\\python.exe tools\\fake_video.py --fps 5
  .venv\\Scripts\\python.exe tools\\fake_video.py --switch-delay 3    # 전환에 3초 걸리는 척 (앱의 "전환 중" 표시 확인)
  .venv\\Scripts\\python.exe tools\\fake_video.py --camera-fail        # camera_ok = false 로 알림
  .venv\\Scripts\\python.exe tools\\fake_video.py --url ws://192.168.0.10:8000/vision/video
끄려면 Ctrl+C. 영상은 앱의 영상 화면 또는 tools\\video_check.py로 본다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time

try:
    import cv2
    import numpy as np
except ImportError as e:  # requirements.txt에 들어 있다
    raise SystemExit("opencv-python, numpy가 필요합니다: .venv\\Scripts\\python.exe -m pip install -r requirements.txt") from e

import websockets

W, H = 320, 240


def make_frame(mode: str, n: int, quality: int = 70) -> bytes:
    """n번째 시험 그림 (가운데를 도는 원 + 글자)."""
    t = n / 10.0
    if mode == "esp32":
        img = np.zeros((H, W, 3), np.uint8)
        img[:] = (60, 120, 40)  # 초록 바탕 (BGR)
        cx, cy = int(W / 2 + 90 * math.cos(t)), int(H / 2 + 70 * math.sin(t))
        cv2.circle(img, (cx, cy), 22, (0, 80, 255), -1)
        cv2.putText(img, f"ESP32 (color) #{n}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    else:
        img = np.full((H, W), 90, np.uint8)  # 흑백
        cx, cy = int(W / 2 + 90 * math.cos(t)), int(H / 2 + 70 * math.sin(t))
        cv2.rectangle(img, (cx - 18, cy - 18), (cx + 18, cy + 18), 230, -1)  # AprilTag 대신 네모
        cv2.putText(img, f"TT down (gray) #{n}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 255, 2)
    cv2.putText(img, time.strftime("%H:%M:%S"), (8, H - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 255 if img.ndim == 2 else (255, 255, 255), 1)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


async def run(args) -> None:
    mode = args.mode
    print(f"연결 중: {args.url}")
    async with websockets.connect(args.url, max_size=None) as ws:
        print("연결됨. Ctrl+C로 끕니다.")

        async def status(message: str) -> None:
            await ws.send(json.dumps({"type": "bridge_status", "mode": mode, "camera_ok": not args.camera_fail,
                                      "message": message}, ensure_ascii=False))

        await status("가짜 영상 송신 중")

        async def reader() -> None:
            nonlocal mode
            async for text in ws:
                if isinstance(text, bytes):
                    continue
                m = json.loads(text)
                if m.get("type") == "vision_mode":
                    want = m.get("mode")
                    print(f"[서버] vision_mode = {want}")
                    if args.no_reply:
                        continue
                    if args.switch_delay > 0:
                        await asyncio.sleep(args.switch_delay)
                    mode = want
                    await status(f"{want} 모드로 전환함 (가짜)")
                    print(f"  → bridge_status mode={mode} 보냄")

        async def sender() -> None:
            n = 0
            period = 1.0 / args.fps
            while True:
                n += 1
                await ws.send(make_frame(mode, n))
                if n % (int(args.fps) * 5 or 1) == 0:
                    print(f"  보낸 장 {n} (모드 {mode})")
                await asyncio.sleep(period)

        await asyncio.gather(reader(), sender())


def main() -> None:
    ap = argparse.ArgumentParser(description="가짜 영상 연결 프로그램 (ws /vision/video)")
    ap.add_argument("--url", default="ws://127.0.0.1:8000/vision/video")
    ap.add_argument("--fps", type=float, default=10)
    ap.add_argument("--mode", default="tt_down", choices=["esp32", "tt_down"], help="처음 모드 (서버가 곧 알려 준다)")
    ap.add_argument("--switch-delay", type=float, default=0, help="모드 전환에 걸리는 척하는 시간(초)")
    ap.add_argument("--no-reply", action="store_true", help="vision_mode를 받아도 답하지 않음 (계속 '전환 중')")
    ap.add_argument("--camera-fail", action="store_true", help="camera_ok = false 로 알림")
    args = ap.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("끝.")
    except (OSError, websockets.exceptions.WebSocketException) as e:
        print(f"연결 실패/끊김: {e}  (서버가 켜져 있는지 확인)")


if __name__ == "__main__":
    main()
