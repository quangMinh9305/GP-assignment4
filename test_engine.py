"""
Tests for Phase 2: core turn logic (engine.py).
Run with: python test_engine.py
"""
from typing import List

from game.models import (
    Card, GameState, Player,
    TYPE_NUMBER, TYPE_SKIP, TYPE_REVERSE, TYPE_DRAW_TWO,
    TYPE_WILD, TYPE_WILD_DRAW_FOUR,
)
from game.engine import ActionResult, draw_card, pass_turn, play_card

PASS_LABEL = "\033[92mPASS\033[0m"
FAIL_LABEL = "\033[91mFAIL\033[0m"


def check(label: str, condition: bool) -> None:
    print(f"  [{PASS_LABEL if condition else FAIL_LABEL}] {label}")
    if not condition:
        raise AssertionError(label)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_card(color, card_type, value=None) -> Card:
    return Card(color=color, card_type=card_type, value=value)


def make_state(
    names: List[str],
    top: Card,
    active_color: str,
    draw_pile: List[Card] = None,
) -> GameState:
    """Minimal GameState; each player starts with an empty hand."""
    players = [Player(player_id=f"p{i}", name=n) for i, n in enumerate(names)]
    return GameState(
        players=players,
        draw_pile=list(draw_pile or []),
        discard_pile=[top],
        phase="playing",
        active_color=active_color,
    )


def give(state: GameState, player_id: str, cards: List[Card]) -> None:
    state.get_player(player_id).hand = list(cards)


# ---------------------------------------------------------------------------
# play_card — validation
# ---------------------------------------------------------------------------

def test_validation() -> None:
    print("play_card: validation")

    top = make_card("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob"], top, "red")
    give(state, "p0", [make_card("red", TYPE_NUMBER, 7)])

    # Wrong player's turn
    r = play_card(state, "p1", 0)
    check("Wrong player rejected", not r.success)
    check("Error mentions turn", "turn" in r.error.lower())

    # Bad card index
    r = play_card(state, "p0", 5)
    check("Out-of-range index rejected", not r.success)

    # Card doesn't match
    give(state, "p0", [make_card("blue", TYPE_NUMBER, 3)])
    r = play_card(state, "p0", 0)
    check("Non-matching card rejected", not r.success)

    # Wild without chosen_color
    give(state, "p0", [make_card(None, TYPE_WILD)])
    r = play_card(state, "p0", 0, chosen_color=None)
    check("Wild without color rejected", not r.success)

    # Wild with invalid color string
    r = play_card(state, "p0", 0, chosen_color="purple")
    check("Wild with bad color rejected", not r.success)

    # Game not in playing phase
    state.phase = "finished"
    r = play_card(state, "p0", 0, chosen_color="red")
    check("Non-playing phase rejected", not r.success)
    state.phase = "playing"


# ---------------------------------------------------------------------------
# play_card — number card (happy path)
# ---------------------------------------------------------------------------

def test_number_card() -> None:
    print("play_card: number card")

    top = make_card("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob", "Carol"], top, "red")
    # Use 9, not 7 — 7 triggers Rule 7 and requires swap_target_id
    give(state, "p0", [make_card("red", TYPE_NUMBER, 9), make_card("blue", TYPE_NUMBER, 3)])

    r = play_card(state, "p0", 0)   # red 9 on red 5 (same color)
    check("Success", r.success)
    check("No special events", r.events == [])
    check("Turn advanced to p1", state.current_player.player_id == "p1")
    check("active_color updated to red", state.active_color == "red")
    check("Discard top is red 9", state.top_card.card_type == TYPE_NUMBER and state.top_card.value == 9)
    check("Alice has 1 card left", state.get_player("p0").hand_count() == 1)

    # Play by value match (different color, same number)
    top2 = make_card("red", TYPE_NUMBER, 7)
    state2 = make_state(["Alice", "Bob"], top2, "red")
    give(state2, "p0", [make_card("blue", TYPE_NUMBER, 7)])
    r2 = play_card(state2, "p0", 0)
    check("Same value, diff color plays", r2.success)
    check("active_color updated to blue", state2.active_color == "blue")


# ---------------------------------------------------------------------------
# play_card — Skip
# ---------------------------------------------------------------------------

def test_skip() -> None:
    print("play_card: Skip")

    top = make_card("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob", "Carol"], top, "red")
    # Two cards so Skip is not the final card (No-Action-Win guard)
    give(state, "p0", [make_card("red", TYPE_SKIP), make_card("red", TYPE_NUMBER, 1)])

    r = play_card(state, "p0", 0)
    check("Success", r.success)
    check("Event: skip", "skip" in r.events)
    check("Turn skipped to p2 (Bob skipped)", state.current_player.player_id == "p2")


# ---------------------------------------------------------------------------
# play_card — Reverse (3-player and 2-player)
# ---------------------------------------------------------------------------

def test_reverse() -> None:
    print("play_card: Reverse")

    top = make_card("red", TYPE_NUMBER, 5)

    # 3-player: direction flips, turn advances in new direction
    state3 = make_state(["Alice", "Bob", "Carol"], top, "red")
    give(state3, "p0", [make_card("red", TYPE_REVERSE), make_card("red", TYPE_NUMBER, 1)])
    r = play_card(state3, "p0", 0)
    check("3p success", r.success)
    check("3p event: reverse", "reverse" in r.events)
    check("3p direction is now -1", state3.direction == -1)
    # With direction=-1, from p0: next = (0 + -1*1) % 3 = 2 -> Carol
    check("3p turn goes to Carol (index 2)", state3.current_player.player_id == "p2")

    # 2-player: acts like Skip, turn comes back to Alice
    state2 = make_state(["Alice", "Bob"], top, "red")
    give(state2, "p0", [make_card("red", TYPE_REVERSE), make_card("red", TYPE_NUMBER, 1)])
    r2 = play_card(state2, "p0", 0)
    check("2p success", r2.success)
    check("2p direction flipped", state2.direction == -1)
    # steps=2, direction=-1: (0 + -1*2) % 2 = -2 % 2 = 0 -> Alice
    check("2p turn stays with Alice", state2.current_player.player_id == "p0")


# ---------------------------------------------------------------------------
# play_card — Draw Two
# ---------------------------------------------------------------------------

def test_draw_two() -> None:
    print("play_card: Draw Two")

    top = make_card("red", TYPE_NUMBER, 5)
    filler = [make_card("blue", TYPE_NUMBER, 1)] * 6
    state = make_state(["Alice", "Bob", "Carol"], top, "red", draw_pile=filler)
    give(state, "p0", [make_card("red", TYPE_DRAW_TWO), make_card("red", TYPE_NUMBER, 1)])

    bob_before = state.get_player("p1").hand_count()
    r = play_card(state, "p0", 0)
    check("Success", r.success)
    check("Event: draw_two", "draw_two" in r.events)
    # Stacking model: Bob has NOT drawn yet — pending_draw accumulates
    check("pending_draw is 2", state.pending_draw == 2)
    check("stacking_type is draw_two", state.stacking_type == TYPE_DRAW_TWO)
    check("Turn is now Bob's (not skipped)", state.current_player.player_id == "p1")
    check("Bob has not drawn yet", state.get_player("p1").hand_count() == bob_before)

    # Bob takes the penalty by calling draw_card
    r2 = draw_card(state, "p1")
    check("Bob takes penalty: success", r2.success)
    check("Event: took_penalty", "took_penalty" in r2.events)
    check("Bob drew 2 cards", state.get_player("p1").hand_count() == bob_before + 2)
    check("Penalty cleared", state.pending_draw == 0)
    check("Turn advanced to Carol (p2)", state.current_player.player_id == "p2")


# ---------------------------------------------------------------------------
# play_card — Wild
# ---------------------------------------------------------------------------

def test_wild() -> None:
    print("play_card: Wild")

    top = make_card("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob"], top, "red")
    give(state, "p0", [make_card(None, TYPE_WILD), make_card("red", TYPE_NUMBER, 1)])

    r = play_card(state, "p0", 0, chosen_color="blue")
    check("Success", r.success)
    check("No skip events", r.events == [])
    check("active_color set to blue", state.active_color == "blue")
    check("Turn advanced to Bob", state.current_player.player_id == "p1")
    check("Wild card color recorded as blue", state.top_card.color == "blue")


# ---------------------------------------------------------------------------
# play_card — Wild Draw Four
# ---------------------------------------------------------------------------

def test_wild_draw_four() -> None:
    print("play_card: Wild Draw Four")

    top = make_card("red", TYPE_NUMBER, 5)
    filler = [make_card("green", TYPE_NUMBER, 2)] * 6
    state = make_state(["Alice", "Bob", "Carol"], top, "red", draw_pile=filler)
    give(state, "p0", [make_card(None, TYPE_WILD_DRAW_FOUR), make_card("red", TYPE_NUMBER, 1)])

    bob_before = state.get_player("p1").hand_count()
    r = play_card(state, "p0", 0, chosen_color="green")
    check("Success", r.success)
    check("Event: wild_draw_four", "wild_draw_four" in r.events)
    check("active_color set to green", state.active_color == "green")
    # Stacking model: Bob faces the penalty, hasn't drawn yet
    check("pending_draw is 4", state.pending_draw == 4)
    check("stacking_type is wild_draw_four", state.stacking_type == TYPE_WILD_DRAW_FOUR)
    check("Turn is now Bob's", state.current_player.player_id == "p1")
    check("Bob has not drawn yet", state.get_player("p1").hand_count() == bob_before)

    # Bob takes the penalty
    r2 = draw_card(state, "p1")
    check("Bob takes penalty: success", r2.success)
    check("Bob drew 4 cards", state.get_player("p1").hand_count() == bob_before + 4)
    check("Penalty cleared", state.pending_draw == 0)
    check("Turn advanced to Carol (p2)", state.current_player.player_id == "p2")


# ---------------------------------------------------------------------------
# play_card — Win condition
# ---------------------------------------------------------------------------

def test_win() -> None:
    print("play_card: win condition")

    top = make_card("red", TYPE_NUMBER, 5)

    # Legal win: number card as last card
    state = make_state(["Alice", "Bob"], top, "red")
    give(state, "p0", [make_card("red", TYPE_NUMBER, 9)])
    r = play_card(state, "p0", 0)
    check("Legal win succeeds", r.success)
    check("Event: win", "win" in r.events)
    check("Phase is finished", state.phase == "finished")
    check("Winner is Alice", state.winner_id == "p0")

    # Illegal win: action card as last card (No-Action-Win rule)
    state2 = make_state(["Alice", "Bob"], top, "red")
    give(state2, "p0", [make_card("red", TYPE_SKIP)])
    r2 = play_card(state2, "p0", 0)
    check("Skip as final card rejected", not r2.success)
    check("Card stays in hand", state2.get_player("p0").hand_count() == 1)
    check("Phase still playing", state2.phase == "playing")

    # Illegal win: wild as last card
    state3 = make_state(["Alice", "Bob"], top, "red")
    give(state3, "p0", [make_card(None, TYPE_WILD)])
    r3 = play_card(state3, "p0", 0, chosen_color="blue")
    check("Wild as final card rejected", not r3.success)


# ---------------------------------------------------------------------------
# draw_card
# ---------------------------------------------------------------------------

def test_draw_card() -> None:
    print("draw_card")

    top = make_card("red", TYPE_NUMBER, 5)

    # Draw an unplayable card -> turn passes automatically
    unplayable = make_card("blue", TYPE_NUMBER, 3)
    state = make_state(["Alice", "Bob"], top, "red", draw_pile=[unplayable])
    give(state, "p0", [])
    r = draw_card(state, "p0")
    check("Draw success", r.success)
    check("Event: draw_pass (unplayable)", "draw_pass" in r.events)
    check("Alice now has 1 card", state.get_player("p0").hand_count() == 1)
    check("Turn advanced to Bob", state.current_player.player_id == "p1")
    check("has_drawn reset", not state.has_drawn)

    # Draw a playable card -> turn stays, player may play or pass
    playable = make_card("red", TYPE_NUMBER, 8)
    state2 = make_state(["Alice", "Bob"], top, "red", draw_pile=[playable])
    give(state2, "p0", [])
    r2 = draw_card(state2, "p0")
    check("Draw playable success", r2.success)
    check("Event: drew_playable", "drew_playable" in r2.events)
    check("Turn stays with Alice", state2.current_player.player_id == "p0")
    check("has_drawn is True", state2.has_drawn)

    # Cannot draw twice in one turn
    r3 = draw_card(state2, "p0")
    check("Second draw rejected", not r3.success)

    # Wrong player cannot draw
    state3 = make_state(["Alice", "Bob"], top, "red", draw_pile=[playable])
    r4 = draw_card(state3, "p1")
    check("Wrong player draw rejected", not r4.success)


# ---------------------------------------------------------------------------
# pass_turn
# ---------------------------------------------------------------------------

def test_pass_turn() -> None:
    print("pass_turn")

    top = make_card("red", TYPE_NUMBER, 5)
    playable = make_card("red", TYPE_NUMBER, 8)
    state = make_state(["Alice", "Bob"], top, "red", draw_pile=[playable])
    give(state, "p0", [])

    # Must draw before passing
    r = pass_turn(state, "p0")
    check("Pass without draw rejected", not r.success)

    # Draw first (drawn card is playable -> has_drawn=True)
    draw_card(state, "p0")
    check("has_drawn is True after draw", state.has_drawn)

    # Now pass
    r2 = pass_turn(state, "p0")
    check("Pass after draw succeeds", r2.success)
    check("Event: pass", "pass" in r2.events)
    check("Turn advanced to Bob", state.current_player.player_id == "p1")
    check("has_drawn reset", not state.has_drawn)

    # Wrong player cannot pass
    r3 = pass_turn(state, "p0")
    check("Wrong player pass rejected", not r3.success)


# ---------------------------------------------------------------------------
# Turn cycling (direction & wrap-around)
# ---------------------------------------------------------------------------

def test_turn_cycling() -> None:
    print("Turn cycling")

    top = make_card("red", TYPE_NUMBER, 5)
    state = make_state(["A", "B", "C", "D"], top, "red")

    # Normal play cycles p0->p1->p2->p3->p0
    # Give 2 cards so the player doesn't win when they play 1
    for pid in ["p1", "p2", "p3", "p0"]:
        cur = state.current_player.player_id
        give(state, cur, [make_card("red", TYPE_NUMBER, 5), make_card("red", TYPE_NUMBER, 1)])
        play_card(state, cur, 0)
        check(f"Turn advanced to {pid}", state.current_player.player_id == pid)

    # Reverse flips direction; next is p3 (counter-clockwise from p0)
    give(state, "p0", [make_card("red", TYPE_REVERSE), make_card("red", TYPE_NUMBER, 1)])
    play_card(state, "p0", 0)
    check("After reverse, direction=-1", state.direction == -1)
    check("Next player is p3", state.current_player.player_id == "p3")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_validation,
        test_number_card,
        test_skip,
        test_reverse,
        test_draw_two,
        test_wild,
        test_wild_draw_four,
        test_win,
        test_draw_card,
        test_pass_turn,
        test_turn_cycling,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failures += 1
            print(f"  [ASSERTION FAILED] {e}")
    print()
    if failures == 0:
        print("\033[92mAll tests passed.\033[0m")
    else:
        print(f"\033[91m{failures} test(s) failed.\033[0m")
