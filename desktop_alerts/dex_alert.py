#!/usr/bin/env python3
r"""One call to put an error on the desktop (the alerts tray app).

Any script that hits a failure a person should know about calls:

    import sys; sys.path.insert(0, r"C:\path\to\desktop-alerts\desktop_alerts")
    import desktop_alerts
    desktop_alerts.raise_alert("Short title", "What happened and what to do", source="My job")

or from a shell:

    python C:\path\to\desktop-alerts\desktop_alerts\desktop_alerts.py raise --title "Short title" --message "What happened"

It appends one line to the tray app's inbox (data\desktop-inbox.jsonl).
The tray app shows it as a Windows notification within a minute and keeps it in its list for
48 hours. Standard library only, never raises, and never blocks the caller.

Repeats: the same source and title is dropped for `repeat_hours` (default 6), so a job that
fails every 20 minutes shows once, not 18 times. Pass repeat_hours=0 to always show.

Use this for discrete events from a script. Failures of scheduled jobs (cron, Task Scheduler,
systemd, health checks) need nothing: the system health collector finds those and the tray app
lists them on its own.

Env: DESKTOP_ALERT_INBOX (inbox path; tests point it at a temp file), DESKTOP_ALERTS_OFF=1 (drop everything).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

DEFAULT_INBOX = str(Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Public")) / "DesktopAlerts" / "desktop-inbox.jsonl")
LEVELS = ("info", "warning", "error", "critical")


def _inbox() -> Path:
    return Path(os.environ.get("DESKTOP_ALERT_INBOX") or DEFAULT_INBOX)


def raise_alert(title: str, message: str = "", *, level: str = "error", source: str = "", link: str = "",
                repeat_hours: float = 6.0) -> bool:
    """True when the alert was written. False when it was a repeat, switched off, or could not be written."""
    try:
        if os.environ.get("DESKTOP_ALERTS_OFF") == "1":
            return False
        inbox = _inbox()
        if not inbox.parent.is_dir():
            return False
        title = " ".join(str(title).split())[:200]
        now = time.time()
        if repeat_hours > 0:
            seen_file = inbox.with_name(inbox.stem + "-seen.json")
            key = hashlib.sha1(f"{source}|{title}".encode("utf-8")).hexdigest()[:16]
            try:
                seen = json.loads(seen_file.read_text(encoding="utf-8"))
            except Exception:
                seen = {}
            if now - float(seen.get(key, 0)) < repeat_hours * 3600:
                return False
            seen = {k: v for k, v in seen.items() if now - float(v) < 7 * 86400}
            seen[key] = now
            seen_file.write_text(json.dumps(seen), encoding="utf-8")
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "epoch": now, "id": f"{os.getpid()}-{int(now * 1000)}",
               "level": level if level in LEVELS else "error", "title": title,
               "message": str(message)[:800].translate({0x2014: ", ", 0x2013: "-"}), "link": link, "source": source}
        with inbox.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Put an error on the desktop (alerts tray app)")
    ap.add_argument("--title", required=True)
    ap.add_argument("--message", default="")
    ap.add_argument("--level", default="error", choices=LEVELS)
    ap.add_argument("--source", default="")
    ap.add_argument("--link", default="")
    ap.add_argument("--repeat-hours", type=float, default=6.0)
    a = ap.parse_args()
    ok = raise_alert(a.title, a.message, level=a.level, source=a.source, link=a.link, repeat_hours=a.repeat_hours)
    print("alert added" if ok else "not added (a repeat inside the quiet window, switched off, or no inbox)")
    return 0


if __name__ == "__main__":
    sys.exit(main())