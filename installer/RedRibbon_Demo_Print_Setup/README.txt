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
  - PDFCreator 프로필 "RedRibbon Auto Save" 생성·자동저장 ON (Interactive OFF)
  - 저장 폴더 C:\RedRibbonDemo\incoming, RedRibbon Printer와 프로필 연결

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
  기대: FAIL 0, pdfcreator_autosave_profile [OK]

RedRibbon Printer 확인:
  Get-Printer -Name "RedRibbon Printer"

자동설정 실패 시 (fallback, 수동):
  PDFCreator -> Profiles -> RedRibbon Auto Save
  Save: Interactive OFF / Automatic ON / Target: C:\RedRibbonDemo\incoming
  Actions: Open file/PDF Architect OFF
  Printer: RedRibbon Printer -> RedRibbon Auto Save

Receiver 실행 (자동 / 수동)
---------------------------
[자동 - 병원 PC 설치 후]
  작업 스케줄러 RedRibbonDemoReceiver 가 사용자 로그온 시 Receiver 를 자동 실행합니다.
  (install_redribbon_demo.ps1 / EXE 설치 시 등록)

[수동 - 본선 시연]
  안정성을 위해 아래 스크립트를 수동 실행해도 됩니다.
  C:\RedRibbonDemo\run_redribbon_receiver.ps1

서버(본선 권장): http://127.0.0.1:8010
  ※ print_receiver\config.json 의 server_url 을 설치 PC 포트에 맞게 수정
API: /api/print-receiver/upload

자세한 설정: SETUP_VIRTUAL_PRINTER.md
