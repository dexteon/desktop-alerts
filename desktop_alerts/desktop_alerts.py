#!/usr/bin/env python3
r"""Desktop alerts: a tray icon, Windows notifications and a tall alert window for anything broken.

What it shows, ranked by priority (P1 first):
  1. Custom alerts any script drops in the inbox (dex_alert.py raise --title T --message M)
  2. System failures from Windows System log (forced off, BSOD, disk errors, crashed services, low memory)
  3. A stalled collector (heartbeat older than configurable threshold)
  4. Quiet failures no job reports (judge unreachable, gateway not answering)

Commands:
  run      the tray app (Task Scheduler runs it at sign-in; one instance only). --show opens the window
  show     open the alert window of the running app (starts the app if needed)
  uri U    handler for the desktop-alerts: links that notifications use (show, page, fix)
  daily    the morning check: writes the open-failures list, makes sure the tray app and the
           collector are alive, and shows one summary notification
  raise    add a custom alert:  raise --title T --message M [--level error] [--link URL] [--source S]
  status   print what is open and whether the tray app is running
  test     show one sample notification

The window: full screen height at the right edge, one card per problem, sorted by priority.
Click a card for the reviewer's cause and fix. "Send to Hermes" (top bar: every P1 and P2; on a
card: that one problem) writes the problems and the house rules to data\hermes-runs and opens a
Hermes chat on them in a new window; Hermes writes a report there and raises a desktop alert when
it is done. Left-click the tray icon, or click a notification, to open it. Closing it only hides it.

Priority: P1 is anything critical, any system failure that loses work or data, and a stalled
outcome open five days or more. P2 is a failing job or sync. P3 is stale data and low-level notices.
Within a priority, the longest open comes first.

Central list: data\open-failures.md, rewritten whenever the open set changes.
State: desktop-alerts-state.json, inbox: desktop-inbox.jsonl, pid: desktop-alerts.pid (same folder,
private, gitignored). Needs: pystray, winotify, Pillow (tkinter ships with Python).

Config (all via environment variables):
  DESKTOP_ALERTS_IDENTITY     App name (default: "DesktopAlerts")
  DESKTOP_ALERTS_DATA_DIR     Where state/inbox/log live (default: %LOCALAPPDATA%\DesktopAlerts)
  DESKTOP_ALERTS_POLL_S       Poll interval seconds (default: 60)
  DESKTOP_ALERTS_STALE_MIN    Collector stale threshold minutes (default: 90)
  DESKTOP_ALERTS_PORTAL_URL   Health page URL for "Health page" button (default: "")
  DESKTOP_ALERTS_HERMES_EXE   Path to hermes.exe for "Fix with Hermes" (default: auto-detect)
  DESKTOP_ALERTS_CLAUDE_CMD   Command to open Claude with fix prompt (default: "claude")
  DESKTOP_ALERTS_SYS_HOURS    How far back to read Windows System log (default: 72)
  DESKTOP_ALERTS_INBOX_KEEP_H How long to keep inbox entries (default: 48)
  DESKTOP_ALERTS_OFF=1        Drop all alerts (for testing)

Register with:  powershell -File scripts/register-desktop-alerts.ps1
Unregister with: powershell -File scripts/unregister-desktop-alerts.ps1
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ── Config ──────────────────────────────────────────────────────────────
ALERT_IDENTITY = os.environ.get("DESKTOP_ALERTS_IDENTITY", "DesktopAlerts")
APP_ID = f"{ALERT_IDENTITY} Alerts"
MUTEX = f"Local\\{ALERT_IDENTITY}DesktopAlerts"

DATA_DIR = Path(os.environ.get("DESKTOP_ALERTS_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Public")) / "DesktopAlerts")
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE = DATA_DIR / "desktop-alerts-state.json"
INBOX = DATA_DIR / "desktop-inbox.jsonl"
PIDFILE = DATA_DIR / "desktop-alerts.pid"
SHOW_FLAG = DATA_DIR / "desktop-alerts.show"
REPORT_MD = DATA_DIR / "open-failures.md"
REPORT_JSON = DATA_DIR / "open-failures.json"
FIX_CMD = DATA_DIR / "fix-with-claude.cmd"
ICON_PNG = DATA_DIR / "desktop-alerts-icon.png"
LOG = DATA_DIR / "desktop-alerts.log"

POLL_S = int(os.environ.get("DESKTOP_ALERTS_POLL_S", "60"))
STALE_MIN = int(os.environ.get("DESKTOP_ALERTS_STALE_MIN", "90"))
SYS_HOURS = int(os.environ.get("DESKTOP_ALERTS_SYS_HOURS", "72"))
SYS_EVERY_S = 600
INBOX_KEEP_H = int(os.environ.get("DESKTOP_ALERTS_INBOX_KEEP_H", "48"))
MENU_ROWS = 12

PAGE_URL = os.environ.get("DESKTOP_ALERTS_PORTAL_URL", "")
HERMES_EXE = Path(os.environ.get("DESKTOP_ALERTS_HERMES_EXE") or Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Public")) / "hermes" / "bin" / "hermes.exe")
CLAUDE_CMD = os.environ.get("DESKTOP_ALERTS_CLAUDE_CMD", "claude")

LEVEL_RANK = {"critical": 3, "error": 2, "warning": 1, "info": 0}
LEVEL_BASE = {"critical": 100, "error": 60, "warning": 30, "info": 10}
KIND_BONUS = {
    "progress": 30, "hermes": 15, "publishing": 20, "jev": 15, "alerts": 40, "task": 10, "mac": 10,
    "litellm": 10, "overdue": 10, "n8n": 10, "watchdog": 15, "refresh": 5, "freshness": 0,
    "system": 20, "resource": 10, "custom": 5
}
NO_WINDOW = 0x08000000

HERMES_DIR = DATA_DIR / "hermes-runs"
HERMES_RULES = """Rules for this job:
- Verify each cause before you change anything. The notes below are leads, not facts.
- You may read logs and code, re-run a failed task to reproduce it, fix a broken path, argument or config line (copy the file to <name>.bak-<date> first), and restart a stopped service with its existing settings.
- Never print a secret (keys, tokens, passwords, .env values). Never open the API key store.
- Never stop a process picked by name; only by a process ID you checked. Never close a browser. Never start Ollama or a local model on this PC.
- Nothing that cannot be undone and nothing that leaves this network: no deleting data, no git push, no sending messages or email, no posting, no orders or payments, no bank logins.
- If a fix needs any of those, do not do it. List it under "Needs User" with the exact command.
- Confirm each fix by its effect (re-run it, read the log), not by an exit code."""

# ── Logging ─────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ── Data loading ────────────────────────────────────────────────────────
def _days(first_seen: str, now: datetime) -> float:
    try:
        return max(0.0, (now - datetime.fromisoformat(first_seen).astimezone(now.tzinfo)).total_seconds() / 86400)
    except Exception:
        return 0.0


def _age(first_seen: str, now: datetime) -> str:
    d = _days(first_seen, now)
    if d < 1 / 24:
        m = int(d * 24 * 60)
        return f"{m} min" if m else "just now"
    if d < 1:
        h = int(d * 24)
        return f"{h} h"
    if d < 7:
        return f"{int(d)} day{'s' if int(d) != 1 else ''}"
    w = int(d / 7)
    return f"{w} week{'s' if w != 1 else ''}"


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"seen": {}, "dismissed": {}, "snooze_until": 0, "inbox_cleared_at": 0}


def save_state(state: dict) -> None:
    try:
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(tmp, STATE)
    except OSError as e:
        log(f"state write failed: {e}")


def load_open(state: dict) -> list:
    now = datetime.now().astimezone()
    rows = []

    # 1. Custom inbox alerts
    inbox_cleared = state.get("inbox_cleared_at", 0)
    try:
        for line in INBOX.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e["epoch"] <= inbox_cleared:
                continue
            if now - datetime.fromisoformat(e["ts"]).astimezone(now.tzinfo) > timedelta(hours=INBOX_KEEP_H):
                continue
            level = e.get("level", "error")
            prio = 1 if level == "critical" else 2 if level == "error" else 3
            rows.append({
                "key": f"custom:{e['id']}",
                "level": level,
                "kind": "Custom alert",
                "kind_code": "custom",
                "title": e["title"],
                "first_seen": e["ts"],
                "total": 1,
                "summary": e.get("message", ""),
                "fix": "",
                "link": e.get("link", "") or PAGE_URL,
                "priority": prio,
                "dismissable": True,
                "source": e.get("source", "")
            })
    except OSError:
        pass

    # 2. Windows System log problems
    rows.extend(system_rows(now, state))

    # 3. Stalled collector (if latest.json exists and is old)
    latest = Path(os.environ.get("DESKTOP_ALERTS_LATEST_JSON", ""))
    if latest.exists():
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
            last = datetime.fromisoformat(data["collector"]["last_run"]).astimezone(now.tzinfo)
            if (now - last).total_seconds() > STALE_MIN * 60:
                rows.append({
                    "key": "system:stalled-collector",
                    "level": "critical",
                    "kind": "System",
                    "kind_code": "system",
                    "title": f"Collector stalled: last run {_age(data['collector']['last_run'], now)} ago",
                    "first_seen": data["collector"]["last_run"],
                    "total": 1,
                    "summary": f"Expected a check every 30 minutes. Last successful read: {data['collector']['last_run']}.",
                    "fix": "Check Task Scheduler: CyberDash-SysHealth-Collect. Verify the Python environment and network.",
                    "link": PAGE_URL,
                    "priority": 1,
                    "dismissable": True
                })
        except Exception:
            pass

    # Priority sort: P1 first, then longest open
    rows.sort(key=lambda r: (r["priority"], -_days(r["first_seen"], now) if r["first_seen"] else 0))
    return rows


def system_rows(now: datetime, state: dict) -> list:
    """Read Windows System log for critical errors."""
    import win32evtlog
    rows = []
    try:
        hand = win32evtlog.OpenEventLog(None, "System")
        flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
        cutoff = now - timedelta(hours=SYS_HOURS)
        forced = []
        restarts = []
        last_each = {}
        groups = {}

        while True:
            events = win32evtlog.ReadEventLog(hand, flags, 0)
            if not events:
                break
            for e in events:
                t = datetime.fromtimestamp(e.TimeGenerated.timestamp()).astimezone()
                if t < cutoff:
                    break
                if e.EventID not in (41, 6008, 1001, 1074, 1076, 6005, 6006, 6009, 7, 9, 11, 51, 55, 140, 1001):
                    continue
                if e.EventType not in (win32evtlog.EVENTLOG_ERROR_TYPE, win32evtlog.EVENTLOG_WARNING_TYPE):
                    continue

                src = e.SourceName or "System"
                msg = " ".join(e.StringInserts) if e.StringInserts else f"Event ID {e.EventID}"
                msg = msg[:500]

                slug = ""
                prio = 2
                fix = ""
                title = ""

                if e.EventID == 41:          # Kernel-Power: unexpected shutdown
                    slug = "bad-shutdown"
                    prio = 1
                    title = "PC forced off or lost power"
                    fix = "Check PSU, thermals, UPS. Event 41 = dirty shutdown. Get-WinEvent -LogName System -FilterXPath \"*/System[EventID=41]\" | Select TimeCreated,Message"
                elif e.EventID == 6008:      # Unexpected shutdown
                    slug = "bad-shutdown"
                    prio = 1
                    title = "Previous shutdown was unexpected"
                    fix = "Same as Event 41. Often paired with 41. Check for BSOD (Event 1001) nearby."
                elif e.EventID == 1001:      # Bugcheck (BSOD)
                    slug = "bugcheck"
                    prio = 1
                    title = "Blue screen (bugcheck)"
                    fix = "Analyze C:\\Windows\\MEMORY.DMP with WinDbg. Look for driver names in the bugcheck parameters."
                elif e.EventID in (1074, 1076):  # User-initiated / app shutdown
                    slug = "user-shutdown"
                    prio = 3
                    title = "User or application initiated shutdown"
                    fix = "Normal if you restarted. If unexpected, check for forced Windows Update restart."
                elif e.EventID == 6005:      # Event log service started
                    restarts.append(t)
                elif e.EventID == 6006:      # Event log service stopped
                    forced.append(t)
                elif e.EventID == 7:         # Disk bad block
                    slug = "disk-bad-block"
                    prio = 1
                    title = "Disk bad block detected"
                    fix = "Back up immediately. Run: chkdsk /r. Check SMART: Get-PhysicalDisk | Get-StorageReliabilityCounter"
                elif e.EventID == 51:        # Disk paging error
                    slug = "disk-paging-error"
                    prio = 1
                    title = "Disk paging error (controller/driver)"
                    fix = "Check cables, controller driver, disk health. Often precedes failure."
                elif e.EventID == 55:        # File system corruption
                    slug = "fs-corruption"
                    prio = 1
                    title = "File system corruption"
                    fix = "Run chkdsk /f /r. Check disk health."
                elif e.EventID == 140:       # File system warning
                    slug = "fs-warning"
                    prio = 2
                    title = "File system warning"
                    fix = "Run chkdsk /scan. Monitor for escalation."
                else:
                    continue

                mode = "each"
                if slug in ("bad-shutdown", "bugcheck"):
                    mode = "day"
                elif slug in ("disk-bad-block", "disk-paging-error", "fs-corruption"):
                    mode = "day"

                if mode == "service":
                    if any(abs((t - r).total_seconds()) < 360 for r in restarts):
                        continue
                    m = re.search(r"The (.+?) service terminated", msg)
                    name = m.group(1) if m else "A Windows"
                    key = f"sys:{slug}:{re.sub(r'[^a-z0-9]+', '-', name.lower())}:{t:%Y%m%d}"
                    title = f"The {name} service {title}"
                elif mode == "each":
                    prev = last_each.get(slug)
                    key = prev[1] if prev and (t - prev[0]).total_seconds() < 1800 else f"sys:{slug}:{t:%Y%m%d%H%M}"
                    last_each[slug] = (t, key)
                else:
                    key = f"sys:{slug}:{t:%Y%m%d}"

                g = groups.setdefault(key, {
                    "key": key, "level": "critical" if prio == 1 else "error",
                    "kind": "System", "kind_code": "system",
                    "title": title, "first_seen": t.isoformat(), "total": 0,
                    "summary": msg[:400], "fix": fix, "link": PAGE_URL,
                    "priority": prio, "dismissable": True
                })
                g["total"] += 1
                if t.isoformat() < g["first_seen"]:
                    g["first_seen"] = t.isoformat()

        win32evtlog.CloseEventLog(hand)
        for g in groups.values():
            g["age"] = _age(g["first_seen"], now)
            if g["total"] > 1 and "service" not in g["key"]:
                g["title"] += f" ({g['total']} times)"
            rows.append(g)
    except Exception as e:
        log(f"system log probe failed: {type(e).__name__}: {e}")
    return rows


# ── Reporting ───────────────────────────────────────────────────────────
def fix_label(p: dict) -> str:
    if p.get("waiting"):
        return "Fixed, waiting for proof"
    if p.get("basis") == "checked":
        return "Needs you" if p.get("state") == "needs_user" else "What to do (checked)"
    if p.get("basis") == "guess":
        return "Unchecked guess, look only"
    return "What to do"


def actionable(rows: list) -> list:
    return [p for p in rows if not p.get("waiting") and p.get("state") != "needs_user"]


def write_report(rows: list) -> None:
    now = datetime.now().astimezone()
    L = [f"# Open failures ({len(rows)})", "",
         f"Written {now.strftime('%Y-%m-%d %H:%M')} by desktop_alerts.py, highest priority first. ",
         f"Full page: {PAGE_URL or '(no portal URL configured)'}", ""]
    if not rows:
        L.append("Nothing is broken right now.")
    for n, p in enumerate(rows, 1):
        L.append(f"## {n}. [P{p['priority']}] {p['title']}")
        L.append(f"- Level: {p['level']}. Area: {p['kind']}. Open for: {p['age'] or 'unknown'}. Seen {p['total']} time(s).")
        if p["summary"]:
            L.append(f"- What is known: {p['summary']}")
        if p.get("trust"):
            L.append(f"- How far to trust this: {p['trust']}")
        if p["fix"]:
            L.append(f"- {fix_label(p)}: {p['fix']}")
        L.append("")
    text = "\n".join(L).translate({0x2014: ", ", 0x2013: "-"})
    try:
        body = lambda s: "\n".join(s.splitlines()[3:])
        if not REPORT_MD.exists() or body(REPORT_MD.read_text(encoding="utf-8")) != body(text):
            REPORT_MD.write_text(text, encoding="utf-8")
            REPORT_JSON.write_text(json.dumps({"written": now.isoformat(), "open": rows}, indent=1), encoding="utf-8")
    except OSError as e:
        log(f"report write failed: {e}")
    if not FIX_CMD.exists():
        try:
            FIX_CMD.write_text(
                "@echo off\r\n"
                f'start "Fix open failures" cmd /k {CLAUDE_CMD} "Read {REPORT_MD}. These are the problems open right now, '
                'highest priority first. Work through them in order: find the cause, fix what you safely can, and tell me plainly what needs me."\r\n',
                encoding="utf-8")
        except OSError:
            pass


# ── Open targets ────────────────────────────────────────────────────────
def open_target(target) -> None:
    try:
        os.startfile(str(target))
    except Exception as e:
        log(f"open failed for {target}: {type(e).__name__}: {e}")
        try:
            subprocess.Popen(["cmd", "/c", "start", "", str(target)], creationflags=NO_WINDOW)
        except Exception as e2:
            log(f"open fallback failed: {e2}")


def send_to_hermes(rows: list) -> str:
    skipped = len(rows) - len(actionable(rows))
    rows = actionable(rows)
    if not rows:
        return "Nothing to send: what is open needs you, or is waiting for its next run"
    if not HERMES_EXE.exists():
        return f"Hermes was not found at {HERMES_EXE}"
    try:
        HERMES_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        prompt, result = HERMES_DIR / f"{stamp}-prompt.md", HERMES_DIR / f"{stamp}-result.md"
        L = [f"Fix the {len(rows)} problem(s) below (from {ALERT_IDENTITY} alerts), highest priority first.", "", HERMES_RULES, ""]
        for n, p in enumerate(rows, 1):
            L += [f"## {n}. [P{p['priority']}] {p['title']}",
                  f"Area: {p['kind']}. Open for: {p['age'] or 'unknown'}. Seen {p['total']} time(s)."]
            if p["summary"]:
                L.append(f"What is known: {p['summary']}")
            if p.get("trust"):
                L.append(f"How far to trust the notes: {p['trust']}")
            if p["fix"]:
                L.append(f"{fix_label(p)}: {p['fix']}")
            L.append("")
        L += ["When you finish:",
              f"1. Write a short report to {result.as_posix()}: for each problem the cause, what you changed, the proof it worked, "
              "and a \"Needs User\" list with exact commands.",
              "2. Then run this so the user sees it on their desktop:",
              f'   python {HERE / "desktop_alerts.py"} raise --title "Hermes finished the alert fixes" '
              f'--message "Report: {result.as_posix()}" --level info --source Hermes --repeat-hours 0']
        prompt.write_text("\n".join(L).translate({0x2014: ", ", 0x2013: "-"}), encoding="utf-8")
        subprocess.Popen(["cmd", "/c", "start", "Hermes: fix alerts", "cmd", "/k", str(HERMES_EXE), "chat", "--query-file", str(prompt)], creationflags=NO_WINDOW)
        log(f"sent {len(rows)} problem(s) to Hermes: {prompt.name}")
        left = f" {skipped} left out (they need you, or wait for a run)." if skipped else ""
        return f"Sent {len(rows)} problem(s) to Hermes.{left} Report goes to hermes-runs\\{result.name}"
    except Exception as e:
        log(f"send to hermes failed: {type(e).__name__}: {e}")
        return f"Could not start Hermes: {type(e).__name__}"


# ── Toast notifications ─────────────────────────────────────────────────
def make_icon(count: int, worst: int, size: int = 64):
    from PIL import Image, ImageDraw, ImageFont
    color = (46, 160, 67) if count == 0 else (207, 34, 46) if worst == 1 else (218, 142, 22) if worst == 2 else (110, 118, 129)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=color)
    text = "\u2713" if count == 0 else (str(count) if count < 100 else "99")
    try:
        font = ImageFont.truetype("seguisym.ttf" if count == 0 else "arialbd.ttf", int(size * (0.6 if len(text) < 2 else 0.5)))
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), text, font=font)
    d.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]), text, fill="white", font=font)
    return img


def register_protocol() -> None:
    try:
        import winreg
        pyw = Path(sys.executable).with_name("pythonw.exe")
        cmd = f'"{pyw if pyw.exists() else sys.executable}" "{Path(__file__).resolve()}" uri "%1"'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\desktop-alerts") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f"URL:{ALERT_IDENTITY} Alerts")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\desktop-alerts\shell\open\command") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, cmd)
    except Exception as e:
        log(f"protocol registration failed: {type(e).__name__}: {e}")


def toast(title: str, message: str, link: str = "", urgent: bool = False, with_fix: bool = False) -> None:
    try:
        from winotify import Notification, audio
        if not ICON_PNG.exists():
            make_icon(1, 1, 128).save(ICON_PNG)
        t = Notification(app_id=APP_ID, title=title[:120], msg=message[:400], icon=str(ICON_PNG),
                         duration="long" if urgent else "short", launch="desktop-alerts:show")
        t.set_audio(audio.Default, loop=False)
        t.add_actions(label="Show alerts", launch="desktop-alerts:show")
        if PAGE_URL or link:
            t.add_actions(label="Health page", launch=link or PAGE_URL)
        if with_fix:
            t.add_actions(label="Fix with AI", launch="desktop-alerts:fix")
        t.show()
    except Exception as e:
        log(f"toast failed: {type(e).__name__}: {e}")


def summary_line(rows: list) -> str:
    if not rows:
        return "Nothing is broken right now."
    n1 = sum(1 for p in rows if p["priority"] == 1)
    head = f"{len(rows)} problem{'s' if len(rows) != 1 else ''} open" + (f", {n1} top priority" if n1 else "")
    return f"{head}. First: {rows[0]['title'][:90]}" + (f" ({rows[0]['age']})" if rows[0]["age"] else "")


# ── Alert window ────────────────────────────────────────────────────────
BG, CARD, TEXT, MUTED, LINE = "#14161b", "#1d2027", "#e8e8ea", "#9aa1ad", "#2a2e37"
PRIO_COLOR = {1: "#e5484d", 2: "#f5a524", 3: "#6b7280"}
PRIO_NAME = {1: "P1  fix now", 2: "P2  fix soon", 3: "P3  when free"}


class AlertWindow:
    WIDTH = 690

    def __init__(self, root, actions: dict):
        import tkinter as tk
        self.tk, self.root, self.actions = tk, root, actions
        self.rows, self.expanded, self.sig = [], set(), None
        root.title(f"{ALERT_IDENTITY} alerts")
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        try:
            from PIL import ImageTk
            self._icon = ImageTk.PhotoImage(make_icon(1, 1, 64))
            root.iconphoto(True, self._icon)
        except Exception:
            pass
        head = tk.Frame(root, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(head, text=f"{ALERT_IDENTITY} alerts", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 15)).pack(side="left")
        self.counts = tk.Label(head, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.counts.pack(side="right")
        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=12, pady=(2, 8))
        for label, key in (("Refresh", "refresh"), ("Health page", "page"), ("Fix with AI", "fix"),
                           ("Send to Hermes", "hermes_all"), ("Snooze 1 hour", "snooze"), ("Clear custom", "clear")):
            self._button(bar, label, actions[key]).pack(side="left", padx=2)
        wrap = tk.Frame(root, bg=BG)
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.win_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win_id, width=e.width))
        self.root.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units"))
        self.foot = tk.Label(root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.foot.pack(fill="x", padx=14, pady=(4, 8))
        self.place()
        root.withdraw()

    def _button(self, parent, label, command, small=False):
        tk = self.tk
        return tk.Button(parent, text=label, command=command, bg=CARD, fg=TEXT, activebackground=LINE, activeforeground=TEXT,
                         relief="flat", bd=0, padx=10 if not small else 8, pady=4 if not small else 2, cursor="hand2",
                         font=("Segoe UI", 9))

    def place(self):
        class RECT(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
        r = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0):
            w = min(self.WIDTH, r.r - r.l)
            self.root.geometry(f"{w}x{r.b - r.t - 40}+{r.r - w - 8}+{r.t}")
        else:
            self.root.geometry(f"{self.WIDTH}x900")

    def show(self):
        self.place()
        self.canvas.yview_moveto(0)
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(400, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()

    def update(self, rows: list, note: str = ""):
        self.rows = rows
        n = {p: sum(1 for r in rows if r["priority"] == p) for p in (1, 2, 3)}
        self.counts.configure(text=(f"{n[1]} fix now   {n[2]} fix soon   {n[3]} when free" if rows else "Nothing is broken"))
        self.foot.configure(text=note or time.strftime("Checked %I:%M %p").replace(" 0", " "))
        sig = (tuple((r["key"], r["priority"], r["total"], r["age"], r.get("basis"), r.get("fix")) for r in rows),
               tuple(sorted(self.expanded)))
        if sig != self.sig:
            self.sig = sig
            self.render()

    def render(self):
        tk = self.tk
        for w in self.inner.winfo_children():
            w.destroy()
        if not self.rows:
            tk.Label(self.inner, text="Nothing is broken right now.", bg=BG, fg=MUTED, font=("Segoe UI", 11)).pack(pady=40)
            return
        wl = self.WIDTH - 90
        last = None
        for p in self.rows:
            if p["priority"] != last:
                last = p["priority"]
                tk.Label(self.inner, text=PRIO_NAME[last].upper(), bg=BG, fg=PRIO_COLOR[last],
                         font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=16, pady=(12, 2))
            card = tk.Frame(self.inner, bg=CARD)
            card.pack(fill="x", padx=12, pady=3)
            tk.Frame(card, bg=PRIO_COLOR[p["priority"]], width=5).pack(side="left", fill="y")
            body = tk.Frame(card, bg=CARD)
            body.pack(side="left", fill="both", expand=True, padx=10, pady=8)
            meta = f"{p['kind']}   open {p['age']}" if p["age"] else p["kind"]
            if p["total"] > 1:
                meta += f"   seen {p['total']} times"
            tag = {"Fixed, waiting for proof": "fixed, waiting for proof", "Needs you": "needs you",
                   "Unchecked guess, look only": "not checked yet"}.get(fix_label(p))
            if tag:
                meta += f"   ({tag})"
            widgets = [tk.Label(body, text=p["title"], bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 10), wraplength=wl,
                                justify="left", anchor="w"),
                       tk.Label(body, text=meta, bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="w")]
            if p["key"] in self.expanded:
                if p["summary"]:
                    widgets.append(tk.Label(body, text=p["summary"], bg=CARD, fg=TEXT, font=("Segoe UI", 9), wraplength=wl,
                                            justify="left", anchor="w"))
                if p.get("trust"):
                    widgets.append(tk.Label(body, text=p["trust"], bg=CARD, font=("Segoe UI", 9, "italic"), wraplength=wl,
                                            fg=("#7ee787" if p.get("basis") == "checked" else "#e3b341"),
                                            justify="left", anchor="w"))
                if p["fix"]:
                    widgets.append(tk.Label(body, text=fix_label(p) + ": " + p["fix"], bg=CARD, fg="#c9d1d9", font=("Consolas", 9),
                                            wraplength=wl, justify="left", anchor="w"))
            for w in widgets:
                w.pack(fill="x", anchor="w", pady=1)
            if p["key"] in self.expanded:
                row = tk.Frame(body, bg=CARD)
                row.pack(anchor="w", pady=(4, 0))
                if p["fix"]:
                    self._button(row, "Copy checks" if p.get("basis") == "guess" else "Copy steps",
                                 lambda t=p["fix"]: self.copy(t), small=True).pack(side="left", padx=(0, 4))
                if actionable([p]):
                    self._button(row, "Send to Hermes", lambda r=p: self.actions["hermes"]([r]), small=True).pack(side="left", padx=(0, 4))
                self._button(row, "Open page", lambda u=p["link"]: open_target(u), small=True).pack(side="left", padx=(0, 4))
                if p.get("dismissable"):
                    self._button(row, "Dismiss", lambda k=p["key"]: self.actions["dismiss"](k), small=True).pack(side="left")
            for w in [card, body] + widgets:
                w.bind("<Button-1>", lambda e, k=p["key"]: self.toggle(k))
                w.configure(cursor="hand2")

    def toggle(self, key):
        self.expanded.symmetric_difference_update({key})
        self.update(self.rows)

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.foot.configure(text="Copied to the clipboard")


# ── Tray app ────────────────────────────────────────────────────────────
def already_running() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX)
    return ctypes.windll.kernel32.GetLastError() == 183


def tray_is_running() -> bool:
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, MUTEX)
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
    return bool(h)


def run_tray(show: bool = False) -> int:
    if already_running():
        SHOW_FLAG.write_text("show", encoding="utf-8")
        print("desktop alerts: already running")
        return 0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    import tkinter as tk
    import pystray
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    register_protocol()
    state = load_state()
    lock = threading.Lock()
    shared = {"rows": [], "first": True}
    root = tk.Tk()
    wake = threading.Event()
    import queue
    jobs: "queue.Queue" = queue.Queue()

    def ui(fn):
        jobs.put(fn)

    def drain():
        try:
            while True:
                fn = jobs.get_nowait()
                try:
                    fn()
                except Exception as e:
                    log(f"ui job failed: {type(e).__name__}: {e}")
        except queue.Empty:
            pass
        root.after(120, drain)

    def do_snooze():
        with lock:
            state["snooze_until"] = time.time() + 3600
            save_state(state)
        win.foot.configure(text="Notifications are quiet for one hour")

    def do_clear():
        with lock:
            state["inbox_cleared_at"] = time.time()
        wake.set()

    def do_dismiss(key):
        with lock:
            state.setdefault("dismissed", {})[key] = time.time()
        wake.set()

    def do_hermes(rows):
        win.foot.configure(text=send_to_hermes(rows))

    def do_hermes_all():
        do_hermes([p for p in shared["rows"] if p["priority"] <= 2][:25])

    win = AlertWindow(root, {"refresh": wake.set, "page": lambda: open_target(PAGE_URL), "fix": lambda: open_target(FIX_CMD),
                             "snooze": do_snooze, "clear": do_clear, "dismiss": do_dismiss,
                             "hermes": do_hermes, "hermes_all": do_hermes_all})

    def menu():
        rows = shared["rows"]
        items = [pystray.MenuItem("Show alerts", lambda icon, item: ui(win.show), default=True),
                 pystray.MenuItem(summary_line(rows)[:110], None, enabled=False), pystray.Menu.SEPARATOR]
        for p in rows[:MENU_ROWS]:
            items.append(pystray.MenuItem(f"P{p['priority']}  {p['title'][:76]}" + (f"  ({p['age']})" if p["age"] else ""),
                                          lambda icon, item: ui(win.show)))
        if len(rows) > MENU_ROWS:
            items.append(pystray.MenuItem(f"and {len(rows) - MENU_ROWS} more in the window", lambda icon, item: ui(win.show)))
        items += [pystray.Menu.SEPARATOR,
                  pystray.MenuItem("Open health page", lambda icon, item: open_target(PAGE_URL)),
                  pystray.MenuItem("Open the failure list file", lambda icon, item: open_target(REPORT_MD)),
                  pystray.MenuItem("Fix with AI", lambda icon, item: open_target(FIX_CMD)),
                  pystray.MenuItem("Send all to Hermes", lambda icon, item: ui(do_hermes_all)),
                  pystray.MenuItem("Snooze notifications for 1 hour", lambda icon, item: ui(do_snooze)),
                  pystray.MenuItem("Clear custom alerts", lambda icon, item: do_clear()),
                  pystray.MenuItem("Quit", lambda icon, item: ui(quit_app))]
        return items

    icon = pystray.Icon("desktop-alerts", make_icon(0, 0), f"{ALERT_IDENTITY} alerts: starting", pystray.Menu(menu))

    def quit_app():
        try:
            icon.stop()
        finally:
            root.destroy()

    def apply(rows):
        shared["rows"] = rows
        worst = min((p["priority"] for p in rows), default=0)
        icon.icon = make_icon(len(rows), worst)
        icon.title = (f"{ALERT_IDENTITY} alerts: " + summary_line(rows))[:127]
        icon.update_menu()
        win.update(rows)
        with lock:
            seen = state.setdefault("seen", {})
            keys = {p["key"]: p for p in rows}
            new = [p for k, p in keys.items() if k not in seen]
            fixed = [t for k, t in seen.items() if k not in keys and k not in state.get("dismissed", {})]
            quiet = time.time() < float(state.get("snooze_until", 0))
            if shared["first"]:
                shared["first"] = False
                if rows:
                    toast(f"{ALERT_IDENTITY} alerts", summary_line(rows), with_fix=True)
                new, fixed = [], []
                for k, p in keys.items():
                    seen.setdefault(k, p["title"])
            if not quiet:
                if 0 < len(new) <= 3:
                    for p in new:
                        toast(f"P{p['priority']} {p['kind']}", p["title"] + (f"\n{p['summary'][:220]}" if p["summary"] else ""),
                              p["link"], urgent=p["priority"] == 1, with_fix=True)
                elif len(new) > 3:
                    toast(f"{len(new)} new problems", "; ".join(p["title"][:60] for p in new[:4]) + " ...", with_fix=True)
                for p in new:
                    seen[p["key"]] = p["title"]
                if fixed:
                    toast("Fixed", "; ".join(t[:70] for t in fixed[:4]) + (f" and {len(fixed) - 4} more" if len(fixed) > 4 else ""))
            for k in [k for k in seen if k not in keys]:
                seen.pop(k, None)
            save_state(state)

    def worker():
        while True:
            try:
                with lock:
                    snapshot = state
                rows = load_open(snapshot)
                write_report(rows)
                ui(lambda r=rows: apply(r))
            except Exception as e:
                log(f"tick failed: {type(e).__name__}: {e}")
            wake.wait(POLL_S)
            wake.clear()

    def poll_flag():
        try:
            if SHOW_FLAG.exists():
                SHOW_FLAG.unlink()
                win.show()
        except OSError:
            pass
        root.after(400, poll_flag)

    log(f"tray started pid {os.getpid()}")
    icon.run_detached()
    threading.Thread(target=worker, daemon=True).start()
    root.after(120, drain)
    root.after(400, poll_flag)
    if show:
        root.after(600, win.show)
    try:
        root.mainloop()
    finally:
        try:
            icon.stop()
        except Exception:
            pass
        try:
            PIDFILE.unlink()
        except OSError:
            pass
    return 0


# ── Other modes ─────────────────────────────────────────────────────────
def start_detached(*args) -> int:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    p = subprocess.Popen([str(pyw if pyw.exists() else sys.executable), str(Path(__file__).resolve()), *args],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)
    return p.pid


def cmd_show() -> int:
    if tray_is_running():
        SHOW_FLAG.write_text("show", encoding="utf-8")
    else:
        start_detached("run", "--show")
    return 0


def cmd_uri(uri: str) -> int:
    what = uri.split(":", 1)[-1].strip("/").lower()
    log(f"link opened: {what}")
    if what == "fix":
        open_target(FIX_CMD)
        return 0
    if what == "page":
        open_target(PAGE_URL)
        return 0
    return cmd_show()


def cmd_daily() -> int:
    state = load_state()
    rows = load_open(state)
    write_report(rows)
    notes = []
    if not tray_is_running():
        pid = start_detached("run")
        notes.append("the tray app was not running and was started again")
        log(f"daily: restarted tray, pid {pid}")
    old = [p for p in rows if p["age"].endswith("days") and int(p["age"].split()[0]) >= 7]
    msg = summary_line(rows)
    if old:
        msg += f"\n{len(old)} open for a week or more."
    if notes:
        msg += "\nAlso: " + "; ".join(notes) + "."
    toast("Morning failure check", msg, urgent=bool(rows), with_fix=bool(rows))
    log(f"daily: {len(rows)} open, {len(old)} older than a week; {notes}")
    print(msg)
    return 0


def cmd_raise(a) -> int:
    row = {"ts": datetime.now().astimezone().isoformat(), "epoch": time.time(),
           "id": f"{os.getpid()}-{int(time.time() * 1000)}",
           "level": a.level, "title": a.title, "message": a.message, "link": a.link, "source": a.source}
    INBOX.parent.mkdir(parents=True, exist_ok=True)
    with INBOX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    if not tray_is_running():
        toast(a.title, a.message, a.link, urgent=a.level == "critical")
    print("alert added")
    return 0


def cmd_status() -> int:
    rows = load_open(load_state())
    print("tray app running:", tray_is_running())
    print(summary_line(rows))
    for p in rows:
        print(f"  P{p['priority']} [{p['kind']}] {p['title'][:100]} ({p['age'] or 'new'})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=f"{ALERT_IDENTITY} desktop alerts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r0 = sub.add_parser("run")
    r0.add_argument("--show", action="store_true")
    for name in ("show", "daily", "status", "test"):
        sub.add_parser(name)
    u = sub.add_parser("uri")
    u.add_argument("uri")
    r = sub.add_parser("raise")
    r.add_argument("--title", required=True)
    r.add_argument("--message", default="")
    r.add_argument("--level", default="error", choices=sorted(LEVEL_RANK))
    r.add_argument("--link", default="")
    r.add_argument("--source", default="")
    a = ap.parse_args()
    if a.cmd == "run":
        return run_tray(a.show)
    if a.cmd == "show":
        return cmd_show()
    if a.cmd == "uri":
        return cmd_uri(a.uri)
    if a.cmd == "daily":
        return cmd_daily()
    if a.cmd == "raise":
        return cmd_raise(a)
    if a.cmd == "test":
        register_protocol()
        write_report(load_open(load_state()))
        toast(f"{ALERT_IDENTITY} alerts test", "Click this notification: the alert window should open.", with_fix=True)
        return 0
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())