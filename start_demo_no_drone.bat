@echo off
rem ============================================================
rem  드론 없이 시험: 가짜 드론 서버(mock, 카메라 위치 입력 켬) + 위치 인식 연결 프로그램(합성 카메라)
rem  합성 카메라 = 조원 지도 그림으로 "드론 위치에서 보이는 화면"을 만들어 실제 위치 인식 코드에 넣는다
rem  자세한 설명: README.md 「실행하기」
rem ============================================================
chcp 65001 > nul
setlocal
cd /d "%~dp0"
title 드론 시스템 시작 (드론 없이 시험)
echo.
echo  [드론 없이 시험] 가짜 드론 서버 + 위치 인식 연결 프로그램(합성 카메라)을 켭니다.
echo.

curl -s -o nul http://127.0.0.1:8000/
if not errorlevel 1 (
  echo [알림] 이미 이 PC에서 서버가 켜져 있습니다 ^(http://127.0.0.1:8000/^).
  echo        먼저 기존 "드론 서버"·"위치 인식 연결" 창을 Ctrl+C로 끄고 다시 실행하세요.
  pause
  exit /b 1
)

echo [1/3] 가짜 드론 서버를 새 창에서 켭니다 (mock --vision --car mock: 가짜 차 포함)...
start "드론 서버 (mock)" cmd /k call "%~dp0server\run_server.bat" mock --vision --car mock

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

echo [3/3] 위치 인식 연결 프로그램을 새 창에서 켭니다 (합성 카메라)...
start "위치 인식 연결" cmd /k call "%~dp0vision\run_bridge.bat"
echo.
echo  앱에서: 이륙 → 지도의 보라색 S4 누르기 → 확인 → 몇 초 뒤 사건 알림, 카메라 아이콘으로 영상 보기
echo  차(UGV): 드론을 지도 (120, 70)cm 근처(정문 왼쪽)로 출동 → 드론이 가짜 차를 인식 → 위쪽 경찰 아이콘으로 차 조종

echo.
echo ============================================================
echo  앱(휴대폰)의 톱니바퀴(⚙)에 아래 주소 중 휴대폰과 같은 네트워크의 것을 넣으세요.
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { '   ' + $_.IPAddress + '   (' + $_.InterfaceAlias + ')' }"
echo  PC 브라우저로 상태 보기: http://127.0.0.1:8000/
echo  끌 때: 열린 두 창에서 각각 Ctrl+C (드론은 먼저 착륙시키기)
echo  서버 로그 파일: server\logs\server-날짜.log
echo ============================================================
echo.
pause
endlocal
