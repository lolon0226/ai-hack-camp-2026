# RedRibbon Demo Receiver - Windows scheduled task registration
param(
    [string]$InstallRoot = "C:\RedRibbonDemo",
    [string]$TaskName = "RedRibbonDemoReceiver"
)

$ErrorActionPreference = "Stop"
$runScript = Join-Path $InstallRoot "run_redribbon_receiver.ps1"
if (-not (Test-Path $runScript)) {
    Write-Error "run script missing: $runScript"
    exit 2
}

$argList = "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argList -WorkingDirectory $InstallRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Output "scheduled_task_registered:$TaskName"
exit 0
