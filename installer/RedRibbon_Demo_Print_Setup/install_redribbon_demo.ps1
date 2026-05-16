# RedRibbon Demo Print Receiver one-click install (C:\RedRibbonDemo)
param(
    [string]$InstallRoot = "C:\RedRibbonDemo",
    [switch]$SkipScheduledTask,
    [switch]$SkipDesktopShortcut
)

$ErrorActionPreference = "Continue"
$packageRoot = $PSScriptRoot
$incomingDir = Join-Path $InstallRoot "incoming"
$logDir = Join-Path $InstallRoot "logs"
$installLog = Join-Path $logDir "install.log"

$dirs = @(
    "incoming", "uploading", "uploaded", "failed", "logs", "print_receiver"
)

function Write-InstallLog {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    try {
        if (-not (Test-Path $logDir)) {
            New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        }
        Add-Content -Path $installLog -Value $line -Encoding UTF8
    } catch {
        # ignore log write errors
    }
    Write-Host $line
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Get-DefaultConfigJson {
    @"
{
  "server_url": "http://127.0.0.1:8000",
  "upload_endpoint": "/api/print-receiver/upload",
  "watch_dir": "C:\\RedRibbonDemo\\incoming",
  "uploading_dir": "C:\\RedRibbonDemo\\uploading",
  "uploaded_dir": "C:\\RedRibbonDemo\\uploaded",
  "failed_dir": "C:\\RedRibbonDemo\\failed",
  "log_dir": "C:\\RedRibbonDemo\\logs",
  "hospital_name": "TEST_HOSPITAL",
  "printer_name": "redribbon",
  "poll_interval_seconds": 2,
  "stable_wait_seconds": 2,
  "upload_timeout_seconds": 900
}
"@
}

function Find-PdfCreatorInstall {
    $candidates = @(
        (Join-Path ${env:ProgramFiles} "PDFCreator"),
        (Join-Path ${env:ProgramFiles(x86)} "PDFCreator"),
        (Join-Path ${env:ProgramFiles} "pdfforge\PDFCreator"),
        (Join-Path ${env:ProgramFiles(x86)} "pdfforge\PDFCreator")
    )
    foreach ($dir in $candidates) {
        if ($dir -and (Test-Path (Join-Path $dir "PDFCreator.exe"))) {
            return $dir
        }
    }
    $cmd = Get-Command "PDFCreator.exe" -ErrorAction SilentlyContinue
    if ($cmd) {
        return Split-Path $cmd.Source -Parent
    }
    return $null
}

function Test-RedRibbonPrinterPresent {
    $names = @("redribbon", "RedRibbon")
    foreach ($n in $names) {
        if (Get-Printer -Name $n -ErrorAction SilentlyContinue) {
            return $true
        }
    }
    try {
        $all = Get-Printer -ErrorAction SilentlyContinue | ForEach-Object { $_.Name }
        foreach ($n in $names) {
            if ($all -contains $n) { return $true }
        }
        foreach ($p in $all) {
            if ($p -match 'redribbon|RedRibbon|Ribbon') { return $true }
        }
    } catch {
        # ignore
    }
    return $false
}

function Try-ConfigurePdfCreatorAutoSave {
    param([string]$TargetIncoming)
    $result = @{
        PdfCreatorInstalled = $false
        AutoConfigured      = $false
        PrinterFound        = $false
        InstallPath         = ""
        Note                = ""
    }

    $installPath = Find-PdfCreatorInstall
    if ($installPath) {
        $result.PdfCreatorInstalled = $true
        $result.InstallPath = $installPath
    }

    $result.PrinterFound = Test-RedRibbonPrinterPresent

    $settingsRoots = @(
        (Join-Path $env:LOCALAPPDATA "PDFCreator"),
        (Join-Path $env:APPDATA "PDFCreator"),
        (Join-Path $env:APPDATA "pdfforge\PDFCreator")
    )

    $escapedIncoming = [regex]::Escape($TargetIncoming)
    foreach ($root in $settingsRoots) {
        if (-not (Test-Path $root)) { continue }
        Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in @(".ini", ".json", ".xml", ".config") } |
            ForEach-Object {
                try {
                    $raw = [System.IO.File]::ReadAllText($_.FullName)
                    if ($raw -notmatch 'redribbon|RedRibbon') { return }
                    $updated = $raw
                    $updated = $updated -replace '(?i)(AutoSaveDirectory|SaveDirectory|TargetDirectory|OutputDirectory|AutosaveTargetDirectory)\s*[=:]\s*[^\r\n"]+', "`${1}=$TargetIncoming"
                    $updated = $updated -replace '(?i)"(AutoSaveDirectory|SaveDirectory|TargetDirectory|OutputDirectory)"\s*:\s*"[^"]*"', "`"`$1`":`"$($TargetIncoming -replace '\\','\\')`""
                    if ($updated -ne $raw) {
                        $bak = "$($_.FullName).redribbon.bak"
                        if (-not (Test-Path $bak)) {
                            Copy-Item $_.FullName $bak -Force -ErrorAction SilentlyContinue
                        }
                        Write-Utf8NoBom -Path $_.FullName -Content $updated
                        $result.AutoConfigured = $true
                    }
                } catch {
                    Write-InstallLog "PDFCreator settings skip: $($_.Exception.Message)"
                }
            }
    }

    $regRoots = @(
        "HKCU:\Software\PDFCreator",
        "HKCU:\Software\pdfcreator",
        "HKCU:\Software\pdfforge\PDFCreator"
    )
    foreach ($regRoot in $regRoots) {
        if (-not (Test-Path $regRoot)) { continue }
        Get-ChildItem -Path $regRoot -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $keyName = $_.PSChildName
                $props = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
                if (-not $props) { return }
                $blob = ($props.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join " "
                if ($keyName -notmatch 'redribbon|RedRibbon' -and $blob -notmatch 'redribbon|RedRibbon') {
                    return
                }
                foreach ($prop in $props.PSObject.Properties) {
                    if ($prop.Name -in @("PSPath", "PSParentPath", "PSChildName", "PSDrive", "PSProvider")) { continue }
                    if ($prop.Value -is [string] -and $prop.Value -match '\\' -and $prop.Name -match 'Folder|Directory|Path|Save|Target|Output') {
                        Set-ItemProperty -Path $_.PSPath -Name $prop.Name -Value $TargetIncoming -ErrorAction SilentlyContinue
                        $result.AutoConfigured = $true
                    }
                }
            } catch {
                # ignore registry write errors
            }
        }
    }

    if (-not $result.PdfCreatorInstalled) {
        $result.Note = "PDFCreator not detected"
    } elseif (-not $result.AutoConfigured) {
        $result.Note = "auto_config_uncertain"
    }
    return $result
}

function Show-ManualPdfCreatorGuide {
    Write-Host ""
    Write-Host "=== PDFCreator manual setup ===" -ForegroundColor Yellow
    Write-Host "Set redribbon printer/profile auto-save folder to:"
    Write-Host "  C:\RedRibbonDemo\incoming"
    Write-Host "See SETUP_VIRTUAL_PRINTER.md in the install folder for details."
    Write-Host ""
}

function New-DesktopReceiverShortcut {
    param([string]$Root)
    try {
        $desktop = [Environment]::GetFolderPath("Desktop")
        $lnkPath = Join-Path $desktop "RedRibbon Receiver Run.lnk"
        $runScript = Join-Path $Root "run_redribbon_receiver.ps1"
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($lnkPath)
        $shortcut.TargetPath = "powershell.exe"
        $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
        $shortcut.WorkingDirectory = $Root
        $shortcut.WindowStyle = 1
        $shortcut.Description = "RedRibbon Print Receiver Engine"
        $shortcut.Save()
        Write-InstallLog "desktop_shortcut: $lnkPath"
        return $true
    } catch {
        Write-InstallLog "desktop_shortcut_failed: $($_.Exception.Message)"
        return $false
    }
}

function Register-ReceiverScheduledTask {
    param([string]$Root)
    $registerScript = Join-Path $PSScriptRoot "register_receiver_task.ps1"
    if (-not (Test-Path $registerScript)) {
        $registerScript = Join-Path (Split-Path $PSScriptRoot -Parent) "scripts\register_receiver_task.ps1"
    }
    if (Test-Path $registerScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $registerScript -InstallRoot $Root
        return ($LASTEXITCODE -eq 0)
    }

    try {
        $runScript = Join-Path $Root "run_redribbon_receiver.ps1"
        $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argList -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName "RedRibbonDemoReceiver" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
        return $true
    } catch {
        Write-InstallLog "scheduled_task_failed: $($_.Exception.Message)"
        return $false
    }
}

Write-InstallLog "install_start package=$packageRoot target=$InstallRoot"

foreach ($d in $dirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallRoot $d) | Out-Null
}

$destReceiver = Join-Path $InstallRoot "print_receiver"
New-Item -ItemType Directory -Force -Path $destReceiver | Out-Null

$copyPairs = @(
    @("print_receiver\receiver_engine.py", "print_receiver\receiver_engine.py"),
    @("print_receiver\check_receiver_ready.ps1", "print_receiver\check_receiver_ready.ps1"),
    @("check_receiver_ready.ps1", "check_receiver_ready.ps1"),
    @("run_redribbon_receiver.ps1", "run_redribbon_receiver.ps1"),
    @("register_receiver_task.ps1", "register_receiver_task.ps1"),
    @("SETUP_VIRTUAL_PRINTER.md", "SETUP_VIRTUAL_PRINTER.md"),
    @("README.txt", "README.txt")
)

foreach ($pair in $copyPairs) {
    $src = Join-Path $packageRoot $pair[0]
    $dst = Join-Path $InstallRoot $pair[1]
    if (-not (Test-Path $src)) { continue }
    $dstParent = Split-Path -Parent $dst
    if ($dstParent -and -not (Test-Path $dstParent)) {
        New-Item -ItemType Directory -Force -Path $dstParent | Out-Null
    }
    if ($src -match '\.py$') {
        Copy-Item -Path $src -Destination $dst -Force
    } else {
        $content = Get-Content $src -Raw -Encoding UTF8
        Write-Utf8NoBom -Path $dst -Content $content
    }
    Write-InstallLog "copied $($pair[0])"
}

$configDst = Join-Path $InstallRoot "print_receiver\config.json"
$configSrc = Join-Path $packageRoot "print_receiver\config.json"
if (Test-Path $configSrc) {
    $json = Get-Content $configSrc -Raw -Encoding UTF8
} else {
    $json = Get-DefaultConfigJson
}
Write-Utf8NoBom -Path $configDst -Content $json
Write-InstallLog "config.json written"

$runSrc = Join-Path $packageRoot "run_redribbon_receiver.ps1"
if (Test-Path $runSrc) {
    Write-Utf8NoBom -Path (Join-Path $InstallRoot "run_redribbon_receiver.ps1") -Content (Get-Content $runSrc -Raw -Encoding UTF8)
}

$taskOk = $false
if (-not $SkipScheduledTask) {
    $taskOk = Register-ReceiverScheduledTask -Root $InstallRoot
    if ($taskOk) {
        Write-InstallLog "scheduled_task: RedRibbonDemoReceiver OK"
        Write-Host "[OK] Scheduled task RedRibbonDemoReceiver registered" -ForegroundColor Green
    } else {
        Write-InstallLog "scheduled_task: manual start required"
        Write-Host "[WARN] Scheduled task registration failed — start manually: C:\RedRibbonDemo\run_redribbon_receiver.ps1" -ForegroundColor Yellow
    }
} else {
    Write-InstallLog "scheduled_task: skipped"
}

if (-not $SkipDesktopShortcut) {
    if (New-DesktopReceiverShortcut -Root $InstallRoot) {
        Write-Host "[OK] Desktop shortcut created" -ForegroundColor Green
    }
}

$pdfResult = Try-ConfigurePdfCreatorAutoSave -TargetIncoming $incomingDir
Write-InstallLog "pdfcreator installed=$($pdfResult.PdfCreatorInstalled) auto=$($pdfResult.AutoConfigured) printer=$($pdfResult.PrinterFound)"

if ($pdfResult.PdfCreatorInstalled) {
    Write-Host "[OK] PDFCreator found: $($pdfResult.InstallPath)" -ForegroundColor Green
} else {
    Write-Host "[WARN] PDFCreator not found — install PDFCreator and configure redribbon profile" -ForegroundColor Yellow
}

if ($pdfResult.AutoConfigured) {
    Write-Host "[OK] PDFCreator auto-save path update attempted for redribbon profile" -ForegroundColor Green
} else {
    Show-ManualPdfCreatorGuide
}

Write-Host ""
Write-Host "Install complete: $InstallRoot" -ForegroundColor Cyan
Write-Host "  Receiver: C:\RedRibbonDemo\run_redribbon_receiver.ps1"
Write-Host "  Check:    C:\RedRibbonDemo\check_receiver_ready.ps1"
Write-Host "  Incoming: C:\RedRibbonDemo\incoming"
Write-InstallLog "install_complete"
