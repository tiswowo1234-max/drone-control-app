import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'config.dart';
import 'map_coords.dart';
import 'protocol.dart';
import 'safety.dart';

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
    this.onSlotTap,
    this.incidents = const [],
    this.isIncidentAcked,
    this.onIncidentTap,
    this.car,
    this.carDraft = const [],
    this.onMapTap,
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

  /// 사진 슬롯(번호 표시나 사각형 안)을 눌렀을 때. 화면은 그 중심에 출동 목표를 놓는다.
  final ValueChanged<PhotoSlot>? onSlotTap;

  /// 지도에 표시할 사건 (사진 위치 `pos`가 있는 것만 그린다)
  final List<Incident> incidents;

  /// 확인한 사건이면 true (회색으로 그린다)
  final bool Function(Incident)? isIncidentAcked;
  final ValueChanged<Incident>? onIncidentTap;

  /// 차(UGV) 상태 (v0.8). 위치를 알면 차 아이콘과 주행 중인 경로를 그린다.
  final CarStatus? car;

  /// 차 조종 모드에서 찍은 경로점 (cm). 차 위치부터 주황 선으로 잇는다.
  final List<Offset> carDraft;

  /// 지도를 누른 곳 (cm). 있으면 마커·슬롯보다 먼저 처리한다 (차 조종 모드).
  final ValueChanged<Offset>? onMapTap;

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
        // 화면 안에 지도 비율(설정의 가로 cm : 세로 cm)을 유지한 채 최대한 크게 맞춘다.
        // assets/map.png도 같은 비율로 만들어 둔다 (tools/pdf_to_png.py).
        final aspect = widget.config.mapWidthCm / widget.config.mapHeightCm;
        var w = constraints.maxWidth;
        var h = w / aspect;
        if (h > constraints.maxHeight) {
          h = constraints.maxHeight;
          w = h * aspect;
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
                  // 슬롯 사각형 안을 누르면 슬롯 출동
                  onTapUp: (widget.onMarkerMoved == null &&
                          widget.onSlotTap == null &&
                          widget.onMapTap == null)
                      ? null
                      : (d) => _onTap(coords.pxToCm(d.localPosition)),
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
                        child: IgnorePointer(
                          child: CustomPaint(
                            painter: _SlotPainter(coords, widget.config.photoSlots),
                          ),
                        ),
                      ),
                      Positioned.fill(
                        child: IgnorePointer(
                          child: CustomPaint(
                            painter: _CarRoutePainter(
                              coords,
                              active: widget.car?.routePoints ?? const [],
                              activeWp: widget.car?.routeWp,
                              draftFrom: widget.car?.pos?.xy,
                              draft: widget.carDraft,
                            ),
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

  void _onTap(Offset cm) {
    if (widget.onMapTap != null) {
      widget.onMapTap!(cm);
      return;
    }
    if (widget.onSlotTap != null) {
      for (final slot in widget.config.photoSlots) {
        if (pointInPolygon(cm, slot.corners)) {
          widget.onSlotTap!(slot);
          return;
        }
      }
    }
    widget.onMarkerMoved?.call(_clamp(cm));
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
        for (final slot in widget.config.photoSlots)
          _icon(coords.cmToPx(slot.center), s, 30,
              child: _SlotLabel(
                slot: slot,
                onTap: widget.onSlotTap == null ? null : () => widget.onSlotTap!(slot),
              )),
        for (final inc in widget.incidents.reversed) // 최신이 위에 오도록
          if (inc.pos != null)
            _icon(coords.cmToPx(inc.pos!), s, 32, bottomAnchor: true,
                child: _IncidentPin(
                  incident: inc,
                  acked: widget.isIncidentAcked?.call(inc) ?? false,
                  onTap: widget.onIncidentTap == null
                      ? null
                      : () => widget.onIncidentTap!(inc),
                )),
        _icon(coords.cmToPx(widget.config.home), s, 28, bottomAnchor: true,
            child: const Tooltip(
                message: '이륙·귀환 지점',
                child: Icon(Icons.home, color: Colors.blue, size: 28))),
        if (st?.target != null)
          _icon(coords.cmToPx(st!.target!), s, 28, bottomAnchor: true,
              child: const Tooltip(
                  message: '현재 이동 목표',
                  child: Icon(Icons.flag, color: Colors.orange, size: 28))),
        for (final (i, p) in widget.carDraft.indexed)
          _icon(coords.cmToPx(p), s, 22,
              child: _RoutePointLabel(key: Key('car-draft-$i'), text: 'P${i + 1}')),
        if (widget.car?.pos != null)
          _icon(coords.cmToPx(widget.car!.pos!.xy), s, 34,
              child: _CarIcon(
                yawDeg: widget.car!.pos!.yawDeg,
                seen: widget.car!.posSource == 'vision',
                active: widget.droneFresh && widget.car!.connected,
              )),
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

/// 차(UGV) 아이콘: 둥근 사각형(드론은 원) + 진행 방향 화살표.
/// 드론이 지금 보고 있으면 진한 파랑, 엔코더로 이어 가는 중이면 회청색 점선 테두리 느낌(옅게).
class _CarIcon extends StatelessWidget {
  const _CarIcon({required this.yawDeg, required this.seen, required this.active});

  final double yawDeg;
  final bool seen;
  final bool active;

  @override
  Widget build(BuildContext context) {
    final color = !active
        ? Colors.grey
        : seen
            ? Colors.blue.shade800
            : Colors.blueGrey.shade400;
    return Tooltip(
      message: '차 (${seen ? '드론 인식' : '엔코더로 이어감'}, ${yawDeg.toStringAsFixed(0)}°)',
      child: DecoratedBox(
        key: const Key('car-icon'),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(8),
          color: color.withValues(alpha: seen ? 0.9 : 0.6),
          border: Border.all(color: seen ? Colors.white : Colors.amber, width: 2),
        ),
        child: Transform.rotate(
          angle: yawDeg * math.pi / 180,
          child: const Icon(Icons.navigation, color: Colors.white, size: 22),
        ),
      ),
    );
  }
}

/// 차 경로점 번호 (P1, P2 …)
class _RoutePointLabel extends StatelessWidget {
  const _RoutePointLabel({super.key, required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: Colors.orange.shade800,
        border: Border.all(color: Colors.white, width: 1.5),
      ),
      child: Center(
        child: Text(text,
            style: const TextStyle(color: Colors.white, fontSize: 9, fontWeight: FontWeight.bold)),
      ),
    );
  }
}

/// 차 경로: 주행 중인 경로(초록, 지나온 구간은 회색) + 찍고 있는 경로(주황, 차 위치에서 시작)
class _CarRoutePainter extends CustomPainter {
  _CarRoutePainter(this.coords,
      {required this.active, this.activeWp, this.draftFrom, required this.draft});

  final MapCoords coords;
  final List<Offset> active;
  final int? activeWp;
  final Offset? draftFrom;
  final List<Offset> draft;

  @override
  void paint(Canvas canvas, Size size) {
    Paint line(Color c, double w) => Paint()
      ..color = c
      ..style = PaintingStyle.stroke
      ..strokeWidth = w
      ..strokeCap = StrokeCap.round;
    if (active.length >= 2) {
      final wp = (activeWp ?? 1).clamp(1, active.length);
      for (var i = 1; i < active.length; i++) {
        final done = i < wp; // P(i-1)→P(i) 구간을 이미 지남
        canvas.drawLine(coords.cmToPx(active[i - 1]), coords.cmToPx(active[i]),
            line(done ? Colors.grey.withValues(alpha: 0.6) : Colors.green.shade700, 3));
      }
      for (final p in active.skip(1)) {
        canvas.drawCircle(coords.cmToPx(p), 4, Paint()..color = Colors.green.shade800);
      }
    }
    if (draft.isNotEmpty) {
      final pts = [?draftFrom, ...draft];
      final paint = line(Colors.orange.shade800, 2.5);
      for (var i = 1; i < pts.length; i++) {
        canvas.drawLine(coords.cmToPx(pts[i - 1]), coords.cmToPx(pts[i]), paint);
      }
    }
  }

  @override
  bool shouldRepaint(_CarRoutePainter old) =>
      old.coords.viewSize != coords.viewSize ||
      old.active != active ||
      old.activeWp != activeWp ||
      old.draftFrom != draftFrom ||
      old.draft != draft;
}

/// 사진 슬롯 번호 표시 (누르면 슬롯 출동)
class _SlotLabel extends StatelessWidget {
  const _SlotLabel({required this.slot, this.onTap});

  final PhotoSlot slot;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: '사진 슬롯 ${slot.name} (누르면 출동 목표)',
      child: GestureDetector(
        key: Key('slot-${slot.slot}'),
        behavior: HitTestBehavior.opaque,
        onTap: onTap,
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: Colors.purple.shade700.withValues(alpha: 0.85),
            borderRadius: BorderRadius.circular(6),
            border: Border.all(color: Colors.white, width: 1.5),
          ),
          child: Center(
            child: Text(slot.label,
                style: const TextStyle(
                    color: Colors.white, fontWeight: FontWeight.bold, fontSize: 13)),
          ),
        ),
      ),
    );
  }
}

/// 사건 위치 표시 (미확인 = 빨강, 확인함 = 회색)
class _IncidentPin extends StatelessWidget {
  const _IncidentPin({required this.incident, required this.acked, this.onTap});

  final Incident incident;
  final bool acked;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: '사건 ${incident.placeText}${acked ? ' (확인함)' : ''}',
      child: GestureDetector(
        key: Key('incident-pin-${incident.key}'),
        behavior: HitTestBehavior.opaque,
        onTap: onTap,
        child: Icon(
          Icons.report,
          size: 32,
          color: acked ? Colors.grey.shade600 : Colors.red.shade700,
          shadows: const [Shadow(color: Colors.white, blurRadius: 6)],
        ),
      ),
    );
  }
}

/// 사진 슬롯 사각형
class _SlotPainter extends CustomPainter {
  _SlotPainter(this.coords, this.slots);

  final MapCoords coords;
  final List<PhotoSlot> slots;

  @override
  void paint(Canvas canvas, Size size) {
    final fill = Paint()..color = Colors.purple.withValues(alpha: 0.15);
    final line = Paint()
      ..color = Colors.purple.shade700
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
    for (final slot in slots) {
      if (slot.corners.length < 3) continue;
      final path = Path()
        ..addPolygon([for (final c in slot.corners) coords.cmToPx(c)], true);
      canvas.drawPath(path, fill);
      canvas.drawPath(path, line);
    }
  }

  @override
  bool shouldRepaint(_SlotPainter old) =>
      old.coords.viewSize != coords.viewSize || old.slots != slots;
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
