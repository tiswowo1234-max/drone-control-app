@echo off
rem vision\ 연결 프로그램 설치: 가상환경(.venv)을 만들고 필요한 패키지를 설치한다. 처음 한 번만 실행.
chcp 65001 > nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [준비] 가상환경을 만듭니다...
  py -3.11 -m venv .venv 2> nul || py -3 -m venv .venv
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [오류] 패키지 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요.
) else (
  echo [완료] 이제 run_bridge.bat 으로 실행합니다.
)
pause
endlocal
