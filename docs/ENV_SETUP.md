# `.env` 설정 가이드

프로젝트 루트의 `.env`는 **git에 커밋하지 않습니다**.  
아래는 변수 **이름과 설명**만 정리합니다. 값은 `<YOUR_KEY>`, `your_*_secret` 같은 **플레이스홀더**로 작성하세요.

> **주의:** CODEF client secret, OpenAI API key, 세션·암호화 시크릿 등이 README·슬랙·스크린샷에 노출되면 **즉시 폐기**하고 새로 발급하세요.

## 준비

```powershell
cd "C:\Users\DELL\Desktop\ai hack camp 2026"
copy .env.example .env
# 메모장 등으로 .env 편집 (실제 값 입력)
```

## 필수·권장 변수 목록

### CODEF 공통

| 변수 | 설명 | 예시(비밀 아님) |
|------|------|-----------------|
| `CODEF_ENV` | 환경 구분 | `development` |
| `CODEF_BASE_URL` | API 베이스 URL | `https://development.codef.io` |
| `CODEF_CLIENT_ID` | 상용 클라이언트 ID | `<YOUR_CODEF_CLIENT_ID>` |
| `CODEF_CLIENT_SECRET` | 상용 시크릿 | `<YOUR_CODEF_CLIENT_SECRET>` |
| `CODEF_PUBLIC_KEY` | RSA 공개키(비밀번호 암호화) | `<YOUR_CODEF_PUBLIC_KEY>` |
| `CODEF_USE_DEMO` | 데모 키 사용 여부 | `1` 또는 `0` |
| `CODEF_DEMO_CLIENT_ID` | 데모 클라이언트 ID | `<YOUR_DEMO_CLIENT_ID>` |
| `CODEF_DEMO_CLIENT_SECRET` | 데모 시크릿 | `<YOUR_DEMO_CLIENT_SECRET>` |
| `CODEF_REAL_CALL_ENABLED` | 실제 CODEF 호출 허용 | `1` |
| `CODEF_HIRA_MEDICAL_PATH` | 심평원 진료내역 경로 | `/v1/kr/public/hw/hira-list/my-medical-information` |

### Credit4u (보험가입이력)

| 변수 | 설명 |
|------|------|
| `CREDIT4U_ORGANIZATION` | 기관 코드 (예: `0001`) |
| `CODEF_CREDIT4U_REGISTER_PATH` | 가입(register) API 경로 |
| `CODEF_CREDIT4U_CONTRACT_INFO_PATH` | contract-info API 경로 |
| `REDRIBBON_CREDIT4U_SECRET` | Credit4u ID·해시용 시크릿 |
| `CREDIT4U_ID_PREFIX` | ID 접두 (기본 `rr`) |

### 앱·보안·DB

| 변수 | 설명 |
|------|------|
| `DEMO_MODE` | 데모 UI/동작 플래그 |
| `SESSION_SECRET` | 세션 서명용 시크릿 |
| `REDRIBBON_DATA_ENCRYPTION_KEY` | 저장 데이터 암호화 키 |
| `REDRIBBON_SEARCH_HASH_SECRET` | 고객 검색 해시용 시크릿 |
| `REDRIBBON_STORAGE_DB_PATH` | SQLite 경로 (예: `./data/redribbon_final.db`) |
| `REDDRIBBON_SECURE_FILE_DIR` | 보안 파일 디렉터리 (예: `./data/secure_files`) |

### 디버그

| 변수 | 설명 |
|------|------|
| `DEBUG_PANEL_ENABLED` | 운영자 디버그 패널 |
| `DEBUG_RAW_CODEF_ENABLED` | CODEF 원문 로그(시연 후 `0` 권장) |

### OpenAI

| 변수 | 설명 |
|------|------|
| `OPENAI_API_KEY` | API 키 — **문서에 원문 금지** |
| `OPENAI_MODEL` | 모델명 (예: `gpt-4o-mini`) |
| `OPENAI_TIMEOUT_SECONDS` | HTTP 타임아웃(초, 예: `60`) |

### Tesseract / Print Receiver (서버 OCR)

| 변수 | 설명 |
|------|------|
| `TESSERACT_CMD` | `tesseract.exe` 전체 경로 |
| `PRINT_RECEIVER_TESSERACT_CMD` | 수신 PDF OCR용 (미설정 시 `TESSERACT_CMD`) |
| `PRINT_RECEIVER_KOR_TRAINEDDATA_PATH` | `kor.traineddata` 경로 |
| `PRINT_RECEIVER_OCR_DPI` | 렌더 DPI (예: `300`) |
| `PRINT_RECEIVER_OCR_MAX_PAGES` | 최대 OCR 페이지 수 |
| `PRINT_RECEIVER_MIN_PDF_TEXT_CHARS` | 텍스트 레이어 최소 글자 수 |
| `PRINT_RECEIVER_API_KEY` | 업로드 API 키(설정 시) |
| `PRINT_RECEIVER_OCR_STRONG` | 강화 OCR 모드 (`1`/`0`) |

### 시연·준비본 (선택)

| 변수 | 설명 |
|------|------|
| `REDRIBBON_PREPARED_INSURANCE_DEMO` | 준비된 보험가입이력 원부 복원 흐름 |
| `PREPARED_INSURANCE_RECORD_JSON` | 준비된 보험가입이력 원부 JSON 경로 |
| `PREPARED_MEDICAL_BACKUP_DB` | 준비된 진료내역 저장본 DB 경로 |

### 기타 (`.env.example` 참고)

- `FINAL_MODE`, `STRICT_ENV`
- `CREDIT4U_TIMEOUT`, `CREDIT4U_*` 인증·타임아웃 관련
- `CODEF_ORGANIZATION`, `CODEF_INSURANCE_CONTRACT_PATH` (레거시 별칭)

## 예시 `.env` 조각 (비밀값 없음)

```env
CODEF_ENV=development
CODEF_BASE_URL=https://development.codef.io
CODEF_CLIENT_ID=<YOUR_CODEF_CLIENT_ID>
CODEF_CLIENT_SECRET=<YOUR_CODEF_CLIENT_SECRET>
CODEF_PUBLIC_KEY=<YOUR_CODEF_PUBLIC_KEY>
CODEF_USE_DEMO=1
CODEF_DEMO_CLIENT_ID=<YOUR_DEMO_CLIENT_ID>
CODEF_DEMO_CLIENT_SECRET=<YOUR_DEMO_CLIENT_SECRET>
CODEF_REAL_CALL_ENABLED=1
DEMO_MODE=0

CODEF_HIRA_MEDICAL_PATH=/v1/kr/public/hw/hira-list/my-medical-information
CREDIT4U_ORGANIZATION=0001
CODEF_CREDIT4U_REGISTER_PATH=/v1/kr/insurance/0001/credit4u/register
CODEF_CREDIT4U_CONTRACT_INFO_PATH=/v1/kr/insurance/0001/credit4u/contract-info

SESSION_SECRET=<YOUR_SESSION_SECRET>
REDRIBBON_CREDIT4U_SECRET=<YOUR_CREDIT4U_SECRET>
REDRIBBON_DATA_ENCRYPTION_KEY=<YOUR_ENCRYPTION_KEY>
REDRIBBON_SEARCH_HASH_SECRET=<YOUR_SEARCH_HASH_SECRET>
REDRIBBON_STORAGE_DB_PATH=./data/redribbon_final.db
REDDRIBBON_SECURE_FILE_DIR=./data/secure_files

DEBUG_PANEL_ENABLED=0
DEBUG_RAW_CODEF_ENABLED=0

OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>
OPENAI_MODEL=gpt-4o-mini
OPENAI_TIMEOUT_SECONDS=60

TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
PRINT_RECEIVER_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
PRINT_RECEIVER_KOR_TRAINEDDATA_PATH=C:\Program Files\Tesseract-OCR\tessdata\kor.traineddata
PRINT_RECEIVER_OCR_DPI=300
PRINT_RECEIVER_OCR_MAX_PAGES=26
```

## 중복 키 점검

```powershell
python check_env_duplicates.py
```

출력이 없으면 중복 없음. `DUPLICATE KEY_NAME 2` 형태면 `.env`에서 중복 줄 제거.

## Receiver `config.json` (별도 파일)

서버 `.env`와 별도로 `C:\RedRibbonDemo\print_receiver\config.json`의 `server_url`을 본선 포트에 맞출 것:

```json
"server_url": "http://127.0.0.1:8010"
```

기본 템플릿은 `8000`일 수 있으므로 설치 후 반드시 확인.

[WINDOWS_SETUP.md](WINDOWS_SETUP.md) · [PRINT_RECEIVER_SETUP.md](PRINT_RECEIVER_SETUP.md)
