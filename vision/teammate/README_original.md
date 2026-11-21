# 캠퍼스 모형 드론 위치 추정 프로젝트

**구현 현황 기준일 · 2026-11-20**

2.5 m × 2.5 m KAIST 캠퍼스 모형에서 하향 카메라 영상으로 드론의 평면 위치 `(x, y)`와 방향 `yaw`를 추정하는 공학설계 프로젝트입니다. 지도·출력물 제작, 가상 영상 추정·추적, PC 수신·매칭 연동을 구현했습니다.

## 현재 구현 상태

| 영역 | 구현 및 검증 상태 |
| --- | --- |
| 캠퍼스 지도 | OSM에서 건물 60개, 도로 중심선 46개, 정문 좌표를 2.5 m 모형 좌표로 변환했습니다. JSON이 추정기와 출력 파일의 공통 기준입니다. |
| 지도 출력 | 2,500 × 2,500 mm 단일 벡터 EPS와 검토용 PDF·PNG를 제작했습니다. EPS 정밀 경계, 좌표, 색상, 도형 수를 검증했습니다. 업체의 대형 출력 결과는 미확인입니다. |
| 가상 영상 위치 추정 | HSV로 건물 영역을 분리하고, 지도 위의 위치·회전·축척 후보를 색상 마스크 IoU로 비교합니다. 후보가 모호하거나 관측이 부족하면 추정을 보류합니다. |
| 자동 축척 추정 | `--auto-scale`을 켜면 관측 폭을 영상에서 위치·yaw와 함께 찾습니다. 기준 위치에서 높이만 바꾼 합성 영상은 기존 6/8, 자동 8/8을 채택했습니다. [높이 변화 비교](localization_app/README.md#높이-변화-검증) |
| 조명·그림자 보정 | 종이의 밝기·색으로 영상을 보정하고 그림자 픽셀의 정합 가중치를 줄입니다. 조명·그림자를 바꾼 합성 영상에서 채택은 13/33에서 31/33으로 늘었습니다. 실물 그림자 조건은 실측 전입니다. [조명·그림자 실험](localization_app/README.md#조명과-그림자-대응) |
| 연속 추적 | 첫 위치를 전역 탐색으로 찾고, 이후 칼만 필터 예측 주변에서 탐색합니다. 관측 누락 시 예측을 표시하고, 실패가 이어지면 전역 탐색으로 복구합니다. 합성 영상에서 검증했습니다. |
| AprilTag 융합 | 39개 마커의 지도 좌표를 건물·도로 정합과 공유하며, 두 관측을 같은 추적기에 입력합니다. [통합 실험과 재현 명령](data/experiments/localization/apriltag_fusion/integration_report.md) |
| 랜덤 뷰·블러 비교 | 같은 포즈의 원본과 블러 영상에 독립적으로 전역 매칭을 수행합니다. GUI와 PNG·JSON 저장, 배치 평가를 지원합니다. |
| XIAO 카메라 연동 | COM5의 XIAO에서 OV3660과 PSRAM 8MB를 확인했습니다. Arduino-ESP32 공식 `CameraWebServer` 예제로 새 Wi-Fi에서 PC 영상 수신을 확인했고, 외장 안테나를 연결한 사용자 시험에서도 영상이 정상적으로 송출됐습니다. [초기 실물 통합 기록](data/experiments/localization/xiao_real_camera/experiment_report.md) |
| A4 축소 지도 위치 추정 | 기존 방식은 특정 높이에서 위치를 인식했고, 높이를 조금 바꾸면 실패하는 현상이 관찰됐습니다. 자동 축척의 성공 높이와 실물 위치 오차는 실측 전입니다. |
| 카메라 보정·실물 추적 | 체커보드 렌즈 보정, 지상 관측 폭 측정, HSV 마스크 진단 도구를 갖췄습니다. 2.5 m 출력 지도에서의 위치 오차와 이동 추적은 실측 전입니다. |

지도 원점은 남서쪽입니다. `x`는 동쪽, `y`는 북쪽을 향하고 단위는 m입니다. yaw는 북쪽 기준 반시계 방향입니다. 도로 선폭은 모형의 지도 표현을 위해 정했습니다. 원본 지도 데이터 © OpenStreetMap contributors.

## 폴더와 주요 파일

공용 입력과 실험 결과는 저장소 최상단의 [data/](data/)에 있습니다. 각 프로그램의 기본 경로는 이 폴더를 기준으로 정합니다.

| 위치 | 역할 |
| --- | --- |
| [localization_app/](localization_app/README.md) | XIAO 실시간 위치 추정, AprilTag·건물·도로 정합, 시뮬레이션, 벤치마크 및 테스트. [펌웨어](localization_app/firmware/xiao_camera_stream/README.md)와 실행 도구도 여기에 있습니다. |
| [map_tools/](map_tools/README.md) | OSM에서 기준 지도를 만들고 실물 크기의 EPS·PDF·PNG를 생성·검증합니다. |
| [incident_detection/](incident_detection/README.md) | [사진 위치 추정](incident_detection/localization/README.md)은 카메라 영상에서 사진의 지도 좌표를 찾고, [사고 사진 분류](incident_detection/LLM_detects/README.md)는 사고 유형을 판별하고 결과를 평가합니다. |
| [data/gis/](data/gis/) | 원본 OSM 및 QGIS 프로젝트 |
| [data/maps/](data/maps/) | 프로그램들이 공유하는 [건물·도로 좌표](data/maps/KAIST_model_features_2p5m.json), [AprilTag 배치](data/maps/apriltag_map.json), 사진 슬롯, 기준·합성 지도 이미지 |
| [data/print/](data/print/) | [캠퍼스 지도 인쇄본](data/print/campus/), [마커·사진 인쇄본](data/print/markers/) 및 [캠퍼스 지도 배포 ZIP](data/print/campus_bundle.zip) |
| [data/photos/](data/photos/) · [data/fixtures/](data/fixtures/) | 사고 사진 원본·흑백·라벨과 자동 테스트용 고정 입력 |
| [data/experiments/](data/experiments/) | [위치 추정](data/experiments/localization/), [사진 좌표 추정](data/experiments/incident_camera/), [사진 분류](data/experiments/photo_classifier/)의 실험 결과 |
| `data/recordings/` · `data/calibration/` | XIAO 녹화의 기본 저장 위치와 사고 사진 좌표 추정용 카메라 보정 파일 위치. 최초 녹화·보정 시 생성됩니다. |

실험 폴더의 과거 로그에는 측정 당시의 경로와 설정을 보존했습니다. 현재 경로에서 실행하는 명령은 아래 실행 예시와 각 구성요소의 README에 있습니다.

로컬 Python 환경 `.localization-venv/`, 펌웨어 빌드 폴더 `.xiao-build/`, Wi-Fi 설정 `wifi_secrets.h`는 각 PC에서 따로 준비합니다.

## 실행

저장소 루트에서 시작합니다. Python 3.10 이상이 필요하며, 새 설치에는 Python 3.11을 기준으로 합니다. 시뮬레이션과 저장 영상 재생은 XIAO 없이 실행할 수 있습니다.

### Windows PowerShell

```powershell
python -m venv .localization-venv
$python = (Resolve-Path .\.localization-venv\Scripts\python.exe).Path
& $python -m pip install -r requirements.txt
Set-Location .\localization_app

& $python main.py                               # 정지 영상 GUI
& $python main.py --save ../data/experiments/localization/snapshot.png           # 창 없이 위치 추정 결과 저장
& $python main.py --track square                # 가상 연속 추적
& $python tests/random_view_test.py --seed 42    # 랜덤 뷰·블러 비교
& $python -m unittest discover -s tests -v       # 위치 추정 테스트
```

### macOS 및 Linux

```sh
python3 -m venv .localization-venv
. .localization-venv/bin/activate
python -m pip install -r requirements.txt
cd localization_app
python main.py
python main.py --save ../data/experiments/localization/snapshot.png
python -m unittest discover -s tests -v
```

GUI에는 데스크톱과 Matplotlib 대화형 백엔드가 필요합니다. Ubuntu/Debian에서 Tk를 사용하는 경우 시스템 패키지 `python3-tk`를 설치합니다. 화면이 없는 환경에서는 `--save`나 벤치마크를 실행합니다.

벤치마크는 위치 추정 폴더에서 `main.py --benchmark`, `main.py --benchmark-tracking`, `main.py --random-test --random-count 30`으로 실행합니다. 렌즈 보정·녹화·재생 옵션은 [위치 추정 README](localization_app/README.md)에 있습니다.

사고 사진 분류와 평가에 필요한 별도 의존성과 실행 예시는 [사고 판별 안내](incident_detection/README.md)에 있습니다.

### 지도 재생성 및 QGIS

가상 환경을 활성화하거나 위에서 지정한 가상 환경 Python으로 저장소 루트에서 다음 파일을 실행합니다. 지도의 한글 라벨을 다시 그리려면 맑은 고딕, 나눔고딕, Apple SD Gothic Neo, Noto Sans CJK KR 중 하나가 설치되어 있어야 합니다.

```sh
python map_tools/build_campus_map.py
python map_tools/create_print_map.py
python map_tools/verify_print_map.py
```

QGIS 작업 파일은 같은 폴더의 `KAIST_map.osm`을 사용합니다. 사용자 정의 실험 경계 레이어까지 열려면 원본 `KAIST_boundary.gpkg`를 `data/gis/`에 넣어야 합니다. 이 파일은 현재 작업본에 없으며, 위치 추정과 출력 지도 생성에는 필요하지 않습니다.

## 인쇄와 실물 카메라 실험

1. [업체 제출용 EPS](data/print/campus/KAIST_map_PRINT_2500x2500mm.eps)를 **100% 배율**로 출력합니다. 분할 출력 시 조립한 지도 면이 2,500 × 2,500 mm가 되도록 전달합니다. [검토 PDF](data/print/campus/KAIST_map_PRINT_preview.pdf) · [검토 PNG](data/print/campus/KAIST_map_PRINT_preview.png)
2. XIAO에 올라간 공식 `CameraWebServer` 예제는 시리얼 모니터(115200 baud)에 제어 페이지 주소를 표시합니다. PC를 같은 네트워크에 연결하고 해당 주소를 열어 **Start Stream**을 누릅니다. 자체 펌웨어를 올리는 절차는 [펌웨어 안내](localization_app/firmware/xiao_camera_stream/README.md)에 있습니다.
3. 브라우저의 스트림을 닫고 PC 수신기를 실행합니다. 인쇄 지도 위에서 카메라를 수직으로 고정하고 자동 축척으로 위치 추정을 시험합니다. A4 용지에서 종이 여백을 뺀 지도 폭이 18 cm라면 `--print-width-m 0.18`로 실제 관측 폭도 표시할 수 있습니다.

```powershell
# localization_app 폴더에서 실행
$cameraUrl = 'http://BOARD_IP:81/stream'  # 공식 예제. 자체 펌웨어는 http://BOARD_IP/stream 사용
& $python main.py --camera-test --camera-url $cameraUrl
& $python tools/test_camera_stream.py --url $cameraUrl --seconds 30
& $python main.py --live-localization --camera-url $cameraUrl --global-only --auto-scale
& $python main.py --live-localization --camera-url $cameraUrl --auto-scale --illumination-method combined --shadow-aware --shadow-debug
```

자동 모드는 기본 기준 폭 약 0.675 지도 m의 0.5–2.0배를 탐색합니다. `--scale-min`과 `--scale-max`로 범위를 조정할 수 있습니다. 고정 축척과 비교할 때는 `--no-auto-scale --nominal-span 값`을 사용합니다. A4의 관측 폭을 지도 좌표로 환산하려면 같은 물리 단위로 잰 두 폭을 사용해 `2.5 × (실제 관측 폭 ÷ 인쇄된 지도 이미지 폭)`을 계산합니다. [높이별 영상 수집과 진단](localization_app/README.md#자동-축척-탐색)

`--ground-truth X Y`에 실측 좌표를 넣으면 오차를 표시합니다. 안정적으로 위치를 찾으면 `--global-only` 없이 연속 추적을 시험할 수 있습니다. 현재 추정기는 JSON 건물 마스크를 기준으로 사용합니다. 완성 스테이지 사진을 기준으로 쓰려면 사진의 원근 보정과 기준 마스크 생성 기능을 추가해야 합니다.

실시간 위치 추정은 최신 프레임을 독립 작업자에서 처리합니다. [CLI 설정과 지연 계측](localization_app/README.md#실시간-처리와-지연-측정) · [0/100/150/200ms 비교 실험](localization_app/README.md#처리-주기-비교)

HSV 영역 정리와 지도 정합을 최적화한 전후 측정은 프로젝트에 보존한 동일한 재생 입력을 사용합니다. [성능 최적화 보고서](data/experiments/localization/performance_optimization/optimization_report.md) · [재현 명령](localization_app/README.md#성능-최적화-실험-재현)

### XIAO Wi-Fi 변경 후 다시 시작

Windows에서는 저장소 루트의 [Start_XIAO.cmd](Start_XIAO.cmd)를 더블클릭합니다. **1**은 Wi-Fi 설정을 메모장에서 열고, 저장한 뒤 Enter를 누르면 USB 업로드부터 적응형 위치 추정까지 시작합니다. **2**는 XIAO의 IPv4, 공식 카메라 예제의 페이지 주소 또는 전체 스트림 주소를 받아 현재 보드에 바로 연결합니다. 두 메뉴는 인쇄 지도를 2.5 m × 2.5 m로 가정합니다. 인쇄 크기가 다르면 PowerShell 실행 명령에 여백을 뺀 실측 폭을 참조해 `-PrintWidthM`을 지정합니다. macOS와 Linux에서 펌웨어를 올릴 때는 [Arduino IDE 절차](localization_app/firmware/xiao_camera_stream/README.md)를 사용합니다.

새 Windows PC에서 **1**을 처음 사용하기 전에 [Arduino CLI](https://arduino.github.io/arduino-cli/1.3/installation/)를 설치하고 `PATH`에 추가한 뒤 PowerShell을 다시 엽니다. 아래 명령은 공식 카메라 예제를 포함한 ESP32 코어를 설치합니다. 이미 보드가 스트림을 송출 중이라면 Python 환경을 설치한 뒤 **2**로 연결할 수 있습니다.

```powershell
$esp32Index = 'https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json'
arduino-cli core update-index --additional-urls $esp32Index
arduino-cli core install esp32:esp32@3.3.5 --additional-urls $esp32Index
```

새 2.4GHz Wi-Fi 정보는 `localization_app/firmware/xiao_camera_stream/wifi_secrets.h`에 저장합니다. 처음 만들 때는 같은 폴더의 `wifi_secrets.example.h`를 복사합니다. PC를 같은 네트워크에 연결하고 XIAO를 USB로 꽂습니다. 시리얼 모니터와 열린 카메라 스트림을 닫은 뒤, 저장소 루트의 PowerShell에서 실행합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File ".\localization_app\tools\start_xiao_camera.ps1"
```

스크립트가 공식 `CameraWebServer` 예제에 설정을 복사하고 빌드·업로드합니다. 이어서 USB 시리얼에서 IP를 읽고 `http://IP:81/stream`을 출력한 뒤 Python 영상 창을 엽니다. RESET 안내가 뜨고 부팅 로그가 보이지 않으면 보드의 RESET을 한 번 누릅니다. 장치가 여러 대라면 `-Port COM5`처럼 선택합니다. `-NoPreview`는 새 스트림 주소까지만 출력하고, `-BuildOnly`는 컴파일에서 끝냅니다.

위치 추정은 `-Localize`를 사용합니다. 현재 스트림 주소를 `-CameraUrl`로 넘기면 Python 위치 추정만 시작합니다. 카메라 영상을 띄운 이전 창이나 브라우저 스트림을 먼저 닫습니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File ".\localization_app\tools\start_xiao_camera.ps1" -Localize -CameraUrl "http://BOARD_IP:81/stream"
```

명령의 `RemoteSigned` 실행 정책은 이 명령으로 연 Windows PowerShell 프로세스에 적용됩니다. 주소는 시리얼에서 확인한 현재 IP를 사용합니다. `-Localize`는 자동 축척과 결합 조명·그림자 보정을 적용하며 기본으로 연속 추적합니다. `-GlobalOnly`를 붙이면 정지 카메라의 프레임별 전역 위치를 시험할 수 있습니다. `-PrintWidthM 0.18`은 인쇄 지도 폭이 18 cm일 때 실제 관측 폭 표시를 위한 예시입니다. Wi-Fi 설정 변경부터 진행하려면 `-Localize`만 지정합니다.

실행 스크립트의 기본값은 `-OpenCvThreads 1`과 `-GuiIntervalMs 33`입니다. `Camera FPS`는 MJPEG 수신률이고, 위치 추정 상태의 `Low match score` 또는 `Insufficient visible buildings`는 입력 영상이 지도 정합 기준을 통과하지 못했다는 뜻입니다. 전역 재시도 대기 중에는 `Last search`에 직전 정합 이유와 IoU가 표시됩니다.

현재 XIAO와 지도 표본에서 카메라가 읽은 건물색에 Hue 편차가 관측됐습니다. 이 조합의 시작값은 `-HueShift 3 -HueBandWidth 15 -SaturationTolerance 78`이며 명령의 끝에 추가합니다. Hue를 보정한 뒤 비슷한 색을 15단위씩 묶으며, 카메라와 지도에 동일한 묶음을 적용합니다. 실물 출력 후에는 실제 출력물의 색으로 다시 검증합니다.

## 검증 기록

| 검증 | 결과 |
| --- | --- |
| 위치 추정 자동 테스트 | 2026-11-20 폴더 재분류 후 94개 통과 |
| 카메라 색 보정 전처리 | 320 × 240 XIAO 녹화 48프레임에 밝기·채널 게인을 적용해 9회 교차 측정했습니다. 프레임당 중앙값 10.91 → 1.90 ms, 출력 픽셀 전체 일치. [측정 조건](localization_app/README.md) |
| 지도·데이터 통합 | 동일한 OSM·지도 JSON·지도 PNG 사본 4개를 통합해 3,735,859바이트를 줄였습니다. 재생성한 지물 JSON의 SHA-256이 AprilTag 배치에 등록된 값과 일치합니다. |
| 정지 가상 영상 벤치마크 | 25/33 채택, 채택된 위치 RMSE 0.81 cm, 평균 yaw 오차 1.72° |
| 연속 가상 영상 벤치마크 | 125/137 관측 채택, 131/137 프레임에서 필터 오차 5 cm 이내, 지역 매칭 평균 141 ms |
| 랜덤 뷰 블러 비교 | 9×9 Gaussian, σ=2에서 원본·블러 각각 30/30 채택, 위치 RMSE 1.13 cm·0.93 cm |
| 로컬 모의 QVGA 스트림 | 3초 동안 60프레임, 20.0 FPS, JPEG 디코딩 평균 0.50 ms |
| 실물 XIAO 공식 예제 · 새 Wi-Fi | 320 × 240, 15초 동안 412프레임, 평균 27.47 FPS, 연결 중단·JPEG 오류 각각 0회 |
| 합성 높이 변화 13장 | 기존 8/13 → 자동 10/13 채택. 채택된 위치 RMSE 1.34 → 0.67 cm. 자동 전역 평균 517 ms, 연속 시험의 지역 평균 73 ms. [결과 CSV](data/experiments/localization/height_robustness/synthetic_20261116/comparison.csv) |
| 합성 조명·그림자 변화 33장 | 기존 13/33 → 결합 31/33 채택. 채택된 위치 RMSE 10.45 → 0.65 cm, 오측정 2 → 0건. 결합 방식의 이동 그림자 지역 추적 평균 137 ms. [결과 CSV](data/experiments/localization/shadow_robustness/synthetic_20261116/comparison.csv) |
| 자체 펌웨어 빌드 | Arduino CLI 1.3.1, Arduino-ESP32 3.3.5에서 컴파일·링크 통과. Flash 961,887바이트(28%) |
| 출력 지도 | EPS `HiResBoundingBox` 기준 2,500.000000 × 2,500.000000 mm. 2000 × 2000px 미리보기와 기준 PNG의 RGB 픽셀 일치 |

대형 출력 지도에서의 위치 오차와 비행 중 기울기의 영향은 실측 전입니다. [상세 벤치마크](localization_app/README.md#합성-검증-결과) · [출력 검증 기록](map_tools/README.md#검증-결과)
