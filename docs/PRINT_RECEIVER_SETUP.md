# RedRibbon Print Receiver · 가상프린터 설정

병원에서 **RedRibbon Printer**로 인쇄한 문서가 PDF로 저장되고, Receiver Engine이 래드리본 서버로 업로드합니다.

## 개요

- **PDFCreator**: Windows에 설치된 가상 PDF 프린터 (기준 드라이버)
- **RedRibbon Printer**: PDFCreator의 Driver/Port를 재사용해 `Add-Printer`로 생성
- **Receiver**: `C:\RedRibbonDemo`에서 `incoming` 폴더 감시 → HTTP 업로드

> Windows 커널 드라이버를 직접 개발한 것이 아니라, **PDFCreator 기반 RedRibbon 전용 가상프린터**를 자동 구성합니다.

**발표용:** 설치파일을 실행하면 PDFCreator 기반 RedRibbon 전용 가상프린터가 자동 생성되고, 병원 직원은 RedRibbon Printer로 출력만 하면 문서가 자동 접수됩니다.

## 사전 요구

1. PDFCreator 설치 및 확인:

```powershell
Get-Printer -Name "PDFCreator"
```

2. 래드리본 서버 실행 (본선 **8010**):

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8010
```

## EXE 설치

| 항목 | 내용 |
|------|------|
| 파일 | `static/downloads/RedRibbon_Demo_Print_Setup.exe` |
| 빌드 | `python scripts/build_print_installer.py` (Inno Setup 필요) |

설치 후 경로:

```
C:\RedRibbonDemo\
  incoming\      ← PDF 유입
  uploading\
  uploaded\
  failed\
  logs\
  print_receiver\
    config.json
    receiver_engine.py
  run_redribbon_receiver.ps1
  check_receiver_ready.ps1
```

작업 스케줄러: **RedRibbonDemoReceiver** (로그온 시 실행)

## RedRibbon Printer 자동 생성

1. `Get-Printer -Name "PDFCreator"`
2. `DriverName`, `PortName` (예: Driver `PDFCreator`, Port `pdfcmon`)
3. `Add-Printer -Name "RedRibbon Printer" ...`

확인:

```powershell
Get-Printer -Name "RedRibbon Printer" | Format-List Name, DriverName, PortName
```

PDFCreator가 없으면 설치는 계속되지만 프린터 생성은 **건너뜀**(WARN).

PDFCreator 자동저장 폴더를 다음으로 맞추는 것을 권장:

```
C:\RedRibbonDemo\incoming
```

## config.json

경로: `C:\RedRibbonDemo\print_receiver\config.json`

| 키 | 설명 |
|----|------|
| `server_url` | 앱 베이스 URL (본선: `http://127.0.0.1:8010`) |
| `upload_endpoint` | `/api/print-receiver/upload` |
| `watch_dir` | `C:\RedRibbonDemo\incoming` |
| `uploaded_dir` | 업로드 성공 폴더 |
| `failed_dir` | 실패 폴더 |
| `printer_name` | `RedRibbon Printer` |

저장소 템플릿(`print_receiver/config.json`)은 `8000`일 수 있음 → 설치 후 **8010**으로 수정.

## 실행·점검

```powershell
powershell -ExecutionPolicy Bypass -File "C:\RedRibbonDemo\run_redribbon_receiver.ps1"
powershell -ExecutionPolicy Bypass -File "C:\RedRibbonDemo\check_receiver_ready.ps1"
```

기대: `FAIL: 0  WARN: 0`

## 수동 PDF 테스트

```powershell
Copy-Item "C:\path\to\sample.pdf" "C:\RedRibbonDemo\incoming\receipt_test.pdf" -Force
```

```powershell
Get-ChildItem "C:\RedRibbonDemo\incoming","C:\RedRibbonDemo\uploaded","C:\RedRibbonDemo\failed" -File |
  Sort-Object LastWriteTime -Descending |
  Select-Object FullName, Length, LastWriteTime -First 10
```

운영자: http://127.0.0.1:8010/operator/received-documents

## 인쇄 테스트

1. 메모장 등에서 인쇄 → **RedRibbon Printer**
2. `incoming` / `uploaded` 확인
3. 수신문서함에서 문서·OCR·매칭 확인

## EXE 빌드 (개발)

```powershell
winget install -e --id JRSoftware.InnoSetup
cd "C:\Users\DELL\Desktop\ai hack camp 2026"
python scripts/build_print_installer.py
```

`ISCC.exe` 후보:

- `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`
- `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`

## 문제 해결

| 문제 | 해결 |
|------|------|
| RedRibbon Printer 없음 | PDFCreator 설치 후 EXE 재실행 |
| Receiver만 동작 | `check_receiver_ready.ps1`에서 `redribbon_printer` FAIL 확인 |
| 업로드 실패 | `server_url` 8010, 서버 실행, 방화벽 |
| `server_url` 8000 | `config.json` 수정 |
| Edge EXE 경고 | 신뢰 출처 확인, 차단 해제 후 설치 |

더 보기: [WINDOWS_SETUP.md](WINDOWS_SETUP.md), `installer/RedRibbon_Demo_Print_Setup/SETUP_VIRTUAL_PRINTER.md`
