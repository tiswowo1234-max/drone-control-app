#!/bin/bash
# ============================================================
#  [Mac] 실제 드론(RoboMaster TT)으로 실행 — Windows의 start_real_drone.bat과 같음
#  중계 서버(real, 카메라 위치 입력 켬) + 위치 인식 연결 프로그램을 각각 새 터미널 창에서 켠다
#  사용 전: 드론을 정문(이륙 지점)에 지도 위쪽을 보게 놓고 전원을 켠 뒤, Mac Wi-Fi를 TT(TELLO-…/RMTT-…)에 연결
#  Finder에서 더블클릭으로 실행. 자세한 설명: README.md 「Mac에서 실행하기」
# ============================================================
cd "$(dirname "$0")" || exit 1
ROOT="$PWD"
source tools/mac/common.sh

echo
echo " [실제 드론] 중계 서버 + 위치 인식 연결 프로그램을 켭니다."
echo " 확인: 드론 전원 ON, Mac Wi-Fi = TT, 드론은 정문에서 지도 위쪽을 보게 놓고 켰는지"
echo " 차(UGV): 차 전원 ON, Mac 블루투스 ON, 폰의 Police Car 웹앱은 차 연결 해제 (차는 서버가 연결)"
echo " 휴대폰: TT Wi-Fi에 함께 연결하면 Mac과 같은 네트워크가 됩니다"
echo

if server_up; then
  echo "[알림] 이미 이 Mac에서 서버가 켜져 있습니다 (http://127.0.0.1:8000/)."
  echo "       먼저 기존 \"드론 서버\"·\"위치 인식 연결\" 창을 Ctrl+C로 끄고 다시 실행하세요."
  pause_exit 1
fi

if ! ping -c 1 -t 1 192.168.10.1 > /dev/null 2>&1; then
  echo "[주의] 드론(192.168.10.1)이 응답하지 않습니다. Mac Wi-Fi가 TT에 연결됐는지 확인하세요."
  echo "       그래도 계속하면 서버가 드론 연결을 계속 다시 시도합니다."
  echo
fi

echo "[1/3] 중계 서버를 새 창에서 켭니다 (real --vision)..."
open_terminal "드론 서버 (real)" "'$ROOT/server/run_server.sh' real --vision"

echo "[2/3] 서버가 준비되기를 기다립니다 (처음 실행 땐 가상환경 설치로 몇 분 걸릴 수 있음)..."
wait_server || pause_exit 1
echo "      서버 준비 완료."

echo "[3/3] 위치 인식 연결 프로그램을 새 창에서 켭니다 (TT 하단 카메라 / ESP32)..."
open_terminal "위치 인식 연결" "'$ROOT/vision/run_bridge.sh'"

echo
show_addresses
pause_exit 0
