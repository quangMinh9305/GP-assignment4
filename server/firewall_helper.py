"""
Elevated firewall helper for UNO Online.

Lifecycle
---------
1. Spawned by the main game process (with admin rights, via UAC if needed).
2. Adds inbound firewall rules for TCP 5555 and UDP 5556.
3. Blocks on WaitForSingleObject until the game process (argv[1]) exits —
   this covers normal exit, unhandled exceptions, and Task Manager kills.
4. Removes both rules and exits.

Usage
-----
    python firewall_helper.py <game_pid>
"""
from __future__ import annotations

import ctypes
import subprocess
import sys

RULE_TCP = "UNO-Online-TCP-5555"
RULE_UDP = "UNO-Online-UDP-5556"
TCP_PORT = 5555
UDP_PORT = 5556

_NETSH = ["netsh", "advfirewall", "firewall"]


def _rule_exists(name: str) -> bool:
    try:
        r = subprocess.run(
            _NETSH + ["show", "rule", f"name={name}"],
            capture_output=True, text=True, timeout=5,
        )
        return "No rules match" not in r.stdout
    except Exception:
        return False


def _add_rules() -> None:
    for name, proto, port in (
        (RULE_TCP, "TCP", TCP_PORT),
        (RULE_UDP, "UDP", UDP_PORT),
    ):
        if not _rule_exists(name):
            subprocess.run(
                _NETSH + [
                    "add", "rule", f"name={name}",
                    f"protocol={proto}", "dir=in", f"localport={port}",
                    "action=allow", "enable=yes", "profile=private,domain",
                ],
                capture_output=True, timeout=10,
            )


def _remove_rules() -> None:
    for name in (RULE_TCP, RULE_UDP):
        subprocess.run(
            _NETSH + ["delete", "rule", f"name={name}"],
            capture_output=True, timeout=10,
        )


def _wait_for_pid(pid: int) -> None:
    """Block until the process with the given PID exits (any reason)."""
    SYNCHRONIZE = 0x00100000
    INFINITE    = 0xFFFFFFFF
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(SYNCHRONIZE, False, pid)
    if handle:
        k32.WaitForSingleObject(handle, INFINITE)
        k32.CloseHandle(handle)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: firewall_helper.py <game_pid>", file=sys.stderr)
        sys.exit(1)

    try:
        pid = int(sys.argv[1])
    except ValueError:
        sys.exit(1)

    _add_rules()
    _wait_for_pid(pid)
    _remove_rules()


if __name__ == "__main__":
    main()
