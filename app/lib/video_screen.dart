import 'dart:async';

import 'package:flutter/material.dart';

import 'drone_client.dart';
import 'protocol.dart';
import 'server_address.dart';
import 'video_client.dart';

/// 드론 카메라 실시간 영상 화면 (docs/api.md v0.7 10.2).
///
/// 이 화면이 열려 있는 동안만 `/ws/video`에 연결하고, 닫으면 끊는다.
/// 명령 통로(`/ws`)는 [DroneClient]가 계속 유지하므로 여기서도 정지·착륙을 바로 보낼 수 있다.
class VideoScreen extends StatefulWidget {
  const VideoScreen({super.key, required this.client, this.videoConnector});

  final DroneClient client;

  /// 테스트용 가짜 연결. null이면 실제 WebSocket.
  final LinkConnector? videoConnector;

  @override
  State<VideoScreen> createState() => _VideoScreenState();
}

class _VideoScreenState extends State<VideoScreen> {
  late final VideoClient _video;
  DroneClient get _c => widget.client;

  /// 명령 결과 알림 (문장, 색)
  (String, Color)? _notice;
  Timer? _noticeTimer;

  @override
  void initState() {
    super.initState();
    _video = VideoClient(connector: widget.videoConnector);
    final uri = _c.uri;
    if (uri != null) _video.connect(ServerAddress.videoUrl(uri));
  }

  @override
  void dispose() {
    _noticeTimer?.cancel();
    _video.dispose(); // 화면을 닫으면 영상 연결을 끊는다
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([_c, _video]),
      builder: (context, _) {
        final st = _c.state;
        final mode = st?.vision?.mode;
        return Scaffold(
          backgroundColor: Colors.black,
          appBar: AppBar(
            title: Text('드론 카메라 · ${visionModeKo(mode)}'),
          ),
          body: SafeArea(
            child: Column(
              children: [
                if (!_c.online)
                  Container(
                    key: const Key('video-cmd-offline'),
                    width: double.infinity,
                    color: Colors.red.shade700,
                    padding: const EdgeInsets.all(8),
                    child: const Text('서버 명령 연결 끊김 — 자동 재연결 중',
                        style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
                  ),
                Expanded(
                  child: Stack(
                    children: [
                      Positioned.fill(child: _picture()),
                      Positioned(left: 8, top: 8, child: _statusChip(mode)),
                      if (_notice != null)
                        Positioned(left: 8, right: 8, bottom: 8, child: _noticeBox(_notice!)),
                    ],
                  ),
                ),
                _droneLine(st),
                _buttons(),
              ],
            ),
          ),
        );
      },
    );
  }

  /// 영상. 흑백 320×240처럼 작은 사진도 화면 폭에 맞춰 크게 보여 주고, 두 손가락으로 더 확대할 수 있다.
  Widget _picture() {
    final f = _video.frame;
    if (f == null) {
      return Center(
        child: Text(
          _video.status == LinkStatus.online
              ? (_video.message ?? '영상을 기다리는 중…')
              : '영상 서버에 연결하는 중…',
          key: const Key('video-empty'),
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.white70, fontSize: 16),
        ),
      );
    }
    return InteractiveViewer(
      maxScale: 6,
      child: Center(
        child: Image.memory(
          f,
          key: const Key('video-frame'),
          gaplessPlayback: true, // 다음 장을 그리는 동안 이전 장을 계속 보여 줘 깜빡이지 않게
          fit: BoxFit.contain,
          width: double.infinity,
          height: double.infinity,
          filterQuality: FilterQuality.medium,
          errorBuilder: (context, error, stack) => const Center(
            child: Text('영상 사진을 읽지 못했습니다',
                style: TextStyle(color: Colors.white70)),
          ),
        ),
      ),
    );
  }

  Widget _statusChip(String? mode) {
    final online = _video.status == LinkStatus.online;
    final live = online && _video.live != false && _video.fps > 0;
    final String text;
    final Color color;
    if (!online) {
      text = _video.status == LinkStatus.connecting ? '영상 연결 중…' : '영상 끊김 — 다시 연결 중';
      color = Colors.red.shade700;
    } else if (_video.live == false) {
      text = '영상 없음${_video.message != null ? ': ${_video.message}' : ''}';
      color = Colors.orange.shade800;
    } else if (live) {
      text = '● 실시간 ${_video.fps}장/초';
      color = Colors.green.shade700;
    } else {
      text = '영상 대기 중 (0장/초)';
      color = Colors.orange.shade800;
    }
    return Container(
      key: const Key('video-status'),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.9),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(text,
              style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
          Text('위치 카메라: ${visionModeKo(mode)}',
              style: const TextStyle(color: Colors.white, fontSize: 12)),
        ],
      ),
    );
  }

  Widget _droneLine(DroneState? st) {
    final pos = st?.pos;
    final bat = st?.batteryPct;
    return Container(
      width: double.infinity,
      color: Colors.grey.shade900,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Text(
        '드론 ${st == null ? '-' : modeKo(st.mode)}'
        '  ·  위치 ${pos == null ? '모름' : 'x ${pos.xCm.toStringAsFixed(0)}, y ${pos.yCm.toStringAsFixed(0)}'}'
        '  ·  고도 ${pos == null ? '-' : '${pos.zCm.toStringAsFixed(0)} cm'}'
        '  ·  배터리 ${bat == null ? '-' : '${bat.toStringAsFixed(0)}%'}',
        style: const TextStyle(color: Colors.white70),
      ),
    );
  }

  Widget _buttons() {
    Widget btn(Key key, String label, IconData icon, Color color, Future<CmdResult> Function() send) {
      return Expanded(
        child: SizedBox(
          height: 56,
          child: FilledButton.icon(
            key: key,
            style: FilledButton.styleFrom(
              backgroundColor: color,
              textStyle: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            onPressed: () => _run(label, send),
            icon: Icon(icon),
            label: Text(label),
          ),
        ),
      );
    }

    return Container(
      color: Colors.black,
      padding: const EdgeInsets.all(8),
      child: Row(
        children: [
          btn(const Key('video-btn-stop'), '정지', Icons.pan_tool, Colors.amber.shade800, _c.stop),
          const SizedBox(width: 8),
          btn(const Key('video-btn-land'), '착륙', Icons.flight_land, Colors.blue.shade700, _c.land),
        ],
      ),
    );
  }

  Future<void> _run(String name, Future<CmdResult> Function() send) async {
    final r = await send();
    if (!mounted) return;
    _noticeTimer?.cancel();
    setState(() => _notice = (
          r.ok ? '$name 명령을 보냈습니다' : '$name 실패: ${r.koreanMessage}',
          r.ok ? Colors.green.shade800 : Colors.red.shade800,
        ));
    _noticeTimer = Timer(const Duration(seconds: 4), () {
      if (mounted) setState(() => _notice = null);
    });
  }

  Widget _noticeBox((String, Color) n) {
    final (text, color) = n;
    return Material(
      key: const Key('video-notice'),
      color: color,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        child: Text(text, style: const TextStyle(color: Colors.white, fontSize: 16)),
      ),
    );
  }
}
