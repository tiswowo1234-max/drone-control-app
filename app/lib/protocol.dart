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
  });

  final double? ts;
  final bool connected;
  final bool flying;
  final String mode;
  final DronePos? pos;
  final String? posSource;
  final Offset? target;
  final double? batteryPct;

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
      null => '-',
      _ => s,
    };

double? _num(Object? v) => v is num ? v.toDouble() : null;
