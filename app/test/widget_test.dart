import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';

import 'package:drone_control/config.dart';
import 'package:drone_control/main.dart';
import 'package:drone_control/map_coords.dart';

void main() {
  group('MapCoords', () {
    const config = AppConfig.fallback; // 250 x 180 cm
    const coords = MapCoords(config: config, viewSize: Size(500, 360));

    test('화면 왼쪽 아래 = 지도 (0,0)', () {
      final cm = coords.pxToCm(const Offset(0, 360));
      expect(cm.dx, closeTo(0, 1e-9));
      expect(cm.dy, closeTo(0, 1e-9));
    });

    test('화면 오른쪽 위 = 지도 (250,180)', () {
      final cm = coords.pxToCm(const Offset(500, 0));
      expect(cm.dx, closeTo(250, 1e-9));
      expect(cm.dy, closeTo(180, 1e-9));
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
      expect(coords.isInside(const Offset(10, 181)), isFalse);
    });
  });

  testWidgets('앱이 지도 화면으로 시작한다', (tester) async {
    await tester.pumpWidget(const DroneControlApp());
    expect(find.text('캠퍼스 경비 드론'), findsOneWidget);
    expect(find.textContaining('지도를 누르면'), findsOneWidget);
  });
}
