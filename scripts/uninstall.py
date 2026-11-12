#!/usr/bin/env python3
"""Unregister desktop-alerts package."""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE_DIR = HERE / "desktop_alerts"


def run(cmd, check=True, **kwargs):
    print(f"> {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, **kwargs)


def unregister_protocol(python: str):
    """Unregister the desktop-alerts: URI protocol handler."""
    print("Unregistering URI protocol handler...")
    try:
        import winreg
        for key_path in [
            r"Software\Classes\desktop-alerts\shell\open\command",
            r"Software\Classes\desktop-alerts\shell\open",
            r"Software\Classes\desktop-alerts\shell",
            r"Software\Classes\desktop-alerts"
        ]:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
                print(f"  Deleted {key_path}")
            except FileNotFoundError:
                pass
            except OSError as e:
                print(f"  Could not delete {key_path}: {e}")
        print("Protocol handler unregistered")
    except Exception as e:
        print(f"Warning: could not unregister protocol: {e}")


def remove_startup_shortcut(identity: str):
    """Remove the startup shortcut."""
    startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    shortcut = startup / f"{identity} Alerts.lnk"
    if shortcut.exists():
        shortcut.unlink()
        print(f"Removed startup shortcut: {shortcut}")
    else:
        print(f"Startup shortcut not found: {shortcut}")


def stop_running_app():
    """Stop the running tray app if any."""
    print("Stopping running tray app...")
    try:
        run(["taskkill", "/F", "/IM", "pythonw.exe", "/FI", "WINDOWTITLE eq desktop-alerts*"])
    except subprocess.CalledProcessError:
        pass
    # Also try by mutex
    try:
        import ctypes
        MUTEX = "Local\\DesktopAlertsDesktopAlerts"
        h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, MUTEX)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            print("Found running instance (mutex exists), but taskkill may not have caught it")
    except Exception:
        pass


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Uninstall desktop-alerts")
    ap.add_argument("--identity", default="DesktopAlerts", help="App name (default: DesktopAlerts)")
    ap.add_argument("--python", default=sys.executable, help="Python executable")
    ap.add_argument("--keep-data", action="store_true", help="Keep data directory")
    args = ap.parse_args()

    identity = args.identity
    python = args.python

    print(f"Uninstalling {identity} alerts...")

    stop_running_app()
    unregister_protocol(python)
    remove_startup_shortcut(identity)

    # Remove .env
    env_file = HERE / ".env"
    if env_file.exists():
        env_file.unlink()
        print(f"Removed config: {env_file}")

    if not args.keep_data:
        data_dir = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Public")) / "DesktopAlerts"
        if data_dir.exists():
            import shutil
            shutil.rmtree(data_dir)
            print(f"Removed data directory: {data_dir}")
    else:
        print("Keeping data directory (use --keep-data to preserve)")

    print("\n✓ Uninstallation complete!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n✗ Uninstallation failed: {e}")
        sys.exit(1)