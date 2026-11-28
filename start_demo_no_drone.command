#!/bin/bash
# ============================================================
#  [Mac] 드론 없이 시험 — Windows의 start_demo_no_drone.bat과 같음
#  가짜 드론(mock) + 가짜 차 + 합성 카메라로 앱 조작을 연습한다
#  Finder에서 더블클릭으로 실행. 휴대폰은 Mac과 같은 Wi-Fi에 연결
# ============================================================
cd "$(dirname "$0")" || exit 1
ROOT="$PWD"
source tools/mac/common.sh

echo
echo " [드론 없이 시험] 가짜 드론 + 가짜 차 + 합성 카메라를 켭니다."
echo

if server_up; then
  echo "[알림] 이미 이 Mac에서 서버가 켜져 있습니다 (http://127.0.0.1:8000/)."
  echo "       먼저 기존 \"드론 서버\"·\"위치 인식 연결\" 창을 Ctrl+C로 끄고 다시 실행하세요."
  pause_exit 1
fi

echo "[1/3] 중계 서버를 새 창에서 켭니다 (mock --vision --car mock)..."
open_terminal "드론 서버 (mock)" "'$ROOT/server/run_server.sh' mock --vision --car mock"

echo "[2/3] 서버가 준비되기를 기다립니다..."
wait_server || pause_exit 1
echo "      서버 준비 완료."

echo "[3/3] 위치 인식 연결 프로그램(합성 카메라)을 새 창에서 켭니다..."
open_terminal "위치 인식 연결" "'$ROOT/vision/run_bridge.sh'"

echo
show_addresses
pause_exit 0
