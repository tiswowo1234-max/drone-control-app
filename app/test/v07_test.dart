// docs/api.md v0.7 앱 기능 시험: 위치 추적 모드 전환, 사진 슬롯, 사건 알림, 실시간 영상
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:drone_control/config.dart';
import 'package:drone_control/drone_client.dart';
import 'package:drone_control/main.dart';
import 'package:drone_control/protocol.dart';
import 'package:drone_control/server_address.dart';

import 'widget_test.dart' show FakeLink, configMsg, stateMsg;

const slotsJson = [
  {
    'slot': 1,
    'name': 'S1 교육지원동 (W8)',
    'center_cm': {'x_cm': 30.9, 'y_cm': 228.6},
    'corners_cm': [
      [21.9, 228.8],
      [34.5, 236.9],
      [39.9, 228.5],
      [27.3, 220.4],
    ],
  },
  {
    'slot': 4,
    'name': 'S4',
    'center_cm': {'x_cm': 46.8, 'y_cm': 89.1},
    'corners_cm': [
      [47.0, 98.1],
      [55.0, 85.4],
      [46.5, 80.1],
      [38.5, 92.8],
    ],
  },
];

Map<String, dynamic> configV07() => {
      ...configMsg(maxRadius: 250),
      'api_version': '0.7',
      'vision_modes': ['esp32', 'tt_down'],
      'photo_slots': slotsJson,
    };

Map<String, dynamic> visionJson({
  String mode = 'tt_down',
  bool bridge = true,
  String? bridgeMode = 'tt_down',
  bool? cameraOk = true,
  bool videoLive = true,
}) =>
    {
      'mode': mode,
      'bridge_connected': bridge,
      'bridge_mode': bridgeMode,
      'camera_ok': cameraOk,
      'video_live': videoLive,
    };

Map<String, dynamic> stateV07({
  String mode = 'hovering',
  String posSource = 'vision',
  Map<String, dynamic>? vision,
}) =>
    {
      ...stateMsg(mode: mode, posSource: posSource),
      'vision': vision ?? visionJson(),
    };

/// docs/api.md 3.5 예시
Map<String, dynamic> incidentJson({String id = 'inc-3', double ts = 1763000000.4, int? slot = 4}) => {
      'id': id,
      'ts': ts,
      'slot': slot,
      'place': 'slot S4, unnamed building',
      'pos': {'x_cm': 46.6, 'y_cm': 89.0},
      'geo': {'lat': 36.3662462, 'lon': 127.3603673},
      'drone_pos': {'x_cm': 44.1, 'y_cm': 83.1, 'z_cm': 79.0},
      'photo_url': '/incidents/$id/photo.jpg',
      'mode': 'tt_down',
      'method': 'slot',
      'detection': null,
    };

void main() {
  group('v0.7 메시지 파싱', () {
    test('config: vision_modes, photo_slots', () {
      final c = (ServerMessage.parse(jsonEncode(configV07())) as ConfigMsg).config;
      expect(c.visionModes, ['esp32', 'tt_down']);
      expect(c.photoSlots.length, 2);
      final s4 = c.photoSlots[1];
      expect(s4.slot, 4);
      expect(s4.label, 'S4');
      expect(s4.center, const Offset(46.8, 89.1));
      expect(s4.corners.length, 4);
      expect(c.photoSlots[0].name, 'S1 교육지원동 (W8)');
    });

    test('config: 새 필드가 없거나 잘못되면 빈 목록', () {
      final c = AppConfig.fromJson({
        'type': 'config',
        'vision_modes': 'esp32',
        'photo_slots': [
          {'slot': 1}, // 중심 없음 → 버림
          'x',
        ],
      });
      expect(c.visionModes, isEmpty);
      expect(c.photoSlots, isEmpty);
      expect(AppConfig.fallback.visionModes, isEmpty);
    });

    test('state.vision', () {
      final st = (ServerMessage.parse(jsonEncode(stateV07())) as StateMsg).state;
      final v = st.vision!;
      expect(v.mode, 'tt_down');
      expect(v.bridgeConnected, isTrue);
      expect(v.cameraOk, isTrue);
      expect(v.videoLive, isTrue);
      expect(v.switching, isFalse);

      final sw = VisionStatus.fromJson(visionJson(mode: 'esp32', bridgeMode: 'tt_down'))!;
      expect(sw.switching, isTrue);
      // 연결 프로그램이 모드를 아직 안 알렸으면(null) "전환 중"이 아니다
      expect(VisionStatus.fromJson(visionJson(bridgeMode: null))!.switching, isFalse);
      // 이전 서버(vision 없음)
      expect(DroneState.fromJson(stateMsg()).vision, isNull);
    });

    test('바꿀 수 있는 드론 상태: 착지·호버링만', () {
      for (final (mode, ok) in [
        ('idle', true),
        ('hovering', true),
        ('moving', false),
        ('taking_off', false),
        ('landing', false),
        ('returning', false),
      ]) {
        expect(DroneState.fromJson(stateMsg(mode: mode)).canSwitchVision, ok, reason: mode);
      }
    });

    test('incident (3.5 예시)', () {
      final m = ServerMessage.parse(jsonEncode({'type': 'incident', 'incident': incidentJson()}));
      final i = (m as IncidentMsg).incident;
      expect(i.id, 'inc-3');
      expect(i.slot, 4);
      expect(i.pos, const Offset(46.6, 89.0));
      expect(i.lat, 36.3662462);
      expect(i.dronePos, const Offset(44.1, 83.1));
      expect(i.droneZCm, 79.0);
      expect(i.photoUrl, '/incidents/inc-3/photo.jpg');
      expect(i.mode, 'tt_down');
      expect(i.method, 'slot');
      expect(i.detection, isNull);
      expect(i.placeText, 'S4 · slot S4, unnamed building');
      expect(i.key, 'inc-3@1763000000.4');
    });

    test('incident: 모르는 값은 null, detection 있으면 파싱', () {
      final i = Incident.fromJson({
        'id': 'inc-1',
        'slot': null,
        'place': null,
        'pos': null,
        'detection': {
          'is_abnormal': true,
          'category': 'fire',
          'urgency': 'high',
          'needs_human_review': false,
          'description': '연기',
        },
      })!;
      expect(i.slot, isNull);
      expect(i.pos, isNull);
      expect(i.placeText, '장소 모름');
      expect(i.detection!.isAbnormal, isTrue);
      expect(i.detection!.urgency, 'high');
      expect(urgencyKo(i.detection!.urgency), '높음');
      expect(Incident.fromJson({'ts': 1}), isNull); // id 없음
    });

    test('incidents 목록: 잘못된 항목은 버린다', () {
      final m = ServerMessage.parse(jsonEncode({
        'type': 'incidents',
        'items': [incidentJson(id: 'inc-2'), 'x', {'no_id': 1}, incidentJson(id: 'inc-1')],
      }));
      expect((m as IncidentsMsg).items.map((i) => i.id), ['inc-2', 'inc-1']);
    });

    test('서버 주소 → 사진·영상 주소', () {
      final ws = Uri.parse('ws://192.168.0.10:8000/ws');
      expect(ServerAddress.httpUrl(ws, '/incidents/inc-3/photo.jpg').toString(),
          'http://192.168.0.10:8000/incidents/inc-3/photo.jpg');
      expect(ServerAddress.videoUrl(ws).toString(), 'ws://192.168.0.10:8000/ws/video');
      expect(ServerAddress.httpUrl(Uri.parse('wss://a.b/ws'), 'x.jpg').toString(),
          'https://a.b/x.jpg');
    });

    test('모드 이름', () {
      expect(visionModeKo('esp32'), 'ESP32 카메라');
      expect(visionModeKo('tt_down'), 'TT 하단 카메라');
      expect(errorMessageKo('WRONG_MODE'), contains('모드'));
    });
  });

  group('DroneClient 사건 저장·확인 표시', () {
    late FakeLink link;
    late DroneClient client;

    setUp(() async {
      link = FakeLink();
      client = DroneClient(connector: (_) async => link)..connect(Uri.parse('ws://t:8000/ws'));
      await Future<void>.delayed(Duration.zero);
    });
    tearDown(() => client.dispose());

    test('새 사건: 목록 맨 앞, 새 사건 알림 1번, 같은 사건은 다시 알리지 않음', () async {
      final got = <Incident>[];
      client.newIncidents.listen(got.add);
      final events = <DroneEvent>[];
      client.events.listen(events.add);

      link.push({'type': 'incident', 'incident': incidentJson(id: 'inc-1', ts: 1)});
      link.push({'type': 'event', 'level': 'info', 'code': 'INCIDENT_NEW', 'message': '새 사건'});
      link.push({'type': 'incident', 'incident': incidentJson(id: 'inc-2', ts: 2)});
      link.push({'type': 'incident', 'incident': incidentJson(id: 'inc-2', ts: 2)}); // 중복
      await Future<void>.delayed(Duration.zero);

      expect(client.incidents.map((i) => i.id), ['inc-2', 'inc-1']);
      expect(got.map((i) => i.id), ['inc-1', 'inc-2']);
      expect(events, isEmpty); // INCIDENT_NEW는 따로 띄우지 않는다
      expect(client.unackedCount, 2);
    });

    test('incidents 목록은 알림 없이 목록만 바꾼다', () async {
      final got = <Incident>[];
      client.newIncidents.listen(got.add);
      link.push({
        'type': 'incidents',
        'items': [incidentJson(id: 'inc-2', ts: 2), incidentJson(id: 'inc-1', ts: 1)],
      });
      await Future<void>.delayed(Duration.zero);
      expect(client.incidents.length, 2);
      expect(got, isEmpty);
      expect(client.unackedCount, 2);
    });

    test('확인 표시: 하나씩, 모두, 되돌리기. 서버 재시작으로 id가 겹쳐도 새 사건은 미확인', () async {
      link.push({
        'type': 'incidents',
        'items': [incidentJson(id: 'inc-2', ts: 2), incidentJson(id: 'inc-1', ts: 1)],
      });
      await Future<void>.delayed(Duration.zero);
      final first = client.incidents.last;
      client.setAcked(first, true);
      expect(client.isAcked(first), isTrue);
      expect(client.unackedCount, 1);
      client.ackAll();
      expect(client.unackedCount, 0);
      client.setAcked(first, false);
      expect(client.unackedCount, 1);
      client.ackAll();

      // 서버 재시작: 목록이 비고, 같은 id inc-1이 다른 시각으로 다시 옴
      link.push({'type': 'incidents', 'items': <Object>[]});
      link.push({'type': 'incident', 'incident': incidentJson(id: 'inc-1', ts: 99)});
      await Future<void>.delayed(Duration.zero);
      expect(client.incidents.single.id, 'inc-1');
      expect(client.unackedCount, 1);
    });

    test('set_vision_mode 명령 형식', () async {
      unawaited(client.setVisionMode('esp32'));
      final cmd = link.cmds.single;
      expect(cmd['cmd'], 'set_vision_mode');
      expect(cmd['mode'], 'esp32');
    });
  });

  group('v0.7 화면', () {
    late FakeLink link;
    late DroneClient client;
    final videoLinks = <FakeLink>[];
    final videoUris = <Uri>[];

    Future<void> start(WidgetTester tester, {Map<String, dynamic>? state}) async {
      tester.view.physicalSize = const Size(1080, 2340);
      tester.view.devicePixelRatio = 2.75; // 약 393 x 851
      addTearDown(tester.view.reset);
      link = FakeLink();
      videoLinks.clear();
      videoUris.clear();
      client = DroneClient(connector: (_) async => link)..connect(Uri.parse('ws://test:8000/ws'));
      await tester.pumpWidget(DroneControlApp(
        client: client,
        videoConnector: (uri) async {
          videoUris.add(uri);
          final l = FakeLink();
          videoLinks.add(l);
          return l;
        },
      ));
      await tester.pump();
      link.push(configV07());
      link.push(state ?? stateV07());
      await tester.pump();
    }

    Future<void> finish(WidgetTester tester) async {
      await tester.pumpWidget(const SizedBox());
      client.dispose();
    }

    Future<void> ack(WidgetTester tester, {bool ok = true, String? error}) async {
      link.push({'type': 'ack', 'id': link.cmds.last['id'], 'ok': ok, 'error': error});
      await tester.pump();
      await tester.pump();
    }

    bool chipEnabled(WidgetTester tester, String mode) =>
        tester.widget<ChoiceChip>(find.byKey(Key('vision-mode-$mode'))).onSelected != null;

    testWidgets('모드 전환: 호버링이면 다른 모드 버튼이 켜지고 set_vision_mode 전송', (tester) async {
      await start(tester);
      expect(find.byKey(const Key('vision-panel')), findsOneWidget);
      expect(chipEnabled(tester, 'esp32'), isTrue);
      expect(chipEnabled(tester, 'tt_down'), isFalse); // 이미 이 모드
      expect(find.text('카메라 정상 · 영상 수신 중'), findsOneWidget);
      // 위치 출처에 카메라 종류가 함께 보인다
      expect(find.textContaining('카메라·TT 하단', findRichText: true), findsOneWidget);

      await tester.tap(find.byKey(const Key('vision-mode-esp32')));
      await tester.pump();
      expect(link.cmds.last['cmd'], 'set_vision_mode');
      expect(link.cmds.last['mode'], 'esp32');
      expect(chipEnabled(tester, 'esp32'), isFalse); // 응답 기다리는 중
      await ack(tester);
      expect(find.textContaining('카메라 전환(ESP32 카메라) 명령을 보냈습니다'), findsOneWidget);

      // 서버는 바꿨지만 연결 프로그램은 아직 이전 모드 → 전환 중
      link.push(stateV07(vision: visionJson(mode: 'esp32', bridgeMode: 'tt_down')));
      await tester.pump();
      expect(find.text('전환 중… (지금 TT 하단)'), findsOneWidget);
      expect(tester.widget<ChoiceChip>(find.byKey(const Key('vision-mode-esp32'))).selected, isTrue);
      await finish(tester);
    });

    testWidgets('모드 전환: 이동 중에는 버튼이 꺼지고 이유를 보여 준다', (tester) async {
      await start(tester, state: stateV07(mode: 'moving'));
      expect(chipEnabled(tester, 'esp32'), isFalse);
      expect(find.byKey(const Key('vision-blocked')), findsOneWidget);
      expect(find.textContaining('이동 중에는 바꿀 수 없습니다'), findsOneWidget);

      link.push(stateV07(mode: 'idle'));
      await tester.pump();
      expect(chipEnabled(tester, 'esp32'), isTrue);
      expect(find.byKey(const Key('vision-blocked')), findsNothing);
      await finish(tester);
    });

    testWidgets('모드 전환: 서버 BUSY를 알기 쉽게 보여 준다', (tester) async {
      await start(tester);
      await tester.tap(find.byKey(const Key('vision-mode-esp32')));
      await tester.pump();
      await ack(tester, ok: false, error: 'BUSY');
      expect(find.textContaining('바꿀 수 없습니다. 정지(호버링)·착지 후 다시 시도하세요'), findsOneWidget);
      expect(chipEnabled(tester, 'esp32'), isTrue); // 다시 누를 수 있다
      await finish(tester);
    });

    testWidgets('모드 전환: 연결 프로그램이 없으면 경고', (tester) async {
      await start(tester, state: stateV07(vision: visionJson(bridge: false, bridgeMode: null, cameraOk: null)));
      expect(find.text('위치 인식 프로그램 연결 안 됨'), findsOneWidget);
      await finish(tester);
    });

    testWidgets('슬롯을 누르면 그 중심에 목표 마커 → 확인하면 goto', (tester) async {
      await start(tester);
      expect(find.byKey(const Key('slot-1')), findsOneWidget);
      await tester.tap(find.byKey(const Key('slot-4')));
      await tester.pump();
      expect(find.text('목표: x 46.8 cm, y 89.1 cm'), findsOneWidget);
      expect(find.textContaining('사진 슬롯 S4'), findsOneWidget);
      expect(find.byKey(const Key('target-marker')), findsOneWidget);

      await tester.tap(find.byKey(const Key('btn-confirm')));
      await tester.pump();
      expect(link.cmds.last['cmd'], 'goto');
      expect(link.cmds.last['x_cm'], 46.8);
      expect(link.cmds.last['y_cm'], 89.1);
      await ack(tester);
      expect(find.byKey(const Key('dispatch-panel')), findsNothing);
      await finish(tester);
    });

    testWidgets('슬롯 출동 후 마커를 끌면 슬롯 표시가 사라진다', (tester) async {
      await start(tester);
      await tester.tap(find.byKey(const Key('slot-4')));
      await tester.pump();
      await tester.drag(find.byKey(const Key('target-marker')), const Offset(80, -50));
      await tester.pump();
      expect(find.textContaining('사진 슬롯 S4'), findsNothing);
      expect(find.text('목표: x 46.8 cm, y 89.1 cm'), findsNothing);
      await finish(tester);
    });

    testWidgets('새 사건: 알림·배지·지도 표시 → 상세 → 확인', (tester) async {
      await start(tester);
      expect(tester.widget<Badge>(find.byKey(const Key('incident-badge'))).isLabelVisible, isFalse);

      link.push({'type': 'incident', 'incident': incidentJson()});
      await tester.pump();
      expect(find.byKey(const Key('incident-alert')), findsOneWidget);
      expect(find.text('새 사건: S4 · slot S4, unnamed building'), findsOneWidget);
      final badge = tester.widget<Badge>(find.byKey(const Key('incident-badge')));
      expect(badge.isLabelVisible, isTrue);
      expect((badge.label as Text).data, '1');
      expect(find.byKey(const Key('incident-pin-inc-3@1763000000.4')), findsOneWidget);

      // 알림을 누르면 상세 화면
      await tester.tap(find.byKey(const Key('incident-alert')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('detection-none')), findsOneWidget);
      expect(find.textContaining('확인 필요 (AI 판별 꺼짐)'), findsOneWidget);
      expect(find.text('TT 하단 카메라'), findsOneWidget);
      expect(find.text('x 44.1, y 83.1 cm, 고도 79 cm'), findsOneWidget);
      expect(find.text('36.36625, 127.36037'), findsOneWidget);

      await tester.tap(find.byKey(const Key('btn-ack')));
      await tester.pump();
      expect(client.unackedCount, 0);
      expect(find.byKey(const Key('btn-unack')), findsOneWidget);

      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(tester.widget<Badge>(find.byKey(const Key('incident-badge'))).isLabelVisible, isFalse);
      await finish(tester);
    });

    testWidgets('알림은 10초 뒤 저절로 사라진다', (tester) async {
      await start(tester);
      link.push({'type': 'incident', 'incident': incidentJson()});
      await tester.pump();
      expect(find.byKey(const Key('incident-alert')), findsOneWidget);
      await tester.pump(const Duration(seconds: 11));
      expect(find.byKey(const Key('incident-alert')), findsNothing);
      await finish(tester);
    });

    testWidgets('사건 목록: 시각·장소·위경도·카메라·확인 여부, 모두 확인', (tester) async {
      await start(tester);
      link.push({
        'type': 'incidents',
        'items': [
          incidentJson(id: 'inc-2', ts: 1763000100),
          {...incidentJson(id: 'inc-1', ts: 1763000000, slot: null), 'mode': 'esp32', 'place': null},
        ],
      });
      await tester.pump();
      expect(find.byKey(const Key('incident-alert')), findsNothing); // 목록은 알림 없이
      await tester.tap(find.byKey(const Key('btn-incidents')));
      await tester.pumpAndSettle();
      expect(find.text('사건 목록 (미확인 2)'), findsOneWidget);
      expect(find.textContaining('S4 · slot S4, unnamed building'), findsOneWidget);
      expect(find.textContaining('장소 모름'), findsOneWidget);
      expect(find.textContaining('위경도 36.36625, 127.36037'), findsNWidgets(2));
      expect(find.textContaining('카메라 ESP32 카메라 · 미확인'), findsOneWidget);
      expect(find.textContaining('카메라 TT 하단 카메라 · 미확인'), findsOneWidget);

      await tester.tap(find.byKey(const Key('btn-ack-all')));
      await tester.pump();
      expect(find.text('사건 목록 (미확인 0)'), findsOneWidget);
      expect(find.textContaining('· 확인함'), findsNWidgets(2));
      await finish(tester);
    });

    testWidgets('사진을 못 받으면(404 등) "사진 없음"', (tester) async {
      await start(tester);
      link.push({'type': 'incidents', 'items': [incidentJson()]});
      await tester.pump();
      await tester.tap(find.byKey(const Key('btn-incidents')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('incident-inc-3@1763000000.4')));
      await tester.pumpAndSettle();
      // 시험 환경의 HTTP는 항상 실패(400)한다 → errorBuilder
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
      await tester.pump();
      expect(find.byKey(const Key('incident-no-photo')), findsOneWidget);
      await finish(tester);
    });

    testWidgets('카메라 영상: 열면 /ws/video 연결, 사진·장수 표시, 정지·착륙, 닫으면 끊김', (tester) async {
      await start(tester);
      await tester.tap(find.byKey(const Key('btn-video')));
      await tester.pumpAndSettle();
      expect(videoUris.single.toString(), 'ws://test:8000/ws/video');
      final v = videoLinks.single;
      expect(find.text('드론 카메라 · TT 하단 카메라'), findsOneWidget);

      v.push({'type': 'video_status', 'live': false, 'message': '카메라 영상이 없습니다'});
      await tester.pump();
      expect(find.textContaining('영상 없음: 카메라 영상이 없습니다'), findsOneWidget);

      v.push({'type': 'video_status', 'live': true, 'message': '수신 중'});
      for (var n = 0; n < 5; n++) {
        v.ctrl.add(Uint8List.fromList(_png1x1));
      }
      await tester.pump();
      expect(find.byKey(const Key('video-frame')), findsOneWidget);
      expect(find.textContaining('실시간 5장/초'), findsOneWidget);

      // 1초 넘게 사진이 안 오면 0장/초
      await tester.pump(const Duration(milliseconds: 1600));
      expect(find.textContaining('0장/초'), findsOneWidget);

      // 정지·착륙은 명령 통로(/ws)로 간다
      link.push(stateV07()); // 서버 응답 유지
      await tester.tap(find.byKey(const Key('video-btn-stop')));
      await tester.pump();
      expect(link.cmds.last['cmd'], 'stop');
      await ack(tester);
      expect(find.text('정지 명령을 보냈습니다'), findsOneWidget);
      await tester.tap(find.byKey(const Key('video-btn-land')));
      await tester.pump();
      expect(link.cmds.last['cmd'], 'land');
      await ack(tester);
      expect(link.closed, isFalse);
      expect(v.sent, isEmpty); // 영상 통로로는 아무것도 보내지 않는다

      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(v.closed, isTrue);
      expect(link.closed, isFalse);
      await finish(tester);
    });

    testWidgets('카메라 영상이 끊기면 자동으로 다시 연결한다', (tester) async {
      await start(tester);
      await tester.tap(find.byKey(const Key('btn-video')));
      await tester.pumpAndSettle();
      unawaited(videoLinks.single.ctrl.close());
      await tester.pump();
      expect(find.textContaining('영상 끊김 — 다시 연결 중'), findsOneWidget);
      link.push(stateV07());
      await tester.pump(const Duration(milliseconds: 2100));
      await tester.pump();
      expect(videoLinks.length, 2);
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(videoLinks.last.closed, isTrue);
      await finish(tester);
    });
  });
}

/// 1×1 PNG (영상 사진 자리 채우기용)
const _png1x1 = <int>[
  0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52, //
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
  0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x63, 0xF8, 0xCF, 0xC0, 0xF0,
  0x1F, 0x00, 0x05, 0x00, 0x01, 0xFF, 0x89, 0x99, 0x3D, 0x1D, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45,
  0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
];
