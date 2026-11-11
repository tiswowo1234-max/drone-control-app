import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'config.dart';
import 'protocol.dart';

/// 서버와의 연결 하나. 실제로는 WebSocket, 테스트에서는 가짜 채널을 쓴다.
abstract class ServerLink {
  Stream<dynamic> get stream;
  void send(String text);
  Future<void> close();
}

/// 주소를 받아 연결을 여는 함수. 실패하면 예외를 던진다.
typedef LinkConnector = Future<ServerLink> Function(Uri uri);

/// web_socket_channel을 쓰는 실제 연결
class WebSocketLink implements ServerLink {
  WebSocketLink._(this._ch);

  final WebSocketChannel _ch;

  static Future<ServerLink> connect(Uri uri) async {
    final ch = WebSocketChannel.connect(uri);
    await ch.ready; // 연결 실패 시 여기서 예외
    return WebSocketLink._(ch);
  }

  @override
  Stream<dynamic> get stream => _ch.stream;

  @override
  void send(String text) => _ch.sink.add(text);

  @override
  Future<void> close() => _ch.sink.close();
}

enum LinkStatus {
  /// 연결 시도 중
  connecting,

  /// 연결됨, 서버 메시지 수신 중
  online,

  /// 끊김 (곧 자동 재연결)
  offline,
}

/// 명령 결과. 서버 ack 또는 앱 자체 오류(전송 실패, 응답 없음).
class CmdResult {
  const CmdResult(this.ok, {this.error, this.message});

  final bool ok;
  final String? error;
  final String? message;

  String get koreanMessage => errorMessageKo(error, message);
}

/// 중계 서버와의 통신을 맡는다.
///
/// - 연결이 끊기면 [reconnectDelay] 뒤 자동 재연결
/// - [pingInterval]마다 `ping` 전송 (docs/api.md 4.2: 1초)
/// - 서버 메시지가 `link_timeout_s` 동안 없으면 끊긴 것으로 보고 다시 연결
/// - `config`를 받으면 [config]를 그 값으로 바꾼다
class DroneClient extends ChangeNotifier {
  DroneClient({
    LinkConnector? connector,
    this.pingInterval = const Duration(seconds: 1),
    this.reconnectDelay = const Duration(seconds: 2),
    this.ackTimeout = const Duration(seconds: 3),
  }) : _connector = connector ?? WebSocketLink.connect;

  final LinkConnector _connector;
  final Duration pingInterval;
  final Duration reconnectDelay;
  final Duration ackTimeout;

  Uri? _uri;
  Uri? get uri => _uri;

  AppConfig _config = AppConfig.fallback;
  AppConfig get config => _config;

  LinkStatus _status = LinkStatus.offline;
  LinkStatus get status => _status;
  bool get online => _status == LinkStatus.online;

  /// 가장 최근 state (연결이 끊겨도 마지막 값은 남겨 둔다)
  DroneState? _state;
  DroneState? get state => _state;

  /// 마지막 연결 실패 이유 (화면 표시용)
  String? _lastError;
  String? get lastError => _lastError;

  /// 서버 event 알림. 화면이 구독해 스낵바로 보여 준다.
  final _events = StreamController<DroneEvent>.broadcast();
  Stream<DroneEvent> get events => _events.stream;

  ServerLink? _link;
  StreamSubscription<dynamic>? _sub;
  Timer? _pingTimer;
  Timer? _reconnectTimer;
  DateTime _lastRx = DateTime.now();
  int _generation = 0; // 주소가 바뀌거나 재연결할 때마다 증가 → 옛 연결의 콜백 무시
  int _cmdSeq = 0;
  final _pending = <String, Completer<CmdResult>>{};
  bool _disposed = false;

  /// [uri]로 접속한다 (이미 연결돼 있으면 끊고 새로 접속).
  void connect(Uri uri) {
    _uri = uri;
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
      _lastRx = DateTime.now();
      _lastError = null;
      _sub = link.stream.listen(
        (raw) => _onMessage(gen, raw),
        onError: (Object e) {
          debugPrint('link error: $e');
          _onLost(gen, '통신 오류로 연결이 끊겼습니다');
        },
        onDone: () => _onLost(gen, '서버가 연결을 닫았습니다'),
        cancelOnError: true,
      );
      _pingTimer = Timer.periodic(pingInterval, (_) => _tick(gen));
      _setStatus(LinkStatus.online);
    }, onError: (Object e) {
      debugPrint('connect failed: $e');
      _onLost(gen, '접속 실패 — 서버가 켜져 있는지, 주소와 방화벽을 확인하세요');
    });
  }

  void _onMessage(int gen, dynamic raw) {
    if (gen != _generation) return;
    _lastRx = DateTime.now();
    final msg = ServerMessage.parse(raw);
    switch (msg) {
      case ConfigMsg(:final config):
        _config = config;
      case StateMsg(:final state):
        _state = state;
      case AckMsg(:final ack):
        _pending
            .remove(ack.id)
            ?.complete(CmdResult(ack.ok, error: ack.error, message: ack.message));
        return; // 화면 갱신 불필요
      case EventMsg(:final event):
        _events.add(event);
        return;
      case null:
        return;
    }
    notifyListeners();
  }

  void _tick(int gen) {
    if (gen != _generation) return;
    final silentMs = DateTime.now().difference(_lastRx).inMilliseconds;
    if (silentMs > _config.limits.linkTimeoutS * 1000) {
      _onLost(gen, '서버 응답이 ${_config.limits.linkTimeoutS.toStringAsFixed(0)}초 이상 없습니다');
      return;
    }
    _sendJson(buildPing(DateTime.now()));
  }

  void _onLost(int gen, String reason) {
    if (gen != _generation || _disposed) return;
    _lastError = reason;
    _teardown();
    _generation++; // 이 연결에서 오는 남은 콜백 무시
    _setStatus(LinkStatus.offline);
    _reconnectTimer = Timer(reconnectDelay, _restart);
  }

  void _teardown() {
    _pingTimer?.cancel();
    _pingTimer = null;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _sub?.cancel();
    _sub = null;
    _link?.close();
    _link = null;
    for (final c in _pending.values) {
      c.complete(const CmdResult(false, error: kErrTimeout)); // 보냈지만 결과 모름
    }
    _pending.clear();
  }

  bool _sendJson(Map<String, dynamic> m) {
    final link = _link;
    if (link == null || !online) return false;
    try {
      link.send(jsonEncode(m));
      return true;
    } catch (_) {
      return false;
    }
  }

  /// 명령 전송 후 ack를 기다린다 (docs/api.md 4.1).
  Future<CmdResult> sendCommand(String cmd, [Map<String, dynamic>? extra]) {
    final id = 'c-${++_cmdSeq}';
    if (!_sendJson(buildCmd(id, cmd, extra))) {
      return Future.value(const CmdResult(false, error: kErrNotSent));
    }
    final c = Completer<CmdResult>();
    _pending[id] = c;
    return c.future.timeout(ackTimeout, onTimeout: () {
      _pending.remove(id);
      return const CmdResult(false, error: kErrTimeout);
    });
  }

  Future<CmdResult> goto(double xCm, double yCm) =>
      sendCommand('goto', {'x_cm': xCm, 'y_cm': yCm});
  Future<CmdResult> stop() => sendCommand('stop');
  Future<CmdResult> land() => sendCommand('land');
  Future<CmdResult> returnHome() => sendCommand('return_home');

  /// 모터 즉시 정지(추락). 화면에서 확인 대화상자를 거친 뒤에만 부른다.
  Future<CmdResult> emergency() => sendCommand('emergency', {'confirm': true});

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
    _events.close();
    super.dispose();
  }
}
