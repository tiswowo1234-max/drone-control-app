import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';

import 'package:drone_control/config.dart';
import 'package:drone_control/geo.dart';
import 'package:drone_control/protocol.dart';
import 'package:drone_control/safety.dart';

/// docs/api.md 3.1 예시와 같은 config 메시지
const configExample = {
  'type': 'config',
  'api_version': '0.1',
  'map': {'width_cm': 250, 'height_cm': 250},
  'home': {'x_cm': 20, 'y_cm': 20},
  'limits': {
    'max_radius_cm': 150,
    'min_alt_cm': 50,
    'max_alt_cm': 150,
    'default_alt_cm': 80,
    'min_battery_pct': 20,
    'link_timeout_s': 3,
  },
  'no_fly_zones': [
    {
      'name': '예시 구역',
      'polygon_cm': [
        [100, 100],
        [130, 100],
        [130, 130],
        [100, 130],
      ],
    },
  ],
  'geo': {
    'sw': {'lat': 36.3680, 'lon': 127.3580},
    'ne': {'lat': 36.3740, 'lon': 127.3680},
  },
  'backend': 'sim',
};

void main() {
  group('메시지 파싱', () {
    test('config', () {
      final m = ServerMessage.parse(jsonEncode(configExample));
      expect(m, isA<ConfigMsg>());
      final c = (m as ConfigMsg).config;
      expect(c.mapWidthCm, 250);
      expect(c.homeXCm, 20);
      expect(c.limits.maxRadiusCm, 150);
      expect(c.limits.minBatteryPct, 20);
      expect(c.geo.neLon, 127.3680);
      expect(c.noFlyZones.single.polygonCm.length, 4);
      expect(c.backend, 'sim');
      expect(c.fromServer, isTrue);
    });

    test('config 항목이 빠지면 기본값 사용', () {
      final c = AppConfig.fromJson({'type': 'config', 'map': {'width_cm': 300}});
      expect(c.mapWidthCm, 300);
      expect(c.mapHeightCm, AppConfig.fallback.mapHeightCm);
      expect(c.limits.maxRadiusCm, AppConfig.fallback.limits.maxRadiusCm);
      expect(c.noFlyZones, isEmpty);
    });

    test('state (명세 예시)', () {
      final m = ServerMessage.parse(jsonEncode({
        'type': 'state',
        'ts': 1763000000.123,
        'connected': true,
        'flying': true,
        'mode': 'moving',
        'pos': {'x_cm': 120.5, 'y_cm': 88.0, 'z_cm': 80.0, 'yaw_deg': 0},
        'pos_source': 'estimate',
        'target': {'x_cm': 150, 'y_cm': 100},
        'battery_pct': 76,
      }));
      final s = (m as StateMsg).state;
      expect(s.connected, isTrue);
      expect(s.mode, 'moving');
      expect(s.pos!.xCm, 120.5);
      expect(s.pos!.zCm, 80.0);
      expect(s.posSource, 'estimate');
      expect(s.target, const Offset(150, 100));
      expect(s.batteryPct, 76);
    });

    test('state: pos·battery·target이 null', () {
      final s = (ServerMessage.parse(jsonEncode({
        'type': 'state',
        'connected': false,
        'flying': false,
        'mode': 'idle',
        'pos': null,
        'pos_source': 'estimate',
        'target': null,
        'battery_pct': null,
      })) as StateMsg)
          .state;
      expect(s.pos, isNull);
      expect(s.target, isNull);
      expect(s.batteryPct, isNull);
    });

    test('ack 오류 → 한국어', () {
      final a = (ServerMessage.parse(jsonEncode({
        'type': 'ack',
        'id': 'c-17',
        'ok': false,
        'error': 'TOO_FAR',
        'message': 'too far',
      })) as AckMsg)
          .ack;
      expect(a.id, 'c-17');
      expect(a.ok, isFalse);
      expect(a.koreanMessage, contains('너무 멉니다'));
      // 모르는 코드면 서버 문장을 쓴다
      expect(errorMessageKo('NEW_CODE', '새 오류'), '새 오류');
    });

    test('event', () {
      final e = (ServerMessage.parse(jsonEncode({
        'type': 'event',
        'level': 'warn',
        'code': 'LINK_LOST_LANDING',
        'message': '앱 연결이 끊겨 자동 착륙합니다',
      })) as EventMsg)
          .event;
      expect(e.level, 'warn');
      expect(e.code, 'LINK_LOST_LANDING');
    });

    test('잘못된 메시지는 무시', () {
      expect(ServerMessage.parse('not json'), isNull);
      expect(ServerMessage.parse('[1,2]'), isNull);
      expect(ServerMessage.parse('{"type":"???"}'), isNull);
    });

    test('cmd / ping 생성', () {
      expect(
        buildCmd('c-1', 'goto', {'x_cm': 10.0, 'y_cm': 20.0}),
        {'type': 'cmd', 'id': 'c-1', 'cmd': 'goto', 'x_cm': 10.0, 'y_cm': 20.0},
      );
      expect(buildCmd('c-2', 'emergency', {'confirm': true})['confirm'], isTrue);
      final p = buildPing(DateTime.fromMillisecondsSinceEpoch(1500));
      expect(p, {'type': 'ping', 'ts': 1.5});
    });
  });

  group('안전 사전 검사', () {
    final config = AppConfig.fromJson(configExample); // 반경 150, home (20,20)

    test('정상 위치', () {
      expect(checkTarget(config, const Offset(60, 60)), isNull);
    });

    test('지도 경계 밖', () {
      expect(checkTarget(config, const Offset(-1, 50))!.code, 'OUT_OF_BOUNDS');
      expect(checkTarget(config, const Offset(50, 251))!.code, 'OUT_OF_BOUNDS');
    });

    test('최대 반경 초과', () {
      // home(20,20)에서 (200,20)까지 180cm > 150cm
      expect(checkTarget(config, const Offset(200, 20))!.code, 'TOO_FAR');
    });

    test('금지 구역', () {
      expect(checkTarget(config, const Offset(110, 110))!.code, 'NO_FLY_ZONE');
      expect(pointInPolygon(const Offset(99, 110), config.noFlyZones.single.polygonCm),
          isFalse);
    });

    test('출동 가능 상태', () {
      bool ok(bool s, bool d, double? b) =>
          checkDispatchReady(
              config: config, serverOnline: s, droneConnected: d, batteryPct: b) ==
          null;
      expect(ok(true, true, 50), isTrue);
      expect(ok(true, true, null), isTrue); // 배터리를 모르면 서버가 판단
      expect(ok(false, true, 50), isFalse);
      expect(ok(true, false, 50), isFalse);
      expect(ok(true, true, 19), isFalse);
    });
  });

  group('위도·경도', () {
    final config = AppConfig.fromJson(configExample); // v0.1 형식 (sw/ne만)

    test('v0.1 형식(sw/ne만): 회전 없는 직사각형으로 보간', () {
      final sw = cmToLatLon(config, Offset.zero);
      expect(sw.lat, closeTo(36.3680, 1e-12));
      expect(sw.lon, closeTo(127.3580, 1e-12));
      final ne = cmToLatLon(config, const Offset(250, 250));
      expect(ne.lat, closeTo(36.3740, 1e-12));
      expect(ne.lon, closeTo(127.3680, 1e-12));
      final mid = cmToLatLon(config, const Offset(125, 125));
      expect(mid.lat, closeTo(36.3710, 1e-12));
      expect(mid.lon, closeTo(127.3630, 1e-12));
    });

    test('v0.2 네 모서리: 각 모서리와 중앙(네 점 평균)', () {
      final c = AppConfig.fromJson({
        'type': 'config',
        'map': {'width_cm': 250, 'height_cm': 250},
        'geo': {
          'sw': {'lat': 36.3638102, 'lon': 127.3607256},
          'se': {'lat': 36.3666003, 'lon': 127.3667053},
          'nw': {'lat': 36.3672948, 'lon': 127.3582399},
          'ne': {'lat': 36.3700849, 'lon': 127.3642196},
          'rotation_deg': 29.98,
        },
      });
      void at(double x, double y, double lat, double lon) {
        final p = cmToLatLon(c, Offset(x, y));
        expect(p.lat, closeTo(lat, 1e-9));
        expect(p.lon, closeTo(lon, 1e-9));
      }

      at(0, 0, 36.3638102, 127.3607256); // 왼쪽 아래 = sw
      at(250, 0, 36.3666003, 127.3667053); // 오른쪽 아래 = se
      at(0, 250, 36.3672948, 127.3582399); // 왼쪽 위 = nw
      at(250, 250, 36.3700849, 127.3642196); // 오른쪽 위 = ne
      at(125, 125, (36.3638102 + 36.3666003 + 36.3672948 + 36.3700849) / 4,
          (127.3607256 + 127.3667053 + 127.3582399 + 127.3642196) / 4);
    });

    test('기본값(최종 지도): 정문 (153.2, 70.2)cm → map-georef 산출 위경도', () {
      const c = AppConfig.fallback;
      expect(c.mapWidthCm, 250);
      expect(c.mapHeightCm, 250);
      expect(c.home, const Offset(153.2, 70.2));
      final p = cmToLatLon(c, c.home);
      // 네 모서리 쌍선형 보간과 map-georef의 직접 변환 값 차이는 경도 약 1.3e-6°(≈0.12 m)
      expect(p.lat, closeTo(36.3658066, 2e-6));
      expect(p.lon, closeTo(127.3637787, 2e-6));
    });

    test('geo가 없거나 null이면 기본값(settings.toml과 같은 네 모서리)', () {
      final c = AppConfig.fromJson({'type': 'config', 'geo': null});
      expect(c.geo.seLat, AppConfig.fallback.geo.seLat);
      final p = cmToLatLon(c, const Offset(250, 0));
      expect(p.lat, closeTo(36.3640364, 1e-9)); // settings.toml [geo] se
      expect(p.lon, closeTo(127.3669092, 1e-9));
    });

    test('표시 형식', () {
      expect(const LatLon(36.1234567, 127.7654321).format(), '36.12346, 127.76543');
    });
  });
}
