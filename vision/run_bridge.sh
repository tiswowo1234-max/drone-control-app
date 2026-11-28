#!/bin/bash
# Mac·Linux용 위치 인식 연결 프로그램 실행 (Windows의 run_bridge.bat과 같음)
# 사용법: vision/run_bridge.sh [옵션]   (옵션은 python -m bridge --help)
cd "$(dirname "$0")" || exit 1
source ../tools/mac/common.sh
ensure_venv "$PWD" "cv2" || { pause_exit 1; }
export PYTHONIOENCODING=utf-8
.venv/bin/python -m bridge "$@"
