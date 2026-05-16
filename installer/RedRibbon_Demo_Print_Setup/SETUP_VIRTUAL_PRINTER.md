# RedRibbon 전용 가상프린터 (PDFCreator · RedRibbon Printer)

본선 시연은 **C:\RedRibbonDemo** 에 RedRibbon Print Receiver Engine(병원 출력물 자동 수신 모듈)을 설치합니다.

## PDFCreator 기반 RedRibbon 전용 가상프린터 자동 구성

`install_redribbon_demo.ps1` 또는 `RedRibbon_Demo_Print_Setup.exe` 가 다음을 수행합니다.

1. `Get-Printer -Name "PDFCreator"` 로 기준 프린터 확인
2. 기준 프린터의 `DriverName`, `PortName` 사용
3. **RedRibbon Printer** 가 없으면 `Add-Printer` 로 생성 (이미 있으면 유지)
4. Receiver Engine·작업 스케줄러·폴더 구성

PDFCreator가 설치되어 있지 않으면 설치는 **중단하지 않고** WARN만 표시합니다.

> PDFCreator가 설치되어 있지 않아 RedRibbon Printer 자동 생성은 건너뜁니다.

## 설치 완료 안내

PDFCreator 기반 RedRibbon 전용 가상프린터가 자동 구성되었습니다.
병원 문서를 **RedRibbon Printer** 로 인쇄하면 `C:\RedRibbonDemo\incoming` 폴더로 저장되고,
Receiver Engine이 래드리본 서버로 전송합니다.

## PDFCreator 자동저장 폴더 (확인)

RedRibbon Printer 프로필의 자동 저장 폴더가 아래인지 PDFCreator에서 확인하세요.

```
C:\RedRibbonDemo\incoming
```

## 동작 흐름

1. 병원 EMR·OCS에서 인쇄 대상 **RedRibbon Printer** 선택
2. PDFCreator가 PDF를 `incoming` 에 저장
3. Receiver Engine이 파일을 감지해 `http://127.0.0.1:8000/api/print-receiver/upload` 로 전송

## 점검

```powershell
powershell -ExecutionPolicy Bypass -File C:\RedRibbonDemo\check_receiver_ready.ps1
Get-Printer -Name "RedRibbon Printer"
Get-Printer | Where-Object { $_.Name -like "*RedRibbon*" } | Select Name, DriverName, PortName
```

`redribbon_printer` 항목이 **[OK]** 이면 RedRibbon Printer가 준비된 것입니다. 없으면 **[FAIL]** 입니다.
