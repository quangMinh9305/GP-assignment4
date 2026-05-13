"""
Core data structures for Custom UNO Online.
All public classes are JSON-serializable via to_dict() / from_dict() pairs.
Server uses to_dict(); clients receive to_client_dict() which hides the draw pile
and other players' hands.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLORS = ("red", "yellow", "green", "blue")

# card_type values
TYPE_NUMBER        = "number"
TYPE_SKIP          = "skip"
TYPE_REVERSE       = "reverse"
TYPE_DRAW_TWO      = "draw_two"
TYPE_WILD          = "wild"
TYPE_WILD_DRAW_FOUR = "wild_draw_four"

WILD_TYPES   = (TYPE_WILD, TYPE_WILD_DRAW_FOUR)
ACTION_TYPES = (TYPE_SKIP, TYPE_REVERSE, TYPE_DRAW_TWO, TYPE_WILD, TYPE_WILD_DRAW_FOUR)

INITIAL_HAND_SIZE = 7


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------

@dataclass
class Card:
    """A single UNO card."""

    color: Optional[str]  # "red" | "yellow" | "green" | "blue" | None (wilds)
    card_type: str        # see TYPE_* constants above
    value: Optional[int]  # 0-9 for number cards; None for all others

    # --- Queries ---

    def is_wild(self) -> bool:
        return self.card_type in WILD_TYPES

    def is_action(self) -> bool:
        return self.card_type in ACTION_TYPES

    def is_legal_final_card(self) -> bool:
        """No-Action-Win rule: only a number card may end the game."""
        return self.card_type == TYPE_NUMBER

    def is_playable_on(
        self,
        top: Card,
        active_color: Optional[str],
        stacking_type: Optional[str],
    ) -> bool:
        """
        Return True if this card may legally be played.

        stacking_type — the card_type of the last penalty card in an active
        draw stack ("draw_two" | "wild_draw_four" | None).  When a stack is
        active the player may only counter with an equal-or-greater penalty:
          draw_two active  → draw_two or wild_draw_four allowed
          wild_draw_four active → only wild_draw_four allowed
        """
        if stacking_type == TYPE_WILD_DRAW_FOUR:
            return self.card_type == TYPE_WILD_DRAW_FOUR
        if stacking_type == TYPE_DRAW_TWO:
            return self.card_type in (TYPE_DRAW_TWO, TYPE_WILD_DRAW_FOUR)

        # Normal turn — wilds are always playable
        if self.is_wild():
            return True

        effective_color = active_color or top.color
        if self.color == effective_color:
            return True
        if self.card_type == top.card_type:
            # Number cards also require the same face value
            if self.card_type == TYPE_NUMBER:
                return self.value == top.value
            return True
        return False

    # --- Serialization ---

    def to_dict(self) -> Dict[str, Any]:
        return {"color": self.color, "card_type": self.card_type, "value": self.value}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Card:
        return cls(color=d["color"], card_type=d["card_type"], value=d.get("value"))

    def __str__(self) -> str:
        label = self.card_type.replace("_", " ").title()
        if self.card_type == TYPE_NUMBER:
            return f"{self.color} {self.value}"
        return label if self.is_wild() else f"{self.color} {label}"


# ---------------------------------------------------------------------------
# Deck
# ---------------------------------------------------------------------------

class Deck:
    """
    Standard 108-card UNO deck.

    Composition per color (×4 colors):
      1× 0, 2× 1-9, 2× Skip, 2× Reverse, 2× Draw Two  → 25 cards × 4 = 100
    Plus: 4× Wild, 4× Wild Draw Four                   →  8 cards
    Total: 108
    """

    def __init__(self) -> None:
        self.cards: List[Card] = []
        self._build()

    def _build(self) -> None:
        self.cards.clear()
        for color in COLORS:
            self.cards.append(Card(color, TYPE_NUMBER, 0))
            for v in range(1, 10):
                self.cards += [Card(color, TYPE_NUMBER, v), Card(color, TYPE_NUMBER, v)]
            for action in (TYPE_SKIP, TYPE_REVERSE, TYPE_DRAW_TWO):
                self.cards += [Card(color, action, None), Card(color, action, None)]
        for _ in range(4):
            self.cards.append(Card(None, TYPE_WILD, None))
            self.cards.append(Card(None, TYPE_WILD_DRAW_FOUR, None))

    def shuffle(self) -> None:
        random.shuffle(self.cards)

    def draw(self) -> Optional[Card]:
        return self.cards.pop() if self.cards else None

    def replenish_from_discard(self, discard_pile: List[Card]) -> None:
        """
        Called when the draw pile is empty.
        Shuffles all discard cards except the top one back into this deck.
        Wild cards have their chosen color reset to None.
        """
        if len(discard_pile) <= 1:
            return
        top = discard_pile[-1]
        self.cards = discard_pile[:-1]
        for card in self.cards:
            if card.is_wild():
                card.color = None
        random.shuffle(self.cards)
        discard_pile[:] = [top]

    def __len__(self) -> int:
        return len(self.cards)


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

@dataclass
class Player:
    player_id: str
    name: str
    hand: List[Card] = field(default_factory=list)
    is_connected: bool = True
    avatar_color: str = "#FF6B6B"  # Default red avatar color (anonymous)

    def hand_count(self) -> int:
        return len(self.hand)

    def has_legal_play(
        self,
        top: Card,
        active_color: Optional[str],
        stacking_type: Optional[str],
    ) -> bool:
        return any(c.is_playable_on(top, active_color, stacking_type) for c in self.hand)

    def to_dict(self, hide_hand: bool = False) -> Dict[str, Any]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "hand": [] if hide_hand else [c.to_dict() for c in self.hand],
            "hand_count": self.hand_count(),
            "is_connected": self.is_connected,
            "avatar_color": self.avatar_color,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Player:
        return cls(
            player_id=d["player_id"],
            name=d["name"],
            hand=[Card.from_dict(c) for c in d.get("hand", [])],
            is_connected=d.get("is_connected", True),
            avatar_color=d.get("avatar_color", "#FF6B6B"),
        )


# ---------------------------------------------------------------------------
# Rule8State
# ---------------------------------------------------------------------------

@dataclass
class Rule8State:
    """
    Tracks the 3-second reaction window triggered when a player plays an 8.
    Server starts a timer; every client must send a reaction payload.
    The last player to react (or whoever times out) draws 2 cards.
    """

    deadline: float                                           # Unix timestamp
    reacted: List[str] = field(default_factory=list)         # player_ids that reacted in time
    pending_players: List[str] = field(default_factory=list) # player_ids not yet reacted

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deadline": self.deadline,
            "reacted": list(self.reacted),
            "pending_players": list(self.pending_players),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Rule8State:
        return cls(
            deadline=d["deadline"],
            reacted=d.get("reacted", []),
            pending_players=d.get("pending_players", []),
        )


# ---------------------------------------------------------------------------
# GameState
# ---------------------------------------------------------------------------

@dataclass
class GameState:
    """
    Single source of truth for the entire game.
    Lives on the server.  Clients receive a sanitized snapshot via
    to_client_dict() / to_client_json() which omits the draw pile contents
    and other players' hands.
    """

    players: List[Player]
    draw_pile: List[Card]    # server-side only — never sent to clients
    discard_pile: List[Card]

    current_player_index: int = 0
    direction: int = 1       # 1 = clockwise, -1 = counter-clockwise

    # "waiting" → lobby open
    # "playing" → normal turn flow
    # "rule8_reaction" → waiting for all clients to react
    # "finished" → game over
    phase: str = "waiting"

    # Penalty-stacking state
    pending_draw: int = 0              # accumulated draws owed to the next player who can't stack
    stacking_type: Optional[str] = None  # card_type of the last penalty card ("draw_two" | "wild_draw_four")

    active_color: Optional[str] = None  # effective color after a wild is played
    winner_id: Optional[str] = None
    rule8_state: Optional[Rule8State] = None
    turn_number: int = 0
    has_drawn: bool = False  # True after draw_card; player may still play or pass

    # --- Convenience accessors ---

    @property
    def current_player(self) -> Player:
        return self.players[self.current_player_index]

    @property
    def top_card(self) -> Card:
        return self.discard_pile[-1]

    def get_player(self, player_id: str) -> Optional[Player]:
        return next((p for p in self.players if p.player_id == player_id), None)

    def next_index(self, steps: int = 1) -> int:
        n = len(self.players)
        return (self.current_player_index + self.direction * steps) % n

    def advance_turn(self, steps: int = 1) -> None:
        self.current_player_index = self.next_index(steps)
        self.turn_number += 1

    # --- Full serialization (server-side persistence / internal use) ---

    def to_dict(self) -> Dict[str, Any]:
        return {
            "players": [p.to_dict() for p in self.players],
            "draw_pile": [c.to_dict() for c in self.draw_pile],
            "discard_pile": [c.to_dict() for c in self.discard_pile],
            "current_player_index": self.current_player_index,
            "direction": self.direction,
            "phase": self.phase,
            "pending_draw": self.pending_draw,
            "stacking_type": self.stacking_type,
            "active_color": self.active_color,
            "winner_id": self.winner_id,
            "rule8_state": self.rule8_state.to_dict() if self.rule8_state else None,
            "turn_number": self.turn_number,
            "has_drawn": self.has_drawn,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> GameState:
        rule8 = Rule8State.from_dict(d["rule8_state"]) if d.get("rule8_state") else None
        return cls(
            players=[Player.from_dict(p) for p in d["players"]],
            draw_pile=[Card.from_dict(c) for c in d.get("draw_pile", [])],
            discard_pile=[Card.from_dict(c) for c in d["discard_pile"]],
            current_player_index=d["current_player_index"],
            direction=d["direction"],
            phase=d["phase"],
            pending_draw=d["pending_draw"],
            stacking_type=d.get("stacking_type"),
            active_color=d.get("active_color"),
            winner_id=d.get("winner_id"),
            rule8_state=rule8,
            turn_number=d["turn_number"],
            has_drawn=d.get("has_drawn", False),
        )

    @classmethod
    def from_json(cls, s: str) -> GameState:
        return cls.from_dict(json.loads(s))

    # --- Client-facing serialization ---

    def to_client_dict(self, viewer_id: str) -> Dict[str, Any]:
        """
        Returns a state snapshot safe to broadcast to a specific client.
        - draw_pile contents are hidden; only the count is sent.
        - Every player's hand is hidden except the viewer's own hand.
        """
        d = self.to_dict()
        d["draw_pile"] = []
        d["draw_pile_count"] = len(self.draw_pile)
        d["players"] = [
            p.to_dict(hide_hand=(p.player_id != viewer_id))
            for p in self.players
        ]
        return d

    def to_client_json(self, viewer_id: str) -> str:
        return json.dumps(self.to_client_dict(viewer_id))


# ---------------------------------------------------------------------------
# Initial deal
# ---------------------------------------------------------------------------

# Default avatar colors for players (assigned in order)
AVATAR_COLORS = [
    "#FF6B6B",  # Red
    "#4ECDC4",  # Teal
    "#45B7D1",  # Blue
    "#FFA07A",  # Light salmon
]

def deal_initial_state(player_names: List[str]) -> GameState:
    """
    Build a freshly shuffled, fully dealt GameState ready for play.

    - Players receive IDs "p0" through "p3" in order.
    - Each player is dealt INITIAL_HAND_SIZE (7) cards.
    - The starting discard card is always a plain number card to avoid
      ambiguity (a wild or action card as first discard would require
      special-casing every house rule simultaneously).
    """
    if not 2 <= len(player_names) <= 4:
        raise ValueError("UNO requires 2–4 players.")

    deck = Deck()
    deck.shuffle()

    players = [
        Player(
            player_id=f"p{i}",
            name=name,
            avatar_color=AVATAR_COLORS[i % len(AVATAR_COLORS)]
        )
        for i, name in enumerate(player_names)
    ]

    # Deal round-robin so each player receives cards interleaved, as in real UNO
    for _ in range(INITIAL_HAND_SIZE):
        for player in players:
            card = deck.draw()
            if card is None:
                raise RuntimeError("Deck exhausted during initial deal.")
            player.hand.append(card)

    # Draw the first discard — must be a number card
    start_card: Optional[Card] = None
    held_back: List[Card] = []
    while deck.cards:
        candidate = deck.draw()
        if candidate.card_type == TYPE_NUMBER:
            start_card = candidate
            break
        held_back.append(candidate)

    if start_card is None:
        raise RuntimeError("Could not find a valid starting card in the deck.")

    # Non-number cards that were skipped go back to the bottom of the draw pile
    deck.cards = held_back + deck.cards

    return GameState(
        players=players,
        draw_pile=deck.cards,
        discard_pile=[start_card],
        current_player_index=0,
        direction=1,
        phase="playing",
        active_color=start_card.color,
    )
