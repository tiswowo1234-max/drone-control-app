import 'dart:async';

import 'package:flutter/material.dart';

import 'config.dart';
import 'drone_client.dart';
import 'geo.dart';
import 'map_view.dart';
import 'protocol.dart';
import 'safety.dart';
import 'server_address.dart';

/// 메인 화면: 지도 + 드론 상태 + 출동 + 제어 버튼
class MapScreen extends StatefulWidget {
  const MapScreen({super.key, required this.client});

  final DroneClient client;

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  DroneClient get _c => widget.client;

  /// 출동 목표 마커 위치 (cm). null이면 출동 모드가 아님.
  Offset? _marker;

  /// 명령을 보내고 응답을 기다리는 중
  bool _sending = false;

  StreamSubscription<DroneEvent>? _eventSub;

  @override
  void initState() {
    super.initState();
    _eventSub = _c.events.listen(_showEvent);
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    _noticeTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: _c,
      builder: (context, _) {
        final config = _c.config;
        final st = _c.state;
        final markerIssue = _marker == null ? null : checkTarget(config, _marker!);
        return Scaffold(
          appBar: AppBar(
            title: const Text('캠퍼스 경비 드론'),
            backgroundColor: Theme.of(context).colorScheme.inversePrimary,
            actions: [
              IconButton(
                tooltip: '서버 주소 설정',
                icon: const Icon(Icons.settings),
                onPressed: _editServerAddress,
              ),
            ],
          ),
          body: SafeArea(
            child: Column(
              children: [
                _banner(st),
                Expanded(
                  child: Stack(
                    children: [
                      Positioned.fill(
                        child: MapView(
                          config: config,
                          state: st,
                          droneFresh: _c.online,
                          marker: _marker,
                          markerOk: markerIssue == null,
                          onMarkerMoved: _marker == null
                              ? null
                              : (cm) => setState(() => _marker = cm),
                        ),
                      ),
                      // 알림은 지도 위쪽에 띄운다 (아래쪽 제어 버튼을 가리지 않도록)
                      if (_notice != null)
                        Positioned(
                          left: 8,
                          right: 8,
                          top: 8,
                          child: _noticeBox(_notice!),
                        ),
                    ],
                  ),
                ),
                _statusPanel(config, st),
                if (_marker != null)
                  _dispatchPanel(config, st, markerIssue)
                else
                  _mainButtons(config),
                _controlButtons(),
              ],
            ),
          ),
        );
      },
    );
  }

  // ---------------------------------------------------------------- 경고 배너

  Widget _banner(DroneState? st) {
    if (!_c.online) {
      final connecting = _c.status == LinkStatus.connecting;
      return _bannerBox(
        key: const Key('offline-banner'),
        color: Colors.red.shade700,
        icon: Icons.wifi_off,
        title: connecting ? '서버에 연결하는 중…' : '서버 연결 끊김 — 자동 재연결 중',
        detail: '${_c.uri ?? '주소 없음'}'
            '${_c.lastError != null ? '\n${_c.lastError}' : ''}',
      );
    }
    if (st != null && !st.connected) {
      return _bannerBox(
        key: const Key('drone-offline-banner'),
        color: Colors.orange.shade800,
        icon: Icons.link_off,
        title: '드론(시뮬레이터)이 연결되어 있지 않습니다',
        detail: '서버는 연결됨. 드론 전원·Wi-Fi 또는 시뮬레이터 실행을 확인하세요.',
      );
    }
    return const SizedBox.shrink();
  }

  Widget _bannerBox({
    required Key key,
    required Color color,
    required IconData icon,
    required String title,
    required String detail,
  }) {
    return Container(
      key: key,
      width: double.infinity,
      color: color,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      child: Row(
        children: [
          Icon(icon, color: Colors.white, size: 32),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title,
                    style: const TextStyle(
                        color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold)),
                Text(detail,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Colors.white70, fontSize: 12)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------- 상태 패널

  Widget _statusPanel(AppConfig config, DroneState? st) {
    final pos = st?.pos;
    final bat = st?.batteryPct;
    final batLow = bat != null && bat < config.limits.minBatteryPct;
    final backend = switch (config.backend) {
      'sim' => '시뮬레이터',
      'real' => '실제 드론',
      'mock' => '가짜 드론(mock)',
      _ => '-',
    };
    final serverText = switch (_c.status) {
      LinkStatus.online => '연결됨',
      LinkStatus.connecting => '연결 중',
      LinkStatus.offline => '끊김',
    };
    final stale = !_c.online && st != null; // 끊긴 뒤 남아 있는 마지막 값

    final items = <(String, String, Color?)>[
      ('서버', serverText, _c.online ? Colors.green.shade800 : Colors.red),
      ('드론', st == null ? '-' : (st.connected ? '연결됨' : '끊김'),
          st?.connected == true ? Colors.green.shade800 : Colors.red),
      ('모드', st == null ? '-' : modeKo(st.mode), null),
      (
        '위치',
        pos == null
            ? '위치 모름'
            : 'x ${pos.xCm.toStringAsFixed(0)}, y ${pos.yCm.toStringAsFixed(0)} cm',
        pos == null ? Colors.red : null
      ),
      ('위치 출처', posSourceKo(st?.posSource), null),
      ('고도', pos == null ? '-' : '${pos.zCm.toStringAsFixed(0)} cm', null),
      ('배터리', bat == null ? '-' : '${bat.toStringAsFixed(0)}%', batLow ? Colors.red : null),
      ('백엔드', backend, null),
    ];

    return Container(
      key: const Key('status-panel'),
      width: double.infinity,
      color: stale ? Colors.grey.shade300 : Colors.grey.shade100,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Wrap(
        spacing: 14,
        runSpacing: 2,
        children: [
          for (final (label, value, color) in items)
            Text.rich(TextSpan(children: [
              TextSpan(text: '$label ', style: const TextStyle(color: Colors.black54)),
              TextSpan(
                text: value,
                style: TextStyle(fontWeight: FontWeight.bold, color: color),
              ),
            ])),
          if (stale)
            const Text('(마지막으로 받은 값)', style: TextStyle(color: Colors.black54)),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------- 출동

  Widget _mainButtons(AppConfig config) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(8, 8, 8, 0),
      child: Row(
        children: [
          Expanded(
            child: _bigButton(
              key: const Key('btn-dispatch'),
              label: '출동',
              icon: Icons.flight_takeoff,
              color: Colors.indigo,
              onPressed: () => setState(() => _marker = Offset(
                  config.mapWidthCm / 2, config.mapHeightCm / 2)),
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(
            height: 56,
            child: OutlinedButton.icon(
              key: const Key('btn-emergency'),
              style: OutlinedButton.styleFrom(
                foregroundColor: Colors.red.shade800,
                side: BorderSide(color: Colors.red.shade800, width: 2),
              ),
              onPressed: _confirmEmergency,
              icon: const Icon(Icons.dangerous),
              label: const Text('비상 정지'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _dispatchPanel(AppConfig config, DroneState? st, TargetIssue? issue) {
    final m = _marker!;
    final ll = cmToLatLon(config, m);
    final notReady = checkDispatchReady(
      config: config,
      serverOnline: _c.online,
      droneConnected: st?.connected ?? false,
      batteryPct: st?.batteryPct,
    );
    final problem = issue?.message ?? notReady;
    return Container(
      key: const Key('dispatch-panel'),
      color: problem == null ? Colors.green.shade50 : Colors.red.shade50,
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('마커를 끌거나 지도를 눌러 목표 지점을 정하세요',
              style: TextStyle(color: Colors.black54)),
          Text(
            '목표: x ${m.dx.toStringAsFixed(1)} cm, y ${m.dy.toStringAsFixed(1)} cm',
            key: const Key('marker-coords'),
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
          ),
          Text('위도·경도(참고): ${ll.format()}',
              style: const TextStyle(color: Colors.black87)),
          if (problem != null)
            Text('⚠ $problem',
                key: const Key('dispatch-problem'),
                style: TextStyle(
                    color: Colors.red.shade800, fontWeight: FontWeight.bold)),
          const SizedBox(height: 6),
          Row(
            children: [
              Expanded(
                child: _bigButton(
                  key: const Key('btn-cancel'),
                  label: '취소',
                  icon: Icons.close,
                  color: Colors.grey.shade700,
                  onPressed: _sending ? null : () => setState(() => _marker = null),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                flex: 2,
                child: _bigButton(
                  key: const Key('btn-confirm'),
                  label: _sending ? '전송 중…' : '확인 (이 지점으로 비행)',
                  icon: Icons.check,
                  color: Colors.green.shade700,
                  onPressed: (problem != null || _sending) ? null : _confirmDispatch,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _confirmDispatch() async {
    final m = _marker;
    if (m == null) return;
    setState(() => _sending = true);
    final r = await _c.goto(
      double.parse(m.dx.toStringAsFixed(1)),
      double.parse(m.dy.toStringAsFixed(1)),
    );
    if (!mounted) return;
    setState(() {
      _sending = false;
      if (r.ok) _marker = null; // 성공하면 출동 모드 종료, 실패하면 마커 유지
    });
    _showResult('출동', r);
  }

  // ---------------------------------------------------------------- 제어 버튼

  Widget _controlButtons() {
    return Padding(
      padding: const EdgeInsets.all(8),
      child: Row(
        children: [
          Expanded(
            child: _bigButton(
              key: const Key('btn-stop'),
              label: '정지',
              icon: Icons.pan_tool,
              color: Colors.amber.shade800,
              onPressed: () => _run('정지(호버링)', _c.stop),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: _bigButton(
              key: const Key('btn-land'),
              label: '착륙',
              icon: Icons.flight_land,
              color: Colors.blue.shade700,
              onPressed: () => _run('착륙', _c.land),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: _bigButton(
              key: const Key('btn-home'),
              label: '귀환',
              icon: Icons.home,
              color: Colors.teal.shade700,
              onPressed: () => _run('귀환', _c.returnHome),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _run(String name, Future<CmdResult> Function() send) async {
    final r = await send();
    if (mounted) _showResult(name, r);
  }

  Future<void> _confirmEmergency() async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        icon: const Icon(Icons.warning, color: Colors.red, size: 48),
        title: const Text('비상 정지'),
        content: const Text(
          '모터가 즉시 멈추고 드론이 그 자리에서 떨어집니다.\n'
          '사람이 다치거나 드론이 부서질 위험이 있을 때만 사용하세요.\n\n'
          '정말 실행할까요?',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('취소', style: TextStyle(fontSize: 18)),
          ),
          FilledButton(
            key: const Key('btn-emergency-confirm'),
            style: FilledButton.styleFrom(backgroundColor: Colors.red.shade800),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('모터 정지', style: TextStyle(fontSize: 18)),
          ),
        ],
      ),
    );
    if (yes == true) await _run('비상 정지', _c.emergency);
  }

  Widget _bigButton({
    required Key key,
    required String label,
    required IconData icon,
    required Color color,
    required VoidCallback? onPressed,
  }) {
    return SizedBox(
      height: 56,
      child: FilledButton.icon(
        key: key,
        style: FilledButton.styleFrom(
          backgroundColor: color,
          textStyle: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        onPressed: onPressed,
        icon: Icon(icon),
        label: FittedBox(child: Text(label)),
      ),
    );
  }

  // ---------------------------------------------------------------- 알림

  void _showResult(String name, CmdResult r) {
    _snack(
      r.ok ? '$name 명령을 보냈습니다' : '$name 실패: ${r.koreanMessage}',
      r.ok ? Colors.green.shade800 : Colors.red.shade800,
    );
  }

  void _showEvent(DroneEvent e) {
    final color = switch (e.level) {
      'error' => Colors.red.shade800,
      'warn' => Colors.orange.shade800,
      _ => Colors.blueGrey.shade700,
    };
    _snack(e.message.isEmpty ? e.code : e.message, color);
  }

  /// 지금 보여 주는 알림 (문장, 색)
  (String, Color)? _notice;
  Timer? _noticeTimer;

  void _snack(String text, Color color) {
    _noticeTimer?.cancel();
    setState(() => _notice = (text, color));
    _noticeTimer = Timer(_noticeDuration, () {
      if (mounted) setState(() => _notice = null);
    });
  }

  static const _noticeDuration = Duration(seconds: 4);

  Widget _noticeBox((String, Color) n) {
    final (text, color) = n;
    return Material(
      key: const Key('notice'),
      color: color,
      elevation: 4,
      borderRadius: BorderRadius.circular(8),
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: () => setState(() => _notice = null), // 누르면 닫힘
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          child: Text(text,
              style: const TextStyle(color: Colors.white, fontSize: 16)),
        ),
      ),
    );
  }

  // ---------------------------------------------------------------- 서버 주소

  Future<void> _editServerAddress() async {
    final ctl = TextEditingController(
        text: _c.uri?.toString() ?? ServerAddress.defaultUrl);
    String? error;
    final url = await showDialog<String>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setLocal) => AlertDialog(
          title: const Text('서버 주소'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('중계 서버를 실행한 PC(또는 라즈베리파이)의 IP를 넣으세요.\n'
                  '예: 192.168.0.10  →  ws://192.168.0.10:8000/ws\n'
                  'PC 브라우저에서 같은 PC의 서버에 붙을 때는 127.0.0.1'),
              const SizedBox(height: 12),
              TextField(
                key: const Key('server-url-field'),
                controller: ctl,
                autofocus: true,
                keyboardType: TextInputType.url,
                decoration: InputDecoration(
                  border: const OutlineInputBorder(),
                  errorText: error,
                ),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => ctl.text = ServerAddress.defaultUrl,
              child: const Text('기본값'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('취소'),
            ),
            FilledButton(
              key: const Key('server-url-save'),
              onPressed: () {
                final u = ServerAddress.normalize(ctl.text);
                if (u == null) {
                  setLocal(() => error = '주소 형식이 올바르지 않습니다');
                } else {
                  Navigator.pop(ctx, u);
                }
              },
              child: const Text('저장 후 연결'),
            ),
          ],
        ),
      ),
    );
    if (url == null) return;
    await ServerAddress.save(url);
    _c.connect(Uri.parse(url));
  }
}
