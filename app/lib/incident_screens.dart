import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'drone_client.dart';
import 'geo.dart';
import 'protocol.dart';
import 'server_address.dart';

/// 시각 표시: "15:04:09" (오늘이 아니면 "11-20 15:04:09")
String formatIncidentTime(DateTime? t, {DateTime? now}) {
  if (t == null) return '시각 모름';
  String two(int v) => v.toString().padLeft(2, '0');
  final hms = '${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
  final n = now ?? DateTime.now();
  final sameDay = t.year == n.year && t.month == n.month && t.day == n.day;
  return sameDay ? hms : '${two(t.month)}-${two(t.day)} $hms';
}

/// 사건 위치의 위도·경도 문장. 서버 값이 없으면 지도 좌표로 계산한다.
String incidentLatLonText(DroneClient c, Incident i) {
  if (i.lat != null && i.lon != null) return LatLon(i.lat!, i.lon!).format();
  if (i.pos != null) return cmToLatLon(c.config, i.pos!).format();
  return '위치 모름';
}

/// 사건 목록 화면 (docs/api.md 3.6)
class IncidentListScreen extends StatelessWidget {
  const IncidentListScreen({super.key, required this.client});

  final DroneClient client;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: client,
      builder: (context, _) {
        final items = client.incidents;
        return Scaffold(
          appBar: AppBar(
            title: Text('사건 목록 (미확인 ${client.unackedCount})'),
            actions: [
              TextButton(
                key: const Key('btn-ack-all'),
                onPressed: client.unackedCount == 0 ? null : client.ackAll,
                child: const Text('모두 확인'),
              ),
            ],
          ),
          body: items.isEmpty
              ? const Center(
                  child: Text('받은 사건이 없습니다\n(서버를 다시 켜면 목록이 비워집니다)',
                      textAlign: TextAlign.center),
                )
              : ListView.separated(
                  itemCount: items.length,
                  separatorBuilder: (_, _) => const Divider(height: 1),
                  itemBuilder: (context, n) => _tile(context, items[n]),
                ),
        );
      },
    );
  }

  Widget _tile(BuildContext context, Incident i) {
    final acked = client.isAcked(i);
    return ListTile(
      key: Key('incident-${i.key}'),
      leading: Icon(
        acked ? Icons.check_circle : Icons.warning_amber_rounded,
        color: acked ? Colors.grey : Colors.red.shade700,
        size: 36,
      ),
      title: Text(
        '${formatIncidentTime(i.time)}  ${i.placeText}',
        style: TextStyle(fontWeight: acked ? FontWeight.normal : FontWeight.bold),
      ),
      subtitle: Text(
        '위경도 ${incidentLatLonText(client, i)}\n'
        '카메라 ${visionModeKo(i.mode)} · ${acked ? '확인함' : '미확인'}',
      ),
      isThreeLine: true,
      trailing: const Icon(Icons.chevron_right),
      onTap: () => openIncidentDetail(context, client, i),
    );
  }
}

void openIncidentDetail(BuildContext context, DroneClient client, Incident i) {
  Navigator.of(context).push(MaterialPageRoute<void>(
    builder: (_) => IncidentDetailScreen(client: client, incident: i),
  ));
}

/// 사건 상세 화면: 사진(확대 가능), 위치, 판별 결과, 확인 표시
class IncidentDetailScreen extends StatelessWidget {
  const IncidentDetailScreen({super.key, required this.client, required this.incident});

  final DroneClient client;
  final Incident incident;

  @override
  Widget build(BuildContext context) {
    final i = incident;
    return ListenableBuilder(
      listenable: client,
      builder: (context, _) {
        final acked = client.isAcked(i);
        return Scaffold(
          appBar: AppBar(title: Text('사건 ${i.placeText}')),
          body: ListView(
            padding: const EdgeInsets.all(12),
            children: [
              _photo(context),
              const SizedBox(height: 4),
              const Text('사진을 누르면 크게 볼 수 있습니다 (두 손가락으로 확대)',
                  style: TextStyle(color: Colors.black54, fontSize: 12)),
              const SizedBox(height: 12),
              _detection(i.detection),
              const SizedBox(height: 12),
              _row('시각', formatIncidentTime(i.time)),
              _row('장소', i.placeText),
              _row('사진 위치',
                  i.pos == null ? '모름' : 'x ${i.pos!.dx.toStringAsFixed(1)}, y ${i.pos!.dy.toStringAsFixed(1)} cm'),
              _row('위도·경도', incidentLatLonText(client, i)),
              _row(
                  '드론 위치',
                  i.dronePos == null
                      ? '모름'
                      : 'x ${i.dronePos!.dx.toStringAsFixed(1)}, y ${i.dronePos!.dy.toStringAsFixed(1)} cm'
                          '${i.droneZCm == null ? '' : ', 고도 ${i.droneZCm!.toStringAsFixed(0)} cm'}'),
              _row('카메라', visionModeKo(i.mode)),
              _row('방식', incidentMethodKo(i.method)),
              _row('사건 번호', i.id),
              const SizedBox(height: 16),
              SizedBox(
                height: 56,
                child: acked
                    ? OutlinedButton.icon(
                        key: const Key('btn-unack'),
                        onPressed: () => client.setAcked(i, false),
                        icon: const Icon(Icons.undo),
                        label: const Text('확인함 (누르면 미확인으로)'),
                      )
                    : FilledButton.icon(
                        key: const Key('btn-ack'),
                        style: FilledButton.styleFrom(
                          textStyle: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                        ),
                        onPressed: () => client.setAcked(i, true),
                        icon: const Icon(Icons.check),
                        label: const Text('확인 완료로 표시'),
                      ),
              ),
            ],
          ),
        );
      },
    );
  }

  Uri? get _photoUri {
    final ws = client.uri;
    final p = incident.photoUrl;
    if (ws == null || p == null || p.isEmpty) return null;
    return ServerAddress.httpUrl(ws, p);
  }

  Widget _photo(BuildContext context) {
    final uri = _photoUri;
    if (uri == null) return _noPhoto();
    final img = Image.network(
      uri.toString(),
      key: const Key('incident-photo'),
      fit: BoxFit.contain,
      filterQuality: FilterQuality.medium, // 작은 사진을 키워도 덜 뭉개지게
      loadingBuilder: (context, child, progress) => progress == null
          ? child
          : const Center(child: CircularProgressIndicator()),
      errorBuilder: (context, error, stack) => _noPhoto(),
    );
    return AspectRatio(
      aspectRatio: 4 / 3,
      child: GestureDetector(
        onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => _PhotoViewer(url: uri.toString()),
        )),
        child: Container(color: Colors.black, child: img),
      ),
    );
  }

  Widget _noPhoto() => Container(
        key: const Key('incident-no-photo'),
        height: 160,
        color: Colors.grey.shade300,
        alignment: Alignment.center,
        child: const Text('사진 없음\n(서버를 다시 켰다면 사진이 지워졌습니다)',
            textAlign: TextAlign.center),
      );

  Widget _detection(Detection? d) {
    if (d == null) {
      return Container(
        key: const Key('detection-none'),
        padding: const EdgeInsets.all(12),
        color: Colors.amber.shade100,
        child: const Text('확인 필요 (AI 판별 꺼짐) — 사진을 직접 보고 판단하세요',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
      );
    }
    final abnormal = d.isAbnormal;
    return Container(
      key: const Key('detection'),
      padding: const EdgeInsets.all(12),
      color: abnormal == true ? Colors.red.shade100 : Colors.green.shade100,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            abnormal == null ? 'AI 판별: 이상 여부 모름' : (abnormal ? 'AI 판별: 이상 있음' : 'AI 판별: 이상 없음'),
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
          ),
          Text('종류 ${d.category ?? '-'} · 긴급도 ${urgencyKo(d.urgency)}'
              '${d.needsHumanReview == true ? ' · 사람 확인 필요' : ''}'),
          if (d.description != null) Text(d.description!),
        ],
      ),
    );
  }

  Widget _row(String label, String value) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
                width: 88,
                child: Text(label, style: const TextStyle(color: Colors.black54))),
            Expanded(child: Text(value, style: const TextStyle(fontWeight: FontWeight.bold))),
          ],
        ),
      );
}

/// 사진 전체 화면 (확대·이동)
class _PhotoViewer extends StatelessWidget {
  const _PhotoViewer({required this.url});

  final String url;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(title: const Text('사건 사진')),
      body: InteractiveViewer(
        maxScale: 10,
        child: Center(
          child: Image.network(
            url,
            fit: BoxFit.contain,
            width: double.infinity,
            height: double.infinity,
            filterQuality: FilterQuality.medium,
            errorBuilder: (context, error, stack) => const Text('사진 없음',
                style: TextStyle(color: Colors.white70)),
          ),
        ),
      ),
    );
  }
}

/// 새 사건이 오면 어느 화면에서든 위쪽에 크게 알리고 진동한다.
/// `MaterialApp.builder`에서 모든 화면을 감싼다.
class IncidentAlertOverlay extends StatefulWidget {
  const IncidentAlertOverlay({
    super.key,
    required this.client,
    required this.navigatorKey,
    required this.child,
  });

  final DroneClient client;
  final GlobalKey<NavigatorState> navigatorKey;
  final Widget child;

  @override
  State<IncidentAlertOverlay> createState() => _IncidentAlertOverlayState();
}

class _IncidentAlertOverlayState extends State<IncidentAlertOverlay> {
  StreamSubscription<Incident>? _sub;
  Incident? _shown;
  Timer? _timer;

  static const showFor = Duration(seconds: 10);

  @override
  void initState() {
    super.initState();
    _sub = widget.client.newIncidents.listen(_onIncident);
  }

  @override
  void dispose() {
    _sub?.cancel();
    _timer?.cancel();
    super.dispose();
  }

  void _onIncident(Incident i) {
    // 진동·알림음 (지원하지 않는 기기·브라우저에서는 조용히 무시)
    HapticFeedback.vibrate().catchError((_) {});
    SystemSound.play(SystemSoundType.alert).catchError((_) {});
    _timer?.cancel();
    setState(() => _shown = i);
    _timer = Timer(showFor, () {
      if (mounted) setState(() => _shown = null);
    });
  }

  @override
  Widget build(BuildContext context) {
    final i = _shown;
    return Stack(
      children: [
        widget.child,
        if (i != null)
          Positioned(
            left: 8,
            right: 8,
            top: 0,
            child: SafeArea(
              child: Material(
                key: const Key('incident-alert'),
                color: Colors.red.shade700,
                elevation: 8,
                borderRadius: BorderRadius.circular(10),
                child: InkWell(
                  borderRadius: BorderRadius.circular(10),
                  onTap: () {
                    setState(() => _shown = null);
                    final nav = widget.navigatorKey.currentState;
                    if (nav != null) {
                      nav.push(MaterialPageRoute<void>(
                        builder: (_) => IncidentDetailScreen(client: widget.client, incident: i),
                      ));
                    }
                  },
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Row(
                      children: [
                        const Icon(Icons.notification_important, color: Colors.white, size: 40),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('새 사건: ${i.placeText}',
                                  style: const TextStyle(
                                      color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold)),
                              Text(
                                '${formatIncidentTime(i.time)} · ${visionModeKo(i.mode)} · 눌러서 사진 보기',
                                style: const TextStyle(color: Colors.white70),
                              ),
                            ],
                          ),
                        ),
                        // 이 알림은 Navigator 바깥에 있어 Tooltip(=IconButton)을 쓸 수 없다
                        InkResponse(
                          key: const Key('incident-alert-close'),
                          onTap: () => setState(() => _shown = null),
                          child: const Padding(
                            padding: EdgeInsets.all(8),
                            child: Icon(Icons.close, color: Colors.white),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}
