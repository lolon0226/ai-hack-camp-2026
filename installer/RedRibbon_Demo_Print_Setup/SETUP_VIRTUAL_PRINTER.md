# RedRibbon 전용 가상프린터 (PDFCreator · redribbon)

본선 시연은 **C:\RedRibbonDemo** 에 RedRibbon Print Receiver Engine(병원 출력물 자동 수신 모듈)을 설치합니다.

## 자동 설치

`install_redribbon_demo.ps1` 또는 `RedRibbon_Demo_Print_Setup.exe` 가 다음을 수행합니다.

- `C:\RedRibbonDemo` 폴더 구조 생성
- Receiver Engine·config.json 배치
- Windows 작업 스케줄러 `RedRibbonDemoReceiver` (로그온 시 실행)
- PDFCreator 설치 여부 확인 및 redribbon 프로필 자동 저장 경로 설정 **시도**
- 실패 시 수동 안내 표시 (설치는 계속 완료)

## PDFCreator 수동 설정 (필요 시)

PDFCreator에서 **redribbon** 프린터 또는 프로필의 **자동 저장** 폴더를 아래로 설정하세요.

```
C:\RedRibbonDemo\incoming
```

설치 스크립트가 자동 설정에 실패해도 Receiver 설치 자체는 완료됩니다.

## 동작 흐름

1. 병원 EMR·OCS에서 인쇄 대상 **redribbon** 선택
2. PDFCreator가 PDF를 `incoming` 에 저장
3. Receiver Engine이 파일을 감지해 `http://127.0.0.1:8000/api/print-receiver/upload` 로 전송

## 폴더 구조

```
C:\RedRibbonDemo\
  print_receiver\     receiver_engine.py, config.json
  incoming\           PDFCreator 자동저장
  uploading\          업로드 처리 중
  uploaded\           업로드 완료
  failed\             업로드 실패
  logs\               receiver.log, install.log
  run_redribbon_receiver.ps1
  check_receiver_ready.ps1
  install_redribbon_demo.ps1
```

## 점검

```powershell
powershell -ExecutionPolicy Bypass -File C:\RedRibbonDemo\check_receiver_ready.ps1
```
