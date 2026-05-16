RedRibbon Print Setup
=====================

RedRibbon 전용 가상프린터와 Print Receiver Engine 설치 패키지입니다.
병원 출력물을 래드리본으로 연결하는 전용 출력 채널을 구성합니다.

1. install_receiver.ps1 을 관리자 PowerShell에서 실행합니다.
2. SETUP_VIRTUAL_PRINTER.md 를 참고해 Windows에 "RedRibbon Printer" 를 추가합니다.
3. run_receiver.ps1 으로 수신 엔진을 실행합니다.
4. check_receiver_ready.ps1 으로 준비 상태를 점검합니다.

기본 서버: http://127.0.0.1:8000
업로드 API: /api/print-receiver/upload
