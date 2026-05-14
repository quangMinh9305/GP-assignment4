# Custom UNO Online — Project Context

## Role
Act as an **Expert Game Developer and Network Engineer** throughout this project.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python Sockets |
| Frontend | Pygame |
| Architecture | Host-Authoritative Server-Client |

## Architecture Overview

- **Server** holds ground truth, validates all moves, broadcasts game state via JSON.
- **Clients** only render UI and send action requests — no local game logic authority.

## Core Game Rules

- 2–4 players, turn-based.
- Standard UNO deck: numbers 0–9, Skip, Reverse, +2, Wild, Wild +4.
- A card is legal to play if it matches the top discard by **Color**, **Number**, or **Action type**, or is a Wild/+4.
- If no legal move: draw 1 card; may play it immediately if legal.
- **Win condition**: empty hand legally (see "No Action Win" constraint below).

## Custom House Rules (Hard Constraints)

### Rule 0 — Hand Pass (on playing a 0)
- The player who plays a 0 chooses a **direction** (clockwise or counter-clockwise).
- All players **simultaneously** pass their **entire hand** in that direction.

### Rule 7 — Hand Swap (on playing a 7)
- The player who plays a 7 **selects one target opponent**.
- The two players **swap hands** completely.

### Rule 8 — Reaction Timer (on playing an 8)
- Triggers a **3-second countdown** on the server.
- **All clients** must send a reaction payload within 3 seconds.
- The **last player to react** (or whoever times out) **draws 2 cards**.
- Server handles timing strictly — client-side timing is display only.

### No Action Win
- A player **cannot win** by playing a Skip, Reverse, +2, Wild, or Wild +4 as their final card.
- The final winning card must be a **numbered card (0–9)**.

### Penalty Stacking
- If a draw penalty is active, the current player can only **stack an equal or greater penalty**:
  - Under a +2 penalty → can play another +2 **or** a +4.
  - Under a +4 penalty → can only play another +4.
- The **first player who cannot stack** draws the full accumulated total and loses their turn.

## Development Phases

Work is organized into phases. Always wait for explicit phase instructions before writing code.

## Communication Protocol

- All server↔client communication is **JSON over raw sockets**.
- Server broadcasts updated game state after every validated action.
- Clients send action request payloads; server accepts or rejects them.

## Key Implementation Notes

- The server is the single source of truth — never trust client-reported state.
- Rule 8 timing must be enforced server-side with a thread or async timer.
- Penalty stacking state must be tracked on the server across turns until resolved.
- "No Action Win" check must happen at the moment a card is played, not after.
