# 🃏 Custom UNO Online

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Custom House Rules](#custom-house-rules)
- [Networking Architecture](#networking-architecture)
- [Room & Connection System](#room--connection-system)
- [Host & Client Roles](#host--client-roles)
- [Disconnect Handling](#disconnect-handling)
- [Game State Validation](#game-state-validation)
- [User Interface](#user-interface)
- [Scoring System](#scoring-system)
- [How to Run](#how-to-run)
- [Tech Stack](#tech-stack)

---

## Overview

**Custom UNO Online** is a multiplayer turn-based card game supporting 2–4 players over a Local Area Network (LAN). The game follows the standard UNO ruleset extended with a set of mandatory custom house rules defined in the course specification.

The system uses a **host-authoritative architecture**: the host machine validates all actions, resolves all effects, and broadcasts the authoritative game state to all connected clients. Clients are never trusted as the final source of truth for any gameplay decision.

---

## Features

- 🌐 LAN multiplayer (2–4 players) via IP address or room code
- 🏠 Host room creation and management
- 🔄 Real-time synchronized game state across all clients
- 🃏 Full standard UNO card set
- ⚡ Custom house rules: Rule of 0, Rule of 7, Rule of 8, Stacking, Final Card Restriction
- 🔁 Host can invite all players to start a new match after a round ends
- 💔 Graceful disconnect handling mid-game
- 📊 End-of-round scoring screen

---

## Custom House Rules

### Rule of 0 — Hand Pass

- **Trigger:** Any player plays a `0` card.
- **Effect:** The player who played the card chooses a direction — **clockwise** or **counter-clockwise** — and all players simultaneously pass their entire hand in that chosen direction.
- **Turn Retention:** The player who initiated the pass keeps their current turn after the hand transfer is resolved.
- **Note:** This rule affects hand passing only. It does **not** change the actual turn order unless otherwise documented.
- **UI:** The interface clearly displays the chosen direction during and after the hand pass.

---

### Rule of 7 — Hand Swap

- **Trigger:** Any player plays a `7` card.
- **Effect:** The player who played the card must immediately choose one target player. Both players swap their **entire hands**.
- **Sync:** The swap result is broadcast to all connected clients simultaneously.
- **UI:** A target-selection prompt is shown to the active player. All other players can see the swap result.

---

### Rule of 8 — Reaction Event

- **Trigger:** Any player plays an `8` card.
- **Effect:** A **3-second reaction window** opens. All players (including the one who played the card) must click a reaction button within the window.
- **Penalty:** The **last player to respond** draws 2 cards.
- **No response:** Any player who fails to respond before the timer expires is treated as the last responder.
- **Tie (multiple non-responders):** All players who failed to respond draw 2 cards each.
- **Special case:** If **nobody** responds, everyone except the player who played the `8` card draws 2 cards.
- **Authority:** The **host** is the sole authority for determining the reaction event result and assigning penalties.

---

### Stacking Rule — +2 and +4 Accumulation

When a Draw Two (`+2`) or Wild Draw Four (`+4`) is played, the next player may **stack** another penalty card to pass the accumulated penalty forward.

**Valid stacks:**

| Previous Card | Allowed Response | Accumulated Total |
|---------------|-----------------|-------------------|
| +2            | +2              | +4                |
| +2            | +4              | +6 *(see note)*   |
| +4            | +4              | +8                |

> **Note on +4 stacking onto +2:** Per the project specification, stacking is only valid if the new penalty value is **greater than or equal to** the previous one. Therefore `+4` played on `+2` is valid. However, `+2` played on `+4` is **invalid**.  
> This differs slightly from the rules document where `+2 on +4` was listed as valid — the **specification document takes precedence**.

**Resolution:**  
If a player cannot or chooses not to continue stacking, they must draw the **full accumulated penalty** and lose their turn.

**Example:**
```
Player A plays +2
Player B plays +2  → total pending: 4
Player C plays +4  → total pending: 8
Player D cannot stack → Player D draws 8 cards and loses turn
```

---

### Final Card Restriction — No Win with Action Card

A player **cannot win** the game by playing an action or wild card as their final card.

**Non-winning final cards:**
- Skip
- Reverse
- Draw Two (+2)
- Wild
- Wild Draw Four (+4)

**Rule behavior:**
- If a player has exactly **one card left** and it is one of the above, that card is treated as an **illegal final play**.
- The player must either play a different legal card, or draw if no legal alternative exists.
- To win legally, the last remaining card **must be a number card (0–9)**.

---

### Reverse in 2-Player Games

In a **2-player match**, the `Reverse` card is treated as a **Skip**: it cancels the opponent's turn and the current player immediately takes another turn. This behavior is consistent with common UNO 2-player variants and is applied automatically by the host.

---

## Networking Architecture

The game uses a **host-client model over sockets** within a LAN environment.

```
┌─────────────────────────────────────────────────────┐
│                      HOST                           │
│  - Manages game state                               │
│  - Validates all actions                            │
│  - Resolves card effects                            │
│  - Broadcasts state to all clients                  │
│  - Controls draw pile, discard pile, turn order     │
└───────────────┬─────────────────────────────────────┘
                │ Authoritative state broadcast
     ┌──────────┼──────────┬──────────┐
     ▼          ▼          ▼          ▼
 Client 1   Client 2   Client 3   Client 4
 (sends     (sends     (sends     (sends
 actions)   actions)   actions)   actions)
```

- Clients **send action requests** (play card, draw card, choose color, select target, react to card 8).
- The host **validates** each action and either accepts or rejects it.
- After each valid action, the host **broadcasts** the updated game state to all connected clients.
- Clients **never** modify game state independently.

---

## Room & Connection System

### Connecting

Players on the **same LAN** can connect using either:
- The **host's IP address**
- A **room code** generated when the room is created

### Starting a Match

- The **host** controls when the match begins.
- The host can start the game once at least **2 players** have joined (maximum **4 players**).

### Play Again

After a round ends, the **host** can send a "Play Again" invitation to all players currently in the room. Each client can choose to accept or decline. If accepted by all remaining players, a new match begins with the same room configuration.

---

## Host & Client Roles

| Responsibility                          | Host | Client |
|-----------------------------------------|------|--------|
| Shuffle and deal cards                  | ✅   | ❌     |
| Determine turn order                    | ✅   | ❌     |
| Validate card legality                  | ✅   | ❌     |
| Resolve card effects (0, 7, 8, +2, +4) | ✅   | ❌     |
| Check win condition                     | ✅   | ❌     |
| Manage draw/discard pile                | ✅   | ❌     |
| Broadcast game state                    | ✅   | ❌     |
| Send action requests                    | ✅   | ✅     |
| Render UI from received state           | ✅   | ✅     |
| Start / restart match                   | ✅   | ❌     |

---

## Disconnect Handling

| Scenario                                    | Behavior                                                                 |
|---------------------------------------------|--------------------------------------------------------------------------|
| **1 player disconnects** (out of 3–4)       | Game continues. The disconnected player's turn is skipped automatically. |
| **All but 1 player disconnect**             | The remaining player is immediately declared the **winner**.             |
| **Host disconnects**                        | The room is **automatically closed**. All clients are notified and returned to the main menu. |
| **Player disconnects before game starts**   | The player is removed from the lobby. The host may still start if ≥ 2 remain. |

> **Note:** When a player disconnects mid-game, their cards are removed from play. Pending penalties targeting a disconnected player are voided.

---

## Game State Validation

The host validates all of the following before accepting any action:

- ✅ It is actually the requesting player's turn
- ✅ The selected card is in the player's hand
- ✅ The card is legal to play (color, number, type match, or wild)
- ✅ The final card restriction (action card cannot be used to win)
- ✅ The stacking legality (+2/+4 chain rules)
- ✅ The target of a card-7 swap is a valid, connected player
- ✅ A reaction event (card 8) is currently active before accepting reaction input
- ✅ Each client submits at most one reaction during a card-8 event
- ✅ The correct player is responding to a pending stacked penalty

**Draw pile exhaustion:** When the draw pile is empty, the discard pile (except the top card) is shuffled and reused as the new draw pile.

---

## User Interface

The game interface always displays:

| Element                              | Description                                              |
|--------------------------------------|----------------------------------------------------------|
| **Player's hand**                    | All cards in the current player's hand                   |
| **Opponent card counts**             | Number of cards each opponent holds                      |
| **Discard pile top card**            | The most recently played card                            |
| **Turn direction**                   | Clockwise or counter-clockwise indicator                 |
| **Current turn indicator**           | Clearly highlights whose turn it is                      |
| **Pending draw penalty**             | Total accumulated penalty awaiting resolution            |
| **Color selection prompt**           | Shown after playing a Wild card                          |
| **Target selection prompt**          | Shown when card 7 is played                              |
| **Reaction countdown (card 8)**      | 3-second timer + reaction button visible to all players  |
| **Hand pass direction (card 0)**     | Shows chosen direction during the hand pass              |
| **End-of-game result screen**        | Winner announcement + score summary                      |

---

## Scoring System

At the end of each round, points are tallied from **cards remaining in opponents' hands**:

| Card Type                              | Point Value        |
|----------------------------------------|--------------------|
| Number Cards (0–9)                     | Face value (0–9)   |
| Action Cards (Skip, Reverse, Draw Two) | 20 points each     |
| Wild Cards (Wild, Wild Draw Four)      | 50 points each     |

The **winner** of the round accumulates points from all opponents' remaining hands.

---

## How to Run

### Requirements

> _(Fill in based on your tech stack, e.g., Python 3.11+, Node.js 20+, etc.)_

```bash
python -m venv venv
# for Windows
venv/Scripts/activate

# for MacOs/Linux
source venv/bin/activate

pip install -r requirements.txt

python main_client.py # The host will create room and the player will join in that room by IP or room code
```

### Joining a Room

1. The host launches the game and creates a room.
2. The host shares their **IP address** or the generated **room code** with other players.
3. Clients enter the IP or room code on the join screen.
4. Once all players have joined, the host presses **Start Game**.

---

## Tech Stack

| Component      | Technology Used         |
|----------------|-------------------------|
| Language       | _(e.g., Python / JS)_   |
| Networking     | _(e.g., TCP Sockets)_   |
| UI / Rendering | _(e.g., Pygame / React)_|
| Serialization  | _(e.g., JSON)_          |

---

## Notes

- This project was developed as part of the **SEM252 Game Programming** course.
- The host-authoritative model ensures consistent game state across all clients regardless of network latency.
- The project does not implement an AI opponent (bot) as it is not required by the MVP specification.
