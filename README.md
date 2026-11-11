# 캠퍼스 경비원 보조 드론 — 제어 앱 & 중계 서버

대학교 조별과제로 만든 **실내 축소 캠퍼스 지도 위를 나는 경비 보조 드론**의 제어 시스템입니다.
폰 앱에서 지도 위 목표 지점을 찍으면, 중계 서버가 안전 검사를 거쳐 드론(DJI RoboMaster TT)을 그 지점으로 보냅니다.

```
[안드로이드 앱] ⇄ WebSocket(JSON, cm) ⇄ [중계 서버 (Python, PC/라즈베리파이)] ⇄ [드론 또는 Unity 시뮬레이터]
                                              ▲
                     [카메라 위치 추정 코드] ─ POST /vision/pose (m)
```

## 구성
| 폴더 | 내용 |
|---|---|
| `app/` | Flutter 안드로이드 앱 (지도, 출동·드래그·확인, 정지·착륙·귀환·비상정지, 드론 상태 표시) |
| `server/` | 중계 서버 (FastAPI WebSocket, 안전 검사, mock/sim/real 백엔드, 카메라 위치 입력) — `server/README.md` |
| `config/settings.toml` | 지도 크기, 이륙 지점, 비행 제한, 좌표 보정 등 조정 가능한 값 |
| `docs/api.md` | 앱 ⇄ 서버 메시지 명세 |
| `docs/teammate/interface.md` | 연동 설명서 (송수신 형식, 명령별 동작, 서버 실행 방법) |
| `map/` | 바닥 인쇄 지도 원본 (2.5m × 2.5m, EPS/PDF) |
| `tools/` | 지도 이미지 변환·위경도 정합 스크립트 |
| `RoboMasterTT-Simulator beta/` | 시뮬레이터용 Python 드라이버 (팀원 제작) |

## 빠른 시작 (드론 없이)
```bash
cd server
./run_server.bat mock          # Windows (macOS/Linux: docs/teammate/interface.md 8장)
```
앱은 `app/`에서 `flutter run -d edge`(PC 브라우저) 또는 빌드한 APK로 실행하고, ⚙에 서버 주소를 입력합니다.
자세한 내용은 `docs/teammate/interface.md`를 참고하세요.

## 지도 데이터 출처
지도(`KAIST_map.osm`, `map/`, `app/assets/map.png` 및 이를 바탕으로 한 좌표 정합 값)는
**© OpenStreetMap contributors** 의 데이터를 사용했으며, [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/)에 따라 이용할 수 있습니다.
자세한 내용: https://www.openstreetmap.org/copyright
