"""
Tests for Phase 3 custom house rules:
  - Penalty stacking (+2/+4)
  - Rule 0 (hand pass)
  - Rule 7 (hand swap)
  - No-Action-Win (verified still enforced)

Run with: python test_phase3.py
"""
from typing import List

from game.models import (
    Card, GameState, Player,
    TYPE_NUMBER, TYPE_SKIP, TYPE_DRAW_TWO, TYPE_WILD, TYPE_WILD_DRAW_FOUR,
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

def C(color, card_type, value=None) -> Card:
    return Card(color=color, card_type=card_type, value=value)


def make_state(
    names: List[str],
    top: Card,
    active_color: str,
    draw_pile: List[Card] = None,
) -> GameState:
    players = [Player(player_id=f"p{i}", name=n) for i, n in enumerate(names)]
    return GameState(
        players=players,
        draw_pile=list(draw_pile or []),
        discard_pile=[top],
        phase="playing",
        active_color=active_color,
    )


def give(state: GameState, pid: str, cards: List[Card]) -> None:
    state.get_player(pid).hand = list(cards)


# ---------------------------------------------------------------------------
# Stacking: +2 on +2
# ---------------------------------------------------------------------------

def test_stack_d2_on_d2() -> None:
    print("Stacking: +2 stacks on +2")

    top = C("red", TYPE_NUMBER, 5)
    filler = [C("blue", TYPE_NUMBER, 1)] * 10
    state = make_state(["Alice", "Bob", "Carol"], top, "red", draw_pile=filler)
    give(state, "p0", [C("red", TYPE_DRAW_TWO), C("red", TYPE_NUMBER, 1)])
    give(state, "p1", [C("blue", TYPE_DRAW_TWO), C("blue", TYPE_NUMBER, 2)])

    # Alice plays +2
    r = play_card(state, "p0", 0)
    check("Alice +2: success", r.success)
    check("pending_draw=2 after first +2", state.pending_draw == 2)
    check("Turn -> Bob", state.current_player.player_id == "p1")

    # Bob stacks with his own +2 (blue +2 is legal: same type)
    r2 = play_card(state, "p1", 0)
    check("Bob stacks +2: success", r2.success)
    check("pending_draw=4 after stack", state.pending_draw == 4)
    check("stacking_type still draw_two", state.stacking_type == TYPE_DRAW_TWO)
    check("Turn -> Carol", state.current_player.player_id == "p2")

    # Carol has nothing to stack — takes the penalty
    give(state, "p2", [C("green", TYPE_NUMBER, 3)])   # no stackable card (1 card in hand)
    r3 = draw_card(state, "p2")
    check("Carol takes penalty: success", r3.success)
    check("Event: took_penalty", "took_penalty" in r3.events)
    check("Carol drew 4 cards (1 existing + 4 drawn = 5)", state.get_player("p2").hand_count() == 5)
    check("pending_draw cleared", state.pending_draw == 0)
    check("stacking_type cleared", state.stacking_type is None)
    check("Turn -> Alice", state.current_player.player_id == "p0")


# ---------------------------------------------------------------------------
# Stacking: +4 escalates the stack
# ---------------------------------------------------------------------------

def test_stack_d4_escalates() -> None:
    print("Stacking: +4 escalates a +2 stack")

    top = C("red", TYPE_NUMBER, 5)
    filler = [C("blue", TYPE_NUMBER, 1)] * 10
    state = make_state(["Alice", "Bob", "Carol", "Dave"], top, "red", draw_pile=filler)
    give(state, "p0", [C("red", TYPE_DRAW_TWO), C("red", TYPE_NUMBER, 1)])
    give(state, "p1", [C(None, TYPE_WILD_DRAW_FOUR), C("blue", TYPE_NUMBER, 2)])
    give(state, "p2", [C(None, TYPE_WILD_DRAW_FOUR), C("green", TYPE_NUMBER, 3)])

    # Alice plays +2
    play_card(state, "p0", 0)
    check("After +2: pending=2, stacking=draw_two",
          state.pending_draw == 2 and state.stacking_type == TYPE_DRAW_TWO)

    # Bob escalates with +4
    r = play_card(state, "p1", 0, chosen_color="blue")
    check("Bob +4 stack: success", r.success)
    check("pending_draw=6 after +4 escalation", state.pending_draw == 6)
    check("stacking_type upgraded to wild_draw_four", state.stacking_type == TYPE_WILD_DRAW_FOUR)
    check("Turn -> Carol", state.current_player.player_id == "p2")

    # Carol stacks another +4
    r2 = play_card(state, "p2", 0, chosen_color="green")
    check("Carol +4 stack: success", r2.success)
    check("pending_draw=10", state.pending_draw == 10)
    check("Turn -> Dave", state.current_player.player_id == "p3")

    # Dave has no +4 — takes 10 cards
    give(state, "p3", [C("yellow", TYPE_NUMBER, 5)])  # 1 card in hand
    r3 = draw_card(state, "p3")
    check("Dave takes 10-card penalty", r3.success)
    check("Dave drew 10 cards (1 existing + 10 drawn = 11)", state.get_player("p3").hand_count() == 11)
    check("Stack fully cleared", state.pending_draw == 0 and state.stacking_type is None)


# ---------------------------------------------------------------------------
# Stacking: +2 blocked when +4 is active
# ---------------------------------------------------------------------------

def test_d2_blocked_by_d4_stack() -> None:
    print("Stacking: +2 cannot stack when +4 is active")

    top = C("red", TYPE_NUMBER, 5)
    filler = [C("green", TYPE_NUMBER, 1)] * 8
    state = make_state(["Alice", "Bob"], top, "red", draw_pile=filler)
    give(state, "p0", [C(None, TYPE_WILD_DRAW_FOUR), C("red", TYPE_NUMBER, 1)])
    give(state, "p1", [C("blue", TYPE_DRAW_TWO), C("blue", TYPE_NUMBER, 2)])

    # Alice plays +4
    play_card(state, "p0", 0, chosen_color="blue")
    check("stacking_type is wild_draw_four", state.stacking_type == TYPE_WILD_DRAW_FOUR)

    # Bob tries to stack a +2 — must be rejected
    r = play_card(state, "p1", 0)
    check("+2 cannot stack on +4", not r.success)
    check("Error mentions stack", "stack" in r.error.lower())

    # Bob's only legal action is to take the penalty (2 existing + 4 drawn = 6)
    bob_before = state.get_player("p1").hand_count()   # captured after give = 2
    r2 = draw_card(state, "p1")
    check("Bob takes penalty", r2.success)
    check("Bob drew 4 cards", state.get_player("p1").hand_count() == bob_before + 4)


# ---------------------------------------------------------------------------
# Stacking: player with stackable card may still choose to take penalty
# ---------------------------------------------------------------------------

def test_can_take_penalty_instead_of_stacking() -> None:
    print("Stacking: player may take penalty even when they could stack")

    top = C("red", TYPE_NUMBER, 5)
    filler = [C("blue", TYPE_NUMBER, 1)] * 4
    state = make_state(["Alice", "Bob"], top, "red", draw_pile=filler)
    give(state, "p0", [C("red", TYPE_DRAW_TWO), C("red", TYPE_NUMBER, 1)])
    give(state, "p1", [C("blue", TYPE_DRAW_TWO), C("blue", TYPE_NUMBER, 2)])

    play_card(state, "p0", 0)
    check("pending_draw=2 on Bob", state.pending_draw == 2)

    # Bob has a +2 but calls draw_card instead — takes the penalty
    bob_before = state.get_player("p1").hand_count()
    r = draw_card(state, "p1")
    check("Taking penalty when stackable: allowed", r.success)
    check("Event: took_penalty", "took_penalty" in r.events)
    check("Bob drew 2 (not 1)", state.get_player("p1").hand_count() == bob_before + 2)
    check("Stack cleared", state.pending_draw == 0)


# ---------------------------------------------------------------------------
# Rule 0: clockwise hand pass
# ---------------------------------------------------------------------------

def test_rule0_clockwise() -> None:
    print("Rule 0: clockwise hand pass (3 players)")

    top = C("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob", "Carol"], top, "red")
    # Distinct hands so we can verify the rotation
    hand_a = [C("red",   TYPE_NUMBER, 1), C("red",   TYPE_NUMBER, 2)]
    hand_b = [C("blue",  TYPE_NUMBER, 3), C("blue",  TYPE_NUMBER, 4)]
    hand_c = [C("green", TYPE_NUMBER, 5), C("green", TYPE_NUMBER, 6)]
    give(state, "p0", [C("red", TYPE_NUMBER, 0)] + hand_a)  # card to play + filler
    give(state, "p1", hand_b)
    give(state, "p2", hand_c)

    # Alice plays 0, passes clockwise (direction=1)
    r = play_card(state, "p0", 0, pass_direction=1)
    check("Success", r.success)
    check("Event: hand_pass", "hand_pass" in r.events)

    # Clockwise pass: player i receives from player (i-1)
    # p0 <- p2 (Carol's hand), p1 <- p0's hand (hand_a), p2 <- p1 (hand_b)
    check("Alice received Carol's hand", state.get_player("p0").hand == hand_c)
    check("Bob received Alice's hand",   state.get_player("p1").hand == hand_a)
    check("Carol received Bob's hand",   state.get_player("p2").hand == hand_b)
    check("Turn advanced to Bob",        state.current_player.player_id == "p1")


# ---------------------------------------------------------------------------
# Rule 0: counter-clockwise hand pass
# ---------------------------------------------------------------------------

def test_rule0_counter_clockwise() -> None:
    print("Rule 0: counter-clockwise hand pass (3 players)")

    top = C("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob", "Carol"], top, "red")
    hand_a = [C("red",   TYPE_NUMBER, 1)]
    hand_b = [C("blue",  TYPE_NUMBER, 3)]
    hand_c = [C("green", TYPE_NUMBER, 5)]
    give(state, "p0", [C("red", TYPE_NUMBER, 0)] + hand_a)
    give(state, "p1", hand_b)
    give(state, "p2", hand_c)

    # Alice plays 0, passes counter-clockwise (direction=-1)
    r = play_card(state, "p0", 0, pass_direction=-1)
    check("Success", r.success)

    # CCW pass: player i receives from player (i+1)
    # p0 <- p1 (Bob's hand), p1 <- p2 (Carol's hand), p2 <- p0 (hand_a)
    check("Alice received Bob's hand",  state.get_player("p0").hand == hand_b)
    check("Bob received Carol's hand",  state.get_player("p1").hand == hand_c)
    check("Carol received Alice's hand", state.get_player("p2").hand == hand_a)


# ---------------------------------------------------------------------------
# Rule 0: validation (direction missing or invalid)
# ---------------------------------------------------------------------------

def test_rule0_validation() -> None:
    print("Rule 0: validation")

    top = C("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob"], top, "red")
    give(state, "p0", [C("red", TYPE_NUMBER, 0), C("red", TYPE_NUMBER, 1)])

    r = play_card(state, "p0", 0)                          # missing direction
    check("Missing pass_direction rejected", not r.success)

    r2 = play_card(state, "p0", 0, pass_direction=0)       # invalid value
    check("pass_direction=0 rejected", not r2.success)

    r3 = play_card(state, "p0", 0, pass_direction=1)       # valid
    check("pass_direction=1 accepted", r3.success)


# ---------------------------------------------------------------------------
# Rule 0: winning with a 0 (no hand pass when game ends)
# ---------------------------------------------------------------------------

def test_rule0_win_no_pass() -> None:
    print("Rule 0: win with 0 skips hand-pass effect")

    top = C("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob"], top, "red")
    hand_b = [C("blue", TYPE_NUMBER, 3)]
    give(state, "p0", [C("red", TYPE_NUMBER, 0)])   # only card = winning card
    give(state, "p1", hand_b)

    r = play_card(state, "p0", 0, pass_direction=1)
    check("Win with 0 succeeds", r.success)
    check("Event: win (not hand_pass)", r.events == ["win"])
    check("Phase is finished", state.phase == "finished")
    # Bob's hand must be unchanged (hand pass never happened)
    check("Bob's hand untouched", state.get_player("p1").hand == hand_b)


# ---------------------------------------------------------------------------
# Rule 7: basic hand swap
# ---------------------------------------------------------------------------

def test_rule7_swap() -> None:
    print("Rule 7: hand swap")

    top = C("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob", "Carol"], top, "red")
    hand_a = [C("red",  TYPE_NUMBER, 1), C("red",  TYPE_NUMBER, 2)]
    hand_b = [C("blue", TYPE_NUMBER, 3), C("blue", TYPE_NUMBER, 4), C("blue", TYPE_NUMBER, 5)]
    give(state, "p0", [C("red", TYPE_NUMBER, 7)] + hand_a)
    give(state, "p1", hand_b)

    r = play_card(state, "p0", 0, swap_target_id="p1")
    check("Success", r.success)
    check("Event: hand_swap", "hand_swap" in r.events)
    check("Alice now has Bob's old hand", state.get_player("p0").hand == hand_b)
    check("Bob now has Alice's old hand", state.get_player("p1").hand == hand_a)
    check("Carol's hand untouched",       state.get_player("p2").hand == [])
    check("Turn advanced to Bob",         state.current_player.player_id == "p1")


# ---------------------------------------------------------------------------
# Rule 7: validation
# ---------------------------------------------------------------------------

def test_rule7_validation() -> None:
    print("Rule 7: validation")

    top = C("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob", "Carol"], top, "red")
    give(state, "p0", [C("red", TYPE_NUMBER, 7), C("red", TYPE_NUMBER, 1)])

    # No target
    r = play_card(state, "p0", 0)
    check("Missing swap_target_id rejected", not r.success)

    # Self-swap
    r2 = play_card(state, "p0", 0, swap_target_id="p0")
    check("Self-swap rejected", not r2.success)

    # Non-existent player
    r3 = play_card(state, "p0", 0, swap_target_id="p99")
    check("Unknown target rejected", not r3.success)

    # Valid swap
    r4 = play_card(state, "p0", 0, swap_target_id="p2")
    check("Valid swap accepted", r4.success)


# ---------------------------------------------------------------------------
# Rule 7: winning with a 7 (no swap when game ends)
# ---------------------------------------------------------------------------

def test_rule7_win_no_swap() -> None:
    print("Rule 7: win with 7 skips swap effect")

    top = C("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob"], top, "red")
    hand_b = [C("blue", TYPE_NUMBER, 3), C("blue", TYPE_NUMBER, 4)]
    give(state, "p0", [C("red", TYPE_NUMBER, 7)])   # only card = winning card
    give(state, "p1", hand_b)

    r = play_card(state, "p0", 0, swap_target_id="p1")
    check("Win with 7 succeeds", r.success)
    check("Event: win (not hand_swap)", r.events == ["win"])
    check("Phase is finished", state.phase == "finished")
    check("Bob's hand untouched", state.get_player("p1").hand == hand_b)


# ---------------------------------------------------------------------------
# No-Action-Win: still enforced in Phase 3
# ---------------------------------------------------------------------------

def test_no_action_win() -> None:
    print("No-Action-Win: still enforced")

    top = C("red", TYPE_NUMBER, 5)
    state = make_state(["Alice", "Bob"], top, "red")

    for card_type, extra_kwargs in [
        (TYPE_SKIP, {}),
        (TYPE_DRAW_TWO, {}),
        (TYPE_WILD, {"chosen_color": "red"}),
        (TYPE_WILD_DRAW_FOUR, {"chosen_color": "red"}),
    ]:
        color = None if card_type in (TYPE_WILD, TYPE_WILD_DRAW_FOUR) else "red"
        give(state, "p0", [Card(color=color, card_type=card_type, value=None)])
        r = play_card(state, "p0", 0, **extra_kwargs)
        check(f"{card_type} as last card rejected", not r.success)
        check(f"{card_type}: card stays in hand", state.get_player("p0").hand_count() == 1)

    # Number card as last card IS legal
    give(state, "p0", [C("red", TYPE_NUMBER, 9)])
    r = play_card(state, "p0", 0)
    check("Number card as last card allowed", r.success)
    check("Game finished", state.phase == "finished")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_stack_d2_on_d2,
        test_stack_d4_escalates,
        test_d2_blocked_by_d4_stack,
        test_can_take_penalty_instead_of_stacking,
        test_rule0_clockwise,
        test_rule0_counter_clockwise,
        test_rule0_validation,
        test_rule0_win_no_pass,
        test_rule7_swap,
        test_rule7_validation,
        test_rule7_win_no_swap,
        test_no_action_win,
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
