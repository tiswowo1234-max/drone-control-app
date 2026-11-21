import 'dart:convert';
import 'dart:ui';

import 'config.dart';

/// 앱 ⇄ 서버 메시지 (docs/api.md v0.1)

/// 드론 위치·자세 (`state.pos`)
class DronePos {
  const DronePos({
    required this.xCm,
    required this.yCm,
    required this.zCm,
    required this.yawDeg,
  });

  final double xCm;
  final double yCm;
  final double zCm;

  /// 지도 위쪽(+y)이 0, 시계 방향이 +
  final double yawDeg;

  Offset get xy => Offset(xCm, yCm);

  static DronePos? fromJson(Object? v) {
    if (v is! Map) return null;
    final x = _num(v['x_cm']), y = _num(v['y_cm']);
    if (x == null || y == null) return null;
    return DronePos(
      xCm: x,
      yCm: y,
      zCm: _num(v['z_cm']) ?? 0,
      yawDeg: _num(v['yaw_deg']) ?? 0,
    );
  }
}

/// 서버 → 앱 `state` (docs/api.md 3.2)
class DroneState {
  const DroneState({
    required this.connected,
    required this.flying,
    required this.mode,
    this.ts,
    this.pos,
    this.posSource,
    this.target,
    this.batteryPct,
    this.vision,
  });

  final double? ts;
  final bool connected;
  final bool flying;
  final String mode;
  final DronePos? pos;
  final String? posSource;
  final Offset? target;
  final double? batteryPct;

  /// 위치 추적 카메라 상태 (v0.7). 이전 서버면 null.
  final VisionStatus? vision;

  /// 위치 추적 모드를 바꿀 수 있는 드론 상태인지 (docs/api.md 11장: 착지·호버링일 때만)
  bool get canSwitchVision => mode == 'idle' || mode == 'hovering';

  factory DroneState.fromJson(Map<String, dynamic> j) {
    final t = j['target'];
    Offset? target;
    if (t is Map && _num(t['x_cm']) != null && _num(t['y_cm']) != null) {
      target = Offset(_num(t['x_cm'])!, _num(t['y_cm'])!);
    }
    return DroneState(
      ts: _num(j['ts']),
      connected: j['connected'] == true,
      flying: j['flying'] == true,
      mode: j['mode'] is String ? j['mode'] as String : 'unknown',
      pos: DronePos.fromJson(j['pos']),
      posSource: j['pos_source'] is String ? j['pos_source'] as String : null,
      target: target,
      batteryPct: _num(j['battery_pct']),
      vision: VisionStatus.fromJson(j['vision']),
    );
  }
}

/// `state.vision` — 위치 추적 카메라 상태 (docs/api.md v0.7 3.2·11장)
class VisionStatus {
  const VisionStatus({
    required this.mode,
    this.bridgeConnected = false,
    this.bridgeMode,
    this.cameraOk,
    this.videoLive = false,
  });

  /// 서버가 정한 모드 (`esp32` / `tt_down`)
  final String? mode;

  /// vision 연결 프로그램이 서버에 붙어 있는지
  final bool bridgeConnected;

  /// 연결 프로그램이 마지막으로 알린 실제 모드 (모르면 null)
  final String? bridgeMode;

  /// 연결 프로그램이 알린 카메라 상태 (모르면 null)
  final bool? cameraOk;

  /// 카메라 영상이 들어오는 중인지
  final bool videoLive;

  /// 연결 프로그램이 아직 새 모드로 바뀌지 않음 → "전환 중"
  bool get switching =>
      bridgeConnected && bridgeMode != null && mode != null && bridgeMode != mode;

  static VisionStatus? fromJson(Object? v) {
    if (v is! Map) return null;
    return VisionStatus(
      mode: v['mode'] is String ? v['mode'] as String : null,
      bridgeConnected: v['bridge_connected'] == true,
      bridgeMode: v['bridge_mode'] is String ? v['bridge_mode'] as String : null,
      cameraOk: v['camera_ok'] is bool ? v['camera_ok'] as bool : null,
      videoLive: v['video_live'] == true,
    );
  }
}

/// AI 판별 결과 (docs/api.md v0.7 3.5 `detection`). 현재 서버는 판별을 꺼 두어 항상 null.
class Detection {
  const Detection({
    this.isAbnormal,
    this.category,
    this.urgency,
    this.needsHumanReview,
    this.imageQuality,
    this.description,
  });

  final bool? isAbnormal;
  final String? category;
  final String? urgency; // none · low · medium · high
  final bool? needsHumanReview;
  final String? imageQuality; // good · degraded · unusable
  final String? description;

  static Detection? fromJson(Object? v) {
    if (v is! Map) return null;
    String? s(String k) => v[k] is String ? v[k] as String : null;
    bool? b(String k) => v[k] is bool ? v[k] as bool : null;
    return Detection(
      isAbnormal: b('is_abnormal'),
      category: s('category'),
      urgency: s('urgency'),
      needsHumanReview: b('needs_human_review'),
      imageQuality: s('image_quality'),
      description: s('description'),
    );
  }
}

/// 사건 보고 하나 (docs/api.md v0.7 3.5)
class Incident {
  const Incident({
    required this.id,
    this.ts,
    this.slot,
    this.place,
    this.pos,
    this.lat,
    this.lon,
    this.dronePos,
    this.droneZCm,
    this.photoUrl,
    this.mode,
    this.method,
    this.detection,
  });

  final String id;

  /// 사진을 찍은 시각 (유닉스 초)
  final double? ts;
  final int? slot;
  final String? place;

  /// 사진 중심의 지도 좌표 (cm)
  final Offset? pos;
  final double? lat, lon;

  /// 찍을 때 드론 위치 (cm)
  final Offset? dronePos;
  final double? droneZCm;

  /// 서버 주소 뒤에 붙이는 사진 경로 (예: `/incidents/inc-3/photo.jpg`)
  final String? photoUrl;

  /// 찍은 카메라 모드 (`esp32` / `tt_down`)
  final String? mode;

  /// `slot` / `photo_finder`
  final String? method;
  final Detection? detection;

  /// 앱 안에서 "확인함"을 기억할 때 쓰는 열쇠.
  /// 서버를 다시 켜면 id가 inc-1부터 다시 시작하므로 시각도 함께 쓴다.
  String get key => '$id@${ts ?? ''}';

  DateTime? get time => ts == null
      ? null
      : DateTime.fromMillisecondsSinceEpoch((ts! * 1000).round());

  /// 사람이 읽는 장소 (예: "S4 · slot S4, unnamed building")
  String get placeText {
    final s = slot == null ? null : 'S$slot';
    final parts = <String>[
      ?s,
      if (place != null && place!.isNotEmpty && place != s) place!,
    ];
    return parts.isEmpty ? '장소 모름' : parts.join(' · ');
  }

  static Incident? fromJson(Object? v) {
    if (v is! Map) return null;
    final id = v['id'];
    if (id == null) return null;
    Offset? xy(Object? p) {
      if (p is! Map) return null;
      final x = _num(p['x_cm']), y = _num(p['y_cm']);
      return (x == null || y == null) ? null : Offset(x, y);
    }

    final geo = v['geo'];
    final drone = v['drone_pos'];
    String? s(String k) => v[k] is String ? v[k] as String : null;
    return Incident(
      id: id.toString(),
      ts: _num(v['ts']),
      slot: v['slot'] is num ? (v['slot'] as num).toInt() : null,
      place: s('place'),
      pos: xy(v['pos']),
      lat: geo is Map ? _num(geo['lat']) : null,
      lon: geo is Map ? _num(geo['lon']) : null,
      dronePos: xy(drone),
      droneZCm: drone is Map ? _num(drone['z_cm']) : null,
      photoUrl: s('photo_url'),
      mode: s('mode'),
      method: s('method'),
      detection: Detection.fromJson(v['detection']),
    );
  }
}

/// 서버 → 앱 `ack` (docs/api.md 3.3)
class Ack {
  const Ack({required this.id, required this.ok, this.error, this.message});

  final String id;
  final bool ok;
  final String? error;
  final String? message;

  factory Ack.fromJson(Map<String, dynamic> j) => Ack(
        id: j['id']?.toString() ?? '',
        ok: j['ok'] == true,
        error: j['error'] is String ? j['error'] as String : null,
        message: j['message'] is String ? j['message'] as String : null,
      );

  /// 사용자에게 보여 줄 한국어 오류 문장
  String get koreanMessage => errorMessageKo(error, message);
}

/// 서버 → 앱 `event` (docs/api.md 3.4)
class DroneEvent {
  const DroneEvent({required this.level, required this.code, required this.message});

  final String level; // info · warn · error
  final String code;
  final String message;

  factory DroneEvent.fromJson(Map<String, dynamic> j) => DroneEvent(
        level: j['level'] is String ? j['level'] as String : 'info',
        code: j['code']?.toString() ?? '',
        message: j['message']?.toString() ?? '',
      );
}

/// 서버에서 받은 메시지 하나. 알 수 없는 메시지는 null.
sealed class ServerMessage {
  const ServerMessage();

  static ServerMessage? parse(Object? raw) {
    Object? decoded;
    try {
      decoded = raw is String ? jsonDecode(raw) : null;
    } on FormatException {
      return null;
    }
    if (decoded is! Map) return null;
    final j = decoded.cast<String, dynamic>();
    switch (j['type']) {
      case 'config':
        return ConfigMsg(AppConfig.fromJson(j));
      case 'state':
        return StateMsg(DroneState.fromJson(j));
      case 'ack':
        return AckMsg(Ack.fromJson(j));
      case 'event':
        return EventMsg(DroneEvent.fromJson(j));
      case 'incident':
        final inc = Incident.fromJson(j['incident']);
        return inc == null ? null : IncidentMsg(inc);
      case 'incidents':
        final items = j['items'] is List ? j['items'] as List : const [];
        return IncidentsMsg([for (final v in items) ?Incident.fromJson(v)]);
    }
    return null;
  }
}

class ConfigMsg extends ServerMessage {
  const ConfigMsg(this.config);
  final AppConfig config;
}

class StateMsg extends ServerMessage {
  const StateMsg(this.state);
  final DroneState state;
}

class AckMsg extends ServerMessage {
  const AckMsg(this.ack);
  final Ack ack;
}

class EventMsg extends ServerMessage {
  const EventMsg(this.event);
  final DroneEvent event;
}

/// 새 사건 하나 (docs/api.md 3.5)
class IncidentMsg extends ServerMessage {
  const IncidentMsg(this.incident);
  final Incident incident;
}

/// 최근 사건 목록, 최신이 앞 (docs/api.md 3.6)
class IncidentsMsg extends ServerMessage {
  const IncidentsMsg(this.items);
  final List<Incident> items;
}

/// 앱 → 서버 `cmd` JSON (docs/api.md 4.1)
Map<String, dynamic> buildCmd(String id, String cmd, [Map<String, dynamic>? extra]) =>
    {'type': 'cmd', 'id': id, 'cmd': cmd, ...?extra};

/// 앱 → 서버 `ping` JSON (docs/api.md 4.2)
Map<String, dynamic> buildPing(DateTime now) =>
    {'type': 'ping', 'ts': now.millisecondsSinceEpoch / 1000.0};

/// 앱 자체에서 쓰는 오류 코드 (서버 명세에는 없음)
const kErrNotSent = 'APP_NOT_SENT';
const kErrTimeout = 'APP_TIMEOUT';

/// 오류 코드 → 한국어 문장. 모르는 코드면 서버가 보낸 message를 쓴다.
String errorMessageKo(String? code, [String? serverMessage]) {
  switch (code) {
    case 'OUT_OF_BOUNDS':
      return '지도 경계 밖 좌표입니다';
    case 'NO_FLY_ZONE':
      return '비행 금지 구역입니다';
    case 'TOO_FAR':
      return '이륙 지점에서 너무 멉니다 (최대 반경 초과)';
    case 'ALT_LIMIT':
      return '고도 제한을 벗어났습니다';
    case 'LOW_BATTERY':
      return '배터리가 부족해 출동할 수 없습니다';
    case 'NOT_CONNECTED':
      return '드론과 연결되어 있지 않습니다';
    case 'BUSY':
      return '지금은 이 명령을 처리할 수 없습니다 (예: 착륙 중)';
    case 'NOT_CONFIRMED':
      return '비상 정지 확인이 빠졌습니다';
    case 'BAD_REQUEST':
      return '명령 형식이 잘못되었습니다';
    case 'WRONG_MODE':
      return '위치 추적 카메라 모드가 맞지 않습니다';
    case kErrNotSent:
      return '서버에 연결되어 있지 않아 명령을 보내지 못했습니다';
    case kErrTimeout:
      return '서버 응답이 없습니다';
  }
  if (serverMessage != null && serverMessage.isNotEmpty) return serverMessage;
  return '알 수 없는 오류 (${code ?? '코드 없음'})';
}

/// state.mode → 한국어
String modeKo(String mode) => switch (mode) {
      'idle' => '착지',
      'taking_off' => '이륙 중',
      'hovering' => '호버링',
      'moving' => '이동 중',
      'returning' => '귀환 중',
      'landing' => '착륙 중',
      'emergency' => '비상 정지',
      _ => mode,
    };

/// state.pos_source → 한국어
String posSourceKo(String? s) => switch (s) {
      'estimate' => '추정값',
      'mission_pad' => '미션패드',
      'sim_truth' => '시뮬레이터',
      'vision' => '카메라',
      null => '-',
      _ => s,
    };

/// 위치를 실제로 측정한 값인지 (카메라·미션패드·시뮬레이터 실제값).
/// false면 명령을 누적해 계산한 추정값이라 오차가 쌓일 수 있다.
bool posSourceMeasured(String? s) =>
    s == 'vision' || s == 'mission_pad' || s == 'sim_truth';

/// 위치 추적 카메라 모드 → 한국어 (docs/api.md 11장)
String visionModeKo(String? m) => switch (m) {
      'esp32' => 'ESP32 카메라',
      'tt_down' => 'TT 하단 카메라',
      null => '-',
      _ => m,
    };

/// 짧은 이름 (상태 줄용)
String visionModeShort(String? m) => switch (m) {
      'esp32' => 'ESP32',
      'tt_down' => 'TT 하단',
      null => '-',
      _ => m,
    };

/// 사건 사진 방식 → 한국어
String incidentMethodKo(String? m) => switch (m) {
      'slot' => '슬롯 촬영',
      'photo_finder' => '사진 찾기',
      null => '-',
      _ => m,
    };

/// AI 판별 긴급도 → 한국어
String urgencyKo(String? u) => switch (u) {
      'none' => '없음',
      'low' => '낮음',
      'medium' => '보통',
      'high' => '높음',
      null => '-',
      _ => u,
    };

double? _num(Object? v) => v is num ? v.toDouble() : null;
