# 중계 서버 (앱 ⇄ 드론)

- 명세: `docs/api.md` v0.2 · 설정: `config/settings.toml` (코드에 숫자를 직접 쓰지 않는다)
- 구조: 앱 ⇄ (WebSocket `ws://<PC IP>:8000/ws`) ⇄ 이 서버 ⇄ 백엔드 `mock` | `sim` | `real`

| 백엔드 | 무엇과 연결? | 언제 쓰나 |
|---|---|---|
| `mock` | 없음 (Python이 드론 흉내) | 앱 화면 개발·테스트. Unity 필요 없음 |
| `sim` | Unity 시뮬레이터 `UnityProject.exe` (127.0.0.1:9911) | 비행 로직 검증 |
| `real` | 실제 RoboMaster TT (djitellopy) | 실기체 (아직 미검증) |

---

## 1. 처음 실행하기 (mock)

1. 탐색기에서 `C:\dev\drone-control-app\server` 폴더를 연다.
2. 주소창에 `powershell`을 입력하고 Enter → 그 폴더에서 PowerShell 창이 열린다.
3. 다음을 입력한다.
   ```
   .\run_server.bat mock
   ```
   - 처음에는 가상환경(`.venv`)을 만들고 패키지를 설치하느라 1~2분 걸린다.
   - **Windows 방화벽 창이 뜨면 "개인 네트워크"에 체크하고 "허용"** (폰이 접속하려면 필요. 공용은 체크하지 않아도 된다).
4. 창에 이런 안내가 나오면 켜진 것이다.
   ```
    드론 중계 서버  backend=mock
    앱에 입력할 주소:
      ws://192.168.0.10:8000/ws
   ```
   이 주소를 앱의 서버 주소에 입력한다. (폰과 PC가 같은 Wi-Fi/핫스팟에 있어야 한다)
5. 끄려면 그 창에서 `Ctrl + C`.

> 인자 없이 `run_server.bat`(더블클릭)만 하면 `settings.toml`의 `[backend] mode` 값으로 켜진다.

### 서버가 살아 있는지 확인
- 같은 PC 브라우저에서 `http://127.0.0.1:8000/` → `{"ok":true, ...}` 가 보이면 정상.
- 폰 브라우저에서 `http://<PC IP>:8000/` 이 안 열리면 방화벽(개인 네트워크 허용) 또는 Wi-Fi가 다른 경우다.

### 앱 없이 동작 확인 (WebSocket 클라이언트)
서버를 켠 채로 PowerShell 창을 **하나 더** 열고:
```
cd C:\dev\drone-control-app\server
.venv\Scripts\python.exe tools\ws_check.py
```
config → 지도 밖 좌표 거부(OUT_OF_BOUNDS) → confirm 없는 비상정지 거부(NOT_CONFIRMED) → goto 비행 → 귀환·착륙이
차례로 출력되고 마지막에 `확인 완료`가 나오면 정상이다. 좌표를 바꾸려면 `--goto 150 100`.

## 2. 시뮬레이터로 실행하기 (sim)

1. `RoboMasterTT-Simulator beta\UnityProject.exe`를 더블클릭해 켠다 (조원에게 받은 빌드 파일).
   왼쪽 위 패널에 `Mode: Grounded`가 보이면 준비 완료.
2. 서버 폴더 PowerShell에서 `.\run_server.bat sim`
3. `ws_check.py` 또는 앱으로 출동시킨다. 시뮬레이터 화면에서 드론이 날아가는 것이 보인다.
   - 시뮬레이터 세계는 지도보다 훨씬 크다 (지도 1cm ≈ 시뮬레이터 28.5cm). 지도 끝에서 끝까지 1분 넘게 걸리고,
     고도 80cm는 시뮬레이터에서 약 23m라 이륙 후 한참 올라간다. 정상이다.
   - 시뮬레이터 드론의 시작 위치는 지도 약 (124.7, 116.6)cm 이다 (home = 정문 (153.2, 70.2)이 아님).
     처음에 `return_home`을 한 번 보내면 정문에 착륙하고, 그 뒤로는 정문에서 이륙한다.
   - 시뮬레이터 높이 센서가 약 29.8m에서 멈춰서 `[sim] max_height_cm = 2800`으로 고도를 지도 약 98cm까지 제한한다.
   - 지도 아래쪽 띠(y < 6.8cm)와 위쪽 띠(y > 244.1cm)는 시뮬레이터 바닥 밖이다. 거기서 착륙하지 말 것.
   - 비상정지 후 다음 이륙 때 서버가 시뮬레이터를 자동 리셋한다 (드론이 시작 위치로 돌아감).
4. 위치 표시 방식: `[sim] pos_source = "estimate"`(명령 누적 추정, 실제 드론과 같은 조건) 또는 `"sim_truth"`(시뮬레이터 참값).

> 영상(cv2, numpy)이 설치되지 않아도 sim 제어는 된다. 서버가 빈 모듈로 대신한다.

## 3. 카메라 위치 추정 입력 시험하기 (vision) — 드론 없이

조원의 카메라 위치 추정 코드는 위치를 `POST /vision/pose`로 서버에 보낸다 (docs/api.md 7장).
조원 코드 대신 `tools/fake_vision.py`가 가짜 위치를 보내서, 앱의 드론 아이콘이 그 위치를 따라가는지 확인할 수 있다.

1. **서버를 vision 모드로 켠다** (PowerShell 창 1):
   ```
   cd C:\dev\drone-control-app\server
   .\run_server.bat mock --vision
   ```
   창에 `위치 입력(vision) 켜짐`이 보이면 된다. (이미 다른 서버가 8000번을 쓰고 있으면 그 창에서 Ctrl+C로 먼저 끈다)
2. **앱을 켜서 서버에 연결**한다 (평소처럼). 드론 아이콘은 정문에 있다.
3. **가짜 위치를 보낸다** (PowerShell 창 2):
   ```
   cd C:\dev\drone-control-app\server
   .venv\Scripts\python.exe tools\fake_vision.py --pattern circle
   ```
   - 창에 `[1] (1.750, 1.250) m → 서버 수신 OK`처럼 줄이 계속 나온다.
   - **앱에서 드론 아이콘이 지도 가운데에 원을 그리며 움직이면 성공.** 위치 출처(pos_source)는 `vision`.
4. 창 2에서 `Ctrl+C`로 송신을 멈추면, 1초(`[vision] stale_s`) 뒤 위치 출처가 `estimate`로 바뀌고 아이콘은 마지막 위치에 머문다.
5. 다른 패턴:
   | 명령 (창 2) | 확인할 것 |
   |---|---|
   | `tools\fake_vision.py` | 정문 (1.532, 0.702)m에 정지 |
   | `tools\fake_vision.py --pattern waypoints --points "1.5,0.7 0.6,2.0 2.2,0.4"` | 지점들을 차례로 이동 |
   | `tools\fake_vision.py --pattern circle --noise 0.03` | 아이콘이 조금씩 떨림 (추정 잡음 흉내) |
   | `tools\fake_vision.py --outside` | `지도 밖 좌표라 거부됨` 출력, 아이콘 그대로 |
   | `tools\fake_vision.py --confidence 0.3` | settings.toml `[vision] min_confidence`를 0.5로 바꾸면 `신뢰도가 낮아 거부됨` |
   | `tools\fake_vision.py --hz 1` | 1초에 한 번 (stale_s 1초와 비슷해 estimate/vision이 번갈아 보일 수 있음) |
6. mock + vision에서 앱으로 출동(goto)하면 가짜 드론은 **마지막 vision 위치에서 출발**해 날아간다. 송신 중이면 아이콘은 vision 값을 따른다.

- vision은 mock·sim·real 모두에서 켤 수 있다: `run_server.bat sim --vision`, 또는 settings.toml의 `[mock]`/`[sim]`/`[real]` `pos_source = "vision"`.
- 브라우저 `http://127.0.0.1:8000/` 의 `"vision"` 항목에서 마지막 값을 받은 지 몇 초인지(`last_age_s`) 볼 수 있다.

### 조원용: 위치 추정 코드 연동
- 형식·예제 코드: `tools/fake_vision.py` 맨 위 주석과 `send_pose()` 함수 (표준 라이브러리만 사용, 그대로 복사 가능).
- 좌표: **m 단위, 지도 왼쪽 아래 (0, 0), x 오른쪽, y 위쪽, 0~2.5 m.** 방향 `yaw_deg`는 지도 위쪽 0°, 시계방향 +.
- 보내기: `POST http://<서버 IP>:8000/vision/pose`, 본문 `{"x_m": 1.53, "y_m": 0.70, "yaw_deg": 0, "confidence": 0.9, "ts": time.time()}`
  (`x_m`, `y_m`만 필수). 같은 라즈베리파이에서 돌리면 `http://127.0.0.1:8000`.
- 응답: `{"ok": true}` 또는 HTTP 400 `{"ok": false, "error": "OUT_OF_BOUNDS" | "LOW_CONFIDENCE" | "BAD_REQUEST", "message": ...}`.
- 빈도는 자유. 서버는 최신 값만 쓰고, `stale_s`(기본 1초) 동안 값이 없으면 명령 누적 추정으로 이어 간다.
  → 1초에 2번 이상 보내면 앱에 계속 `vision`으로 표시된다. 더 드물게 보내면 `[vision] stale_s`를 늘린다.
- `ts`(사진 찍은 시각)를 보내면 순서가 뒤바뀐 오래된 값은 서버가 무시한다.
- 실제 드론에서 쓰려면 `settings.toml`의 `[real] pos_source = "vision"`. 서버는 구간 이동마다 이 위치를 다시 읽어 목표로 보정한다.

## 4. 테스트
```
cd C:\dev\drone-control-app\server
.venv\Scripts\python.exe -m pytest -q
```
안전 검사, 프로토콜(WebSocket), mock 비행 흐름, sim/real 공통 백엔드(가짜 Tello)를 검사한다. Unity는 필요 없다.

---

## 5. 동작 규칙 (명세에 없는 세부)

- 연결하면 `config` 1회 → 바로 `state` 1회 → 이후 `[server] state_hz`(5Hz)로 `state`.
- 앱은 1초마다 `ping`. 비행 중 `link_timeout_s`(3초) 동안 제어 앱의 `ping`/`cmd`가 없으면 자동 착륙 + event `LINK_LOST_LANDING`.
- 명령은 **마지막에 연결한 앱**만 가능. 다른 앱의 `cmd` → ack `BUSY`.
- `goto`: 착지 상태면 이륙부터. 이동 중 새 `goto`는 기존 이동을 취소하고 새 목표로. 이륙·착륙 중에는 `BUSY`.
- `stop`/`land`/`emergency`는 진행 중 명령을 취소하고 바로 처리한다.
  - sim: 시뮬레이터가 진행 중 이동을 끊지 못해서, 한 구간(`[sim] segment_cm`, 약 2초)이 끝난 뒤 멈춘다. `emergency`는 즉시.
  - real: SDK `stop` 명령으로 즉시 멈춘다(예정).
- 출동(`takeoff`/`goto`)만 배터리 검사. `land`/`return_home`/`stop`은 배터리가 낮아도 받는다.
- 이미 그 상태인 명령(`takeoff` 중복, 착지 중 `land`/`stop`)은 ok만 돌려준다. 착지 상태 `return_home`은 ok + event `ALREADY_LANDED`.
- 추가 event 코드: `EMERGENCY_STOP`, `LOW_BATTERY`(비행 중 배터리 < min), `DRONE_CONNECTED`, `DRONE_DISCONNECTED`, `CMD_FAILED`, `CONTROLLER_CHANGED`, `BAD_REQUEST`(JSON 오류).
- 정수인 숫자는 JSON 정수로 보낸다 (`250.0` → `250`).

## 6. 코드 구조
```
server/
  run_server.py / run_server.bat   실행 (인자: mock | sim | real)
  drone_server/
    settings.py      settings.toml 읽기·검증
    safety.py        안전 검사 (경계, 반경, 고도, 금지구역·경로, 배터리)
    controller.py    명령 처리, mode 관리, 감시(ping 끊김·배터리·연결)
    app.py           FastAPI WebSocket /ws, 상태 push, POST /vision/pose
    positioning.py   위치 공급자(PositionProvider: 누적 추정·시뮬 참값·vision·미션패드), 좌표계 변환
    backends/
      base.py        백엔드 인터페이스
      mock.py        가짜 드론
      tello.py       djitellopy 공통: 목표 지도 좌표 → 상대 이동(go) 구간 분할
      sim.py         Unity 시뮬레이터 (가짜 djitellopy)
      real.py        실제 TT (진짜 djitellopy)
  tools/ws_check.py         앱 없이 확인하는 클라이언트
  tools/fake_vision.py      가짜 카메라 위치 추정 송신기 (POST /vision/pose)
  tools/sim_calibrate.py    시뮬레이터 좌표 보정값 계산
  tests/
```

## 7. 실제 드론(real) 연동 — 조원용

### 실행
```
cd server
.venv\Scripts\python.exe -m pip install djitellopy
.venv\Scripts\python.exe run_server.py real
```
- PC(또는 라즈베리파이)는 TT의 Wi-Fi에 연결돼야 한다. 앱은 같은 기기의 다른 네트워크 인터페이스로 접속해야 하므로
  라즈베리파이는 Wi-Fi 2개(TT용 + 폰 핫스팟용) 또는 유선 구성이 필요하다.
- 드론을 **home 위치에 지도 위쪽(+y)을 보도록** 놓고 켠다 (추정 위치의 시작점·방향 가정).
- `[real] speed_cm_s`, `segment_cm`로 속도와 구간 길이를 조정한다.
- **아직 실기체로 검증하지 않았다.** 처음에는 프로펠러 가드, 낮은 `max_alt_cm`, 짧은 거리로 시험할 것.

### 위치 공급자 끼워 넣기 (미션패드)
서버는 위치를 `PositionProvider`(drone_server/positioning.py)에게만 묻는다. 새 방식은 이 클래스를 상속해 4개 메서드를 구현하면 된다.

```python
class PositionProvider(ABC):
    source: str                                # state.pos_source 값 ("mission_pad" 등)
    def pose(self) -> Pose | None              # 지도 좌표계(cm) 현재 위치. 모르면 None
    def reset(self, pose: Pose) -> None        # 알고 있는 위치로 맞추기 (시작 시 home)
    def update(self, raw_state: dict) -> None  # 약 10Hz로 TT 상태 dict 전달 (mid, x, y, z, h, yaw, bat ...)
    def on_move(self, dx, dy, dz) -> None      # 상대 이동 명령 1구간이 끝났을 때 (지도 좌표 변위)
```
- 지도 좌표계: cm, 원점 = 지도 왼쪽 아래, x = 오른쪽, y = 위쪽, yaw는 지도 위쪽 0·시계방향 + (docs/api.md 2장).
- `MissionPadProvider`에 동작 가능한 단순 버전이 들어 있다: 패드가 보이면 `패드 위치 + 회전(패드 기준 x, y)`,
  안 보이면 명령 누적 추정으로 넘어간다. 패드 배치는 settings.toml에 적는다:
  ```toml
  [real]
  pos_source = "mission_pad"

  [[mission_pads]]
  mid = 1        # 패드 번호
  x_cm = 20      # 지도 좌표
  y_cm = 20
  rot_deg = 0    # 패드 x축이 지도 x축에서 반시계로 돈 각도
  ```
- TODO(조원): 패드 좌표축 방향 실측, 튀는 값 필터링, 패드 간 전환. 다른 방식(카메라 등)이면 새 Provider를 만들고
  `backends/real.py`의 `make_position_provider()`에 한 줄 추가하면 된다.
- 위치가 정확해지면 `tello.py`의 `move_to()`가 구간마다 위치를 다시 읽어 목표로 보정하므로(폐루프), 별도 수정 없이 정확도가 오른다.

### 라즈베리파이로 옮길 때
- 이 폴더를 그대로 복사하고 `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt djitellopy`.
- `run_server.bat` 대신 `.venv/bin/python run_server.py real`. Windows 전용 코드는 없다.

## 8. 시뮬레이터 좌표 보정 (`[sim_calibration]`)
- 시뮬레이터 장면의 건물 이름에 OSM ID가 들어 있어, 건물 75개를 OSM과 맞춰 정확히 구했다 (잔차 약 0.2m, 시뮬레이터 기준).
- 결과(2.5m×2.5m 확정 지도): 시뮬레이터 = 실제의 약 1/10, **동서 거울 반전**(+x = 서쪽), 회전 거의 0°(0.03°), 지도 1cm = 시뮬레이터 0.285m.
- 시뮬레이터 빌드가 바뀌면 `tools/sim_calibrate.py`를 다시 실행해 값을 갱신한다 (실행법은 파일 맨 위 주석).
- 실측으로 확인(확정 지도): 시작 → return_home(정문 착륙 참값 153.4, 69.9) → goto (60, 200) 참값 (59.8, 200.3) → goto (220, 40) 참값 (220.4, 39.7) → return_home. 지도 밖 goto는 OUT_OF_BOUNDS로 거부.
