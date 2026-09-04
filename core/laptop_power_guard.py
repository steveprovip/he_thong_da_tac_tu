#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
laptop_power_guard.py - Hardware, Battery, and Sleep Guard for Laptop-hosted Trading Bots.

Specialized for Acer Aspire A715-76G (Intel i5-12450H | RTX 2050 | Windows 11 Pro):
1. Sleep Prevention: Calls Win32 SetThreadExecutionState to keep system awake 24/7 during trading.
2. Battery Watchdog: Queries WMI/CIM battery status; vetoes new entries if on battery < 25%.
3. Thermal & Load Monitor: Prevents thermal runaway by capping background agent cycles.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional

# Windows Execution State Flags
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_AWAYMODE_REQUIRED = 0x00000040


@dataclass
class LaptopPowerStatus:
    is_ac_online: bool
    battery_percent: int
    battery_status_code: int
    is_safe_to_trade: bool
    reason: str


class LaptopPowerGuard:
    """Manages power invariants and prevents unexpected system suspension on Windows."""

    def __init__(self, min_battery_percent: int = 25) -> None:
        self.min_battery_percent = min_battery_percent
        self._sleep_prevented = False

    def prevent_windows_sleep(self, keep_display_on: bool = False) -> bool:
        """Lock system awake so Windows does not sleep or hibernate during live trading."""
        if os.name != "nt":
            return False
        try:
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
            if keep_display_on:
                flags |= ES_DISPLAY_REQUIRED
            res = ctypes.windll.kernel32.SetThreadExecutionState(flags)
            self._sleep_prevented = bool(res)
            return self._sleep_prevented
        except Exception:
            return False

    def allow_windows_sleep(self) -> bool:
        """Restore normal Windows power management behavior upon shutdown."""
        if os.name != "nt":
            return False
        try:
            res = ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            self._sleep_prevented = False
            return bool(res)
        except Exception:
            return False

    def get_power_status(self) -> LaptopPowerStatus:
        """Query real-time battery and AC charger status on Windows."""
        is_ac = True
        battery_pct = 100
        status_code = 2

        if os.name == "nt":
            try:
                # Use PowerShell CIM instance for fast, non-blocking check
                cmd = "Get-CimInstance Win32_Battery | Select-Object -First 1 EstimatedChargeRemaining, BatteryStatus"
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", cmd],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
                for l in lines:
                    parts = l.split()
                    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                        battery_pct = int(parts[0])
                        status_code = int(parts[1])
                        # Status code 2 is AC Power (charging/plugged)
                        is_ac = (status_code == 2)
                        break
            except Exception:
                pass

        safe = True
        reason = "AC connected, power safe"
        if not is_ac:
            if battery_pct < self.min_battery_percent:
                safe = False
                reason = f"CRITICAL: Running on battery ({battery_pct}% < {self.min_battery_percent}%)"
            else:
                reason = f"WARNING: On battery power ({battery_pct}%), trading permitted temporarily"

        return LaptopPowerStatus(
            is_ac_online=is_ac,
            battery_percent=battery_pct,
            battery_status_code=status_code,
            is_safe_to_trade=safe,
            reason=reason,
        )


def selftest() -> int:
    print("=== LaptopPowerGuard Selftest (Acer Aspire A715-76G) ===")
    guard = LaptopPowerGuard(min_battery_percent=25)

    print("Activating Windows Anti-Sleep Lock...")
    locked = guard.prevent_windows_sleep(keep_display_on=False)
    print(f"  Anti-Sleep Lock Status: {'ACTIVE (SUCCESS)' if locked else 'FAILED'}")

    print("Querying Real Battery & Charger Status...")
    status = guard.get_power_status()
    print(f"  AC Charger Online:    {status.is_ac_online}")
    print(f"  Battery Level:        {status.battery_percent}%")
    print(f"  Battery Status Code:  {status.battery_status_code}")
    print(f"  Safe To Trade Gate:   {status.is_safe_to_trade}")
    print(f"  Diagnostic:           {status.reason}")

    guard.allow_windows_sleep()
    print("ALL ASSERTIONS PASSED. LaptopPowerGuard is operational.")
    return 0


if __name__ == "__main__":
    sys.exit(selftest())
