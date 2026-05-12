"""
Smoke tests for Phase 1 data structures.
Run with: python test_models.py
"""
import json
from game.models import (
    Card, Deck, Player, GameState, Rule8State,
    deal_initial_state,
    TYPE_NUMBER, TYPE_SKIP, TYPE_REVERSE, TYPE_DRAW_TWO,
    TYPE_WILD, TYPE_WILD_DRAW_FOUR,
    INITIAL_HAND_SIZE,
)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def check(label: str, condition: bool) -> None:
    print(f"  [{PASS if condition else FAIL}] {label}")
    if not condition:
        raise AssertionError(label)


# ---------------------------------------------------------------------------
# Deck tests
# ---------------------------------------------------------------------------

def test_deck() -> None:
    print("Deck")
    deck = Deck()
    check("108 cards total", len(deck) == 108)

    number_cards  = [c for c in deck.cards if c.card_type == TYPE_NUMBER]
    skip_cards    = [c for c in deck.cards if c.card_type == TYPE_SKIP]
    reverse_cards = [c for c in deck.cards if c.card_type == TYPE_REVERSE]
    draw2_cards   = [c for c in deck.cards if c.card_type == TYPE_DRAW_TWO]
    wild_cards    = [c for c in deck.cards if c.card_type == TYPE_WILD]
    wd4_cards     = [c for c in deck.cards if c.card_type == TYPE_WILD_DRAW_FOUR]

    check("76 number cards (1×0 + 2×1-9 per color × 4 colors)", len(number_cards) == 76)
    check("8 Skip cards",        len(skip_cards)    == 8)
    check("8 Reverse cards",     len(reverse_cards) == 8)
    check("8 Draw Two cards",    len(draw2_cards)   == 8)
    check("4 Wild cards",        len(wild_cards)    == 4)
    check("4 Wild Draw Four cards", len(wd4_cards)  == 4)

    deck.shuffle()
    card = deck.draw()
    check("draw() returns a Card", isinstance(card, Card))
    check("deck shrinks by 1 after draw", len(deck) == 107)


# ---------------------------------------------------------------------------
# Card.is_playable_on tests
# ---------------------------------------------------------------------------

def test_card_legality() -> None:
    print("Card.is_playable_on")

    red5   = Card("red",   TYPE_NUMBER, 5)
    blue5  = Card("blue",  TYPE_NUMBER, 5)
    red7   = Card("red",   TYPE_NUMBER, 7)
    green3 = Card("green", TYPE_NUMBER, 3)
    red_skip   = Card("red",  TYPE_SKIP,    None)
    blue_draw2 = Card("blue", TYPE_DRAW_TWO, None)
    wild       = Card(None,   TYPE_WILD,     None)
    wd4        = Card(None,   TYPE_WILD_DRAW_FOUR, None)

    # Normal turn — top is red 5, active_color red
    # green3: different color, different value — not playable
    check("diff color, diff value blocked",  green3.is_playable_on(red5, "red", None) is False)
    # red7: same color, different value — playable by color
    check("same color plays",                red7.is_playable_on(red5,   "red", None) is True)
    # blue5: different color, same value — playable by value
    check("same value diff color plays",     blue5.is_playable_on(red5,  "red", None) is True)
    # red5: exact match
    check("exact match plays",               red5.is_playable_on(red5,   "red", None) is True)

    # Wild is always playable on a normal turn
    check("wild always playable",    wild.is_playable_on(red5,  "red",  None) is True)
    check("wd4 always playable",     wd4.is_playable_on(red5,   "red",  None) is True)

    # Active color override after a wild
    # top is a wild (color=None), active_color = "blue"
    wild_top = Card(None, TYPE_WILD, None)
    check("match active color after wild", blue5.is_playable_on(wild_top, "blue", None) is True)
    check("mismatch active color",         red7.is_playable_on(wild_top,  "blue", None) is False)

    # Penalty stacking — draw_two active
    check("draw_two stacks draw_two",  blue_draw2.is_playable_on(red5, "red", TYPE_DRAW_TWO) is True)
    check("draw_two stacks wd4",       wd4.is_playable_on(red5,        "red", TYPE_DRAW_TWO) is True)
    check("number blocked when stacking", red5.is_playable_on(red5,    "red", TYPE_DRAW_TWO) is False)

    # Penalty stacking — wild_draw_four active
    check("wd4 stacks wd4",             wd4.is_playable_on(red5,        "red", TYPE_WILD_DRAW_FOUR) is True)
    check("draw_two blocked by wd4",    blue_draw2.is_playable_on(red5, "red", TYPE_WILD_DRAW_FOUR) is False)

    # No-Action-Win rule
    check("number is legal final card", red5.is_legal_final_card() is True)
    check("skip is not legal final",    red_skip.is_legal_final_card() is False)
    check("wild is not legal final",    wild.is_legal_final_card() is False)
    check("wd4 is not legal final",     wd4.is_legal_final_card() is False)


# ---------------------------------------------------------------------------
# Serialization round-trip tests
# ---------------------------------------------------------------------------

def test_serialization() -> None:
    print("Serialization round-trips")

    # Card
    c = Card("red", TYPE_NUMBER, 7)
    check("Card round-trip", Card.from_dict(c.to_dict()) == c)

    wild = Card(None, TYPE_WILD, None)
    check("Wild card round-trip", Card.from_dict(wild.to_dict()) == wild)

    # Player
    p = Player(player_id="p0", name="Alice", hand=[c, wild])
    p2 = Player.from_dict(p.to_dict())
    check("Player name", p2.name == "Alice")
    check("Player hand count", p2.hand_count() == 2)
    check("Player hide_hand empties list", p.to_dict(hide_hand=True)["hand"] == [])
    check("Player hide_hand keeps count",  p.to_dict(hide_hand=True)["hand_count"] == 2)

    # Rule8State
    r8 = Rule8State(deadline=9999.0, reacted=["p0"], pending_players=["p1", "p2"])
    check("Rule8State round-trip", Rule8State.from_dict(r8.to_dict()).deadline == 9999.0)

    # GameState — full round-trip via JSON
    state = deal_initial_state(["Alice", "Bob", "Carol"])
    json_str = state.to_json()
    restored = GameState.from_json(json_str)
    check("GameState JSON is valid",            isinstance(json.loads(json_str), dict))
    check("Restored player count",              len(restored.players) == 3)
    check("Restored draw pile count",           len(restored.draw_pile) == len(state.draw_pile))
    check("Restored direction",                 restored.direction == 1)
    check("Restored phase",                     restored.phase == "playing")
    check("Restored active_color is a string",  isinstance(restored.active_color, str))

    # Client snapshot hides other hands but preserves hand_count
    snap = state.to_client_dict("p0")
    check("Client snap: viewer hand visible",   len(snap["players"][0]["hand"]) == INITIAL_HAND_SIZE)
    check("Client snap: opponent hand hidden",  len(snap["players"][1]["hand"]) == 0)
    check("Client snap: opponent count correct",snap["players"][1]["hand_count"] == INITIAL_HAND_SIZE)
    check("Client snap: draw_pile hidden",      snap["draw_pile"] == [])
    check("Client snap: draw_pile_count set",   snap["draw_pile_count"] > 0)


# ---------------------------------------------------------------------------
# deal_initial_state tests
# ---------------------------------------------------------------------------

def test_deal() -> None:
    print("deal_initial_state")

    state = deal_initial_state(["Alice", "Bob"])
    check("2-player deal: 2 players",            len(state.players) == 2)
    check("Each player has 7 cards",             all(p.hand_count() == INITIAL_HAND_SIZE for p in state.players))
    check("Discard pile has 1 card",             len(state.discard_pile) == 1)
    check("Start card is a number",              state.top_card.card_type == TYPE_NUMBER)
    check("active_color matches start card",     state.active_color == state.top_card.color)
    check("Draw pile size: 108 - 14(hands) - 1(discard) = 93", len(state.draw_pile) == 93)
    check("Phase is playing",                    state.phase == "playing")
    check("Current player index is 0",           state.current_player_index == 0)

    state4 = deal_initial_state(["A", "B", "C", "D"])
    check("4-player draw pile: 108 - 28 - 1 = 79", len(state4.draw_pile) == 79)

    try:
        deal_initial_state(["Solo"])
        check("1-player raises ValueError", False)
    except ValueError:
        check("1-player raises ValueError", True)

    try:
        deal_initial_state(["A", "B", "C", "D", "E"])
        check("5-player raises ValueError", False)
    except ValueError:
        check("5-player raises ValueError", True)


# ---------------------------------------------------------------------------
# advance_turn / direction tests
# ---------------------------------------------------------------------------

def test_turn_mechanics() -> None:
    print("Turn mechanics")

    state = deal_initial_state(["A", "B", "C"])

    state.advance_turn()
    check("Advance from 0 -> 1 (clockwise)",  state.current_player_index == 1)

    state.direction = -1
    state.advance_turn()
    check("Reverse: advance from 1 -> 0",    state.current_player_index == 0)

    state.direction = 1
    state.advance_turn(steps=2)
    check("Skip: advance from 0 -> 2",       state.current_player_index == 2)

    state.advance_turn()
    check("Wrap-around: 2 -> 0",             state.current_player_index == 0)

    check("get_player by id",               state.get_player("p1").name == "B")
    check("get_player unknown returns None", state.get_player("p99") is None)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [test_deck, test_card_legality, test_serialization, test_deal, test_turn_mechanics]
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
