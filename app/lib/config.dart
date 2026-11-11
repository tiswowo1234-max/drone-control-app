/// 앱이 쓰는 설정값.
///
/// 원본은 `config/settings.toml`이며, 중계 서버가 연결 직후 `config` 메시지로 보내 준다
/// (docs/api.md 3.1). 서버에 연결되기 전에는 아래 [AppConfig.fallback] 값을 쓴다.
/// settings.toml을 바꾸면 이 기본값도 같이 맞춰 둔다.
class AppConfig {
  const AppConfig({
    required this.mapWidthCm,
    required this.mapHeightCm,
    required this.homeXCm,
    required this.homeYCm,
  });

  /// 바닥 지도의 실제 가로 길이 (cm)
  final double mapWidthCm;

  /// 바닥 지도의 실제 세로 길이 (cm)
  final double mapHeightCm;

  /// 이륙·귀환 지점 (cm, 지도 왼쪽 아래 기준)
  final double homeXCm;
  final double homeYCm;

  /// 서버 연결 전 기본값 (config/settings.toml과 동일하게 유지)
  static const fallback = AppConfig(
    mapWidthCm: 250,
    mapHeightCm: 180,
    homeXCm: 20,
    homeYCm: 20,
  );
}
