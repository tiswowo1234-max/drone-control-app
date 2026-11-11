import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'config.dart';
import 'map_coords.dart';
import 'protocol.dart';

/// 지도 이미지의 가로:세로 비율 (assets/map.png = 2501x1802)
const double kMapImageAspect = 2501 / 1802;

/// 확대·이동 가능한 지도 + 위에 겹쳐 그리는 표시들
/// (이륙 지점, 허용 반경, 드론, 현재 이동 목표, 출동 마커).
///
/// 표시 아이콘은 확대해도 화면에서 같은 크기로 보이도록 확대 배율만큼 줄여서 그린다.
class MapView extends StatefulWidget {
  const MapView({
    super.key,
    required this.config,
    required this.state,
    required this.droneFresh,
    this.marker,
    this.markerOk = true,
    this.onMarkerMoved,
  });

  final AppConfig config;
  final DroneState? state;

  /// 드론 위치가 최신 값인지 (서버 끊김 등이면 흐리게 표시)
  final bool droneFresh;

  /// 출동 목표 마커 위치 (cm). null이면 마커 없음.
  final Offset? marker;

  /// false면 마커를 빨갛게 표시
  final bool markerOk;

  /// 마커를 끌거나 지도를 눌러 위치를 바꿨을 때 (cm)
  final ValueChanged<Offset>? onMarkerMoved;

  @override
  State<MapView> createState() => _MapViewState();
}

class _MapViewState extends State<MapView> {
  final _tc = TransformationController();
  final _mapKey = GlobalKey();

  /// 마커를 잡은 지점과 마커 끝 사이의 차이 (화면 픽셀, 지도 기준)
  Offset _grab = Offset.zero;

  @override
  void dispose() {
    _tc.dispose();
    super.dispose();
  }

  /// 화면 전체 좌표 → 지도 이미지 기준 픽셀 (확대·이동 반영)
  Offset? _toMapLocal(Offset global) {
    final box = _mapKey.currentContext?.findRenderObject() as RenderBox?;
    return box?.globalToLocal(global);
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        // 화면 안에 지도 비율을 유지한 채 최대한 크게 맞춘다
        var w = constraints.maxWidth;
        var h = w / kMapImageAspect;
        if (h > constraints.maxHeight) {
          h = constraints.maxHeight;
          w = h * kMapImageAspect;
        }
        final coords = MapCoords(config: widget.config, viewSize: Size(w, h));

        return ClipRect(
          child: InteractiveViewer(
            transformationController: _tc,
            minScale: 1,
            maxScale: 8,
            child: Center(
              child: SizedBox(
                key: _mapKey,
                width: w,
                height: h,
                child: GestureDetector(
                  // 출동 중이면 지도를 누른 곳으로 마커를 옮긴다
                  onTapUp: widget.onMarkerMoved == null
                      ? null
                      : (d) => widget.onMarkerMoved!(
                          _clamp(coords.pxToCm(d.localPosition))),
                  child: Stack(
                    clipBehavior: Clip.none,
                    children: [
                      Positioned.fill(
                        child: Image.asset('assets/map.png', fit: BoxFit.fill),
                      ),
                      Positioned.fill(
                        child: IgnorePointer(
                          child: CustomPaint(
                            painter: _RadiusPainter(coords, widget.config),
                          ),
                        ),
                      ),
                      Positioned.fill(
                        child: AnimatedBuilder(
                          animation: _tc,
                          builder: (context, _) => _overlay(coords),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  Offset _clamp(Offset cm) => Offset(
        cm.dx.clamp(0, widget.config.mapWidthCm),
        cm.dy.clamp(0, widget.config.mapHeightCm),
      );

  Widget _overlay(MapCoords coords) {
    final s = _tc.value.getMaxScaleOnAxis(); // 현재 확대 배율
    final st = widget.state;
    final pos = st?.pos;
    return Stack(
      clipBehavior: Clip.none,
      children: [
        _icon(coords.cmToPx(widget.config.home), s, 28, bottomAnchor: true,
            child: const Tooltip(
                message: '이륙·귀환 지점',
                child: Icon(Icons.home, color: Colors.blue, size: 28))),
        if (st?.target != null)
          _icon(coords.cmToPx(st!.target!), s, 28, bottomAnchor: true,
              child: const Tooltip(
                  message: '현재 이동 목표',
                  child: Icon(Icons.flag, color: Colors.orange, size: 28))),
        if (pos != null)
          _icon(coords.cmToPx(pos.xy), s, 40,
              child: _DroneIcon(
                  yawDeg: pos.yawDeg,
                  active: widget.droneFresh && st!.connected)),
        if (widget.marker != null) _markerWidget(coords, s),
      ],
    );
  }

  /// [px] 위치에 화면상 [size] 크기로 보이는 아이콘을 놓는다.
  /// [bottomAnchor]가 true면 아이콘 아래 끝, 아니면 가운데가 [px]에 온다.
  Widget _icon(Offset px, double scale, double size,
      {required Widget child, bool bottomAnchor = false}) {
    final sz = size / scale;
    return Positioned(
      left: px.dx - sz / 2,
      top: bottomAnchor ? px.dy - sz : px.dy - sz / 2,
      width: sz,
      height: sz,
      child: FittedBox(child: SizedBox(width: size, height: size, child: child)),
    );
  }

  Widget _markerWidget(MapCoords coords, double scale) {
    const size = 56.0; // 손가락으로 잡기 쉬운 크기
    final px = coords.cmToPx(widget.marker!);
    final color = widget.markerOk ? Colors.green.shade700 : Colors.red;
    return _icon(
      px,
      scale,
      size,
      bottomAnchor: true,
      child: GestureDetector(
        key: const Key('target-marker'),
        behavior: HitTestBehavior.opaque,
        // 누른 순간의 위치로 잡은 지점을 기억한다 (드래그로 인정되기까지 움직인 거리도 반영되도록)
        onPanDown: (d) {
          final local = _toMapLocal(d.globalPosition);
          if (local != null) _grab = coords.cmToPx(widget.marker!) - local;
        },
        onPanUpdate: (d) {
          final local = _toMapLocal(d.globalPosition);
          if (local == null) return;
          widget.onMarkerMoved?.call(_clamp(coords.pxToCm(local + _grab)));
        },
        child: Icon(Icons.location_on, color: color, size: size, shadows: const [
          Shadow(color: Colors.white, blurRadius: 6),
        ]),
      ),
    );
  }
}

/// 드론 아이콘: 화살표가 yaw 방향(지도 위쪽 0°, 시계 방향 +)을 가리킨다.
class _DroneIcon extends StatelessWidget {
  const _DroneIcon({required this.yawDeg, required this.active});

  final double yawDeg;
  final bool active;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: '드론 (${yawDeg.toStringAsFixed(0)}°)',
      child: DecoratedBox(
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: (active ? Colors.deepPurple : Colors.grey).withValues(alpha: 0.85),
          border: Border.all(color: Colors.white, width: 2),
        ),
        child: Transform.rotate(
          // 화면은 y가 아래로 증가하므로 양수 각도가 시계 방향 회전이다
          angle: yawDeg * math.pi / 180,
          child: const Icon(Icons.navigation, color: Colors.white, size: 26),
        ),
      ),
    );
  }
}

/// home 중심의 최대 비행 반경을 점선 대신 반투명 원으로 표시
class _RadiusPainter extends CustomPainter {
  _RadiusPainter(this.coords, this.config);

  final MapCoords coords;
  final AppConfig config;

  @override
  void paint(Canvas canvas, Size size) {
    final c = coords.cmToPx(config.home);
    final r = config.limits.maxRadiusCm;
    final rx = coords.cmToPx(config.home + Offset(r, 0)).dx - c.dx;
    final ry = coords.cmToPx(config.home - Offset(0, r)).dy - c.dy;
    final rect = Rect.fromCenter(center: c, width: rx * 2, height: ry * 2);
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.drawOval(rect, Paint()..color = Colors.blue.withValues(alpha: 0.08));
    canvas.drawOval(
      rect,
      Paint()
        ..color = Colors.blue.withValues(alpha: 0.6)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5,
    );
    canvas.restore();
  }

  @override
  bool shouldRepaint(_RadiusPainter old) =>
      old.coords.viewSize != coords.viewSize || old.config != config;
}
