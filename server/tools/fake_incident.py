"""가짜 사건 보고 — vision/ 연결 프로그램 대신 슬롯 사진을 서버에 보고한다 (docs/api.md 9장).

슬롯 위치는 config/settings.toml의 [[photo_slots]]에서 읽는다. 사진은 시험용 그림을 만들어 보낸다
(--photo로 실제 JPEG 파일을 줄 수도 있다).

사용 예 (server 폴더에서, 서버를 먼저 켠 뒤):
  .venv\\Scripts\\python.exe tools\\fake_incident.py                    # 슬롯 S4, tt_down
  .venv\\Scripts\\python.exe tools\\fake_incident.py --slot 1 --mode esp32
  .venv\\Scripts\\python.exe tools\\fake_incident.py --slot 2 --repeat 2  # 두 번째는 duplicate: true
  .venv\\Scripts\\python.exe tools\\fake_incident.py --photo C:\\사진.jpg
  .venv\\Scripts\\python.exe tools\\fake_incident.py --url http://192.168.0.10:8000
응답의 photo 주소를 브라우저에 붙여 넣으면 사진이 보인다.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

SETTINGS = Path(__file__).resolve().parents[2] / "config" / "settings.toml"


def test_photo(slot: int, mode: str) -> bytes:
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise SystemExit("opencv-python이 필요합니다 (또는 --photo 파일.jpg)") from None
    img = np.full((320, 480, 3), (40, 40, 160), np.uint8)
    cv2.putText(img, f"TEST INCIDENT  S{slot}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
    cv2.putText(img, f"mode {mode}  {time.strftime('%H:%M:%S')}", (20, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (255, 255, 0), 2)
    if mode == "tt_down":
        img = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url.rstrip("/") + "/vision/incident", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")
    except OSError as e:
        return {"ok": False, "error": "CONNECTION", "message": f"{e} (서버가 켜져 있는지 확인)"}


def main() -> int:
    ap = argparse.ArgumentParser(description="가짜 사건 보고 (POST /vision/incident)")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--slot", type=int, default=4)
    ap.add_argument("--mode", default="tt_down", choices=["esp32", "tt_down"])
    ap.add_argument("--method", default="slot", choices=["slot", "photo_finder"])
    ap.add_argument("--photo", help="보낼 JPEG 파일 (없으면 시험 그림)")
    ap.add_argument("--repeat", type=int, default=1, help="같은 보고를 몇 번 보낼지 (중복 확인용)")
    args = ap.parse_args()

    slots = {s["slot"]: s for s in tomllib.loads(SETTINGS.read_text(encoding="utf-8")).get("photo_slots", [])}
    if args.slot not in slots:
        print(f"settings.toml에 슬롯 {args.slot}이 없습니다 (있는 슬롯: {sorted(slots)})")
        return 1
    s = slots[args.slot]
    cx, cy = (float(v) / 100 for v in s["center_cm"])  # cm → m
    photo = Path(args.photo).read_bytes() if args.photo else test_photo(args.slot, args.mode)
    body = {
        "ts": time.time(), "mode": args.mode, "method": args.method, "slot": args.slot, "place": s.get("name"),
        "x_m": round(cx, 4), "y_m": round(cy, 4),
        "drone": {"x_m": round(cx, 4), "y_m": round(cy - 0.03, 4), "z_m": 0.8},  # 슬롯 바로 위에서 찍은 척
        "photo_jpeg_b64": base64.b64encode(photo).decode(), "detection": None,
    }
    for i in range(args.repeat):
        r = post(args.url, body)
        print(f"[{i + 1}] 응답: {r}")
        if r.get("ok"):
            print(f"     사진: {args.url.rstrip('/')}/incidents/{r['id']}/photo.jpg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
