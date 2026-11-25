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
    this.visionModes = const [],
    this.photoSlots = const [],
    this.car,
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

  /// 앱에서 고를 수 있는 위치 추적 카메라 모드 (docs/api.md v0.7 3.1·11장).
  /// 예: `["esp32", "tt_down"]`. 비어 있으면(이전 서버) 전환 버튼을 숨긴다.
  final List<String> visionModes;

  /// 사건 사진 슬롯 S1~S4 (docs/api.md v0.7 3.1·9장)
  final List<PhotoSlot> photoSlots;

  /// 차(UGV) 제어 설정 (v0.8 13장). null이면 서버에 차 기능이 없음 → 차 버튼을 숨긴다.
  final CarConfig? car;

  Offset get home => Offset(homeXCm, homeYCm);

  /// 서버 연결 전 기본값 (config/settings.toml과 동일하게 유지)
  static const fallback = AppConfig(
    mapWidthCm: 250,
    mapHeightCm: 250,
    homeXCm: 153.2,
    homeYCm: 70.2,
    limits: Limits(
      maxRadiusCm: 250,
      minAltCm: 30,
      maxAltCm: 150,
      defaultAltCm: 50,
      minBatteryPct: 20,
      linkTimeoutS: 3,
    ),
    geo: GeoBounds(
      swLat: 36.3639270,
      swLon: 127.3589258,
      seLat: 36.3640364,
      seLon: 127.3669092,
      nwLat: 36.3703837,
      nwLon: 127.3587905,
      neLat: 36.3704931,
      neLon: 127.3667739,
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
    final hasGeo = geo['sw'] is Map && geo['ne'] is Map;
    final sw = _obj(geo['sw']);
    final ne = _obj(geo['ne']);
    final swLat = _num(sw['lat']), swLon = _num(sw['lon']);
    final neLat = _num(ne['lat']), neLon = _num(ne['lon']);
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
      geo: (hasGeo &&
              swLat != null &&
              swLon != null &&
              neLat != null &&
              neLon != null)
          ? GeoBounds(
              swLat: swLat,
              swLon: swLon,
              // se/nw가 없으면(명세 v0.1) 회전 없는 직사각형으로 본다
              seLat: _num(_obj(geo['se'])['lat']) ?? swLat,
              seLon: _num(_obj(geo['se'])['lon']) ?? neLon,
              nwLat: _num(_obj(geo['nw'])['lat']) ?? neLat,
              nwLon: _num(_obj(geo['nw'])['lon']) ?? swLon,
              neLat: neLat,
              neLon: neLon,
            )
          : f.geo,
      noFlyZones: [
        for (final z in (j['no_fly_zones'] is List ? j['no_fly_zones'] : []))
          if (z is Map) NoFlyZone.fromJson(z.cast<String, dynamic>()),
      ],
      backend: j['backend'] is String ? j['backend'] as String : f.backend,
      fromServer: true,
      visionModes: [
        for (final m in (j['vision_modes'] is List ? j['vision_modes'] : []))
          if (m is String) m,
      ],
      photoSlots: [
        for (final p in (j['photo_slots'] is List ? j['photo_slots'] : []))
          if (p is Map) PhotoSlot.fromJson(p.cast<String, dynamic>()),
      ].whereType<PhotoSlot>().toList(),
      car: CarConfig.fromJson(j['car']),
    );
  }
}

/// `config.car` (docs/api.md v0.8 13.1)
class CarConfig {
  const CarConfig({
    this.link = 'ble',
    this.maxSpeedMmS = 100,
    this.maxOmegaRadS = 1.5,
    this.routeSpeedMmS = 60,
    this.maxPoints = 15,
    this.mapMarginCm = 5,
  });

  /// `ble` = 실제 차 / `mock` = 가짜 차
  final String link;
  final double maxSpeedMmS;
  final double maxOmegaRadS;
  final double routeSpeedMmS;

  /// 한 번에 보낼 수 있는 경로점 수 (출발 위치 제외)
  final int maxPoints;

  /// 경로점은 지도 가장자리에서 이만큼 안쪽 (cm)
  final double mapMarginCm;

  static CarConfig? fromJson(Object? v) {
    if (v is! Map) return null;
    const d = CarConfig();
    return CarConfig(
      link: v['link'] is String ? v['link'] as String : d.link,
      maxSpeedMmS: _num(v['max_speed_mm_s']) ?? d.maxSpeedMmS,
      maxOmegaRadS: _num(v['max_omega_rad_s']) ?? d.maxOmegaRadS,
      routeSpeedMmS: _num(v['route_speed_mm_s']) ?? d.routeSpeedMmS,
      maxPoints: v['max_points'] is num ? (v['max_points'] as num).toInt() : d.maxPoints,
      mapMarginCm: _num(v['map_margin_cm']) ?? d.mapMarginCm,
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

/// 지도 이미지 네 모서리의 위도·경도 (docs/api.md v0.2 `geo`).
/// sw/se/nw/ne는 **이미지 기준** 왼쪽아래/오른쪽아래/왼쪽위/오른쪽위이며 실제 방위가 아니다
/// (지도가 약 30° 회전돼 있음).
class GeoBounds {
  const GeoBounds({
    required this.swLat,
    required this.swLon,
    required this.seLat,
    required this.seLon,
    required this.nwLat,
    required this.nwLon,
    required this.neLat,
    required this.neLon,
  });

  final double swLat, swLon;
  final double seLat, seLon;
  final double nwLat, nwLon;
  final double neLat, neLon;
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

/// 사건 사진 슬롯 하나 (docs/api.md v0.7 3.1 `photo_slots`)
class PhotoSlot {
  const PhotoSlot({
    required this.slot,
    required this.name,
    required this.center,
    this.corners = const [],
  });

  /// 슬롯 번호 1~4
  final int slot;

  /// 사람이 읽는 이름 (예: "S1 교육지원동 (W8)")
  final String name;

  /// 슬롯 중심 (cm). 누르면 여기에 출동 목표를 놓는다.
  final Offset center;

  /// 네 모서리 (cm). 지도에 사각형으로 그린다.
  final List<Offset> corners;

  /// 짧은 표시 이름 (예: "S1")
  String get label => 'S$slot';

  /// 형식이 잘못되면(번호·중심 없음) null
  static PhotoSlot? fromJson(Map<String, dynamic> j) {
    final n = j['slot'];
    final c = _obj(j['center_cm']);
    final cx = _num(c['x_cm']), cy = _num(c['y_cm']);
    if (n is! num || cx == null || cy == null) return null;
    return PhotoSlot(
      slot: n.toInt(),
      name: j['name']?.toString() ?? 'S${n.toInt()}',
      center: Offset(cx, cy),
      corners: [
        for (final p in (j['corners_cm'] is List ? j['corners_cm'] : []))
          if (p is List && p.length >= 2 && p[0] is num && p[1] is num)
            Offset((p[0] as num).toDouble(), (p[1] as num).toDouble()),
      ],
    );
  }
}

Map<String, dynamic> _obj(Object? v) =>
    v is Map ? v.cast<String, dynamic>() : const <String, dynamic>{};

double? _num(Object? v) => v is num ? v.toDouble() : null;
