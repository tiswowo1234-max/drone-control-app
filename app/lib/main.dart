import 'package:flutter/material.dart';

import 'drone_client.dart';
import 'incident_screens.dart';
import 'map_screen.dart';
import 'server_address.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final url = await ServerAddress.load();
  final client = DroneClient()..connect(Uri.parse(url));
  runApp(DroneControlApp(client: client));
}

class DroneControlApp extends StatefulWidget {
  const DroneControlApp({super.key, required this.client, this.videoConnector});

  final DroneClient client;

  /// 실시간 영상 연결 (테스트에서 가짜로 바꿀 때만 지정)
  final LinkConnector? videoConnector;

  @override
  State<DroneControlApp> createState() => _DroneControlAppState();
}

class _DroneControlAppState extends State<DroneControlApp> {
  final _navigatorKey = GlobalKey<NavigatorState>();

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '캠퍼스 경비 드론',
      debugShowCheckedModeBanner: false,
      navigatorKey: _navigatorKey,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.indigo),
      ),
      // 새 사건 알림은 어느 화면(지도·영상·목록)에 있든 위에 뜨게 한다
      builder: (context, child) => IncidentAlertOverlay(
        client: widget.client,
        navigatorKey: _navigatorKey,
        child: child ?? const SizedBox.shrink(),
      ),
      home: MapScreen(client: widget.client, videoConnector: widget.videoConnector),
    );
  }
}
