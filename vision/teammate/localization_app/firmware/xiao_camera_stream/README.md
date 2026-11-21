# XIAO ESP32-S3 Sense 카메라 업로드

## Arduino IDE로 자체 펌웨어 올리기

1. 카메라 리본과 Sense 확장 보드를 연결한 뒤, USB-C **데이터** 케이블로 PC에 연결한다. Arduino IDE 2에서 **File → Preferences → Additional Boards Manager URLs**에 다음 주소를 추가한다.

   ```text
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```

2. Boards Manager에서 **esp32 by Espressif Systems**를 설치한다. 보드는 **XIAO_ESP32S3**, 포트는 USB 케이블을 뺐다 꽂을 때 나타나는 COM 포트를 선택한다. Windows 장치 관리자에서도 확인할 수 있다. USB 연결 확인에는 Arduino의 **File → Examples → 01.Basics → Blink**를 먼저 Verify/Upload해도 된다.
3. Tools에 항목이 있으면 **USB CDC On Boot → Enabled**, **PSRAM → OPI PSRAM**을 선택한다. Flash 크기는 보드 정의의 8 MB 설정, Partition Scheme은 기본값으로 시작한다. 이 스케치는 CameraWebServer의 얼굴 인식 기능을 포함하지 않는다. 설치된 버전에서 메뉴 구성이 다르면 XIAO 보드 기본값과 업로드 로그의 Flash 크기를 확인한다.
4. 같은 폴더에서 `wifi_secrets.example.h`를 `wifi_secrets.h`로 복사해 **2.4GHz Wi-Fi** SSID와 비밀번호를 입력한다. 이 파일은 Git에서 제외된다.

   ```powershell
   Copy-Item .\wifi_secrets.example.h .\wifi_secrets.h
   ```

   macOS와 Linux에서는 같은 폴더에서 `cp wifi_secrets.example.h wifi_secrets.h`를 실행한다.

5. `xiao_camera_stream.ino`를 열고 **Verify → Upload**한다. Serial Monitor는 **115200 baud**로 연다. 모니터에 부팅 로그가 나오지 않으면 포트를 다시 선택하고 RESET을 한 번 누른다.

2026-11-14 작업 PC 초기 확인에서 Arduino IDE와 기존 ESP32 코어를 찾지 못했다. COM3과 COM4는 Bluetooth 가상 포트였다. CLI와 ESP32 코어는 컴파일 검증을 위해 작업 폴더에 별도로 준비했다.

2026-11-16에는 COM5에서 실제 ESP32-S3 USB-Serial/JTAG 장치를 확인했고, 기존 빌드 업로드와 플래시 해시 검증을 완료했다. Serial Monitor에 PSRAM 8MB와 OV3660(PID `0x3660`), 320 × 240 JPEG가 표시됐다. Wi-Fi 설정을 채운 뒤 기존 바이너리를 그대로 올리는 대신 다시 컴파일해 업로드한다. [실물 통합 기록](../../../data/experiments/localization/xiao_real_camera/experiment_report.md)

Arduino-ESP32 2.0.17 CameraWebServer 예제는 카메라 모델 선택이 `.ino` 안에 있고, 3.3.5 예제는 `board_config.h`에서 선택한다. 이 스케치는 자체 XIAO 핀 헤더와 ESP-IDF HTTP 서버를 사용한다. [Seeed 보드 설정](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/) · [Seeed 카메라 가이드](https://wiki.seeedstudio.com/xiao_esp32s3_camera_usage/) · [Espressif 2.0.17 예제](https://github.com/espressif/arduino-esp32/tree/2.0.17/libraries/ESP32/examples/Camera/CameraWebServer) · [Espressif 3.3.5 예제](https://github.com/espressif/arduino-esp32/tree/3.3.5/libraries/ESP32/examples/Camera/CameraWebServer)

Arduino CLI와 3.3.5 코어를 사용하는 경우 프로젝트 폴더에서 다음 명령으로 빌드할 수 있다.

```powershell
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi,CDCOnBoot=default firmware/xiao_camera_stream
```

Arduino CLI 1.3.1 및 Arduino-ESP32 3.3.5에서 컴파일과 링크를 확인했다. 스케치는 Flash 961,887바이트(28%)와 정적 RAM 54,580바이트(16%)를 사용했다. Windows 링커가 한글 경로를 처리하지 못할 경우 프로젝트와 빌드 경로를 영문 경로에 두거나 임시 드라이브 문자로 접근한다.

## 영상 확인

- Serial Monitor에서 카메라 PID와 모델, PSRAM, 실제 프레임 크기와 JPEG 형식, `Ready: http://…/stream`을 확인한다. PID가 알려진 경우 OV2640, OV3660, OV5640을 표시한다. 자동 초점 기능은 포함하지 않았다.
- PC를 같은 네트워크에 연결하고 **Serial Monitor에 표시된 주소**를 브라우저에 입력한다. 펌웨어는 **HTTP 80번 포트의 `/stream`**을 제공한다. 브라우저 탭을 닫은 뒤 Python 수신기를 사용한다. 한 번에 한 스트림 클라이언트를 사용하며 인증이 없으므로 실험 네트워크 내부에서 운용한다.
- 기본은 QVGA 320 × 240, JPEG quality 12다. PSRAM이 있으면 버퍼 2개와 최신 프레임 모드, 없으면 DRAM 버퍼 1개로 초기화를 시도한다. VGA 비교는 스케치의 `FRAME_SIZE`를 `FRAMESIZE_VGA`로 바꿔 다시 컴파일한다. `JPEG_QUALITY` 값이 작을수록 품질과 데이터량이 올라간다.
- 포트가 없거나 업로드가 실패하면 Serial Monitor를 닫고, BOOT을 누른 채 USB를 연결한 뒤 BOOT을 놓는다. 새 COM 포트를 선택해 업로드하고 RESET을 누른다. [Seeed Bootloader Mode](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/#bootloader-mode)
- IP가 없으면 SSID가 2.4GHz에서 제공되는지와 비밀번호를 확인한다. IP가 찍혔다면 PowerShell에서 `Test-NetConnection <IP> -Port 80`으로 확인한다. 게스트망 클라이언트 격리, PC 방화벽의 아웃바운드 정책, 다른 브라우저/수신기 연결을 점검한다.

펌웨어의 `X-Frame-Id`는 HTTP 송신을 시도한 프레임에 붙고, `X-Stream-Session`은 보드가 시작될 때 새로 생성된다. 프레임 버퍼에서 송신 전에 버린 프레임 수와 촬영 타임스탬프는 이 헤더에 포함되지 않는다.
