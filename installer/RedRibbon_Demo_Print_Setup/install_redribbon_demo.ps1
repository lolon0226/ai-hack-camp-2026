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
  "server_url": "http://127.0.0.1:8010",
  "upload_endpoint": "/api/print-receiver/upload",
  "watch_dir": "C:\\RedRibbonDemo\\incoming",
  "uploading_dir": "C:\\RedRibbonDemo\\uploading",
  "uploaded_dir": "C:\\RedRibbonDemo\\uploaded",
  "failed_dir": "C:\\RedRibbonDemo\\failed",
  "log_dir": "C:\\RedRibbonDemo\\logs",
  "hospital_name": "TEST_HOSPITAL",
  "printer_name": "RedRibbon Printer",
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

$script:RedRibbonPrinterName = "RedRibbon Printer"
$script:RedRibbonProfileName = "RedRibbon Auto Save"
$script:RedRibbonProfileGuid = "RedRibbonAutoSaveGuid"
$script:PdfCreatorSettingsReg = "HKCU:\Software\pdfforge\PDFCreator\Settings"

function Set-RegSzValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )
    if (-not (Test-Path $Path)) {
        New-Item -Path $Path -Force | Out-Null
    }
    Set-ItemProperty -Path $Path -Name $Name -Value $Value -Type String -Force -ErrorAction Stop
}

function Copy-RegistryTree {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestPath
    )
    if (-not (Test-Path -LiteralPath $SourcePath)) { return }
    if (-not (Test-Path -LiteralPath $DestPath)) {
        New-Item -Path $DestPath -Force | Out-Null
    }
    $srcKey = Get-Item -LiteralPath $SourcePath
    foreach ($prop in $srcKey.GetValueNames()) {
        if ($prop -in @("PSPath", "PSParentPath", "PSChildName", "PSDrive", "PSProvider")) { continue }
        Set-ItemProperty -LiteralPath $DestPath -Name $prop -Value $srcKey.GetValue($prop) -Force -ErrorAction SilentlyContinue
    }
    foreach ($sub in Get-ChildItem -LiteralPath $SourcePath -ErrorAction SilentlyContinue) {
        Copy-RegistryTree -SourcePath $sub.PSPath -DestPath (Join-Path $DestPath $sub.PSChildName)
    }
}

function Get-PdfCreatorProfileCount {
    $profilesRoot = Join-Path $script:PdfCreatorSettingsReg "ConversionProfiles"
    if (-not (Test-Path $profilesRoot)) { return 0 }
    $raw = (Get-ItemProperty -LiteralPath $profilesRoot -Name numClasses -ErrorAction SilentlyContinue).numClasses
    if (-not $raw) { return 0 }
    $n = 0
    if (-not [int]::TryParse([string]$raw, [ref]$n)) { return 0 }
    return $n
}

function Find-PdfCreatorProfileIndex {
    param(
        [string]$Name = "",
        [string]$Guid = ""
    )
    $profilesRoot = Join-Path $script:PdfCreatorSettingsReg "ConversionProfiles"
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

function Set-PdfCreatorProfileAutoSaveValues {
    param(
        [Parameter(Mandatory = $true)][int]$ProfileIndex,
        [Parameter(Mandatory = $true)][string]$TargetIncoming
    )
    $profileKey = Join-Path (Join-Path $script:PdfCreatorSettingsReg "ConversionProfiles") ([string]$ProfileIndex)
    if (-not (Test-Path $profileKey)) { throw "profile key missing: $profileKey" }

    $incomingNorm = [System.IO.Path]::GetFullPath($TargetIncoming).TrimEnd('\')
    $fileTemplate = "redribbon_<PrintJobName>_<DateTime>"

    Set-RegSzValue -Path $profileKey -Name "Name" -Value $script:RedRibbonProfileName
    Set-RegSzValue -Path $profileKey -Name "Guid" -Value $script:RedRibbonProfileGuid
    Set-RegSzValue -Path $profileKey -Name "OutputFormat" -Value "Pdf"
    Set-RegSzValue -Path $profileKey -Name "FileNameTemplate" -Value $fileTemplate
    if (-not (Test-Path $TargetIncoming)) {
        New-Item -ItemType Directory -Force -Path $TargetIncoming | Out-Null
    }
    Set-RegSzValue -Path $profileKey -Name "TargetDirectory" -Value $incomingNorm
    Set-RegSzValue -Path $profileKey -Name "SaveFileTemporary" -Value "False"
    Set-RegSzValue -Path $profileKey -Name "SkipPrintDialog" -Value "True"
    Set-RegSzValue -Path $profileKey -Name "ShowProgress" -Value "False"
    Set-RegSzValue -Path $profileKey -Name "ShowQuickActions" -Value "False"
    Set-RegSzValue -Path $profileKey -Name "ShowAllNotifications" -Value "False"
    Set-RegSzValue -Path $profileKey -Name "ShowOnlyErrorNotifications" -Value "True"

    Set-RegSzValue -Path (Join-Path $profileKey "AutoSave") -Name "Enabled" -Value "True"
    Set-RegSzValue -Path (Join-Path $profileKey "AutoSave") -Name "ExistingFileBehaviour" -Value "EnsureUniqueFilenames"

    Set-RegSzValue -Path (Join-Path $profileKey "OpenViewer") -Name "Enabled" -Value "False"
    Set-RegSzValue -Path (Join-Path $profileKey "OpenViewer") -Name "OpenWithPdfArchitect" -Value "False"
    Set-RegSzValue -Path (Join-Path $profileKey "OpenViewer") -Name "OpenFolder" -Value "False"

    foreach ($section in @("EmailClientSettings", "EmailSmtpSettings", "EmailWebSettings", "Ftp", "HttpSettings", "DropboxSettings", "OneDriveSettings", "ForwardToFurtherProfile")) {
        $secPath = Join-Path $profileKey $section
        if (Test-Path $secPath) {
            Set-RegSzValue -Path $secPath -Name "Enabled" -Value "False"
        }
    }

    $printingPath = Join-Path $profileKey "Printing"
    if (Test-Path $printingPath) {
        Set-RegSzValue -Path $printingPath -Name "Enabled" -Value "False"
    }

    $writtenTarget = [string](Get-ItemProperty -LiteralPath $profileKey -Name TargetDirectory -ErrorAction SilentlyContinue).TargetDirectory
    if ($writtenTarget.TrimEnd('\') -ne $incomingNorm) {
        Set-RegSzValue -Path $profileKey -Name "TargetDirectory" -Value $incomingNorm
        Write-InstallLog "pdfcreator_targetdirectory_rewrite expected=$incomingNorm got=$writtenTarget"
    }
}

function New-PdfCreatorProfileFromTemplate {
    param([int]$TemplateIndex = 0)
    $profilesRoot = Join-Path $script:PdfCreatorSettingsReg "ConversionProfiles"
    if (-not (Test-Path $profilesRoot)) {
        New-Item -Path $profilesRoot -Force | Out-Null
        Set-RegSzValue -Path $profilesRoot -Name "numClasses" -Value "0"
    }
    $count = Get-PdfCreatorProfileCount
    $templateKey = Join-Path $profilesRoot ([string]$TemplateIndex)
    if (-not (Test-Path $templateKey)) {
        throw "PDFCreator template profile $TemplateIndex not found"
    }
    $newIndex = $count
    $newKey = Join-Path $profilesRoot ([string]$newIndex)
    Copy-RegistryTree -SourcePath $templateKey -DestPath $newKey
    Set-RegSzValue -Path $profilesRoot -Name "numClasses" -Value ([string]($newIndex + 1))
    return $newIndex
}

function Set-RedRibbonPrinterProfileMapping {
    $mapRoot = Join-Path $script:PdfCreatorSettingsReg "ApplicationSettings\PrinterMappings"
    if (-not (Test-Path $mapRoot)) {
        New-Item -Path $mapRoot -Force | Out-Null
        Set-RegSzValue -Path $mapRoot -Name "numClasses" -Value "0"
    }
    $count = 0
    $raw = (Get-ItemProperty -LiteralPath $mapRoot -Name numClasses -ErrorAction SilentlyContinue).numClasses
    if ($raw) { [void][int]::TryParse([string]$raw, [ref]$count) }

    $targetIdx = -1
    for ($i = 0; $i -lt $count; $i++) {
        $entry = Join-Path $mapRoot ([string]$i)
        if (-not (Test-Path $entry)) { continue }
        $props = Get-ItemProperty -LiteralPath $entry -ErrorAction SilentlyContinue
        if ([string]$props.PrinterName -eq $script:RedRibbonPrinterName) {
            $targetIdx = $i
            break
        }
    }
    if ($targetIdx -lt 0) {
        $targetIdx = $count
        $entry = Join-Path $mapRoot ([string]$targetIdx)
        New-Item -Path $entry -Force | Out-Null
        Set-RegSzValue -Path $entry -Name "PrinterName" -Value $script:RedRibbonPrinterName
        Set-RegSzValue -Path $entry -Name "IsHotFolder" -Value "False"
        Set-RegSzValue -Path $mapRoot -Name "numClasses" -Value ([string]($count + 1))
    }
    $entryPath = Join-Path $mapRoot ([string]$targetIdx)
    Set-RegSzValue -Path $entryPath -Name "ProfileGuid" -Value $script:RedRibbonProfileGuid
    Set-RegSzValue -Path $entryPath -Name "ProfileName" -Value $script:RedRibbonProfileName
}

function Get-PdfCreatorProcessIds {
    $ids = New-Object System.Collections.Generic.List[int]
    foreach ($procName in @("PDFCreator", "PDFCreator-cli")) {
        Get-Process -Name $procName -ErrorAction SilentlyContinue | ForEach-Object {
            if ($ids -notcontains $_.Id) { [void]$ids.Add($_.Id) }
        }
    }
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path -match '\\PDFCreator\\PDFCreator(-cli)?\.exe$'
    } | ForEach-Object {
        if ($ids -notcontains $_.Id) { [void]$ids.Add($_.Id) }
    }
    return $ids
}

function Stop-PdfCreatorProcessesSafely {
    param(
        [switch]$Mandatory,
        [int]$MaxWaitSeconds = 20
    )
    $deadline = (Get-Date).AddSeconds($MaxWaitSeconds)
    $stopErrors = 0
    do {
        $running = @(Get-PdfCreatorProcessIds)
        if ($running.Count -eq 0) { break }
        foreach ($procId in $running) {
            try {
                $proc = Get-Process -Id $procId -ErrorAction Stop
                if ($proc.MainWindowHandle -ne 0) {
                    $null = $proc.CloseMainWindow()
                }
            } catch {
                # process may have exited
            }
        }
        Start-Sleep -Milliseconds 600
        foreach ($procId in @($running)) {
            try {
                $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
                if ($proc -and -not $proc.HasExited) {
                    Stop-Process -Id $procId -Force -ErrorAction Stop
                    Write-InstallLog "pdfcreator_process_force_stopped pid=$procId name=$($proc.ProcessName)"
                }
            } catch {
                $stopErrors++
                Write-InstallLog "WARN: could not stop pid=$procId : $($_.Exception.Message)"
            }
        }
        Start-Sleep -Milliseconds 400
    } while ((Get-PdfCreatorProcessIds).Count -gt 0 -and (Get-Date) -lt $deadline)

    $remaining = @(Get-PdfCreatorProcessIds)
    if ($remaining.Count -gt 0) {
        Write-InstallLog "WARN: PDFCreator still running pids=$($remaining -join ',')"
        if ($Mandatory) { return $false }
        return $false
    }
    if ($stopErrors -gt 0) { return $false }
    Write-InstallLog "pdfcreator_processes_stopped"
    return $true
}

function Get-RedRibbonPdfCreatorMappingVerify {
    param([int]$ProfileIndex = -1)
    $result = @{
        MappingOk       = $false
        TargetOk        = $false
        ProfileName     = ""
        ProfileGuid     = ""
        TargetDirectory = ""
        PrinterName     = $script:RedRibbonPrinterName
        GuiProfileLabel = ""
    }
    $mapRoot = Join-Path $script:PdfCreatorSettingsReg "ApplicationSettings\PrinterMappings"
    if (-not (Test-Path $mapRoot)) { return $result }
    $mc = 0
    $raw = (Get-ItemProperty -LiteralPath $mapRoot -Name numClasses -ErrorAction SilentlyContinue).numClasses
    if ($raw) { [void][int]::TryParse([string]$raw, [ref]$mc) }
    for ($i = 0; $i -lt $mc; $i++) {
        $entry = Get-ItemProperty -LiteralPath (Join-Path $mapRoot ([string]$i)) -ErrorAction SilentlyContinue
        if ([string]$entry.PrinterName -ne $script:RedRibbonPrinterName) { continue }
        $result.MappingOk = ([string]$entry.ProfileGuid -eq $script:RedRibbonProfileGuid)
        $guiName = [string]$entry.ProfileName
        if (-not $guiName) { $guiName = $script:RedRibbonProfileName }
        $result.ProfileGuid = [string]$entry.ProfileGuid
        $result.ProfileName = $guiName
        $result.GuiProfileLabel = $guiName
        break
    }
    if ($ProfileIndex -ge 0) {
        $profileKey = Join-Path (Join-Path $script:PdfCreatorSettingsReg "ConversionProfiles") ([string]$ProfileIndex)
        if (Test-Path $profileKey) {
            $profile = Get-ItemProperty -LiteralPath $profileKey -ErrorAction SilentlyContinue
            $result.TargetDirectory = [string]$profile.TargetDirectory
            if ($profile.Name) { $result.GuiProfileLabel = [string]$profile.Name }
            $incomingNorm = [System.IO.Path]::GetFullPath($incomingDir).TrimEnd('\')
            $result.TargetOk = ($result.TargetDirectory.TrimEnd('\') -eq $incomingNorm)
        }
    }
    return $result
}

function Show-PdfCreatorGuiVerifyHint {
    param($Verify)
    $profileLabel = if ($Verify.GuiProfileLabel) { $Verify.GuiProfileLabel } else { $script:RedRibbonProfileName }
    Write-Host "[INFO] PDFCreator GUI verify: Application Settings -> Printers tab" -ForegroundColor Cyan
    Write-Host "       Printer '$($script:RedRibbonPrinterName)' -> profile '$profileLabel' (GUID $($script:RedRibbonProfileGuid))" -ForegroundColor Cyan
    if ($Verify.TargetDirectory) {
        Write-Host "       Profile '$profileLabel' save folder: $($Verify.TargetDirectory)" -ForegroundColor Cyan
    }
}

function Test-RedRibbonPrinterPresent {
    if (Get-Printer -Name $script:RedRibbonPrinterName -ErrorAction SilentlyContinue) {
        return $true
    }
    $names = @("redribbon", "RedRibbon")
    foreach ($n in $names) {
        if (Get-Printer -Name $n -ErrorAction SilentlyContinue) {
            return $true
        }
    }
    try {
        foreach ($p in (Get-Printer -ErrorAction SilentlyContinue)) {
            if ($p.Name -match 'RedRibbon|redribbon') { return $true }
        }
    } catch {
        # ignore
    }
    return $false
}

function Ensure-RedRibbonPrinter {
    <#
    PDFCreator 기준 프린터에서 드라이버·포트를 복사해 RedRibbon Printer를 생성합니다.
    #>
    $result = @{
        PrinterName   = $script:RedRibbonPrinterName
        Success       = $false
        AlreadyExists = $false
        Created       = $false
        Skipped       = $false
        SkipReason    = ""
        DriverName    = ""
        PortName      = ""
    }

    $existing = Get-Printer -Name $script:RedRibbonPrinterName -ErrorAction SilentlyContinue
    if ($existing) {
        $result.AlreadyExists = $true
        $result.Success = $true
        $result.DriverName = [string]$existing.DriverName
        $result.PortName = [string]$existing.PortName
        Write-InstallLog "redribbon_printer: already exists DriverName=$($result.DriverName) PortName=$($result.PortName)"
        return $result
    }

    $base = Get-Printer -Name "PDFCreator" -ErrorAction SilentlyContinue
    if (-not $base) {
        $result.Skipped = $true
        $result.SkipReason = "pdfcreator_base_missing"
        Write-InstallLog "redribbon_printer: skipped (PDFCreator base printer not found)"
        return $result
    }

    $driver = [string]$base.DriverName
    $port = [string]$base.PortName
    if (-not $driver) { $driver = "PDFCreator" }
    if (-not $port) { $port = "pdfcmon" }

    try {
        Add-Printer -Name $script:RedRibbonPrinterName -DriverName $driver -PortName $port -ErrorAction Stop
        $created = Get-Printer -Name $script:RedRibbonPrinterName -ErrorAction SilentlyContinue
        if ($created) {
            $result.Created = $true
            $result.Success = $true
            $result.DriverName = [string]$created.DriverName
            $result.PortName = [string]$created.PortName
            Write-InstallLog "redribbon_printer: created DriverName=$($result.DriverName) PortName=$($result.PortName)"
        } else {
            $result.SkipReason = "verify_failed_after_add"
            Write-InstallLog "redribbon_printer: Add-Printer returned but printer not listed"
        }
    } catch {
        $result.SkipReason = $_.Exception.Message
        Write-InstallLog "redribbon_printer: create failed: $($_.Exception.Message)"
    }
    return $result
}

function Invoke-PdfCreatorCliCommand {
    param(
        [Parameter(Mandatory = $true)][string]$CliPath,
        [Parameter(Mandatory = $true)][string]$Command
    )
    if (-not (Test-Path $CliPath)) { return $false }
    $outLog = Join-Path $logDir "pdfcreator_cli_$Command.log"
    $errLog = "$outLog.err"
    try {
        Write-InstallLog "pdfcreator_cli: $CliPath $Command"
        $proc = Start-Process -FilePath $CliPath `
            -ArgumentList $Command `
            -Wait -PassThru `
            -WindowStyle Hidden `
            -RedirectStandardOutput $outLog `
            -RedirectStandardError $errLog `
            -ErrorAction Stop
        Write-InstallLog "pdfcreator_cli: $Command exit=$($proc.ExitCode)"
        return ($proc.ExitCode -eq 0)
    } catch {
        Write-InstallLog "pdfcreator_cli_failed: $Command $($_.Exception.Message)"
        return $false
    }
}

function Try-PdfCreatorCliRestorePrinters {
    param([string]$PdfCreatorDir)
    if (-not $PdfCreatorDir) { return $false }
    foreach ($name in @("PDFCreator-cli.exe", "pdfcreator-cli.exe")) {
        $cliPath = Join-Path $PdfCreatorDir $name
        if (Test-Path $cliPath) {
            if (Invoke-PdfCreatorCliCommand -CliPath $cliPath -Command "InitializeSettings") { }
            if (Invoke-PdfCreatorCliCommand -CliPath $cliPath -Command "RestorePrinters") {
                return $true
            }
        }
    }
    $pathCli = Get-Command "PDFCreator-cli.exe" -ErrorAction SilentlyContinue
    if ($pathCli) {
        return (Invoke-PdfCreatorCliCommand -CliPath $pathCli.Source -Command "RestorePrinters")
    }
    return $false
}

function Try-ConfigurePdfCreatorAutoSave {
    param([string]$TargetIncoming)
    $result = @{
        PdfCreatorInstalled = $false
        AutoConfigured      = $false
        ProfileCreated      = $false
        ProfileLinked       = $false
        PrinterFound        = $false
        InstallPath         = ""
        ProfileIndex        = -1
        Note                = ""
    }

    $installPath = Find-PdfCreatorInstall
    if ($installPath) {
        $result.PdfCreatorInstalled = $true
        $result.InstallPath = $installPath
    }

    $result.PrinterFound = Test-RedRibbonPrinterPresent

    if (-not (Test-Path $script:PdfCreatorSettingsReg)) {
        $result.Note = "pdfcreator_settings_registry_missing"
        return $result
    }

    $stoppedBefore = Stop-PdfCreatorProcessesSafely -Mandatory
    if (-not $stoppedBefore) {
        Write-InstallLog "WARN: PDFCreator still running before registry update; settings may not apply until restart"
    }

    try {
        $profileIdx = Find-PdfCreatorProfileIndex -Name $script:RedRibbonProfileName
        if ($profileIdx -lt 0) {
            $profileIdx = Find-PdfCreatorProfileIndex -Guid $script:RedRibbonProfileGuid
        }
        if ($profileIdx -lt 0) {
            $profileIdx = New-PdfCreatorProfileFromTemplate -TemplateIndex 0
            $result.ProfileCreated = $true
            Write-InstallLog "pdfcreator_profile_created index=$profileIdx"
        } else {
            Write-InstallLog "pdfcreator_profile_exists index=$profileIdx"
        }
        $result.ProfileIndex = $profileIdx

        Set-PdfCreatorProfileAutoSaveValues -ProfileIndex $profileIdx -TargetIncoming $TargetIncoming
        Set-RedRibbonPrinterProfileMapping
        $result.ProfileLinked = $true

        $mapVerify = Get-RedRibbonPdfCreatorMappingVerify -ProfileIndex $profileIdx
        $verifyKey = Join-Path (Join-Path $script:PdfCreatorSettingsReg "ConversionProfiles") ([string]$profileIdx)
        $autoSave = Get-ItemProperty -LiteralPath (Join-Path $verifyKey "AutoSave") -ErrorAction SilentlyContinue
        if ([string]$autoSave.Enabled -eq "True" -and $mapVerify.TargetOk -and $mapVerify.MappingOk) {
            $result.AutoConfigured = $true
        } else {
            $result.Note = "verify_partial TargetOk=$($mapVerify.TargetOk) AutoSave=$($autoSave.Enabled) MappingOk=$($mapVerify.MappingOk) Target=$($mapVerify.TargetDirectory)"
        }
        Write-InstallLog "pdfcreator_verify mapping=$($mapVerify.MappingOk) target=$($mapVerify.TargetOk) profile_gui=$($mapVerify.GuiProfileLabel)"
    } catch {
        $result.Note = $_.Exception.Message
        Write-InstallLog "pdfcreator_autosave_failed: $($_.Exception.Message)"
    }

    $stoppedAfter = Stop-PdfCreatorProcessesSafely -Mandatory
    if (-not $stoppedAfter) {
        Write-InstallLog "WARN: PDFCreator still running after registry update — close PDFCreator and re-run install"
        if ($result.Note) {
            $result.Note = "$($result.Note);pdfcreator_still_running"
        } else {
            $result.Note = "pdfcreator_still_running"
        }
    }

    if ($installPath) {
        $cliOk = Try-PdfCreatorCliRestorePrinters -PdfCreatorDir $installPath
        if ($cliOk) {
            $result.Note = if ($result.Note) { "$($result.Note);cli_ok" } else { "cli_ok" }
            $result.PrinterFound = Test-RedRibbonPrinterPresent
        }
        $null = Stop-PdfCreatorProcessesSafely -Mandatory
    }

    if (-not $result.PdfCreatorInstalled) {
        $result.Note = if ($result.Note) { $result.Note } else { "PDFCreator not detected" }
    }
    return $result
}

function Show-ManualPdfCreatorGuide {
    param([string]$Reason = "autosave")
    Write-Host ""
    Write-Host "=== PDFCreator 수동 설정 (자동설정 실패 시 fallback) ===" -ForegroundColor Yellow
    if ($Reason -eq "pdfcreator_missing") {
        Write-Host "PDFCreator가 설치되어 있지 않아 RedRibbon Printer 자동 생성은 건너뜁니다." -ForegroundColor Yellow
        Write-Host "PDFCreator 설치 후 설치 스크립트를 다시 실행하거나 프린터를 수동으로 추가하세요." -ForegroundColor Yellow
    } else {
        Write-Host "PDFCreator -> Profiles -> RedRibbon Auto Save" -ForegroundColor Yellow
        Write-Host "Save: Interactive OFF / Automatic ON / Target directory: C:\RedRibbonDemo\incoming" -ForegroundColor Yellow
        Write-Host "Actions: Open file / PDF Architect OFF / Email OFF" -ForegroundColor Yellow
        Write-Host "Printer tab: RedRibbon Printer -> profile RedRibbon Auto Save" -ForegroundColor Yellow
    }
    Write-Host "자세한 내용: SETUP_VIRTUAL_PRINTER.md" -ForegroundColor DarkGray
    Write-Host ""
}

function Show-InstallSuccessGuide {
    Write-Host ""
    Write-Host "PDFCreator 기반 RedRibbon 전용 가상프린터가 자동 구성되었습니다." -ForegroundColor Green
    Write-Host "병원 문서를 RedRibbon Printer로 인쇄하면 C:\RedRibbonDemo\incoming 폴더로 저장되고," -ForegroundColor Cyan
    Write-Host "Receiver Engine이 이를 서버로 전송합니다." -ForegroundColor Cyan
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

$printerResult = Ensure-RedRibbonPrinter
Write-InstallLog "redribbon_printer success=$($printerResult.Success) created=$($printerResult.Created) skipped=$($printerResult.Skipped)"

if ($printerResult.Skipped -and $printerResult.SkipReason -eq "pdfcreator_base_missing") {
    Write-Host "[WARN] PDFCreator가 설치되어 있지 않아 RedRibbon Printer 자동 생성은 건너뜁니다." -ForegroundColor Yellow
    Show-ManualPdfCreatorGuide -Reason "pdfcreator_missing"
} elseif ($printerResult.Success) {
    if ($printerResult.Created) {
        Write-Host "[OK] RedRibbon Printer created (DriverName=$($printerResult.DriverName) PortName=$($printerResult.PortName))" -ForegroundColor Green
    } else {
        Write-Host "[OK] RedRibbon Printer already exists" -ForegroundColor Green
    }
} else {
    Write-Host "[WARN] RedRibbon Printer 자동 생성에 실패했습니다. PDFCreator 설치·PDFCreator 프린터 존재 여부를 확인하세요." -ForegroundColor Yellow
    if ($printerResult.SkipReason) {
        Write-Host "       $($printerResult.SkipReason)" -ForegroundColor DarkYellow
    }
}

$pdfResult = Try-ConfigurePdfCreatorAutoSave -TargetIncoming $incomingDir
Write-InstallLog "pdfcreator installed=$($pdfResult.PdfCreatorInstalled) auto=$($pdfResult.AutoConfigured) printer=$($pdfResult.PrinterFound)"

if ($pdfResult.PdfCreatorInstalled) {
    Write-Host "[OK] PDFCreator found: $($pdfResult.InstallPath)" -ForegroundColor Green
} elseif (-not $printerResult.Skipped) {
    Write-Host "[WARN] PDFCreator application path not found (printer may still work)" -ForegroundColor Yellow
}

$mapVerifyOut = $null
if ($pdfResult.ProfileIndex -ge 0) {
    $mapVerifyOut = Get-RedRibbonPdfCreatorMappingVerify -ProfileIndex $pdfResult.ProfileIndex
}

if ($pdfResult.AutoConfigured) {
    Write-Host "[OK] PDFCreator auto-save profile configured (RedRibbon Auto Save -> $incomingDir)" -ForegroundColor Green
    if ($mapVerifyOut) { Show-PdfCreatorGuiVerifyHint -Verify $mapVerifyOut }
} elseif ($printerResult.Success) {
    Write-Host "[WARN] RedRibbon Printer는 생성되었지만 PDFCreator 자동저장 프로필 연결 확인이 필요합니다." -ForegroundColor Yellow
    if ($pdfResult.Note) {
        Write-Host "       $($pdfResult.Note)" -ForegroundColor DarkYellow
    }
    Show-ManualPdfCreatorGuide -Reason "autosave"
    if ($mapVerifyOut) { Show-PdfCreatorGuiVerifyHint -Verify $mapVerifyOut }
} elseif ($pdfResult.PdfCreatorInstalled) {
    Write-Host "[WARN] PDFCreator 자동저장 프로필 설정을 완료하지 못했습니다." -ForegroundColor Yellow
    Show-ManualPdfCreatorGuide -Reason "autosave"
    if ($mapVerifyOut) { Show-PdfCreatorGuiVerifyHint -Verify $mapVerifyOut }
}

if ($pdfResult.Note -match 'pdfcreator_still_running') {
    Write-Host "[WARN] Close all PDFCreator windows, then re-run this installer to apply printer profile mapping." -ForegroundColor Yellow
}

if ($printerResult.Success) {
    Show-InstallSuccessGuide
    if (-not $pdfResult.AutoConfigured) {
        Write-Host "[INFO] 인쇄 시 저장창이 뜨면 fallback 안내(SETUP_VIRTUAL_PRINTER.md)대로 프로필을 연결하세요." -ForegroundColor Yellow
    }
} elseif (-not ($printerResult.Skipped -and $printerResult.SkipReason -eq "pdfcreator_base_missing")) {
    Show-ManualPdfCreatorGuide
}

Write-Host "Install complete: $InstallRoot" -ForegroundColor Cyan
Write-Host "  Receiver: C:\RedRibbonDemo\run_redribbon_receiver.ps1"
Write-Host "  Check:    C:\RedRibbonDemo\check_receiver_ready.ps1"
Write-Host "  Incoming: C:\RedRibbonDemo\incoming"
Write-InstallLog "install_complete"
