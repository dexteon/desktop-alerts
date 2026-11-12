# Desktop Alerts

A standalone, configurable Windows tray application that shows system failures and custom alerts as desktop notifications with actionable buttons.

## Features

- **Tray icon** with live problem count and color-coded status (green/red/yellow/gray)
- **Windows toast notifications** with action buttons: "Show alerts", "Health page", "Fix with AI"
- **Alert window** (full-height sidebar) showing all open problems sorted by priority
- **Custom alerts** from any script via `desktop_alerts.py raise --title "..." --message "..."`
- **Windows System log monitoring** for critical events (BSOD, disk errors, forced shutdowns, service crashes)
- **Stalled collector detection** - alerts when health checks stop running
- **Priority system**: P1 (fix now), P2 (fix soon), P3 (when free)
- **Configurable identity** - run multiple instances with different names
- **Hermes/Claude integration** - send problems to AI for automated fixing
- **Daily summary** at 09:00 with automatic tray restart if dead

## Quick Install

```powershell
# Clone or download the package
cd desktop-alerts

# Install with defaults (uses %LOCALAPPDATA%\DesktopAlerts for data)
python scripts/install.py

# Or customize:
python scripts/install.py --identity "MyLab" --portal-url "https://my-dashboard/health" --data-dir "C:\MyLab\alerts"
```

This installs dependencies, registers the `desktop-alerts:` URI protocol, creates a startup shortcut, and writes a `.env` config file.

## Configuration

All config via environment variables (written to `.env` by installer):

| Variable | Default | Description |
|----------|---------|-------------|
| `DESKTOP_ALERTS_IDENTITY` | `DesktopAlerts` | App name (tray title, mutex, task names) |
| `DESKTOP_ALERTS_DATA_DIR` | `%LOCALAPPDATA%\DesktopAlerts` | Where state, inbox, logs live |
| `DESKTOP_ALERTS_PORTAL_URL` | *(empty)* | URL for "Health page" button |
| `DESKTOP_ALERTS_HERMES_EXE` | Auto-detect | Path to `hermes.exe` for "Fix with AI" |
| `DESKTOP_ALERTS_CLAUDE_CMD` | `claude` | Command to open Claude with fix prompt |
| `DESKTOP_ALERTS_POLL_S` | `60` | Poll interval seconds |
| `DESKTOP_ALERTS_STALE_MIN` | `90` | Collector stale threshold minutes |
| `DESKTOP_ALERTS_SYS_HOURS` | `72` | How far back to read Windows System log |
| `DESKTOP_ALERTS_INBOX_KEEP_H` | `48` | How long to keep custom inbox entries |
| `DESKTOP_ALERTS_OFF=1` | *(unset)* | Drop all alerts (testing) |

Edit `.env` and restart the tray app to apply changes.

## Usage

```powershell
# Run tray app manually
python desktop_alerts/desktop_alerts.py run

# Open alert window of running app
python desktop_alerts/desktop_alerts.py show

# Show status
python desktop_alerts/desktop_alerts.py status

# Morning check (also runs via Task Scheduler)
python desktop_alerts/desktop_alerts.py daily

# Raise a custom alert from any script
python desktop_alerts/desktop_alerts.py raise --title "Build failed" --message "CI pipeline broke" --level error --source "GitHub Actions"

# Test notification
python desktop_alerts/desktop_alerts.py test

# URI handlers (used by notification buttons)
python desktop_alerts/desktop_alerts.py uri desktop-alerts:show
python desktop_alerts/desktop_alerts.py uri desktop-alerts:page
python desktop_alerts/desktop_alerts.py uri desktop-alerts:fix
```

### From Python scripts

```python
import sys
sys.path.insert(0, r"C:\path\to\desktop-alerts\desktop_alerts")
import desktop_alerts

desktop_alerts.raise_alert(
    title="Backup failed",
    message="Offsite backup to Wasabi timed out",
    level="error",
    source="Backup job",
    link="https://github.com/.../runs/123",
    repeat_hours=6  # dedupe same source+title for 6 hours
)
```

Or via the `dex_alert.py` helper (copied to your scripts folder):

```python
from dex_alert import raise_alert
raise_alert("Disk full", "C: has 2 GB free", source="Disk monitor")
```

## Scheduled Tasks

The installer can register two tasks (run `scripts/register-desktop-alerts.ps1` after install):

| Task | Trigger | Action |
|------|---------|--------|
| `{Identity}-Desktop-Alerts` | At sign-in | `pythonw.exe desktop_alerts.py run` |
| `{Identity}-Failures-Daily` | Daily 09:00 | `pythonw.exe desktop_alerts.py daily` |

Run manually:
```powershell
pwsh -File scripts/register-desktop-alerts.ps1
pwsh -File scripts/unregister-desktop-alerts.ps1
```

## Data Files

All in `DESKTOP_ALERTS_DATA_DIR`:
- `desktop-alerts-state.json` - Seen/dismissed/snoozed state
- `desktop-inbox.jsonl` - Custom alerts (append-only, 48h TTL)
- `desktop-alerts.log` - App log
- `open-failures.md` - Human-readable failure list
- `open-failures.json` - Machine-readable failure list
- `fix-with-claude.cmd` - Opens Claude with the failure list
- `desktop-alerts.pid` - Running tray app PID
- `desktop-alerts.show` - Flag file to show window
- `hermes-runs/` - Hermes AI fix sessions

## Uninstall

```powershell
python scripts/uninstall.py --identity "MyLab"
```

Add `--keep-data` to preserve the data directory.

## Requirements

- Windows 10/11
- Python 3.10+ (tested on 3.12)
- `pystray`, `winotify`, `pillow`, `pywin32` (installed by installer)

## Architecture

```
desktop-alerts/
├── desktop_alerts/
│   └── desktop_alerts.py    # Single-file app (all logic)
├── scripts/
│   ├── install.py           # Installs deps, registers protocol, creates startup shortcut
│   ├── uninstall.py         # Removes everything
│   ├── register-desktop-alerts.ps1   # Task Scheduler registration
│   └── unregister-desktop-alerts.ps1 # Task Scheduler removal
├── .env                     # Generated config (gitignored)
└── README.md
```

## License

MIT - Use freely.

<!-- ca-tracked-link -->
[cyberautomations.com](https://cyberautomations.com/?utm_source=github&utm_medium=readme&utm_campaign=desktop-alerts)