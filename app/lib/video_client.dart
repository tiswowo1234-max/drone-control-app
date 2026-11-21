import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import 'drone_client.dart';

/// 실시간 카메라 영상 통로 `ws://<서버>:8000/ws/video` (docs/api.md v0.7 10.2).
///
/// - 영상 화면을 열 때만 [connect]하고, 닫을 때 [dispose]로 끊는다.
/// - 서버 → 앱: 바이너리 = JPEG 한 장, 텍스트 = `video_status`
/// - 연결이 끊기면 [reconnectDelay] 뒤 자동으로 다시 연결한다.
/// - 명령 통로(`/ws`, [DroneClient])와는 완전히 별개다. 영상이 끊겨도 비행·ping에는 영향이 없다.
class VideoClient extends ChangeNotifier {
  VideoClient({
    LinkConnector? connector,
    this.reconnectDelay = const Duration(seconds: 2),
  }) : _connector = connector ?? WebSocketLink.connect;

  final LinkConnector _connector;
  final Duration reconnectDelay;

  Uri? _uri;

  LinkStatus _status = LinkStatus.offline;
  LinkStatus get status => _status;

  /// 가장 최근 JPEG 한 장
  Uint8List? _frame;
  Uint8List? get frame => _frame;

  /// 받은 사진 수 (연결 이후 누적)
  int _frameCount = 0;
  int get frameCount => _frameCount;

  /// 서버가 알린 영상 상태 (`video_status.live`). 아직 모르면 null.
  bool? _live;
  bool? get live => _live;

  /// 서버가 알린 상태 문장
  String? _message;
  String? get message => _message;

  /// 최근 약 1초 동안 받은 사진 수 (0.5초마다 갱신)
  int _fps = 0;
  int get fps => _fps > 0 ? _fps : _bucketCur; // 첫 갱신 전에도 받는 중이면 0으로 보이지 않게

  // 0.5초 단위로 센 사진 수: 직전 칸 + 지금 칸 = 최근 1초
  int _bucketPrev = 0;
  int _bucketCur = 0;

  ServerLink? _link;
  StreamSubscription<dynamic>? _sub;
  Timer? _reconnectTimer;
  Timer? _fpsTimer;
  int _generation = 0;
  bool _disposed = false;

  void connect(Uri uri) {
    _uri = uri;
    // 초당 장수 표시를 갱신하고, 사진이 멈춰도 0으로 내려가게 한다
    _fpsTimer ??= Timer.periodic(const Duration(milliseconds: 500), (_) {
      if (_disposed) return;
      _fps = _bucketPrev + _bucketCur;
      _bucketPrev = _bucketCur;
      _bucketCur = 0;
      notifyListeners();
    });
    _restart();
  }

  void _restart() {
    _teardown();
    final gen = ++_generation;
    final uri = _uri;
    if (uri == null || _disposed) return;
    _setStatus(LinkStatus.connecting);
    _connector(uri).then((link) {
      if (gen != _generation || _disposed) {
        link.close();
        return;
      }
      _link = link;
      _sub = link.stream.listen(
        (raw) => _onMessage(gen, raw),
        onError: (Object e) => _onLost(gen),
        onDone: () => _onLost(gen),
        cancelOnError: true,
      );
      _setStatus(LinkStatus.online);
    }, onError: (Object e) {
      debugPrint('video connect failed: $e');
      _onLost(gen);
    });
  }

  void _onMessage(int gen, dynamic raw) {
    if (gen != _generation || _disposed) return;
    final bytes = switch (raw) {
      Uint8List b => b,
      ByteBuffer b => b.asUint8List(),
      List<int> b => Uint8List.fromList(b),
      _ => null,
    };
    if (bytes != null) {
      _frame = bytes;
      _frameCount++;
      _bucketCur++;
      notifyListeners();
      return;
    }
    if (raw is String) {
      try {
        final j = jsonDecode(raw);
        if (j is Map && j['type'] == 'video_status') {
          _live = j['live'] == true;
          _message = j['message']?.toString();
          notifyListeners();
        }
      } on FormatException {
        // 모르는 텍스트는 무시
      }
    }
  }

  void _onLost(int gen) {
    if (gen != _generation || _disposed) return;
    _teardown();
    _generation++;
    _setStatus(LinkStatus.offline);
    _reconnectTimer = Timer(reconnectDelay, _restart);
  }

  void _teardown() {
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _sub?.cancel();
    _sub = null;
    _link?.close();
    _link = null;
  }

  void _setStatus(LinkStatus s) {
    if (_status == s) return;
    _status = s;
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _generation++;
    _teardown();
    _fpsTimer?.cancel();
    super.dispose();
  }
}
