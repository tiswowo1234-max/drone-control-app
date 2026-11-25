@echo off
rem ============================================================
rem  실제 드론(RoboMaster TT)으로 실행: 중계 서버(real, 카메라 위치 입력 켬) + 위치 인식 연결 프로그램
rem  사용 전: 드론을 정문(이륙 지점)에 지도 위쪽을 보게 놓고 전원을 켠 뒤, 노트북 Wi-Fi를 TT(TELLO-…/RMTT-…)에 연결
rem  자세한 설명: README.md 「실행하기」
rem ============================================================
chcp 65001 > nul
setlocal
cd /d "%~dp0"
title 드론 시스템 시작 (실제 드론)
echo.
echo  [실제 드론] 중계 서버 + 위치 인식 연결 프로그램을 켭니다.
echo  확인: 드론 전원 ON, 노트북 Wi-Fi = TT, 드론은 정문에서 지도 위쪽을 보게 놓고 켰는지
echo.

curl -s -o nul http://127.0.0.1:8000/
if not errorlevel 1 (
  echo [알림] 이미 이 PC에서 서버가 켜져 있습니다 ^(http://127.0.0.1:8000/^).
  echo        먼저 기존 "드론 서버"·"위치 인식 연결" 창을 Ctrl+C로 끄고 다시 실행하세요.
  pause
  exit /b 1
)

ping -n 1 -w 1000 192.168.10.1 > nul
if errorlevel 1 (
  echo [주의] 드론^(192.168.10.1^)이 응답하지 않습니다. 노트북 Wi-Fi가 TT에 연결됐는지 확인하세요.
  echo        그래도 계속하면 서버가 드론 연결을 계속 다시 시도합니다.
  echo.
)
echo [1/3] 중계 서버를 새 창에서 켭니다 (real --vision)...
start "드론 서버 (real)" cmd /k call "%~dp0server\run_server.bat" real --vision

rem ---- 서버가 응답할 때까지 기다림 (처음 실행 땐 가상환경 설치로 몇 분 걸릴 수 있음) ----
echo [2/3] 서버가 준비되기를 기다립니다...
set /a tries=0
:wait_server
ping -n 3 127.0.0.1 > nul
curl -s -o nul http://127.0.0.1:8000/
if not errorlevel 1 goto server_ready
set /a tries+=1
if %tries% geq 150 (
  echo [오류] 5분이 지나도 서버가 켜지지 않았습니다. "드론 서버" 창의 오류를 확인하세요.
  pause
  exit /b 1
)
goto wait_server
:server_ready
echo       서버 준비 완료.

echo [3/3] 위치 인식 연결 프로그램을 새 창에서 켭니다 (TT 하단 카메라 / ESP32)...
start "위치 인식 연결" cmd /k call "%~dp0vision\run_bridge.bat"

echo.
echo ============================================================
echo  앱(휴대폰)의 톱니바퀴(⚙)에 아래 주소 중 휴대폰과 같은 네트워크의 것을 넣으세요.
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { '   ' + $_.IPAddress + '   (' + $_.InterfaceAlias + ')' }"
echo  PC 브라우저로 상태 보기: http://127.0.0.1:8000/
echo  끌 때: 열린 두 창에서 각각 Ctrl+C (드론은 먼저 착륙시키기)
echo  로그 파일: server\logs\server-날짜.log , vision\logs\bridge-날짜.log
echo ============================================================
echo.
pause
endlocal
