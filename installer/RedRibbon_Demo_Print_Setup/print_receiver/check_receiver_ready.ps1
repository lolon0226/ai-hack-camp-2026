# RedRibbon Demo Print Receiver readiness check (C:\RedRibbonDemo)
$ErrorActionPreference = "Continue"
$root = "C:\RedRibbonDemo"
$requiredDirs = @("incoming", "uploading", "uploaded", "failed", "logs")
$configPath = Join-Path $root "print_receiver\config.json"
$enginePath = Join-Path $root "print_receiver\receiver_engine.py"
$runScriptPath = Join-Path $root "run_redribbon_receiver.ps1"
$defaultServerUrl = "http://127.0.0.1:8000"
$taskName = "RedRibbonDemoReceiver"
$incomingDir = Join-Path $root "incoming"
$redRibbonPrinterName = "RedRibbon Printer"

$script:FailCount = 0
$script:WarnCount = 0
$script:ReceiverCoreOk = $false

function Write-Check {
    param(
        [ValidateSet("OK", "FAIL", "WARN")]
        [string]$Level,
        [string]$Code,
        [string]$Detail = ""
    )
    switch ($Level) {
        "FAIL" { $script:FailCount++ }
        "WARN" { $script:WarnCount++ }
    }
    $color = switch ($Level) {
        "OK" { "Green" }
        "FAIL" { "Red" }
        "WARN" { "Yellow" }
    }
    $msg = "[$Level] $Code"
    if ($Detail) { $msg += " — $Detail" }
    Write-Host $msg -ForegroundColor $color
}

function Find-PdfCreatorExe {
    $candidates = @(
        (Join-Path ${env:ProgramFiles} "PDFCreator\PDFCreator.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "PDFCreator\PDFCreator.exe"),
        (Join-Path ${env:ProgramFiles} "pdfforge\PDFCreator\PDFCreator.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "pdfforge\PDFCreator\PDFCreator.exe")
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    $cmd = Get-Command "PDFCreator.exe" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

Write-Host "=== RedRibbon Demo Print Receiver check ===" -ForegroundColor Cyan
Write-Host ""

# 1) C:\RedRibbonDemo
if (Test-Path $root) {
    Write-Check -Level OK -Code "install_root" -Detail $root
} else {
    Write-Check -Level FAIL -Code "install_root_missing" -Detail $root
}

# 2) Receiver Engine
if (Test-Path $enginePath) {
    Write-Check -Level OK -Code "receiver_engine" -Detail $enginePath
} else {
    Write-Check -Level FAIL -Code "receiver_engine_missing" -Detail $enginePath
}

# 3) Working directories
foreach ($sub in $requiredDirs) {
    $dir = Join-Path $root $sub
    if (Test-Path $dir) {
        Write-Check -Level OK -Code "dir_$sub" -Detail $dir
    } else {
        Write-Check -Level FAIL -Code "dir_${sub}_missing" -Detail $dir
    }
}

# 4) config.json + server_url
$cfgServerUrl = ""
if (Test-Path $configPath) {
    Write-Check -Level OK -Code "config_json" -Detail $configPath
    try {
        $cfg = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $cfgServerUrl = [string]$cfg.server_url
        Write-Host "       watch_dir=$($cfg.watch_dir)" -ForegroundColor DarkGray
        Write-Host "       printer_name=$($cfg.printer_name)" -ForegroundColor DarkGray
        if ($cfgServerUrl) {
            Write-Check -Level OK -Code "config_server_url" -Detail $cfgServerUrl
        } else {
            Write-Check -Level WARN -Code "config_server_url_empty" -Detail "set server_url in config.json"
        }
    } catch {
        Write-Check -Level WARN -Code "config_parse_failed" -Detail $configPath
    }
} else {
    Write-Check -Level FAIL -Code "config_json_missing" -Detail $configPath
}

# 5) run script
if (Test-Path $runScriptPath) {
    Write-Check -Level OK -Code "run_script" -Detail $runScriptPath
} else {
    Write-Check -Level FAIL -Code "run_script_missing" -Detail $runScriptPath
}

$script:ReceiverCoreOk = (
    (Test-Path $root) -and
    (Test-Path $enginePath) -and
    (Test-Path $configPath) -and
    (Test-Path $runScriptPath)
)

# 6) Scheduled task RedRibbonDemoReceiver
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Check -Level OK -Code "scheduled_task" -Detail $taskName
} else {
    Write-Check -Level WARN -Code "scheduled_task_missing" -Detail "run install_redribbon_demo.ps1 or register_receiver_task.ps1"
}

# 7) PDFCreator base printer / application
$pdfCreatorPrinter = Get-Printer -Name "PDFCreator" -ErrorAction SilentlyContinue
$pdfExe = Find-PdfCreatorExe
if ($pdfCreatorPrinter) {
    Write-Check -Level OK -Code "pdfcreator_printer" -Detail "DriverName=$($pdfCreatorPrinter.DriverName) PortName=$($pdfCreatorPrinter.PortName)"
} elseif ($pdfExe) {
    Write-Check -Level WARN -Code "pdfcreator_printer_missing" -Detail "PDFCreator.exe found but PDFCreator printer not listed"
} else {
    Write-Check -Level WARN -Code "pdfcreator_not_found" -Detail "PDFCreator가 설치되어 있지 않아 RedRibbon Printer 자동 생성은 건너뜁니다."
}

# 8) RedRibbon Printer (OK/FAIL)
$redRibbonPrinter = Get-Printer -Name $redRibbonPrinterName -ErrorAction SilentlyContinue
if ($redRibbonPrinter) {
    Write-Check -Level OK -Code "redribbon_printer" -Detail "Name=$($redRibbonPrinter.Name) DriverName=$($redRibbonPrinter.DriverName) PortName=$($redRibbonPrinter.PortName)"
} else {
    Write-Check -Level FAIL -Code "redribbon_printer_missing" -Detail "Get-Printer -Name '$redRibbonPrinterName' — run install_redribbon_demo.ps1 or EXE setup"
}

# 9) incoming writable
if (Test-Path $incomingDir) {
    $probe = Join-Path $incomingDir ".write_test_$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    try {
        [System.IO.File]::WriteAllText($probe, "ok")
        Remove-Item $probe -Force -ErrorAction SilentlyContinue
        Write-Check -Level OK -Code "incoming_writable" -Detail $incomingDir
    } catch {
        Write-Check -Level FAIL -Code "incoming_not_writable" -Detail $incomingDir
    }
} else {
    Write-Check -Level FAIL -Code "incoming_missing" -Detail $incomingDir
}

# 10) Server reachability (optional for demo)
$checkUrl = if ($cfgServerUrl) { $cfgServerUrl.TrimEnd('/') } else { $defaultServerUrl }
try {
    $resp = Invoke-WebRequest -Uri $checkUrl -UseBasicParsing -TimeoutSec 5
    Write-Check -Level OK -Code "server_reachable" -Detail "$checkUrl status=$($resp.StatusCode)"
} catch {
    Write-Check -Level WARN -Code "server_unreachable" -Detail "$checkUrl (start RedRibbon app if needed)"
}

Write-Host ""
Write-Host "=== summary ===" -ForegroundColor Cyan
Write-Host "FAIL: $script:FailCount  WARN: $script:WarnCount" -ForegroundColor $(if ($script:FailCount -gt 0) { "Red" } elseif ($script:WarnCount -gt 0) { "Yellow" } else { "Green" })

if (-not $redRibbonPrinter -and $script:ReceiverCoreOk) {
    Write-Host ""
    Write-Host "[FAIL] RedRibbon Printer가 없습니다. EXE 설치를 다시 실행하거나 PDFCreator 기준 프린터를 확인하세요." -ForegroundColor Red
}

Write-Host ""
Write-Host "PDFCreator auto-save folder: C:\RedRibbonDemo\incoming" -ForegroundColor Cyan
Write-Host "=== check complete ===" -ForegroundColor Cyan

if ($script:FailCount -gt 0) { exit 1 }
exit 0
