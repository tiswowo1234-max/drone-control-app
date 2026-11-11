import 'dart:ui';

/// 앱이 쓰는 설정값.
///
/// 원본은 `config/settings.toml`이며, 중계 서버가 연결 직후 `config` 메시지로 보내 준다
/// (docs/api.md 3.1). 서버에 연결되기 전에는 아래 [AppConfig.fallback] 값을 쓰고,
/// `config` 메시지를 받으면 [AppConfig.fromJson]으로 만든 값으로 통째로 바꾼다.
/// settings.toml을 바꾸면 이 기본값도 같이 맞춰 둔다.
class AppConfig {
  const AppConfig({
    required this.mapWidthCm,
    required this.mapHeightCm,
    required this.homeXCm,
    required this.homeYCm,
    required this.limits,
    required this.geo,
    this.noFlyZones = const [],
    this.backend = 'unknown',
    this.fromServer = false,
  });

  /// 바닥 지도의 실제 가로 길이 (cm)
  final double mapWidthCm;

  /// 바닥 지도의 실제 세로 길이 (cm)
  final double mapHeightCm;

  /// 이륙·귀환 지점 (cm, 지도 왼쪽 아래 기준)
  final double homeXCm;
  final double homeYCm;

  final Limits limits;
  final GeoBounds geo;
  final List<NoFlyZone> noFlyZones;

  /// `sim`(시뮬레이터) · `real`(실제 드론) · `unknown`(서버에서 아직 못 받음)
  final String backend;

  /// 서버가 보낸 값이면 true, 앱 기본값이면 false
  final bool fromServer;

  Offset get home => Offset(homeXCm, homeYCm);

  /// 서버 연결 전 기본값 (config/settings.toml과 동일하게 유지)
  static const fallback = AppConfig(
    mapWidthCm: 250,
    mapHeightCm: 180,
    homeXCm: 20,
    homeYCm: 20,
    limits: Limits(
      maxRadiusCm: 250,
      minAltCm: 50,
      maxAltCm: 150,
      defaultAltCm: 80,
      minBatteryPct: 20,
      linkTimeoutS: 3,
    ),
    geo: GeoBounds(
      swLat: 36.3642100,
      swLon: 127.3589200,
      neLat: 36.3702100,
      neLon: 127.3667800,
    ),
    backend: 'unknown',
  );

  /// 서버 `config` 메시지(docs/api.md 3.1) → 설정값.
  /// 빠진 항목은 [fallback] 값을 그대로 쓴다.
  factory AppConfig.fromJson(Map<String, dynamic> j) {
    const f = fallback;
    final map = _obj(j['map']);
    final home = _obj(j['home']);
    final lim = _obj(j['limits']);
    final geo = _obj(j['geo']);
    final sw = _obj(geo['sw']);
    final ne = _obj(geo['ne']);
    final fl = f.limits;
    return AppConfig(
      mapWidthCm: _num(map['width_cm']) ?? f.mapWidthCm,
      mapHeightCm: _num(map['height_cm']) ?? f.mapHeightCm,
      homeXCm: _num(home['x_cm']) ?? f.homeXCm,
      homeYCm: _num(home['y_cm']) ?? f.homeYCm,
      limits: Limits(
        maxRadiusCm: _num(lim['max_radius_cm']) ?? fl.maxRadiusCm,
        minAltCm: _num(lim['min_alt_cm']) ?? fl.minAltCm,
        maxAltCm: _num(lim['max_alt_cm']) ?? fl.maxAltCm,
        defaultAltCm: _num(lim['default_alt_cm']) ?? fl.defaultAltCm,
        minBatteryPct: _num(lim['min_battery_pct']) ?? fl.minBatteryPct,
        linkTimeoutS: _num(lim['link_timeout_s']) ?? fl.linkTimeoutS,
      ),
      geo: GeoBounds(
        swLat: _num(sw['lat']) ?? f.geo.swLat,
        swLon: _num(sw['lon']) ?? f.geo.swLon,
        neLat: _num(ne['lat']) ?? f.geo.neLat,
        neLon: _num(ne['lon']) ?? f.geo.neLon,
      ),
      noFlyZones: [
        for (final z in (j['no_fly_zones'] is List ? j['no_fly_zones'] : []))
          if (z is Map) NoFlyZone.fromJson(z.cast<String, dynamic>()),
      ],
      backend: j['backend'] is String ? j['backend'] as String : f.backend,
      fromServer: true,
    );
  }
}

/// 비행 제한값 (docs/api.md 3.1 `limits`)
class Limits {
  const Limits({
    required this.maxRadiusCm,
    required this.minAltCm,
    required this.maxAltCm,
    required this.defaultAltCm,
    required this.minBatteryPct,
    required this.linkTimeoutS,
  });

  final double maxRadiusCm;
  final double minAltCm;
  final double maxAltCm;
  final double defaultAltCm;
  final double minBatteryPct;
  final double linkTimeoutS;
}

/// 지도 네 모서리 중 남서(왼쪽 아래)·북동(오른쪽 위)의 위도·경도
class GeoBounds {
  const GeoBounds({
    required this.swLat,
    required this.swLon,
    required this.neLat,
    required this.neLon,
  });

  final double swLat;
  final double swLon;
  final double neLat;
  final double neLon;
}

/// 비행 금지 구역 (현재 설정에는 없지만 명세에 있으므로 지원)
class NoFlyZone {
  const NoFlyZone({required this.name, required this.polygonCm});

  final String name;
  final List<Offset> polygonCm;

  factory NoFlyZone.fromJson(Map<String, dynamic> j) => NoFlyZone(
        name: j['name']?.toString() ?? '금지 구역',
        polygonCm: [
          for (final p in (j['polygon_cm'] is List ? j['polygon_cm'] : []))
            if (p is List && p.length >= 2 && p[0] is num && p[1] is num)
              Offset((p[0] as num).toDouble(), (p[1] as num).toDouble()),
        ],
      );
}

Map<String, dynamic> _obj(Object? v) =>
    v is Map ? v.cast<String, dynamic>() : const <String, dynamic>{};

double? _num(Object? v) => v is num ? v.toDouble() : null;
