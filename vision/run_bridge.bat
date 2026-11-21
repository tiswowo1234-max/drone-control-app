@echo off
rem 사용법: run_bridge.bat [옵션]   예) run_bridge.bat --dry-run --tour
rem   옵션 목록: run_bridge.bat --help   (설명서: vision\README.md)
chcp 65001 > nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup.bat
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m bridge %*
pause
endlocal
