import 'package:flutter/material.dart';

import 'drone_client.dart';
import 'map_screen.dart';
import 'server_address.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final url = await ServerAddress.load();
  final client = DroneClient()..connect(Uri.parse(url));
  runApp(DroneControlApp(client: client));
}

class DroneControlApp extends StatelessWidget {
  const DroneControlApp({super.key, required this.client});

  final DroneClient client;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '캠퍼스 경비 드론',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.indigo),
      ),
      home: MapScreen(client: client),
    );
  }
}
