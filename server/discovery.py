"""
LAN room-discovery helper for Custom UNO Online.

Protocol (UDP, port 5556)
-------------------------
Client → broadcast: "UNO_DISCOVER:<CODE>"
Host   → unicast:   "UNO_HOST:<CODE>:<PORT>"

Usage
-----
Host side:
    srv = DiscoveryServer("ABCD", game_port=5555)
    srv.start()          # runs in a daemon thread
    ...
    srv.stop()

Join side:
    result = discover_room("ABCD", timeout=3.0)
    if result:
        host_ip, port = result
        # connect TCP socket to (host_ip, port)
    else:
        print("Room not found")
"""
from __future__ import annotations

import socket
import threading
from typing import Optional, Tuple

DISCOVERY_PORT = 5556


class DiscoveryServer:
    """Listens for discovery broadcasts and replies with the game port."""

    def __init__(self, room_code: str, game_port: int) -> None:
        self.room_code = room_code.upper()
        self.game_port = game_port
        self._running   = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="discovery-server")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", DISCOVERY_PORT))
            sock.settimeout(1.0)
            response = f"UNO_HOST:{self.room_code}:{self.game_port}".encode()
            query    = f"UNO_DISCOVER:{self.room_code}".encode()
            while self._running:
                try:
                    data, addr = sock.recvfrom(64)
                    if data == query:
                        sock.sendto(response, addr)
                except socket.timeout:
                    pass
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass


def discover_room(
    room_code: str,
    timeout: float = 3.0,
) -> Optional[Tuple[str, int]]:
    """
    Broadcast a discovery request and return (host_ip, port) or None.
    Blocks for up to `timeout` seconds — call from a background thread.
    """
    code = room_code.upper()
    query = f"UNO_DISCOVER:{code}".encode()
    prefix = f"UNO_HOST:{code}:".encode()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        sock.sendto(query, ("255.255.255.255", DISCOVERY_PORT))
        data, addr = sock.recvfrom(64)
        if data.startswith(prefix):
            port = int(data[len(prefix):])
            return (addr[0], port)
    except Exception:
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return None


def get_lan_ip() -> str:
    """Return the best-guess LAN IPv4 address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
