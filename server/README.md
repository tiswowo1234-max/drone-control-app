# 중계 서버 (앱 ⇄ 드론)

명세: `docs/api.md` v0.1 · 설정: `config/settings.toml`

## 빠른 실행 (mock = Unity 없이 가짜 드론)
1. 탐색기에서 `server\run_server.bat`을 **더블클릭**하면 settings.toml의 모드로 켜진다.
   모드를 고르려면 PowerShell에서:
   ```
   cd C:\dev\drone-control-app\server
   .\run_server.bat mock
   ```
   처음에는 가상환경(.venv)을 만들고 패키지를 설치하느라 1~2분 걸린다.
2. Windows 방화벽 창이 뜨면 **"개인 네트워크" 허용**을 체크하고 확인 (폰이 접속하려면 필요).
3. 창에 표시된 `ws://<IP>:8000/ws` 주소를 앱에 입력한다.

## 앱 없이 확인
서버를 켠 상태에서 PowerShell 창을 하나 더 열고:
```
cd C:\dev\drone-control-app\server
.venv\Scripts\python.exe tools\ws_check.py
```
config → 금지 좌표 거부 → goto 비행 → 귀환·착륙이 차례로 출력되고 "확인 완료"가 나오면 정상.

## 테스트
```
cd C:\dev\drone-control-app\server
.venv\Scripts\python.exe -m pytest -q
```
