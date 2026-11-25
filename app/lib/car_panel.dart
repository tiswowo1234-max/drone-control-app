import 'dart:async';

import 'package:flutter/material.dart';

import 'config.dart';
import 'drone_client.dart';
import 'protocol.dart';

/// 차(UGV, 미니 경찰차) 조종 패널 — 지도 화면의 "차 조종" 모드에서 아래쪽에 보인다 (docs/api.md v0.8 13장).
///
/// - 상태: 서버⇄차 연결, 차 상태, 위치 출처(드론 인식 / 엔코더로 이어감), 경로 진행
/// - 경로: 지도를 눌러 찍은 점([draft])을 보내면 서버가 차 좌표로 바꿔 바로 출발시킨다
/// - 조이스틱: 누르고 있는 동안 0.12초마다 속도를 보내고, 손을 떼면 정지 (조원 PWA와 같은 조작)
/// - 정지 · 비상정지 · 비상정지 해제
/// - 드론 따라가기: 호버링 중인 드론이 차 뒤를 따라 난다 (멈춤 = 드론 정지)
class CarPanel extends StatefulWidget {
  const CarPanel({
    super.key,
    required this.client,
    required this.config,
    required this.car,
    required this.draft,
    required this.onUndo,
    required this.onClear,
    required this.onRouteSent,
    required this.onResult,
    this.droneFlying = false,
    this.droneMode = 'idle',
  });

  final DroneClient client;
  final CarConfig config;
  final CarStatus? car;

  /// 지도에서 찍은 경로점 (cm, 출발 위치 제외)
  final List<Offset> draft;
  final VoidCallback onUndo;
  final VoidCallback onClear;

  /// 경로를 보내는 데 성공했을 때 (화면이 [draft]를 비운다)
  final VoidCallback onRouteSent;

  /// 명령 결과 알림 (이름, 결과)
  final void Function(String name, CmdResult r) onResult;

  /// 드론 상태 (state.flying, state.mode) — 따라가기 버튼용
  final bool droneFlying;
  final String droneMode;

  @override
  State<CarPanel> createState() => _CarPanelState();
}

class _CarPanelState extends State<CarPanel> {
  static const _tick = Duration(milliseconds: 120);

  DroneClient get _c => widget.client;

  bool _sending = false;
  Timer? _driveTimer;
  Offset _knob = Offset.zero;
  /// 조이스틱 최대 속도 (슬라이더). 30 mm/s ~ 설정 최대값.
  late double _maxSpeed = _sliderMax * 0.5 < 30 ? 30.0 : _sliderMax * 0.5;

  double get _sliderMax => widget.config.maxSpeedMmS > 31 ? widget.config.maxSpeedMmS : 31.0;

  @override
  void dispose() {
    // 화면을 떠나면 조이스틱 주행을 멈춘다 (dispose 중에는 setState를 부르지 않는다)
    if (_driveTimer != null) {
      _driveTimer!.cancel();
      _driveTimer = null;
      _c.carStop();
    }
    super.dispose();
  }

  // ---------------------------------------------------------------- 조이스틱

  (int, double) get _drive => joystickToDrive(_knob, _maxSpeed, widget.config.maxOmegaRadS);

  void _startDrive(Offset knob) {
    _knob = knob;
    _c.carDrive(_drive.$1, _drive.$2);
    _driveTimer ??= Timer.periodic(_tick, (_) => _c.carDrive(_drive.$1, _drive.$2));
    setState(() {});
  }

  void _moveDrive(Offset knob) => setState(() => _knob = knob);

  void _endDrive({bool sendStop = true}) {
    final wasDriving = _driveTimer != null;
    _driveTimer?.cancel();
    _driveTimer = null;
    _knob = Offset.zero;
    if (wasDriving && sendStop) _c.carStop(); // 서버도 명령이 끊기면 멈추지만 바로 세운다
    if (mounted) setState(() {});
  }

  // ---------------------------------------------------------------- 명령

  Future<void> _run(String name, Future<CmdResult> Function() send) async {
    final r = await send();
    if (mounted) widget.onResult(name, r);
  }

  Future<void> _sendRoute() async {
    setState(() => _sending = true);
    final r = await _c.carRoute(widget.draft, speedMmS: widget.config.routeSpeedMmS);
    if (!mounted) return;
    setState(() => _sending = false);
    if (r.ok) widget.onRouteSent();
    widget.onResult('차 경로 출발', r);
  }

  @override
  Widget build(BuildContext context) {
    final car = widget.car;
    final connected = _c.online && (car?.connected ?? false);
    final blocked = car?.blocked ?? false;
    final knowsWhere = car?.posSource != null;
    final String? routeProblem = !connected
        ? '차와 연결되어 있지 않습니다'
        : blocked
            ? '비상정지를 해제해야 합니다'
            : !knowsWhere
                ? '드론이 차를 인식해야 경로를 보낼 수 있습니다'
                : null;

    return Container(
      key: const Key('car-panel'),
      width: double.infinity,
      color: Colors.indigo.shade50,
      padding: const EdgeInsets.fromLTRB(10, 6, 10, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _statusLine(car, connected),
          const SizedBox(height: 4),
          _followRow(),
          const SizedBox(height: 4),
          Text(
            widget.draft.isEmpty
                ? '지도를 눌러 차가 갈 경로점을 찍으세요 (최대 ${widget.config.maxPoints}개)'
                : '경로점 ${widget.draft.length}/${widget.config.maxPoints}개 — 차 위치에서 P1부터 차례로 갑니다',
            key: const Key('car-hint'),
            style: const TextStyle(color: Colors.black54, fontSize: 12),
          ),
          if (routeProblem != null && widget.draft.isNotEmpty)
            Text('⚠ $routeProblem',
                key: const Key('car-route-problem'),
                style: TextStyle(color: Colors.red.shade800, fontWeight: FontWeight.bold, fontSize: 12)),
          const SizedBox(height: 4),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  key: const Key('btn-car-undo'),
                  onPressed: widget.draft.isEmpty ? null : widget.onUndo,
                  child: const FittedBox(child: Text('마지막 점 삭제')),
                ),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: OutlinedButton(
                  key: const Key('btn-car-clear'),
                  onPressed: widget.draft.isEmpty ? null : widget.onClear,
                  child: const FittedBox(child: Text('경로 지우기')),
                ),
              ),
              const SizedBox(width: 6),
              Expanded(
                flex: 2,
                child: FilledButton.icon(
                  key: const Key('btn-car-send'),
                  style: FilledButton.styleFrom(backgroundColor: Colors.green.shade700),
                  onPressed: (widget.draft.isEmpty || routeProblem != null || _sending) ? null : _sendRoute,
                  icon: const Icon(Icons.route),
                  label: FittedBox(child: Text(_sending ? '보내는 중…' : '경로 출발')),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              CarJoystick(
                key: const Key('car-joystick'),
                size: 128,
                knob: _knob,
                enabled: connected && !blocked,
                onStart: _startDrive,
                onMove: _moveDrive,
                onEnd: () => _endDrive(sendStop: true),
              ),
              const SizedBox(width: 10),
              Expanded(child: _rightColumn(connected, blocked)),
            ],
          ),
        ],
      ),
    );
  }

  Widget _rightColumn(bool connected, bool blocked) {
    final (v, w) = _drive;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          _driveTimer == null ? '조이스틱: 정지' : '조이스틱: $v mm/s, ${w.toStringAsFixed(2)} rad/s',
          key: const Key('car-drive-readout'),
          style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
        ),
        Row(
          children: [
            const Text('최대 속도', style: TextStyle(fontSize: 12, color: Colors.black54)),
            Expanded(
              child: Slider(
                key: const Key('car-speed'),
                min: 30,
                max: _sliderMax,
                divisions: (((_sliderMax - 30) / 5).round()).clamp(1, 100).toInt(),
                value: _maxSpeed.clamp(30.0, _sliderMax).toDouble(),
                label: '${_maxSpeed.round()} mm/s',
                onChanged: (x) => setState(() => _maxSpeed = x),
              ),
            ),
            Text('${_maxSpeed.round()}', style: const TextStyle(fontSize: 12)),
          ],
        ),
        Row(
          children: [
            Expanded(
              child: FilledButton(
                key: const Key('btn-car-stop'),
                style: FilledButton.styleFrom(backgroundColor: Colors.amber.shade800),
                onPressed: connected ? () => _run('차 정지', _c.carStop) : null,
                child: const FittedBox(child: Text('차 정지')),
              ),
            ),
            const SizedBox(width: 6),
            Expanded(
              child: blocked
                  ? FilledButton(
                      key: const Key('btn-car-clear-estop'),
                      style: FilledButton.styleFrom(backgroundColor: Colors.blueGrey),
                      onPressed: connected ? () => _run('차 비상정지 해제', _c.carClearEstop) : null,
                      child: const FittedBox(child: Text('비상정지 해제')),
                    )
                  : FilledButton(
                      key: const Key('btn-car-estop'),
                      style: FilledButton.styleFrom(backgroundColor: Colors.red.shade800),
                      onPressed: connected
                          ? () {
                              _endDrive(sendStop: false);
                              _run('차 비상정지', _c.carEstop);
                            }
                          : null,
                      child: const FittedBox(child: Text('차 비상정지')),
                    ),
            ),
          ],
        ),
      ],
    );
  }

  /// 드론 따라가기 (docs/api.md v0.8 13.5). 서버가 시작 조건(비행 중·차가 카메라에 보임)을 최종 확인한다.
  Widget _followRow() {
    final following = widget.droneMode == 'following';
    final canStart = _c.online && widget.droneFlying && widget.droneMode == 'hovering';
    final String hint = following
        ? '드론이 차 뒤를 따라가는 중'
        : !widget.droneFlying
            ? '드론을 이륙시켜 차 위로 보낸 뒤 누르세요'
            : canStart
                ? '드론 카메라에 차가 보이면 따라갑니다'
                : '드론이 호버링 중일 때 누를 수 있습니다';
    return Row(
      children: [
        Expanded(
          child: Text(hint,
              key: const Key('follow-hint'),
              style: TextStyle(
                  fontSize: 12,
                  color: following ? Colors.green.shade800 : Colors.black54,
                  fontWeight: following ? FontWeight.bold : FontWeight.normal)),
        ),
        const SizedBox(width: 6),
        following
            ? FilledButton.icon(
                key: const Key('btn-follow-stop'),
                style: FilledButton.styleFrom(backgroundColor: Colors.amber.shade800),
                onPressed: _c.online ? () => _run('따라가기 멈춤', _c.stop) : null,
                icon: const Icon(Icons.pause),
                label: const Text('따라가기 멈춤'),
              )
            : FilledButton.icon(
                key: const Key('btn-follow-start'),
                style: FilledButton.styleFrom(backgroundColor: Colors.indigo),
                onPressed: canStart ? () => _run('드론 따라가기', _c.followStart) : null,
                icon: const Icon(Icons.flight),
                label: const Text('드론 따라가기'),
              ),
      ],
    );
  }

  Widget _statusLine(CarStatus? car, bool connected) {
    final String posText;
    final Color posColor;
    switch (car?.posSource) {
      case 'vision':
        posText = '드론 인식 중';
        posColor = Colors.green.shade800;
      case 'odom':
        final age = car!.seenAgeS;
        posText = '엔코더로 이어감${age == null ? '' : ' (${age.toStringAsFixed(0)}초 전 인식)'}';
        posColor = Colors.orange.shade900;
      default:
        posText = '위치 모름 — 드론을 차 위로';
        posColor = Colors.red.shade700;
    }
    final items = <(String, String, Color?)>[
      (
        '차',
        connected ? '연결됨' : (car?.linkMessage ?? '연결 안 됨'),
        connected ? Colors.green.shade800 : Colors.red.shade700
      ),
      ('상태', carStateKo(car?.state), (car?.blocked ?? false) ? Colors.red.shade700 : null),
      ('위치', posText, posColor),
      if (car != null && car.hasRoute && car.routeWp != null && car.routeN != null)
        ('경로', 'P${car.routeWp!.clamp(0, car.routeN! - 1)} / ${car.routeN! - 1}', null),
    ];
    return Wrap(
      key: const Key('car-status'),
      spacing: 12,
      runSpacing: 2,
      children: [
        for (final (label, value, color) in items)
          Text.rich(TextSpan(children: [
            TextSpan(text: '$label ', style: const TextStyle(color: Colors.black54)),
            TextSpan(text: value, style: TextStyle(fontWeight: FontWeight.bold, color: color)),
          ])),
      ],
    );
  }
}

/// 둥근 조이스틱. 누르고 있는 동안 손잡이 위치(가운데 0, 반지름 1, 화면 기준 오른쪽·아래 +)를 알려 준다.
class CarJoystick extends StatefulWidget {
  const CarJoystick({
    super.key,
    required this.size,
    required this.knob,
    required this.enabled,
    required this.onStart,
    required this.onMove,
    required this.onEnd,
  });

  final double size;
  final Offset knob;
  final bool enabled;
  final ValueChanged<Offset> onStart;
  final ValueChanged<Offset> onMove;
  final VoidCallback onEnd;

  @override
  State<CarJoystick> createState() => _CarJoystickState();
}

class _CarJoystickState extends State<CarJoystick> {
  int? _pointer;

  Offset _norm(Offset local) {
    final r = widget.size * 0.38;
    var d = (local - Offset(widget.size / 2, widget.size / 2)) / r;
    if (d.distance > 1) d = d / d.distance;
    return d;
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.size;
    final knobR = s * 0.17;
    final k = widget.knob * (s * 0.38);
    return Listener(
      onPointerDown: (e) {
        if (!widget.enabled || _pointer != null) return;
        _pointer = e.pointer;
        widget.onStart(_norm(e.localPosition));
      },
      onPointerMove: (e) {
        if (e.pointer == _pointer) widget.onMove(_norm(e.localPosition));
      },
      onPointerUp: (e) => _release(e.pointer),
      onPointerCancel: (e) => _release(e.pointer),
      child: SizedBox(
        width: s,
        height: s,
        child: Stack(
          children: [
            Positioned.fill(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: widget.enabled ? Colors.blueGrey.shade100 : Colors.grey.shade300,
                  border: Border.all(color: Colors.blueGrey.shade300, width: 2),
                ),
              ),
            ),
            const Align(alignment: Alignment(0, -0.92), child: Text('전진', style: TextStyle(fontSize: 10))),
            const Align(alignment: Alignment(0, 0.92), child: Text('후진', style: TextStyle(fontSize: 10))),
            const Align(alignment: Alignment(-0.92, 0), child: Text('좌', style: TextStyle(fontSize: 10))),
            const Align(alignment: Alignment(0.92, 0), child: Text('우', style: TextStyle(fontSize: 10))),
            Positioned(
              left: s / 2 + k.dx - knobR,
              top: s / 2 + k.dy - knobR,
              width: knobR * 2,
              height: knobR * 2,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: widget.enabled ? Colors.indigo : Colors.grey,
                  border: Border.all(color: Colors.white, width: 3),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _release(int pointer) {
    if (pointer != _pointer) return;
    _pointer = null;
    widget.onEnd();
  }
}
