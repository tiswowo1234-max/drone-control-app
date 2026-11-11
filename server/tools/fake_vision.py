"""가짜 카메라 위치 추정 송신기 — 조원 위치 추정 코드 대신 서버에 위치를 보낸다 (docs/api.md 7장).

조원 코드 연동용 참고 예제이기도 하다. 표준 라이브러리만 쓴다 (라즈베리파이에 그대로 복사해 실행 가능).

────────────────────────── 보내는 형식 (이것만 맞추면 된다) ──────────────────────────
  POST http://<서버 IP>:8000/vision/pose        (같은 라즈베리파이면 http://127.0.0.1:8000)
  Content-Type: application/json
  본문:
    {
      "x_m": 1.53,          # 필수. 지도 왼쪽 아래가 0, 오른쪽이 +   (m, 0 ~ 2.5)
      "y_m": 0.70,          # 필수. 지도 왼쪽 아래가 0, 위쪽이 +     (m, 0 ~ 2.5)
      "yaw_deg": 0.0,       # 선택. 드론 방향: 지도 위쪽 0°, 시계방향 +. 없으면 서버가 드론 센서 값 사용
      "confidence": 0.92,   # 선택. 0~1. settings.toml [vision] min_confidence보다 낮으면 서버가 버림
      "ts": 1763000000.12   # 선택. 사진 찍은 시각(유닉스 초, time.time()). 이전 값보다 오래된 값은 무시됨
    }
  응답:
    200 {"ok": true}
    400 {"ok": false, "error": "OUT_OF_BOUNDS" | "LOW_CONFIDENCE" | "BAD_REQUEST", "message": "..."}
  빈도: 자유. 서버는 가장 최근 값만 쓴다. [vision] stale_s(기본 1초) 동안 값이 없으면
        서버는 명령 누적 추정으로 이어 가고 앱에 pos_source = "estimate"로 표시한다.
  서버 쪽 준비: run_server.bat mock --vision  (또는 settings.toml의 백엔드 pos_source = "vision")
─────────────────────────────────────────────────────────────────────────────────────

사용 예 (server 폴더에서):
  .venv\\Scripts\\python.exe tools\\fake_vision.py                         # 정문(1.532, 0.702)에 정지
  .venv\\Scripts\\python.exe tools\\fake_vision.py --pattern circle        # 지도 가운데에 원 그리기
  .venv\\Scripts\\python.exe tools\\fake_vision.py --pattern waypoints --points "1.5,0.7 0.6,2.0 2.2,0.4"
  .venv\\Scripts\\python.exe tools\\fake_vision.py --pattern circle --noise 0.03 --confidence 0.8
  .venv\\Scripts\\python.exe tools\\fake_vision.py --outside               # 지도 밖 좌표 → OUT_OF_BOUNDS 확인
  .venv\\Scripts\\python.exe tools\\fake_vision.py --url http://192.168.0.10:8000 --hz 2
끄려면 Ctrl+C.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
import urllib.error
import urllib.request


def send_pose(url: str, x_m: float, y_m: float, yaw_deg: float | None = None,
              confidence: float | None = None, ts: float | None = None, timeout: float = 2.0) -> dict:
    """위치 1개를 서버로 보낸다. 조원 코드에서 이 함수만 복사해 써도 된다.
    반환: 서버 응답 JSON (연결 실패 시 {"ok": False, "error": "CONNECTION", "message": ...})."""
    body: dict = {"x_m": round(x_m, 4), "y_m": round(y_m, 4)}
    if yaw_deg is not None:
        body["yaw_deg"] = round(yaw_deg, 2)
    if confidence is not None:
        body["confidence"] = confidence
    body["ts"] = ts if ts is not None else time.time()
    req = urllib.request.Request(url.rstrip("/") + "/vision/pose", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # 400: 서버가 거부한 이유가 본문에 있다
        try:
            return json.loads(e.read().decode("utf-8"))
        except ValueError:
            return {"ok": False, "error": f"HTTP {e.code}", "message": e.reason}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": "CONNECTION", "message": str(e)}


ERROR_KO = {
    "OUT_OF_BOUNDS": "지도 밖 좌표라 거부됨",
    "LOW_CONFIDENCE": "신뢰도가 낮아 거부됨",
    "BAD_REQUEST": "형식 오류 또는 서버 vision 꺼짐",
    "CONNECTION": "서버에 연결 안 됨 (서버가 켜져 있는지, 주소가 맞는지 확인)",
}


def parse_points(text: str) -> list[tuple[float, float]]:
    pts = []
    for tok in text.replace(";", " ").split():
        x, y = tok.split(",")
        pts.append((float(x), float(y)))
    if not pts:
        raise argparse.ArgumentTypeError("--points 예: \"1.5,0.7 0.6,2.0\"")
    return pts


def main() -> None:
    ap = argparse.ArgumentParser(description="가짜 카메라 위치 추정 송신기 (m 단위, 지도 왼쪽 아래 원점)")
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="서버 주소 (기본 http://127.0.0.1:8000)")
    ap.add_argument("--hz", type=float, default=5.0, help="1초에 몇 번 보낼지 (기본 5)")
    ap.add_argument("--pattern", choices=["point", "circle", "waypoints"], default="point",
                    help="point=한 점 정지, circle=원 그리기, waypoints=지점 목록 순회")
    ap.add_argument("--point", nargs=2, type=float, metavar=("X_M", "Y_M"), default=(1.532, 0.702),
                    help="point 위치 (기본 정문 1.532 0.702)")
    ap.add_argument("--center", nargs=2, type=float, metavar=("X_M", "Y_M"), default=(1.25, 1.25), help="원 중심")
    ap.add_argument("--radius", type=float, default=0.5, help="원 반지름 m (기본 0.5)")
    ap.add_argument("--period", type=float, default=20.0, help="원 한 바퀴 시간 초 (기본 20)")
    ap.add_argument("--points", type=parse_points, default=parse_points("1.532,0.702 0.6,2.0 2.2,1.6 2.2,0.4"),
                    help='waypoints 지점들 "x,y x,y ..." (m)')
    ap.add_argument("--speed", type=float, default=0.15, help="waypoints 이동 속도 m/s (기본 0.15)")
    ap.add_argument("--noise", type=float, default=0.0, help="위치에 더할 잡음 표준편차 m (예: 0.03)")
    ap.add_argument("--confidence", type=float, default=None, help="보낼 confidence 0~1 (생략하면 안 보냄)")
    ap.add_argument("--no-yaw", action="store_true", help="yaw_deg를 보내지 않음 (서버가 센서 yaw 사용)")
    ap.add_argument("--outside", action="store_true", help="지도 밖 좌표(3.0, 3.0)를 보내 OUT_OF_BOUNDS 확인")
    ap.add_argument("--count", type=int, default=0, help="이 횟수만큼 보내고 끝 (0=무한)")
    args = ap.parse_args()

    period = 1.0 / args.hz
    t0 = time.time()
    # waypoints: 구간 길이로 전체 경로를 만들어 시간에 따라 위치를 정한다 (마지막 → 처음으로 반복)
    pts = args.points
    segs = [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))] if len(pts) > 1 else [(pts[0], pts[0])]
    seg_len = [max(math.dist(a, b), 1e-9) for a, b in segs]
    total = sum(seg_len)

    print(f"송신 시작: {args.url}/vision/pose, {args.hz:g}Hz, 패턴={'지도 밖' if args.outside else args.pattern}. 끄려면 Ctrl+C")
    n = 0
    try:
        while True:
            t = time.time() - t0
            yaw: float | None = None
            if args.outside:
                x, y = 3.0, 3.0
            elif args.pattern == "point":
                x, y = args.point
                yaw = 0.0
            elif args.pattern == "circle":
                a = 2 * math.pi * t / args.period
                x = args.center[0] + args.radius * math.cos(a)
                y = args.center[1] + args.radius * math.sin(a)
                yaw = math.degrees(math.atan2(-math.sin(a), math.cos(a)))  # 반시계로 돌 때 진행 방향 (지도 위 0°, 시계 +)
            else:
                d = (t * args.speed) % total
                for (a_, b_), L in zip(segs, seg_len):
                    if d <= L:
                        k = d / L
                        x, y = a_[0] + (b_[0] - a_[0]) * k, a_[1] + (b_[1] - a_[1]) * k
                        yaw = math.degrees(math.atan2(b_[0] - a_[0], b_[1] - a_[1]))
                        break
                    d -= L
            if args.noise > 0:
                x += random.gauss(0, args.noise)
                y += random.gauss(0, args.noise)
            r = send_pose(args.url, x, y, None if args.no_yaw else yaw, args.confidence)
            n += 1
            if r.get("ok"):
                print(f"[{n}] ({x:.3f}, {y:.3f}) m → 서버 수신 OK")
            else:
                err = r.get("error", "?")
                print(f"[{n}] ({x:.3f}, {y:.3f}) m → 실패: {ERROR_KO.get(err, err)} ({r.get('message', '')})")
            if args.count and n >= args.count:
                break
            time.sleep(max(0.0, period - (time.time() - t0 - t) ))
    except KeyboardInterrupt:
        print("\n송신 종료")


if __name__ == "__main__":
    main()
