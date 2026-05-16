# RedRibbon Demo Print Receiver readiness check (C:\RedRibbonDemo)
$ErrorActionPreference = "Continue"
$root = "C:\RedRibbonDemo"
$requiredDirs = @("incoming", "uploading", "uploaded", "failed", "logs")
$configPath = Join-Path $root "print_receiver\config.json"
$enginePath = Join-Path $root "print_receiver\receiver_engine.py"
$runScriptPath = Join-Path $root "run_redribbon_receiver.ps1"
$serverUrl = "http://127.0.0.1:8000"
$taskName = "RedRibbonDemoReceiver"
$incomingDir = Join-Path $root "incoming"
$printerNames = @("redribbon", "RedRibbon")

function Write-Check {
    param(
        [ValidateSet("OK", "FAIL", "WARN")]
        [string]$Level,
        [string]$Code,
        [string]$Detail = ""
    )
    $color = switch ($Level) {
        "OK" { "Green" }
        "FAIL" { "Red" }
        "WARN" { "Yellow" }
    }
    $msg = "[$Level] $Code"
    if ($Detail) { $msg += " — $Detail" }
    Write-Host $msg -ForegroundColor $color
}

Write-Host "=== RedRibbon Demo Print Receiver check ===" -ForegroundColor Cyan

if (Test-Path $root) {
    Write-Check -Level OK -Code "install_root" -Detail $root
} else {
    Write-Check -Level FAIL -Code "install_root_missing" -Detail $root
}

foreach ($sub in $requiredDirs) {
    $dir = Join-Path $root $sub
    if (Test-Path $dir) {
        Write-Check -Level OK -Code "dir_$sub" -Detail $dir
    } else {
        Write-Check -Level FAIL -Code "dir_${sub}_missing" -Detail $dir
    }
}

if (Test-Path $enginePath) {
    Write-Check -Level OK -Code "receiver_engine"
} else {
    Write-Check -Level FAIL -Code "receiver_engine_missing" -Detail $enginePath
}

if (Test-Path $configPath) {
    Write-Check -Level OK -Code "config_json"
    try {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        Write-Host "       watch_dir=$($cfg.watch_dir)"
        Write-Host "       server_url=$($cfg.server_url)"
    } catch {
        Write-Check -Level WARN -Code "config_parse_failed"
    }
} else {
    Write-Check -Level FAIL -Code "config_json_missing" -Detail $configPath
}

if (Test-Path $runScriptPath) {
    Write-Check -Level OK -Code "run_script"
} else {
    Write-Check -Level FAIL -Code "run_script_missing" -Detail $runScriptPath
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Check -Level OK -Code "scheduled_task" -Detail $taskName
} else {
    Write-Check -Level WARN -Code "scheduled_task_missing" -Detail "run install or register_receiver_task.ps1"
}

try {
    $resp = Invoke-WebRequest -Uri $serverUrl -UseBasicParsing -TimeoutSec 5
    Write-Check -Level OK -Code "server_reachable" -Detail "$serverUrl status=$($resp.StatusCode)"
} catch {
    Write-Check -Level FAIL -Code "server_unreachable" -Detail $serverUrl
}

$pdfInstall = $null
$pdfCandidates = @(
    (Join-Path ${env:ProgramFiles} "PDFCreator\PDFCreator.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "PDFCreator\PDFCreator.exe"),
    (Join-Path ${env:ProgramFiles} "pdfforge\PDFCreator\PDFCreator.exe")
)
foreach ($p in $pdfCandidates) {
    if (Test-Path $p) {
        $pdfInstall = $p
        break
    }
}
if (-not $pdfInstall) {
    $cmd = Get-Command "PDFCreator.exe" -ErrorAction SilentlyContinue
    if ($cmd) { $pdfInstall = $cmd.Source }
}
if ($pdfInstall) {
    Write-Check -Level OK -Code "pdfcreator_installed" -Detail $pdfInstall
} else {
    Write-Check -Level WARN -Code "pdfcreator_not_found"
}

$printerHit = $false
$matchedPrinter = ""
foreach ($n in $printerNames) {
    if (Get-Printer -Name $n -ErrorAction SilentlyContinue) {
        $printerHit = $true
        $matchedPrinter = $n
        break
    }
}
if (-not $printerHit) {
    try {
        foreach ($p in (Get-Printer -ErrorAction SilentlyContinue)) {
            if ($p.Name -match 'redribbon|RedRibbon|Ribbon') {
                $printerHit = $true
                $matchedPrinter = $p.Name
                break
            }
        }
    } catch {
        # ignore
    }
}
if ($printerHit) {
    Write-Check -Level OK -Code "printer_found" -Detail $matchedPrinter
} else {
    Write-Check -Level WARN -Code "printer_redribbon_missing" -Detail "configure PDFCreator profile redribbon"
}

if (Test-Path $incomingDir) {
    $probe = Join-Path $incomingDir ".write_test_$([guid]::NewGuid().ToString('N').Substring(0,8))"
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

Write-Host ""
Write-Host "PDFCreator auto-save folder (manual if needed): C:\RedRibbonDemo\incoming" -ForegroundColor Cyan
Write-Host "=== check complete ===" -ForegroundColor Cyan
