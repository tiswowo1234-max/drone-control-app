import 'dart:ui';

import 'config.dart';

/// 화면 위 지도 위치(픽셀) ↔ 바닥 지도 좌표(cm) 변환.
///
/// - 화면 픽셀: 왼쪽 위가 (0,0), 아래로 갈수록 y 증가
/// - 지도 cm  : 왼쪽 아래가 (0,0), 위로 갈수록 y 증가 (docs/api.md 2장)
class MapCoords {
  const MapCoords({required this.config, required this.viewSize});

  final AppConfig config;

  /// 화면에 그려진 지도 이미지의 크기 (픽셀)
  final Size viewSize;

  double get _pxPerCmX => viewSize.width / config.mapWidthCm;
  double get _pxPerCmY => viewSize.height / config.mapHeightCm;

  /// 화면 픽셀 위치 → 지도 cm 좌표
  Offset pxToCm(Offset px) => Offset(
        px.dx / _pxPerCmX,
        config.mapHeightCm - px.dy / _pxPerCmY,
      );

  /// 지도 cm 좌표 → 화면 픽셀 위치
  Offset cmToPx(Offset cm) => Offset(
        cm.dx * _pxPerCmX,
        (config.mapHeightCm - cm.dy) * _pxPerCmY,
      );

  /// 지도 경계 안인지
  bool isInside(Offset cm) =>
      cm.dx >= 0 &&
      cm.dy >= 0 &&
      cm.dx <= config.mapWidthCm &&
      cm.dy <= config.mapHeightCm;
}
