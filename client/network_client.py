"""
NetworkClient — thin TCP socket wrapper for the UNO client.

Runs a background reader thread that assembles newline-delimited JSON
messages and queues them for the main (Pygame) thread to consume via
poll().  send() is safe to call from the main thread at any time.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
from typing import Any, Dict, List


class NetworkClient:
    def __init__(self, host: str, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect((host, port))
        self._incoming: queue.Queue = queue.Queue()
        self._buf      = ""
        self._running  = True
        self._connected = True

        t = threading.Thread(target=self._reader, daemon=True, name="net-reader")
        t.start()

    # ------------------------------------------------------------------
    # Public API (called from the main thread)
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    def send(self, **kwargs: Any) -> None:
        """Serialise kwargs as a JSON line and send it to the server."""
        if not self._connected:
            return
        try:
            self._sock.sendall((json.dumps(kwargs) + "\n").encode("utf-8"))
        except OSError:
            self._connected = False

    def poll(self) -> List[Dict]:
        """Drain and return all messages received since the last call."""
        msgs: List[Dict] = []
        while True:
            try:
                msgs.append(self._incoming.get_nowait())
            except queue.Empty:
                break
        return msgs

    def close(self) -> None:
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Background reader
    # ------------------------------------------------------------------

    def _reader(self) -> None:
        while self._running:
            try:
                chunk = self._sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break

            self._buf += chunk.decode("utf-8", errors="replace")
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    self._incoming.put(json.loads(line))
                except json.JSONDecodeError:
                    pass

        self._connected = False
        self._incoming.put({"type": "disconnected"})
