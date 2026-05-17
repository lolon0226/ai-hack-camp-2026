# Windows 설치·실행 요약 (RedRibbon)

상세 내용은 [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md)를 참고하세요.

## 필수·권장 프로그램

| 구분 | 프로그램 |
|------|----------|
| OS | Windows 10 또는 11 |
| 셸 | PowerShell 5.1+ |
| 런타임 | Python 3.11+ (테스트: 3.13) |
| 패키지 | pip |
| OCR | Tesseract OCR (+ `kor.traineddata`) |
| 가상프린터 | PDFCreator (RedRibbon Printer 자동 생성의 기반) |
| EXE 빌드(개발자) | Inno Setup 6 (`ISCC.exe`) |

Windows 커널 드라이버를 직접 개발한 것이 아니라, **PDFCreator 기반 RedRibbon 전용 가상프린터**를 설치 스크립트/EXE로 자동 구성합니다.

## 권장 설치 순서

1. Python + `pip install -r requirements.txt`
2. 프로젝트 `.env` 작성 → [docs/ENV_SETUP.md](docs/ENV_SETUP.md)
3. `data/`, `data/secure_files/` 확인
4. Tesseract 설치 (`winget install --id UB-Mannheim.TesseractOCR -e`)
5. PDFCreator 설치·확인 (`Get-Printer -Name "PDFCreator"`)
6. 서버 기동: `python -m uvicorn app:app --host 127.0.0.1 --port 8010`
7. `RedRibbon_Demo_Print_Setup.exe` 실행 → `C:\RedRibbonDemo` 구성
8. `config.json`의 `server_url`을 `http://127.0.0.1:8010`으로 설정
9. Receiver 점검·실행 → [docs/PRINT_RECEIVER_SETUP.md](docs/PRINT_RECEIVER_SETUP.md)

## 한 번에 점검 (PowerShell)

```powershell
cd "C:\Users\DELL\Desktop\ai hack camp 2026"
python -c "import app; print('APP IMPORT OK')"
Test-Path ".\data\redribbon_final.db"
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
Get-Printer -Name "PDFCreator" -ErrorAction SilentlyContinue
Get-Printer -Name "RedRibbon Printer" -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File "C:\RedRibbonDemo\check_receiver_ready.ps1"
Invoke-WebRequest -Uri "http://127.0.0.1:8010/" -UseBasicParsing | Select-Object StatusCode
```

## 발표용 한 줄

설치파일을 실행하면 PDFCreator 기반 RedRibbon 전용 가상프린터가 자동 생성되고, 병원 직원은 **RedRibbon Printer**로 출력만 하면 문서가 자동 접수됩니다.
