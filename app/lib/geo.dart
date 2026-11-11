import 'dart:ui';

import 'config.dart';

/// 위도·경도 한 점 (화면 표시용)
class LatLon {
  const LatLon(this.lat, this.lon);

  final double lat;
  final double lon;

  /// 예: "36.36721, 127.36285"
  String format({int digits = 5}) =>
      '${lat.toStringAsFixed(digits)}, ${lon.toStringAsFixed(digits)}';
}

/// 지도 cm 좌표 → 위도·경도 (docs/api.md v0.2).
///
/// 지도가 회전돼 있으므로 네 모서리 쌍선형 보간을 쓴다.
/// u = x/가로, v = y/세로일 때
/// lat = (1-u)(1-v)·sw + u(1-v)·se + (1-u)v·nw + uv·ne  (lon도 같은 식)
LatLon cmToLatLon(AppConfig config, Offset cm) {
  final g = config.geo;
  final u = cm.dx / config.mapWidthCm;
  final v = cm.dy / config.mapHeightCm;
  double mix(double sw, double se, double nw, double ne) =>
      (1 - u) * (1 - v) * sw + u * (1 - v) * se + (1 - u) * v * nw + u * v * ne;
  return LatLon(
    mix(g.swLat, g.seLat, g.nwLat, g.neLat),
    mix(g.swLon, g.seLon, g.nwLon, g.neLon),
  );
}
