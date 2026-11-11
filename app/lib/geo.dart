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

/// 지도 cm 좌표 → 위도·경도.
///
/// 지도 왼쪽 아래 = `geo.sw`, 오른쪽 위 = `geo.ne`로 보고 선형 보간한다.
/// 캠퍼스 정도의 좁은 범위에서는 이 근사로 충분하다 (참고용 표시).
LatLon cmToLatLon(AppConfig config, Offset cm) {
  final g = config.geo;
  final fx = cm.dx / config.mapWidthCm;
  final fy = cm.dy / config.mapHeightCm;
  return LatLon(
    g.swLat + fy * (g.neLat - g.swLat),
    g.swLon + fx * (g.neLon - g.swLon),
  );
}
