# vision/ — 카메라 → 위치 인식 → 중계 서버 연결 프로그램

드론 카메라 영상을 받아 **조원 위치 인식 코드**로 드론 위치를 계산하고, 중계 서버(노트북)에 보낸다.
서버는 그 위치를 앱 지도에 표시하고, 사건 사진·실시간 영상을 앱에 나눠 준다. (명세: `docs/api.md` 7~12장)

```
 카메라 ──영상──▶ vision 연결 프로그램 ──▶ 중계 서버 ──▶ 앱
 (XIAO 또는 TT)    · 위치 계산 (조원 코드)     POST /vision/pose      지도 위 드론
                    · 슬롯 사진 자르기          POST /vision/incident  사건 알림
                    · 영상 JPEG                 ws  /vision/video      실시간 영상
                                         ◀──  모드 전환(vision_mode)  ◀── 앱의 카메라 전환 버튼
```

위치 추적 카메라는 두 가지이고, **앱에서 바꾼다**(동시에 쓰지 않음):

| 모드 | 카메라 | 위치 계산 (조원 코드) |
|---|---|---|
| `esp32` | 드론에 단 XIAO ESP32 (컬러, Wi-Fi) | `localization_app`: 건물·도로 색 맞추기 + AprilTag |
| `tt_down` | TT 내장 하단 카메라 (흑백 320×240) | `incident_detection`: AprilTag만 |

조원 코드는 `teammate/`에 복사해 두었다(출처·고친 곳: [teammate/SOURCE.md](teammate/SOURCE.md)).
설정은 모두 `config/settings.toml`의 `[bridge]`, `[video]`, `[incident]`, `[[photo_slots]]`에서 읽는다.

---

## 1. 설치 (처음 한 번)

1. 탐색기에서 `C:\dev\drone-control-app\vision` 폴더를 연다.
2. **`setup.bat`을 더블클릭**한다. 가상환경(`.venv`)을 만들고 필요한 패키지를 설치한다(몇 분).
   마지막에 `[완료]`가 보이면 된다.
   - Python 3.11을 권장하지만 없으면 설치된 Python 3(3.13·3.14에서도 시험함)을 쓴다.

## 2. 실물 없이 먼저 시험 — 합성 카메라

Unity 시뮬레이터에는 인쇄 지도·AprilTag가 없어서, 연결 프로그램이 **조원 지도 그림으로 드론 카메라 화면을 직접 그려**(합성 카메라) 시험한다.

### 2-1. 서버 없이: 보낼 내용만 출력 (`--dry-run`)
PowerShell에서:
```powershell
cd C:\dev\drone-control-app\vision
.\run_bridge.bat --dry-run --tour
```
- `--tour`: 가상의 드론이 이륙 지점 → 슬롯 S1~S4 위를 차례로 돌며 4초씩 멈춘다.
- 화면에 다음이 나오면 정상이다(1초마다):
  - `[dry-run] POST /vision/pose {...}  | 참값 (...) → 오차 1.2cm` — 서버로 보낼 위치와 실제 위치의 차이
  - `[dry-run] ws /vision/video 바이너리 JPEG 320x240 흑백, 평균 8KB × 9장/초` — 영상
  - `[슬롯] S3 조건 충족` / `S4 작음 29px < 30px` — 슬롯 사진 조건 판단
  - `[dry-run] POST /vision/incident {... "slot": 3 ...}` — 사건 사진 보고
- 컬러 카메라 모드는 `--mode esp32`를 붙인다. 끝내려면 **Ctrl+C**.
- 잘라낸 사진을 보고 싶으면 `--save-dir out\crops`를 붙인다(`vision\out\crops`에 JPEG 저장).

### 2-2. mock 서버와 함께 (전체 흐름)
창 두 개를 쓴다.
1. 첫 번째 창: 서버를 켠다.
   ```powershell
   C:\dev\drone-control-app\server\run_server.bat mock --vision
   ```
2. 두 번째 창: 연결 프로그램을 켠다.
   ```powershell
   C:\dev\drone-control-app\vision\run_bridge.bat
   ```
   - 서버가 `mock`이면 자동으로 합성 카메라를 쓴다(`[bridge] synthetic_camera = "auto"`).
     참값 위치는 서버의 `GET /sim/truth`에서 가져온다.
   - 5초마다 상태 한 줄이 나온다: `[tt_down] 카메라 OK … | (0.445, 0.896) m yaw 0° tracking 태그 [20, 21] | 위치 보냄 16 (ok 16) 서버응답 {'ok': True} | 영상 91장 | 사건 0`
3. 앱(또는 Edge)으로 서버에 접속해 **슬롯 S4로 출동**시킨다. 드론이 슬롯 위에서 1초 이상 멈추면
   연결 프로그램 창에 `사건 사진: 슬롯 4 …` → `사건 보고 접수: {'ok': True, 'id': 'inc-1'}`이 나오고, 앱에 사건 알림이 뜬다.
4. 앱에서 카메라를 ESP32 ⇄ TT로 바꾸면 연결 프로그램 창에 `전환 → 모드 esp32`가 나온다.
   - 바꾸는 순간 보낸 위치 하나가 `WRONG_MODE`로 거부될 수 있다(모드가 바뀌기 직전에 보낸 값). 정상이다.
- 확인 포인트: 브라우저로 `http://127.0.0.1:8000/`을 열면 `vision.last_age_s`가 1 이하로 계속 바뀐다.
- 서버를 늦게 켜도, 중간에 꺼도 연결 프로그램은 죽지 않고 경고만 낸 뒤 다시 연결한다.

## 3. 실물 XIAO ESP32 카메라 (`esp32` 모드)

1. XIAO 펌웨어(조원 `teammate/localization_app/firmware/`, 또는 공식 CameraWebServer 예제)를 올리고
   노트북과 **같은 Wi-Fi**에 연결한다. 시리얼 모니터(115200)에 나온 주소를 확인한다.
2. 브라우저로 `http://<XIAO IP>:81/stream`을 열어 영상이 보이는지 확인하고 **브라우저 탭은 닫는다**(스트림은 한 곳만 받을 수 있다).
3. 주소를 `config/settings.toml`의 `[bridge] esp32_camera_url`에 적거나(병합 세션 담당 파일 — 요청), 실행할 때 지정한다:
   ```powershell
   .\run_bridge.bat --esp32-url http://192.168.0.50:81/stream
   ```
4. 앱에서 카메라를 ESP32로 바꾼다(또는 `--mode esp32`로 시작).
- 서버가 `real`이면 합성 카메라를 쓰지 않는다. `mock` 서버로 실물 카메라를 시험하려면 `--synthetic off`.
- 영상이 안 오면 상태 줄에 `ESP32 카메라 연결 중… (reconnecting …)`이 나온다. 주소·Wi-Fi를 확인한다.
- **색·조명 보정**(`config/settings.toml` `[bridge] esp32_*`): 조원이 실물 XIAO + 지도 표본에서 찾은 값으로 시작한다.
  - `esp32_hue_shift = 3`, `esp32_hue_band_width = 15`, `esp32_saturation_tolerance = 78`
  - 조명 보정 `esp32_illumination = "combined"`, 그림자 인식 `esp32_shadow_aware = true`
  - 조원 `start_xiao_camera.ps1 -Localize -HueShift 3 -HueBandWidth 15 -SaturationTolerance 78`과 같은 구성이다.
  - **Hue 이동(`esp32_hue_shift`)은 실물 카메라에만 적용된다.** 합성 카메라는 색이 정확해서 빼고 쓴다
    (합성 화면에서 넣으면 36장 중 23장만 성공, 빼면 36/36).
  - 2.5m 실제 인쇄 지도에서 위치가 잘 안 잡히면, 이 값들을 조정한다.

## 4. 실물 TT 하단 카메라 (`tt_down` 모드)

- TT 영상 켜기(`downvision 1` → `streamon`)는 **서버가 한다**(서버 `real` + 모드 `tt_down`일 때).
  TT는 영상을 노트북의 **UDP 11111** 포트로 보내고, 그 포트는 **연결 프로그램이 받는다**(서버는 받지 않는다. 한 포트는 한 프로그램만).
- 순서: TT Wi-Fi에 노트북 연결 → `server\run_server.bat real` → `vision\run_bridge.bat` → 앱에서 TT 카메라 선택.
- 상태 줄에 `TT 하단 카메라 수신 중 (320x240 흑백)`이 나오면 정상이다.
  `TT 하단 카메라 영상 기다리는 중 …`이 계속되면:
  1. **방화벽:** 처음 실행할 때 Windows가 "Python의 네트워크 접근 허용?"을 물으면 **개인 네트워크 허용**을 누른다.
     놓쳤다면 [Windows 보안] → [방화벽 및 네트워크 보호] → [방화벽에서 앱 허용]에서 `vision\.venv\Scripts\python.exe`를 허용한다.
     (또는 관리자 PowerShell에서 UDP 11111만 여는 규칙: `New-NetFirewallRule -DisplayName "TT video UDP 11111" -Direction Inbound -Protocol UDP -LocalPort 11111 -Action Allow`)
  2. 서버 창에 `downvision 1`·`streamon`을 보냈다는 기록이 있는지 본다.
  3. 다른 프로그램(조원 코드, djitellopy 예제 등)이 11111 포트를 쓰고 있지 않은지 확인한다.
- TT 화면은 320×240보다 크게 들어오고 **왼쪽 위 320×240만 실제 영상**(나머지는 잡음)이라, 그 부분만 잘라 쓴다(`[bridge] tt_crop_top_left`). 화면이 320×240보다 작으면 경고가 나온다(`downvision 1`이 안 되어 전방 카메라 영상일 수 있음).
- **장착 각도:** TT 하단 카메라 화면은 **드론 앞쪽 기준 반시계 90°** 돌아 있다(앞쪽을 가리키는 화살표가 화면 왼쪽을 향함, 2026-11-21 실물 확인).
  - `[bridge] tt_mount_yaw_deg = -90`: 서버로 보내는 yaw를 드론 앞쪽 방향으로 바꾼다. 위치와 슬롯 촬영은 카메라 자세로 계산하므로 영향이 없다.
  - `[bridge] video_forward_up = true`: 앱 영상은 앞쪽이 위로 오게 돌려서 보낸다(TT는 세로 240×320).
  - 합성 카메라도 같은 각도로 달린 것처럼 그린다.
  - 확인 방법: 드론 앞쪽을 가리키는 화살표 종이 위에 들고, 앱 영상에서 화살표가 **위**를 가리키면 맞다. ESP32는 달고 나서 같은 방법으로 `esp32_mount_yaw_deg`를 정한다.

## 5. TT 하단 카메라 보정 (정확도를 높이려면)

처음에는 시야각 추정값(`[bridge] tt_hfov_deg = 60`, 모름)으로 계산한다. 실제 렌즈 값을 재면 위치·높이가 정확해진다.
조원 `calibrate_camera.py`를 **TT 영상 320×240 그대로** 쓴다.

1. 체스판을 인쇄한다(예: 10×7칸, 한 칸 25mm). 칸 크기를 자로 잰다. 딱딱한 판에 붙인다.
2. 서버(`real`)와 연결 프로그램을 켜고 TT 카메라 모드에서 **화면을 저장**한다:
   ```powershell
   .\run_bridge.bat --record-dir out\calib_tt --record-every 1
   ```
   TT를 손으로 들고 아래를 보게 한 채, 체스판을 화면 여러 곳(네 귀퉁이, 가운데)·여러 기울기(최대 45°)·가까이/멀리 두며 20~30장 찍는다. Ctrl+C로 끝낸다.
3. 보정 계산 (`--cols`/`--rows`는 **안쪽 꼭짓점 수**: 10×7칸 → 9×6):
   ```powershell
   cd C:\dev\drone-control-app\vision\teammate\incident_detection\localization
   $files = Get-ChildItem ..\..\..\out\calib_tt\*.png | ForEach-Object FullName
   ..\..\..\.venv\Scripts\python.exe calibrate_camera.py $files --cols 9 --rows 6 --square-mm 25 --out ..\..\..\calibration\tt_down_320x240.json
   ```
   마지막에 `RMS reprojection error 0.4 px`처럼 나온다. 0.5px 이하면 좋고, 1px 넘으면 흐린 사진을 지우고 다시 한다.
4. `config/settings.toml`의 `[bridge] tt_camera_file = "vision/calibration/tt_down_320x240.json"`으로 바꾼다(병합 세션에 요청).

## 6. 실행 옵션

`.\run_bridge.bat --help`로 전체 목록을 본다. 자주 쓰는 것:

| 옵션 | 뜻 |
|---|---|
| `--dry-run` | 서버에 아무것도 보내지 않고 보낼 내용을 출력 |
| `--mode esp32` / `--mode tt_down` | 처음 모드 (서버가 모드를 보내면 그걸 따른다) |
| `--tour` / `--truth X Y [Z YAW]` | 서버 없이 합성 카메라: 슬롯 순회 / 한 곳에 고정 |
| `--synthetic on/off/auto` | 합성 카메라 강제 켜기·끄기 |
| `--synthetic-map tags` | 합성 카메라 그림을 태그만 있는 지도로 (기본 `photos` = 태그 + 슬롯 사진) |
| `--esp32-url URL` | XIAO 영상 주소 |
| `--input 파일또는폴더` | 녹화 영상·사진 폴더로 오프라인 시험 (`--input-once`: 한 번만) |
| `--server http://IP:8000` | 서버 주소 (기본 `[bridge] server_url`) |
| `--save-dir 폴더` | 잘라낸 사건 사진도 저장 |
| `--record-dir 폴더` | 카메라 원본 화면 저장 (보정용) |
| `--tt-upscale 2` | tt_down: 태그 찾기 전 화면 2배 확대 (아래 7장) |

## 7. 측정 결과와 알아 둘 점 (합성 카메라, 2026-11-21)

**tt_down, 320×240 흑백, 고도 80cm** (시야각 60° 가정, 지도 위 무작위 200곳, 조원 합성 카메라의 흐림·잡음·JPEG 포함, 한 장씩 독립 계산):

| 고도 | 확대 | 화면 안 태그 인식률 | 태그 한 변 | 위치가 나온 장 | 위치 오차 (중앙값 / 90%) | 높이 오차 |
|---|---|---|---|---|---|---|
| 60cm | ×1 | 75% | 17.8px | 71% | 2.5 / 6.2cm | 1.0cm |
| **80cm** | **×1** | **20%** | **13.2px** | **34%** | **2.6 / 7.6cm** | **2.5cm** |
| 80cm | ×2 | 37% | 13.3px | 50% | 2.9 / 10.1cm | 1.3cm |
| 100cm | ×1 | 2% | 10.5px | 9% | 3.1 / 8.8cm | 5.5cm |

- 80cm에서는 태그가 약 13px로 작게 찍혀 **보이는 태그의 5개 중 1개만 읽힌다.** 칼만 추적이 태그 없는 장을 최대 1초 이어 주지만,
  태그가 드문 곳에서는 위치가 끊긴다. 고도를 60cm로 낮추면 크게 좋아진다(시연 고도 결정에 참고).
- `--tt-upscale 2`는 인식률을 올리지만 오차가 조금 커진다. 기본은 끔.
- 슬롯 위 호버링(80cm): 슬롯이 화면에서 짧은 변 약 34px. 그 장에서 **실제로 측정한 자세일 때만** 자른다(예측값으로 자르면 수십 px 어긋났다).
  이때 슬롯 모서리 투영 오차는 중앙값 약 3px, 최대 약 10px (슬롯 34px 기준) — 사진이 조금 밀려 잘릴 수 있다.
- **esp32** (컬러 320×240, 시야각 68° 가정 — 조원 기본값): 슬롯 S2~S4 위 호버링 위치 오차 중앙값 0.6cm. 첫 위치 찾기에 0.3~1초 걸린다.
  지도 가장자리(S1 근처)는 화면에 지도 밖 바닥이 많이 보여 위치를 못 찾을 수 있다.
  80cm·68°에서 슬롯 짧은 변이 29~30px라 `min_slot_px = 30`에 걸려 사진 보고가 안 될 수 있다.
- 조원 사진 찾기(`[incident] method = "photo_finder"`)는 320×240·80cm에서 슬롯 사진(약 30px)을 거의 찾지 못했다(슬롯 위 호버링 흑백 0/120장, 컬러 1/120장).
- 방향(yaw)은 **카메라 화면 위쪽이 가리키는 방향**이다. 드론 앞쪽과 같게 하려면 카메라 화면 위쪽이 드론 앞을 향하게 단다(TT 하단 카메라는 실물로 확인 필요).
- AI 판별은 끄기로 해서 사건 보고의 `detection`은 항상 `null`이다.

## 8. 시험 (개발자용)

```powershell
cd C:\dev\drone-control-app\vision
.venv\Scripts\python.exe -m pytest -s
```
- `test_yaw.py` 방향 기준 / `test_slots.py` 슬롯 투영·조건·자르기·중복 / `test_bridge.py` 모드 전환·보낼 내용·위경도·오프라인 재생 /
  `test_accuracy.py` 합성 화면 위치 오차. 고도·확대 비교표(7장)는 `-m slow -s`.

## 9. 폴더 구조

```
vision/
  bridge/            연결 프로그램 (우리 코드)
    app.py             본체: 스레드(입력·처리·영상·HTTP), 모드 전환, 실행 옵션
    config.py          settings.toml 읽기
    sources.py         입력: 합성 카메라 / XIAO MJPEG / TT UDP H.264 / 파일
    localizers.py      모드별 위치 계산 (조원 코드 호출, yaw 변환)
    slots.py           슬롯 촬영 · 사진 찾기 방식
    synthetic.py       합성 카메라
    server_link.py     서버 HTTP·WebSocket
    geometry.py        좌표·투영·yaw 변환
  teammate/          조원 코드 사본 (SOURCE.md)
  tests/             자동 시험
  setup.bat, run_bridge.bat, requirements.txt
```
