import 'package:flutter/material.dart';

import 'config.dart';
import 'map_coords.dart';

/// 지도 이미지의 가로:세로 비율 (assets/map.png = 2501x1802)
const double kMapImageAspect = 2501 / 1802;

/// 1단계 화면: 캠퍼스 지도를 보여 주고, 확대·이동하며, 누른 곳의 좌표(cm)를 표시한다.
class MapScreen extends StatefulWidget {
  const MapScreen({super.key, this.config = AppConfig.fallback});

  final AppConfig config;

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  /// 마지막으로 누른 지점 (지도 cm 좌표)
  Offset? _tappedCm;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('캠퍼스 경비 드론'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
      ),
      body: Column(
        children: [
          Expanded(child: _buildMap()),
          _buildInfoBar(),
        ],
      ),
    );
  }

  Widget _buildMap() {
    return LayoutBuilder(
      builder: (context, constraints) {
        // 화면 안에 지도 비율을 유지한 채 최대한 크게 맞춘다
        var w = constraints.maxWidth;
        var h = w / kMapImageAspect;
        if (h > constraints.maxHeight) {
          h = constraints.maxHeight;
          w = h * kMapImageAspect;
        }
        final coords = MapCoords(config: widget.config, viewSize: Size(w, h));
        final home = coords.cmToPx(
          Offset(widget.config.homeXCm, widget.config.homeYCm),
        );

        return InteractiveViewer(
          minScale: 1,
          maxScale: 8,
          child: Center(
            child: SizedBox(
              width: w,
              height: h,
              child: GestureDetector(
                // localPosition은 확대·이동과 상관없이 지도 이미지 기준 픽셀 좌표다
                onTapUp: (d) =>
                    setState(() => _tappedCm = coords.pxToCm(d.localPosition)),
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    Positioned.fill(
                      child: Image.asset('assets/map.png', fit: BoxFit.fill),
                    ),
                    _pin(home, Icons.home, Colors.blue, '이륙 지점'),
                    if (_tappedCm != null)
                      _pin(coords.cmToPx(_tappedCm!), Icons.place, Colors.red,
                          '누른 곳'),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  /// 지도 위 표시 아이콘. 아이콘 아래 끝이 [px] 위치에 오도록 놓는다.
  Widget _pin(Offset px, IconData icon, Color color, String tooltip) {
    const size = 28.0;
    return Positioned(
      left: px.dx - size / 2,
      top: px.dy - size,
      child: Tooltip(
        message: tooltip,
        child: Icon(icon, color: color, size: size),
      ),
    );
  }

  Widget _buildInfoBar() {
    final c = widget.config;
    final t = _tappedCm;
    final String text;
    if (t == null) {
      text = '지도를 누르면 그 지점의 좌표(cm)가 표시됩니다. '
          '두 손가락(또는 마우스 휠)으로 확대할 수 있습니다.';
    } else {
      final inside = MapCoords(config: c, viewSize: const Size(1, 1)).isInside(t);
      text = '누른 곳: x = ${t.dx.toStringAsFixed(1)} cm, '
          'y = ${t.dy.toStringAsFixed(1)} cm'
          '${inside ? '' : '  (지도 밖)'}';
    }
    return Container(
      width: double.infinity,
      color: Colors.black87,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Text(
        '$text\n지도 크기: ${c.mapWidthCm.toStringAsFixed(0)} × '
        '${c.mapHeightCm.toStringAsFixed(0)} cm (임시값)',
        style: const TextStyle(color: Colors.white, fontSize: 14),
      ),
    );
  }
}
