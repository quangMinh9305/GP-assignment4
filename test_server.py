"""
Integration tests for Phase 4: NetworkServer.
Spins up a real UNOServer in a background thread and connects genuine
TCP sockets to exercise the full JSON message protocol.

Run with: python test_server.py
"""
import json
import socket
import threading
import time
from typing import Any, Dict, List, Optional

from game.models import Card, TYPE_NUMBER, TYPE_WILD
from server.server import UNOServer

PASS_LABEL = "\033[92mPASS\033[0m"
FAIL_LABEL = "\033[91mFAIL\033[0m"
TEST_PORT_BASE = 19100   # incremented per test to avoid port reuse collisions


def check(label: str, condition: bool) -> None:
    print(f"  [{PASS_LABEL if condition else FAIL_LABEL}] {label}")
    if not condition:
        raise AssertionError(label)


# ---------------------------------------------------------------------------
# TestClient — thin helper that wraps a connected socket
# ---------------------------------------------------------------------------

class TestClient:
    def __init__(self, host: str, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect((host, port))
        self._sock.settimeout(3.0)
        self._buf = ""

    def send(self, **kwargs: Any) -> None:
        self._sock.sendall((json.dumps(kwargs) + "\n").encode())

    def read_one(self, timeout: float = 2.0) -> Optional[Dict]:
        """Read the next complete JSON line, waiting up to `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while True:
            if "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.strip()
                if line:
                    return json.loads(line)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(4096).decode()
                if not chunk:
                    return None
                self._buf += chunk
            except socket.timeout:
                return None

    def drain(self, timeout: float = 0.3) -> List[Dict]:
        """Read all messages available within `timeout` seconds."""
        msgs = []
        while True:
            m = self.read_one(timeout=timeout)
            if m is None:
                break
            msgs.append(m)
        return msgs

    def find(self, msg_type: str, timeout: float = 2.0) -> Optional[Dict]:
        """Read messages until one with the given type is found."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            m = self.read_one(timeout=deadline - time.monotonic())
            if m and m.get("type") == msg_type:
                return m
        return None

    def close(self) -> None:
        self._sock.close()


# ---------------------------------------------------------------------------
# ServerFixture — starts a server, connects N clients, cleans up
# ---------------------------------------------------------------------------

class ServerFixture:
    _port_counter = TEST_PORT_BASE

    def __init__(self, n_clients: int = 2, rule8_timeout: float = 3.0) -> None:
        ServerFixture._port_counter += 1
        self.port = ServerFixture._port_counter
        self.server = UNOServer(
            host="127.0.0.1",
            port=self.port,
            min_players=2,
            max_players=4,
            rule8_timeout=rule8_timeout,
        )
        self._thread = threading.Thread(target=self.server.run, daemon=True)
        self._thread.start()
        time.sleep(0.05)   # let the server socket bind
        self.clients: List[TestClient] = [
            TestClient("127.0.0.1", self.port) for _ in range(n_clients)
        ]
        # Drain initial welcome / lobby_update messages
        for c in self.clients:
            c.drain(timeout=0.3)

    def teardown(self) -> None:
        self.server.stop()
        for c in self.clients:
            c.close()

    @property
    def p0(self) -> TestClient:
        return self.clients[0]

    @property
    def p1(self) -> TestClient:
        return self.clients[1]

    def start_game(self) -> None:
        """Convenience: host starts the game and all clients drain initial state."""
        self.p0.send(action="start_game")
        for c in self.clients:
            c.drain(timeout=0.5)

    def inject_hand(self, player_id: str, cards: List[Card]) -> None:
        """Directly replace a player's hand (must hold no lock — test thread only)."""
        with self.server._lock:
            p = self.server._state.get_player(player_id)
            p.hand = list(cards)

    def inject_top_and_color(self, card: Card, color: str) -> None:
        """Replace the top discard card and active color."""
        with self.server._lock:
            self.server._state.discard_pile[-1] = card
            self.server._state.active_color = color

    def set_current_player(self, player_id: str) -> None:
        """Force whose turn it is."""
        with self.server._lock:
            idx = next(i for i, p in enumerate(self.server._state.players)
                       if p.player_id == player_id)
            self.server._state.current_player_index = idx


# ---------------------------------------------------------------------------
# Test: lobby — welcome and player count enforcement
# ---------------------------------------------------------------------------

def test_lobby_welcome() -> None:
    print("Lobby: welcome and name setting")
    fix = ServerFixture(n_clients=2)
    try:
        # Welcome messages already drained; reconnect a third socket to verify welcome
        c3 = TestClient("127.0.0.1", fix.port)
        welcome = c3.find("welcome")
        check("Third client gets welcome",  welcome is not None)
        check("Welcome contains player_id", "player_id" in (welcome or {}))
        c3.close()

        # Non-host cannot start
        fix.p1.send(action="start_game")
        r = fix.p1.find("action_result")
        check("Non-host start rejected", r is not None and not r["success"])

        # Start with 1 player should fail (min=2)
        fix2 = ServerFixture(n_clients=1, rule8_timeout=3.0)
        try:
            fix2.p0.send(action="start_game")
            r2 = fix2.p0.find("action_result")
            check("Start with 1 player rejected", r2 is not None and not r2["success"])
        finally:
            fix2.teardown()

        # Name can be set before game
        fix.p0.send(action="set_name", name="Alice")
        r3 = fix.p0.find("action_result")
        check("set_name accepted", r3 is not None and r3["success"])

    finally:
        fix.teardown()


# ---------------------------------------------------------------------------
# Test: game starts and state is broadcast
# ---------------------------------------------------------------------------

def test_game_start() -> None:
    print("Game start: state broadcast to all clients")
    fix = ServerFixture(n_clients=2)
    try:
        fix.p0.send(action="start_game")

        # Both clients should receive game_started + state_update
        m0 = fix.p0.find("game_started")
        m1 = fix.p1.find("game_started")
        check("p0 receives game_started", m0 is not None)
        check("p1 receives game_started", m1 is not None)

        s0 = fix.p0.find("state_update")
        s1 = fix.p1.find("state_update")
        check("p0 receives state_update", s0 is not None)
        check("p1 receives state_update", s1 is not None)

        # p0 sees their own hand; p1 sees p0's hand as empty
        state_for_p0 = s0["state"]
        check("p0's own hand is visible",      len(state_for_p0["players"][0]["hand"]) == 7)
        check("p0 sees p1's hand as hidden",   len(state_for_p0["players"][1]["hand"]) == 0)
        check("p0 sees p1's hand_count=7",     state_for_p0["players"][1]["hand_count"] == 7)
        check("Draw pile count > 0",           state_for_p0["draw_pile_count"] > 0)
        check("Phase is playing",              state_for_p0["phase"] == "playing")

    finally:
        fix.teardown()


# ---------------------------------------------------------------------------
# Test: play_card — happy path and validation
# ---------------------------------------------------------------------------

def test_play_card() -> None:
    print("play_card: happy path and rejection")
    fix = ServerFixture(n_clients=2)
    try:
        fix.start_game()

        # Inject a known playable card into p0's hand and set top card
        red5  = Card("red",  TYPE_NUMBER, 5)
        red9  = Card("red",  TYPE_NUMBER, 9)
        blue3 = Card("blue", TYPE_NUMBER, 3)
        fix.inject_top_and_color(red5, "red")
        fix.set_current_player("p0")
        fix.inject_hand("p0", [red9, blue3])   # index 0 = red 9 (playable)

        # Valid play
        fix.p0.send(action="play_card", card_index=0)
        r = fix.p0.find("action_result")
        check("Valid play accepted", r is not None and r["success"])
        s = fix.p0.find("state_update")
        check("State broadcast after play", s is not None)
        check("Discard top is now red 9",
              s["state"]["discard_pile"][-1] == {"color": "red", "card_type": "number", "value": 9})
        check("Turn advanced to p1",
              s["state"]["players"][s["state"]["current_player_index"]]["player_id"] == "p1")

        # Wrong player's turn (p0 already played)
        fix.p0.send(action="play_card", card_index=0)
        r2 = fix.p0.find("action_result")
        check("Play out-of-turn rejected", r2 is not None and not r2["success"])

        # p1 tries to play a card that doesn't match
        fix.inject_hand("p1", [Card("blue", TYPE_NUMBER, 4)])   # 4 ≠ 9, blue ≠ red
        fix.p1.send(action="play_card", card_index=0)
        r3 = fix.p1.find("action_result")
        check("Non-matching card rejected", r3 is not None and not r3["success"])

    finally:
        fix.teardown()


# ---------------------------------------------------------------------------
# Test: draw_card and pass_turn
# ---------------------------------------------------------------------------

def test_draw_and_pass() -> None:
    print("draw_card / pass_turn")
    fix = ServerFixture(n_clients=2)
    try:
        fix.start_game()

        red5 = Card("red", TYPE_NUMBER, 5)
        fix.inject_top_and_color(red5, "red")
        fix.set_current_player("p0")
        # Give p0 only an unplayable card
        fix.inject_hand("p0", [Card("blue", TYPE_NUMBER, 3)])

        # Draw an unplayable card → turn should auto-advance
        with fix.server._lock:
            fix.server._state.draw_pile = [Card("green", TYPE_NUMBER, 4)]

        fix.p0.send(action="draw_card")
        r = fix.p0.find("action_result")
        check("Draw accepted", r is not None and r["success"])
        check("draw_pass event (unplayable drawn)", "draw_pass" in (r or {}).get("events", []))
        s = fix.p0.find("state_update")
        check("Turn advanced to p1 after draw_pass",
              s["state"]["players"][s["state"]["current_player_index"]]["player_id"] == "p1")

        # Draw a playable card → turn stays, player may pass
        fix.set_current_player("p1")
        fix.inject_hand("p1", [Card("blue", TYPE_NUMBER, 3)])
        with fix.server._lock:
            fix.server._state.draw_pile = [Card("red", TYPE_NUMBER, 9)]  # playable (red)
            fix.server._state.discard_pile[-1] = red5
            fix.server._state.active_color = "red"

        fix.p1.send(action="draw_card")
        r2 = fix.p1.find("action_result")
        check("Drew playable: accepted", r2 is not None and r2["success"])
        check("drew_playable event", "drew_playable" in (r2 or {}).get("events", []))

        # Pass turn
        fix.p1.send(action="pass_turn")
        r3 = fix.p1.find("action_result")
        check("pass_turn accepted", r3 is not None and r3["success"])
        s2 = fix.p1.find("state_update")
        check("Turn back to p0 after pass",
              s2["state"]["players"][s2["state"]["current_player_index"]]["player_id"] == "p0")

    finally:
        fix.teardown()


# ---------------------------------------------------------------------------
# Test: win condition
# ---------------------------------------------------------------------------

def test_win() -> None:
    print("Win condition: game_over broadcast")
    fix = ServerFixture(n_clients=2)
    try:
        fix.start_game()

        fix.inject_top_and_color(Card("red", TYPE_NUMBER, 5), "red")
        fix.set_current_player("p0")
        fix.inject_hand("p0", [Card("red", TYPE_NUMBER, 9)])   # last card, legal final

        fix.p0.send(action="play_card", card_index=0)
        r = fix.p0.find("action_result")
        check("Win play accepted", r is not None and r["success"])
        check("win event present", "win" in (r or {}).get("events", []))

        go0 = fix.p0.find("game_over")
        go1 = fix.p1.find("game_over")
        check("p0 receives game_over",   go0 is not None)
        check("p1 receives game_over",   go1 is not None)
        check("winner_id is p0",         go0["winner_id"] == "p0")

    finally:
        fix.teardown()


# ---------------------------------------------------------------------------
# Test: Rule 8 — reaction window
# ---------------------------------------------------------------------------

def test_rule8_all_react() -> None:
    print("Rule 8: all players react in time (slowest draws 2)")
    fix = ServerFixture(n_clients=2, rule8_timeout=0.8)
    try:
        fix.start_game()

        # Inject an 8 into p0's hand (not last card so no win)
        fix.inject_top_and_color(Card("red", TYPE_NUMBER, 5), "red")
        fix.set_current_player("p0")
        fix.inject_hand("p0", [Card("red", TYPE_NUMBER, 8), Card("red", TYPE_NUMBER, 1)])

        # Play the 8
        fix.p0.send(action="play_card", card_index=0)
        r = fix.p0.find("action_result")
        check("8 played: success",              r is not None and r["success"])
        check("rule8_triggered event emitted",  "rule8_triggered" in (r or {}).get("events", []))

        # Both clients should get a state_update with phase=rule8_reaction
        s0 = fix.p0.find("state_update")
        check("p0 sees rule8_reaction phase",   s0 is not None and s0["state"]["phase"] == "rule8_reaction")
        fix.p1.drain(timeout=0.3)   # consume p1's state_update

        # p0 reacts first (fast)
        fix.p0.send(action="react")
        react_r0 = fix.p0.find("action_result")
        check("p0 react accepted",  react_r0 is not None and react_r0["success"])

        # p1 reacts second (slow — will be the loser)
        time.sleep(0.1)
        fix.p1.send(action="react")
        react_r1 = fix.p1.find("action_result")
        check("p1 react accepted",  react_r1 is not None and react_r1["success"])

        # All reacted → resolved immediately (no wait for timer)
        resolved = fix.p0.find("rule8_resolved", timeout=1.0)
        check("rule8_resolved broadcast",   resolved is not None)
        check("p1 is the loser (slowest)",  resolved["losers"] == ["p1"])

        # State should return to playing
        s_final = fix.p0.find("state_update", timeout=1.0)
        check("Phase back to playing",      s_final is not None and s_final["state"]["phase"] == "playing")

        # p1 should have drawn 2 cards (hand_count increased by 2)
        with fix.server._lock:
            p1_count = fix.server._state.get_player("p1").hand_count()
        check("p1 drew 2 cards (hand_count >= 2)", p1_count >= 2)

    finally:
        fix.teardown()


def test_rule8_timeout() -> None:
    print("Rule 8: p1 times out (draws 2)")
    fix = ServerFixture(n_clients=2, rule8_timeout=1.0)
    try:
        fix.start_game()

        fix.inject_top_and_color(Card("red", TYPE_NUMBER, 5), "red")
        fix.set_current_player("p0")
        fix.inject_hand("p0", [Card("red", TYPE_NUMBER, 8), Card("red", TYPE_NUMBER, 1)])

        fix.p0.send(action="play_card", card_index=0)
        fix.p0.find("action_result")   # consume result

        # React immediately so p0 is off the hook — p1 never reacts
        fix.p0.send(action="react")
        react_r = fix.p0.find("action_result")
        check("p0 react accepted", react_r is not None and react_r["success"])

        # Drain remaining buffered messages (state_update, etc.) quickly
        fix.p0.drain(timeout=0.1)
        fix.p1.drain(timeout=0.1)

        # Wait for the 1.0 s timer to fire (we're well under that so far)
        time.sleep(0.7)

        resolved = fix.p0.find("rule8_resolved", timeout=1.0)
        check("rule8_resolved fired after timeout",  resolved is not None)
        check("p1 is the loser (timed out)",         "p1" in (resolved or {}).get("losers", []))

        s = fix.p0.find("state_update", timeout=1.0)
        check("Phase back to playing after timeout", s is not None and s["state"]["phase"] == "playing")

    finally:
        fix.teardown()


def test_rule8_cannot_play_during_reaction() -> None:
    print("Rule 8: play_card blocked during reaction window")
    fix = ServerFixture(n_clients=2, rule8_timeout=2.0)
    try:
        fix.start_game()

        fix.inject_top_and_color(Card("red", TYPE_NUMBER, 5), "red")
        fix.set_current_player("p0")
        fix.inject_hand("p0", [Card("red", TYPE_NUMBER, 8), Card("red", TYPE_NUMBER, 1)])

        fix.p0.send(action="play_card", card_index=0)
        fix.p0.find("action_result")
        fix.p0.drain(timeout=0.3)
        fix.p1.drain(timeout=0.3)

        # p1 tries to play a card while Rule 8 is active — must be rejected
        fix.p1.send(action="play_card", card_index=0)
        r = fix.p1.find("action_result", timeout=1.0)
        check("play_card rejected during rule8_reaction", r is not None and not r["success"])

    finally:
        fix.teardown()


# ---------------------------------------------------------------------------
# Test: unknown action / malformed JSON
# ---------------------------------------------------------------------------

def test_protocol_errors() -> None:
    print("Protocol: unknown action and malformed JSON")
    ServerFixture._port_counter += 1
    port = ServerFixture._port_counter
    server = UNOServer(host="127.0.0.1", port=port, min_players=2)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(0.05)

    try:
        c = TestClient("127.0.0.1", port)
        c.drain(timeout=0.3)   # welcome

        # Unknown action
        c.send(action="do_something_weird")
        r = c.find("error", timeout=1.0)
        check("Unknown action returns error", r is not None)

        # Malformed JSON
        c._sock.sendall(b"not json at all\n")
        r2 = c.find("error", timeout=1.0)
        check("Malformed JSON returns error", r2 is not None)

        c.close()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_lobby_welcome,
        test_game_start,
        test_play_card,
        test_draw_and_pass,
        test_win,
        test_rule8_all_react,
        test_rule8_timeout,
        test_rule8_cannot_play_during_reaction,
        test_protocol_errors,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failures += 1
            print(f"  [ASSERTION FAILED] {e}")
        except Exception as e:
            failures += 1
            print(f"  [EXCEPTION] {type(e).__name__}: {e}")
    print()
    if failures == 0:
        print("\033[92mAll tests passed.\033[0m")
    else:
        print(f"\033[91m{failures} test(s) failed.\033[0m")
