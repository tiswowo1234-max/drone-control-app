import 'dart:ui';

import 'config.dart';

/// 앱 쪽 목표 지점 사전 검사 결과 (docs/api.md 6장).
/// 서버가 최종적으로 다시 검사하므로 여기서는 사용자에게 미리 알려 주는 용도다.
class TargetIssue {
  const TargetIssue(this.code, this.message);

  /// 서버 오류 코드와 같은 이름 (OUT_OF_BOUNDS, NO_FLY_ZONE, TOO_FAR)
  final String code;
  final String message;
}

/// 목표 지점이 갈 수 없는 곳이면 그 이유를, 괜찮으면 null을 돌려준다.
TargetIssue? checkTarget(AppConfig config, Offset cm) {
  if (cm.dx < 0 ||
      cm.dy < 0 ||
      cm.dx > config.mapWidthCm ||
      cm.dy > config.mapHeightCm) {
    return const TargetIssue('OUT_OF_BOUNDS', '지도 경계 밖입니다');
  }
  for (final z in config.noFlyZones) {
    if (pointInPolygon(cm, z.polygonCm)) {
      return TargetIssue('NO_FLY_ZONE', '비행 금지 구역입니다 (${z.name})');
    }
  }
  final dist = (cm - config.home).distance;
  if (dist > config.limits.maxRadiusCm) {
    return TargetIssue(
      'TOO_FAR',
      '이륙 지점에서 너무 멉니다 '
          '(${dist.toStringAsFixed(0)} cm > 최대 ${config.limits.maxRadiusCm.toStringAsFixed(0)} cm)',
    );
  }
  return null;
}

/// 다각형 안에 점이 있는지 (짝홀 규칙)
bool pointInPolygon(Offset p, List<Offset> poly) {
  if (poly.length < 3) return false;
  var inside = false;
  for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    final a = poly[i], b = poly[j];
    if ((a.dy > p.dy) != (b.dy > p.dy) &&
        p.dx < (b.dx - a.dx) * (p.dy - a.dy) / (b.dy - a.dy) + a.dx) {
      inside = !inside;
    }
  }
  return inside;
}

/// 출동(goto) 자체를 막아야 하는 상태가 있으면 그 이유를 돌려준다.
/// [serverOnline]: 중계 서버와 연결됨, [droneConnected]: 서버가 드론과 연결됨,
/// [batteryPct]: 모르면 null (서버가 최종 판단).
String? checkDispatchReady({
  required AppConfig config,
  required bool serverOnline,
  required bool droneConnected,
  required double? batteryPct,
}) {
  if (!serverOnline) return '서버에 연결되어 있지 않습니다';
  if (!droneConnected) return '드론이 연결되어 있지 않습니다';
  if (batteryPct != null && batteryPct < config.limits.minBatteryPct) {
    return '배터리 부족 (${batteryPct.toStringAsFixed(0)}% < '
        '${config.limits.minBatteryPct.toStringAsFixed(0)}%)';
  }
  return null;
}
