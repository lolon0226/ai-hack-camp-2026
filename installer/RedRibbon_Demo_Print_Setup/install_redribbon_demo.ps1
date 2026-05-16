# RedRibbon Demo Print Receiver 설치 → C:\RedRibbonDemo
$ErrorActionPreference = "Stop"
$installRoot = "C:\RedRibbonDemo"
$packageRoot = $PSScriptRoot
$dirs = @("incoming", "uploading", "uploaded", "failed", "logs", "print_receiver")

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

Write-Host "RedRibbon Demo 설치: $installRoot"

foreach ($d in $dirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $installRoot $d) | Out-Null
}

$destReceiver = Join-Path $installRoot "print_receiver"
New-Item -ItemType Directory -Force -Path $destReceiver | Out-Null
Copy-Item -Path (Join-Path $packageRoot "print_receiver\receiver_engine.py") -Destination $destReceiver -Force
Copy-Item -Path (Join-Path $packageRoot "print_receiver\check_receiver_ready.ps1") -Destination $destReceiver -Force

$configSrc = Join-Path $packageRoot "print_receiver\config.json"
$configDst = Join-Path $installRoot "print_receiver\config.json"
if (Test-Path $configSrc) {
    $json = Get-Content $configSrc -Raw -Encoding UTF8
    Write-Utf8NoBom -Path $configDst -Content $json
}

$runScript = Join-Path $packageRoot "run_redribbon_receiver.ps1"
$runDest = Join-Path $installRoot "run_redribbon_receiver.ps1"
if (Test-Path $runScript) {
    $runContent = Get-Content $runScript -Raw -Encoding UTF8
    Write-Utf8NoBom -Path $runDest -Content $runContent
}

foreach ($name in @("SETUP_VIRTUAL_PRINTER.md", "README.txt")) {
    $src = Join-Path $packageRoot $name
    if (Test-Path $src) {
        $text = Get-Content $src -Raw -Encoding UTF8
        Write-Utf8NoBom -Path (Join-Path $installRoot $name) -Content $text
    }
}

Write-Host "설치 완료."
Write-Host "PDFCreator: 프린터 redribbon → 자동저장 C:\RedRibbonDemo\incoming"
Write-Host "실행: C:\RedRibbonDemo\run_redribbon_receiver.ps1"
