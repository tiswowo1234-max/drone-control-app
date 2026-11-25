import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:drone_control/config.dart';
import 'package:drone_control/drone_client.dart';
import 'package:drone_control/main.dart';
import 'package:drone_control/protocol.dart';

import 'widget_test.dart' show FakeLink, configMsg, stateMsg;

/// docs/api.md v0.8 13.1 config.car
const carConfig = {
  'link': 'mock',
  'max_speed_mm_s': 100,
  'max_omega_rad_s': 1.5,
  'route_speed_mm_s': 60,
  'max_points': 15,
  'map_margin_cm': 5,
};

Map<String, dynamic> carState({
  bool connected = true,
  String? state = 'READY',
  Map<String, dynamic>? pos = const {'x_cm': 120, 'y_cm': 70, 'yaw_deg': 0},
  String? posSource = 'vision',
  num? seenAge = 0.2,
  Map<String, dynamic>? route,
}) =>
    {
      'connected': connected,
      'link_message': connected ? '가짜 차(mock) 연결됨' : '차 찾는 중',
      'state': state,
      'pos': pos,
      'pos_source': posSource,
      'seen_age_s': seenAge,
      'route': route,
      'driving': false,
    };

void main() {
  group('차 메시지 (v0.8)', () {
    test('config.car가 없으면 null (이전 서버)', () {
      expect(AppConfig.fromJson(configMsg()).car, isNull);
    });

    test('config.car 읽기', () {
      final c = AppConfig.fromJson({...configMsg(), 'car': carConfig}).car!;
      expect(c.link, 'mock');
      expect(c.maxPoints, 15);
      expect(c.routeSpeedMmS, 60);
    });

    test('state.car 읽기: 위치·출처·경로', () {
      final st = DroneState.fromJson({
        ...stateMsg(),
        'car': carState(posSource: 'odom', seenAge: 12.3, route: {
          'points_cm': [
            [120, 70],
            [150, 70],
            [150, 110],
          ],
          'wp': 1,
          'n': 3,
        }),
      });
      final car = st.car!;
      expect(car.connected, isTrue);
      expect(car.pos!.xy, const Offset(120, 70));
      expect(car.posSource, 'odom');
      expect(car.seenAgeS, 12.3);
      expect(car.routePoints.length, 3);
      expect(car.routeWp, 1);
      expect(car.hasRoute, isTrue);
      expect(car.blocked, isFalse);
      expect(carPosSourceKo('odom'), '엔코더로 이어감');
    });

    test('비상정지 상태면 blocked', () {
      final car = CarStatus.fromJson(carState(state: 'ESTOP'))!;
      expect(car.blocked, isTrue);
      expect(carStateKo('ESTOP'), '비상정지');
    });

    test('조이스틱 → 속도: 위 = 전진, 왼쪽 = 좌회전(+ω), 가운데는 0', () {
      expect(joystickToDrive(const Offset(0, -1), 80, 1.5), (80, 0.0));
      expect(joystickToDrive(const Offset(0, 1), 80, 1.5), (-80, 0.0));
      expect(joystickToDrive(const Offset(-1, 0), 80, 1.5), (0, 1.5));
      expect(joystickToDrive(const Offset(0.5, 0), 80, 1.5), (0, -0.75));
      expect(joystickToDrive(const Offset(0.05, -0.05), 80, 1.5), (0, 0.0));
    });
  });

  group('차 조종 화면', () {
    late FakeLink link;
    late DroneClient client;

    Future<void> start(WidgetTester tester, {Map<String, dynamic>? car}) async {
      tester.view.physicalSize = const Size(1080, 2340);
      tester.view.devicePixelRatio = 2.75;
      addTearDown(tester.view.reset);
      link = FakeLink();
      client = DroneClient(connector: (_) async => link)..connect(Uri.parse('ws://test:8000/ws'));
      await tester.pumpWidget(DroneControlApp(client: client));
      await tester.pump();
      link.push({...configMsg(), 'car': carConfig});
      link.push({...stateMsg(), 'car': car ?? carState()});
      await tester.pump();
    }

    Future<void> finish(WidgetTester tester) async {
      await tester.pumpWidget(const SizedBox());
      client.dispose();
    }

    testWidgets('서버에 차 기능이 없으면 차 버튼이 없다', (tester) async {
      tester.view.physicalSize = const Size(1080, 2340);
      tester.view.devicePixelRatio = 2.75;
      addTearDown(tester.view.reset);
      link = FakeLink();
      client = DroneClient(connector: (_) async => link)..connect(Uri.parse('ws://test:8000/ws'));
      await tester.pumpWidget(DroneControlApp(client: client));
      await tester.pump();
      link.push(configMsg());
      link.push(stateMsg());
      await tester.pump();
      expect(find.byKey(const Key('btn-car-mode')), findsNothing);
      await finish(tester);
    });

    testWidgets('차 모드: 지도에 차 아이콘, 지도를 눌러 경로점 → 경로 출발 = car_route', (tester) async {
      await start(tester);
      await tester.tap(find.byKey(const Key('btn-car-mode')));
      await tester.pump();
      expect(find.byKey(const Key('car-panel')), findsOneWidget);
      expect(find.byKey(const Key('btn-dispatch')), findsNothing); // 드론 출동 버튼 대신 차 패널
      expect(find.byKey(const Key('btn-stop')), findsOneWidget); // 드론 정지·착륙은 그대로
      expect(find.byKey(const Key('car-icon')), findsOneWidget);
      expect(find.textContaining('드론 인식 중'), findsOneWidget);
      expect(find.byKey(const Key('btn-car-send')), findsOneWidget);

      // 지도 가운데를 두 번 누름 → 경로점 2개
      final map = find.byType(InteractiveViewer);
      await tester.tapAt(tester.getCenter(map));
      await tester.pump();
      await tester.tapAt(tester.getCenter(map) + const Offset(40, -40));
      await tester.pump();
      expect(find.byKey(const Key('car-draft-0')), findsOneWidget);
      expect(find.byKey(const Key('car-draft-1')), findsOneWidget);

      await tester.tap(find.byKey(const Key('btn-car-undo')));
      await tester.pump();
      expect(find.byKey(const Key('car-draft-1')), findsNothing);

      await tester.tap(find.byKey(const Key('btn-car-send')));
      await tester.pump();
      final cmd = link.cmds.last;
      expect(cmd['cmd'], 'car_route');
      expect((cmd['points_cm'] as List).length, 1);
      expect(cmd['speed_mm_s'], 60);
      link.push({'type': 'ack', 'id': cmd['id'], 'ok': true});
      await tester.pump();
      await tester.pump();
      expect(find.byKey(const Key('car-draft-0')), findsNothing); // 보낸 뒤 비움
      expect(find.text('차 경로 출발 명령을 보냈습니다'), findsOneWidget);
      await finish(tester);
    });

    testWidgets('드론이 차를 못 봤으면 경로 출발이 막힌다', (tester) async {
      await start(tester, car: carState(pos: null, posSource: null, seenAge: null));
      await tester.tap(find.byKey(const Key('btn-car-mode')));
      await tester.pump();
      await tester.tapAt(tester.getCenter(find.byType(InteractiveViewer)));
      await tester.pump();
      expect(find.byKey(const Key('car-route-problem')), findsOneWidget);
      final btn = tester.widget<FilledButton>(find.byKey(const Key('btn-car-send')));
      expect(btn.onPressed, isNull);
      await finish(tester);
    });

    testWidgets('조이스틱: 누르고 있으면 car_drive 반복, 떼면 car_stop', (tester) async {
      await start(tester);
      await tester.tap(find.byKey(const Key('btn-car-mode')));
      await tester.pump();
      final joy = find.byKey(const Key('car-joystick'));
      final g = await tester.startGesture(tester.getCenter(joy) + const Offset(0, -40)); // 위 = 전진
      await tester.pump(const Duration(milliseconds: 400));
      final drives = link.sent.where((m) => m['type'] == 'car_drive').toList();
      expect(drives.length, greaterThanOrEqualTo(3));
      expect(drives.last['v_mm_s'], greaterThan(0));
      expect(drives.last['omega_rad_s'], 0.0);
      await g.up();
      await tester.pump();
      expect(link.cmds.last['cmd'], 'car_stop');
      final n = link.sent.where((m) => m['type'] == 'car_drive').length;
      await tester.pump(const Duration(milliseconds: 400));
      expect(link.sent.where((m) => m['type'] == 'car_drive').length, n); // 뗀 뒤로는 안 보냄
      await finish(tester);
    });

    testWidgets('차 비상정지 → 해제 버튼으로 바뀐다', (tester) async {
      await start(tester);
      await tester.tap(find.byKey(const Key('btn-car-mode')));
      await tester.pump();
      await tester.tap(find.byKey(const Key('btn-car-estop')));
      await tester.pump();
      expect(link.cmds.last['cmd'], 'car_estop');
      link.push({...stateMsg(), 'car': carState(state: 'ESTOP')});
      await tester.pump();
      expect(find.byKey(const Key('btn-car-clear-estop')), findsOneWidget);
      await tester.tap(find.byKey(const Key('btn-car-clear-estop')));
      await tester.pump();
      expect(link.cmds.last['cmd'], 'car_clear_estop');
      await finish(tester);
    });
    testWidgets('드론 따라가기: 호버링 중 누르면 follow_start, 따라가는 중엔 멈춤 = stop', (tester) async {
      await start(tester); // 드론 호버링 중 (stateMsg 기본)
      await tester.tap(find.byKey(const Key('btn-car-mode')));
      await tester.pump();
      await tester.tap(find.byKey(const Key('btn-follow-start')));
      await tester.pump();
      expect(link.cmds.last['cmd'], 'follow_start');
      link.push({...stateMsg(mode: 'following'), 'car': carState()});
      await tester.pump();
      expect(find.byKey(const Key('btn-follow-start')), findsNothing);
      expect(find.text('차 따라가는 중'), findsWidgets);
      await tester.tap(find.byKey(const Key('btn-follow-stop')));
      await tester.pump();
      expect(link.cmds.last['cmd'], 'stop');
      await finish(tester);
    });

    testWidgets('드론이 착지 상태면 따라가기 버튼이 꺼져 있다', (tester) async {
      await start(tester);
      link.push({...stateMsg(mode: 'idle'), 'car': carState()});
      await tester.pump();
      await tester.tap(find.byKey(const Key('btn-car-mode')));
      await tester.pump();
      final btn = tester.widget<FilledButton>(find.byKey(const Key('btn-follow-start')));
      expect(btn.onPressed, isNull);
      expect(find.textContaining('이륙시켜'), findsOneWidget);
      await finish(tester);
    });
  });
}
