; RedRibbon Demo — 병원 출력물 자동 수신 모듈 설치 (Inno Setup 6)
; 빌드: ISCC.exe setup.iss  →  static\downloads\RedRibbon_Demo_Print_Setup.exe

#define MyAppName "RedRibbon Demo Print Receiver"
#define MyAppVersion "1.1.0"
#define MyInstallDir "C:\RedRibbonDemo"
#define MyPublisher "RedRibbon"
#define MyExeName "run_redribbon_receiver.ps1"

[Setup]
AppId={{A8F3C2E1-9B4D-4E6A-RRD1-DEMO20260001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyPublisher}
DefaultDirName={#MyInstallDir}
DisableDirPage=yes
DisableProgramGroupPage=yes
DefaultGroupName=RedRibbon
OutputBaseFilename=RedRibbon_Demo_Print_Setup
OutputDir=..\..\static\downloads
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\print_receiver\receiver_engine.py

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Messages]
korean.FinishedLabel=설치가 완료되었습니다.%n%nPDFCreator 기반 RedRibbon 전용 가상프린터가 자동 구성되었습니다.%n병원 문서를 RedRibbon Printer로 인쇄하면 C:\RedRibbonDemo\incoming 폴더로 저장되고,%nReceiver Engine이 이를 서버로 전송합니다.%n%n[설치된 항목]%n· C:\RedRibbonDemo 및 Receiver Engine%n· RedRibbon Printer (PDFCreator 기준 자동 생성 시도)%n· 작업 스케줄러 RedRibbonDemoReceiver%n%nPDFCreator가 없으면 프린터 자동 생성은 건너뜁니다. 점검: 「수신 엔진 점검」

[Dirs]
Name: "{app}\print_receiver"
Name: "{app}\incoming"
Name: "{app}\uploading"
Name: "{app}\uploaded"
Name: "{app}\failed"
Name: "{app}\logs"

[Files]
Source: "print_receiver\receiver_engine.py"; DestDir: "{app}\print_receiver"; Flags: ignoreversion
Source: "print_receiver\config.json"; DestDir: "{app}\print_receiver"; Flags: ignoreversion
Source: "print_receiver\check_receiver_ready.ps1"; DestDir: "{app}\print_receiver"; Flags: ignoreversion
Source: "run_redribbon_receiver.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "run_redribbon_receiver.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "install_redribbon_demo.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "register_receiver_task.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "check_receiver_ready.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "SETUP_VIRTUAL_PRINTER.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userdesktop}\RedRibbon Receiver 실행"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\{#MyExeName}"""; WorkingDir: "{app}"; Comment: "RedRibbon Print Receiver Engine"
Name: "{group}\RedRibbon Receiver 실행"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\{#MyExeName}"""; WorkingDir: "{app}"
Name: "{group}\수신 엔진 점검"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\check_receiver_ready.ps1"""
Name: "{group}\설치 폴더 열기"; Filename: "{app}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_redribbon_demo.ps1"""; WorkingDir: "{app}"; Flags: waituntilterminated; StatusMsg: "RedRibbon Demo 설치 구성(폴더·작업 스케줄러·PDFCreator 확인)…"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
