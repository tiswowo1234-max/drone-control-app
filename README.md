# 캠퍼스 경비원 보조 드론 — 제어 앱 · 중계 서버 · 위치 인식

대학교 조별과제로 만든 **실내 축소 캠퍼스 지도(2.5m × 2.5m) 위를 나는 경비 보조 드론** 시스템입니다.
폰 앱에서 지도 위 목표 지점(또는 사건 사진 슬롯)을 누르면, 중계 서버가 안전 검사를 거쳐 드론(DJI RoboMaster TT)을 보냅니다.
드론 아래를 보는 카메라 영상으로 **드론 위치를 알아내고**, 사건 사진 슬롯 위에 멈추면 **사진을 찍어 앱에 알립니다.**

> 진척 상황·시험 결과·남은 일은 **[docs/progress.md](docs/progress.md)** 에 정리되어 있습니다.
> **코드를 이어서 작업할 분**(UGV 추종 병합, 시뮬레이터 + 앱 통합 등)은 **[docs/dev-guide.md](docs/dev-guide.md)** 부터 읽어 주세요.

```
                       ┌───────────── 노트북 ──────────────────────────────────────┐
 [드론 카메라]  영상 → │ [위치 인식 연결 프로그램 vision/] ─ 위치·사건 사진·영상 ─→ │
  TT 하단(흑백)        │                                      [중계 서버 server/]   │ ⇄ [드론 RoboMaster TT]
  또는 XIAO ESP32       └──────────────────────────────────────────────┬────────────┘     (또는 가짜 드론·Unity 시뮬레이터)
                                                                     WebSocket
                                                                  [안드로이드 앱 app/]
```

## 무엇을 할 수 있나
- **지도 위 출동:** 지도에서 목표를 찍고 확인 → 드론이 이륙해 그 지점으로 이동. 정지·착륙·귀환·비상정지, 이륙 버튼.
- **안전장치:** 지도 경계·반경·고도 제한, 배터리 부족 시 출동 거부, 앱 연결이 3초 끊기면 자동 착륙,
  드론이 스스로 착륙하면 감지해 알림, TT의 "15초 무명령 자동 착륙" 방지.
- **카메라 위치 인식 (조원 코드 병합):** 바닥 지도의 AprilTag(39개)와 건물·도로 모양으로 드론 위치·방향을 계산해 앱 지도에 표시.
  카메라 2가지를 **앱에서 전환**(동시 사용 안 함, 성능 비교용):
  - `TT 하단 카메라` — 드론 내장, 흑백 320×240, AprilTag로 위치 계산
  - `ESP32 카메라` — 드론에 단 XIAO ESP32(컬러), 건물·도로 색 + AprilTag로 위치 계산
- **사건 사진 알림:** 지도의 사진 슬롯 S1~S4로 출동해 멈추면 그 영역을 잘라 앱에 알림(목록·지도 표시·사진 확대).
  AI(LLM) 판별 기능은 조원 코드에 있지만 현재 **꺼 둠**.
- **실시간 카메라 영상:** 앱의 카메라 버튼으로 드론 카메라 화면 보기(드론 앞쪽이 위로 오게 회전).
- **UGV(미니 경찰차) 조종 (v0.8):** 앱 위쪽 경찰 아이콘 → 차 조종 모드. 조이스틱 수동 조종, 정지·비상정지, 지도를 눌러 경유점 경로 주행.
  노트북 서버가 블루투스(BLE)로 차에 연결한다(조원 [police-car-controller](https://github.com/jiseung2519/police-car-controller)와 같은 명령, 펌웨어 수정 없음).
  **차 위치는 드론이 인식한 UGV 태그**로 정하고, 드론이 못 보는 동안은 마지막 인식 위치에서 차 엔코더로 이어 간다(앱에 "드론 인식 / 엔코더로 이어감" 표시).
  드론이 차를 한 번도 못 봤으면 경로 주행은 거부되고 조이스틱만 된다. 자세한 내용: [docs/api.md](docs/api.md) 13장.

## 폴더 구성
| 폴더 | 내용 |
|---|---|
| `app/` | Flutter 안드로이드 앱 |
| `server/` | 중계 서버 (FastAPI WebSocket, 안전 검사, mock/sim/real 백엔드) — [server/README.md](server/README.md) |
| `vision/` | 위치 인식 연결 프로그램 + **조원 위치 인식 코드 복사본**(`vision/teammate/`) — [vision/README.md](vision/README.md) |
| `config/settings.toml` | 조정 가능한 모든 값 (지도·이륙 지점·고도·제한·카메라 설정·사진 슬롯) |
| `docs/api.md` | 앱 ⇄ 서버 ⇄ 연결 프로그램 메시지 명세 (v0.7) — **기준 문서** |
| `docs/dev-guide.md` | 개발자 가이드: 구조, 명령·위치 흐름, 확장 방법, UGV 추종·시뮬레이터 통합 안내 |
| `docs/progress.md` | 진척 상황·시험 결과·남은 일 |
| `docs/decisions.md` | 결정 사항과 실물 시험 기록 (날짜순) |
| `CLAUDE.md` | Claude Code 세션용 프로젝트 안내 (현재 상태·작업 규칙) |
| `map/` | 바닥 인쇄 지도 원본 (2.5m × 2.5m, EPS/PDF) |
| `tools/` | 지도 이미지 변환·위경도 정합 스크립트 |
| `start_real_drone.bat` / `start_demo_no_drone.bat` | **간편 실행 파일** (아래 「실행하기」) |

## 실행하기 (Windows)
처음 실행할 때는 Python 가상환경을 자동으로 만들고 필요한 패키지를 설치합니다(몇 분 걸림). Python 3.11 이상이 필요합니다.

### 드론 없이 시험 — `start_demo_no_drone.bat` 더블클릭
가짜 드론 서버와 위치 인식 연결 프로그램(**합성 카메라**: 조원 지도 그림으로 드론 위치에서 보이는 화면을 만들어 실제 위치 인식 코드에 넣음)이 새 창 두 개로 켜집니다.
앱에서 **이륙 → 보라색 S4 누르기 → 확인** 하면 드론 아이콘이 이동하고, 몇 초 뒤 **사건 알림**이 옵니다. 카메라 아이콘으로 영상도 볼 수 있습니다.

### 실제 드론 — `start_real_drone.bat` 더블클릭
1. 드론을 **정문(지도의 빨간 점)에 앞쪽이 지도 위쪽을 보게 놓고** 전원을 켭니다. (드론 방향 센서는 켤 때 방향을 0°로 삼으므로, 켠 뒤에는 돌리지 않습니다.)
2. 노트북 Wi-Fi를 드론(`TELLO-…`/`RMTT-…`)에 연결합니다. 인터넷은 휴대폰 USB 테더링 또는 USB Wi-Fi 동글로 따로 연결합니다.
3. 실행 파일을 더블클릭하면 서버(real) → 연결 프로그램 순서로 켜지고, **앱에 넣을 주소**가 표시됩니다.
4. 처음 실행 때 Windows 방화벽 창이 뜨면 **허용**(공용·개인 모두)을 누릅니다. (드론 영상 UDP 11111, 앱 접속 8000)

**차(UGV)도 쓸 때:** 차 전원을 켜고 노트북 블루투스를 켭니다. 서버가 `PoliceCar-XIAO`를 찾아 자동 연결합니다(서버 창에 `car BLE connected`).
폰의 Police Car 웹앱이 차에 연결돼 있으면 서버가 연결할 수 없으니 웹앱 연결을 먼저 끊습니다. 차 없이 쓰려면 `server\run_server.bat real --vision --car off`.

끌 때는 드론을 먼저 착륙시키고, 열린 두 창에서 각각 **Ctrl+C**를 누릅니다. 서버 로그는 `server\logs\server-날짜.log`에 남습니다.

### 명령으로 직접 실행 (참고)
```powershell
cd server
.\run_server.bat real --vision      # 드론 없이: mock --vision, Unity 시뮬레이터: sim
```
```powershell
cd vision
.\run_bridge.bat                    # 서버를 먼저 켠 뒤 실행 (옵션: --help)
```

## 실행하기 (Mac)
Windows와 같은 순서이고, `.bat` 대신 `.command` 파일을 Finder에서 **더블클릭**합니다(터미널 창 두 개가 새로 열림).
- **준비(처음 한 번):**
  - Python 3.11 이상: 터미널에서 `brew install python@3.12`. 가상환경은 처음 실행할 때 자동으로 만듭니다.
  - **저장소 폴더 경로에 `:`가 있으면 안 됩니다**(Mac에서 Python 가상환경을 만들 수 없음). 예: `~/dev/drone-control-app`.
  - 처음 더블클릭할 때 "확인되지 않은 개발자" 경고가 뜨면: 파일을 **오른쪽 클릭 → 열기**. 실행 권한이 없다고 하면 터미널에서
    `chmod +x start_*.command server/run_server.sh vision/run_bridge.sh`.
- **드론 없이 시험:** `start_demo_no_drone.command` — 폰은 Mac과 같은 Wi-Fi에 연결.
- **실제 드론:** `start_real_drone.command` — Mac Wi-Fi를 TT에 연결. 폰은 **TT Wi-Fi에 함께 연결**하면 Mac과 같은 네트워크가 됩니다
  (안드로이드 USB 테더링은 Mac에서 기본으로 안 됨). 실행 창에 나온 `192.168.10.x` 주소를 앱 ⚙에 넣습니다.
- **차(UGV) 블루투스:** 처음 한 번 터미널에서 `server/.venv/bin/python server/tools/ble_scan.py`로 차가 보이는지 확인합니다.
  "터미널이 블루투스를 사용하려고 합니다" 창이 뜨면 **허용**. 거부하면 macOS가 서버를 바로 종료시킵니다
  (다시 켜기: 시스템 설정 → 개인정보 보호 및 보안 → 블루투스 → 터미널).
- 직접 실행: `server/run_server.sh real --vision`, `vision/run_bridge.sh` (Windows의 `.bat`과 같은 인자).
- Mac에서 확인한 것(2026-11-26): 드론 없이 시험 전체, 서버·vision 테스트 전부, UDP 11111 수신, OpenCV FFmpeg.
  **실물(TT 영상·차 블루투스 연결)은 아직 Mac에서 시험하지 않음.**

### 앱
- 안드로이드: 빌드한 APK를 설치 → 오른쪽 위 ⚙에 노트북 주소 입력(예: `192.168.0.10`).
- PC 브라우저(개발용): `app/`에서 `flutter run -d edge`.

## 다음 작업 (3단계)
| 작업 | 담당 | 안내 |
|---|---|---|
| 드론이 지상 로봇(UGV)을 따라가는 코드 병합 | UGV 담당 조원 | [dev-guide 8장](docs/dev-guide.md#8-ugv-추종-병합-안내) |
| 앱을 Unity 시뮬레이터와 통합 | 시뮬레이터 담당 조원 | [dev-guide 9장](docs/dev-guide.md#9-시뮬레이터--앱-통합-안내) |

앱 ⇄ 서버 메시지를 바꾸면 `docs/api.md` 버전을 올리고, 서버·앱·테스트를 함께 고쳐 주세요.

## 조원 작업물
- `vision/teammate/` 는 조원의 위치 인식 저장소(`drone-localization-main`)에서 필요한 부분을 **조원 동의를 받아** 복사한 것입니다.
  출처·복사 날짜·고친 곳은 [vision/teammate/SOURCE.md](vision/teammate/SOURCE.md)에 있습니다. API 키 등 비밀 정보는 포함하지 않습니다.
- Unity 시뮬레이터(`RoboMasterTT-Simulator beta/`)는 팀원의 작업물이라 이 저장소에 포함하지 않습니다. `sim` 모드로 실행하려면 프로젝트 루트에 따로 둡니다.

## 지도 데이터 출처
지도(`KAIST_map.osm`, `map/`, `app/assets/map.png`, 조원 지도 파일 및 이를 바탕으로 한 좌표 정합 값)는
**© OpenStreetMap contributors** 의 데이터를 사용했으며, [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/)에 따라 이용할 수 있습니다.
자세한 내용: https://www.openstreetmap.org/copyright
