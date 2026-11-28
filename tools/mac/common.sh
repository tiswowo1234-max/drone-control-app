#!/bin/bash
# Mac 실행 파일(.command, run_*.sh)이 함께 쓰는 함수. 직접 실행하지 않는다.

pause_exit() {
  echo
  read -r -p "엔터를 누르면 창을 닫습니다..." _
  exit "${1:-0}"
}

# Python 3.11 이상 찾기 (Homebrew → PATH 순서)
find_python() {
  local c
  for c in /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
           /opt/homebrew/bin/python3.11 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
           python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$c" > /dev/null 2>&1 &&
       "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2> /dev/null; then
      command -v "$c"
      return 0
    fi
  done
  return 1
}

# ensure_venv <폴더> <확인할 모듈>: 폴더/.venv가 없으면 만들고, 모듈이 없으면 requirements.txt를 설치한다
ensure_venv() {
  local dir="$1" mod="$2" py
  case "$dir" in
    *:*)
      echo "[오류] 폴더 경로에 ':'가 들어 있습니다: $dir"
      echo "       Mac에서는 Python 가상환경을 만들 수 없으니 ':' 없는 폴더(예: ~/dev/drone-control-app)로 옮기세요."
      return 1 ;;
  esac
  if [ ! -x "$dir/.venv/bin/python" ]; then
    py="$(find_python)" || {
      echo "[오류] Python 3.11 이상이 없습니다. 터미널에서:  brew install python@3.12"
      return 1
    }
    echo "[준비] 가상환경을 처음 만듭니다 ($("$py" --version)). 1~2분 걸립니다..."
    "$py" -m venv "$dir/.venv" || return 1
    "$dir/.venv/bin/python" -m pip install -q --upgrade pip
    "$dir/.venv/bin/python" -m pip install -r "$dir/requirements.txt" || return 1
  fi
  if ! "$dir/.venv/bin/python" -c "import $mod" 2> /dev/null; then
    echo "[준비] 새로 필요한 패키지를 설치합니다..."
    "$dir/.venv/bin/python" -m pip install -r "$dir/requirements.txt" || return 1
  fi
}

server_up() {
  curl -s -o /dev/null --max-time 2 http://127.0.0.1:8000/
}

# 새 터미널 창에서 명령 실행 (창 제목 포함)
open_terminal() {
  local title="$1" cmd="$2"
  osascript - "$title" "$cmd" <<'APPLESCRIPT' > /dev/null
on run argv
  tell application "Terminal"
    activate
    set t to do script (item 2 of argv)
    set custom title of t to (item 1 of argv)
  end tell
end run
APPLESCRIPT
}

wait_server() {
  local tries=0
  until server_up; do
    sleep 2
    tries=$((tries + 1))
    if [ $tries -ge 150 ]; then
      echo "[오류] 5분이 지나도 서버가 켜지지 않았습니다. \"드론 서버\" 창의 오류를 확인하세요."
      return 1
    fi
  done
}

show_addresses() {
  echo "============================================================"
  echo " 앱(휴대폰)의 톱니바퀴(⚙)에 아래 주소 중 휴대폰과 같은 네트워크의 것을 넣으세요."
  ifconfig -a | awk '/^[a-z]/ {iface=$1} /inet / && $2 != "127.0.0.1" && $2 !~ /^169\.254\./ {print "   " $2 "   (" iface ")"}'
  echo " PC 브라우저로 상태 보기: http://127.0.0.1:8000/"
  echo " 끌 때: 열린 두 창에서 각각 Ctrl+C (드론은 먼저 착륙시키기)"
  echo " 로그 파일: server/logs/server-날짜.log , vision/logs/bridge-날짜.log"
  echo "============================================================"
}
