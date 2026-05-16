# RedRibbon 전용 가상프린터 설정

Windows에서 **RedRibbon Printer** 를 등록해 병원 EMR·OCS 출력을
래드리본 Print Receiver 로 전달합니다.

## 개요

- **RedRibbon 전용 가상프린터**: 병원 업무 프로그램에서 선택하는 출력 대상
- **전용 출력 채널**: 인쇄된 PDF가 `incoming` 폴더로 전달되고, Receiver가 서버로 업로드

## 권장 절차

1. `install_receiver.ps1` 실행 → `C:\RedRibbonPrint` 구조 생성
2. PDF 가상 프린터 도구(예: Microsoft Print to PDF, 벤더 제공 RedRibbon Printer 포트)에서
   출력 경로를 `C:\RedRibbonPrint\RedRibbon_Printer_Output\incoming` 으로 지정
3. 프린터 이름을 **RedRibbon Printer** 로 표시
4. `run_receiver.ps1` 로 `receiver_engine.py` 실행
5. `check_receiver_ready.ps1` 로 폴더·config·서버·프린터 확인

## config.json

`C:\RedRibbonPrint\config.json` 에서 `server_url`, `hospital_name` 등을 수정할 수 있습니다.
