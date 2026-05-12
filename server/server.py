"""
UNO Online — Network Server (Phase 4)

Architecture
------------
- One TCP socket per client (2–4 players).
- Message framing: newline-delimited JSON — every message ends with "\\n".
  Both sides may buffer partial lines; the server assembles complete lines
  before parsing.
- One reader thread per client; all game-state mutations happen under a single
  threading.Lock so the authoritative GameState is always consistent.
- Rule 8 uses threading.Timer for the server-side countdown.  The timer
  callback re-acquires the lock before touching any state.

Client → Server action payloads
--------------------------------
{"action": "set_name",   "name": "<display name>"}
{"action": "start_game"}
{"action": "play_card",  "card_index": <int>,
                         "chosen_color": "red"|"yellow"|"green"|"blue",  // wilds
                         "pass_direction": 1|-1,                          // 0 cards
                         "swap_target_id": "<player_id>"}                 // 7 cards
{"action": "draw_card"}
{"action": "pass_turn"}
{"action": "react"}      // Rule 8 reaction

Server → Client message types
------------------------------
{"type": "welcome",             "player_id": "<id>"}
{"type": "lobby_update",        "players": [...], "min_players": 2, "max_players": 4}
{"type": "game_started"}
{"type": "state_update",        "state": { <to_client_dict output> }}
{"type": "action_result",       "success": bool, "error": str|null, "events": [...]}
{"type": "error",               "message": "<reason>"}
{"type": "rule8_resolved",      "losers": ["<player_id>", ...]}
{"type": "game_over",           "winner_id": "<id>", "winner_name": "<name>"}
{"type": "player_disconnected", "player_id": "<id>"}
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Dict, List, Optional

from game.engine import play_card, draw_card, pass_turn, resolve_rule8_penalty
from game.models import GameState, Rule8State, deal_initial_state

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5555
BUFFER_SIZE  = 4096
ENCODING     = "utf-8"


# ---------------------------------------------------------------------------
# ClientConnection — thin wrapper around one accepted socket
# ---------------------------------------------------------------------------

class ClientConnection:
    """
    Wraps a single connected client socket.
    send() is the only method called from multiple threads simultaneously;
    it serialises each message to a JSON line and uses sendall, which is
    atomic enough for our message sizes (< 64 KB each).
    """

    def __init__(self, conn: socket.socket, addr: tuple, player_id: str) -> None:
        self.conn      = conn
        self.addr      = addr
        self.player_id = player_id
        self.name      = f"Player {player_id}"

    def send(self, msg: dict) -> bool:
        """Encode `msg` as a JSON line and send it.  Returns False on failure."""
        try:
            self.conn.sendall((json.dumps(msg) + "\n").encode(ENCODING))
            return True
        except OSError:
            return False

    def close(self) -> None:
        try:
            self.conn.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# UNOServer
# ---------------------------------------------------------------------------

class UNOServer:
    """
    Host-authoritative UNO server.

    Lifecycle
    ---------
    1. call run() — blocks until stop() is called or the game ends.
    2. Clients connect, exchange lobby messages, host sends start_game.
    3. The game proceeds through reader threads; the main thread idles.
    4. When the game ends (phase == "finished"), run() returns.

    Thread safety
    -------------
    self._lock serialises all reads/writes of self._state and self._clients.
    It is also held during broadcasts so that every client sees the same
    state snapshot.  For 2–4 clients with < 64 KB messages this is fine;
    a production server would use a separate outbox queue per client.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        min_players: int = 2,
        max_players: int = 4,
        rule8_timeout: float = 3.0,
    ) -> None:
        if not (2 <= min_players <= max_players <= 4):
            raise ValueError("min_players/max_players must satisfy 2 ≤ min ≤ max ≤ 4.")

        self.host          = host
        self.port          = port
        self.min_players   = min_players
        self.max_players   = max_players
        self.rule8_timeout = rule8_timeout

        self._clients:     Dict[str, ClientConnection] = {}
        self._state:       Optional[GameState]          = None
        self._lock         = threading.RLock()   # reentrant: handlers call helpers that also lock
        self._rule8_timer: Optional[threading.Timer]   = None
        self._running      = False
        self._done_event   = threading.Event()
        self._player_seq   = 0   # monotonically increasing; never recycled

        # Play-again state (active only while phase == "finished")
        self._play_again_invited = False
        self._play_again_votes:  Dict[str, bool] = {}   # pid → True(accept)/False(decline)

    # -----------------------------------------------------------------------
    # Public entry points
    # -----------------------------------------------------------------------

    def run(self) -> None:
        """
        Start the server.  Blocks until the game finishes or stop() is called.
        """
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((self.host, self.port))
        server_sock.listen(self.max_players)
        server_sock.settimeout(1.0)   # non-blocking accept so stop() works
        self._running = True

        print(f"[Server] Listening on {self.host}:{self.port}")

        try:
            while self._running:
                with self._lock:
                    game_active   = self._state is not None
                    at_capacity   = len(self._clients) >= self.max_players

                # Accept new connections only while in lobby and under capacity
                if not game_active and not at_capacity:
                    try:
                        conn, addr = server_sock.accept()
                        self._register_client(conn, addr)
                    except socket.timeout:
                        pass
                else:
                    # Idle: the reader threads are driving the game
                    if self._done_event.wait(timeout=0.1):
                        break
        finally:
            server_sock.close()
            print("[Server] Shut down.")

    def stop(self) -> None:
        """Signal the server to stop and close all connections."""
        self._running = False
        if self._rule8_timer:
            self._rule8_timer.cancel()
        with self._lock:
            for client in list(self._clients.values()):
                client.close()
            self._clients.clear()
        self._done_event.set()

    # -----------------------------------------------------------------------
    # Connection management
    # -----------------------------------------------------------------------

    def _register_client(self, conn: socket.socket, addr: tuple) -> None:
        """Assign a player_id, record the client, and start its reader thread."""
        with self._lock:
            player_id = f"p{self._player_seq}"
            self._player_seq += 1
            client = ClientConnection(conn, addr, player_id)
            self._clients[player_id] = client

        print(f"[Server] {player_id} connected from {addr}")
        client.send({"type": "welcome", "player_id": player_id})
        self._broadcast_lobby_update()

        t = threading.Thread(
            target=self._client_reader, args=(client,), daemon=True, name=f"reader-{player_id}"
        )
        t.start()

    def _client_reader(self, client: ClientConnection) -> None:
        """
        Read newline-delimited JSON messages from one client until it disconnects.
        Partial lines are buffered across recv() calls.
        """
        buf = ""
        while self._running:
            try:
                chunk = client.conn.recv(BUFFER_SIZE)
            except OSError:
                break
            if not chunk:
                break

            buf += chunk.decode(ENCODING, errors="replace")
            while "\n" in buf:
                raw, buf = buf.split("\n", 1)
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    client.send({"type": "error", "message": "Malformed JSON."})
                    continue
                self._dispatch(client.player_id, msg)

        # ── Client disconnected ──────────────────────────────────────────────
        with self._lock:
            self._clients.pop(client.player_id, None)
            if self._state:
                player = self._state.get_player(client.player_id)
                if player:
                    player.is_connected = False
            # Cancel any pending play-again if a participant left
            if self._play_again_invited:
                self._play_again_invited = False
                self._play_again_votes   = {}
                self._broadcast({"type": "play_again_cancelled"})
        self._broadcast({"type": "player_disconnected", "player_id": client.player_id})
        client.close()
        print(f"[Server] {client.player_id} disconnected.")

    # -----------------------------------------------------------------------
    # Action dispatcher
    # -----------------------------------------------------------------------

    def _dispatch(self, player_id: str, msg: dict) -> None:
        """Route one parsed message to the correct handler under the lock."""
        action = msg.get("action")
        with self._lock:
            if   action == "set_name":          self._h_set_name(player_id, msg)
            elif action == "start_game":         self._h_start_game(player_id)
            elif action == "play_card":          self._h_play_card(player_id, msg)
            elif action == "draw_card":          self._h_draw_card(player_id)
            elif action == "pass_turn":          self._h_pass_turn(player_id)
            elif action == "react":              self._h_react(player_id)
            elif action == "play_again_invite":  self._h_play_again_invite(player_id)
            elif action == "play_again_accept":  self._h_play_again_vote(player_id, True)
            elif action == "play_again_decline": self._h_play_again_vote(player_id, False)
            else:
                self._send(player_id, {"type": "error", "message": f"Unknown action '{action}'."})

    # -----------------------------------------------------------------------
    # Sending helpers  (all called while the lock is already held)
    # -----------------------------------------------------------------------

    def _send(self, player_id: str, msg: dict) -> None:
        client = self._clients.get(player_id)
        if client:
            client.send(msg)

    def _broadcast(self, msg: dict) -> None:
        for client in list(self._clients.values()):
            client.send(msg)

    def _broadcast_state(self) -> None:
        """Send each connected client their personalised state snapshot."""
        for pid, client in list(self._clients.items()):
            client.send({
                "type":  "state_update",
                "state": self._state.to_client_dict(pid),
            })

    def _broadcast_lobby_update(self) -> None:
        with self._lock:
            payload = {
                "type": "lobby_update",
                "players": [
                    {"player_id": pid, "name": c.name}
                    for pid, c in self._clients.items()
                ],
                "min_players": self.min_players,
                "max_players": self.max_players,
            }
        self._broadcast(payload)

    def _ok(self, player_id: str, events: List[str]) -> None:
        self._send(player_id, {"type": "action_result", "success": True,
                               "error": None, "events": events})

    def _err(self, player_id: str, message: str) -> None:
        self._send(player_id, {"type": "action_result", "success": False,
                               "error": message, "events": []})

    # -----------------------------------------------------------------------
    # Guard helpers
    # -----------------------------------------------------------------------

    def _require_playing_phase(self, player_id: str) -> bool:
        """Return True only if game is active and in the 'playing' phase."""
        if self._state is None:
            self._err(player_id, "Game has not started yet.")
            return False
        if self._state.phase != "playing":
            self._err(player_id, f"Action not allowed in phase '{self._state.phase}'.")
            return False
        return True

    # -----------------------------------------------------------------------
    # Lobby handlers
    # -----------------------------------------------------------------------

    def _h_set_name(self, player_id: str, msg: dict) -> None:
        if self._state is not None:
            self._err(player_id, "Cannot change name after game has started.")
            return
        name = str(msg.get("name", "")).strip()[:32]
        if not name:
            self._err(player_id, "Name cannot be empty.")
            return
        self._clients[player_id].name = name
        self._ok(player_id, [])
        self._broadcast_lobby_update()

    def _h_start_game(self, player_id: str) -> None:
        if self._state is not None:
            self._err(player_id, "Game already started.")
            return
        if player_id != "p0":
            self._err(player_id, "Only the host (p0) can start the game.")
            return
        if len(self._clients) < self.min_players:
            self._err(player_id, f"Need at least {self.min_players} players to start.")
            return

        names = [self._clients[pid].name for pid in sorted(self._clients)]
        self._state = deal_initial_state(names)

        print(f"[Server] Game started with {len(self._clients)} players.")
        self._broadcast({"type": "game_started"})
        self._broadcast_state()

    # -----------------------------------------------------------------------
    # In-game handlers
    # -----------------------------------------------------------------------

    def _h_play_card(self, player_id: str, msg: dict) -> None:
        if not self._require_playing_phase(player_id):
            return

        card_index = msg.get("card_index")
        if not isinstance(card_index, int):
            self._err(player_id, "'card_index' must be an integer.")
            return

        result = play_card(
            self._state,
            player_id,
            card_index,
            chosen_color   = msg.get("chosen_color"),
            pass_direction = msg.get("pass_direction"),
            swap_target_id = msg.get("swap_target_id"),
        )
        self._send(player_id, {"type": "action_result", **result.to_dict()})

        if not result.success:
            return

        if "win" in result.events:
            self._on_game_over()
        elif "rule8_triggered" in result.events:
            self._start_rule8()
        else:
            self._broadcast_state()

    def _h_draw_card(self, player_id: str) -> None:
        if not self._require_playing_phase(player_id):
            return

        result = draw_card(self._state, player_id)
        self._send(player_id, {"type": "action_result", **result.to_dict()})
        if result.success:
            self._broadcast_state()

    def _h_pass_turn(self, player_id: str) -> None:
        if not self._require_playing_phase(player_id):
            return

        result = pass_turn(self._state, player_id)
        self._send(player_id, {"type": "action_result", **result.to_dict()})
        if result.success:
            self._broadcast_state()

    # -----------------------------------------------------------------------
    # Rule 8 — Reaction timer
    # -----------------------------------------------------------------------

    def _start_rule8(self) -> None:
        """
        Called immediately after an 8 is played (not as a winning card).
        Transitions to 'rule8_reaction' phase and starts the server-side timer.
        The turn has already advanced (the 8's normal effect ran in the engine),
        so the game will resume from the next player's turn after resolution.
        """
        deadline   = time.time() + self.rule8_timeout
        player_ids = [p.player_id for p in self._state.players]

        self._state.rule8_state = Rule8State(
            deadline        = deadline,
            reacted         = [],
            pending_players = list(player_ids),
        )
        self._state.phase = "rule8_reaction"

        print(f"[Server] Rule 8: {self.rule8_timeout}s reaction window open.")
        self._broadcast_state()

        # The timer callback must re-acquire the lock — no deadlock risk since
        # the callback runs in its own thread after this handler releases the lock.
        self._rule8_timer = threading.Timer(self.rule8_timeout, self._rule8_on_timeout)
        self._rule8_timer.daemon = True
        self._rule8_timer.start()

    def _h_react(self, player_id: str) -> None:
        """Handle a Rule 8 reaction payload from a client."""
        if self._state is None or self._state.phase != "rule8_reaction":
            self._err(player_id, "No active Rule 8 reaction window.")
            return

        r8 = self._state.rule8_state
        if player_id not in r8.pending_players:
            self._err(player_id, "Already reacted (or not a valid participant).")
            return

        r8.pending_players.remove(player_id)
        r8.reacted.append(player_id)
        self._ok(player_id, ["reacted"])

        print(f"[Server] Rule 8: {player_id} reacted "
              f"({len(r8.reacted)}/{len(r8.reacted) + len(r8.pending_players)} total).")

        # If every player reacted, cancel the timer and resolve immediately
        if not r8.pending_players:
            if self._rule8_timer:
                self._rule8_timer.cancel()
                self._rule8_timer = None
            self._resolve_rule8()

    def _rule8_on_timeout(self) -> None:
        """
        Invoked by threading.Timer after rule8_timeout seconds.
        Acquires the lock, checks phase is still rule8_reaction, then resolves.
        """
        with self._lock:
            if self._state and self._state.phase == "rule8_reaction":
                print("[Server] Rule 8: timer expired.")
                self._resolve_rule8()

    def _resolve_rule8(self) -> None:
        """
        Determine losers, apply the 2-card penalty, and resume play.
        Must be called while self._lock is held.

        Loser determination:
          - If any player did NOT react before the deadline: all non-reactors draw 2.
          - If ALL players reacted in time: the last to react (slowest) draws 2.
        """
        r8 = self._state.rule8_state
        if r8.pending_players:
            loser_ids = list(r8.pending_players)   # timed-out players
        else:
            loser_ids = [r8.reacted[-1]]            # slowest reactor

        resolve_rule8_penalty(self._state, loser_ids)   # draws 2, clears r8 state, → "playing"
        self._rule8_timer = None

        print(f"[Server] Rule 8 resolved. Losers: {loser_ids}")
        self._broadcast({"type": "rule8_resolved", "losers": loser_ids})
        self._broadcast_state()

    # -----------------------------------------------------------------------
    # Game over
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Play-again handlers  (all called while self._lock is held)
    # -----------------------------------------------------------------------

    def _h_play_again_invite(self, player_id: str) -> None:
        """Host toggles a play-again invite to all connected non-host players."""
        if self._state is None or self._state.phase != "finished":
            self._err(player_id, "Can only invite when the game is finished.")
            return
        if player_id != "p0":
            self._err(player_id, "Only the host can send a play-again invite.")
            return

        non_host_ids = [pid for pid in self._clients if pid != "p0"]
        if not non_host_ids:
            self._err(player_id, "No other players connected.")
            return

        if self._play_again_invited:
            # Toggle off — cancel the pending invite
            self._play_again_invited = False
            self._play_again_votes   = {}
            self._broadcast({"type": "play_again_cancelled"})
            self._ok(player_id, [])
            print("[Server] Host cancelled play-again invite.")
            return

        self._play_again_invited = True
        self._play_again_votes   = {}
        host_name = self._clients["p0"].name

        for pid in non_host_ids:
            self._send(pid, {"type": "play_again_invite", "host_name": host_name})

        # Give host the initial status (all non-hosts still pending)
        self._send(player_id, {
            "type":     "play_again_status",
            "accepted": [],
            "pending":  non_host_ids,
        })
        self._ok(player_id, [])
        print("[Server] Host sent play-again invite.")

    def _h_play_again_vote(self, player_id: str, accept: bool) -> None:
        """Non-host player accepts or declines a play-again invite."""
        if self._state is None or self._state.phase != "finished":
            self._err(player_id, "Not in finished phase.")
            return
        if not self._play_again_invited:
            self._err(player_id, "No play-again invite is active.")
            return
        if player_id == "p0":
            self._err(player_id, "Host cannot vote on their own invite.")
            return

        self._play_again_votes[player_id] = accept
        self._ok(player_id, [])

        non_host_ids = [pid for pid in self._clients if pid != "p0"]

        if not accept:
            self._play_again_invited = False
            self._play_again_votes   = {}
            self._broadcast({"type": "play_again_cancelled"})
            print(f"[Server] {player_id} declined play-again.")
            return

        accepted = [pid for pid, v in self._play_again_votes.items() if v]
        pending  = [pid for pid in non_host_ids if pid not in self._play_again_votes]
        status   = {"type": "play_again_status", "accepted": accepted, "pending": pending}
        self._broadcast(status)

        if not pending:   # every non-host accepted
            self._restart_game()

    def _restart_game(self) -> None:
        """Reset game state and start a new round with the same players."""
        names = [self._clients[pid].name for pid in sorted(self._clients)]
        self._state = deal_initial_state(names)
        self._play_again_invited = False
        self._play_again_votes   = {}
        print(f"[Server] Game restarted with {len(self._clients)} players.")
        self._broadcast({"type": "game_started"})
        self._broadcast_state()

    # -----------------------------------------------------------------------
    # Game over
    # -----------------------------------------------------------------------

    def _on_game_over(self) -> None:
        winner = self._state.get_player(self._state.winner_id)
        payload = {
            "type":        "game_over",
            "winner_id":   self._state.winner_id,
            "winner_name": winner.name if winner else "Unknown",
        }
        self._broadcast(payload)
        self._broadcast_state()
        print(f"[Server] Game over — winner: {self._state.winner_id} ({winner.name if winner else '?'})")
        # Keep server alive so host can invite everyone to play again.
        # Server stops when host calls stop() (closes window) or all leave.
        self._play_again_invited = False
        self._play_again_votes   = {}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Custom UNO Online Server")
    parser.add_argument("--host",        default=DEFAULT_HOST)
    parser.add_argument("--port",        default=DEFAULT_PORT, type=int)
    parser.add_argument("--min-players", default=2, type=int, dest="min_players")
    parser.add_argument("--max-players", default=4, type=int, dest="max_players")
    parser.add_argument("--rule8-timeout", default=3.0, type=float, dest="rule8_timeout")
    args = parser.parse_args()

    server = UNOServer(
        host          = args.host,
        port          = args.port,
        min_players   = args.min_players,
        max_players   = args.max_players,
        rule8_timeout = args.rule8_timeout,
    )
    try:
        server.run()
    except KeyboardInterrupt:
        print("\n[Server] Interrupted.")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
