@echo off
rem 사용법: run_server.bat [mock^|sim^|real]   (생략하면 config\settings.toml 값)
chcp 65001 > nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [준비] 가상환경을 처음 만듭니다. 1~2분 걸립니다...
  py -3.14 -m venv .venv || py -3 -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
rem 새로 추가된 패키지(예: 차 BLE 연결용 bleak)가 없으면 설치한다
".venv\Scripts\python.exe" -c "import bleak" 2> nul || (
  echo [준비] 새로 필요한 패키지를 설치합니다...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
".venv\Scripts\python.exe" run_server.py %*
pause
endlocal
