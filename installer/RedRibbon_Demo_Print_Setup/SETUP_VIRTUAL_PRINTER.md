# RedRibbon 전용 가상프린터 (PDFCreator · RedRibbon Printer)

본선 시연은 **C:\RedRibbonDemo** 에 RedRibbon Print Receiver Engine(병원 출력물 자동 수신 모듈)을 설치합니다.

Windows 커널 드라이버를 직접 개발한 것이 아니라, **PDFCreator 기반 RedRibbon 전용 가상프린터**를 설치 스크립트/EXE로 자동 구성합니다.

## PDFCreator 기반 RedRibbon 전용 가상프린터 자동 구성

`install_redribbon_demo.ps1` 또는 `RedRibbon_Demo_Print_Setup.exe` 가 다음을 수행합니다.

1. `Get-Printer -Name "PDFCreator"` 로 기준 프린터 확인
2. 기준 프린터의 `DriverName`, `PortName` 사용
3. **RedRibbon Printer** 가 없으면 `Add-Printer` 로 생성 (이미 있으면 유지)
4. PDFCreator 레지스트리에 프로필 **RedRibbon Auto Save** 생성·갱신
   - 자동저장(Automatic) ON, Interactive OFF
   - 대상 폴더: `C:\RedRibbonDemo\incoming`
   - 파일명: `redribbon_<PrintJobName>_<DateTime>.pdf`
   - Open file / PDF Architect / Email 액션 OFF
5. **RedRibbon Printer** → 프로필 **RedRibbon Auto Save** 연결 (`PrinterMappings` + `ProfileName`)
6. 설정 반영 전·후 **PDFCreator / PDFCreator-cli 프로세스 종료** (실행 중이면 레지스트리가 덮어써질 수 있음)
7. Receiver Engine·작업 스케줄러·폴더 구성

### GUI에서 연결 확인

PDFCreator → **Application Settings** → **Printers** 탭:

| Printer | Profile (표시) |
|---------|----------------|
| RedRibbon Printer | RedRibbon Auto Save |

레지스트리: `PrinterMappings\<n>\ProfileGuid` = `RedRibbonAutoSaveGuid`, `ProfileName` = `RedRibbon Auto Save`

PDFCreator가 설치되어 있지 않으면 설치는 **중단하지 않고** WARN만 표시합니다.

> PDFCreator가 설치되어 있지 않아 RedRibbon Printer 자동 생성은 건너뜁니다.

## 설치 완료 안내

설치파일을 실행하면 PDFCreator 기반 RedRibbon 전용 가상프린터가 자동 생성되고, 병원 직원은 **RedRibbon Printer**로 출력만 하면 문서가 자동 접수됩니다.

인쇄 시 **PDFCreator 저장 대화상자가 뜨지 않아야** 하며, PDF는 `C:\RedRibbonDemo\incoming` 에 저장됩니다.

## 설정 위치 (참고)

주 설정 경로(레지스트리):

```
HKCU\Software\pdfforge\PDFCreator\Settings\ConversionProfiles\<index>
HKCU\Software\pdfforge\PDFCreator\Settings\ApplicationSettings\PrinterMappings
```

보조 탐색 경로(파일):

- `%APPDATA%\pdfforge\PDFCreator`
- `%LOCALAPPDATA%\pdfforge\PDFCreator`
- `C:\ProgramData\pdfforge\PDFCreator`

## 동작 흐름

1. 병원 EMR·OCS에서 인쇄 대상 **RedRibbon Printer** 선택
2. PDFCreator가 **RedRibbon Auto Save** 프로필로 PDF를 `incoming` 에 자동 저장
3. Receiver Engine이 파일을 감지해 `http://127.0.0.1:8010/api/print-receiver/upload` 로 전송

## 점검

```powershell
powershell -ExecutionPolicy Bypass -File C:\RedRibbonDemo\check_receiver_ready.ps1
Get-Printer -Name "RedRibbon Printer"
Get-Printer | Where-Object { $_.Name -like "*RedRibbon*" } | Select Name, DriverName, PortName
```

- `redribbon_printer` **[OK]**: RedRibbon Printer 준비
- `pdfcreator_autosave_profile` **[OK]**: 자동저장 프로필·프린터 연결 확인

프로필 연결이 불확실하면 **[WARN]**: RedRibbon Printer는 생성되었지만 PDFCreator 자동저장 프로필 연결 확인이 필요합니다.

## 수동 fallback (자동설정 실패 시만)

1. PDFCreator → **Profiles** → **RedRibbon Auto Save** (없으면 동일 이름으로 생성)
2. **Save**: Interactive OFF / Automatic ON / Target directory: `C:\RedRibbonDemo\incoming`
3. **Actions**: Open file / PDF Architect OFF / Email OFF
4. **Printer** 탭: **RedRibbon Printer** → 프로필 **RedRibbon Auto Save** 선택 → Save
