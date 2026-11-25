# 앱 ⇄ 중계 서버 API 명세 (초안 v0.7)

> 상태: **초안**. 2단계(2026-11-21~)부터 조원 위치 인식 코드는 우리 프로젝트 `vision/`에 병합되어 이 명세를 따른다.
> 전체 흐름: XIAO 카메라 →(Wi-Fi 영상)→ `vision/` 연결 프로그램 →(7·8·9장)→ 중계 서버(노트북) →(3·10장)→ 앱
> 바뀌면 이 문서의 버전과 변경 기록을 먼저 고친 다음 코드를 고친다.

## 1. 연결
- 방식: WebSocket, 메시지는 UTF-8 JSON 한 개씩
- 주소: `ws://<서버 IP>:8000/ws` (예: PC가 `192.168.0.10`이면 `ws://192.168.0.10:8000/ws`)
- 앱 하나만 연결하는 것을 기본으로 한다. 여러 개가 연결되면 모두 상태를 받지만, 명령은 마지막에 연결한 앱만 보낼 수 있다.
  제어 권한이 없는 앱이 명령을 보내면 `BUSY`로 거부한다. 제어 권한이 바뀌면 `CONTROLLER_CHANGED` 이벤트를 보낸다.
- **명령권 넘겨받기:** 명령권을 가진 앱의 연결이 끊기면, 남아 있는 앱 중 **가장 최근에 연결된 앱**이 즉시 명령권을 넘겨받는다
  (`CONTROLLER_CHANGED` 이벤트). 비행은 계속되고, 이후 생존 신호는 새 명령권 앱의 `ping`으로 판단한다.
  남은 앱이 하나도 없으면 4.2의 자동 착륙 규칙을 따른다.

## 2. 좌표계 (모든 위치 값의 기준)
- 단위: **cm**
- 원점 (0, 0): 바닥 지도의 **왼쪽 아래 모서리** (지도를 정방향으로 놓았을 때)
- x: 지도 오른쪽이 +, y: 지도 위쪽이 +, z: 바닥에서의 높이
  - **주의:** "지도 오른쪽/위쪽"이 실제 동쪽/북쪽과 같다고 가정하지 않는다. (확정 지도는 약 1° 회전, 이전 지도는 약 30° 회전이었다.)
- yaw: 도(°). 지도 위쪽(+y)이 0, 시계 방향이 +
- 미션패드 좌표를 이 좌표계로 바꾸는 일은 **서버(조원 코드)의 책임**이다. 앱은 이 좌표계만 안다.
- 위도·경도는 앱에서 화면 표시용으로만 계산한다 (`config`의 `geo` 참고).

## 3. 서버 → 앱

### 3.1 `config` — 연결 직후 1회 전송
```json
{
  "type": "config",
  "api_version": "0.7",
  "map": { "width_cm": 250, "height_cm": 250 },
  "home": { "x_cm": 153.2, "y_cm": 70.2 },
  "limits": {
    "max_radius_cm": 150,
    "min_alt_cm": 50,
    "max_alt_cm": 150,
    "default_alt_cm": 80,
    "min_battery_pct": 20,
    "link_timeout_s": 3
  },
  "no_fly_zones": [
    { "name": "예시 구역", "polygon_cm": [[100, 100], [130, 100], [130, 130], [100, 130]] }
  ],
  "geo": {
    "sw": { "lat": 36.3639270, "lon": 127.3589258 },
    "se": { "lat": 36.3640364, "lon": 127.3669092 },
    "nw": { "lat": 36.3703837, "lon": 127.3587905 },
    "ne": { "lat": 36.3704931, "lon": 127.3667739 },
    "rotation_deg": 0.97
  },
  "backend": "sim",
  "vision_modes": ["esp32", "tt_down"],
  "photo_slots": [
    { "slot": 1, "name": "S1 교육지원동 (W8)", "center_cm": { "x_cm": 30.9, "y_cm": 228.6 },
      "corners_cm": [[21.9, 228.8], [34.5, 236.9], [39.9, 228.5], [27.3, 220.4]] }
  ]
}
```
- 숫자는 모두 예시이며 서버 설정 파일에서 정한다.
- `api_version`: 서버가 따르는 이 명세의 버전.
- `backend`: `mock`(Unity 없는 가짜 드론) · `sim`(시뮬레이터) · `real`(실제 드론). 화면 표시용이다.
- `vision_modes` (v0.7): 앱에서 고를 수 있는 위치 추적 카메라 모드 (11장). 앱은 이 목록으로 전환 버튼을 만든다.
- `photo_slots` (v0.7): 사건 사진 슬롯 S1~S4 (`config/settings.toml`의 `[[photo_slots]]`). 앱은 지도에 표시하고, 누르면 그 `center_cm`을 출동 목표로 놓는다(9장 슬롯 촬영).
- `geo`: 지도 이미지 **네 모서리**의 위도·경도 (`config/settings.toml`의 `[geo]`).
  - `sw`/`se`/`nw`/`ne`는 **이미지 기준** 왼쪽아래/오른쪽아래/왼쪽위/오른쪽위이며 실제 방위가 아니다.
  - 지도가 회전되어 있을 수 있으므로 두 모서리만으로 보간하지 않는다(이전 지도에서는 최대 약 380m 틀렸다). **반드시 네 모서리 쌍선형 보간**을 쓴다.
    `u = x_cm / width_cm`, `v = y_cm / height_cm`일 때
    `lat = (1-u)(1-v)·sw + u(1-v)·se + (1-u)v·nw + uv·ne` (lon도 같은 식)
  - `rotation_deg`: 이미지 +x축이 동쪽에서 반시계로 돈 각도 (참고용)

### 3.2 `state` — 주기적으로 전송 (약 5Hz)
```json
{
  "type": "state",
  "ts": 1763000000.123,
  "connected": true,
  "flying": true,
  "mode": "moving",
  "pos": { "x_cm": 120.5, "y_cm": 88.0, "z_cm": 80.0, "yaw_deg": 0 },
  "pos_source": "estimate",
  "target": { "x_cm": 150, "y_cm": 100 },
  "battery_pct": 76,
  "vision": { "mode": "tt_down", "bridge_connected": true, "bridge_mode": "tt_down", "camera_ok": true, "video_live": true }
}
```
- `connected`: 서버가 드론(또는 시뮬레이터)과 통신 중인지 여부
- `mode`: `idle`(착지) · `taking_off` · `hovering` · `moving` · `returning` · `landing` · `emergency`
- `pos_source`: `vision`(카메라 사진 기반 위치 추정, 7장) · `estimate`(명령 누적 추정) · `mission_pad`(미션패드 측정) · `sim_truth`(시뮬레이터 디버그 값)
- `pos`, `battery_pct`는 모를 때 `null`이다.
- `target`은 이동 중이 아니면 `null`이다.
- `vision` (v0.7): 위치 추적 카메라 상태 (11장). `mode` = 서버가 정한 모드, `bridge_connected` = 연결 프로그램 연결 여부,
  `bridge_mode`/`camera_ok` = 연결 프로그램이 마지막으로 알린 실제 모드·카메라 상태(모르면 `null`), `video_live` = 영상이 들어오는 중인지.
  `bridge_mode`가 `mode`와 다르면 앱은 "전환 중"으로 표시한다.

### 3.3 `ack` — 명령마다 1회 응답
```json
{ "type": "ack", "id": "c-17", "ok": false, "error": "OUT_OF_BOUNDS", "message": "지도 경계 밖 좌표입니다" }
```
- 명령을 **접수했는지**에 대한 응답이다. 비행이 끝났는지는 `state.mode`로 확인한다.
- 성공(`ok: true`)이면 `error`와 `message`는 없다.

### 3.4 `event` — 알림
```json
{ "type": "event", "level": "warn", "code": "LINK_LOST_LANDING", "message": "앱 연결이 끊겨 자동 착륙합니다" }
```
- `level`: `info` · `warn` · `error`
- 앱은 `code`를 몰라도 `message`를 그대로 보여 주면 된다. 현재 서버가 쓰는 코드:

| code | 의미 |
|---|---|
| `LINK_LOST_LANDING` | 앱 ping이 끊겨 자동 착륙 |
| `EMERGENCY_STOP` | 비상 모터 정지 실행됨 |
| `LOW_BATTERY` | 배터리 부족 경고 |
| `DRONE_CONNECTED` / `DRONE_DISCONNECTED` | 드론(시뮬레이터) 연결 변화 |
| `CMD_FAILED` | 접수된 명령이 실행 중 실패 |
| `CONTROLLER_CHANGED` | 명령 권한을 가진 앱이 바뀜 |
| `ALREADY_LANDED` | 이미 착지 상태라 착륙 명령을 무시 |
| `BAD_REQUEST` | 앱이 보낸 메시지가 JSON 형식이 아님 |
| `DRONE_LANDED_ITSELF` | (v0.7) 실제 TT가 스스로 착륙함(배터리 부족·명령 끊김·충돌 등). 서버가 TT 높이로 감지해 `mode`를 `idle`로 바꾼다 |
| `TARGET_TOO_CLOSE` | (v0.7) 출동 목표가 현재 위치에서 20cm 이내라 움직이지 않음(TT 최소 이동 거리) |
| `ALIGN_START` | (2026-11-25) 초기 정렬 시작: 태그 ID n 위로 이동 |
| `ALIGN_DONE` | (2026-11-25) 초기 정렬 완료: 위치·방향 보정값 |
| `ALIGN_FAILED` | (2026-11-25, warn) 태그 위에서 제한 시간 안에 카메라 위치(또는 방향 보정)가 모이지 않음 — 추정 위치로 계속 비행 |
| `ALIGN_SKIPPED` | (2026-11-25, warn) 현재 위치를 모르거나 가장 가까운 태그가 너무 멀어 정렬을 건너뜀 |
| `INCIDENT_NEW` | 새 사건 사진이 보고됨 (3.5의 `incident` 메시지와 함께 보낸다. 앱은 `incident`를 처리하면 이 event는 무시해도 된다) |

### 3.5 `incident` — 새 사건 보고 (v0.7)
`vision/`이 사건 사진을 찾아 서버에 보고(9장)하면, 서버가 **모든 앱**에 1회 보낸다.
```json
{
  "type": "incident",
  "incident": {
    "id": "inc-3",
    "ts": 1763000000.4,
    "slot": 4,
    "place": "slot S4, unnamed building",
    "pos": { "x_cm": 46.6, "y_cm": 89.0 },
    "geo": { "lat": 36.3662462, "lon": 127.3603673 },
    "drone_pos": { "x_cm": 44.1, "y_cm": 83.1, "z_cm": 79.0 },
    "photo_url": "/incidents/inc-3/photo.jpg",
    "mode": "tt_down",
    "method": "slot",
    "detection": null
  }
}
```
| 필드 | 설명 |
|---|---|
| `id` | 서버가 붙이는 고유 문자열 (`inc-1`, `inc-2`, … 서버를 다시 켜면 1부터) |
| `ts` | 사진을 찍은 시각 (유닉스 초) |
| `slot` | 사진 슬롯 번호 1~4 (S1~S4). 슬롯 밖에서 찾았으면 `null` |
| `place` | 사람이 읽는 장소 설명 (조원 코드의 `location.name`에서 좌표 부분을 뺀 것). 없으면 `null` |
| `pos` | 사진 중심의 지도 좌표 (cm, 2장 좌표계). 모르면 `null` |
| `geo` | 사진 위치의 위도·경도 (표시용). 모르면 `null` |
| `drone_pos` | 사진을 찍을 때 드론 위치 (cm). 모르면 `null` |
| `mode`, `method` | 찍은 카메라 모드(`esp32`/`tt_down`)와 방식(`slot`/`photo_finder`). 모르면 `null` (9.1) |
| `photo_url` | 사진(JPEG) 주소. 서버 주소 뒤에 붙여 `http://<서버 IP>:8000/incidents/inc-3/photo.jpg`로 내려받는다 (10.1) |
| `detection` | AI 판별 결과. **현재 판별은 꺼져 있으므로 항상 `null`** — 앱은 "확인 필요(판별 없음)"로 표시한다. 켜면 아래 형식 |

`detection`이 있을 때 (조원 코드 `LLM_detects/schema.py`의 `Detection`과 같음):
```json
{ "is_abnormal": true, "category": "fire", "urgency": "high", "needs_human_review": false,
  "image_quality": "good", "description": "건물 앞에서 연기가 난다", "observations": "...", "model": "..." }
```
- `urgency`: `none` · `low` · `medium` · `high`, `image_quality`: `good` · `degraded` · `unusable`

### 3.6 `incidents` — 최근 사건 목록 (v0.7)
앱이 연결하면 `config` 바로 다음에 1회 보낸다. 서버가 기억하는 최근 사건(최신이 앞, 최대 `[incident] keep`개)이다.
```json
{ "type": "incidents", "items": [ { "id": "inc-3", "...": "3.5의 incident와 같은 형식" } ] }
```
- 서버는 사건을 **메모리에만** 둔다. 서버를 다시 켜면 목록이 비고 사진도 사라진다.
- "확인함" 표시는 앱이 스스로 기억한다(서버에 보내지 않는다).

## 4. 앱 → 서버

### 4.1 `cmd` — 명령
```json
{ "type": "cmd", "id": "c-17", "cmd": "goto", "x_cm": 150, "y_cm": 100, "z_cm": 80 }
```
| cmd | 추가 필드 | 설명 |
|---|---|---|
| `takeoff` | — | 이륙 후 `default_alt_cm`에서 호버링. (2026-11-25) `[align] enabled`이고 카메라 위치(vision)가 있으면, 연결 후 첫 이륙 때 **초기 정렬**(가장 가까운 AprilTag 위로 가서 위치·방향 확정)을 먼저 한다. 그동안 `mode`는 `taking_off` |
| `goto` | `x_cm`, `y_cm`, (`z_cm` 생략 시: `[limits] fixed_alt = true`이면 `default_alt_cm`(v0.7, 시연 전 고도 고정 — `return_home`의 이동 고도도 같음), false면 현재 고도를 `min_alt_cm`~`max_alt_cm`로 맞춘 값) | 목표 지점으로 이동. 착지 상태이면 이륙부터 한다 |
| `stop` | — | 그 자리에서 호버링 |
| `land` | — | 그 자리에 착륙 |
| `return_home` | — | `home`으로 이동한 뒤 착륙 |
| `set_vision_mode` | `mode`: `esp32` / `tt_down` | (v0.7) 위치 추적 카메라 전환 (11장). `idle`·`hovering`일 때만, 아니면 `BUSY`. 목록에 없는 값이면 `BAD_REQUEST` |
| `emergency` | `confirm: true` | **모터 즉시 정지(추락)**. 앱에서 한 번 더 확인한 경우에만 보낸다 |

- `id`: 앱이 정하는 고유 문자열. `ack`에 그대로 돌려준다.
- `stop`, `land`, `emergency`는 다른 명령이 진행 중이어도 즉시 처리한다.
  (시뮬레이터는 진행 중인 이동 구간을 끊지 못해 `stop`이 약 2초 늦을 수 있다. `emergency`는 즉시.)
- 이동 중에 새 `goto`가 오면 기존 이동을 취소하고 새 목표로 간다.
- 이륙 중(`taking_off`)에 `goto`나 `return_home`이 오면 `BUSY`로 거부한다.
- 귀환 중(`returning`)에 새 `goto`가 오면 귀환을 취소하고 새 목표로 간다.
- 도착 판정: 각 축 오차가 약 20cm 미만이면 도착으로 본다(TT의 최소 이동 거리 제한). 목표에서 최대 약 20cm 벗어나 멈출 수 있다.

### 4.2 `ping` — 생존 신호 (1초마다)
```json
{ "type": "ping", "ts": 1763000000.5 }
```
- 비행 중에 서버가 **명령권을 가진 앱**에게서 `link_timeout_s`초 동안 `ping`을 받지 못하면 **자동 착륙**한다.
  (명령권 앱이 끊겨도 다른 앱이 남아 있으면 1장에 따라 명령권이 넘어가고, 그 앱의 `ping`으로 판단한다.)
  `cmd` 메시지도 생존 신호로 인정한다.

## 5. 오류 코드
| 코드 | 의미 |
|---|---|
| `OUT_OF_BOUNDS` | 지도 경계 밖 좌표 |
| `NO_FLY_ZONE` | 금지 구역 안의 좌표 |
| `TOO_FAR` | `home`에서 `max_radius_cm`보다 먼 좌표 |
| `ALT_LIMIT` | 고도 제한 밖 |
| `LOW_BATTERY` | 배터리가 `min_battery_pct` 미만이라 출동 거부 |
| `NOT_CONNECTED` | 드론과 연결되지 않음 |
| `BUSY` | 처리할 수 없는 상태 (예: 착륙 중) |
| `NOT_CONFIRMED` | `emergency`에 `confirm: true`가 없음 |
| `BAD_REQUEST` | 형식 오류 |
| `WRONG_MODE` | (v0.7, `/vision/pose`만) 보낸 `mode`가 서버의 현재 위치 추적 모드와 다름 (11장) |

## 6. 안전 검사 위치
- 앱: 마커를 놓을 때 경계·금지 구역·반경을 미리 검사해 사용자에게 바로 알려 준다 (편의 목적).
- 서버: 모든 명령을 **다시 검사**한다 (최종 방어선). 앱 검사를 믿지 않는다.

## 7. 위치 추정 입력 (조원 위치 추정 코드 → 중계 서버)
조원의 카메라 위치 추정 코드가 추정한 위치를 서버에 넘기는 통로다. 앱은 이 값을 직접 받지 않고, 서버가 `state.pos`로 바꿔 전달한다.

### 7.1 좌표 형식 (조원 결과 화면 기준으로 통일)
- 단위: **m** (앱⇄서버의 cm와 다르다. 서버가 ×100 하여 cm로 바꾼다)
- 원점 (0, 0): 지도 **왼쪽 아래** (아래가 0)
- x: 지도 오른쪽(≈동쪽)이 +, y: 지도 위쪽(≈북쪽)이 +. 범위 0 ~ 2.5 m (지도 2.5m×2.5m)
- 방향 `yaw_deg`(선택): 2장과 같다. 지도 위쪽(+y)이 0, 시계 방향이 +
  - **주의(v0.7, 시험으로 확인):** 조원 모듈마다 기준이 다르다. `vision/`이 바꿔서 보낸다.
    - `esp32`(조원 `localization_app`): "북쪽(+y) 기준 반시계" → `yaw_deg = (-조원값) mod 360`
    - `tt_down`(조원 `incident_detection`): 이미 "북쪽 기준 시계"(나침반) → **그대로**
    - 위치 계산이 주는 yaw는 **카메라 화면 위쪽이 가리키는 방향**이다. `vision/`이 장착 각도(`[bridge] tt_mount_yaw_deg`/`esp32_mount_yaw_deg`)를 더해 **드론 앞쪽 방향**으로 보낸다.
      TT 하단 카메라는 화면이 드론 앞쪽 기준 반시계 90° 돌아 있다(2026-11-21 실물 확인) → `tt_mount_yaw_deg = -90`.
    - 앱 영상(10.2)도 `[bridge] video_forward_up = true`면 드론 앞쪽이 화면 위로 오게 돌려 보낸다(TT는 세로 240×320이 됨).

### 7.2 보내는 방법: HTTP POST
- 주소: `http://<서버 IP>:8000/vision/pose` (같은 라즈베리파이에서 돌리면 `http://127.0.0.1:8000/vision/pose`)
- 본문 (JSON):
```json
{ "x_m": 0.20, "y_m": 2.33, "yaw_deg": 0.0, "confidence": 0.92, "ts": 1763000000.12 }
```
| 필드 | 필수 | 설명 |
|---|---|---|
| `x_m`, `y_m` | ✅ | 추정 위치 (m) |
| `yaw_deg` | | 드론 방향. 앱 화면 표시에만 쓰고, 비행 제어는 드론 자체 yaw 센서를 쓴다. 없으면 센서 값을 표시한다 |
| `z_m` | | (v0.7) 카메라로 추정한 높이 (m, 지도 바닥 기준). 있으면 앱의 `pos.z_cm` 표시에 쓴다. 없으면 TT 센서 높이. 비행 제어에는 쓰지 않는다 |
| `mode` | | (v0.7) 이 위치를 계산한 카메라 모드 `esp32`/`tt_down`. 서버 모드와 다르면 `WRONG_MODE`로 거부. 생략하면 검사하지 않는다(이전 도구 호환) |
| `confidence` | | 0~1 신뢰도. `[vision] min_confidence`보다 낮으면 `LOW_CONFIDENCE`로 거부, 0~1 밖이면 `BAD_REQUEST` |
| `ts` | | 사진을 찍은 시각 (유닉스 초). **순서 확인에만 쓴다**: 이전에 받은 값보다 오래된 `ts`는 버린다(응답은 `{"ok": true}`) |
- 응답: 성공 `{"ok": true}` / 실패(HTTP 400) `{"ok": false, "error": "OUT_OF_BOUNDS" | "LOW_CONFIDENCE" | "BAD_REQUEST" | "WRONG_MODE", "message": "한국어 설명"}`
- 서버가 vision 입력을 켜지 않은 상태(`--vision` 또는 `pos_source = "vision"` 아님)면 `BAD_REQUEST`로 거부한다.
- 보내는 빈도는 정하지 않는다(미정). 서버는 **가장 최근 값**만 쓴다.
  - 권장: `stale_s`(기본 1초) 안에 최소 2번, 즉 **1초에 2번 이상** 보내야 앱에 계속 `vision`으로 표시된다. 더 느리면 `stale_s`를 늘린다.

### 7.3 서버 동작
- 마지막 값을 **서버가 받은 시각** 기준으로 `[vision] stale_s`초(기본 1.0) 이내면 `state.pos_source = "vision"`, 그 위치를 쓴다.
- 그보다 오래되면 마지막 추정 위치에서부터 **명령 누적 추정**으로 이어 가고 `pos_source = "estimate"`가 된다.
- 지도 밖 좌표(여유 `[vision] margin_m` 포함)는 `OUT_OF_BOUNDS`로 거부한다.
- **방향 보정 (real, 2026-11-23, `[real] heading_from_vision`):** `confidence` ≥ `heading_min_confidence`인 값의 `yaw_deg`와 TT 방향 센서의 차이를
  모아(최근 `heading_samples`개, 흔들림 ≤ `heading_max_spread_deg`) 이동 방향 계산에 더한다. TT를 지도 위쪽이 아닌 방향으로 켜도 바르게 이동한다.
  TT를 다시 연결하면 새로 모은다. 보정값은 서버 로그의 `방향 보정:`과 `move_to ... (센서 …, 카메라 보정 …)`에 나온다.
- 상태 확인: 브라우저로 `http://<서버 IP>:8000/`을 열면 `vision.last_age_s`(마지막 값을 받은 뒤 지난 초)가 보인다.
- 시험용 가짜 송신 스크립트: `server/tools/fake_vision.py` (사용법은 `server/README.md`)
- 조원용 전체 설명서: `docs/teammate/interface.md`

## 8. 카메라 영상 입력 (`vision/` → 중계 서버) (v0.7)
카메라 영상은 `vision/` 연결 프로그램만 받는다(11장의 모드에 따라 ESP32 카메라 또는 TT 하단 카메라).
연결 프로그램은 위치 계산에 쓰는 프레임을 JPEG로 다시 압축해 서버에 넘기고, 서버가 앱에 나눠 준다(10장).

- 주소: `ws://<서버 IP>:8000/vision/video` (같은 노트북이면 `ws://127.0.0.1:8000/vision/video`)
- **연결 프로그램 → 서버**
  - **바이너리 메시지 하나 = JPEG 사진 한 장.** 흑백(TT 하단 카메라)이어도 그대로 JPEG로 보낸다.
    권장: 가로 `[video] send_width_px` 이하, 초당 `[video] send_fps`장 이하. 한 장 최대 `[video] max_frame_kb` KB(넘으면 서버가 버림).
  - 텍스트 메시지 `{"type": "bridge_status", "mode": "tt_down", "camera_ok": true, "message": "TT 하단 카메라 수신 중"}` —
    연결 직후, 모드를 바꾼 뒤, 카메라 상태가 바뀔 때 보낸다. `mode`는 연결 프로그램이 **실제로 쓰고 있는** 모드다.
- **서버 → 연결 프로그램 (자세·높이, 2026-11-23):** 텍스트 메시지 약 0.1초마다
  `{"type": "drone_state", "t": 1763000000.1, "pitch_deg": 1.0, "roll_deg": -2.0, "yaw_deg": 35.0, "tof_cm": 52.0, "h_cm": 50.0, "flying": true}`
  (아는 값만). 연결 프로그램은 TT 모드 위치 계산에 기울기(중력 방향)를 넣고, 카메라 높이가 `tof_cm`와 `[bridge] tt_tof_check_cm` 넘게 다르면 그 위치를 버린다.
  `[bridge] drone_state_max_age_s`보다 오래된 값은 쓰지 않는다. mock은 기울기 0·거리 센서 = 참값 높이.
- **서버 → 연결 프로그램:** 텍스트 메시지 `{"type": "vision_mode", "mode": "tt_down"}` — 연결 직후 1회, 그리고 앱이 모드를 바꿀 때(11장).
  연결 프로그램은 받으면 입력 카메라와 위치 계산 방식을 바꾸고 `bridge_status`로 답한다.
- 연결 프로그램은 하나만 둔다. 새 연결이 오면 서버는 이전 연결을 닫는다.
- 서버는 **가장 최근 한 장**만 기억한다. 보는 앱이 없으면 받기만 하고 버린다.
- 마지막 사진을 받은 지 `[video] stale_s`초가 지나면 "영상 끊김"으로 본다 (10.2의 `video_status`).

## 9. 사건 보고 입력 (`vision/` → 중계 서버) (v0.7)
사건 사진은 지도의 정해진 **사진 슬롯 S1~S4**(`config.photo_slots`, 3.1)에 붙어 있다.
**기본 방식 = 슬롯 촬영:** 사용자가 앱에서 슬롯으로 드론을 출동시키고, 드론이 슬롯 위에서 멈추면 연결 프로그램이
드론 위치로 슬롯 네 모서리가 화면 어디에 있는지 계산해 그 영역을 똑바로 펴서 잘라 보고한다(사진을 화면에서 "찾지" 않는다).
- 보고 조건(연결 프로그램): ① **그 프레임에서 실제로 측정한** 위치(칼만 추적 예측값만 있는 프레임은 제외 — 예측값으로 자르면 수십 px 어긋남) ② 슬롯 네 모서리가 모두 화면 안(여유 `[incident] slot_margin_px`)
  ③ 화면 속 슬롯의 짧은 변 ≥ `[incident] min_slot_px` ④ 최근 `[incident] still_s`초 동안 드론 이동 ≤ `[incident] still_cm`(호버링 중).
- 잘라낸 사진은 위쪽이 슬롯의 `up` 방향이 되게 펴서 `[incident] crop_width_px` 폭으로 만든다(작게 찍혀도 앱에서 보기 쉽게 확대).
- 조원 코드의 **사진 찾기(`photo_finder`)는 지우지 않고 남겨 둔다.** `[incident] method = "photo_finder"`로 바꾸면 그 방식으로 보고한다(나중에 시험용).
  합성 화면 시험(320×240, 고도 80cm)에서는 사진 찾기가 거의 실패했다(흑백 0/120, 컬러 1/120) — 저해상도에서는 슬롯 촬영이 기본이다.
- 두 모드(ESP32 / TT 하단) 모두 같은 방식으로 보고한다.

### 9.1 보내는 방법: HTTP POST
- 주소: `http://<서버 IP>:8000/vision/incident`
- 본문 (JSON):
```json
{
  "ts": 1763000000.4,
  "mode": "tt_down",
  "method": "slot",
  "slot": 4,
  "place": "S4",
  "x_m": 0.468, "y_m": 0.891,
  "lat": 36.3662462, "lon": 127.3603673,
  "drone": { "x_m": 0.4406, "y_m": 0.8312, "z_m": 0.7895 },
  "photo_jpeg_b64": "/9j/4AAQSkZJRg...",
  "detection": null
}
```
| 필드 | 필수 | 설명 |
|---|---|---|
| `photo_jpeg_b64` | ✅ | 잘라낸 사진을 JPEG로 만들어 base64로 바꾼 문자열. 최대 `[incident] max_photo_kb` KB(디코딩 후) |
| `ts` | | 찍은 시각 (유닉스 초). 없으면 서버가 받은 시각 |
| `mode` | | 찍은 카메라 모드 `esp32` / `tt_down` (11장). 성능 비교용으로 앱에 그대로 전달 |
| `method` | | `slot`(슬롯 촬영) / `photo_finder`(조원 사진 찾기) |
| `slot` | | 1~4. 모르면 생략 또는 `null` |
| `place` | | 장소 설명 문자열 (예: `S4`, `S1 교육지원동 (W8)`) |
| `x_m`, `y_m` | | 사진 중심의 지도 좌표 (m). 둘 다 있어야 쓴다. 지도 밖(여유 `[vision] margin_m`)이면 `OUT_OF_BOUNDS` |
| `lat`, `lon` | | 위도·경도. 둘 다 있어야 쓴다 |
| `drone` | | 찍을 때 드론 위치 (m). `z_m`은 생략 가능 |
| `detection` | | AI 판별 결과(3.5 형식). **현재는 판별을 끄므로 생략 또는 `null`** |

- 응답: 성공 `{"ok": true, "id": "inc-3"}`, 같은 사건으로 보고 무시 `{"ok": true, "id": "inc-2", "duplicate": true}`,
  실패(HTTP 400) `{"ok": false, "error": "BAD_REQUEST" | "OUT_OF_BOUNDS" | "TOO_LARGE", "message": "한국어 설명"}`
- `pos_source`와 상관없이 항상 받는다(위치 입력과 달리 켜는 설정이 없다).
- 서버 검사(v0.7 구현): `slot`은 `[[photo_slots]]`에 있는 번호만, `method`는 `slot`/`photo_finder`만, `mode`는 `config.vision_modes` 안의 값만 받고,
  사진은 실제 JPEG인지 확인한다(아니면 `BAD_REQUEST`).
- `lat`/`lon`이 없고 `x_m`/`y_m`이 있으면 서버가 `[geo]` 네 모서리 보간으로 위도·경도를 채운다.

### 9.2 중복 보고 막기
드론이 슬롯 위에 머무는 동안 조건이 계속 맞으므로 두 곳에서 막는다.
- **연결 프로그램:** 같은 슬롯은 `[incident] report_cooldown_s`초에 한 번만 보낸다. 슬롯이 `null`(사진 찾기 방식)이면 사진 중심이 `[incident] same_place_m` 안이면 같은 사진으로 본다.
- **서버:** 같은 **카메라 모드**의 같은 `slot`(또는 `same_place_m` 안의 위치)으로 `[incident] dedup_s`초 안에 다시 오면
  (모드가 다르면 따로 기록 — 두 카메라를 같은 슬롯에서 비교하기 위해) `duplicate: true`로 응답하고 앱에 다시 알리지 않는다.

## 10. 앱이 쓰는 HTTP·영상 통로 (v0.7)

### 10.1 사건 사진: `GET /incidents/{id}/photo.jpg`
- `http://<서버 IP>:8000/incidents/inc-3/photo.jpg` → JPEG. 없는 id(서버 재시작 등)면 HTTP 404 `{"ok": false, "error": "NOT_FOUND", ...}`.
- Flutter web(다른 포트)에서도 읽을 수 있도록 서버는 이 응답에 `Access-Control-Allow-Origin: *`를 붙인다.

### 10.2 실시간 영상: `ws://<서버 IP>:8000/ws/video`
- 앱이 **영상 화면을 열 때만** 연결하고, 화면을 닫으면 끊는다 (데이터 절약).
- 서버 → 앱:
  - **바이너리 메시지 = JPEG 한 장.** 앱은 받은 순서대로 바꿔 보여 준다(최신 한 장만 표시).
  - 텍스트 메시지 `{"type": "video_status", "live": false, "message": "카메라 영상이 없습니다"}` — 연결 직후 1회, 그리고 영상이 끊기거나(`stale_s`) 다시 들어올 때(`live: true`).
- 서버는 앱 하나당 초당 `[video] max_fps`장까지만 보낸다. 앱이 느려 밀리면 오래된 장은 버리고 최신 장을 보낸다.
  한 장을 5초 넘게 받아 가지 못하는 앱은 연결을 끊는다(앱은 다시 연결한다).
- 앱 → 서버: 보내는 메시지 없음.
- 앱은 사건을 `id@ts`로 구별한다(서버 재시작 후 같은 `inc-1`이 다시 나와도 새 사건으로 보이게). 연결 직후의 `incidents` 목록은 배지만 갱신하고 알림은 띄우지 않는다. 명령권과 관계없이 연결된 앱은 모두 볼 수 있고, 생존 신호(4.2)와도 무관하다.
- 영상은 드론 비행 안전 기능과 별개다. 영상 연결이 끊겨도 비행에는 영향이 없다.

## 11. 위치 추적 모드 전환: ESP32 카메라 ⇄ TT 하단 카메라 (v0.7)
실제 시연 후 성능을 비교하기 위해 **앱에서 위치 추적 카메라를 바꿀 수 있다.** 두 방식을 **동시에 쓰지 않는다**(값이 섞여 위치가 꼬이지 않도록).

| 모드 | 카메라 | 영상 경로 | 위치 계산 (조원 코드) |
|---|---|---|---|
| `esp32` | 드론에 단 XIAO ESP32 카메라 (컬러) | XIAO → Wi-Fi(MJPEG) → 연결 프로그램 | `localization_app`: 건물·도로 색 정합 + AprilTag 융합, 칼만 추적 |
| `tt_down` | TT 내장 하단 카메라 (**흑백 320×240**) | TT → UDP 11111(H.264) → 연결 프로그램 | `incident_detection`: AprilTag 위치(흑백 전용), 칼만 추적 |

- 처음 모드: `[bridge] default_mode`. 서버가 기억하고, 앱이 바꾼다(4.1 `set_vision_mode`).
- **바꾸는 순서 (서버)**
  1. 명령권 앱의 `set_vision_mode`를 받는다. 드론이 `idle` 또는 `hovering`일 때(비상정지 뒤 바닥에 있을 때 포함)만 받고, 그 밖(이동·이착륙·귀환 중)이면 `BUSY`.
  2. 서버의 모드를 바꾸고 **마지막 카메라 위치를 버린다**(이전 모드 값이 남지 않게). 새 값이 올 때까지는 명령 누적 추정(`estimate`)으로 이어 간다.
  3. 연결 프로그램에 `vision_mode`를 보낸다(8장).
  4. 실제 드론(`real`)이면 `tt_down`일 때 TT에 `downvision 1` → `streamon`(하단 카메라로 먼저 전환한 뒤 영상 켜기), `esp32`일 때 `streamoff`를 보낸다.
     (TT 영상은 UDP 11111로 노트북에 오며 **연결 프로그램이 받는다.** 서버는 영상을 받지 않는다 — 포트는 한 프로그램만 쓸 수 있다.)
     카메라 명령은 뒤에서 보내므로 `ack`는 바로 `ok`이고(모드는 바뀜), TT가 실패를 돌려주면 `CMD_FAILED` event로 알린다. 서버를 켤 때·드론이 연결될 때도 현재 모드에 맞게 보낸다.
  5. `state.vision`에 새 모드를 표시한다.
- **값이 섞이지 않게:** `POST /vision/pose`·`/vision/incident`에 `mode`를 넣어 보낸다. 서버의 현재 모드와 다르면
  위치는 `WRONG_MODE`(HTTP 400)로 거부하고, 사건 보고는 받되 `mode`를 그대로 기록한다(어느 카메라로 찍었는지 남기기 위해).
- 시뮬레이터(`sim`)·가짜 드론(`mock`)에는 실제 카메라가 없다. 연결 프로그램의 **합성 카메라**(12장)로 시험한다.

## 12. 시험용 합성 카메라 (`mock`/`sim` 전용) (v0.7)
Unity 시뮬레이터는 바닥에 인쇄 지도·AprilTag가 없는 3D 캠퍼스라 위치 인식을 시험할 수 없다.
그래서 연결 프로그램은 조원 지도 그림(`map_with_tags*.png`)에서 **드론 참값 위치에서 보이는 화면을 직접 그려** 카메라 대신 쓴다.
- `esp32` 모드: 컬러 640×480(또는 설정값), `tt_down` 모드: 흑백 320×240 — 실제 카메라와 같은 크기·색으로 만든다.
- 참값 위치: `GET http://<서버 IP>:8000/sim/truth` → `{"ok": true, "x_m": 1.53, "y_m": 0.70, "z_m": 0.80, "yaw_deg": 0.0}`
  (`mock`은 가짜 드론의 실제 위치, `sim`은 시뮬레이터 참값을 지도 좌표로 바꾼 값). `yaw_deg`는 2장 기준(지도 위쪽 0°, 시계 +).
  `real`에서는 `{"ok": false, "error": "BAD_REQUEST"}`, `sim`에서 아직 시뮬레이터 상태를 못 읽었으면 `NOT_CONNECTED`.
- 합성 화면 기본 그림은 태그와 예시 사건 사진이 함께 있는 `map_with_photos(_gray).png`다(슬롯 촬영까지 시험되도록).
  이 값은 **합성 카메라 그리기에만** 쓴다. 서버 위치(`state.pos`)는 여전히 연결 프로그램이 계산해 보낸 값이다.

## 변경 기록
| 버전 | 날짜 | 내용 |
|---|---|---|
| 0.1 | 2026-11-11 | 최초 초안 |
| 0.2 | 2026-11-11 | `geo`를 네 모서리(sw/se/nw/ne)+`rotation_deg`로 변경, 쌍선형 보간 필수. 지도 x/y가 실제 동/북이 아님을 명시 (지도 30° 회전 발견) |
| 0.3 | 2026-11-11 | 구현에 맞춰 명시: `backend`에 `mock`, event 코드 목록, 성공 ack 형식, `cmd`도 생존 신호, goto 교체·이륙 중 BUSY, 제어 권한 없는 앱은 BUSY (메시지 형식 변화 없음) |
| 0.4 | 2026-11-11 | 7장 추가: 카메라 위치 추정 입력 `POST /vision/pose` (m 단위, 지도 왼쪽 아래 원점). `pos_source`에 `vision` 추가 |
| 0.5 | 2026-11-11 | 구현에 맞춰 명시: 실패 응답 `message`, vision 꺼짐·confidence 범위 → `BAD_REQUEST`, `ts`는 순서 확인용(오래되면 무시), stale 판정은 서버 수신 시각, vision `yaw_deg`는 표시용, 권장 빈도 2Hz 이상, `/` 상태의 `vision.last_age_s`, event `BAD_REQUEST`, 이륙 중 `return_home` BUSY, 귀환 중 goto 교체, `z_cm` 생략 시 고도 클램프, 도착 오차 약 20cm, `api_version` = 명세 버전 |
| 0.6 | 2026-11-11 | 명령권 넘겨받기: 명령권 앱이 끊기면 남은 앱 중 가장 최근 연결 앱이 이어받음. 자동 착륙은 남은 앱이 없을 때(또는 명령권 앱의 ping이 끊길 때) |
| 0.7 | 2026-11-21 | (2026-11-25 추가, 메시지 형식 변화 없음: 초기 정렬 event `ALIGN_START`·`ALIGN_DONE`·`ALIGN_FAILED`·`ALIGN_SKIPPED`, 정렬 중 `taking_off`. real은 이동 전 목표 쪽으로 기수를 돌림 — 서버 내부 동작) 2단계 병합: `/vision/pose`에 `z_m`(표시용) 추가, 조원 yaw 기준 변환 명시. 사건 보고 `POST /vision/incident`(9장), 앱 메시지 `incident`·`incidents`(3.5·3.6), event `INCIDENT_NEW`, 사건 사진 `GET /incidents/{id}/photo.jpg`. 실시간 영상: 입력 `ws /vision/video`(8장), 앱 `ws /ws/video`(10.2). AI 판별은 꺼 둠(`detection: null`). 위치 추적 모드 `esp32`(XIAO 컬러)/`tt_down`(TT 하단 흑백 320×240) 전환: `cmd set_vision_mode`, `state.vision`, `config.vision_modes`, 서버↔연결 프로그램 `vision_mode`/`bridge_status`, `WRONG_MODE`(11장). 사건 보고 기본 = 슬롯 촬영(`config.photo_slots`, 9장). 시험용 합성 카메라와 `GET /sim/truth`(12장). 구현 후 보완: 모듈별 yaw 기준, 슬롯 촬영은 측정 자세만, 서버 사건 검사·위경도 채움·모드별 중복 판정, 영상 앱 5초 정지 시 끊음, 사진 404 `NOT_FOUND`, 비상정지 후 바닥에서도 모드 전환, 카메라 명령 실패는 `CMD_FAILED`, 앱 사건 키 `id@ts` |
