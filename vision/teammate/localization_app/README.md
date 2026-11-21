# KAIST 드론 영상 기반 위치 추정

2.5 m × 2.5 m KAIST 캠퍼스 모형을 촬영한 카메라 영상에서 위치, yaw, 관측 폭을 추정한다. 실시간 XIAO 영상과 녹화본은 건물·도로 정합과 AprilTag를 한 추적기에서 사용할 수 있다. 지도에는 건물 60개, 도로 중심선 46개, 정문 위치가 있으며 도로 선폭은 인쇄용이다. 지도 원본 © OpenStreetMap contributors. [지도 JSON](../data/maps/KAIST_model_features_2p5m.json)

## 설치 및 실행

[운영체제별 설치 절차](../README.md#실행)에 따라 저장소 루트에서 가상 환경을 만든다. 아래 명령은 `localization_app` 폴더에서 가상 환경의 Python으로 실행한다. Windows PowerShell에서 환경을 활성화하지 않았다면 `python` 대신 `..\.localization-venv\Scripts\python.exe`를 호출할 수 있다.

| 작업 | 명령 |
| --- | --- |
| 정지 영상 GUI | `python main.py` |
| 정지 영상 PNG | `python main.py --x 1.73 --y 2.02 --yaw 37 --save ../data/experiments/localization/snapshot.png` |
| 연속 추적 GUI | `python main.py --track square` |
| 랜덤 뷰·블러 비교 GUI | `python tests/random_view_test.py --seed 42` 또는 `python main.py --random-test --seed 42` |
| 랜덤 뷰 PNG와 JSON | `python tests/random_view_test.py --seed 42 --save ../data/experiments/localization/comparison.png` |
| 정지 영상 벤치마크 | `python main.py --benchmark` |
| 연속 추적 벤치마크 | `python main.py --benchmark-tracking --tracking-csv ../data/experiments/localization/tracking_run.csv` |
| 랜덤 뷰 배치 평가 | `python tests/random_view_test.py --seed 42 --random-count 30 --random-json ../data/experiments/localization/blur_run.json` |
| 전체 자동 테스트 | `python -m unittest discover -s tests -v` |

GUI에는 TkAgg 등 Matplotlib 대화형 백엔드가 필요하다. `--save`와 벤치마크는 창 없이 실행한다. `tests/random_view_test.py`는 실험 실행 파일이고 `tests/test_random_view.py`는 자동 검증 파일이다.

## 실제 XIAO 카메라

현재 보드에 올라간 Arduino-ESP32 공식 `CameraWebServer` 예제는 기본 QVGA JPEG를 송신한다. 같은 Wi-Fi에 연결한 PC에서 시리얼 모니터(115200 baud)에 표시된 주소를 열고 **Start Stream**을 누른다. 브라우저에서 스트림을 닫고 Python 수신기를 실행한다. [공식 예제](https://github.com/espressif/arduino-esp32/tree/3.3.5/libraries/ESP32/examples/Camera/CameraWebServer) · [자체 펌웨어 업로드 절차](firmware/xiao_camera_stream/README.md)

```powershell
$cameraUrl = 'http://BOARD_IP:81/stream'  # 공식 예제. 자체 펌웨어는 http://BOARD_IP/stream 사용
python main.py --camera-test --camera-url $cameraUrl
python tools/test_camera_stream.py --url $cameraUrl --seconds 30 --csv ../data/experiments/localization/xiao_real_camera/stream_metrics_antenna.csv
python main.py --live-localization --camera-url $cameraUrl --global-only --auto-scale
python main.py --live-localization --camera-url $cameraUrl --auto-scale
```

`--camera-test`는 매칭 없이 영상, 해상도, 수신 FPS, 디코딩 시간과 연결 상태를 보여준다. `--global-only`는 매 프레임 전역 매칭을 시도하므로, 먼저 카메라를 고정한 상태에서 시험할 때 사용한다. `--ground-truth X Y`를 추가하면 그 좌표를 평가에만 사용한다. 연속 모드는 관측으로 갱신한 위치와 예측 위치를 표시한다. 일반 카메라 화면은 최신 수신 프레임을 표시한다. 영상과 위치 결과의 프레임 번호를 각각 표시한다. 연결이 끊기거나 마지막 수신 이후 2초가 지나면 영상과 위치를 숨긴다. 위치 결과 자체의 유효 시간은 `--result-age`(기본 5초)로 조정한다. 새 연결과 해상도 변경에는 추적을 재초기화한다.

수신기는 단일 슬롯에 최신 BGR 프레임을 유지한다. **Skipped**는 위치 추정 작업자가 선택하지 않은 디코딩 프레임, **Stale**은 작업 직전에 입력 유효 시간을 초과해 거절한 프레임이다. **Sender gaps**는 펌웨어 번호의 누락, **Bad JPEG**는 디코딩 실패다. 송신 번호가 없는 서버에서 Sender gaps는 알 수 없다. `tools/test_camera_stream.py --match`는 수신과 전역 매칭을 빠르게 점검하는 별도 도구다.

### AprilTag와 지도 정합 통합

기본 `fusion` 모드는 같은 프레임에서 지도 정합과 AprilTag 자세를 구하고, 해당 프레임을 한 번만 칼만 필터에 반영한다. `apriltag` 모드는 태그로만 절대 위치를 갱신한다. `map` 모드는 기존 건물·도로 추적을 사용하며 `--global-only`로 매 프레임 전역 정합도 실행할 수 있다. 태그 지도를 선택한 GUI에서 `1`, `2`, `3` 키로 각각 `map`, `apriltag`, `fusion`을 선택할 수 있다. 모드를 바꾸면 다음 프레임에서 추적이 새로 시작한다.

| 출력물 | 옵션 | 지도 기준 |
| --- | --- | --- |
| 태그 없는 기존 지도 | `--map-version original_map` | 기존 기준 JSON과 `KAIST_model_reference_2p5m.png` |
| 태그가 있는 지도 | `--map-version apriltag_map` | 같은 기준 JSON과 `../data/maps/apriltag_map.json`의 `tag36h11` ID 0–38 |

기본 제공 지도에서 `fusion`과 `apriltag`는 `apriltag_map`을 선택한다. 기존 `map` 경로는 `original_map`을 사용한다. 태그가 있는 출력물에서 지도 정합만 비교할 때 `--localization-mode map --map-version apriltag_map`으로 실행하면, 검출한 태그의 종이 영역을 건물·도로 점수에서 제외한다. 사용자 지정 레이아웃은 `--apriltag-map`으로 연결하며, JSON의 지도 크기와 기준 지도 SHA-256이 일치해야 한다.

태그 지도와 마커는 같은 비율로 출력한다. `--print-width-m`에는 **여백을 뺀 인쇄 지도 정사각형의 실측 너비**를 미터 단위로 넣는다. `Start_XIAO.cmd`는 2.5 m를 전달한다. 2.5 m 모델에서 검은 태그 한 변은 0.040 m, 종이 한 변은 0.050 m다. 예를 들어 실측 지도 폭이 0.200 m라면 검은 태그는 3.2 mm여야 한다. A4에서 지도와 마커를 따로 출력할 경우 이 비율을 자로 확인한다. 결과 `x`, `y`, `span`은 항상 2.5 m 모델 단위다.

기존 체커보드 도구로 XIAO가 송신하는 원본 해상도에서 캘리브레이션을 생성한다. 아래 코너 수와 칸 길이는 체커보드에 맞춰 지정한다.

```powershell
$cameraUrl = 'http://BOARD_IP:81/stream'
python tools/calibrate_camera.py collect --url $cameraUrl --output ../data/recordings/calibration_frames --count 20
python tools/calibrate_camera.py fit --images ../data/recordings/calibration_frames --corners 9 6 --square-mm 25 --output ../data/recordings/xiao_320x240.npz
Copy-Item config/camera_config.yaml config/xiao_320x240.yaml
```

복사한 YAML의 `calibration`을 다음과 같이 지정한다.

```yaml
calibration:
  enabled: true
  calibration_file: ../../data/recordings/xiao_320x240.npz
```

```powershell
$printWidth = Read-Host '여백을 뺀 인쇄 지도 폭 (m)'
$cameraConfig = (Resolve-Path .\config\xiao_320x240.yaml).Path
python main.py --live-localization --camera-url $cameraUrl --camera-config $cameraConfig --print-width-m $printWidth --localization-mode fusion --auto-scale --hybrid-matching
python main.py --live-localization --camera-url $cameraUrl --camera-config $cameraConfig --print-width-m $printWidth --localization-mode apriltag
python main.py --live-localization --camera-url $cameraUrl --localization-mode map --auto-scale --hybrid-matching
python main.py --video-file ../data/recordings/tagged.avi --camera-config $cameraConfig --print-width-m $printWidth --localization-mode fusion --auto-scale --hybrid-matching --save-performance-log ../data/experiments/localization/apriltag_fusion/replay.csv
& .\tools\start_xiao_camera.ps1 -Localize -CameraUrl $cameraUrl -CameraConfig $cameraConfig -LocalizationMode fusion -PrintWidthM $printWidth -HybridMatching
```

보정값이 없으면 영상의 태그 코너와 지도에 등록된 코너 사이에 2D 변환을 맞춰 지도 좌표, yaw, 관측 폭을 근사한다. 카메라가 지도에 거의 수직인 조건을 가정한다. 모든 코너를 같은 변환으로 설명하기 어려운 관측은 거절한다. 화면의 `Planar fit`과 `Corner score`(0–1)는 맞춘 방식과 코너 일치도를 표시한다.

`fusion`에서는 코너 일치도가 낮을수록 태그 관측의 불확실성을 높여 융합 비중을 낮춘다. 두 관측이 크게 어긋나면 채택 가능한 건물·도로 정합 결과를 사용한다. `apriltag`에서는 채택된 평면 근사로 위치를 갱신한다. 실측 보정값과 인쇄 폭이 제공되면 카메라의 3D 자세와 높이를 구하는 PnP를 사용한다. 캘리브레이션 파일의 해상도와 입력이 다르면 해당 태그 자세를 거절한다. 영상 해상도나 카메라 Crop을 바꾸면 해당 모드에서 다시 보정한다.

카메라 화면과 태그를 검출한 프레임이 같을 때 ID, 코너, 중심과 최소 변 길이를 영상 위에 그린다. 처리가 늦어지면 최신 영상을 계속 표시하고 태그 ID와 위치는 마지막 처리 결과에서 읽어 상태 영역과 지도에 표시한다. 지도에서 파랑 `+`는 지도 정합, 자주색 `×`는 AprilTag, 녹색은 관측 갱신 위치, 갈색은 예측 위치다. 같은 프레임에 태그와 조명 또는 도로 진단이 모두 있으면 `D` 키로 최신 영상과 처리된 진단 화면을 전환한다.

`--min-tag-side-px`의 기본값은 원본 영상에서 14 px다. `--map-period-ms`를 지정하면 추적 중 예측 게이트를 통과한 보정된 태그 관측이 있을 때 지도 정합 간격을 늘린다. 합성 프레임으로 세 모드를 비교할 수 있다. [AprilTag 통합 보고서](../data/experiments/localization/apriltag_fusion/integration_report.md)

```powershell
python benchmark_apriltag_fusion.py --donor-frames --output ../data/experiments/localization/apriltag_fusion/replay_check.json
```

### 건물·도로 융합 정합

프로젝트 상위 디렉터리에서 기존 XIAO에 연결한다. 아래 Hue 옵션은 이전 A4 인쇄물에서 확인한 값이다. 실제 인쇄물의 지도 너비를 측정했다면 `-PrintWidthM`에 m 단위로 입력해 실제 관측 폭 표시를 사용할 수 있다.

```powershell
$cameraUrl = 'http://BOARD_IP:81/stream'
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File ".\localization_app\tools\start_xiao_camera.ps1" -Localize -CameraUrl $cameraUrl -HybridMatching -HueShift 3 -HueBandWidth 15 -SaturationTolerance 78
```

`-RoadDebug`를 추가하면 원본, 건물 색상 마스크, 검출 도로, 도로 경계, 채택 후보의 기준 도로 투영, 겹침 영상을 볼 수 있다. 겹침 영상은 관측만 있는 픽셀을 파랑, 기준에만 있는 픽셀을 주황, 양쪽에 있는 픽셀을 초록으로 표시한다. 실측 표시 정확도는 지도에 수직인 하향 촬영과 렌즈 보정에 영향을 받는다.

Python을 직접 실행하거나 녹화본을 재생할 때도 같은 옵션을 사용할 수 있다.

```powershell
python main.py --live-localization --camera-url $cameraUrl --auto-scale --illumination-method combined --shadow-aware --hybrid-matching --hue-shift 3 --hue-band-width 15 --saturation-tolerance 78 --opencv-threads 1
python main.py --video-file ../data/recordings/session01.avi --auto-scale --illumination-method combined --shadow-aware --hybrid-matching --road-debug
python benchmark_hybrid.py
```

- 기본값은 기존 건물 전용 정합이다. `--matching-mode building|road|fixed|adaptive` 또는 PowerShell의 `-MatchingMode`로 네 모드를 비교한다. `--hybrid-matching`과 `-HybridMatching`은 `adaptive`를 선택한다.
- 기준 도로 영역은 출력 미리보기의 JSON 등록 회색 잉크에서 생성한다. 중심선과 분기점은 JSON 좌표에서 계산한다. 건물과 도로는 같은 위치·yaw·span 변환을 사용한다. 도로 점수는 영역 IoU에 기준 경계까지의 거리를 반영한다. 도로 검출에는 국소 종이 밝기와 채도, 건물 경계, 그림자 신뢰도를 사용한다. Hue 보정은 건물에 적용한다.
- 고정 모드는 두 특징이 모두 유효할 때 건물 65%, 도로 35%를 사용한다. 적응형은 건물의 유효 면적과 도로의 유효 면적·방향 다양성으로 비중을 정한다. 건물 채택에 성공한 건물 풍부 장면은 기존 결과를 사용한다. 도로 전용 관측은 여러 방향의 구조, 최소 점수, 다른 위치·yaw·span 후보와의 점수 차를 요구한다.
- 그림자 신뢰도를 쓰는 도로 전용 정합에서는 화면 가장자리에 붙은 넓은 저신뢰 영역을 IoU 계산에서 제외한다. A4 정문처럼 종이 바깥이 함께 보일 때 그 영역의 기준 도로가 불일치로 집계되는 것을 줄인다.
- 실제 관측 소스는 `BUILDING`, `HYBRID`, `ROAD_ONLY`로 표시된다. 관측이 거절되면 `PREDICTION_ONLY`와 위치 표준편차가 표시된다. 마지막 관측 후 1.2초가 지나거나 위치 표준편차가 0.28 지도 m 이상이면 위치 출력을 중단하고 재탐색한다.

[실험 보고서](../data/experiments/localization/hybrid_map_matching/hybrid_matching_report.md) · [합성 비교 CSV](../data/experiments/localization/hybrid_map_matching/comparison.csv) · [XIAO 비교 CSV](../data/experiments/localization/hybrid_map_matching/real_camera/comparison.csv)

### 실시간 처리와 지연 측정

기존 수신 스레드는 MJPEG를 계속 디코딩해 한 슬롯을 덮어썼다. GUI는 50ms마다 그 슬롯을 비우고, 기존 추정 작업이 끝났을 때 다음 작업을 제출했다. 위치 추정용 대기열은 쌓이지 않았고, 소비한 ID를 다시 제출하는 흐름도 없었다. 다음 작업을 시작하는 시점은 GUI 갱신에 의존했다. 추적 실패 시 새 프레임으로 예측·재탐색을 이어갔으며, Kalman Filter는 이미 입력 타임스탬프 간 차이로 예측했다.

현재는 수신, 위치 추정, GUI 갱신이 독립적으로 동작한다. 작업자는 시작 가능 시점에 최신 슬롯을 읽는다. 소비자는 각자 마지막 처리 ID를 관리하며, 슬롯의 BGR 이미지가 필요할 때 참조를 공유한다. 입력 수정이 필요한 소비자는 복사본을 사용한다. 향후 UGV 작업도 같은 소스의 `wait_latest(after_id, timeout)`에서 각자 최신 프레임을 받아 실행 주기를 관리할 수 있다. 두 작업의 CPU·메모리 경쟁과 동시 실행 성능은 별도 실험 항목이다.

```powershell
$python = (Resolve-Path ..\.localization-venv\Scripts\python.exe).Path
$cameraUrl = 'http://BOARD_IP:81/stream'  # 현재 XIAO 주소로 교체
& $python main.py --live-localization --camera-url $cameraUrl --auto-scale --localization-period-ms 150 --max-frame-age-ms 300 --gui-interval-ms 100 --performance-monitor --save-performance-log ../data/experiments/localization/frame_timing/live_trial.csv
```

- `--localization-period-ms`는 최소 시작 간격이다. `0`은 기본값인 Unlimited이며, `100`, `150`, `200`을 선택할 수 있다. 한 추정이 간격보다 길면 끝나는 즉시 새 프레임을 확인한다.
- `--max-frame-age-ms`는 처리 시작 전에 검사하는 한계다. 기본값 300ms는 진단 초기값이다. 초과 시 ID, 경과 시간과 거절 내역을 로그와 GUI에 표시한다. GUI에서 지난 결과를 숨기는 `--result-age`는 별도로 적용된다.
- `--gui-interval-ms` 기본값은 50ms다. `--performance-monitor`는 최근 60개 처리 시간을 그리고 실제 캔버스 렌더링 시간을 별도로 요약한다. `--save-performance-log`에 새 CSV 경로를 지정할 수 있다.
- Frame Age는 JPEG 전체를 받은 PC 시점부터 처리 시작까지, Processing Time은 처리 시작부터 결과 계산 완료까지, Receive-to-Result는 PC 수신부터 결과 계산 완료까지다. PC 시각에는 `perf_counter()`를 사용한다. JPEG 파트 읽기 시간은 경계 뒤 헤더와 본문을 읽는 시간이며, 촬영 시각과 네트워크 단방향 지연은 계측할 수 없다.
- 로그는 선택된 프레임의 시간, 처리 또는 거절 상태, 직전에 건너뛴 개수와 결과를 기록한다. 단계별 분포는 처리한 프레임으로 계산한다. 전처리, HSV 분류, 조명·그림자 보정, 전역/지역 검색, 추적, 진단 이미지 계산이 해당하는 경우 따로 기록된다. 창을 닫을 때 출력하는 GUI 분포는 단계별 최근 최대 600개 표본을 사용한다.
- 실시간 관측 시각은 PC JPEG 수신 시각으로 근사한다. 재생에서는 녹화 타임스탬프의 간격을 추적에 사용한다. 누락 프레임 간격까지 반영해 관측 시각에 필터를 갱신하며, 표시용 XY만 일정 속도 모델로 결과 시각까지 외삽한다. 예측 상태의 yaw는 마지막으로 채택한 관측값이다.
- 수신·재생 입력 자체가 2초 이상 끊겼거나 연결 또는 해상도가 바뀌면 추적기를 초기화한다. 긴 처리로 작업자가 프레임을 건너뛴 경우는 연속 입력으로 판단한다. 재생에서도 Frame Age는 프레임이 실제로 슬롯에 들어온 뒤부터 계산하며, 녹화 시간표 대비 발행 지연은 별도 열에 기록한다.

### 처리 주기 비교

같은 카메라 높이·방향과 검색 설정으로 먼저 정지 실험을 한다. 실측 좌표나 방향이 있으면 각각 `--ground-truth X Y`, `--truth-yaw DEG`를 사용한다. 이동 실험은 별도 출력 경로에서 같은 경로와 비슷한 이동 속도로 반복한다. 네 모드는 순차 실행되므로 모드별 동일 입력이 필요하면 녹화본을 재생한다.

```powershell
& $python main.py --live-localization --camera-url $cameraUrl --auto-scale --illumination-method combined --shadow-aware --benchmark-live-pipeline --benchmark-seconds 60 --benchmark-output ../data/experiments/localization/frame_timing/live_stationary
& $python main.py --live-localization --camera-url $cameraUrl --auto-scale --illumination-method combined --shadow-aware --benchmark-live-pipeline --benchmark-scenario moving --benchmark-output ../data/experiments/localization/frame_timing/live_moving
& $python main.py --video-file ../data/recordings/session01.avi --auto-scale --illumination-method combined --shadow-aware --benchmark-live-pipeline --benchmark-output ../data/experiments/localization/frame_timing/replay_stationary
```

도구는 기본 출력 경로 `../data/experiments/localization/frame_timing/` 또는 지정 경로에 `performance_unlimited.csv`, `performance_100ms.csv`, `performance_150ms.csv`, `performance_200ms.csv`, `performance_summary.json`, `performance_report.md`를 쓴다. CSV는 프레임별 계측이고, JSON은 평균·중앙값·P95·최대 및 모드별 집계다. CPU는 Python 프로세스의 코어 사용 시간으로 100%가 한 코어에 해당한다. 측정 구간 종료 후 완료된 작업은 CSV에 `drained`로 표기한다. Matching Success는 실제 검색 시도의 성공 비율, Accepted는 구간 안에서 완료한 처리 프레임 중 추적기에 채택된 관측 비율이다. Tracking Loss는 `RELOCALIZING`으로의 전이 횟수다. 재생 입력은 60초보다 길게 준비한다. 스트림을 시작할 수 없거나 측정 구간을 채우지 못하면 도구가 불완전 상태를 기록하고 실패 코드로 종료한다.

비교 시 P95 Frame Age와 Receive-to-Result, 위치 추정 FPS, 채택률과 추적 손실, 오차를 먼저 확인한다. 이 값이 충분히 비슷할 때 CPU 차이를 반영한다. 이전에 보고한 지역 매칭 141ms는 기존 합성 추적 실험의 참고값이다.

정지 합성 영상 60초 재생을 네 모드에서 완료했다. [재생 실험 보고서](../data/experiments/localization/frame_timing/synthetic_stationary/performance_report.md) · [단계별 계측 JSON](../data/experiments/localization/frame_timing/synthetic_stationary/performance_summary.json)

### 성능 최적화 실험 재현

성능 비교 입력은 `../data/experiments/localization/performance_optimization/input/stationary.avi`에 있다. 240 × 180의 정지 합성 영상이며, CSV 사이드카가 25 FPS 재생 시간표를 제공한다. `manifest.json`에는 AVI, 사이드카, 지도 해시와 생성 설정이 들어 있다. 새 입력을 만들 때는 `tools/generate_performance_fixture.py --output <새 폴더>`를 사용한다.

```powershell
$video = '../data/experiments/localization/performance_optimization/input/stationary.avi'
& $python main.py --video-file $video --auto-scale --illumination-method combined --shadow-aware --ground-truth 1.73 2.02 --truth-yaw 37 --benchmark-live-pipeline --benchmark-seconds 60 --benchmark-warmup-seconds 4 --benchmark-repeats 3 --benchmark-seed 42 --opencv-threads 1 --benchmark-output ../data/experiments/localization/performance_optimization/replay_comparison
& $python tools/profile_localization.py --video $video --opencv-threads 1 --quality --output ../data/experiments/localization/performance_optimization/profile_comparison
$study = '../data/experiments/localization/performance_optimization'
& $python tools/compare_localization_stages.py --video $video --baseline-sources "$study/baseline/reference_sources.zip" --baseline-profile "$study/profiling/baseline/profile.json" --output "$study/paired_comparison.json"
```

반복 실행은 첫 순서를 시드로 정하고 다음 반복마다 회전한다. 워밍업의 관측은 다음 추정의 초기 상태에 반영되며 시간·품질 통계는 지정한 측정 구간에서 수집한다. 콜드 스타트의 첫 전역 정합을 포함하려면 워밍업을 0으로 설정한다. `--benchmark-periods 0 200`처럼 비교 대상을 고를 수 있다. `--opencv-threads`로 전체 Python 프로세스의 OpenCV 설정을 바꿀 수 있다. 생략하면 설치된 OpenCV의 기본 설정을 사용한다.

요약 JSON에는 영상·설정·코드 해시, 실행 순서, OpenCV·NumPy 환경, 구간별 CPU 사용률과 시작·종료 시점의 프로세스 RSS가 기록된다. 정답을 입력한 경우 RMSE의 분모는 채택된 관측이며, 오측정은 위치 오차 5cm 초과 또는 yaw 오차 15° 초과로 집계한다. 추적 복구 시간은 `RELOCALIZING` 진입부터 복구된 관측까지의 입력 시간표로 계산한다.

기존 팔레트의 순환 hue와 S/V 판정은 LUT로 유지하고, 색상별 건물 정리는 여백을 둔 영역에서 수행한다. 정합에는 미리 계산한 지도 색상 경계와 프레임별 색상 목록을 사용한다. 조명·그림자 처리에서는 종이 표본을 공유하고, 디버그 화면에서는 정합에 사용한 레이블을 재사용한다. [최적화 결과](../data/experiments/localization/performance_optimization/optimization_report.md)

### 녹화와 보정

`--record`만 지정하면 `../data/recordings/`에 촬영 시각을 포함한 이름으로 AVI와 CSV·JSON을 저장한다. 아래 예시처럼 파일명까지 지정하면 해당 경로를 사용한다.

카메라 YAML에서 밝기나 채널별 게인을 바꾸면 전처리기가 채널별 변환표를 만들어 각 프레임에 적용한다. XIAO의 320 × 240 녹화 48장에 밝기 1.13과 BGR 게인 (0.93, 1.08, 1.03)을 적용한 전후 교차 측정 9회에서 프레임당 전처리 시간 중앙값은 10.91 ms에서 1.90 ms로 줄었고 모든 출력 픽셀이 일치했다. OpenCV 5.0.0 스레드 1개와 NumPy 2.4.6을 사용했다. [측정에 사용한 XIAO 녹화본](../data/experiments/localization/hybrid_map_matching/real_camera/timed-captures/a4_stationary.avi)

```powershell
python main.py --camera-test --camera-url $cameraUrl --record ../data/recordings/session01.avi
python main.py --video-file ../data/recordings/session01.avi --global-only
python tools/calibrate_camera.py collect --url $cameraUrl --output ../data/recordings/board --count 15
python tools/calibrate_camera.py fit --images ../data/recordings/board --corners 9 6 --square-mm 20 --output ../data/recordings/lens.npz --preview ../data/recordings/undistort.png
python tools/calibrate_camera.py span --image ../data/recordings/nadir.png --length-m 0.25 --x 50 150
```

녹화는 소비한 원본 프레임을 MJPG AVI로 저장하고, 입력 시각·해상도·추정 결과를 CSV로, 설정을 JSON으로 저장한다. 높은 수신률을 보존하려면 매칭이 없는 `--camera-test --record`로 녹화한다. 출력 경로에는 새 이름을 사용한다. AVI 컨테이너의 10 FPS는 호환용이다. 프로젝트 재생기는 같은 이름의 CSV가 있으면 파일에 기록된 입력 간격에 맞춰 프레임을 공급한다. CSV가 없는 영상은 컨테이너의 평균 FPS를 사용한다.

보정 사진 수집은 Space로 저장하고 Esc로 끝낸다. `--corners`에는 인쇄된 체커보드의 내부 코너 개수, `--square-mm`에는 한 칸을 직접 잰 길이를 입력한다. 다양한 위치와 기울기에서 같은 해상도로 찍은 사진이 최소 10장 필요하다. 생성된 NPZ의 경로는 [카메라 YAML](config/camera_config.yaml)에 적고 보정을 켠다. `span`은 지면에 평행한 카메라 영상에서 길이를 아는 수평 구간의 픽셀 양 끝점을 이용한다. 계산한 값을 YAML의 `nominal_span_m`으로 저장하거나 CLI의 `--nominal-span`을 사용한다.

YAML에는 영상 회전·반전·색 보정과 렌즈 파일을 지정할 수 있다. 리사이즈는 종횡비를 유지한다. 기본 YAML의 화각, 고도, 렌즈 계수는 미측정 상태다. YAML에 A4용 `height_m`과 `hfov_deg`를 함께 지정해 폭을 계산할 때는 높이를 지도 좌표계로 환산한다. 렌즈 보정은 영상 가장자리의 광학 왜곡에 적용된다. 보정된 AprilTag PnP로 카메라의 기울기를 계산할 수 있다. [OpenCV 카메라 보정](https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html)

TT 탑재 시에는 카메라의 시야와 장착 방향을 다시 확인하고, TT 제어 Wi-Fi와 XIAO 스트림에 PC가 동시에 접근할 수 있는지 실험한다. 네트워크가 분리되어 있다면 별도 Wi-Fi 어댑터 사용을 검토한다.

### PC 검증 결과

- 2026-11-17 성능 최적화 후 전체 자동 테스트 63개가 통과했다. 조명 조건을 바꾼 합성 영상 39장에서 마스크와 채택 결과가 기존 코드와 일치했다.
- 2026-11-17 전체 자동 테스트 58개가 통과했다. 최신 프레임 교체, 중복 방지, 100/150/200ms 간격, 연산 초과, 오래된 입력, 재접속, 타임스탬프 재생, 전역/지역 계측을 검증했다.
- 2026-11-16 전체 Python 테스트 50개가 통과했다. 이전 40개에 조명·그림자 합성, 가중 IoU, 저신뢰 관측 거절, 높이 변화·그림자가 겹친 추적, 진단 화면·녹화 검증을 추가했다.
- localhost 모의 QVGA 서버에서 3초간 60프레임을 디코딩했다. 평균 수신률 20.0 FPS, 평균 JPEG 디코딩 0.50ms, 수신 간격 평균 50.58ms였다. 고정 좌표의 합성 JPEG를 모의 스트림으로 전달한 전역 매칭에서 약 0.7cm 오차가 나왔다. 합성 체커보드 12장으로 계산한 재투영 RMS는 0.073px였다.
- 2026-11-16 COM5의 XIAO에서 PSRAM 8MB와 OV3660을 확인했다. 공식 `CameraWebServer` 예제와 새 Wi-Fi에서 320 × 240 프레임 412장을 15초간 받았다(27.47 FPS, 연결 중단·JPEG 오류 0회). 안테나 장착 후 기존 방식으로 A4 지도의 특정 높이에서 위치를 인식했고, 높이를 조금 바꾸면 실패했다. 자동 축척의 실물 위치 오차는 미측정이다. [초기 실물 통합 기록](../data/experiments/localization/xiao_real_camera/experiment_report.md)

### HSV 진단과 실험 기록

`tools/inspect_camera_frame.py`에 `--image`로 저장 영상 한 장을 주거나 `--url`로 스트림에서 한 프레임을 받는다. `--output`에 새 디렉터리를 지정하면 원본, 전처리 영상, 색상 마스크, 나란히 배치한 비교 PNG, HSV 통계, 추정 CSV를 저장한다. `--hsv-roi X Y W H`는 전처리된 영상에서 아직 분류되지 않은 영역의 색도 확인할 수 있다. `--hsv-tolerances H S V`로 허용 범위를 바꾸고, 저장된 설정은 `--hsv-settings`로 불러온다. Live GUI에는 결정한 값을 `--hue-tolerance`, `--saturation-tolerance`, `--value-tolerance`에 적용한다.

## 조명과 그림자 대응

기존 HSV 분류기는 hue가 가장 가까운 지도 색을 찾고 saturation과 value의 허용 범위를 검사한다. 균일한 밝기 감소는 주로 value를 낮춰 건물 픽셀을 누락시켰다. 색온도와 그림자가 겹치면 hue와 saturation도 바뀌어 다른 건물 색으로 분류됐다. 남은 일부 색상만 우연히 다른 위치와 맞아 잘못된 측정이 채택된 합성 프레임도 있었다.

조명 보정 방법은 `--illumination-method`로 고른다.

| 방법 | 동작 |
| --- | --- |
| `none` | 카메라의 기본 기하·색 설정을 적용한 영상을 기존 HSV 정합에 전달한다. |
| `clahe` | LAB의 L 채널에 CLAHE를 적용하고 a, b를 유지해 BGR로 변환한다. `--clahe-clip-limit`와 `--clahe-grid X Y`의 기본값은 2.0과 8 × 8이다. BGR 변환 과정의 색 변화와 잡음 증폭은 비교 실험에서 확인한다. [OpenCV CLAHE](https://docs.opencv.org/4.x/d6/db6/classcv_1_1CLAHE.html) |
| `constancy` | 가까운 중성색 중 주변보다 밝은 종이 후보에서 채널별 색과 노출 게인을 추정한다. 기준 종이가 충분히 보이지 않으면 보정을 건너뛴다. |
| `field` | 종이 후보의 밝기를 가우시안으로 평활화해 저주파 조명장을 구하고, 어두운 곳에 채널 공통의 제한된 게인을 적용한다. |
| `combined` | 채널·노출 보정과 저주파 조명장 보정을 함께 적용한다. `--illumination-correction`의 기본 방법이다. |

그림자 검출은 보정 전 영상에서 주변 종이와의 밝기 차를 추정한다. 주변의 밝은 종이 표본을 사용해 좁은 회색 도로와 채도가 있는 어두운 건물의 영향을 줄인다. 결과는 그림자 마스크와 0–1 픽셀 가중치다. 이 가중치는 측정 확률이나 칼만 필터의 분산에 사용되지 않는다. 강한 그림자, 채널 포화, 노이즈로 사라진 색 정보는 복원되지 않을 수 있다.

`--shadow-aware`는 레이블과 가중치에 같은 평면 투영을 적용한다. 같은 색의 교집합에 가중치를 더하고, 분모는 `가중 영상 건물 + 가중 지도 건물 − 가중 교집합`으로 계산한다. 서로 다른 색끼리 겹친 픽셀은 양측 건물 면적에 남는다. 시야 밖 픽셀의 가중치는 0이다. 유효 면적과 유효 건물 픽셀이 너무 적거나, 낮은 유효 면적에서 구별되는 색이 부족하면 정합을 보류한다. 기존 IoU·차점 기준과 운동 게이트가 함께 적용된다. 관측을 잃으면 이전 위치에서 예측을 계속하고, 관측이 돌아오면 기존 추적기의 지역 탐색 또는 전역 재탐색으로 복구한다.

PowerShell에서 가상환경을 활성화하고 `localization_app` 폴더에서 실행한다. 새 XIAO IP를 사용하고 브라우저의 스트림은 닫는다.

```powershell
$cameraUrl = 'http://BOARD_IP:81/stream'
python main.py --live-localization --camera-url $cameraUrl --auto-scale --illumination-method combined --shadow-aware --shadow-debug
python main.py --random-test --lighting-case moving --seed 42
```

`--shadow-debug`는 동일 프레임의 조명 보정 전후 영상, HSV 건물 레이블, 그림자·신뢰도 마스크를 표시한다. 상태 창에는 유효 면적, 그림자 비율, 전처리·매칭 시간, 지도상 위치·방향과 추적 상태가 표시된다. 보정 전후 추적을 직접 비교하려면 같은 저장 영상을 `--video-file`로 열고 `--no-illumination-correction --no-shadow-aware`와 위 보정 옵션으로 각각 실행한다. 그림자 디버그 패널이 없는 모드에서는 최신 수신 프레임을 표시한다.

### 그림자 벤치마크 실행

```powershell
python benchmark_shadow.py --output ../data/experiments/localization/shadow_robustness/run01
python benchmark_shadow.py --camera-url $cameraUrl --cases clear local moving complex --print-width-m 0.18 --ground-truth 1.73 2.02 --truth-yaw 37 --output ../data/experiments/localization/shadow_robustness/a4_run01
python benchmark_shadow.py --images clear.png shadow.png --cases clear local --reference-image clear.png --output ../data/experiments/localization/shadow_robustness/replay01
python tools/inspect_camera_frame.py --url $cameraUrl --auto-scale --illumination-method combined --shadow-aware --shadow-debug --output ../data/experiments/localization/shadow_robustness/diagnostic01
```

라이브 비교는 카메라 위치와 yaw를 고정한 뒤 각 조명 조건을 만든 상태에서 Enter를 눌러 한 프레임을 저장한다. 인쇄 폭 0.18 m와 정답 좌표·yaw는 예시다. 대형 지도는 실제 인쇄 폭과 새 출력 폴더를 사용한다. `--video-file`과 `--frame-indices`로 저장 영상의 프레임을 읽을 수 있다. `--reference-image`는 같은 구도로 찍은 조명 기준 사진이며, 실물의 색 분류 수치는 이 사진의 HSV 분류와 비교한다. 정답 위치와 그림자·건물 마스크는 평가에만 사용한다.

`comparison.csv`에는 프레임별 위치·yaw 오차, 색 분류 정확도, 검출 면적, IoU, 유효 면적, 전처리·매칭 시간을 저장한다. `color_confusion.csv`는 기준 색에서 어떤 색으로 바뀌거나 누락됐는지 픽셀 수를 기록한다. 검색 점수와 함께, 채택된 위치에서 카메라 픽셀 좌표로 다시 계산한 일반·가중 IoU도 저장한다. 위치 오차 5 cm 초과 또는 yaw 오차 15° 초과인 채택을 오측정으로 집계한다. 합성 `tracking.csv`에는 이동 그림자, 높이 변화, 관측 상실 후 복구를 기록한다.

### 조명·그림자 검증 결과

320 × 240 합성 영상 39장을 사용했다. 조명 변화가 없는 6장과, 위치·방향을 같은 조건으로 유지한 채 조명을 바꾼 33장이 포함됐다. 모든 방법은 같은 자동 축척 설정과 HSV 임계값을 사용했다. 변화가 없는 6장은 다섯 방식 모두 채택했고 위치 RMSE는 0.55 cm였다.

| 변화가 있는 33장 | 채택 | 위치 RMSE(cm) | 오측정 | 색 분류 정확도 | 이진 건물 마스크 IoU | 전처리 / 정합 평균(ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 기존 HSV + IoU | 13 | 10.45 | 2 | 31.8% | 0.362 | <0.1 / 425 |
| LAB CLAHE | 10 | 8.56 | 1 | 29.7% | 0.340 | 1.8 / 441 |
| 색·노출 정규화 | 18 | 6.20 | 1 | 50.3% | 0.506 | 6.3 / 510 |
| 그림자 가중치 | 20 | 1.44 | 0 | 31.8% | 0.362 | 4.0 / 442 |
| 색·노출 정규화 + 조명장 + 그림자 가중치 | 31 | 0.65 | 0 | 75.3% | 0.764 | 9.7 / 592 |

위치 RMSE는 채택된 프레임만 사용한다. 변화 조건에서 기존 방식이 채택한 오측정은 2/13건(15.4%)이었고 결합 방식은 0/31건이었다. 평균 yaw 오차는 8.96°에서 0.29°로 줄었다. 결합 방식이 채택한 위치에서 다시 계산한 일반 IoU와 가중 IoU의 평균은 0.637과 0.701이었다. 픽셀 가중치의 평균은 72.8%였다.

기존 방식의 변화 조건에서 건물 픽셀의 평균 절대 HSV 변화는 OpenCV 단위로 H 1.4, S 4.4, V 103.4였다. 전체 영상에서 건물로 분류된 면적은 조명 변화가 없는 조건의 28.2%에서 변화 조건의 11.0%로 줄었다. 기준 지도 색 `#66f29a`가 `#66f27d`로 바뀐 경우가 다른 색으로 오분류된 픽셀 중 가장 많았고, `#f26666`은 검출 누락 픽셀이 가장 많았다. CLAHE 단독은 균일 회색 잡음 시험에서 표준편차를 2.02에서 5.57로 높였고 해당 위치 추정 비교에서도 채택 수가 줄었다.

결합 방식은 균일한 밝기 감소 5/5장, 이동 그림자 5/5장, 부드러운 그림자 6/6장을 채택했다. 매우 강한 그림자 한 장은 자세 모호성, 복합 조명 한 장은 낮은 정합 점수로 거절했다. 이동 그림자와 높이 변화를 포함한 연속 시험에서 기존·결합 방식 모두 18프레임 중 15개 관측을 사용했다. 어두워진 세 프레임 동안 예측 상태로 전환했고, 첫 관측 상실부터 재획득까지 입력 시간 기준 0.75초가 걸렸다. 결합 방식의 채택된 이동 그림자 지역 추적 평균은 137 ms였다. GUI 그리기 시간을 제외한 값이다.

이번에는 XIAO가 USB 목록에 없었고 마지막 스트림 주소가 응답하지 않아 실물 그림자 성능은 재측정하지 못했다. 실물에서는 같은 구도의 기준 사진과 높이·조명별 영상을 수집해 흰 종이가 적게 보이는 경우, 반사·포화, 강한 그림자와 기울기를 함께 확인한다. [프레임별 비교](../data/experiments/localization/shadow_robustness/synthetic_20261116/comparison.csv) · [색 분류 혼동](../data/experiments/localization/shadow_robustness/synthetic_20261116/color_confusion.csv) · [추적 CSV](../data/experiments/localization/shadow_robustness/synthetic_20261116/tracking.csv) · [진단 예시](../data/experiments/localization/shadow_robustness/soft_shadow_diagnostic/lighting_comparison.png)

## 자동 축척 탐색

평면을 수직으로 촬영하는 핀홀 모델에서는 `W = 2 × h × tan(HFOV / 2)`다. 같은 렌즈와 영상 설정에서 높이가 바뀌면 물리적 가로 관측 폭 `W`도 비례해서 바뀐다. 가상 카메라의 68°는 시뮬레이션 값이다. 실제 렌즈의 화각은 보정으로 구한다. 2.5 m 출력 지도에서는 물리적 관측 폭이 지도 좌표계의 관측 폭과 같다. A4 축소 지도에서는 여백을 뺀 인쇄 너비를 사용해 `지도 관측 폭 = 2.5 m × 실제 관측 폭 ÷ 인쇄 지도 폭`으로 환산한다.

고정 탐색은 기준 폭의 0.8–1.2배에서 다섯 후보를 비교하고, 연속 추적 중에는 직전 폭과 가까운 최대 세 후보로 좁힌다. 합성 비교에서 높이 1.50배와 1.75배에서는 검출 건물 비율이 약 32%와 29%로 충분했지만, 맞는 폭이 탐색 범위를 벗어나 색상 IoU가 채택 기준 0.36을 넘지 못했다. 0.60배에서는 가장 작은 후보를 채택하며 실제 폭을 약 33% 크게 추정했다.

`--auto-scale`은 기본 기준 폭 약 0.675 지도 m에 대해 0.5–2.0배의 로그 간격 후보 15개를 절반 해상도 지도에서 탐색한다. 서로 다른 위치·yaw·폭의 후보를 최대 여섯 개 유지하고, 주변을 기본 80 px/m에서 다시 비교한다. 기본 설정의 정밀 단계에서 yaw 간격은 2.5°, 폭 간격은 약 2.5%다. 점수와 차점 여유가 모두 기준을 넘은 측정만 채택한다. 후보 위치와 yaw가 비슷하더라도 폭이 1.35배 이상 다르면 별개의 해로 비교한다.

추적 중에는 마지막으로 채택한 폭 주변을 우선 정밀 탐색한다. 지역 폭 범위의 끝에 최적값이 걸리거나 관측을 거절하면, 다음 프레임에서 위치·yaw의 추적 범위 안에서 폭을 다시 넓힌다. 실패가 이어지면 기존 상태 머신이 전역 탐색으로 전환한다. 채택된 폭을 다음 입력에 사용하고, 실패하거나 운동 게이트에서 거절한 측정은 이 폭을 갱신하지 않는다. GUI는 Auto-scale 상태, 위치·yaw, 폭·배율, IoU·차점 여유, 실패 이유·처리 시간과 채택된 관측 영역을 표시한다. 관측 영역은 보정·회전 후 영상 종횡비를 사용한 평면의 직사각형이다.

PowerShell에서 `localization_app` 폴더의 가상 환경을 활성화한 뒤 실행한다. 0.18 m는 A4의 예시 인쇄 폭이다. 2.5 m 출력 지도는 `--print-width-m 2.5`를 사용한다. 이 값은 화면과 결과 CSV의 물리 단위 환산에 사용한다.

```powershell
$cameraUrl = 'http://BOARD_IP:81/stream'
python main.py --live-localization --camera-url $cameraUrl --auto-scale --global-only --print-width-m 0.18
python main.py --live-localization --camera-url $cameraUrl --auto-scale --print-width-m 0.18
python main.py --live-localization --camera-url $cameraUrl --no-auto-scale --nominal-span 0.83
```

`--scale-min`, `--scale-max`, `--scale-steps`는 자동 모드의 탐색 범위와 후보 수다. `--scale-refine-steps`는 전역 정밀 단계의 샘플 수다. 필요하면 `--nominal-span`으로 자동 모드의 탐색 기준 폭을 옮길 수 있다. `--scales`는 고정 모드의 후보 배율이다. `--video-file`에서도 같은 자동 모드 옵션으로 녹화 영상을 재생할 수 있다.

### 높이 변화 검증

아래 첫 명령은 높이만 바꾼 합성 영상과 지도 가장자리, 희박한 관측, 회전·노이즈·블러·색상 변화 및 강한 원근 조건을 비교한다. 실물 시험에서는 카메라의 x, y, yaw를 고정하고 높이만 바꾼다. 두 번째 명령은 높이마다 Enter를 눌러 XIAO에서 한 프레임을 받는다. 세 번째 명령은 저장된 영상에서 지정한 프레임을 읽는다. `--images`로 저장 사진을 직접 넘길 수도 있다. 높이와 정답 위치·yaw·폭은 결과 비교에 사용되며 두 추정기는 같은 영상을 입력받는다.

```powershell
python benchmark_scale.py --output ../data/experiments/localization/height_robustness/run01
python benchmark_scale.py --camera-url $cameraUrl --heights-m 0.08 0.10 0.12 --print-width-m 0.18 --ground-truth 1.73 2.02 --truth-yaw 37 --output ../data/experiments/localization/height_robustness/a4_run01
python benchmark_scale.py --video-file ../data/recordings/height.avi --frame-indices 1 90 180 --heights-m 0.08 0.10 0.12 --output ../data/experiments/localization/height_robustness/replay01
python tools/inspect_camera_frame.py --url $cameraUrl --auto-scale --debug-scale --output ../data/experiments/localization/height_robustness/diagnostic01
```

높이, 인쇄 폭, 좌표, yaw는 예시다. 카메라의 실제 가로 관측 폭을 쟀다면 `--observed-widths-m`으로 각 관측 폭을 입력해 추정 폭과 비교할 수 있다. `comparison.csv`와 `tracking.csv`는 원본 프레임 경로, 관측 정보, 추정 위치·yaw·폭, IoU·차점 여유, 오차, 시간과 채택 여부를 기록한다. `--debug-scale`은 단계별 폭과 최상위 IoU, 위치 후보, 차점 판정에 사용한 경쟁 후보를 JSON으로 저장하고, 검증 후보가 있으면 점수 그래프와 영상·지도 마스크 비교 이미지를 생성한다.

같은 합성 프레임을 사용한 기준 위치 높이 시험 결과다. 폭은 지도 좌표계의 m이며, 추정을 거절한 행의 위치 오차는 N/A다.

| 높이 비율 | 고정 성공 | 자동 성공 | 고정 오차(cm) | 자동 오차(cm) | 실제 폭(m) | 자동 추정 폭(m) |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 0.60 | 예 | 예 | 3.09 | 0.71 | 0.405 | 0.391 |
| 0.75 | 예 | 예 | 0.90 | 0.71 | 0.506 | 0.514 |
| 0.90 | 예 | 예 | 0.71 | 0.71 | 0.607 | 0.611 |
| 1.00 | 예 | 예 | 0.71 | 0.71 | 0.675 | 0.675 |
| 1.10 | 예 | 예 | 0.71 | 0.71 | 0.742 | 0.745 |
| 1.25 | 예 | 예 | 0.71 | 0.71 | 0.843 | 0.843 |
| 1.50 | 아니오 | 예 | N/A | 0.71 | 1.012 | 1.002 |
| 1.75 | 아니오 | 예 | N/A | 0.71 | 1.180 | 1.192 |

변형을 포함한 전체 13장에서는 채택률이 8/13에서 10/13, 채택된 위치 RMSE가 1.34 cm에서 0.67 cm로 개선됐다. 채택된 yaw 평균 오차는 2.38°에서 0.45°, 지도 폭 평균 절대 오차는 4.64 cm에서 0.61 cm로 줄었다. 평균 전역 처리 시간은 고정 464 ms, 자동 517 ms였다. 연속 시험의 17개 프레임에서 채택된 측정은 고정 8개, 자동 13개였고, 지역 탐색은 평균 62 ms와 73 ms가 걸렸다. 자동 모드의 범위 확대 시도 한 번에는 326 ms가 걸렸다. 큰 높이 변화 뒤 지역 탐색으로 복구했고, 세 프레임 동안 관측이 가려진 뒤에는 전역 탐색으로 위치와 폭을 다시 찾았다. [프레임별 비교](../data/experiments/localization/height_robustness/synthetic_20261116/comparison.csv) · [추적 결과](../data/experiments/localization/height_robustness/synthetic_20261116/tracking.csv) · [집계](../data/experiments/localization/height_robustness/synthetic_20261116/summary.json)

건물이 적은 영상, HSV 레이블을 뒤바꾸는 색 변화, 큰 기울기를 모사한 원근 영상은 자동 모드에서도 거절됐다. 이번 변경 시 XIAO가 USB에 연결되지 않았고 마지막 스트림 주소가 응답하지 않아 실물 높이 변화는 재측정하지 못했다. 실제 비행 전에는 인쇄 색과 노출, 렌즈 왜곡과 roll/pitch, 높이별 채택률 및 위치·yaw 오차를 확인한다.

## 동작과 설정

### 공통 좌표계와 매칭

원점은 모형 남서쪽이며 x는 동쪽, y는 북쪽으로 증가한다. 위치 단위는 m다. yaw는 북쪽을 0°로 하고 반시계 방향으로 증가한다. 지도 픽셀은 `u = x × ppm`, `v = (H − y) × ppm`으로 변환한다.

가상 카메라는 평면을 수직으로 내려다본다. 기본값은 고도 0.50 m, 수평 화각 68°, 240 × 180 px다. `--height`, `--hfov`, `--width`, `--frame-height`로 바꾸고, `--noise`, `--brightness`, `--bgr-gains`, `--seed`로 영상 변화와 재현성을 설정한다. 지도 렌더링은 기본 320 px/m(`--map-ppm`), 매칭은 80 px/m(`--match-ppm`)를 사용한다.

매칭은 HSV 건물 레이블을 만들고, 후보 yaw·축척마다 영상과 지도의 같은 색상 영역을 상관시켜 IoU를 구한다. `--nominal-span`은 추정기가 독립적으로 사용하는 지상 관측 폭 사전값(약 0.675 m)이며 `--scales` 기본값은 0.8, 0.9, 1.0, 1.1, 1.2다. yaw 탐색 간격은 기본 5°(`--yaw-step`)다. HSV 허용 범위는 `--hue-tolerance`, `--saturation-tolerance`, `--value-tolerance`로 지정한다.

`--min-coverage`는 매칭을 시작하기 위한 검출 건물 비율이며 기본값은 0.0125다. IoU 0.36 미만이거나, 8 cm 또는 20° 이상 떨어진 후보와의 점수 차이가 0.07 미만이면 추정을 거절한다. 점수와 차이는 확률로 해석하지 않는다. 정답 좌표는 평가와 시각화에 쓰며 전역 매칭에는 영상만 전달한다.

### 정지 영상과 연속 추적

정지 GUI에서는 지도 클릭으로 위치를 고르고 yaw 막대로 방향을 조절한다. 추적 GUI에서는 Play/Pause/Reset으로 재생하며, 정답·영상 측정·필터 추정 궤적과 오차·불확실성·처리 시간을 표시한다.

`--track`은 `line`, `square`, `circle`, `waypoints`를 지원한다. `--speed`(m/s), `--dt`(s), `--yaw-rate`(°/s)를 조절할 수 있다. 초기 위치 사전값은 외부에서 아는 경우에만 `--initial-xy X Y`로 넣는다.

```powershell
python main.py --track waypoints --waypoint 1.65 2.08 --waypoint 2.08 1.82 --waypoint 2.18 2.02
```

초기 전역 매칭 후 등속 Kalman 필터가 캡처 시각의 차이로 위치와 속도를 예측한다. 정상 추적은 예측 위치·yaw·관측 폭 주변을 탐색한다. 관측이 끊기면 위치와 속도만 예측한다. 기본 연속 3회 실패 또는 위치 표준편차 0.28 m 이상이면 0.6초 이상의 간격을 두고 전역 재탐색을 시도한다. 성공하면 위치·공분산을 재초기화하고 속도를 다시 학습한다. 재생 중 계산이 밀리면 최신 프레임만 처리하며 누락된 캡처 시간도 다음 예측에 반영한다.

### 랜덤 뷰와 블러

**Random Test**는 다음 랜덤 뷰, **Reseed**는 입력 시드로 재시작, **Apply Blur**는 현재 원본을 유지한 채 블러만 재평가, **Save Result**는 PNG와 JSON 저장이다. GUI에서 매칭은 백그라운드에서 실행한다. `--blur-kernel`은 기본 9인 양의 홀수, `--blur-sigma`는 기본 2인 0 이상의 유한한 값이다. sigma 0은 OpenCV가 커널에 맞춰 자동 설정한다. 블러가 없는 대조군은 커널 1이다.

`--min-building-coverage`는 랜덤 표본을 고를 때 지도에서 투영한 건물 면적의 최소 비율(기본 5%)이다. 0이면 건물이 없는 지역을 포함한다. `--max-sampling-attempts`(기본 100)까지 적합한 뷰를 못 찾으면 오류를 보고한다. 같은 시드와 카메라·표본 설정으로 동일한 뷰를 얻는다. 원본과 블러는 각각 전역 매칭을 실행한다. 실패한 오차는 화면에서 N/A, JSON에서 `null`이다.

## 코드 구성

| 경로 | 역할 |
| --- | --- |
| `main.py`, `localization/scale_cli.py`, `config/camera_config.yaml` | 실행 모드, 공통 축척 옵션, 실제 카메라 설정 |
| `localization/map_loader.py`, `camera_simulator.py` | 지도와 레이블 렌더링, 가상 카메라 투영 |
| `localization/image_processing.py`, `localization/map_matching.py` | 건물 레이블 및 선택적 외곽선, 고정·자동 축척의 전역·지역 IoU 매칭 |
| `localization/kalman_filter.py`, `localization/tracking_manager.py` | 상태 예측, 측정 검증, 마지막 채택 폭과 재획득 |
| `simulation/` | 경로 생성, 캡처 시각 및 최신 프레임 재생 정책 |
| `localization/random_view.py` | 무작위 표본 선택, 블러 비교, 통계 |
| `visualization/map_viewer.py`, `tests/random_view_test.py` | 공통 지도·방향 표시, 정지·추적·블러 화면 |
| `benchmark.py`, `benchmark_tracking.py`, `tests/test_*.py` | 벤치마크와 자동 검증 |
| `benchmark_scale.py`, `tests/test_scale_robustness.py` | 동일 프레임 높이 변화 비교, 축척·추적·표시 회귀 검증 |
| `benchmark_shadow.py`, `simulation/lighting.py`, `tests/test_shadow_robustness.py` | 조명·그림자의 합성 입력, 보정별 비교, 가중 정합 및 복구 검증 |
| `firmware/xiao_camera_stream/` | XIAO 전용 핀, QVGA JPEG 송신, 로컬 Wi-Fi 설정 예제 |
| `localization/camera_interface.py`, `camera_stream.py` | 프레임 형식, 합성·파일 입력, HTTP 수신 및 재접속 |
| `localization/camera_preprocessing.py`, `camera_recording.py` | 실제 입력 전처리, AVI/CSV/JSON 녹화 |
| `localization/illumination_correction.py`, `shadow_detection.py`, `lighting_pipeline.py` | 조명 보정, 상대 그림자와 신뢰도, 실시간·실험 공통 처리 |
| `localization/live_localization.py`, `visualization/live_camera_viewer.py` | 실제 카메라 추정기 연동, 최신 영상과 위치·관측 영역 표시 |
| `tools/test_camera_stream.py`, `tools/calibrate_camera.py` | 수신 성능 측정, 렌즈 보정 및 지상 관측 폭 측정 |
| `tools/inspect_camera_frame.py` | 단일 프레임 HSV, 후보 폭·점수, 지도 정렬 진단 |

## 합성 검증 결과

| 모드 | 결과 |
| --- | --- |
| 정지 영상 | 25/33 채택, 위치 RMSE 0.81 cm, 평균 yaw 오차 1.72°, 평균 441 ms/프레임 |
| 연속 추적 | 125/137 관측 채택, 131/137 프레임에서 필터 오차 5 cm 이내, 채택된 지역 매칭 평균 141 ms |
| 랜덤 뷰, Gaussian 9×9·σ=2 | 양쪽 30/30 채택, 위치 RMSE 원본 1.13 cm / 블러 0.93 cm, 평균 yaw 오차 1.52° / 1.34°, 평균 정합 시간 989 / 1,011 ms |

위치 RMSE는 정지·랜덤 평가에서는 채택된 프레임만 대상으로 한다. 연속 추적의 필터 오차는 예측만 수행한 프레임도 포함한다. 예를 들어 잘못된 초기 위치 사전값을 포함하면 연속 평가 전체의 필터 RMSE는 33.1 cm였다. 강한 블러(31×31·σ=7)를 사용한 첫 8개 뷰에서는 원본 8/8, 블러 6/8을 채택했다. [연속 추적 CSV](../data/experiments/localization/demo_samples/tracking_results.csv) · [랜덤 뷰 JSON](../data/experiments/localization/demo_samples/random_test_batch.json) · [비교 화면](../data/experiments/localization/demo_samples/random_test.png)

재실행한 연속 벤치마크에서 전역 탐색은 평균 1,293 ms였고, GUI 그리기를 제외한 전체 계산은 4.4 FPS였다. 저장된 CSV와 JSON은 이전 실행의 프레임별 자료다.

매칭에서는 외곽선 추출을 생략하고 같은 색상 픽셀이 겹칠 가능성이 있는 영역에서만 IoU 분모를 계산한다. 동일한 합성 프레임 4장(원본 2장, 블러 2장)을 모드별로 두 번씩 실행한 전후 비교에서 위치·yaw·IoU·차점 점수가 일치했다. 전역 평균은 730.9 → 469.8 ms, 지역 평균은 37.0 → 37.1 ms였다. 이 비교의 지역 탐색에는 정해진 시험 사전값을 사용했고, 시간은 검증 PC와 표본에 한정된다.

위 위치 오차는 평면 합성 영상으로 측정했다. 입체 건물과 그림자, 실제 카메라의 왜곡과 비행 중 기울기, 방향성 모션 블러의 영향은 실측 전이다.
