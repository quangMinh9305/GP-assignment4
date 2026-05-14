"""
Windows Firewall auto-configuration for UNO Online.

Strategy
--------
Spawns firewall_helper.py as a separate process that:
  1. Adds inbound rules for TCP 5555 and UDP 5556.
  2. Watches the game PID via WaitForSingleObject.
  3. Removes both rules automatically when the game exits
     (normal exit, crash, or Task Manager kill — all covered).

If the game is already running as admin the helper is spawned directly
(inheriting admin rights).  Otherwise ShellExecuteW with "runas" triggers
a UAC prompt so the helper gets the elevation it needs.

Returns
-------
'ok'             — helper spawned, rules being added (admin path)
'elevated'       — UAC prompt shown; helper will add rules after user accepts
'failed'         — elevation denied or unexpected error
'skipped'        — not on Windows
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys

_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firewall_helper.py")


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def ensure_firewall_rules() -> str:
    """Spawn the firewall helper tied to the current process PID."""
    if sys.platform != "win32":
        return "skipped"

    pid = os.getpid()

    if _is_admin():
        try:
            subprocess.Popen(
                [sys.executable, _HELPER, str(pid)],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return "ok"
        except Exception:
            return "failed"

    # Not admin — request elevation via UAC
    try:
        args = f'"{_HELPER}" {pid}'
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, args, None, 0,  # SW_HIDE
        )
        return "elevated" if ret > 32 else "failed"
    except Exception:
        return "failed"
