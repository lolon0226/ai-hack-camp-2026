RedRibbon Demo Print Receiver (병원 출력물 자동 수신 모듈)
=========================================================

설치 경로: C:\RedRibbonDemo
PDFCreator 기반 전용 가상프린터 redribbon → PDF 자동 저장 → RedRibbon Print Receiver Engine 업로드

원클릭 설치
-----------
1. RedRibbon_Demo_Print_Setup.exe 실행 (또는 ZIP 압축 해제 후 install_redribbon_demo.ps1)
2. 설치 스크립트가 폴더·config·작업 스케줄러·바탕화면 바로가기를 구성합니다.
3. PDFCreator에서 redribbon 프로필 자동 저장 폴더:
   C:\RedRibbonDemo\incoming
   (자동 설정이 불확실하면 설치 안내에 따라 수동 설정)

점검
----
powershell -ExecutionPolicy Bypass -File C:\RedRibbonDemo\check_receiver_ready.ps1

수동 실행
---------
C:\RedRibbonDemo\run_redribbon_receiver.ps1

작업 스케줄러: RedRibbonDemoReceiver (로그온 시 자동 시작)

서버: http://127.0.0.1:8000
API: /api/print-receiver/upload

자세한 PDFCreator 설정: SETUP_VIRTUAL_PRINTER.md
