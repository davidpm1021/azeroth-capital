$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CollectorScript = Join-Path $ProjectRoot "scripts\\collect-once.ps1"
$TaskName = "AzerothCapital-HourlyCollector"

if (-not (Test-Path $CollectorScript)) {
    throw "Collector script not found: $CollectorScript"
}

$PowerShell = (Get-Command powershell.exe).Source
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$CollectorScript`"" -WorkingDirectory $ProjectRoot
$Start = (Get-Date).AddMinutes(2)
$Trigger = New-ScheduledTaskTrigger -Once -At $Start -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Collects the Blizzard US commodity auction snapshot for Azeroth Capital once per hour." -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "First run: $Start"
Write-Host "Logs: $ProjectRoot\\data\\logs\\collector.log"
