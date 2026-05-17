# RedRibbon MVP (AI Hack Camp 2026)

병원·고객·운영자 흐름을 하나의 FastAPI 앱으로 제공하는 **래드리본(RedRibbon)** 본선 시연 프로젝트입니다.  
진료내역(CODEF 심평원), 보험가입이력(신용정보원 Credit4u), AI 청구 검토, 가상프린터 수신(Print Receiver)을 포함합니다.

## Windows PC에서 실행하기

일반 Windows 10/11 PC에서 가동하려면 아래 문서를 순서대로 따르세요.

| 문서 | 내용 |
|------|------|
| [README_WINDOWS_SETUP.md](README_WINDOWS_SETUP.md) | 설치·실행 요약(빠른 시작) |
| [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md) | **전체** 환경·부가 프로그램·점검·문제 해결 |
| [docs/ENV_SETUP.md](docs/ENV_SETUP.md) | `.env` 변수 설명(값 예시만, 비밀값 없음) |
| [docs/PRINT_RECEIVER_SETUP.md](docs/PRINT_RECEIVER_SETUP.md) | PDFCreator·RedRibbon Printer·Receiver |
| [installer/RedRibbon_Demo_Print_Setup/README.txt](installer/RedRibbon_Demo_Print_Setup/README.txt) | EXE 설치 후 로컬 경로 안내 |

## 최소 실행 순서 (요약)

1. Python 3.11+ 설치, `pip install -r requirements.txt`
2. `.env` 작성 (`.env.example` 참고)
3. `data/`, `data/secure_files/` 준비
4. (권장) Tesseract OCR, PDFCreator 설치
5. `python -m uvicorn app:app --host 127.0.0.1 --port 8010`
6. 브라우저: http://127.0.0.1:8010/
7. (선택) `static/downloads/RedRibbon_Demo_Print_Setup.exe` 로 Print Receiver 설치

## 본선 테스트 포트

- **8010** (`http://127.0.0.1:8010`)
- Print Receiver `config.json`의 `server_url`도 동일 포트로 맞출 것

## 접속 주소 (8010 기준)

| 용도 | URL |
|------|-----|
| 인트로 | http://127.0.0.1:8010/ |
| 고객용 | http://127.0.0.1:8010/customer/chat |
| 병원용 | http://127.0.0.1:8010/hospital/start |
| 운영자 | http://127.0.0.1:8010/operator |
| 수신 문서함 | http://127.0.0.1:8010/operator/received-documents |

## Python 패키지

```powershell
cd "C:\Users\DELL\Desktop\ai hack camp 2026"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -c "import app; print('APP IMPORT OK')"
```

## 저장소·시연 데이터 보호

- 기본 DB: `data/redribbon_final.db`
- **준비된 진료내역 저장본**, **준비된 보험가입이력 원부**, `data/redribbon_final_before_*.db` 등 시연·복원용 파일은 **삭제·덮어쓰기 금지**
- 고객 초기화·탈퇴 시에도 원부 파일은 보존

## 민감 정보

README·문서에는 CODEF/OpenAI **실제 키를 적지 마세요**. 노출된 키는 폐기 후 재발급하세요.

## 저장소

https://github.com/lolon0226/ai-hack-camp-2026
