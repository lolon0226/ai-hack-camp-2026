# RedRibbon Demo 설치파일 빌드

## 요구 사항

- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`ISCC.exe`)

## 빌드

```powershell
cd "C:\Users\DELL\Desktop\ai hack camp 2026"
python scripts\build_print_installer.py
```

또는:

```powershell
python scripts\build_print_installer_zip.py
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "installer\RedRibbon_Demo_Print_Setup\setup.iss"
```

## 산출물

- `static\downloads\RedRibbon_Demo_Print_Setup.exe` — RedRibbon 전용 출력 수신 엔진 설치파일
- `static\downloads\RedRibbon_Demo_Print_Setup.zip` — ZIP 패키지(기존)

설치 경로: `C:\RedRibbonDemo` (고정)
