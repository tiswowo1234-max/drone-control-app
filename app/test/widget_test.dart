import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:drone_control/config.dart';
import 'package:drone_control/drone_client.dart';
import 'package:drone_control/main.dart';
import 'package:drone_control/map_coords.dart';

/// 서버 대신 쓰는 가짜 연결. 앱이 보낸 메시지는 [sent]에 쌓인다.
class FakeLink implements ServerLink {
  final ctrl = StreamController<dynamic>(sync: true); // 넣자마자 앱에 전달
  final sent = <Map<String, dynamic>>[];
  bool closed = false;

  @override
  Stream<dynamic> get stream => ctrl.stream;

  @override
  void send(String text) => sent.add(jsonDecode(text) as Map<String, dynamic>);

  @override
  Future<void> close() async => closed = true;

  void push(Map<String, dynamic> m) => ctrl.add(jsonEncode(m));

  List<Map<String, dynamic>> get cmds =>
      sent.where((m) => m['type'] == 'cmd').toList();
}

Map<String, dynamic> configMsg({double maxRadius = 150}) => {
      'type': 'config',
      'api_version': '0.1',
      'map': {'width_cm': 250, 'height_cm': 250},
      'home': {'x_cm': 20, 'y_cm': 20},
      'limits': {
        'max_radius_cm': maxRadius,
        'min_alt_cm': 50,
        'max_alt_cm': 150,
        'default_alt_cm': 80,
        'min_battery_pct': 20,
        'link_timeout_s': 3,
      },
      'no_fly_zones': [],
      'geo': {
        'sw': {'lat': 36.3638102, 'lon': 127.3607256},
        'se': {'lat': 36.3666003, 'lon': 127.3667053},
        'nw': {'lat': 36.3672948, 'lon': 127.3582399},
        'ne': {'lat': 36.3700849, 'lon': 127.3642196},
        'rotation_deg': 29.98,
      },
      'backend': 'sim',
    };

Map<String, dynamic> stateMsg({
  bool connected = true,
  String mode = 'hovering',
  Map<String, dynamic>? pos = const {'x_cm': 20, 'y_cm': 20, 'z_cm': 80, 'yaw_deg': 90},
  num? battery = 76,
  String posSource = 'estimate',
}) =>
    {
      'type': 'state',
      'ts': 1763000000.0,
      'connected': connected,
      'flying': mode != 'idle',
      'mode': mode,
      'pos': pos,
      'pos_source': posSource,
      'target': null,
      'battery_pct': battery,
    };

void main() {
  group('MapCoords', () {
    const config = AppConfig.fallback; // 250 x 250 cm
    const coords = MapCoords(config: config, viewSize: Size(500, 500));

    test('화면 왼쪽 아래 = 지도 (0,0)', () {
      final cm = coords.pxToCm(const Offset(0, 500));
      expect(cm.dx, closeTo(0, 1e-9));
      expect(cm.dy, closeTo(0, 1e-9));
    });

    test('화면 오른쪽 위 = 지도 (250,250)', () {
      final cm = coords.pxToCm(const Offset(500, 0));
      expect(cm.dx, closeTo(250, 1e-9));
      expect(cm.dy, closeTo(250, 1e-9));
    });

    test('cm → px → cm 왕복 변환이 일치', () {
      const p = Offset(123.4, 56.7);
      final back = coords.pxToCm(coords.cmToPx(p));
      expect(back.dx, closeTo(p.dx, 1e-9));
      expect(back.dy, closeTo(p.dy, 1e-9));
    });

    test('지도 밖 판정', () {
      expect(coords.isInside(const Offset(10, 10)), isTrue);
      expect(coords.isInside(const Offset(-1, 10)), isFalse);
      expect(coords.isInside(const Offset(10, 251)), isFalse);
    });
  });

  group('화면', () {
    late FakeLink link;
    late DroneClient client;

    /// 폰 크기 화면에 앱을 띄우고 가짜 서버에 연결한다.
    Future<void> start(WidgetTester tester, {Map<String, dynamic>? config}) async {
      tester.view.physicalSize = const Size(1080, 2340);
      tester.view.devicePixelRatio = 2.75; // 약 393 x 851 (일반적인 폰)
      addTearDown(tester.view.reset);
      link = FakeLink();
      client = DroneClient(connector: (_) async => link)
        ..connect(Uri.parse('ws://test:8000/ws'));
      await tester.pumpWidget(DroneControlApp(client: client));
      await tester.pump();
      link.push(config ?? configMsg());
      link.push(stateMsg());
      await tester.pump();
    }

    /// 테스트 끝: 화면을 내리고 타이머를 정리한다.
    Future<void> finish(WidgetTester tester) async {
      await tester.pumpWidget(const SizedBox());
      client.dispose();
    }

    /// 마지막 명령에 응답한다.
    Future<void> ack(WidgetTester tester, {bool ok = true, String? error}) async {
      link.push({'type': 'ack', 'id': link.cmds.last['id'], 'ok': ok, 'error': error});
      await tester.pump();
      await tester.pump();
    }

    testWidgets('서버에 못 붙으면 경고 배너를 크게 보여 준다', (tester) async {
      client = DroneClient(connector: (_) async => throw Exception('refused'))
        ..connect(Uri.parse('ws://test:8000/ws'));
      await tester.pumpWidget(DroneControlApp(client: client));
      await tester.pump();
      expect(find.byKey(const Key('offline-banner')), findsOneWidget);
      expect(find.text('서버 연결 끊김 — 자동 재연결 중'), findsOneWidget);
      await tester.pump(const Duration(seconds: 2)); // 재연결 시도
      expect(client.status, isNot(LinkStatus.online));
      await finish(tester);
    });

    testWidgets('상태 패널: config·state 표시', (tester) async {
      await start(tester);
      expect(find.byKey(const Key('offline-banner')), findsNothing);
      expect(find.textContaining('x 20, y 20 cm', findRichText: true), findsOneWidget);
      expect(find.textContaining('호버링', findRichText: true), findsOneWidget);
      expect(find.textContaining('76%', findRichText: true), findsOneWidget);
      expect(find.textContaining('80 cm', findRichText: true), findsOneWidget);
      expect(find.textContaining('추정값', findRichText: true), findsOneWidget);
      expect(find.textContaining('시뮬레이터', findRichText: true), findsOneWidget);
      expect(find.byTooltip('드론 (90°)'), findsOneWidget);
      expect(client.config.limits.maxRadiusCm, 150); // 서버 값으로 덮어씀
      await finish(tester);
    });

    testWidgets('위치가 null이면 "위치 모름", 드론 미연결이면 경고', (tester) async {
      await start(tester);
      link.push(stateMsg(pos: null, connected: false, mode: 'idle'));
      await tester.pump();
      expect(find.textContaining('위치 모름', findRichText: true), findsOneWidget);
      expect(find.byKey(const Key('drone-offline-banner')), findsOneWidget);
      expect(find.byTooltip('드론 (90°)'), findsNothing);
      await finish(tester);
    });

    testWidgets('위치 출처: 카메라(초록) ↔ 추정값(주황) 전환이 보인다', (tester) async {
      await start(tester);
      Color? colorOf(String text) {
        for (final r in tester.widgetList<RichText>(find.byType(RichText))) {
          Color? found;
          r.text.visitChildren((span) {
            if (span is TextSpan && span.text == text) found = span.style?.color;
            return found == null;
          });
          if (found != null) return found;
        }
        return null;
      }

      link.push(stateMsg(posSource: 'vision'));
      await tester.pump();
      expect(colorOf('카메라'), Colors.green.shade800);
      expect(colorOf('추정값'), isNull);

      link.push(stateMsg(posSource: 'estimate')); // 카메라 끊김 → 서버가 추정값으로 전환
      await tester.pump();
      expect(colorOf('추정값'), Colors.orange.shade900);
      expect(colorOf('카메라'), isNull);
      await finish(tester);
    });

    testWidgets('1초마다 ping을 보낸다', (tester) async {
      await start(tester);
      await tester.pump(const Duration(milliseconds: 1100));
      link.push(stateMsg());
      await tester.pump(const Duration(milliseconds: 1000));
      expect(link.sent.where((m) => m['type'] == 'ping').length, 2);
      await finish(tester);
    });

    testWidgets('출동 → 드래그 → 확인 → goto 전송', (tester) async {
      await start(tester);
      await tester.tap(find.byKey(const Key('btn-dispatch')));
      await tester.pump();
      expect(find.text('목표: x 125.0 cm, y 125.0 cm'), findsOneWidget);
      expect(find.textContaining('위도·경도(참고): 36.36695, 127.36247'), findsOneWidget);

      // 마커를 왼쪽 아래로 끈다
      await tester.drag(find.byKey(const Key('target-marker')), const Offset(-60, 30));
      await tester.pump();
      final text = tester
          .widget<Text>(find.byKey(const Key('marker-coords')))
          .data!;
      final m = RegExp(r'x ([\d.]+) cm, y ([\d.]+) cm').firstMatch(text)!;
      final x = double.parse(m[1]!), y = double.parse(m[2]!);
      // 393px 폭 화면 → 1cm = 393/250 px. 끈 거리만큼 정확히 움직여야 한다.
      const pxPerCm = 393 / 250;
      expect(x, closeTo(125 - 60 / pxPerCm, 0.5));
      expect(y, closeTo(125 - 30 / pxPerCm, 0.5));

      await tester.tap(find.byKey(const Key('btn-confirm')));
      await tester.pump();
      final cmd = link.cmds.last;
      expect(cmd['cmd'], 'goto');
      expect(cmd['x_cm'], closeTo(x, 0.05));
      expect(cmd['y_cm'], closeTo(y, 0.05));

      await ack(tester);
      expect(find.byKey(const Key('dispatch-panel')), findsNothing);
      expect(find.text('출동 명령을 보냈습니다'), findsOneWidget);
      await finish(tester);
    });

    testWidgets('반경 밖이면 확인 버튼이 막힌다', (tester) async {
      await start(tester, config: configMsg(maxRadius: 50));
      await tester.tap(find.byKey(const Key('btn-dispatch')));
      await tester.pump();
      expect(find.byKey(const Key('dispatch-problem')), findsOneWidget);
      final btn = tester.widget<ButtonStyleButton>(find.byKey(const Key('btn-confirm')));
      expect(btn.onPressed, isNull);

      await tester.tap(find.byKey(const Key('btn-cancel')));
      await tester.pump();
      expect(find.byKey(const Key('dispatch-panel')), findsNothing);
      expect(link.cmds, isEmpty);
      await finish(tester);
    });

    testWidgets('배터리가 부족하면 출동 확인이 막힌다', (tester) async {
      await start(tester);
      link.push(stateMsg(battery: 10));
      await tester.pump();
      await tester.tap(find.byKey(const Key('btn-dispatch')));
      await tester.pump();
      expect(find.textContaining('배터리 부족'), findsOneWidget);
      final btn = tester.widget<ButtonStyleButton>(find.byKey(const Key('btn-confirm')));
      expect(btn.onPressed, isNull);
      await finish(tester);
    });

    testWidgets('ack 오류를 한국어로 보여 준다', (tester) async {
      await start(tester);
      await tester.tap(find.byKey(const Key('btn-dispatch')));
      await tester.pump();
      await tester.tap(find.byKey(const Key('btn-confirm')));
      await tester.pump();
      await ack(tester, ok: false, error: 'LOW_BATTERY');
      expect(find.text('출동 실패: 배터리가 부족해 출동할 수 없습니다'), findsOneWidget);
      expect(find.byKey(const Key('dispatch-panel')), findsOneWidget); // 마커 유지
      await finish(tester);
    });

    testWidgets('정지·착륙·귀환 버튼', (tester) async {
      await start(tester);
      for (final (key, cmd) in [
        ('btn-stop', 'stop'),
        ('btn-land', 'land'),
        ('btn-home', 'return_home'),
      ]) {
        await tester.tap(find.byKey(Key(key)));
        await tester.pump();
        expect(link.cmds.last['cmd'], cmd);
        await ack(tester);
      }
      await finish(tester);
    });

    testWidgets('비상 정지는 확인 후에만 confirm:true로 보낸다', (tester) async {
      await start(tester);
      await tester.tap(find.byKey(const Key('btn-emergency')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('취소').last);
      await tester.pumpAndSettle();
      expect(link.cmds, isEmpty);

      await tester.tap(find.byKey(const Key('btn-emergency')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('btn-emergency-confirm')));
      await tester.pumpAndSettle();
      expect(link.cmds.single['cmd'], 'emergency');
      expect(link.cmds.single['confirm'], isTrue);
      await ack(tester);
      await finish(tester);
    });

    testWidgets('서버 연결이 끊기면 경고 배너', (tester) async {
      await start(tester);
      unawaited(link.ctrl.close());
      await tester.pump();
      expect(find.byKey(const Key('offline-banner')), findsOneWidget);
      expect(find.text('(마지막으로 받은 값)'), findsOneWidget);
      await finish(tester);
    });

    testWidgets('서버 event를 알림으로 보여 준다', (tester) async {
      await start(tester);
      link.push({
        'type': 'event',
        'level': 'warn',
        'code': 'LINK_LOST_LANDING',
        'message': '앱 연결이 끊겨 자동 착륙합니다',
      });
      await tester.pump();
      await tester.pump();
      expect(find.text('앱 연결이 끊겨 자동 착륙합니다'), findsOneWidget);
      await finish(tester);
    });
  });
}
