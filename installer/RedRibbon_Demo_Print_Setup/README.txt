RedRibbon Demo Print Receiver (병원 출력물 자동 수신 모듈)
=========================================================

설치 경로: C:\RedRibbonDemo
PDFCreator 기반 RedRibbon 전용 가상프린터 → PDF 자동 저장 → RedRibbon Print Receiver Engine 업로드

RedRibbon_Demo_Print_Setup.exe 가 수행하는 작업
---------------------------------------------
[자동]
  - C:\RedRibbonDemo 폴더 생성 (incoming, uploading, uploaded, failed, logs, print_receiver)
  - receiver_engine.py, config.json 설치
  - run_redribbon_receiver.ps1 설치
  - 작업 스케줄러 RedRibbonDemoReceiver 등록 (로그온 시 실행)
  - 바탕화면 「RedRibbon Receiver 실행」 바로가기
  - PDFCreator 기준 프린터에서 RedRibbon Printer 자동 생성 (Add-Printer)
  - PDFCreator 자동저장 경로 설정 시도 (C:\RedRibbonDemo\incoming)

[PDFCreator 미설치 시]
  - 설치는 중단하지 않음
  - WARN: PDFCreator가 설치되어 있지 않아 RedRibbon Printer 자동 생성은 건너뜁니다.

설치 완료 안내
--------------
PDFCreator 기반 RedRibbon 전용 가상프린터가 자동 구성되었습니다.
병원 문서를 RedRibbon Printer로 인쇄하면 C:\RedRibbonDemo\incoming 폴더로 저장되고,
Receiver Engine이 이를 서버로 전송합니다.

점검
----
powershell -ExecutionPolicy Bypass -File C:\RedRibbonDemo\check_receiver_ready.ps1

RedRibbon Printer 확인:
  Get-Printer -Name "RedRibbon Printer"

수동 실행
---------
C:\RedRibbonDemo\run_redribbon_receiver.ps1

서버: http://127.0.0.1:8000
API: /api/print-receiver/upload

자세한 설정: SETUP_VIRTUAL_PRINTER.md
