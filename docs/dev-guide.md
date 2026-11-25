# 개발자 가이드 — 구조 · 확장 지점 · 다음 작업 안내

**2026-11-25 기준.** 이 저장소에 코드를 더할 사람을 위한 안내서입니다. 다음 작업은 두 가지입니다.
- **UGV 추종 병합:** 드론이 지상 로봇(UGV)을 따라가는 코드를 병합합니다. → [8장](#8-ugv-추종-병합-안내)
- **시뮬레이터 + 앱 통합:** Unity 시뮬레이터와 앱을 합칩니다. → [9장](#9-시뮬레이터--앱-통합-안내)

| 알고 싶은 것 | 볼 문서 |
|---|---|
| 프로젝트 소개·실행 방법 | [README.md](../README.md) |
| 메시지·HTTP·WebSocket 형식 (**기준 문서**) | [docs/api.md](api.md) v0.7 |
| 조정 가능한 모든 숫자 | [config/settings.toml](../config/settings.toml) |
| 진척·시험 결과·남은 일 | [docs/progress.md](progress.md) |
| 결정과 그 근거 (날짜순) | [docs/decisions.md](decisions.md) |
| 서버 실행·동작 규칙 | [server/README.md](../server/README.md) |
| 위치 인식 연결 프로그램 | [vision/README.md](../vision/README.md) |
| 앱 코드 | [app/README.md](../app/README.md) |

---

## 1. 전체 구조

노트북 한 대에서 **프로세스 두 개**가 돌아갑니다. 중계 서버와 위치 인식 연결 프로그램입니다.
폰 앱은 그중 **중계 서버에만** 붙습니다.

```
                      ┌──────────────────────── 노트북 ─────────────────────────────┐
 드론 카메라 ──영상──▶│ vision/ 연결 프로그램 ──POST /vision/pose────▶ server/ 중계 서버 │──djitellopy──▶ 드론 (real)
  TT 하단 UDP 11111   │  (조원 위치 인식 코드)  ──POST /vision/incident─▶  (FastAPI)       │──가짜 djitellopy──▶ Unity (sim)
  XIAO ESP32 MJPEG    │                        ══ws /vision/video══════▶                  │   (또는 mock: 파이썬 흉내)
                      │                        ◀═ vision_mode, drone_state ═             │
                      └──────────────────────────────────────────────────┬──────────────┘
                                                        ws /ws (명령·상태) │ ws /ws/video (영상)
                                                                          ▼
                                                                  app/ 안드로이드 앱 (Flutter)
```

| 통로 | 방향 | 형식 | 명세 |
|---|---|---|---|
| `ws://<노트북>:8000/ws` | 앱 ⇄ 서버 | JSON (`config`·`state`·`ack`·`event`·`incident` / `cmd`·`ping`) | api.md 1~6장 |
| `ws://<노트북>:8000/ws/video` | 서버 → 앱 | 바이너리 JPEG + 텍스트 `video_status` | 10.2 |
| `GET /incidents/{id}/photo.jpg` | 앱 ← 서버 | JPEG | 10.1 |
| `POST /vision/pose` | 연결 프로그램 → 서버 | JSON, **m 단위** | 7장 |
| `POST /vision/incident` | 연결 프로그램 → 서버 | JSON + base64 사진 | 9장 |
| `ws /vision/video` | 연결 프로그램 ⇄ 서버 | JPEG ↑ / `vision_mode`·`drone_state` ↓ | 8·11장 |
| `GET /sim/truth` | 연결 프로그램 ← 서버 | 시험용 참값 위치 (mock/sim) | 12장 |
| `GET /` | 누구나 | 서버 상태 확인 | — |
| UDP 8889/8890/11111 | 서버·연결 프로그램 ⇄ TT | TT SDK 명령 / 상태 / 영상 | DJI SDK |
| TCP 127.0.0.1:9911 | 서버 ⇄ Unity | 가짜 djitellopy | 시뮬레이터 |

**원칙**
- 앱은 서버 API에만 의존합니다. 드론 종류(mock/sim/real)는 서버 설정으로만 바꿉니다.
- 안전 검사는 앱(미리 알림용)과 서버(**최종 방어선**) 양쪽에서 합니다. 서버는 앱의 검사를 믿지 않습니다.
- 숫자는 `config/settings.toml` 한 곳에 둡니다. 코드에 직접 쓰지 않고, `[임시]`/`[확정]` 표시를 남깁니다.

## 2. 좌표·단위·방향 약속 (가장 자주 틀리는 부분)

| 항목 | 약속 |
|---|---|
| 지도 좌표 | 바닥 인쇄 지도 2.5m × 2.5m. **원점은 지도 왼쪽 아래**, x는 오른쪽, y는 위쪽. 실제 동·북 방향이 아닙니다(지도는 약 0.97° 회전). |
| 단위 | 앱 ⇄ 서버는 **cm**(`x_cm`). `/vision/pose`·`/vision/incident`는 **m**(`x_m`, 조원 코드 기준). 서버가 변환합니다. |
| 우리 yaw | **지도 위쪽(+y) = 0°, 시계 방향 +**, −180~180 (api.md 2장) |
| 조원 yaw | "북쪽 기준 반시계". `vision/bridge/geometry.py`가 우리 yaw로 바꿔서 보냅니다. **조원 모듈마다 기준이 다르므로** 새 코드를 붙일 때 반드시 시험으로 확인하세요(`vision/tests/test_yaw.py` 참고). |
| home | 정문 = 지도의 빨간 점 (153.2, 70.2) cm |
| 실제 TT 이동 | SDK `go x y z speed`: x = 전진, y = **왼쪽**, z = 위 (기체 기준, cm, 각 축 20~500, 모두 ±20 이내면 거부) |
| Unity 시뮬레이터 이동 | `go a b c`: a = **오른쪽**, b = 전진 → `[sim] go_axes = "sim"` |
| Unity 좌표 | 실제의 약 1/10 축척, **동서 거울 반전**. 지도 1cm ≈ 시뮬레이터 0.285m. 변환은 `[sim_calibration]`과 `server/tools/sim_calibrate.py` |
| TT 방향 센서 | **전원을 켤 때 향한 방향이 0°**입니다. 서버는 "home에서 지도 위쪽을 보게 놓고 켰다"고 가정하고, 카메라로 보정합니다(`heading_from_vision`). |
| 높이 | real은 거리 센서(`tof`, 바닥까지 잰 값)를 씁니다. TT `h`(이륙 지점 기준 추정)는 실내에서 30cm까지 틀렸습니다. |

## 3. 실행 모드 조합

서버 백엔드와 위치 출처를 조합해 실행합니다. 기본값은 `config/settings.toml`에 있고, 실행 인자로 바꿀 수 있습니다.

| 백엔드 | 무엇을 움직이나 | 위치 출처 (`pos_source`) | 실행 |
|---|---|---|---|
| `mock` | 파이썬 가짜 드론 | `estimate`(자기 위치) / `vision` | `server\run_server.bat mock [--vision]` |
| `sim` | Unity 시뮬레이터 | `estimate` / `sim_truth`(시뮬 참값) / `vision` | `UnityProject.exe` 실행 후 `server\run_server.bat sim` |
| `real` | 실제 TT | `estimate` / `vision` / `mission_pad`(미사용) | `server\run_server.bat real --vision` |

- `vision` 위치는 연결 프로그램(`vision\run_bridge.bat`)이 보냅니다.
  - mock·sim 서버에서는 연결 프로그램이 **합성 카메라**를 씁니다. 조원의 지도 그림으로 드론 아래 화면을 만들어 실제 위치 인식 코드에 넣는 방식입니다.
  - 그래서 드론이나 카메라 없이도 전체 흐름을 시험할 수 있습니다.
- 간편 실행:
  - `start_demo_no_drone.bat`: mock --vision + 합성 카메라
  - `start_real_drone.bat`: real --vision + 실제 카메라
- 로그:
  - 서버: `server\logs\server-날짜.log`
  - 연결 프로그램: `vision\logs\bridge-날짜.log`
  - 둘 다 git에는 올리지 않습니다.

## 4. 중계 서버 내부 (`server/drone_server/`)

### 4.1 파일 지도
| 파일 | 역할 |
|---|---|
| `settings.py` | `settings.toml`을 읽어 dataclass(`Settings`, `Limits`, `TelloCfg`, `AlignCfg` …)로 만들고 검증합니다. |
| `app.py` | FastAPI 엔드포인트, 앱 연결 관리(`Hub`: 명령권 앱), 상태 push 루프, 드론 재연결 루프, `make_config_message` |
| `controller.py` | **명령 처리의 중심.** 안전 검사, `mode` 관리, 미션(asyncio Task), 감시 루프(앱 끊김·배터리·자체 착륙), 초기 정렬 `_align` |
| `safety.py` | `check_target`(경계·반경·고도·금지 구역), `check_battery`, `clamp_alt`. 실패하면 `SafetyError(code, message)` |
| `positioning.py` | 위치 공급자(`PositionProvider`), 좌표계 변환(`DroneFrame`/`SimFrame`) |
| `backends/base.py` | 드론 백엔드 인터페이스 `DroneBackend` |
| `backends/mock.py` | 가짜 드론 |
| `backends/tello.py` | sim·real 공통. 지도 목표를 `go` 구간으로 나누고, 목표를 바라보고, 유지 신호·자체 착륙 감지·방향 보정을 합니다. |
| `backends/sim.py`, `real.py` | 가짜 djitellopy / 진짜 djitellopy 연결, 위치 공급자 선택 |
| `vision_link.py` | 위치 추적 모드(esp32/tt_down), 연결 프로그램 상태, 영상 중계(`VideoRelay`), `drone_state` 전달 |
| `incidents.py` | 사건 보고 검사·중복 판정·메모리 보관 |
| `tags.py` | AprilTag 배치 읽기(연결 프로그램과 같은 파일), `nearest_tag` |
| `models.py` | `Pose`, `Telemetry`, `BackendError` |
| `serve.py`, `../run_server.py` | 실행 진입점, 로그 파일 |
| `../tools/` | 앱 없이 시험하는 도구: `ws_check.py`, `fake_vision.py`, `fake_incident.py`, `fake_video.py`, `video_check.py`, `sim_calibrate.py` |

### 4.2 명령 하나가 흐르는 길 (예: `goto`)
1. 앱이 `{"type":"cmd","id":"c-1","cmd":"goto","x_cm":46.8,"y_cm":89.1}`를 `/ws`로 보냅니다.
2. `app.py`의 `handle_message`가 명령권 앱인지 확인하고 `Controller.handle_cmd`를 부릅니다.
3. `handle_cmd`는 `COMMANDS`에 있는 이름인지 보고 `_c_goto`를 부릅니다. `_c_<명령>`이 하는 일:
   - 검사: 연결, 안전(`check_target`), 상태(`BUSY`), 배터리
   - 진행 중 미션 취소(`_cancel_mission`)
   - 새 미션 `_m_goto` 시작(`_start` → asyncio Task)
   - **곧바로 ack를 돌려줍니다.** 비행이 끝날 때까지 기다리지 않습니다.
4. `_m_goto`: 착지 상태면 `b.takeoff()`와 `_align()`을 먼저 하고, `mode = "moving"`으로 바꾼 뒤 `b.move_to(x, y, z)`를 부릅니다.
5. `TelloBackend.move_to`는 다음을 반복합니다.
   - 현재 위치(공급자)를 읽습니다.
   - 필요하면 목표 쪽으로 회전합니다(`_face_toward`).
   - `plan_step`으로 다음 구간을 정합니다(`segment_cm` 이하, 최소 20cm).
   - `go_xyz_speed`를 보냅니다.
   - 공급자에 이동량을 알립니다(`on_move`).
   - 구간마다 위치를 다시 읽으므로 **카메라 위치가 정확할수록 목표에 정확히 갑니다**(폐루프).
6. 끝나면 `mode = "hovering"`이 됩니다. 실패하면 `_run`이 `CMD_FAILED` event를 보냅니다.
7. 그동안 `state_loop`가 5Hz로 `state_message()`를 모든 앱에 보냅니다.

- `stop`·`land`·`emergency`는 진행 중 미션을 취소하고 바로 처리합니다. 모든 백엔드 메서드는 취소되면 가능한 한 빨리 멈춰야 합니다.
- **새 비행 동작은 "미션 하나 = async 함수 하나"로 만들면** 취소·우선순위·실패 알림을 그대로 물려받습니다.

### 4.3 `mode` 값
`idle` → `taking_off`(이륙·초기 정렬 중) → `hovering` ⇄ `moving` / `returning` → `landing` → `idle`. 비상정지는 `emergency`.
- 앱은 이 값으로 버튼을 켜고 끕니다.
- 새 동작(예: 추종)을 넣을 때 **새 mode 값을 만들면 앱과 명세도 고쳐야 합니다**(`FLIGHT_MODES`, api.md 3.2, 앱 `protocol.dart`·`map_screen.dart`).

### 4.4 위치가 흐르는 길
- 서버는 위치를 백엔드의 `PositionProvider.pose()`에게만 묻습니다. 결과는 지도 cm `Pose(x, y, z, yaw)`이고 `state.pos`로 나갑니다.
- 공급자 종류:
  | 공급자 | 위치 출처 |
  |---|---|
  | `DeadReckoningProvider` | 보낸 명령을 누적한 추정 |
  | `SimTruthProvider` | 시뮬레이터 참값 |
  | `VisionProvider` | `/vision/pose`로 받은 값. `stale_s`(1초) 동안 새 값이 없으면 누적 추정으로 이어 갑니다. |
  | `MissionPadProvider` | 미사용 |
- 새 위치 출처를 붙이려면 `PositionProvider`를 상속해 `pose`, `reset`, `update`, `on_move`를 구현합니다. 그다음 `real.py`·`sim.py`의 공급자 선택에 한 줄을 추가합니다.

### 4.5 감시 루프 (`Controller.watchdog_once`, 0.2초마다)
- 명령권 앱의 `ping`이 `link_timeout_s`(3초) 동안 없으면 **자동 착륙**합니다.
- 비행 중 배터리가 낮으면 `LOW_BATTERY`를 알립니다.
- 드론 연결이 바뀌면 알립니다. 다시 연결되면 초기 정렬을 다시 합니다.
- real에서 TT가 스스로 착륙하면 감지합니다(`DRONE_LANDED_ITSELF`).
- real 유지 신호는 백엔드가 직접 보냅니다(`_maybe_keepalive`, 5초 무명령 시 `rc 0 0 0 0`).

## 5. 위치 인식 연결 프로그램 (`vision/`)

| 파일 | 역할 |
|---|---|
| `bridge/app.py` | 본체 `Bridge`. 스레드 구성: 입력 · 처리(위치 계산 → POST, 슬롯 촬영) · 영상 송신 · HTTP |
| `bridge/sources.py` | 입력: 합성 카메라, XIAO MJPEG, TT UDP H.264(ffmpeg), 파일. **최신 한 장만** 보관합니다(`LatestFrame`). |
| `bridge/localizers.py` | 모드별 위치 계산. 조원 코드를 부르고, 화면 가운데 영역 태그만 쓰고, 기울기·높이를 검사하고, yaw를 바꿉니다. 결과는 `PoseResult`입니다. |
| `bridge/slots.py` | 슬롯 촬영(호버링 중 슬롯 영역을 잘라 펴기) / 조원 사진 찾기 |
| `bridge/geometry.py` | 좌표·투영·yaw 변환 |
| `bridge/server_link.py` | HTTP 송신 큐, `ws /vision/video` 연결 |
| `bridge/synthetic.py` | 합성 카메라 |
| `bridge/config.py` | `settings.toml` 읽기 (서버와 같은 파일) |
| `teammate/` | 조원 원본(`drone-localization-main`)에서 복사한 사본. 출처·고친 곳은 `teammate/SOURCE.md` |
| `data/maps/` | 확장 태그 배치(슬롯 보드, 현재 꺼 둠) |
| `print/`, `tools/` | 인쇄용 태그 PDF와 그것을 만드는 스크립트 |

**조원 코드를 다룰 때의 규칙**
- 조원 원본 폴더(`C:\dev\drone-localization-main` 등)는 수정하지 않습니다. 필요한 것만 복사해 `SOURCE.md`에 출처·날짜·고친 곳을 적습니다.
- `.env`나 API 키는 절대 복사하지 않습니다.

## 6. 앱 (`app/`, Flutter)
- 파일별 역할은 [app/README.md](../app/README.md)를 보세요.
- 핵심 구조:
  - `DroneClient`(`lib/drone_client.dart`)가 `/ws` 연결·재연결·ping·명령·ack를 맡습니다.
  - 화면(`map_screen.dart`, `map_view.dart`)은 `DroneClient`의 상태만 그립니다.
  - 메시지 형식은 `lib/protocol.dart`, 설정은 서버의 `config` 메시지(`lib/config.dart`)에서 받습니다.
- 앱은 위치를 계산하지 않습니다. 화면 픽셀 ⇄ 지도 cm 변환(`map_coords.dart`)과 미리 하는 안전 검사(`safety.dart`)만 합니다.

## 7. 자주 하는 작업 레시피

### 설정값 하나 추가
1. `config/settings.toml`에 키를 넣고 주석에 `[임시]`/`[확정]`과 이유를 적습니다.
2. 서버: `settings.py`의 해당 dataclass에 필드와 기본값을 넣습니다. 섹션 통째로 읽는 `_sub`가 자동으로 채웁니다.
3. 연결 프로그램도 쓰면 `vision/bridge/config.py`에 넣습니다.
4. 앱도 알아야 하면 `app.py` `make_config_message`에 넣습니다. 이때 api.md 3.1과 앱 `config.dart`도 함께 고칩니다.

### 앱 명령(`cmd`) 하나 추가
1. `controller.py`:
   - `COMMANDS`에 이름을 넣습니다.
   - `_c_<이름>`(검사, 미션 시작)과 `_m_<이름>`(실제 비행)을 만듭니다.
   - 실패는 `SafetyError("코드", "한국어 설명")`으로 던집니다.
2. 새 오류 코드나 event 코드는 api.md 3.4·5장 표에 추가합니다.
3. 앱: `drone_client.dart`에 `sendCommand('<이름>', {...})` 메서드를 추가하고, `map_screen.dart`에 버튼을 답니다.
4. 시험: `server/tests/`에 mock 백엔드로 시험을 추가합니다(`test_mock_flow.py`, `test_align_face.py` 참고).
5. api.md 4.1 표와 맨 아래 **변경 기록**에 버전(v0.8 …)을 올려 적습니다.

### 외부 프로그램이 서버로 데이터를 넣는 통로 추가 (예: UGV 위치)
- `app.py`의 `@app.post("/vision/pose")` 구현을 본뜹니다. 형식 검사 → 실패 시 `{"ok": false, "error", "message"}`, 성공 시 저장.
- 시험 도구는 `server/tools/fake_vision.py`를 본떠 만듭니다.

### 테스트
```powershell
cd C:\dev\drone-control-app\server; .venv\Scripts\python.exe -m pytest -q      # 127개 (2026-11-25)
```
```powershell
cd C:\dev\drone-control-app\vision; .venv\Scripts\python.exe -m pytest -q      # 39개 (+ -m slow 측정)
```
```powershell
cd C:\dev\drone-control-app\app; C:\dev\flutter\bin\flutter.bat test          # 63개 (2026-11-21)
```
- 서버 테스트에는 Unity도 드론도 필요 없습니다(가짜 Tello 사용).
- 기능을 고치면 해당 테스트를 함께 추가하고, **세 묶음이 모두 통과하는지** 확인한 뒤 커밋합니다.

## 8. UGV 추종 병합 안내

> 아래는 지금 코드 구조에 맞춘 **제안**입니다. UGV 담당 조원의 코드·하드웨어를 보고 확정한 뒤, 결정은 `docs/decisions.md`에 기록합니다.

### 8.1 먼저 정할 것 (조원에게 확인)
| 질문 | 왜 중요한가 |
|---|---|
| UGV 위치를 **누가** 아는가? (UGV 자체 주행기록 / 드론 카메라가 UGV 위의 표식(AprilTag 등)을 봄 / 바닥 고정 카메라) | 입력 통로와 담당 코드가 달라집니다. 드론 카메라로 본다면 `vision/`에 검출을 추가합니다. |
| 위치의 좌표계·단위·yaw 기준 | 우리 지도 좌표(왼쪽 아래 원점, cm 또는 m, yaw 지도 위쪽 0·시계 +)로 맞춰야 합니다(2장). |
| 위치를 보내는 빈도·지연 | 추종 반응 속도와 "UGV 놓침" 판정 시간을 정합니다. |
| 추종 방식: UGV 바로 위 / 일정 거리 뒤 / 일정 고도 | 목표점 계산식이 달라집니다. |
| 시작·종료: 앱 버튼? 자동? | 새 `cmd`와 앱 화면이 필요한지가 정해집니다. |
| UGV를 놓치거나 지도 밖으로 나가면? | 권장: 호버링 + 앱 알림. 이후 착륙 여부를 정합니다. |
| UGV 통신 방식 (Wi-Fi HTTP / ROS / 시리얼) | 노트북에서 받을 방법을 정합니다. |
| 시뮬레이터에 UGV가 있는가? | 드론 없이 시험할 수 있는지가 달라집니다(9장). |

### 8.2 권장 연결 구조
```
UGV 위치 출처 ──POST /ugv/pose (7장과 같은 형식, m)──▶ 서버: UgvTracker(최신 값·나이)
                                                            │
앱 ──cmd follow_start / stop──▶ Controller._c_follow_start ─▶ 미션 _m_follow (반복)
                                                            │   목표 = UGV 위치 + 오프셋
                                                            │   check_target 통과 시 backend 이동
state.ugv {x_cm, y_cm, yaw_deg, age_s} ◀────────────────────┘   UGV 놓침 → 호버링 + event UGV_LOST
```
- **미션 하나로** 만듭니다(`_m_follow`). 그러면 `stop`·`land`·`emergency`·앱 끊김 자동 착륙이 그대로 우선 처리됩니다.
  `mode`는 새 값 `following`을 권장합니다.
- 목표점마다 서버의 `check_target`을 통과시킵니다(지도 경계·반경·고도).
- UGV 위치는 `positioning.py`의 `VisionProvider`처럼 "마지막 값 + 받은 시각"으로 보관합니다. 새 위치 공급자로 만들 필요는 없습니다. 드론 위치가 아니라 **목표**이기 때문입니다.
- 앱: 지도에 UGV 아이콘(`state.ugv`), 추종 시작·멈춤 버튼을 둡니다. api.md를 v0.8로 올려 3.2 `state.ugv`, 4.1 `follow_*`, 3.4 event 코드를 적습니다.

### 8.3 드론 쪽 제약 (실물에서 확인된 것)
- **`go`는 한 구간이 끝날 때까지 다음 명령을 받지 않습니다.** 현재 15cm/s, 구간 50cm면 약 4초입니다. 계속 움직이는 UGV를 `move_to`로 쫓으면 반응이 느립니다.
- **`go`는 모든 축이 ±20cm 이내면 거부됩니다**(`MIN_GO_CM`). 가까이 따라붙은 상태의 작은 보정은 `go`로 할 수 없습니다.
- 그래서 연속 추종에는 **속도 명령 `rc 좌우 앞뒤 상하 회전`**(djitellopy `send_rc_control`)이 맞습니다.
  - 실제 TT와 Unity의 가짜 djitellopy 모두 `send_rc_control`을 지원합니다(가짜는 `rc` 문자열로 처리).
  - 현재 `DroneBackend`에는 속도 명령이 없습니다. 추가 방법:
    1. `base.py`에 예를 들어 `async def set_velocity(vx_cm_s, vy_cm_s, vz_cm_s, yaw_rate)`(지도 기준)를 추가합니다.
    2. `tello.py`에서 지도 기준 → 기체 기준(`frame.map_to_drone` + 현재 yaw)으로 바꿔 `rc`를 보내고, `mock.py`에도 구현합니다.
    3. 속도 상한은 `settings.toml`에 둡니다.
    4. 경계 근처에서는 0으로 멈추게 합니다.
  - 속도 명령은 응답이 없고 계속 보내야 하므로 0.1~0.2초마다 보내고, 멈출 때는 반드시 `rc 0 0 0 0`을 보냅니다. 유지 신호(15초 자동 착륙 방지)는 이것으로 자연히 해결됩니다.
- 진행 중 `go`를 끊는 `stop`은 real만 즉시 됩니다. 시뮬레이터는 구간이 끝날 때까지(약 2초) 못 끊습니다.
- `[real] face_target`(이동 전 목표 쪽으로 회전)은 남은 거리 30cm 이상일 때만 동작합니다. 추종처럼 짧게 자주 움직이면 자동으로 건너뜁니다.
- 드론 위치 자체가 부정확하면(카메라 위치가 없을 때 누적 추정) 추종도 어긋납니다. 실물에서는 카메라 위치 상태(`state.pos_source`)를 함께 보세요.

### 8.4 시험 순서 (권장)
1. `server/tools/fake_ugv.py`(새로 작성)를 만듭니다. 원이나 직선으로 움직이는 가짜 UGV 위치를 `POST /ugv/pose`로 보냅니다.
2. mock 서버로 앱에서 추종 시작·멈춤 → 드론 아이콘이 따라가는지, `stop`·앱 끄기(자동 착륙)가 먹히는지 확인합니다.
3. `server/tests/`에 mock 시험을 추가합니다: 추종 시작, UGV 놓침 → 호버링, 경계 밖 UGV → 따라가지 않음, `stop` 우선.
4. sim(Unity)에서 확인합니다.
5. 실물은 낮은 속도, 넓은 공간, 프로펠러 가드로 시험합니다.

## 9. 시뮬레이터 + 앱 통합 안내

> 시뮬레이터 담당 조원이 **앱 자체를 시뮬레이터와 합칠** 예정입니다(2026-11-25 사용자 전달). 구체적 방식은 아직 정해지지 않았습니다.
> 아래는 현재 상태와 지켜야 할 연결 지점입니다.

### 9.1 지금 시뮬레이터가 붙어 있는 방식
- `server\run_server.bat sim`을 실행하면 서버가 `RoboMasterTT-Simulator beta/Python/simulated/djitellopy`(가짜 djitellopy)를 불러 Unity `UnityProject.exe`(127.0.0.1:9911)에 연결합니다.
  앱은 mock·real 때와 똑같이 서버에 붙습니다.
- 시뮬레이터 폴더 전체(빌드 + Python)는 조원 작업물이라 **git에 넣지 않습니다**. 로컬에 두고 따로 받습니다.
  처음 실행할 때 시뮬레이터 `.venv`는 새로 만들어야 합니다.
- 실측된 특성(현재 빌드):
  - 축척과 방향: 실제의 약 1/10, **동서 거울 반전**(조원에게 수정 요청함). 지도 1cm ≈ 0.285m라서 고도 50cm는 시뮬레이터에서 약 14m입니다.
  - 명령 처리:
    - 비행 명령은 끝난 뒤에 응답합니다. 그 사이 다른 비행 명령은 거부되고 `emergency`만 즉시 먹힙니다.
    - 비상정지 뒤에는 reset을 해야 다시 이륙합니다(서버가 자동 reset).
  - 기타:
    - 높이 센서가 약 29.8m에서 멈춥니다(`[sim] max_height_cm`).
    - 드론 시작 위치는 지도 약 (124.7, 116.6) cm로, home과 다릅니다.
    - 지도 아래쪽·위쪽 띠(y < 6.8, y > 244.1 cm)는 시뮬레이터 바닥 밖입니다.
- 위치:
  - `[sim] pos_source = "estimate"`(실물과 같은 조건) 또는 `"sim_truth"`(참값)를 씁니다.
  - Unity의 하단 카메라(포트 9914, 640×480 컬러, 시야각 75°)는 **아직 연결 프로그램에 붙이지 않았습니다**. 장면에 인쇄 지도·AprilTag가 없기 때문입니다. 그래서 카메라 위치 시험은 합성 카메라로 합니다.
- `[real] face_target`(목표 바라보기)는 real 전용입니다. 시뮬레이터는 좌우 반전 때문에 끕니다.

### 9.2 통합 방식 선택지
| 방식 | 내용 | 장단점 |
|---|---|---|
| **A. 서버를 가운데 유지 (권장)** | Unity ⇄ 서버(sim 백엔드) ⇄ 앱. 앱 화면을 Unity 안에 넣고 싶다면 Unity 쪽 UI가 앱과 **같은 `/ws` 클라이언트**가 되어 api.md 1~6장을 구현합니다(또는 Flutter 웹 빌드를 Unity WebView에 띄움). | 안전 검사·자동 착륙·명령권·사건·영상을 그대로 씁니다. 실물 전환은 서버 설정만 바꾸면 됩니다. |
| B. 앱(또는 Unity)이 시뮬레이터를 직접 제어 | 서버를 거치지 않습니다. | 서버 기능(안전 검사, 앱 끊김 자동 착륙, 구간 분할, 좌표 변환, 초기 정렬, 카메라 위치)을 다시 만들어야 하고, 실물 드론과 경로가 갈라집니다. **비권장** |

어느 쪽이든 지킬 것:
- 앱 ⇄ 서버 메시지를 바꾸면 **`docs/api.md` 버전을 올리고** 서버(`server/`)·앱(`app/lib/protocol.dart`)·테스트를 함께 고칩니다.
- 좌표는 모두 지도 cm(2장)입니다. 시뮬레이터 좌표 변환은 서버의 `SimFrame`/`[sim_calibration]` 한 곳에서만 합니다.
- **Unity 빌드(장면·축척·반전)를 바꾸면:**
  1. `server/tools/sim_calibrate.py`로 `[sim_calibration]`을 다시 계산합니다.
  2. `[sim] go_axes`와 face_target 사용 여부를 다시 확인합니다.
  3. 실측 경로를 다시 확인합니다(server/README 8장).
- Unity 카메라로 위치 인식을 시험하려면:
  1. 장면 바닥에 조원 지도 그림과 AprilTag를 깝니다.
  2. `vision/bridge/sources.py`에 포트 9914 입력 `Source`를 추가합니다.
  3. 카메라 시야각·장착 각도를 `[bridge]`에 넣습니다.
- 시뮬레이터 폴더를 저장소에 넣을지는 조원 동의를 받아 따로 결정합니다(현재 제외, 공개 저장소).

## 10. 실물 시험에서 배운 함정
- TT는 **15초 동안 명령이 없으면 스스로 착륙**합니다. 비행 중에는 무엇이든 주기적으로 보내야 합니다(현재 5초 유지 신호).
- 이륙이 끝나는 데 약 8초가 걸립니다. 그 사이 착륙 명령은 무시됩니다.
- TT 하단 카메라:
  - 흑백 320×240이고, 디코딩 화면의 왼쪽 위만 실제 영상입니다.
  - 화면이 드론 앞쪽 기준 반시계 90° 돌아 있습니다.
  - 시야각은 60°가 아니라 약 **75.5°**입니다(11-25 추정, 다음 비행에서 확인).
- UDP 11111(TT 영상)은 한 프로그램만 받을 수 있습니다. 연결 프로그램이 받으므로 다른 프로그램을 같이 켜지 않습니다.
- 11-25 비행에서 드론이 명령보다 1.3~1.5배 멀리 간 것으로 보입니다(TT 광학흐름이 틀린 높이 `h`를 쓰는 가설, 미확인). 이동 거리 관련 코드를 만질 때 참고하세요.
- XIAO ESP32 카메라는 아직 실물로 시험하지 않았습니다.

## 11. 커밋·문서 규칙
- 커밋할 때는 `git add <경로>`로 자기가 바꾼 파일만 지정합니다(`git add -A` 금지). 브랜치를 바꾸지 않고, 강제 push는 하지 않습니다.
- 공개 저장소입니다. API 키, `.env`, 조원 동의 없는 작업물(시뮬레이터 빌드 등), 로그(`server/logs`, `vision/logs`)는 올리지 않습니다.
- 결정·실측 결과는 `docs/decisions.md` 맨 위에 한 줄 추가합니다. 현재 상태가 바뀌면 `CLAUDE.md` "현재 상태"와 `docs/progress.md`를 고칩니다.
- 사용자와 문서는 한국어로, 초보자도 따라 할 수 있게 씁니다.
