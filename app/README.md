# app/ — 경비 드론 제어 앱 (Flutter, 안드로이드)

폰에서 캠퍼스 지도를 보며 드론을 출동시키는 앱입니다.
**앱은 중계 서버 API에만 의존**합니다. 위치를 계산하지 않고, 서버가 보내는 `state.pos`를 그립니다.
메시지 형식은 [docs/api.md](../docs/api.md) v0.7이 기준입니다.
전체 구조는 [docs/dev-guide.md](../docs/dev-guide.md)에 있습니다.

## 실행
- **PC 브라우저(개발용):** `flutter run -d edge`. 서버는 `server\run_server.bat mock`으로 켭니다.
- **안드로이드:** APK를 설치한 뒤 오른쪽 위 ⚙에 노트북 주소를 입력합니다(예: `192.168.0.10`, 폰을 껐다 켜도 기억).
- **APK 빌드** (이 PC 기준. Flutter `C:\dev\flutter`, Android SDK `C:\dev\android-sdk`):
  1. `JAVA_HOME`=JDK17, `PUB_CACHE=C:\dev\pub-cache`, `GRADLE_USER_HOME=C:\dev\gradle-home`을 설정합니다.
  2. 이 폴더에서 `flutter build apk --release`를 실행합니다.
  3. 결과를 `C:\dev\경비드론.apk`로 복사합니다.
- **시험:** `flutter test`(63개, 2026-11-21) · `flutter analyze`

## 파일 구성 (`lib/`)
| 파일 | 역할 |
|---|---|
| `main.dart` | 시작점. 저장된 서버 주소로 `DroneClient`를 연결하고 `MapScreen`을 띄웁니다. 새 사건 알림 오버레이로 모든 화면을 감쌉니다. |
| `drone_client.dart` | **서버 통신 담당** (`/ws`): 연결·자동 재연결, 1초 ping, 명령 전송(`sendCommand`, `goto`/`takeoff`/`stop`/`land` …)과 ack 대기, `state`·`event`·`incident` 수신. `ChangeNotifier`라서 화면이 상태 변화를 듣습니다. |
| `protocol.dart` | 서버 메시지 → Dart 객체: `DronePos`, `DroneState`, `VisionStatus`, `Incident`, `Detection`, ack |
| `config.dart` | 서버 `config` 메시지(지도 크기·home·제한·위경도·카메라 모드·사진 슬롯). 연결 전에는 `AppConfig.fallback`을 씁니다. **`settings.toml`을 바꾸면 이 기본값도 맞춥니다.** |
| `map_screen.dart` | 메인 화면: 지도, 상태 표시, 출동(마커 드래그 → 확인), 이륙·정지·착륙·귀환·비상정지, 카메라 전환, 사건 목록, 영상 버튼 |
| `map_view.dart` | 확대·이동 지도와 그 위 표시: 이륙 지점, 허용 반경, 드론 아이콘(yaw 화살표), 목표, 출동 마커, 사진 슬롯, 사건 핀 |
| `map_coords.dart` | 화면 픽셀(왼쪽 위 원점) ⇄ 지도 cm(왼쪽 아래 원점) 변환 |
| `safety.dart` | 출동 전 미리 하는 검사(경계·반경·금지 구역·연결·배터리). 최종 검사는 서버가 합니다. |
| `geo.dart` | 지도 cm → 위도·경도 (네 모서리 쌍선형 보간, 표시용) |
| `incident_screens.dart` | 사건 목록·상세·사진 확대, 새 사건 배너·진동 |
| `video_client.dart`, `video_screen.dart` | 실시간 영상(`/ws/video`, 명령 통로와 별개) |
| `server_address.dart` | 서버 주소 저장 (`shared_preferences`) |

- `assets/map.png`: 바닥 지도 이미지입니다(2500×2500px, `tools/pdf_to_png.py`로 생성).
- `test/`:
  - `logic_test.dart`: 좌표·검사·프로토콜
  - `v07_test.dart`: 사건·영상·카메라 모드
  - `widget_test.dart`: 화면, 가짜 서버 연결 사용

## 고칠 때 알아 둘 것
- **새 서버 기능을 화면에 붙일 때:**
  1. `protocol.dart`에 메시지 필드를 추가합니다.
  2. `drone_client.dart`에 명령 메서드를 추가합니다.
  3. `map_screen.dart`에 버튼·표시를 답니다.
  4. `docs/api.md` 버전을 올리고, 서버 쪽과 함께 고칩니다. 예: UGV 추종은 `state.ugv` + 추종 버튼(dev-guide 8장).
- **버튼 활성화:** 서버 `state.mode`(`idle`·`taking_off`·`hovering`·`moving`·`returning`·`landing`·`emergency`)를 보고 정합니다. 새 mode 값이 생기면 여기도 처리합니다.
- **시뮬레이터와 합칠 때:** 이 앱이 하는 일은 "서버 `/ws`에 붙어 명령을 보내고 상태를 그리기"가 전부입니다. 그래서 다음 두 방법이 가장 손이 덜 갑니다(dev-guide 9장).
  - 앱을 그대로 두고 서버를 `sim` 백엔드로 켭니다.
  - Unity 쪽 화면이 같은 `/ws` 프로토콜로 서버에 붙습니다.
