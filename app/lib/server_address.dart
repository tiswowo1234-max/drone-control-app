import 'package:shared_preferences/shared_preferences.dart';

/// 중계 서버 주소 저장·불러오기 (폰을 껐다 켜도 유지)
class ServerAddress {
  static const _key = 'server_url';

  /// PC에서 Edge로 개발할 때 쓰는 기본값. 폰에서는 PC(파이)의 IP로 바꿔야 한다.
  static const defaultUrl = 'ws://127.0.0.1:8000/ws';

  static Future<String> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getString(_key) ?? defaultUrl;
    } catch (_) {
      return defaultUrl;
    }
  }

  static Future<void> save(String url) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, url);
  }

  /// 사용자가 입력한 문자열을 ws 주소로 정리한다. 잘못된 입력이면 null.
  ///
  /// - `192.168.0.10`        → `ws://192.168.0.10:8000/ws`
  /// - `192.168.0.10:9000`   → `ws://192.168.0.10:9000/ws`
  /// - `ws://host:8000/ws`   → 그대로
  static String? normalize(String input) {
    var s = input.trim();
    if (s.isEmpty) return null;
    if (!s.contains('://')) s = 'ws://$s';
    final u = Uri.tryParse(s);
    if (u == null || u.host.isEmpty) return null;
    if (u.scheme != 'ws' && u.scheme != 'wss') return null;
    final defaults = Uri.parse(defaultUrl);
    return u
        .replace(
          port: u.hasPort ? u.port : defaults.port,
          path: (u.path.isEmpty || u.path == '/') ? defaults.path : u.path,
        )
        .toString();
  }

  /// 명령 통로 주소(`ws://host:8000/ws`)와 같은 서버의 HTTP 주소에 [path]를 붙인다.
  /// 사건 사진(docs/api.md 10.1)에 쓴다. `ws`→`http`, `wss`→`https`.
  ///
  /// - `ws://192.168.0.10:8000/ws` + `/incidents/inc-3/photo.jpg`
  ///   → `http://192.168.0.10:8000/incidents/inc-3/photo.jpg`
  static Uri httpUrl(Uri wsUri, String path) => wsUri.replace(
        scheme: wsUri.scheme == 'wss' ? 'https' : 'http',
        path: path.startsWith('/') ? path : '/$path',
        query: null,
      );

  /// 같은 서버의 실시간 영상 통로 (docs/api.md 10.2): `ws://host:8000/ws/video`
  static Uri videoUrl(Uri wsUri) => wsUri.replace(path: '/ws/video');
}
