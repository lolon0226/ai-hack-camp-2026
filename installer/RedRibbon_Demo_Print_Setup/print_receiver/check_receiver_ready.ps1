# RedRibbon Demo Print Receiver readiness check (C:\RedRibbonDemo)
$ErrorActionPreference = "Continue"
$root = "C:\RedRibbonDemo"
$requiredDirs = @("incoming", "uploading", "uploaded", "failed", "logs")
$configPath = Join-Path $root "print_receiver\config.json"
$enginePath = Join-Path $root "print_receiver\receiver_engine.py"
$runScriptPath = Join-Path $root "run_redribbon_receiver.ps1"
$defaultServerUrl = "http://127.0.0.1:8010"
$taskName = "RedRibbonDemoReceiver"
$incomingDir = Join-Path $root "incoming"
$redRibbonPrinterName = "RedRibbon Printer"
$redRibbonProfileName = "RedRibbon Auto Save"
$redRibbonProfileGuid = "RedRibbonAutoSaveGuid"
$pdfCreatorSettingsReg = "HKCU:\Software\pdfforge\PDFCreator\Settings"

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
    if ($Detail) { $msg += " - $Detail" }
    Write-Host $msg -ForegroundColor $color
}

function Test-IsUsablePathString {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value) { return $false }
    return -not [string]::IsNullOrWhiteSpace([string]$Value)
}

function Resolve-FullPathSafe {
    param(
        [string]$Path,
        [string]$Label = "path"
    )
    $raw = [string]$Path
    if (-not (Test-IsUsablePathString $raw)) {
        return @{
            Ok = $false
            Normalized = ""
            Reason = "${Label} is empty"
        }
    }
    $trimmed = $raw.Trim()
    if ($trimmed -match '[\x00-\x1f\x7f]') {
        return @{
            Ok = $false
            Normalized = ""
            Reason = "${Label} has invalid control characters"
        }
    }
    try {
        $normalized = [System.IO.Path]::GetFullPath($trimmed).TrimEnd('\')
        return @{
            Ok = $true
            Normalized = $normalized
            Reason = ""
        }
    } catch {
        return @{
            Ok = $false
            Normalized = ""
            Reason = "${Label} is not a valid path: $trimmed"
        }
    }
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

function Get-PdfCreatorProfileCount {
    $profilesRoot = Join-Path $pdfCreatorSettingsReg "ConversionProfiles"
    if (-not (Test-Path $profilesRoot)) { return 0 }
    $raw = (Get-ItemProperty -LiteralPath $profilesRoot -Name numClasses -ErrorAction SilentlyContinue).numClasses
    $n = 0
    if (-not $raw -or -not [int]::TryParse([string]$raw, [ref]$n)) { return 0 }
    return $n
}

function Find-PdfCreatorProfileIndex {
    param([string]$Name = "", [string]$Guid = "")
    $profilesRoot = Join-Path $pdfCreatorSettingsReg "ConversionProfiles"
    $count = Get-PdfCreatorProfileCount
    for ($i = 0; $i -lt $count; $i++) {
        $keyPath = Join-Path $profilesRoot ([string]$i)
        if (-not (Test-Path $keyPath)) { continue }
        $props = Get-ItemProperty -LiteralPath $keyPath -ErrorAction SilentlyContinue
        if (-not $props) { continue }
        if ($Name -and [string]$props.Name -eq $Name) { return $i }
        if ($Guid -and [string]$props.Guid -eq $Guid) { return $i }
    }
    return -1
}

function Test-PdfCreatorAutoSaveProfile {
    param([string]$ExpectedIncoming)
    $result = @{
        ProfileFound   = $false
        AutoSaveOn     = $false
        TargetOk       = $false
        MappingOk      = $false
        InteractiveOff = $false
        OpenViewerOff  = $false
        TargetWarn              = $false
        TargetEmptyOrUnreadable = $false
        Detail                  = ""
    }
    if (-not (Test-Path $pdfCreatorSettingsReg)) {
        $result.Detail = "settings registry missing"
        return $result
    }
    $idx = Find-PdfCreatorProfileIndex -Name $redRibbonProfileName
    if ($idx -lt 0) { $idx = Find-PdfCreatorProfileIndex -Guid $redRibbonProfileGuid }
    if ($idx -lt 0) {
        $result.Detail = "profile not found"
        return $result
    }
    $result.ProfileFound = $true
    $profileKey = Join-Path (Join-Path $pdfCreatorSettingsReg "ConversionProfiles") ([string]$idx)
    $props = Get-ItemProperty -LiteralPath $profileKey -ErrorAction SilentlyContinue
    $autoSave = Get-ItemProperty -LiteralPath (Join-Path $profileKey "AutoSave") -ErrorAction SilentlyContinue
    $openViewer = Get-ItemProperty -LiteralPath (Join-Path $profileKey "OpenViewer") -ErrorAction SilentlyContinue

    $expectedResolved = Resolve-FullPathSafe -Path $ExpectedIncoming -Label "expected incoming folder"
    $targetRaw = ""
    if ($props -and $props.PSObject.Properties.Name -contains "TargetDirectory") {
        $targetRaw = [string]$props.TargetDirectory
    }

    if (-not (Test-IsUsablePathString $targetRaw)) {
        $result.TargetWarn = $true
        $result.TargetEmptyOrUnreadable = $true
        $result.TargetOk = $false
        $targetResolved = @{
            Ok = $false
            Normalized = ""
            Reason = "empty"
        }
    } else {
        $targetResolved = Resolve-FullPathSafe -Path $targetRaw -Label "PDFCreator TargetDirectory"
    }

    $result.AutoSaveOn = ([string]$autoSave.Enabled -eq "True")
    $result.OpenViewerOff = ([string]$openViewer.Enabled -ne "True")
    $result.InteractiveOff = $result.AutoSaveOn

    if ($result.TargetEmptyOrUnreadable) {
        # GetFullPath skipped; warn only, no exception
    } elseif (-not $targetResolved.Ok) {
        $result.TargetWarn = $true
        $result.TargetEmptyOrUnreadable = $true
        $result.TargetOk = $false
    } elseif ($expectedResolved.Ok) {
        $result.TargetOk = ($targetResolved.Normalized -eq $expectedResolved.Normalized)
        if (-not $result.TargetOk) {
            $result.TargetWarn = $true
        }
    } else {
        $result.TargetWarn = $true
        $result.TargetOk = $false
    }

    $mapRoot = Join-Path $pdfCreatorSettingsReg "ApplicationSettings\PrinterMappings"
    $mc = 0
    if (Test-Path $mapRoot) {
        $raw = (Get-ItemProperty -LiteralPath $mapRoot -Name numClasses -ErrorAction SilentlyContinue).numClasses
        if ($raw) { [void][int]::TryParse([string]$raw, [ref]$mc) }
    }
    for ($i = 0; $i -lt $mc; $i++) {
        $entry = Get-ItemProperty -LiteralPath (Join-Path $mapRoot ([string]$i)) -ErrorAction SilentlyContinue
        if ([string]$entry.PrinterName -ne $redRibbonPrinterName) { continue }
        $guidOk = ([string]$entry.ProfileGuid -eq $redRibbonProfileGuid)
        $nameOk = (-not $entry.ProfileName) -or ([string]$entry.ProfileName -eq $redRibbonProfileName)
        if ($guidOk -and $nameOk) {
            $result.MappingOk = $true
            break
        }
    }

    $targetDisplay = if ($targetResolved.Ok) { $targetResolved.Normalized } else { "(invalid or empty)" }
    $result.Detail = "idx=$idx AutoSave=$($autoSave.Enabled) Target=$targetDisplay Mapping=$($result.MappingOk)"
    if ($targetResolved.Reason) {
        $result.Detail += " TargetNote=$($targetResolved.Reason)"
    }
    return $result
}

Write-Host "=== RedRibbon Demo Print Receiver check ===" -ForegroundColor Cyan
Write-Host ""

if (Test-Path $root) {
    Write-Check -Level OK -Code "install_root" -Detail $root
} else {
    Write-Check -Level FAIL -Code "install_root_missing" -Detail $root
}

if (Test-Path $enginePath) {
    Write-Check -Level OK -Code "receiver_engine" -Detail $enginePath
} else {
    Write-Check -Level FAIL -Code "receiver_engine_missing" -Detail $enginePath
}

foreach ($sub in $requiredDirs) {
    $dir = Join-Path $root $sub
    if (Test-Path $dir) {
        Write-Check -Level OK -Code "dir_$sub" -Detail $dir
    } else {
        Write-Check -Level FAIL -Code "dir_${sub}_missing" -Detail $dir
    }
}

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

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Check -Level OK -Code "scheduled_task" -Detail $taskName
} else {
    Write-Check -Level WARN -Code "scheduled_task_missing" -Detail "run install_redribbon_demo.ps1 or register_receiver_task.ps1"
}

$pdfCreatorPrinter = Get-Printer -Name "PDFCreator" -ErrorAction SilentlyContinue
$pdfExe = Find-PdfCreatorExe
if ($pdfCreatorPrinter) {
    Write-Check -Level OK -Code "pdfcreator_printer" -Detail "DriverName=$($pdfCreatorPrinter.DriverName) PortName=$($pdfCreatorPrinter.PortName)"
} elseif ($pdfExe) {
    Write-Check -Level WARN -Code "pdfcreator_printer_missing" -Detail "PDFCreator.exe found but PDFCreator printer not listed"
} else {
    Write-Check -Level WARN -Code "pdfcreator_not_found" -Detail "PDFCreator not installed"
}

$redRibbonPrinter = Get-Printer -Name $redRibbonPrinterName -ErrorAction SilentlyContinue
if ($redRibbonPrinter) {
    Write-Check -Level OK -Code "redribbon_printer" -Detail "Name=$($redRibbonPrinter.Name) DriverName=$($redRibbonPrinter.DriverName) PortName=$($redRibbonPrinter.PortName)"
} else {
    Write-Check -Level FAIL -Code "redribbon_printer_missing" -Detail "Get-Printer -Name '$redRibbonPrinterName'"
}

$profileMappingWarn = "RedRibbon Printer exists, but PDFCreator auto-save profile mapping needs verification."
$autoSaveCheck = Test-PdfCreatorAutoSaveProfile -ExpectedIncoming $incomingDir
if ($redRibbonPrinter) {
    if ($autoSaveCheck.TargetEmptyOrUnreadable) {
        Write-Check -Level WARN -Code "pdfcreator_autosave_target" -Detail "PDFCreator auto-save target is empty or unreadable"
    } elseif ($autoSaveCheck.TargetWarn) {
        Write-Check -Level WARN -Code "pdfcreator_autosave_target" -Detail "PDFCreator auto-save target path does not match incoming folder"
    }
    if ($autoSaveCheck.ProfileFound -and $autoSaveCheck.AutoSaveOn -and $autoSaveCheck.TargetOk -and $autoSaveCheck.MappingOk) {
        Write-Check -Level OK -Code "pdfcreator_autosave_profile" -Detail "PDFCreator auto-save profile configured"
    } elseif ($autoSaveCheck.ProfileFound) {
        if (-not $autoSaveCheck.TargetEmptyOrUnreadable) {
            Write-Check -Level WARN -Code "pdfcreator_autosave_partial" -Detail $autoSaveCheck.Detail
        }
        if (-not $autoSaveCheck.MappingOk) {
            Write-Check -Level WARN -Code "pdfcreator_profile_link" -Detail $profileMappingWarn
        }
    } else {
        Write-Check -Level WARN -Code "pdfcreator_autosave_profile_missing" -Detail "RedRibbon Auto Save profile not found"
        Write-Check -Level WARN -Code "pdfcreator_profile_link" -Detail $profileMappingWarn
    }
    if ($autoSaveCheck.OpenViewerOff -and $autoSaveCheck.AutoSaveOn) {
        Write-Check -Level OK -Code "pdfcreator_interactive_off" -Detail "AutoSave ON / OpenViewer OFF"
    } elseif ($autoSaveCheck.ProfileFound) {
        Write-Check -Level WARN -Code "pdfcreator_interactive_uncertain" -Detail "OpenViewer or AutoSave settings need review"
    }
}

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
    Write-Host "[FAIL] RedRibbon Printer is missing. Re-run the installer or check the PDFCreator base printer." -ForegroundColor Red
}

Write-Host ""
Write-Host "PDFCreator auto-save folder: $incomingDir" -ForegroundColor Cyan
Write-Host "Scheduled task: $taskName (runs Receiver at logon when registered)" -ForegroundColor Cyan
Write-Host "Manual run (demo): $runScriptPath" -ForegroundColor Cyan
Write-Host "=== check complete ===" -ForegroundColor Cyan

if ($script:FailCount -gt 0) { exit 1 }
exit 0
