<#
.SYNOPSIS
    Unregisters desktop-alerts scheduled tasks.

.DESCRIPTION
    Removes the two Task Scheduler tasks created by register-desktop-alerts.ps1.
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

foreach ($n in "$identity-Desktop-Alerts", "$identity-Failures-Daily") {
    if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $n -Confirm:$false
        Write-Host "unregistered: $n"
    } else {
        Write-Host "not found: $n"
    }
}