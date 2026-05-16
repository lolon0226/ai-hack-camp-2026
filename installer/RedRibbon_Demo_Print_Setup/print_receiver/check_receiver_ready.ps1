# RedRibbon Demo Print Receiver 준비 상태 점검 (C:\RedRibbonDemo)
$ErrorActionPreference = "Continue"
$root = "C:\RedRibbonDemo"
$requiredDirs = @("incoming", "uploading", "uploaded", "failed", "logs")
$configPath = Join-Path $root "print_receiver\config.json"
$enginePath = Join-Path $root "print_receiver\receiver_engine.py"
$serverUrl = "http://127.0.0.1:8000"
$printerName = "redribbon"

Write-Host "=== RedRibbon Demo Print Receiver 점검 ===" -ForegroundColor Cyan

if (-not (Test-Path $root)) {
    Write-Host "[FAIL] $root 없음" -ForegroundColor Red
} else {
    Write-Host "[OK] $root" -ForegroundColor Green
}

foreach ($sub in $requiredDirs) {
    $dir = Join-Path $root $sub
    if (Test-Path $dir) {
        Write-Host "[OK] $dir" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] $dir 없음" -ForegroundColor Red
    }
}

if (Test-Path $configPath) {
    Write-Host "[OK] config.json" -ForegroundColor Green
    try {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        Write-Host "     watch_dir=$($cfg.watch_dir)"
        Write-Host "     printer_name=$($cfg.printer_name)"
    } catch {
        Write-Host "[WARN] config.json 파싱 실패" -ForegroundColor Yellow
    }
} else {
    Write-Host "[FAIL] config.json 없음 ($configPath)" -ForegroundColor Red
}

if (Test-Path $enginePath) {
    Write-Host "[OK] receiver_engine.py" -ForegroundColor Green
} else {
    Write-Host "[FAIL] receiver_engine.py 없음 ($enginePath)" -ForegroundColor Red
}

try {
    $resp = Invoke-WebRequest -Uri $serverUrl -UseBasicParsing -TimeoutSec 5
    Write-Host "[OK] 서버 응답 $($resp.StatusCode) $serverUrl" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] 서버 접속 불가 $serverUrl" -ForegroundColor Red
    Write-Host "       $($_.Exception.Message)"
}

$printer = Get-Printer -Name $printerName -ErrorAction SilentlyContinue
if ($printer) {
    Write-Host "[OK] Windows 프린터: $printerName" -ForegroundColor Green
} else {
    Write-Host "[WARN] Windows 프린터 '$printerName' 없음 — PDFCreator에서 redribbon 등록 확인" -ForegroundColor Yellow
}

Write-Host "PDFCreator 자동저장 폴더: C:\RedRibbonDemo\incoming" -ForegroundColor Cyan
Write-Host "=== 점검 완료 ===" -ForegroundColor Cyan
