<#
.SYNOPSIS
    Registers desktop-alerts scheduled tasks for auto-start and daily check.

.DESCRIPTION
    Creates two Task Scheduler tasks:
      - {Identity}-Desktop-Alerts  at sign-in   (tray icon and notifications)
      - {Identity}-Failures-Daily  09:00 daily  (open-failures list, watcher check, summary)

    Both run pythonw.exe (no console window) as the signed-in user, interactive,
    because a tray icon and a notification need the desktop session.

.NOTES
    Run:  pwsh -NoProfile -File "scripts\register-desktop-alerts.ps1"
    Stop the tray app: right-click its icon and choose Quit, or stop the PID in
    %LOCALAPPDATA%\DesktopAlerts\desktop-alerts.pid

    Requires: DESTOP_ALERTS_IDENTITY, DESKTOP_ALERTS_DATA_DIR, DESKTOP_ALERTS_PORTAL_URL
    to be set in the .env file at the package root.
#>
$ErrorActionPreference = 'Stop'

# Load .env file
$envFile = "$PSScriptRoot\..\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') {
            $name = $matches[1]
            $value = $matches[2].Trim().Trim('"').Trim("'")
            $env:$name = $value
        }
    }
}

$identity = $env:DESKTOP_ALERTS_IDENTITY ?? "DesktopAlerts"
$dataDir = $env:DESKTOP_ALERTS_DATA_DIR ?? "$env:LOCALAPPDATA\DesktopAlerts"
$python = $env:DESKTOP_ALERTS_PYTHON ?? (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source ?? "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe"
$scriptDir = "$PSScriptRoot\..\desktop_alerts"
$mainScript = "$scriptDir\desktop_alerts.py"

foreach ($p in @($python, $mainScript)) {
    if (-not (Test-Path $p)) { throw "missing: $p" }
}

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

# Tray app at sign-in
$run = New-ScheduledTaskAction -Execute $python -Argument "`"$mainScript`" run" -WorkingDirectory $scriptDir
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$s1 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "$identity-Desktop-Alerts" -Action $run -Trigger $atLogon -Settings $s1 `
  -Principal $principal -Force `
  -Description "Tray icon and Windows notifications for every open failure the system health collector finds" | Out-Null

# Daily check at 09:00
$daily = New-ScheduledTaskAction -Execute $python -Argument "`"$mainScript`" daily" -WorkingDirectory $scriptDir
$at9 = New-ScheduledTaskTrigger -Daily -At '09:00'
$s2 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -Hidden
Register-ScheduledTask -TaskName "$identity-Failures-Daily" -Action $daily -Trigger $at9 -Settings $s2 `
  -Principal $principal -Force `
  -Description "Morning failure check: writes data\open-failures.md, restarts the tray app if it died, shows one summary notification" | Out-Null

foreach ($n in "$identity-Desktop-Alerts", "$identity-Failures-Daily") {
    $t = Get-ScheduledTask -TaskName $n
    Write-Host ("registered: {0}  state={1}  next={2}" -f $n, $t.State, (Get-ScheduledTaskInfo -TaskName $n).NextRunTime)
}