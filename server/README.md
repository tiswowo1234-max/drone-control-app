# 중계 서버 (앱 ⇄ 드론)

- 명세: `docs/api.md` v0.7 · 설정: `config/settings.toml` (코드에 숫자를 직접 쓰지 않는다)
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

## 3-1. 사건 사진·실시간 영상·카메라 모드 시험하기 (v0.7) — 드론·카메라 없이

2단계에서는 `vision/` 연결 프로그램이 ① 위치(`POST /vision/pose`) ② 사건 사진(`POST /vision/incident`)
③ 카메라 영상(`ws /vision/video`)을 서버에 보낸다. 아래 가짜 도구로 연결 프로그램 없이 서버를 확인할 수 있다.

| 가짜 도구 | 대신하는 것 | 하는 일 |
|---|---|---|
| `tools\fake_incident.py` | 연결 프로그램의 슬롯 촬영 | 슬롯(S1~S4) 사진을 한 장 보고한다 |
| `tools\fake_video.py` | 연결 프로그램의 영상 송신 | 움직이는 시험 영상을 보낸다. 앱이 카메라를 바꾸면 영상 색이 바뀐다 (esp32 = 컬러, tt_down = 흑백) |
| `tools\video_check.py` | 앱 | 사건 알림·영상 장 수·카메라 모드(`state.vision`)를 글자로 보여 준다 |

**PowerShell 창을 4개** 쓴다. 창 2~4는 모두 먼저 `cd C:\dev\drone-control-app\server`를 입력한다.

1. **창 1 — 서버 켜기**
   ```
   cd C:\dev\drone-control-app\server
   .\run_server.bat mock --vision
   ```
2. **창 2 — 가짜 영상 보내기**
   ```
   .venv\Scripts\python.exe tools\fake_video.py
   ```
   `보낸 장 50 (모드 tt_down)` 같은 줄이 5초마다 나오면 된다.
3. **창 3 — 확인용 클라이언트** (15초 동안 보고, 3초 뒤 카메라를 esp32로 바꿔 본다)
   ```
   .venv\Scripts\python.exe tools\video_check.py --switch esp32 --seconds 15 --save last.jpg
   ```
   확인할 것:
   - `[video_status] ... 'live': True` 와 `... 영상 N장 받음`의 N이 계속 늘어난다 → **영상 중계 OK**
   - `[state.vision] {'mode': 'esp32', ..., 'bridge_mode': 'tt_down'}` (전환 중) 다음에
     `'bridge_mode': 'esp32'` 줄이 나온다 → **카메라 모드 전환 OK** (창 2에도 `[서버] vision_mode = esp32`)
   - 끝나면 `last.jpg`가 생긴다. 더블클릭하면 초록 바탕(esp32 컬러) 그림이 보인다.
4. **창 4 — 가짜 사건 보고** (창 3이 도는 15초 안에)
   ```
   .venv\Scripts\python.exe tools\fake_incident.py --slot 4
   ```
   - 창 4: `응답: {'ok': True, 'id': 'inc-1'}`와 사진 주소가 나온다. **그 주소를 브라우저에 붙여 넣으면 시험 사진이 보인다.**
   - 창 3: `[incident] inc-1 슬롯 4 ...`와 `[event] ... INCIDENT_NEW` 가 나온다 → **사건 알림 OK**
   - 한 번 더 보내면 `'duplicate': True` (같은 슬롯은 `[incident] dedup_s`=30초 동안 한 번만). `--mode esp32`로 보내면 다른 카메라라 새 사건이 된다.
5. **합성 카메라용 참값 위치:** 브라우저에서 `http://127.0.0.1:8000/sim/truth` →
   `{"ok":true,"x_m":1.532,"y_m":0.702,"z_m":0.0,"yaw_deg":0.0}` (mock = 가짜 드론의 실제 위치, 처음엔 정문).
6. **상태 한눈에 보기:** 브라우저 `http://127.0.0.1:8000/`의 `vision_link`(현재 모드, 연결 프로그램 연결 여부, 영상 마지막 장 경과 초,
   보는 앱 수)와 `incidents`(사건 수).
7. 다 보면 각 창에서 `Ctrl+C`.

더 해 볼 것:
- `tools\fake_video.py --switch-delay 3` → 전환 버튼을 누른 뒤 3초 동안 앱/창 3에 "전환 중"(`bridge_mode`가 옛 모드)이 보인다.
- `tools\fake_video.py --camera-fail` → `camera_ok: False`.
- 창 2를 끄면 `[video] stale_s`(2초) 뒤 `video_status live: False`("카메라 영상이 없습니다"), `state.vision.bridge_connected: false`.

알아 둘 점:
- 카메라 모드(`esp32`/`tt_down`)는 **착지 상태나 호버링 중에만** 바꿀 수 있다(이동·이착륙 중이면 `BUSY`). 바꾸면 마지막 카메라 위치를 버리고,
  새 모드의 위치가 올 때까지 명령 누적 추정(`estimate`)으로 이어 간다. 처음 모드는 `settings.toml [bridge] default_mode`.
- `POST /vision/pose`에 `mode`를 넣으면 서버의 현재 모드와 다를 때 `WRONG_MODE`로 거부한다(생략하면 검사 안 함). `z_m`은 앱 표시용 높이.
- 실제 드론(real)은 모드에 맞춰 TT에 `downvision 1` → `streamon`(tt_down) 또는 `streamoff`(esp32)를 보낸다. 서버를 켠 뒤 드론이 연결될 때도 보낸다.
  TT 영상(UDP 11111)은 **연결 프로그램이 받는다.** 서버는 영상 포트를 열지 않는다. mock/sim은 명령을 보내지 않는다.
- mock + vision: 가짜 드론은 "서버가 아는 위치"(vision 값으로 맞춰짐)와 "실제 위치"(`/sim/truth`, 이동 명령만큼만 움직임)를 따로 가진다.
  합성 카메라는 실제 위치로 화면을 그리므로, 위치 인식이 틀려도 실제 위치가 그 틀린 값으로 끌려가지 않는다.
- 사건·사진은 메모리에만 둔다(최근 `[incident] keep`개). 서버를 다시 켜면 사라진다.

## 4. 테스트
```
cd C:\dev\drone-control-app\server
.venv\Scripts\python.exe -m pytest -q
```
안전 검사, 프로토콜(WebSocket), mock 비행 흐름, sim/real 공통 백엔드(가짜 Tello)를 검사한다. Unity는 필요 없다.
v0.7 기능(고도 고정, 카메라 모드 전환과 real의 streamon/downvision/streamoff, 사건 보고·중복·사진, 영상 중계·초당 장수 제한,
`/sim/truth`, 느린 영상 앱이 state·자동 착륙을 늦추지 않는지)은 `tests/test_v07.py`.

---

## 5. 동작 규칙 (명세에 없는 세부)

- 연결하면 `config` 1회 → `incidents`(최근 사건 목록) 1회 → 바로 `state` 1회 → 이후 `[server] state_hz`(5Hz)로 `state`.
- 앱은 1초마다 `ping`. 비행 중 `link_timeout_s`(3초) 동안 명령권 앱의 `ping`/`cmd`가 없으면 자동 착륙 + event `LINK_LOST_LANDING`.
- 명령은 **명령권 앱**(마지막에 연결한 앱)만 가능. 다른 앱의 `cmd` → ack `BUSY`. 새 앱이 연결되면 그 앱이 명령권을 갖는다.
- **명령권 넘겨받기:** 명령권 앱의 연결이 끊기면 남은 앱 중 가장 최근에 연결된 앱이 즉시 넘겨받고, 모든 앱에 event `CONTROLLER_CHANGED`.
  비행은 계속되고, 생존 타이머(`link_timeout_s`)는 넘겨받은 순간부터 새 명령권 앱의 `ping`으로 다시 센다. 남은 앱이 없으면 `link_timeout_s` 뒤 자동 착륙.
- `goto`: 착지 상태면 이륙부터. 이동 중 새 `goto`는 기존 이동을 취소하고 새 목표로. 이륙·착륙 중에는 `BUSY`.
- `stop`/`land`/`emergency`는 진행 중 명령을 취소하고 바로 처리한다.
  - sim: 시뮬레이터가 진행 중 이동을 끊지 못해서, 한 구간(`[sim] segment_cm`, 약 2초)이 끝난 뒤 멈춘다. `emergency`는 즉시.
  - real: **진행 중인 명령(이동 구간 등)이 있을 때만** SDK `stop`으로 끊고 그 명령이 끝나기를 기다린 뒤 처리한다.
    호버링 중(진행 중 명령 없음)이면 `stop` 없이 바로 `land`를 보낸다 — 멈춰 있는 TT에 `stop`과 `land`를 연달아 보내
    착륙이 늦거나 다시 눌러야 하던 문제를 줄이기 위해(2026-11-21 실물 시험).
- **real 유지 신호:** TT는 **15초 동안 명령을 못 받으면 스스로 착륙**한다. 그래서 비행 중 `[real] keepalive_s`(5초) 동안 다른 명령이 없으면
  `rc 0 0 0 0`(제자리, 응답 없음)을 보낸다(2026-11-21 실물 시험: 이륙 뒤 28초 무명령 → TT 자체 착륙).
- **real 자체 착륙 감지:** 비행 중인데 TT 높이(`h`)가 `[real] ground_h_cm`(10cm) 이하로 `ground_s`(1초) 이어지면 스스로 착륙한 것으로 보고
  `mode = idle` + event `DRONE_LANDED_ITSELF`. 바닥에 있는데 TT가 `land`를 `error`로 거절하면 실패 대신 '이미 착지'로 처리한다.
- **이동 기록:** `goto`마다 목표·현재 위치·위치 출처·구간을 로그에 남긴다. 목표가 20cm 이내라 움직이지 않으면 event `TARGET_TOO_CLOSE`.
- **로그 파일:** 서버 창에 나오는 로그는 `server\logs\server-날짜.log`에도 저장된다(하루 한 파일, 실행마다 `===== 서버 시작` 구분선).
  실물 시험에서 문제가 생기면 이 파일을 보면 된다. git에는 올리지 않는다.
- 출동(`takeoff`/`goto`)만 배터리 검사. `land`/`return_home`/`stop`은 배터리가 낮아도 받는다.
- 이미 그 상태인 명령(`takeoff` 중복, 착지 중 `land`/`stop`)은 ok만 돌려준다. 착지 상태 `return_home`은 ok + event `ALREADY_LANDED`.
- 추가 event 코드: `EMERGENCY_STOP`, `LOW_BATTERY`(비행 중 배터리 < min), `DRONE_CONNECTED`, `DRONE_DISCONNECTED`, `CMD_FAILED`, `CONTROLLER_CHANGED`, `BAD_REQUEST`(JSON 오류).
- 정수인 숫자는 JSON 정수로 보낸다 (`250.0` → `250`).
- **고도 고정 (`[limits] fixed_alt = true`):** `z_cm` 없는 `goto`와 `return_home`(귀환 이동)은 `default_alt_cm`으로 난다.
  `false`면 예전처럼 현재 고도(min~max로 맞춤).
- `set_vision_mode`: 명령권 앱만, `idle`·`hovering`(그리고 비상정지 후 바닥에 있을 때)만. 드론 카메라 명령이 실패하면(real) ack는 ok이고 event `CMD_FAILED`.
- 사건 중복: 같은 슬롯 + **같은 카메라 모드**가 `dedup_s` 안에 다시 오면 중복. 슬롯이 없으면 `same_place_m` 안의 위치.
  `lat`/`lon`을 안 보내면 서버가 `[geo]`로 계산해 넣는다.
- `/sim/truth`: real은 HTTP 400 `BAD_REQUEST`, sim에서 아직 시뮬레이터 상태를 못 읽었으면 400 `NOT_CONNECTED`. `yaw_deg`는 2장 기준(지도 위쪽 0, 시계 +).
- 영상: 앱마다 따로 보내며(초당 `[video] max_fps`, 최신 장만), 한 장 보내기가 5초 넘게 걸리는 앱은 연결을 끊는다.
  `[video] max_frame_kb`보다 크거나 JPEG가 아닌 장은 버린다(`GET /`의 `frames_dropped`).

## 6. 코드 구조
```
server/
  run_server.py / run_server.bat   실행 (인자: mock | sim | real)
  drone_server/
    settings.py      settings.toml 읽기·검증
    safety.py        안전 검사 (경계, 반경, 고도, 금지구역·경로, 배터리)
    controller.py    명령 처리, mode 관리, 감시(ping 끊김·배터리·연결)
    app.py           FastAPI: /ws, 상태 push, /vision/pose, /vision/incident, /vision/video, /ws/video, /sim/truth, 사진
    vision_link.py   위치 추적 모드(esp32/tt_down), 연결 프로그램 상태, 영상 중계(VideoRelay)
    incidents.py     사건 보고 검사·중복·메모리 보관
    positioning.py   위치 공급자(PositionProvider: 누적 추정·시뮬 참값·vision·미션패드), 좌표계 변환
    backends/
      base.py        백엔드 인터페이스
      mock.py        가짜 드론
      tello.py       djitellopy 공통: 목표 지도 좌표 → 상대 이동(go) 구간 분할
      sim.py         Unity 시뮬레이터 (가짜 djitellopy)
      real.py        실제 TT (진짜 djitellopy)
  tools/ws_check.py         앱 없이 확인하는 클라이언트
  tools/fake_vision.py      가짜 카메라 위치 추정 송신기 (POST /vision/pose)
  tools/fake_incident.py    가짜 사건 사진 보고 (POST /vision/incident)
  tools/fake_video.py       가짜 영상 연결 프로그램 (ws /vision/video, 모드 전환에 답함)
  tools/video_check.py      영상·사건·카메라 모드 확인 클라이언트
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
