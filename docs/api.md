# 앱 ⇄ 중계 서버 API 명세 (초안 v0.3)

> 상태: **초안**. 조원(라즈베리파이·미션패드 담당)과 협의해 수정·통일할 예정이다.
> 바뀌면 이 문서의 버전과 변경 기록을 먼저 고친 다음 코드를 고친다.

## 1. 연결
- 방식: WebSocket, 메시지는 UTF-8 JSON 한 개씩
- 주소: `ws://<서버 IP>:8000/ws` (예: PC가 `192.168.0.10`이면 `ws://192.168.0.10:8000/ws`)
- 앱 하나만 연결하는 것을 기본으로 한다. 여러 개가 연결되면 모두 상태를 받지만, 명령은 마지막에 연결한 앱만 보낼 수 있다.
  제어 권한이 없는 앱이 명령을 보내면 `BUSY`로 거부한다. 제어 권한이 바뀌면 `CONTROLLER_CHANGED` 이벤트를 보낸다.

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
  "api_version": "0.1",
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
  "backend": "sim"
}
```
- 숫자는 모두 예시이며 서버 설정 파일에서 정한다.
- `backend`: `mock`(Unity 없는 가짜 드론) · `sim`(시뮬레이터) · `real`(실제 드론). 화면 표시용이다.
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
  "battery_pct": 76
}
```
- `connected`: 서버가 드론(또는 시뮬레이터)과 통신 중인지 여부
- `mode`: `idle`(착지) · `taking_off` · `hovering` · `moving` · `returning` · `landing` · `emergency`
- `pos_source`: `estimate`(명령 누적 추정) · `mission_pad`(미션패드 측정) · `sim_truth`(시뮬레이터 디버그 값)
- `pos`, `battery_pct`는 모를 때 `null`이다.
- `target`은 이동 중이 아니면 `null`이다.

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

## 4. 앱 → 서버

### 4.1 `cmd` — 명령
```json
{ "type": "cmd", "id": "c-17", "cmd": "goto", "x_cm": 150, "y_cm": 100, "z_cm": 80 }
```
| cmd | 추가 필드 | 설명 |
|---|---|---|
| `takeoff` | — | 이륙 후 `default_alt_cm`에서 호버링 |
| `goto` | `x_cm`, `y_cm`, (`z_cm` 생략 시 현재 고도) | 목표 지점으로 이동. 착지 상태이면 이륙부터 한다 |
| `stop` | — | 그 자리에서 호버링 |
| `land` | — | 그 자리에 착륙 |
| `return_home` | — | `home`으로 이동한 뒤 착륙 |
| `emergency` | `confirm: true` | **모터 즉시 정지(추락)**. 앱에서 한 번 더 확인한 경우에만 보낸다 |

- `id`: 앱이 정하는 고유 문자열. `ack`에 그대로 돌려준다.
- `stop`, `land`, `emergency`는 다른 명령이 진행 중이어도 즉시 처리한다.
  (시뮬레이터는 진행 중인 이동 구간을 끊지 못해 `stop`이 약 2초 늦을 수 있다. `emergency`는 즉시.)
- 이동 중에 새 `goto`가 오면 기존 이동을 취소하고 새 목표로 간다.
- 이륙 중(`taking_off`)에 `goto`가 오면 `BUSY`로 거부한다.

### 4.2 `ping` — 생존 신호 (1초마다)
```json
{ "type": "ping", "ts": 1763000000.5 }
```
- 비행 중에 서버가 `link_timeout_s`초 동안 `ping`을 받지 못하면 **자동 착륙**한다.
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

## 6. 안전 검사 위치
- 앱: 마커를 놓을 때 경계·금지 구역·반경을 미리 검사해 사용자에게 바로 알려 준다 (편의 목적).
- 서버: 모든 명령을 **다시 검사**한다 (최종 방어선). 앱 검사를 믿지 않는다.

## 변경 기록
| 버전 | 날짜 | 내용 |
|---|---|---|
| 0.1 | 2026-11-11 | 최초 초안 |
| 0.2 | 2026-11-11 | `geo`를 네 모서리(sw/se/nw/ne)+`rotation_deg`로 변경, 쌍선형 보간 필수. 지도 x/y가 실제 동/북이 아님을 명시 (지도 30° 회전 발견) |
| 0.3 | 2026-11-11 | 구현에 맞춰 명시: `backend`에 `mock`, event 코드 목록, 성공 ack 형식, `cmd`도 생존 신호, goto 교체·이륙 중 BUSY, 제어 권한 없는 앱은 BUSY (메시지 형식 변화 없음) |
