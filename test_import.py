#!/usr/bin/env python3
"""Test script to verify desktop-alerts imports correctly."""
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent / "desktop_alerts"
sys.path.insert(0, str(PACKAGE_DIR))

# Test imports
try:
    import desktop_alerts
    print("✓ desktop_alerts module imports successfully")
    print(f"  ALERT_IDENTITY: {desktop_alerts.ALERT_IDENTITY}")
    print(f"  APP_ID: {desktop_alerts.APP_ID}")
    print(f"  MUTEX: {desktop_alerts.MUTEX}")
    print(f"  DATA_DIR: {desktop_alerts.DATA_DIR}")
    print(f"  PAGE_URL: {desktop_alerts.PAGE_URL or '(empty)'}")
    print(f"  POLL_S: {desktop_alerts.POLL_S}")
except Exception as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

# Test config via env
import os
os.environ["DESKTOP_ALERTS_IDENTITY"] = "TestLab"
os.environ["DESKTOP_ALERTS_PORTAL_URL"] = "https://test.example.com/health"

import importlib
importlib.reload(desktop_alerts)
print(f"\nAfter env override:")
print(f"  ALERT_IDENTITY: {desktop_alerts.ALERT_IDENTITY}")
print(f"  APP_ID: {desktop_alerts.APP_ID}")
print(f"  MUTEX: {desktop_alerts.MUTEX}")
print(f"  PAGE_URL: {desktop_alerts.PAGE_URL}")

print("\n✓ All tests passed")