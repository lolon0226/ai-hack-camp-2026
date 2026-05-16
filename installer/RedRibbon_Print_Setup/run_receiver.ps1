# RedRibbon Print Receiver 실행
$ErrorActionPreference = "Stop"
$installRoot = "C:\RedRibbonPrint"
$engine = Join-Path $installRoot "print_receiver\receiver_engine.py"
$config = Join-Path $installRoot "config.json"

if (-not (Test-Path $engine)) {
    Write-Error "receiver_engine.py 없음: $engine (install_receiver.ps1 실행 필요)"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "python 명령을 찾을 수 없습니다."
}

Write-Host "Print Receiver 시작..."
& $python.Source $engine $config
