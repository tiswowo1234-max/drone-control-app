# 조원 코드 출처와 수정 기록

이 폴더는 조원 위치 인식 코드를 **복사**한 것이다(병합 방식 B안, 2026-11-21 결정, 조원 동의 받음).
원본은 고치지 않았고, 여기서도 꼭 필요한 곳만 최소로 고쳤다.

- 원본: `C:\dev\drone-localization-main` (조원 저장소 사본, 2026-11-21 01:18 파일 날짜)
- 복사한 날: 2026-11-21 (vision 하위 세션)
- 폴더 구조는 원본과 같게 두었다 → 조원 코드 안의 상대 경로(`data/maps/...`)가 그대로 동작한다.

## 복사한 것

| 여기 | 원본 | 쓰는 곳 |
|---|---|---|
| `localization_app/localization/*.py` (24개) | `localization_app/localization/` 전체 | esp32 모드 위치 계산 (`LiveLocalization`: 건물·도로 색 정합 + AprilTag 융합, 칼만), XIAO 수신기(`camera_stream.XiaoCameraSource`) |
| `localization_app/config/camera_config.yaml` | 같은 경로 | esp32 카메라 방향·렌즈 보정 설정 |
| `localization_app/tools/calibrate_camera.py` | 같은 경로 | XIAO 카메라 보정 (참고용) |
| `localization_app/firmware/xiao_camera_stream/` (4개) | 같은 경로 | XIAO 펌웨어 (참고용). `wifi_secrets.example.h`만 있고 실제 `wifi_secrets.h`는 복사하지 않음 |
| `localization_app/README.md`, `requirements.txt` | 같은 경로 | 조원 설명서 |
| `incident_detection/localization/` `localizer.py`, `camera_model.py`, `photo_finder.py`, `geo.py`, `settings.py`, `sim_camera.py`, `calibrate_camera.py`, `pipeline.py`, `_paths.py`, `README.md` | `incident_detection/localization/` | tt_down 모드 위치 계산(`Localizer` AprilTag PnP + `PoseTracker`), 사진 찾기, 위경도(`geo.MapGeo`), 합성 카메라(`sim_camera.render`), TT 카메라 보정 |
| `incident_detection/localization/image_io.py` | `incident_detection/LLM_detects/image_io.py` | 한글 경로 이미지 읽기·쓰기 (LLM과 무관한 작은 파일이라 이 폴더로 옮김) |
| `incident_detection/README.md` | 같은 경로 | 조원 설명서 |
| `data/maps/` `apriltag_map.json`, `slots.json`, `KAIST_model_features_2p5m.json`, `KAIST_model_reference_2p5m.png`, `map_with_tags(_gray).png`, `map_with_photos(_gray).png` | `data/maps/` | 태그 위치, 슬롯(`up_xy`), 지도 정합 기준, 합성 카메라 그림 |
| `data/print/campus/KAIST_map_PRINT_preview.png` | 같은 경로 | `localization_app/road_map.py`가 도로 영역을 만들 때 읽음 |
| `data/experiments/incident_camera/sim_frames/` (13개) | 같은 경로 | 오프라인 시험 프레임(640×480 흑백) + 참값 |
| `README_original.md` | 저장소 루트 `README.md` | 조원 전체 설명서 |

## 복사하지 않은 것
- `.env`(API 키), `wifi_secrets.h`, `.venv`, `__pycache__`
- `incident_detection/LLM_detects/` 전체 (LLM 판별, `openai` 의존) — AI 판별은 끄기로 결정(2026-11-21)
- `incident_detection/localization/map_layout.py`, `osm_map_roads_gate_2p5m.py` (지도·태그 배치 제작 도구, LLM_detects와 map_tools 의존)
- `map_tools/`, `data/print/` 나머지(인쇄용 PDF), `data/photos/`(81MB), `data/gis/` (`KAIST_map.osm`은 프로젝트 루트에 같은 파일이 있음, SHA-256 일치 → 그걸 씀)
- `data/experiments/` 의 큰 실험 결과 (sim_frames만 가져옴)
- `localization_app`의 `main.py`, 벤치마크, `tests/`, `simulation/`, `visualization/`, 나머지 `tools/` (GUI·실험용)

## 고친 곳 (모두 `[drone-control-app 수정 2026-11-21]` 주석)
1. `incident_detection/localization/_paths.py` — 원본은 옆 폴더 `LLM_detects`가 없으면 ImportError. LLM을 복사하지 않았으므로 그 검사와 `sys.path` 추가를 뺐다.
2. `incident_detection/localization/sim_camera.py` — `import map_layout`(LLM_detects 의존)을 `scene_from_configs()` 안으로 옮기고, `PX_PER_MM = 2.0`(map_layout과 같은 값)을 직접 두었다.

고치지 않았지만 이 사본에서 **동작하지 않는 것**:
- `pipeline.py` — `schema`(LLM_detects)를 import하므로 실행되지 않는다. 연결 프로그램(`vision/bridge/`)이 같은 흐름(위치 → 사진 → 슬롯)을 다시 구현했다. 참고용으로 남김.
- `photo_finder.py --detect`, `map_layout.py` 관련 기능.

## 연결 프로그램이 조원 코드를 쓰는 방식
`vision/bridge/teammate.py`가 `incident_detection/localization/`과 `localization_app/`을 `sys.path`에 넣는다.
- tt_down: `localizer.Localizer`, `PoseTracker`, `TagMap`, `camera_model.CameraModel` (`bridge/localizers.py`)
- esp32: `localization.live_localization.LiveLocalization`, `map_matching.MapMatcher`, `apriltag_localization.AprilTagLocalizer` (`bridge/localizers.py`)
- 합성 카메라: `sim_camera.render`(흑백), 같은 원근 변환의 컬러판 (`bridge/synthetic.py`)
- 사진 찾기: `photo_finder.find_photos`, `rectify` (`bridge/slots.py`)
- 위경도: `geo.MapGeo` (`bridge/app.py`)

## 방향(yaw) 기준 — 시험으로 확인 (`vision/tests/test_yaw.py`)
| 모듈 | 조원 값 | 기준 | 명세 yaw로 |
|---|---|---|---|
| `localization_app` `TrackingResult.yaw_deg`, `Pose.yaw_deg` | yaw | 북쪽 기준 **반시계** | `(-값) mod 360` |
| `incident_detection` `Pose.heading_deg`, `TrackedPose.heading_deg` | heading | 나침반 방위(0=북, 90=동) = **시계** | 그대로 |
