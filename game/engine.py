"""
Core turn logic for Custom UNO Online.
All functions take a GameState, validate the action, mutate the state in-place
on success, and return an ActionResult.

Implemented:
  play_card   : full validation + card effects
  draw_card   : draw one card, or take an active penalty stack
  pass_turn   : end turn after drawing a playable card without playing it

Custom house rules active in this module:
  No-Action-Win  : cannot win with Skip / Reverse / +2 / Wild / +4
  Stacking       : +2 stacks +2 or +4; +4 only stacks +4; loser draws total
  Rule 0         : playing a 0 → all players pass hands in chosen direction
  Rule 7         : playing a 7 → choose one opponent, swap full hands

Deferred to Phase 4:
  Rule 8 (reaction timer on 8)
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional

from game.models import (
    COLORS,
    TYPE_DRAW_TWO,
    TYPE_NUMBER,
    TYPE_REVERSE,
    TYPE_SKIP,
    TYPE_WILD,
    TYPE_WILD_DRAW_FOUR,
    Card,
    GameState,
    Player,
)


# ---------------------------------------------------------------------------
# ActionResult
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    """
    Returned by every engine function.
    success=True  → state was mutated; events describes what happened.
    success=False → state is unchanged; error describes why.

    Possible events:
      "skip"          - Skip card applied; next player's turn was skipped
      "reverse"       - Direction reversed
      "draw_two"      - +2 played; pending_draw += 2 (victim must draw or stack)
      "wild_draw_four"- +4 played; pending_draw += 4 (victim must draw or stack)
      "took_penalty"  - Player took the full accumulated draw penalty
      "hand_pass"     - Rule 0: all hands rotated in chosen direction
      "hand_swap"     - Rule 7: two players swapped hands
      "draw_pass"     - Drew 1 unplayable card; turn auto-advanced
      "drew_playable" - Drew 1 playable card; player may still play or pass
      "pass"          - Player chose to end their turn without playing drawn card
      "win"           - Player emptied hand with a legal final card
    """
    success: bool
    error: Optional[str] = None
    events: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"success": self.success, "error": self.error, "events": self.events}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _replenish(state: GameState) -> None:
    """Shuffle all discard cards except the top one back into the draw pile."""
    if len(state.discard_pile) <= 1:
        return
    top = state.discard_pile[-1]
    state.draw_pile = state.discard_pile[:-1]
    for card in state.draw_pile:
        if card.is_wild():
            card.color = None
    random.shuffle(state.draw_pile)
    state.discard_pile[:] = [top]


def _draw_cards(state: GameState, player: Player, count: int) -> None:
    """Draw `count` cards for `player`, replenishing from discard as needed."""
    for _ in range(count):
        if not state.draw_pile:
            _replenish(state)
        if state.draw_pile:
            player.hand.append(state.draw_pile.pop())


def _apply_hand_pass(state: GameState, direction: int) -> None:
    """
    Rule 0: simultaneously pass every player's entire hand in `direction`.
    direction=1  → clockwise by seat index  (player i receives from player i-1)
    direction=-1 → counter-clockwise        (player i receives from player i+1)
    """
    n = len(state.players)
    snapshot = [list(p.hand) for p in state.players]
    for i, player in enumerate(state.players):
        player.hand = snapshot[(i - direction) % n]


# ---------------------------------------------------------------------------
# play_card
# ---------------------------------------------------------------------------

def play_card(
    state: GameState,
    player_id: str,
    card_index: int,
    chosen_color: Optional[str] = None,
    pass_direction: Optional[int] = None,
    swap_target_id: Optional[str] = None,
) -> ActionResult:
    """
    Play the card at `card_index` in the current player's hand.

    Parameters
    ----------
    chosen_color   Required when playing Wild or Wild Draw Four ("red" | "yellow" |
                   "green" | "blue").  Ignored for other card types.
    pass_direction Required when playing a 0 (Rule 0).  1 = clockwise seat order,
                   -1 = counter-clockwise.
    swap_target_id Required when playing a 7 (Rule 7).  player_id of the opponent
                   to swap hands with.

    Validation order (nothing is mutated until all checks pass):
      1. Phase is "playing"
      2. Correct player
      3. card_index in bounds
      4. Card legal on current discard (including stacking enforcement)
      5. Wild → chosen_color provided
      6. No-Action-Win: cannot play action/wild as the last card
      7. Rule 0: pass_direction provided
      8. Rule 7: swap_target_id valid
    """
    # --- Guard: phase ---
    if state.phase != "playing":
        return ActionResult(False, error=f"Cannot play: game is in '{state.phase}' phase.")

    # --- Guard: player identity and turn ---
    player = state.get_player(player_id)
    if player is None:
        return ActionResult(False, error="Unknown player_id.")
    if state.current_player.player_id != player_id:
        return ActionResult(False, error="Not your turn.")

    # --- Guard: card index ---
    if not (0 <= card_index < len(player.hand)):
        return ActionResult(
            False,
            error=f"card_index {card_index} out of range (hand size {len(player.hand)}).",
        )
    card = player.hand[card_index]

    # --- Guard: card legality (stacking enforced via state.stacking_type) ---
    if not card.is_playable_on(state.top_card, state.active_color, state.stacking_type):
        if state.stacking_type:
            return ActionResult(
                False,
                error=(
                    f"A draw stack is active (pending: {state.pending_draw}). "
                    f"Must play a '{state.stacking_type}' or higher card, "
                    f"or take the penalty via draw_card."
                ),
            )
        return ActionResult(
            False,
            error=f"'{card}' cannot be played on '{state.top_card}' (active color: {state.active_color}).",
        )

    # --- Guard: wild color ---
    if card.is_wild() and chosen_color not in COLORS:
        return ActionResult(
            False,
            error=f"Must choose a valid color {COLORS} when playing a wild card.",
        )

    # --- Guard: No-Action-Win ---
    if len(player.hand) == 1 and not card.is_legal_final_card():
        return ActionResult(
            False,
            error="Cannot win with an action or wild card (house rule). Play a numbered card.",
        )

    # --- Guards: Rule 0 and Rule 7 (only when not the winning card) ---
    # If this is the player's last card the win fires before any card effect,
    # so the direction / target params are not needed in that case.
    _winning_play = len(player.hand) == 1 and card.is_legal_final_card()

    if not _winning_play:
        if card.card_type == TYPE_NUMBER and card.value == 0:
            if pass_direction not in (1, -1):
                return ActionResult(
                    False,
                    error="Playing a 0 requires pass_direction=1 (clockwise) or -1 (counter-clockwise).",
                )

        if card.card_type == TYPE_NUMBER and card.value == 7:
            if swap_target_id is None:
                return ActionResult(False, error="Playing a 7 requires swap_target_id.")
            if swap_target_id == player_id:
                return ActionResult(False, error="Cannot swap hands with yourself.")
            if state.get_player(swap_target_id) is None:
                return ActionResult(False, error=f"No player with id '{swap_target_id}'.")

    # =========================================================================
    # All validation passed — mutate state from here
    # =========================================================================

    player.hand.pop(card_index)
    state.discard_pile.append(card)
    state.has_drawn = False
    events: List[str] = []

    # Update effective color
    if card.is_wild():
        card.color = chosen_color   # record chosen color on card for display/history
        state.active_color = chosen_color
    else:
        state.active_color = card.color

    # Win check (hand empty + card was a legal final card — already validated)
    if len(player.hand) == 0:
        state.phase = "finished"
        state.winner_id = player_id
        events.append("win")
        return ActionResult(True, events=events)

    # --- Card effects ---
    n = len(state.players)

    if card.card_type == TYPE_SKIP:
        state.advance_turn(steps=2)
        events.append("skip")

    elif card.card_type == TYPE_REVERSE:
        state.direction *= -1
        events.append("reverse")
        # With 2 players Reverse is functionally a Skip
        state.advance_turn(steps=2 if n == 2 else 1)

    elif card.card_type == TYPE_DRAW_TWO:
        # Stacking: accumulate, let next player draw or counter
        state.pending_draw += 2
        state.stacking_type = TYPE_DRAW_TWO
        state.advance_turn(steps=1)
        events.append("draw_two")

    elif card.card_type == TYPE_WILD_DRAW_FOUR:
        # Stacking: same model as draw_two
        state.pending_draw += 4
        state.stacking_type = TYPE_WILD_DRAW_FOUR
        state.advance_turn(steps=1)
        events.append("wild_draw_four")

    elif card.card_type == TYPE_NUMBER and card.value == 0:
        # Rule 0: rotate all hands in the chosen direction, then normal advance
        _apply_hand_pass(state, pass_direction)
        state.advance_turn()
        events.append("hand_pass")

    elif card.card_type == TYPE_NUMBER and card.value == 7:
        # Rule 7: swap hands with chosen opponent, then normal advance
        target = state.get_player(swap_target_id)
        player.hand, target.hand = target.hand, player.hand
        state.advance_turn()
        events.append("hand_swap")

    else:
        # Regular number cards (1-6, 8-9) and plain Wild: normal advance
        state.advance_turn()
        # Rule 8: playing an 8 (not as winning card) opens a reaction window.
        # The server is responsible for the timer; the engine only signals it.
        if card.card_type == TYPE_NUMBER and card.value == 8:
            events.append("rule8_triggered")

    return ActionResult(True, events=events)


# ---------------------------------------------------------------------------
# Rule 8 resolution  (called by the server after the timer / all reactions)
# ---------------------------------------------------------------------------

def resolve_rule8_penalty(state: GameState, loser_ids: List[str]) -> None:
    """
    Draw 2 cards for each loser, clear Rule8State, and resume play.
    The server determines who the losers are; this function only applies
    the penalty and returns the game to "playing" phase.
    Called by the server while holding its state lock.
    """
    for loser_id in loser_ids:
        loser = state.get_player(loser_id)
        if loser:
            _draw_cards(state, loser, 2)
    state.rule8_state = None
    state.phase = "playing"


# ---------------------------------------------------------------------------
# draw_card
# ---------------------------------------------------------------------------

def draw_card(state: GameState, player_id: str) -> ActionResult:
    """
    Draw cards for the current player.

    Two distinct behaviors depending on state:

    A) Active penalty stack (pending_draw > 0):
       Player takes the full accumulated penalty — draws `pending_draw` cards,
       loses their turn (advance 1 step), and the stack is cleared.
       Returns event "took_penalty".

    B) Normal turn, no active penalty:
       Player draws exactly 1 card.
       - If drawn card is NOT playable: turn advances automatically ("draw_pass").
       - If drawn card IS playable: has_drawn=True, turn stays so player can call
         play_card or pass_turn ("drew_playable").
    """
    if state.phase != "playing":
        return ActionResult(False, error=f"Cannot draw: game is in '{state.phase}' phase.")

    player = state.get_player(player_id)
    if player is None:
        return ActionResult(False, error="Unknown player_id.")

    if state.current_player.player_id != player_id:
        return ActionResult(False, error="Not your turn.")

    if state.has_drawn:
        return ActionResult(
            False, error="Already drew this turn. Play the drawn card or call pass_turn."
        )

    # --- Case A: active penalty stack ---
    if state.pending_draw > 0:
        total = state.pending_draw
        _draw_cards(state, player, total)
        state.pending_draw = 0
        state.stacking_type = None
        state.advance_turn()
        return ActionResult(True, events=["took_penalty"])

    # --- Case B: normal single-card draw ---
    _draw_cards(state, player, 1)
    state.has_drawn = True

    drawn = player.hand[-1]
    if not drawn.is_playable_on(state.top_card, state.active_color, stacking_type=None):
        state.has_drawn = False
        state.advance_turn()
        return ActionResult(True, events=["draw_pass"])

    return ActionResult(True, events=["drew_playable"])


# ---------------------------------------------------------------------------
# pass_turn
# ---------------------------------------------------------------------------

def pass_turn(state: GameState, player_id: str) -> ActionResult:
    """
    End the current player's turn after drawing a playable card and choosing
    not to play it.  Only valid immediately after draw_card returned
    "drew_playable".
    """
    if state.phase != "playing":
        return ActionResult(False, error=f"Cannot pass: game is in '{state.phase}' phase.")

    if state.current_player.player_id != player_id:
        return ActionResult(False, error="Not your turn.")

    if not state.has_drawn:
        return ActionResult(False, error="Cannot pass without drawing first.")

    state.has_drawn = False
    state.advance_turn()
    return ActionResult(True, events=["pass"])
