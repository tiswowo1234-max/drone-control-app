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
".venv\Scripts\python.exe" run_server.py %*
pause
endlocal
