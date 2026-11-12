# Desktop Alerts - Agent Deployment Instructions

This document explains how to deploy and operate the desktop-alerts package on any Windows machine.

## Quick Deploy (Automated)

```powershell
# 1. Clone the repository
git clone https://github.com/dexteon/desktop-alerts.git
cd desktop-alerts

# 2. Run the installer (customize as needed)
python scripts/install.py --identity "MyLab" --portal-url "https://my-dashboard/health" --data-dir "C:\MyLab\alerts"

# 3. Register scheduled tasks for auto-start and daily checks
pwsh -File scripts/register-desktop-alerts.ps1
```

The installer handles:
- Python dependencies (pystray, winotify, pillow, pywin32)
- URI protocol registration (`desktop-alerts:`)
- Startup shortcut creation
- `.env` configuration file generation

## Manual Deploy (Step by Step)

### Prerequisites
- Windows 10/11
- Python 3.10+ in PATH (tested on 3.12)
- Git (optional, for cloning)

### 1. Install Python Dependencies
```powershell
python -m pip install pystray winotify pillow pywin32
```

### 2. Configure Environment
Create a `.env` file in the package root:
```ini
DESKTOP_ALERTS_IDENTITY=MyLab
DESKTOP_ALERTS_DATA_DIR=C:\MyLab\alerts
DESKTOP_ALERTS_PORTAL_URL=https://my-dashboard/health
DESKTOP_ALERTS_HERMES_EXE=C:\path\to\hermes.exe
DESKTOP_ALERTS_CLAUDE_CMD=claude
DESKTOP_ALERTS_POLL_S=60
DESKTOP_ALERTS_STALE_MIN=90
DESKTOP_ALERTS_SYS_HOURS=72
DESKTOP_ALERTS_INBOX_KEEP_H=48
```

### 3. Register URI Protocol Handler
```powershell
python desktop_alerts/desktop_alerts.py test
```
This registers the `desktop-alerts:` protocol for notification buttons.

### 4. Create Startup Shortcut (Auto-start at Sign-in)
```powershell
# Via PowerShell
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\MyLab Alerts.lnk")
$Shortcut.TargetPath = "pythonw.exe"
$Shortcut.Arguments = '"C:\path\to\desktop-alerts\desktop_alerts\desktop_alerts.py" run'
$Shortcut.WorkingDirectory = "C:\path\to\desktop-alerts\desktop_alerts"
$Shortcut.Description = "MyLab desktop alerts tray app"
$Shortcut.Save()
```

### 5. Register Scheduled Tasks
```powershell
pwsh -File scripts/register-desktop-alerts.ps1
```
Creates two tasks:
- `MyLab-Desktop-Alerts` - runs at sign-in
- `MyLab-Failures-Daily` - runs at 09:00 daily

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DESKTOP_ALERTS_IDENTITY` | No | `DesktopAlerts` | App name (tray, mutex, task names) |
| `DESKTOP_ALERTS_DATA_DIR` | No | `%LOCALAPPDATA%\DesktopAlerts` | State/inbox/log directory |
| `DESKTOP_ALERTS_PORTAL_URL` | No | *(empty)* | "Health page" button URL |
| `DESKTOP_ALERTS_HERMES_EXE` | No | Auto-detect | Path to `hermes.exe` for AI fixing |
| `DESKTOP_ALERTS_CLAUDE_CMD` | No | `claude` | Command to open Claude with fixes |
| `DESKTOP_ALERTS_POLL_S` | No | `60` | Poll interval (seconds) |
| `DESKTOP_ALERTS_STALE_MIN` | No | `90` | Collector stale threshold (minutes) |
| `DESKTOP_ALERTS_SYS_HOURS` | No | `72` | System log lookback (hours) |
| `DESKTOP_ALERTS_INBOX_KEEP_H` | No | `48` | Custom alert TTL (hours) |
| `DESKTOP_ALERTS_OFF` | No | *(unset)* | Set to `1` to disable all alerts |

## Usage

### Run the Tray App
```powershell
# Foreground (for testing)
python desktop_alerts/desktop_alerts.py run --show

# Background (normal operation via Task Scheduler/startup)
pythonw.exe desktop_alerts/desktop_alerts.py run
```

### Common Commands
```powershell
# Show status
python desktop_alerts/desktop_alerts.py status

# Open alert window
python desktop_alerts/desktop_alerts.py show

# Morning check (runs daily at 09:00 via Task Scheduler)
python desktop_alerts/desktop_alerts.py daily

# Raise custom alert from script/CI
python desktop_alerts/desktop_alerts.py raise --title "Build failed" --message "CI pipeline error" --level error --source "GitHub Actions"

# Test notification
python desktop_alerts/desktop_alerts.py test
```

### From Python Scripts
```python
import sys
sys.path.insert(0, r"C:\path\to\desktop-alerts\desktop_alerts")
from desktop_alerts import raise_alert

raise_alert(
    title="Backup failed",
    message="Offsite backup to Wasabi timed out",
    level="error",
    source="Backup job",
    link="https://github.com/.../runs/123",
    repeat_hours=6  # dedupe same source+title for 6 hours
)
```

## Data Files (in DESKTOP_ALERTS_DATA_DIR)

| File | Purpose |
|------|---------|
| `desktop-alerts-state.json` | Seen/dismissed/snoozed state |
| `desktop-inbox.jsonl` | Custom alerts (48h TTL) |
| `desktop-alerts.log` | App log |
| `open-failures.md` | Human-readable failure list |
| `open-failures.json` | Machine-readable failure list |
| `fix-with-claude.cmd` | Opens Claude with failure list |
| `desktop-alerts.pid` | Running tray app PID |
| `desktop-alerts.show` | Flag to show window |
| `hermes-runs/` | Hermes AI fix sessions |

## Uninstall

```powershell
# Full removal (keeps data with --keep-data)
python scripts/uninstall.py --identity "MyLab"

# Remove scheduled tasks only
pwsh -File scripts/unregister-desktop-alerts.ps1
```

## Troubleshooting

**Notifications not appearing:**
- Check Windows Settings → System → Notifications → "MyLab Alerts" is enabled
- Verify `winotify` works: `python -c "from winotify import Notification; Notification('test','test').show()"`

**Protocol handler not working:**
- Re-run `python desktop_alerts/desktop_alerts.py test` to re-register
- Check registry: `reg query HKCU\Software\Classes\desktop-alerts`

**Tray app won't start:**
- Check `desktop-alerts.log` in data directory
- Verify Python dependencies installed
- Run manually with `python desktop_alerts/desktop_alerts.py run --show` to see errors

**Scheduled tasks not running:**
- Verify tasks exist: `Get-ScheduledTask MyLab*`
- Check Task Scheduler history for errors
- Ensure `pythonw.exe` is in PATH or set `DESKTOP_ALERTS_PYTHON` in `.env`

## Architecture Notes

- Single-file app (`desktop_alerts.py`) - easy to audit and modify
- All config via environment variables - no code changes needed
- Standard library + 4 dependencies (pystray, winotify, pillow, pywin32)
- Windows-only (uses win32 APIs for system log, toast notifications, mutex)
- Can run multiple instances with different `DESKTOP_ALERTS_IDENTITY` values

## Security

- No network calls except optional Hermes/Claude integration (local)
- No credentials stored - all config via environment
- URI protocol only opens local files/URLs
- Inbox deduplication prevents notification spam
- `DESKTOP_ALERTS_OFF=1` disables all alert processing for testing