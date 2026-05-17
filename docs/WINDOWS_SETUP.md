# Windows PC 실행 가이드 (RedRibbon 본선 시연)

일반 Windows PC에서 래드리본 프로그램을 가동하기 위한 **부가 프로그램**, **설치 조건**, **실행 순서**, **확인 명령**을 정리합니다.

프로젝트 폴더 예:

```
C:\Users\DELL\Desktop\ai hack camp 2026
```

---

## 1. 기본 실행 환경

| 항목 | 요구 사항 |
|------|-----------|
| OS | Windows 10 또는 Windows 11 |
| 셸 | PowerShell 5.1 이상 |
| Python | 3.11 이상 권장 (현재 테스트 환경: **3.13**에서도 동작) |
| pip | 사용 가능해야 함 |
| 네트워크 | CODEF·OpenAI·수신 업로드 시 인터넷 필요 |

```powershell
$PSVersionTable.PSVersion
python --version
pip --version
```

---

## 2. Python 패키지 설치

프로젝트 루트에서:

```powershell
cd "C:\Users\DELL\Desktop\ai hack camp 2026"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### requirements.txt 구성

| 패키지 | 용도 |
|--------|------|
| `fastapi`, `uvicorn[standard]` | 웹 서버 |
| `jinja2`, `python-multipart` | 템플릿·업로드 |
| `requests`, `python-dotenv` | HTTP·환경변수 |
| `pycryptodome` | CODEF RSA 등 |
| `pypdf` | PDF 텍스트 레이어 추출 |
| `pymupdf` | PDF 렌더·페이지 이미지 |
| `pytesseract` | Tesseract OCR 바인딩 |
| `Pillow` | 이미지 처리 |

### 설치 확인

```powershell
python -c "import app; print('APP IMPORT OK')"
```

실패 시: 가상환경 활성화 여부, `requirements.txt` 재설치, 프로젝트 루트에서 실행했는지 확인.

---

## 3. 서버 실행 방법

본선 테스트 포트: **8010**

```powershell
cd "C:\Users\DELL\Desktop\ai hack camp 2026"
python -m uvicorn app:app --host 127.0.0.1 --port 8010
```

접속: http://127.0.0.1:8010

### 포트 사용 중 확인

```powershell
netstat -ano | findstr :8010
```

### 포트 점유 프로세스 종료

```powershell
taskkill /PID <PID> /F
```

`<PID>`는 `netstat` 마지막 열 값.

---

## 4. `.env` 설정

프로젝트 루트에 `.env` 파일이 필요합니다. **실제 키 원문은 README·문서에 적지 마세요.**  
샘플·플레이스홀더만 [docs/ENV_SETUP.md](ENV_SETUP.md) 및 `.env.example` 참고.

### 중복 키 점검

```powershell
python check_env_duplicates.py
```

또는 인라인:

```powershell
python -c "from pathlib import Path; from collections import Counter; keys=[l.split('=',1)[0] for l in Path('.env').read_text(encoding='utf-8').splitlines() if l.strip() and not l.strip().startswith('#') and '=' in l]; [print('DUPLICATE',k,n) for k,n in Counter(keys).items() if n>1]"
```

---

## 5. DB / 저장소

| 항목 | 값 |
|------|-----|
| 기본 DB | `data/redribbon_final.db` |
| 환경변수 | `REDRIBBON_STORAGE_DB_PATH=./data/redribbon_final.db` |
| 보안 파일 | `REDRIBBON_SECURE_FILE_DIR=./data/secure_files` (또는 `secure_files` 폴더) |

필요 폴더:

- `data/`
- `data/secure_files/` (또는 프로젝트의 `secure_files` 정책에 맞는 경로)

### 삭제 금지 (시연·복원용)

- **준비된 보험가입이력 원부** 및 시연용 저장본
- `success_insurance_record_export.json` (경로는 `PREPARED_INSURANCE_RECORD_JSON` 등으로 지정될 수 있음)
- `data/redribbon_final_before_*.db`
- `prepared/`, `seed/`, `demo` 성격의 보험가입이력 원부 파일

고객 초기화·탈퇴 시에도 **원부 파일은 보존**해야 합니다.

---

## 6. Tesseract OCR 설치

수신 PDF OCR에 **필수**입니다.

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
```

확인:

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
Get-ChildItem "C:\Program Files\Tesseract-OCR\tessdata" -Filter "kor.traineddata"
```

`.env` 예 (경로만, 비밀값 없음):

```env
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
PRINT_RECEIVER_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
PRINT_RECEIVER_KOR_TRAINEDDATA_PATH=C:\Program Files\Tesseract-OCR\tessdata\kor.traineddata
```

---

## 7. PDFCreator 설치

**RedRibbon Printer** 자동 생성의 기반 프로그램입니다. Windows에 PDFCreator가 설치되어 있어야 합니다.

```powershell
Get-Printer -Name "PDFCreator"
```

> Windows 커널 드라이버를 직접 개발한 것이 아니라, **PDFCreator 기반 RedRibbon 전용 가상프린터**를 자동 구성합니다.

PDFCreator가 있어야 EXE 설치 시 **RedRibbon Printer** 자동 생성이 가능합니다. RedRibbon Printer는 PDFCreator의 **DriverName**, **PortName**(예: `pdfcmon`)을 사용합니다.

---

## 8. RedRibbon Print Receiver EXE 설치

| 항목 | 경로 |
|------|------|
| 배포 EXE | `static/downloads/RedRibbon_Demo_Print_Setup.exe` |
| 빌드 | `python scripts/build_print_installer.py` |
| Inno Setup | `winget install -e --id JRSoftware.InnoSetup` |
| ISCC 예 | `C:\Users\DELL\AppData\Local\Programs\Inno Setup 6\ISCC.exe` |

### 설치 후 생성·등록되는 것

- `C:\RedRibbonDemo\`
- `incoming`, `uploading`, `uploaded`, `failed`, `logs`, `print_receiver\`
- `run_redribbon_receiver.ps1`, `check_receiver_ready.ps1`, `config.json`
- 작업 스케줄러: **RedRibbonDemoReceiver**
- **RedRibbon Printer** (PDFCreator 있을 때)

확인:

```powershell
Test-Path "C:\RedRibbonDemo"
Get-Printer | Where-Object { $_.Name -like "*RedRibbon*" }
Get-ScheduledTask -TaskName "RedRibbonDemoReceiver" -ErrorAction SilentlyContinue
```

---

## 9. RedRibbon Printer 자동 생성 구조

1. `Get-Printer -Name "PDFCreator"` 로 기준 프린터 확인
2. `DriverName`, `PortName` 조회
3. 없으면 `Add-Printer`로 **RedRibbon Printer** 생성

확인 예:

```
Name: RedRibbon Printer
DriverName: PDFCreator
PortName: pdfcmon
```

**발표용:** 설치파일을 실행하면 PDFCreator 기반 RedRibbon 전용 가상프린터가 자동 생성되고, 병원 직원은 RedRibbon Printer로 출력만 하면 문서가 자동 접수됩니다.

자세한 내용: [PRINT_RECEIVER_SETUP.md](PRINT_RECEIVER_SETUP.md), `installer/RedRibbon_Demo_Print_Setup/SETUP_VIRTUAL_PRINTER.md`

---

## 10. Receiver 설정

설정 파일: `C:\RedRibbonDemo\print_receiver\config.json`

주요 키: `server_url`, `upload_endpoint`, `watch_dir`, `uploaded_dir`, `failed_dir`, `printer_name`

본선 포트 **8010**:

```json
"server_url": "http://127.0.0.1:8010"
```

실행:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\RedRibbonDemo\run_redribbon_receiver.ps1"
```

점검:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\RedRibbonDemo\check_receiver_ready.ps1"
```

정상 기대: **FAIL: 0  WARN: 0**

---

## 11. 수동 테스트 (PDF 투입)

1. 서버 실행 (8010)
2. Receiver 실행 또는 스케줄러 대기
3. PDF 복사:

```powershell
Copy-Item "C:\Users\DELL\Desktop\진료영수증_김도무11.pdf" "C:\RedRibbonDemo\incoming\receipt_test.pdf" -Force
```

4. 폴더 상태:

```powershell
Get-ChildItem "C:\RedRibbonDemo\incoming","C:\RedRibbonDemo\uploaded","C:\RedRibbonDemo\failed" -File |
  Sort-Object LastWriteTime -Descending |
  Select-Object FullName, Length, LastWriteTime -First 10
```

5. 운영자 수신문서함: http://127.0.0.1:8010/operator/received-documents

---

## 12. 프린터 인쇄 테스트

1. 임의 문서 인쇄 → 프린터 **RedRibbon Printer** 선택
2. `C:\RedRibbonDemo\incoming` 또는 `uploaded`에 PDF 생성 여부 확인
3. 운영자 수신문서함에서 수신·OCR·매칭 확인

---

## 13. 접속 주소 (8010)

| 용도 | URL |
|------|-----|
| 인트로 | http://127.0.0.1:8010/ |
| 고객용 | http://127.0.0.1:8010/customer/chat |
| 병원용 | http://127.0.0.1:8010/hospital/start |
| 운영자 | http://127.0.0.1:8010/operator |
| 수신 문서함 | http://127.0.0.1:8010/operator/received-documents |

---

## 14. 준비본 / 시연 안정화

- CODEF 일 호출 한도, 외부 인증 지연 등으로 현장 변동이 있을 수 있습니다.
- 본선 시연 안정화를 위해 **준비된 진료내역 저장본**, **준비된 보험가입이력 원부**를 **현재 고객 기준 복원**하는 흐름이 있습니다.
- 해당 파일·DB는 삭제하지 마세요.

---

## 15. OpenAI 설정

- `OPENAI_API_KEY`: 필수(권장). 문서·Git에 **실제 키 기재 금지**. 유출 시 **즉시 폐기·재발급**.
- `OPENAI_MODEL`: 예 `gpt-4o-mini` (미설정 시 코드 기본값 사용)
- `OPENAI_TIMEOUT_SECONDS`: API 대기 시간(초). 예 `60`

---

## 16. 문제 해결

| 증상 | 조치 |
|------|------|
| 8010 포트 충돌 | `netstat` → `taskkill` 또는 다른 포트 사용(Receiver `server_url`도 동일하게) |
| APP IMPORT 실패 | venv, `pip install -r requirements.txt`, 프로젝트 루트에서 실행 |
| Tesseract not found | winget 설치, `.env`에 `TESSERACT_CMD` / `PRINT_RECEIVER_TESSERACT_CMD` |
| `kor.traineddata` 없음 | Tesseract 재설치 또는 tessdata에 한국어 팩 배치 |
| RedRibbon Printer 안 보임 | PDFCreator 설치 후 EXE 재실행 또는 `SETUP_VIRTUAL_PRINTER.md` |
| Receiver만 있고 프린터 없음 | PDFCreator 미설치 시 WARN만 나오고 프린터 생략됨 |
| `C:\RedRibbonDemo` 없음 | `RedRibbon_Demo_Print_Setup.exe` 재설치 |
| 작업 스케줄러 없음 | EXE 재설치, `Get-ScheduledTask -TaskName RedRibbonDemoReceiver` |
| `server_url`이 8000 | `config.json`을 `http://127.0.0.1:8010`으로 수정 |
| OCR `text_len=0` | Tesseract·한글 데이터·`PRINT_RECEIVER_OCR_DPI` 확인 |
| CODEF `CF-00012` | 토큰·키·`CODEF_USE_DEMO`·네트워크, 일 호출 한도 |
| PDFCreator 미설치 | PDFCreator 설치 후 프린터 자동 생성 단계 재실행 |
| Edge EXE 다운로드 경고 | “유지” 또는 파일 속성 차단 해제, 신뢰할 수 있는 출처에서만 설치 |

---

## 17. 설치 상태 점검 (한 번에)

```powershell
cd "C:\Users\DELL\Desktop\ai hack camp 2026"

# APP IMPORT
python -c "import app; print('APP IMPORT OK')"

# DB 경로
python -c "import os; from pathlib import Path; p=os.getenv('REDRIBBON_STORAGE_DB_PATH') or os.getenv('REDDRIBBON_STORAGE_DB_PATH') or './data/redribbon_final.db'; print('DB', Path(p).resolve(), 'exists=', Path(p).exists())"

# Tesseract
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
Test-Path "C:\Program Files\Tesseract-OCR\tessdata\kor.traineddata"

# PDFCreator
Get-Printer -Name "PDFCreator" -ErrorAction SilentlyContinue | Format-Table Name, DriverName, PortName

# RedRibbon Printer
Get-Printer -Name "RedRibbon Printer" -ErrorAction SilentlyContinue | Format-Table Name, DriverName, PortName

# Receiver
Test-Path "C:\RedRibbonDemo"
powershell -ExecutionPolicy Bypass -File "C:\RedRibbonDemo\check_receiver_ready.ps1"

# 서버 8010
try { (Invoke-WebRequest -Uri "http://127.0.0.1:8010/" -UseBasicParsing -TimeoutSec 5).StatusCode } catch { $_.Exception.Message }
```

---

## 18. EXE 빌드 (개발자)

```powershell
winget install -e --id JRSoftware.InnoSetup
cd "C:\Users\DELL\Desktop\ai hack camp 2026"
python scripts/build_print_installer.py
```

산출물: `static/downloads/RedRibbon_Demo_Print_Setup.exe`

---

관련 문서: [ENV_SETUP.md](ENV_SETUP.md) · [PRINT_RECEIVER_SETUP.md](PRINT_RECEIVER_SETUP.md) · [../README_WINDOWS_SETUP.md](../README_WINDOWS_SETUP.md)
