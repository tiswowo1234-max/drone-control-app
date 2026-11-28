#!/bin/bash
# Mac·Linux용 중계 서버 실행 (Windows의 run_server.bat과 같음)
# 사용법: server/run_server.sh [mock|sim|real] [--vision] [--car ble|mock|off]   (생략하면 config/settings.toml 값)
cd "$(dirname "$0")" || exit 1
source ../tools/mac/common.sh
ensure_venv "$PWD" "bleak" || { pause_exit 1; }
.venv/bin/python run_server.py "$@"
