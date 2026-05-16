# RedRibbon Print Receiver 설치
$ErrorActionPreference = "Stop"
$installRoot = "C:\RedRibbonPrint"
$packageRoot = $PSScriptRoot
$outputRoot = Join-Path $installRoot "RedRibbon_Printer_Output"
$dirs = @("incoming", "uploading", "uploaded", "failed", "logs")

Write-Host "RedRibbon Print Receiver 설치: $installRoot"

New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
foreach ($d in $dirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $outputRoot $d) | Out-Null
}

$destReceiver = Join-Path $installRoot "print_receiver"
New-Item -ItemType Directory -Force -Path $destReceiver | Out-Null
Copy-Item -Path (Join-Path $packageRoot "print_receiver\*") -Destination $destReceiver -Recurse -Force

$configSrc = Join-Path $packageRoot "print_receiver\config.json"
$configDst = Join-Path $installRoot "config.json"
if (Test-Path $configSrc) {
    Copy-Item $configSrc $configDst -Force
}

Copy-Item (Join-Path $packageRoot "run_receiver.ps1") (Join-Path $installRoot "run_receiver.ps1") -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $packageRoot "SETUP_VIRTUAL_PRINTER.md") (Join-Path $installRoot "SETUP_VIRTUAL_PRINTER.md") -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $packageRoot "README.txt") (Join-Path $installRoot "README.txt") -Force -ErrorAction SilentlyContinue

Write-Host "설치 완료. check_receiver_ready.ps1 로 점검하세요."
