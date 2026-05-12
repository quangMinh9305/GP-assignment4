"""
GameClient — Pygame UI for Custom UNO Online (Phase 5 + 6).

UI phases
---------
main_menu     : title screen; name input; Create / Join buttons
create_room   : host starts server + discovery; shows room code
join_room     : enter room code or IP:port to connect
lobby         : waiting for host to start; room code shown in corner
playing       : normal turn flow
rule8_reaction: Rule 8 countdown overlay
finished      : game-over screen
error         : lost connection
"""
from __future__ import annotations

import math
import os
import random
import string
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pygame

from client.asset_manager import AssetManager, CARD_W, CARD_H
from client.network_client import NetworkClient
from game.models import Card

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
WIN_W, WIN_H = 1280, 720

HEADER_H  = 55
OPP_Y     = HEADER_H
OPP_H     = 140
CENTER_Y  = OPP_Y + OPP_H        # 195
CENTER_H  = 265
CTRL_Y    = CENTER_Y + CENTER_H  # 460
CTRL_H    = 50
HAND_Y    = CTRL_Y + CTRL_H      # 510
HAND_H    = WIN_H - HAND_Y       # 210
HAND_CARD_Y = HAND_Y + (HAND_H - CARD_H) // 2  # ≈552

DISCARD_W, DISCARD_H = 120, 168
OPP_CW, OPP_CH = 50, 70
DRAW_CX    = 380
DISCARD_CX = 640
STATUS_X   = 810
_CMY = CENTER_Y + CENTER_H // 2  # 327

DRAW_PILE_RECT = pygame.Rect(DRAW_CX - CARD_W // 2,   _CMY - CARD_H // 2,   CARD_W,    CARD_H)
DISCARD_RECT   = pygame.Rect(DISCARD_CX - DISCARD_W // 2, _CMY - DISCARD_H // 2, DISCARD_W, DISCARD_H)

GAME_PORT      = 5555
DISCOVERY_PORT = 5556

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
BG_FALLBACK   = (0, 100, 40)
HEADER_COL    = (10, 30, 15, 200)      # semi-transparent dark green
TEXT_COL      = (240, 240, 240)
DIM_ALPHA     = 110
HIGHLIGHT_COL = (255, 220, 50)
HOVER_COL     = (255, 255, 255)
BTN_COL       = (30, 120, 60)
BTN_HOV_COL   = (50, 160, 80)
BTN_OFF_COL   = (50, 70, 50)
ERR_COL       = (210, 60, 60)
INFO_COL      = (60, 140, 210)
WARN_COL      = (210, 170, 30)
OK_COL        = (60, 180, 80)

UNO_COLORS: Dict[str, Tuple] = {
    "red":    (220, 50,  50),
    "yellow": (230, 200,  0),
    "green":  (50,  180, 70),
    "blue":   (50,  100, 220),
}

NOTIF_BG       = (18, 18, 25, 220)
NOTIF_COLORS   = {"info": INFO_COL, "success": OK_COL, "error": ERR_COL, "warn": WARN_COL}
RULE8_BG       = (10, 5, 20, 210)
RULE8_BORDER   = (200, 50, 255)


# ---------------------------------------------------------------------------
# Helper dataclasses
# ---------------------------------------------------------------------------

@dataclass
class _Anim:
    """A card surface flying from start to end over `duration` seconds."""
    surf:     pygame.Surface
    start:    Tuple[int, int]
    end:      Tuple[int, int]
    duration: float
    delay:    float = 0.0
    elapsed:  float = 0.0

    @property
    def active(self) -> bool:
        return self.elapsed >= self.delay

    @property
    def done(self) -> bool:
        return self.elapsed >= self.delay + self.duration

    def pos(self) -> Tuple[int, int]:
        if not self.active:
            return self.start
        t = min(1.0, (self.elapsed - self.delay) / self.duration)
        t = t * t * (3 - 2 * t)           # smoothstep easing
        x = self.start[0] + (self.end[0] - self.start[0]) * t
        y = self.start[1] + (self.end[1] - self.start[1]) * t
        return (int(x), int(y))


@dataclass
class _Notif:
    """A transient notification banner."""
    message: str
    ntype:   str    # "info" | "success" | "error" | "warn"
    ttl:     float = 4.0
    elapsed: float = 0.0
    SLIDE_IN  = 0.25
    FADE_OUT  = 0.35

    @property
    def done(self) -> bool:
        return self.elapsed >= self.ttl

    @property
    def alpha(self) -> int:
        if self.elapsed < self.SLIDE_IN:
            return int(255 * self.elapsed / self.SLIDE_IN)
        if self.elapsed > self.ttl - self.FADE_OUT:
            return int(255 * (self.ttl - self.elapsed) / self.FADE_OUT)
        return 255

    @property
    def slide_x(self) -> int:
        """Offset from resting position (slides in from the right)."""
        if self.elapsed < self.SLIDE_IN:
            t = self.elapsed / self.SLIDE_IN
            t = t * t * (3 - 2 * t)
            return int((1 - t) * 340)
        return 0


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class _Button:
    def __init__(self, rect: pygame.Rect, label: str,
                 col: Tuple = BTN_COL, hov: Tuple = BTN_HOV_COL) -> None:
        self.rect    = rect
        self.label   = label
        self.col     = col
        self.hov     = hov
        self.visible = True
        self.enabled = True

    def draw(self, surf: pygame.Surface, font: pygame.font.Font,
             mouse: Tuple, alpha: int = 255) -> None:
        if not self.visible:
            return
        c = BTN_OFF_COL if not self.enabled else (
            self.hov if self.rect.collidepoint(mouse) else self.col)
        btn_s = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        btn_s.fill((*c, alpha))
        pygame.draw.rect(btn_s, (*TEXT_COL, alpha), btn_s.get_rect(), 2, border_radius=8)
        surf.blit(btn_s, self.rect.topleft)
        txt = font.render(self.label, True, TEXT_COL)
        surf.blit(txt, txt.get_rect(center=self.rect.center))

    def clicked(self, pos: Tuple) -> bool:
        return bool(self.visible and self.enabled and self.rect.collidepoint(pos))


class _TextInput:
    """Single-line text entry with blinking cursor."""

    def __init__(self, rect: pygame.Rect, placeholder: str = "",
                 max_len: int = 40) -> None:
        self.rect        = rect
        self.placeholder = placeholder
        self.max_len     = max_len
        self.text        = ""
        self.active      = False
        self._cur_t      = 0.0
        self._cur_vis    = True
        self._transform  = str  # identity; override to str.upper for code inputs

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(event.pos)
        elif event.type == pygame.KEYDOWN and self.active:
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif event.unicode and len(self.text) < self.max_len:
                self.text += self._transform(event.unicode)

    def update(self, dt: float) -> None:
        self._cur_t += dt
        if self._cur_t >= 0.5:
            self._cur_vis = not self._cur_vis
            self._cur_t = 0.0

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        bg = (35, 50, 40) if self.active else (25, 35, 28)
        pygame.draw.rect(surf, bg, self.rect, border_radius=6)
        border = (80, 200, 100) if self.active else (50, 80, 55)
        pygame.draw.rect(surf, border, self.rect, 2, border_radius=6)

        display = self.text or self.placeholder
        col     = TEXT_COL if self.text else (100, 120, 105)
        txt = font.render(display, True, col)
        tx  = self.rect.x + 10
        ty  = self.rect.centery - txt.get_height() // 2
        # Clip to box
        clip = surf.get_clip()
        surf.set_clip(self.rect.inflate(-4, -4))
        surf.blit(txt, (tx, ty))
        surf.set_clip(clip)

        if self.active and self.text and self._cur_vis:
            cx = tx + txt.get_width() + 2
            pygame.draw.line(surf, TEXT_COL,
                             (cx, ty + 2), (cx, ty + txt.get_height() - 2), 2)


# ---------------------------------------------------------------------------
# GameClient
# ---------------------------------------------------------------------------

class GameClient:
    def __init__(self, assets_root: str) -> None:
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.init()
        pygame.display.set_caption("Custom UNO Online")
        self._screen     = pygame.display.set_mode((WIN_W, WIN_H))
        self._clock      = pygame.time.Clock()
        self._assets_root = assets_root

        # Fonts
        self._fxl  = pygame.font.SysFont("Arial", 34, bold=True)
        self._flg  = pygame.font.SysFont("Arial", 24, bold=True)
        self._fmd  = pygame.font.SysFont("Arial", 17)
        self._fsm  = pygame.font.SysFont("Arial", 13)

        # Card assets (requires display to be set first)
        self._assets = AssetManager(assets_root)

        # Pre-baked surfaces
        self._dim_surf = pygame.Surface((CARD_W, CARD_H), pygame.SRCALPHA)
        self._dim_surf.fill((0, 0, 0, DIM_ALPHA))

        # Background images
        self._bg: Dict[str, Optional[pygame.Surface]] = {
            "menu":    self._load_bg("menu_bg.jpg"),
            "waiting": self._load_bg("waiting_room_bg.jpg"),
            "playing": self._load_bg("playing_bg.jpg"),
        }

        # Music
        self._music_playing = False
        self._music_path = os.path.join(assets_root, "bg", "music.mp3")

        # Network (created lazily)
        self._net:       Optional[NetworkClient]  = None
        self._pending_net: Optional[NetworkClient] = None  # from discovery thread
        self._discover_msg: Optional[str]          = None  # error or None
        self._discovering = False

        # Hosted server objects
        self._server    = None
        self._discovery = None

        # Room info
        self._room_code: str  = ""
        self._room_ip:   str  = ""
        self._room_port: int  = GAME_PORT

        # Game state
        self._my_id:       str  = ""
        self._pending_name: str  = ""
        self._state:       dict = {}
        self._lobby:       list = []
        self._ui_phase:    str  = "main_menu"
        self._overlay:   str  = ""
        self._sel_idx:   int  = -1
        self._hover_idx: int  = -1

        # Pending play info (for play animation)
        self._pending_play_card:     Optional[dict]          = None
        self._pending_play_src_rect: Optional[pygame.Rect]   = None
        self._last_action: str = ""

        # Animations
        self._anims:       List[_Anim]   = []
        self._play_anim:   Optional[_Anim] = None
        self._deal_phase:  str   = ""   # "" | shuffle | deal
        self._deal_elapsed: float = 0.0
        self._deal_state_ready = False
        self._deal_queue:  List[_Anim] = []
        self._deal_emit_t: float = 0.0
        self._deal_emit_i: int   = 0
        self._deal_total:  int   = 0

        # Notifications (stacked, newest first)
        self._notifs: List[_Notif] = []

        # Buttons
        self._btn_create = _Button(pygame.Rect(WIN_W//2 - 155, 390, 140, 48), "Create Room")
        self._btn_join   = _Button(pygame.Rect(WIN_W//2 + 15,  390, 140, 48), "Join Room")
        self._btn_back   = _Button(pygame.Rect(40, 40, 100, 38), "< Back",
                                   col=(50, 80, 55), hov=(70, 110, 75))
        self._btn_connect = _Button(pygame.Rect(WIN_W//2 - 80, 440, 160, 46), "Connect")
        self._btn_start   = _Button(pygame.Rect(WIN_W//2 - 100, 440, 200, 50), "Start Game")
        self._btn_draw    = _Button(pygame.Rect(40,  CTRL_Y + 6, 160, 38), "Draw Card")
        self._btn_pass    = _Button(pygame.Rect(215, CTRL_Y + 6, 160, 38), "Pass Turn")
        self._btn_react   = _Button(pygame.Rect(WIN_W//2 - 110, CTRL_Y, 220, 50),
                                    "  React!  ",
                                    col=(160, 30, 30), hov=(210, 55, 55))

        # Color-pick overlay buttons
        _bw, _bh = 100, 80
        _cx = WIN_W // 2 - (_bw * 4 + 30) // 2
        _cy = WIN_H // 2 - _bh // 2
        self._color_btns: Dict[str, _Button] = {
            c: _Button(pygame.Rect(_cx + i * (_bw + 10), _cy, _bw, _bh),
                       c.capitalize())
            for i, c in enumerate(("red", "yellow", "green", "blue"))
        }
        # Direction-pick overlay
        self._dir_btns: Dict[int, _Button] = {
            1:  _Button(pygame.Rect(WIN_W//2 - 165, WIN_H//2 - 30, 150, 60), "Clockwise"),
            -1: _Button(pygame.Rect(WIN_W//2 + 15,  WIN_H//2 - 30, 150, 60), "Counter-CW"),
        }
        self._swap_btns: Dict[str, _Button] = {}

        # Text inputs
        self._inp_name    = _TextInput(pygame.Rect(WIN_W//2 - 160, 320, 320, 46),
                                       "Your name…", max_len=20)
        self._inp_room    = _TextInput(pygame.Rect(WIN_W//2 - 160, 350, 320, 46),
                                       "Room code (4 letters) or IP:Port", max_len=30)
        self._inp_room._transform = str.upper
        self._inp_name.active = True  # focused by default

    # -----------------------------------------------------------------------
    # Background / music helpers
    # -----------------------------------------------------------------------

    def _load_bg(self, filename: str) -> Optional[pygame.Surface]:
        path = os.path.join(self._assets_root, "bg", filename)
        if not os.path.exists(path):
            return None
        try:
            img = pygame.image.load(path).convert()
            return pygame.transform.smoothscale(img, (WIN_W, WIN_H))
        except Exception:
            return None

    def _draw_bg(self, key: str) -> None:
        bg = self._bg.get(key)
        if bg:
            self._screen.blit(bg, (0, 0))
        else:
            self._screen.fill(BG_FALLBACK)

    def _start_music(self) -> None:
        if self._music_playing:
            return
        if not os.path.exists(self._music_path):
            return
        try:
            pygame.mixer.music.load(self._music_path)
            pygame.mixer.music.play(-1)
            pygame.mixer.music.set_volume(0.45)
            self._music_playing = True
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Networking helpers
    # -----------------------------------------------------------------------

    def _connect(self, host: str, port: int) -> None:
        try:
            self._net = NetworkClient(host, port)
        except ConnectionRefusedError:
            self._add_notif(f"Cannot connect to {host}:{port}", "error")

    def _host_game(self) -> None:
        from server.server import UNOServer
        from server.discovery import DiscoveryServer, get_lan_ip

        self._room_code = "".join(random.choices(string.ascii_uppercase, k=4))
        self._room_port = GAME_PORT
        self._room_ip   = get_lan_ip()

        self._server = UNOServer("0.0.0.0", self._room_port, min_players=2, max_players=4)
        t = threading.Thread(target=self._server.run, daemon=True, name="uno-server")
        t.start()
        time.sleep(0.12)   # let the socket bind

        self._discovery = DiscoveryServer(self._room_code, self._room_port)
        self._discovery.start()

        self._connect("127.0.0.1", self._room_port)
        self._add_notif(f"Room created: {self._room_code}", "success")

    def _discover_and_connect(self, code: str) -> None:
        from server.discovery import discover_room
        result = discover_room(code, timeout=3.0)
        if result:
            host, port = result
            try:
                self._pending_net = NetworkClient(host, port)
                self._discover_msg = None
            except Exception:
                self._discover_msg = f"Found room but could not connect to {host}:{port}"
        else:
            self._discover_msg = f"Room '{code}' not found on this network."
        self._discovering = False

    def _try_connect_input(self) -> None:
        name = self._inp_name.text.strip() or "Player"
        text = self._inp_room.text.strip()
        if not text:
            self._add_notif("Enter a room code or IP:port.", "warn")
            return

        # Pure alpha ≤ 6 chars → treat as room code
        if len(text) <= 6 and text.isalpha():
            self._discovering = True
            t = threading.Thread(
                target=self._discover_and_connect,
                args=(text.upper(),), daemon=True, name="discover")
            t.start()
        else:
            # IP:port or plain IP
            if ":" in text:
                parts = text.rsplit(":", 1)
                host  = parts[0]
                try:
                    port = int(parts[1])
                except ValueError:
                    port = GAME_PORT
            else:
                host, port = text, GAME_PORT
            self._connect(host, port)
        self._pending_name = name

    # -----------------------------------------------------------------------
    # Animations
    # -----------------------------------------------------------------------

    def _start_deal_sequence(self) -> None:
        """Kick off shuffle then deal animation using current state."""
        self._deal_phase   = "shuffle"
        self._deal_elapsed = 0.0

    def _start_play_anim(self) -> None:
        if self._pending_play_card is None or self._pending_play_src_rect is None:
            return
        src  = self._pending_play_src_rect.topleft
        dest = (DISCARD_RECT.centerx - CARD_W // 2,
                DISCARD_RECT.centery - CARD_H // 2)
        self._play_anim = _Anim(
            surf=self._assets.get(self._pending_play_card),
            start=src, end=dest, duration=0.35)
        self._pending_play_card     = None
        self._pending_play_src_rect = None

    def _setup_deal_anims(self) -> None:
        """Build the deal animation queue from self._state."""
        players = self._state.get("players", [])
        n       = len(players)
        if n == 0:
            return

        # Destinations per player
        my_idx = next((i for i, p in enumerate(players)
                        if p["player_id"] == self._my_id), 0)
        dests  = []
        opp_players = [p for p in players if p["player_id"] != self._my_id]
        opp_n = len(opp_players)

        dest_map: Dict[str, Tuple] = {}
        dest_map[self._my_id] = (WIN_W // 2, HAND_CARD_Y + CARD_H // 2)
        for k, opp in enumerate(opp_players):
            sec_w = WIN_W // opp_n
            dest_map[opp["player_id"]] = (sec_w * k + sec_w // 2,
                                          OPP_Y + OPP_H // 2)

        hand_sz = 7  # INITIAL_HAND_SIZE
        self._deal_queue = []
        src = (DISCARD_CX, _CMY)
        back = self._assets.get_back()
        delay = 0.0
        for card_i in range(hand_sz):
            for player in players:
                dest = dest_map.get(player["player_id"], (WIN_W // 2, WIN_H // 2))
                self._deal_queue.append(
                    _Anim(back, src, dest, duration=0.38, delay=delay))
                delay += 0.07
        self._deal_emit_i = 0
        self._deal_total  = len(self._deal_queue)

    # -----------------------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------------------

    def _add_notif(self, msg: str, ntype: str = "info", ttl: float = 4.0) -> None:
        self._notifs.insert(0, _Notif(msg, ntype, ttl=ttl))
        if len(self._notifs) > 4:
            self._notifs = self._notifs[:4]

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    def run(self) -> None:
        running = True
        while running:
            dt = self._clock.tick(60) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                    break
                self._handle_event(event)

            self._process_messages()
            self._update(dt)
            self._render()
            pygame.display.flip()

        if self._net:
            self._net.close()
        if self._server:
            self._server.stop()
        if self._discovery:
            self._discovery.stop()
        pygame.quit()

    # -----------------------------------------------------------------------
    # Event handling
    # -----------------------------------------------------------------------

    def _handle_event(self, event: pygame.event.Event) -> None:
        # Always forward key events to text inputs
        self._inp_name.handle_event(event)
        self._inp_room.handle_event(event)

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                self._handle_return_key()

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._handle_click(event.pos)
        elif event.type == pygame.MOUSEMOTION:
            if self._ui_phase == "playing" and not self._overlay:
                self._hover_idx = self._card_at(event.pos)
            else:
                self._hover_idx = -1

    def _handle_return_key(self) -> None:
        if self._ui_phase == "main_menu":
            pass   # buttons only
        elif self._ui_phase == "join_room":
            self._try_connect_input()

    def _handle_click(self, pos: tuple) -> None:
        phase = self._ui_phase

        # ── Overlays ────────────────────────────────────────────────────────
        if self._overlay == "color_pick":
            for color, btn in self._color_btns.items():
                if btn.clicked(pos):
                    self._net.send(action="play_card",
                                   card_index=self._sel_idx, chosen_color=color)
                    self._overlay = ""
                    self._sel_idx = -1
            return

        if self._overlay == "direction_pick":
            for direction, btn in self._dir_btns.items():
                if btn.clicked(pos):
                    self._net.send(action="play_card",
                                   card_index=self._sel_idx, pass_direction=direction)
                    self._overlay = ""
                    self._sel_idx = -1
            return

        if self._overlay == "swap_pick":
            for pid, btn in self._swap_btns.items():
                if btn.clicked(pos):
                    self._net.send(action="play_card",
                                   card_index=self._sel_idx, swap_target_id=pid)
                    self._overlay = ""
                    self._sel_idx = -1
            return

        # ── Main menu ────────────────────────────────────────────────────────
        if phase == "main_menu":
            if self._btn_create.clicked(pos):
                name = self._inp_name.text.strip() or "Player"
                self._pending_name = name
                self._ui_phase = "create_room"
                self._host_game()
            elif self._btn_join.clicked(pos):
                self._ui_phase = "join_room"
                self._inp_room.active = True
            return

        # ── Join room ────────────────────────────────────────────────────────
        if phase == "join_room":
            if self._btn_back.clicked(pos):
                self._ui_phase = "main_menu"
            elif self._btn_connect.clicked(pos):
                self._try_connect_input()
            return

        # ── Create room ──────────────────────────────────────────────────────
        if phase == "create_room":
            if self._btn_back.clicked(pos):
                if self._server:
                    self._server.stop()
                if self._discovery:
                    self._discovery.stop()
                if self._net:
                    self._net.close()
                    self._net = None
                self._ui_phase = "main_menu"
            return

        # ── Lobby ────────────────────────────────────────────────────────────
        if phase == "lobby":
            if self._btn_start.clicked(pos):
                self._net.send(action="start_game")
            return

        # ── Rule 8 reaction ──────────────────────────────────────────────────
        if phase == "rule8_reaction":
            if self._btn_react.clicked(pos):
                self._net.send(action="react")
            return

        # ── Playing ──────────────────────────────────────────────────────────
        if phase != "playing":
            return
        if self._deal_phase:
            return     # block input during deal animation

        if self._btn_draw.clicked(pos):
            self._last_action = "draw_card"
            self._net.send(action="draw_card")
            return
        if self._btn_pass.clicked(pos):
            self._net.send(action="pass_turn")
            return
        if (DRAW_PILE_RECT.collidepoint(pos)
                and self._is_my_turn()
                and not self._state.get("has_drawn")):
            self._last_action = "draw_card"
            self._net.send(action="draw_card")
            return

        idx = self._card_at(pos)
        if idx >= 0 and self._is_my_turn():
            self._try_play(idx)

    def _try_play(self, idx: int) -> None:
        hand = self._my_hand()
        if idx >= len(hand):
            return
        card = hand[idx]

        if not self._is_playable(card):
            self._add_notif("That card can't be played right now.", "warn", ttl=2.5)
            return

        ct  = card["card_type"]
        val = card.get("value")
        rects = self._hand_rects(len(hand))
        if idx < len(rects):
            self._pending_play_card     = card
            self._pending_play_src_rect = rects[idx]

        if ct in ("wild", "wild_draw_four"):
            self._sel_idx = idx
            self._overlay = "color_pick"
            return
        if ct == "number" and val == 0:
            self._sel_idx = idx
            self._overlay = "direction_pick"
            return
        if ct == "number" and val == 7:
            self._sel_idx = idx
            self._build_swap_btns()
            self._overlay = "swap_pick"
            return

        self._last_action = "play_card"
        self._net.send(action="play_card", card_index=idx)

    def _build_swap_btns(self) -> None:
        opps  = [p for p in self._state.get("players", [])
                 if p["player_id"] != self._my_id]
        bw, bh, gap = 160, 50, 15
        total_w = len(opps) * bw + max(0, len(opps) - 1) * gap
        sx = (WIN_W - total_w) // 2
        cy = WIN_H // 2 - bh // 2
        self._swap_btns = {
            o["player_id"]: _Button(
                pygame.Rect(sx + i * (bw + gap), cy, bw, bh), o["name"])
            for i, o in enumerate(opps)
        }

    # -----------------------------------------------------------------------
    # Message processing
    # -----------------------------------------------------------------------

    def _process_messages(self) -> None:
        # Collect result from discovery background thread
        if self._pending_net is not None:
            self._net = self._pending_net
            self._pending_net = None
            if self._pending_name:
                pass   # name sent after welcome
        if self._discover_msg is not None:
            self._add_notif(self._discover_msg, "error")
            self._discover_msg = None

        if self._net is None:
            return

        for msg in self._net.poll():
            mtype = msg.get("type")

            if mtype == "welcome":
                self._my_id = msg["player_id"]
                name = self._pending_name or "Player"
                self._net.send(action="set_name", name=name)
                self._ui_phase = "lobby"
                self._add_notif("Connected to server!", "success")

            elif mtype == "lobby_update":
                self._lobby = msg.get("players", [])

            elif mtype == "game_started":
                self._ui_phase      = "playing"
                self._overlay       = ""
                self._sel_idx       = -1
                self._deal_phase    = "shuffle"
                self._deal_elapsed  = 0.0
                self._deal_state_ready = False
                self._start_music()
                self._add_notif("Game started! Dealing cards…", "info")

            elif mtype == "state_update":
                self._state = msg["state"]
                sp = self._state.get("phase", "")
                if not self._deal_phase:
                    if sp == "finished":
                        self._ui_phase = "finished"
                    elif sp == "rule8_reaction":
                        self._ui_phase = "rule8_reaction"
                    else:
                        self._ui_phase = "playing"
                else:
                    if not self._deal_state_ready:
                        self._deal_state_ready = True

                if not self._is_my_turn() and self._overlay:
                    self._overlay = ""
                    self._sel_idx = -1

            elif mtype == "action_result":
                if not msg["success"]:
                    self._add_notif(msg.get("error", "Action failed."), "error", ttl=3.0)
                    self._overlay = ""
                    self._sel_idx = -1
                    self._pending_play_card = None
                else:
                    evts = msg.get("events", [])
                    if self._last_action == "play_card" and self._pending_play_card:
                        self._start_play_anim()
                    if "rule8_triggered" in evts:
                        self._add_notif("Rule 8 triggered! Everyone react!", "warn", ttl=5.0)
                    if "hand_swap" in evts:
                        self._add_notif("Hands swapped!", "info")
                    if "hand_pass" in evts:
                        self._add_notif("Hands passed around the table!", "info")
                    if "took_penalty" in evts:
                        self._add_notif("You took the penalty draw.", "warn")
                self._last_action = ""

            elif mtype == "rule8_resolved":
                losers = msg.get("losers", [])
                names  = [self._player_name(p) for p in losers]
                self._add_notif(
                    f"Rule 8 resolved — {', '.join(names)} draw 2 cards!", "warn", ttl=5.0)

            elif mtype == "game_over":
                wname = msg.get("winner_name", "?")
                self._add_notif(f"Game over!  {wname} wins!", "success", ttl=8.0)
                self._ui_phase = "finished"

            elif mtype == "player_disconnected":
                pid = msg.get("player_id", "")
                self._add_notif(f"{self._player_name(pid)} disconnected.", "warn")

            elif mtype == "disconnected":
                self._ui_phase = "error"
                self._add_notif("Lost connection to the server.", "error", ttl=9999)

            elif mtype == "error":
                self._add_notif(msg.get("message", "Server error."), "error")

    # -----------------------------------------------------------------------
    # Per-frame update
    # -----------------------------------------------------------------------

    def _update(self, dt: float) -> None:
        self._inp_name.update(dt)
        self._inp_room.update(dt)

        # Notifications
        for n in self._notifs:
            n.elapsed += dt
        self._notifs = [n for n in self._notifs if not n.done]

        # Play animation
        if self._play_anim:
            self._play_anim.elapsed += dt
            if self._play_anim.done:
                self._play_anim = None

        # Active card animations (deal)
        for a in self._anims:
            a.elapsed += dt
        self._anims = [a for a in self._anims if not a.done]

        # Deal sequence state machine
        if self._deal_phase == "shuffle":
            self._deal_elapsed += dt
            if self._deal_elapsed >= 1.2 and self._deal_state_ready:
                self._deal_phase   = "deal"
                self._deal_elapsed = 0.0
                self._setup_deal_anims()
                # Emit all anims immediately (delays control timing)
                self._anims.extend(self._deal_queue)
                self._deal_queue = []

        elif self._deal_phase == "deal":
            self._deal_elapsed += dt
            if not self._anims:
                self._deal_phase = ""
                sp = self._state.get("phase", "playing")
                if sp == "finished":
                    self._ui_phase = "finished"
                elif sp == "rule8_reaction":
                    self._ui_phase = "rule8_reaction"
                else:
                    self._ui_phase = "playing"

        # Button visibility
        is_my   = self._is_my_turn()
        has_drw = self._state.get("has_drawn", False)
        phase   = self._ui_phase
        dealing = bool(self._deal_phase)

        self._btn_draw.visible  = phase == "playing" and is_my and not has_drw and not dealing
        self._btn_pass.visible  = phase == "playing" and is_my and has_drw and not dealing
        self._btn_react.visible = phase == "rule8_reaction"
        r8 = self._state.get("rule8_state") or {}
        self._btn_react.enabled = self._my_id in r8.get("pending_players", [])

        self._btn_start.visible  = phase == "lobby" and self._my_id == "p0"
        self._btn_start.enabled  = len(self._lobby) >= 2
        self._btn_back.visible   = phase in ("join_room", "create_room")
        self._btn_connect.visible = phase == "join_room"

    # -----------------------------------------------------------------------
    # Rendering — dispatcher
    # -----------------------------------------------------------------------

    def _render(self) -> None:
        phase = self._ui_phase
        mouse = pygame.mouse.get_pos()

        if phase in ("main_menu",):
            self._draw_bg("menu")
            self._render_main_menu(mouse)
        elif phase in ("create_room", "join_room"):
            self._draw_bg("waiting")
            if phase == "create_room":
                self._render_create_room(mouse)
            else:
                self._render_join_room(mouse)
        elif phase == "lobby":
            self._draw_bg("waiting")
            self._render_lobby(mouse)
        elif phase in ("playing", "rule8_reaction", "finished"):
            self._draw_bg("playing")
            if self._deal_phase:
                self._render_deal_animation()
            else:
                self._render_game(mouse)
                if phase == "rule8_reaction":
                    self._render_rule8_overlay(mouse)
                if phase == "finished":
                    self._render_finished_overlay()
            self._render_animations()
        elif phase == "error":
            self._draw_bg("menu")
            self._render_error()

        # Global layers
        self._render_notifications()

        # Modal overlays (always on top)
        if self._overlay == "color_pick":
            self._render_color_pick(mouse)
        elif self._overlay == "direction_pick":
            self._render_direction_pick(mouse)
        elif self._overlay == "swap_pick":
            self._render_swap_pick(mouse)

    # -----------------------------------------------------------------------
    # Screen renders
    # -----------------------------------------------------------------------

    def _render_main_menu(self, mouse: tuple) -> None:
        # Title panel
        panel = pygame.Surface((700, 320), pygame.SRCALPHA)
        panel.fill((10, 20, 15, 175))
        pygame.draw.rect(panel, (60, 180, 80, 200), panel.get_rect(), 2, border_radius=16)
        self._screen.blit(panel, (WIN_W // 2 - 350, 200))

        title = self._fxl.render("Custom UNO Online", True, TEXT_COL)
        self._screen.blit(title, title.get_rect(center=(WIN_W // 2, 250)))

        sub = self._fmd.render("Enter your name to get started", True, (160, 200, 160))
        self._screen.blit(sub, sub.get_rect(center=(WIN_W // 2, 295)))

        self._inp_name.draw(self._screen, self._fmd)
        self._btn_create.draw(self._screen, self._fmd, mouse)
        self._btn_join.draw(self._screen, self._fmd, mouse)

        hint = self._fsm.render("Create Room = host a game   |   Join Room = enter a code to join", True, (130, 160, 130))
        self._screen.blit(hint, hint.get_rect(center=(WIN_W // 2, 460)))

    def _render_create_room(self, mouse: tuple) -> None:
        panel = pygame.Surface((700, 340), pygame.SRCALPHA)
        panel.fill((10, 20, 15, 175))
        pygame.draw.rect(panel, (60, 180, 80, 200), panel.get_rect(), 2, border_radius=16)
        self._screen.blit(panel, (WIN_W // 2 - 350, 180))

        title = self._flg.render("Room Created!", True, (100, 230, 120))
        self._screen.blit(title, title.get_rect(center=(WIN_W // 2, 225)))

        # Room code
        code_bg = pygame.Surface((260, 80), pygame.SRCALPHA)
        code_bg.fill((20, 60, 30, 200))
        pygame.draw.rect(code_bg, HIGHLIGHT_COL, code_bg.get_rect(), 3, border_radius=12)
        cx = WIN_W // 2 - 130
        self._screen.blit(code_bg, (cx, 255))
        code_txt = self._fxl.render(self._room_code, True, HIGHLIGHT_COL)
        self._screen.blit(code_txt, code_txt.get_rect(center=(WIN_W // 2, 295)))

        lbl = self._fmd.render("Share this code with friends on your network", True, TEXT_COL)
        self._screen.blit(lbl, lbl.get_rect(center=(WIN_W // 2, 348)))

        ip_txt = self._fmd.render(f"LAN IP:  {self._room_ip}:{self._room_port}", True, (160, 200, 160))
        self._screen.blit(ip_txt, ip_txt.get_rect(center=(WIN_W // 2, 378)))

        if self._net:
            wait = self._fmd.render("Waiting for players…", True, (130, 160, 130))
            self._screen.blit(wait, wait.get_rect(center=(WIN_W // 2, 415)))
        else:
            start = self._fmd.render("Starting server…", True, (200, 180, 80))
            self._screen.blit(start, start.get_rect(center=(WIN_W // 2, 415)))

        self._btn_back.draw(self._screen, self._fmd, mouse)

    def _render_join_room(self, mouse: tuple) -> None:
        panel = pygame.Surface((700, 320), pygame.SRCALPHA)
        panel.fill((10, 20, 15, 175))
        pygame.draw.rect(panel, (60, 100, 200, 200), panel.get_rect(), 2, border_radius=16)
        self._screen.blit(panel, (WIN_W // 2 - 350, 200))

        title = self._flg.render("Join a Room", True, TEXT_COL)
        self._screen.blit(title, title.get_rect(center=(WIN_W // 2, 240)))

        name_lbl = self._fsm.render("Your name:", True, (160, 200, 160))
        self._screen.blit(name_lbl, (WIN_W // 2 - 160, 300))
        self._inp_name.draw(self._screen, self._fmd)

        code_lbl = self._fsm.render("Room code (4 letters) or IP:Port:", True, (160, 200, 160))
        self._screen.blit(code_lbl, (WIN_W // 2 - 160, 332))
        self._inp_room.draw(self._screen, self._fmd)

        if self._discovering:
            spin = "◐◓◑◒"[int(time.monotonic() * 4) % 4]
            s = self._fmd.render(f"{spin}  Searching for room…", True, (200, 200, 80))
            self._screen.blit(s, s.get_rect(center=(WIN_W // 2, 415)))
        else:
            self._btn_connect.draw(self._screen, self._fmd, mouse)

        self._btn_back.draw(self._screen, self._fmd, mouse)

        hint = self._fsm.render("Press Enter or click Connect", True, (100, 130, 100))
        self._screen.blit(hint, hint.get_rect(center=(WIN_W // 2, 497)))

    def _render_lobby(self, mouse: tuple) -> None:
        panel = pygame.Surface((720, 380), pygame.SRCALPHA)
        panel.fill((10, 20, 15, 175))
        pygame.draw.rect(panel, (60, 180, 80, 200), panel.get_rect(), 2, border_radius=16)
        self._screen.blit(panel, (WIN_W // 2 - 360, 155))

        title = self._flg.render("Waiting Room", True, TEXT_COL)
        self._screen.blit(title, title.get_rect(center=(WIN_W // 2, 195)))

        y = 230
        hdr = self._fmd.render(f"Players  ({len(self._lobby)} connected):", True, (160, 200, 160))
        self._screen.blit(hdr, hdr.get_rect(centerx=WIN_W // 2, top=y))
        y += 32
        for p in self._lobby:
            tags = ""
            if p["player_id"] == "p0":    tags += "  HOST"
            if p["player_id"] == self._my_id: tags += "  (you)"
            col  = (255, 230, 80) if p["player_id"] == "p0" else TEXT_COL
            line = self._fmd.render(f"   {p['name']}{tags}", True, col)
            self._screen.blit(line, line.get_rect(centerx=WIN_W // 2, top=y))
            y += 28

        if self._my_id == "p0":
            hint = ("Start when everyone is ready!" if len(self._lobby) >= 2
                    else "Need at least one more player.")
            h = self._fsm.render(hint, True, (130, 160, 130))
            self._screen.blit(h, h.get_rect(center=(WIN_W // 2, 440)))
            self._btn_start.draw(self._screen, self._fmd, mouse)
        else:
            w = self._fmd.render("Waiting for host to start the game…", True, (140, 170, 140))
            self._screen.blit(w, w.get_rect(center=(WIN_W // 2, 440)))

        # Room code corner (host only)
        if self._room_code and self._my_id == "p0":
            self._render_room_code_corner()

    def _render_room_code_corner(self) -> None:
        """Small room-code badge in the top-right corner."""
        badge = pygame.Surface((240, 70), pygame.SRCALPHA)
        badge.fill((10, 25, 15, 200))
        pygame.draw.rect(badge, HIGHLIGHT_COL, badge.get_rect(), 2, border_radius=10)
        self._screen.blit(badge, (WIN_W - 250, 10))
        lbl = self._fsm.render("Room code", True, (160, 200, 160))
        self._screen.blit(lbl, (WIN_W - 238, 18))
        code = self._flg.render(self._room_code, True, HIGHLIGHT_COL)
        self._screen.blit(code, (WIN_W - 238, 35))

    def _render_error(self) -> None:
        txt = self._fxl.render("Connection lost.", True, ERR_COL)
        self._screen.blit(txt, txt.get_rect(center=(WIN_W // 2, WIN_H // 2 - 20)))
        sub = self._fmd.render("Close the window to exit.", True, (160, 160, 160))
        self._screen.blit(sub, sub.get_rect(center=(WIN_W // 2, WIN_H // 2 + 25)))

    # -----------------------------------------------------------------------
    # In-game render
    # -----------------------------------------------------------------------

    def _render_game(self, mouse: tuple) -> None:
        self._render_header()
        self._render_opponents()
        self._render_center(mouse)
        self._render_controls(mouse)
        self._render_hand(mouse)
        if self._room_code and self._my_id == "p0":
            self._render_room_code_corner()

    def _render_header(self) -> None:
        bar = pygame.Surface((WIN_W, HEADER_H), pygame.SRCALPHA)
        bar.fill((10, 25, 15, 210))
        self._screen.blit(bar, (0, 0))
        pygame.draw.line(self._screen, (40, 120, 60), (0, HEADER_H - 1), (WIN_W, HEADER_H - 1))

        players = self._state.get("players", [])
        idx     = self._state.get("current_player_index", 0)
        cur_pid = players[idx]["player_id"] if players else ""
        cur_nm  = players[idx]["name"] if players else "?"
        pending = self._state.get("pending_draw", 0)
        dirn    = "CW" if self._state.get("direction", 1) == 1 else "CCW"

        left = self._fmd.render(f"You: {self._pending_name}  ({self._my_id})",
                                True, TEXT_COL)
        mid_col = (255, 230, 80) if cur_pid == self._my_id else TEXT_COL
        mid  = self._flg.render(f"Current: {cur_nm}", True, mid_col)
        rt   = f"Turn {self._state.get('turn_number',0)}  |  {dirn}"
        if pending:
            rt += f"  |  Stack: +{pending}"
        right = self._fmd.render(rt, True, (255, 140, 50) if pending else TEXT_COL)

        cy = HEADER_H // 2
        self._screen.blit(left,  (12, cy - left.get_height() // 2))
        self._screen.blit(mid,   mid.get_rect(centerx=WIN_W // 2, centery=cy))
        self._screen.blit(right, (WIN_W - right.get_width() - 12, cy - right.get_height() // 2))

    def _render_opponents(self) -> None:
        players  = self._state.get("players", [])
        opps     = [p for p in players if p["player_id"] != self._my_id]
        if not opps:
            return
        cur_idx  = self._state.get("current_player_index", 0)
        cur_pid  = players[cur_idx]["player_id"] if players else ""
        n        = len(opps)
        sec_w    = WIN_W // n

        for i, opp in enumerate(opps):
            cx = sec_w * i + sec_w // 2
            y  = OPP_Y + 8
            is_active = opp["player_id"] == cur_pid
            name_col  = (255, 230, 80) if is_active else TEXT_COL
            cnt       = opp["hand_count"]
            ns = self._fmd.render(f"{opp['name']}  [{cnt} card{'s' if cnt != 1 else ''}]",
                                  True, name_col)
            if is_active:
                glow = pygame.Surface((ns.get_width() + 16, ns.get_height() + 8), pygame.SRCALPHA)
                glow.fill((255, 230, 50, 40))
                self._screen.blit(glow, (cx - glow.get_width() // 2, y - 4))
            self._screen.blit(ns, ns.get_rect(centerx=cx, top=y))
            y += ns.get_height() + 6

            n_show = min(cnt, 10)
            if n_show:
                back    = self._assets.get_back((OPP_CW, OPP_CH))
                spacing = min(OPP_CW + 4, (sec_w - 40) // n_show if n_show > 1 else OPP_CW + 4)
                total_w = OPP_CW + spacing * (n_show - 1)
                sx = cx - total_w // 2
                for j in range(n_show):
                    self._screen.blit(back, (sx + j * spacing, y))
            if cnt > 10:
                more = self._fsm.render(f"+{cnt-10}", True, TEXT_COL)
                self._screen.blit(more, (cx - more.get_width() // 2, y + OPP_CH + 2))

    def _render_center(self, mouse: tuple) -> None:
        # Draw pile
        back = self._assets.get_back()
        if (self._is_my_turn() and not self._state.get("has_drawn")
                and DRAW_PILE_RECT.collidepoint(mouse)):
            glow = pygame.Surface((CARD_W + 10, CARD_H + 10), pygame.SRCALPHA)
            glow.fill((255, 255, 255, 40))
            self._screen.blit(glow, (DRAW_PILE_RECT.x - 5, DRAW_PILE_RECT.y - 5))
            pygame.draw.rect(self._screen, HOVER_COL,
                             DRAW_PILE_RECT.inflate(6, 6), 3, border_radius=6)
        self._screen.blit(back, DRAW_PILE_RECT.topleft)
        cnt_s  = self._fsm.render(f"{self._state.get('draw_pile_count', 0)} cards",
                                   True, TEXT_COL)
        lbl_d  = self._fsm.render("Draw Pile", True, (150, 175, 155))
        self._screen.blit(lbl_d, lbl_d.get_rect(centerx=DRAW_PILE_RECT.centerx,
                                                   bottom=DRAW_PILE_RECT.top - 4))
        self._screen.blit(cnt_s, cnt_s.get_rect(centerx=DRAW_PILE_RECT.centerx,
                                                  top=DRAW_PILE_RECT.bottom + 4))

        # Discard pile
        discard = self._state.get("discard_pile", [])
        if discard:
            top_surf = self._assets.get(discard[-1], (DISCARD_W, DISCARD_H))
            # Drop shadow
            shadow = pygame.Surface((DISCARD_W + 8, DISCARD_H + 8), pygame.SRCALPHA)
            shadow.fill((0, 0, 0, 80))
            self._screen.blit(shadow, (DISCARD_RECT.x - 4, DISCARD_RECT.y + 4))
            self._screen.blit(top_surf, DISCARD_RECT.topleft)

            active_color = self._state.get("active_color")
            if active_color in UNO_COLORS:
                dc = (DISCARD_RECT.centerx, DISCARD_RECT.bottom + 18)
                pygame.draw.circle(self._screen, UNO_COLORS[active_color], dc, 14)
                pygame.draw.circle(self._screen, TEXT_COL, dc, 14, 2)
                cl = self._fsm.render(active_color, True, TEXT_COL)
                self._screen.blit(cl, cl.get_rect(centerx=dc[0], top=dc[1] + 16))

        lbl_dis = self._fsm.render("Discard", True, (150, 175, 155))
        self._screen.blit(lbl_dis, lbl_dis.get_rect(centerx=DISCARD_RECT.centerx,
                                                      bottom=DISCARD_RECT.top - 4))

        # Info panel
        sx, sy = STATUS_X, CENTER_Y + 12
        lines: List[Tuple[str, tuple]] = []
        pending  = self._state.get("pending_draw", 0)
        stacking = self._state.get("stacking_type")
        is_my    = self._is_my_turn()
        has_drw  = self._state.get("has_drawn", False)

        if pending:
            lines.append((f"Penalty stack: +{pending}", (255, 130, 50)))
            if stacking == "draw_two":
                lines.append(("  Stack +2 / +4, or draw", (180, 180, 180)))
            elif stacking == "wild_draw_four":
                lines.append(("  Stack +4 only, or draw", (180, 180, 180)))
        if is_my:
            if has_drw:
                lines.append(("You drew — play or pass", (130, 220, 130)))
            else:
                lines.append(("Your turn!", (80, 240, 80)))

        for text, col in lines:
            s = self._fmd.render(text, True, col)
            self._screen.blit(s, (sx, sy))
            sy += s.get_height() + 5

    def _render_controls(self, mouse: tuple) -> None:
        self._btn_draw.draw(self._screen, self._fmd, mouse)
        self._btn_pass.draw(self._screen, self._fmd, mouse)

    def _render_hand(self, mouse: tuple) -> None:
        hand = self._my_hand()
        lbl  = self._fsm.render("Your hand:", True, (140, 170, 145))
        self._screen.blit(lbl, (12, HAND_Y + 4))
        if not hand:
            return

        is_my  = self._is_my_turn()
        rects  = self._hand_rects(len(hand))
        dealing = bool(self._deal_phase)

        for i, (card, rect) in enumerate(zip(hand, rects)):
            playable = is_my and self._is_playable(card) and not dealing
            self._screen.blit(self._assets.get(card), rect.topleft)

            if is_my and not playable and not dealing:
                self._screen.blit(self._dim_surf, rect.topleft)

            if i == self._sel_idx:
                pygame.draw.rect(self._screen, HIGHLIGHT_COL, rect, 3, border_radius=5)
            elif i == self._hover_idx and is_my and playable:
                glow = pygame.Surface((CARD_W + 6, CARD_H + 6), pygame.SRCALPHA)
                glow.fill((100, 240, 100, 50))
                self._screen.blit(glow, (rect.x - 3, rect.y - 3))
                pygame.draw.rect(self._screen, (100, 230, 100), rect, 2, border_radius=5)

    # -----------------------------------------------------------------------
    # Rule 8 overlay
    # -----------------------------------------------------------------------

    def _render_rule8_overlay(self, mouse: tuple) -> None:
        r8 = self._state.get("rule8_state") or {}
        deadline  = r8.get("deadline", time.time())
        remaining = max(0.0, deadline - time.time())
        reacted   = r8.get("reacted", [])
        pending_p = r8.get("pending_players", [])
        i_pending = self._my_id in pending_p

        # Dark overlay
        veil = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        veil.fill(RULE8_BG)
        self._screen.blit(veil, (0, 0))

        # Pulsing border
        pulse = (math.sin(time.monotonic() * 6) * 0.5 + 0.5)
        border_alpha = int(120 + 135 * pulse)
        border_surf  = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        pygame.draw.rect(border_surf, (*RULE8_BORDER, border_alpha),
                         (0, 0, WIN_W, WIN_H), 8)
        self._screen.blit(border_surf, (0, 0))

        # Title
        t_rule8 = self._fxl.render("RULE 8  —  REACT NOW!", True, (230, 80, 255))
        self._screen.blit(t_rule8, t_rule8.get_rect(center=(WIN_W // 2, 120)))

        # Countdown ring
        cx, cy, R = WIN_W // 2, 260, 70
        frac = remaining / 3.0
        pygame.draw.circle(self._screen, (50, 20, 60), (cx, cy), R + 6)
        pygame.draw.circle(self._screen, (40, 15, 50), (cx, cy), R)
        # Arc (drawn as many small circles)
        arc_col = (
            (80, 220, 80) if frac > 0.5 else
            (220, 180, 40) if frac > 0.25 else
            (220, 60, 60)
        )
        for deg in range(int(frac * 360)):
            rad = math.radians(deg - 90)
            px  = cx + int(R * math.cos(rad))
            py  = cy + int(R * math.sin(rad))
            pygame.draw.circle(self._screen, arc_col, (px, py), 5)

        sec_str  = f"{remaining:.1f}s"
        sec_surf = self._flg.render(sec_str, True,
                                    (80, 220, 80) if remaining > 1.5 else (220, 80, 80))
        self._screen.blit(sec_surf, sec_surf.get_rect(center=(cx, cy)))

        # Player status
        players  = self._state.get("players", [])
        sx = WIN_W // 2 - 200
        sy = 355
        status_lbl = self._fmd.render("Reactions:", True, (180, 160, 200))
        self._screen.blit(status_lbl, (sx, sy - 28))
        for p in players:
            pid   = p["player_id"]
            nm    = p["name"]
            if pid in reacted:
                icon, col = "✓", (80, 220, 80)
            elif pid in pending_p:
                icon, col = "…", (220, 80, 80)
            else:
                icon, col = "-", (120, 120, 120)
            line = self._fmd.render(f"{icon}  {nm}", True, col)
            self._screen.blit(line, (sx, sy))
            sy += 26

        # React button (pulsing size)
        pulse_scale = 1.0 + 0.06 * pulse
        bw = int(220 * pulse_scale)
        bh = int(56 * pulse_scale)
        react_rect = pygame.Rect(WIN_W // 2 - bw // 2, CTRL_Y - 2, bw, bh)
        if i_pending:
            self._btn_react.rect = react_rect
            self._btn_react.draw(self._screen, self._flg, mouse)
        else:
            done_s = self._fmd.render("You reacted!", True, (80, 220, 80))
            self._screen.blit(done_s, done_s.get_rect(center=(WIN_W // 2, CTRL_Y + 25)))

    # -----------------------------------------------------------------------
    # Game-over overlay
    # -----------------------------------------------------------------------

    def _render_finished_overlay(self) -> None:
        veil = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        self._screen.blit(veil, (0, 0))

        panel = pygame.Surface((600, 200), pygame.SRCALPHA)
        panel.fill((15, 30, 20, 210))
        pygame.draw.rect(panel, HIGHLIGHT_COL, panel.get_rect(), 3, border_radius=18)
        self._screen.blit(panel, (WIN_W // 2 - 300, WIN_H // 2 - 100))

        winner_id   = self._state.get("winner_id", "")
        winner_name = self._player_name(winner_id)
        is_me       = winner_id == self._my_id
        msg_col     = (255, 220, 50) if is_me else TEXT_COL
        msg_text    = "You win!" if is_me else f"{winner_name} wins!"
        msg = self._fxl.render(msg_text, True, msg_col)
        self._screen.blit(msg, msg.get_rect(center=(WIN_W // 2, WIN_H // 2 - 30)))
        sub = self._fmd.render("Close the window to exit.", True, (160, 160, 160))
        self._screen.blit(sub, sub.get_rect(center=(WIN_W // 2, WIN_H // 2 + 30)))

    # -----------------------------------------------------------------------
    # Deal animation render
    # -----------------------------------------------------------------------

    def _render_deal_animation(self) -> None:
        # Darken playing background slightly
        veil = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 60))
        self._screen.blit(veil, (0, 0))

        if self._deal_phase == "shuffle":
            t = min(1.0, self._deal_elapsed / 1.2)
            back = self._assets.get_back()
            cx, cy = WIN_W // 2 - CARD_W // 2, WIN_H // 2 - CARD_H // 2
            for k in range(6):
                phase = t * 10 + k * 1.1
                amp   = 45 * min(t * 2, 1.0) * min((1 - t) * 2, 1.0)
                ox = int(math.sin(phase) * amp)
                oy = int(math.cos(phase * 0.8 + k) * amp * 0.4)
                self._screen.blit(back, (cx + ox, cy + oy))
            lbl = self._flg.render("Shuffling…", True, TEXT_COL)
            self._screen.blit(lbl, lbl.get_rect(center=(WIN_W // 2, WIN_H // 2 + CARD_H // 2 + 30)))

        elif self._deal_phase == "deal":
            # Deck pile (shrinks as cards fly away)
            remaining = self._deal_total - sum(1 for a in self._anims if a.active)
            if remaining > 0:
                back = self._assets.get_back()
                for k in range(min(remaining, 4)):
                    self._screen.blit(back, (DISCARD_CX - CARD_W // 2 - k, _CMY - CARD_H // 2 - k))
            lbl = self._fmd.render("Dealing cards…", True, TEXT_COL)
            self._screen.blit(lbl, lbl.get_rect(center=(WIN_W // 2, WIN_H // 2 + CARD_H // 2 + 30)))

    # -----------------------------------------------------------------------
    # Flying-card animation render (deal + play)
    # -----------------------------------------------------------------------

    def _render_animations(self) -> None:
        for a in self._anims:
            if a.active:
                self._screen.blit(a.surf, a.pos())
        if self._play_anim and self._play_anim.active:
            self._screen.blit(self._play_anim.surf, self._play_anim.pos())

    # -----------------------------------------------------------------------
    # Notification render
    # -----------------------------------------------------------------------

    def _render_notifications(self) -> None:
        W_N, PAD = 370, 12
        y = WIN_H - 10

        for notif in self._notifs:
            border_col = NOTIF_COLORS.get(notif.ntype, INFO_COL)
            alpha      = notif.alpha
            slide      = notif.slide_x

            lines = []
            words = notif.message.split()
            line  = ""
            for w in words:
                test = (line + " " + w).strip()
                if self._fmd.size(test)[0] > W_N - 2 * PAD - 6:
                    lines.append(line)
                    line = w
                else:
                    line = test
            if line:
                lines.append(line)
            nlines = len(lines)

            h = nlines * (self._fmd.get_height() + 2) + 2 * PAD
            x = WIN_W - W_N - 10 + slide

            y -= h + 6

            bg_s = pygame.Surface((W_N, h), pygame.SRCALPHA)
            bg_s.fill((*NOTIF_BG[:3], min(alpha, NOTIF_BG[3])))
            # Left accent bar
            accent = pygame.Surface((5, h), pygame.SRCALPHA)
            accent.fill((*border_col, alpha))
            bg_s.blit(accent, (0, 0))
            # Border
            pygame.draw.rect(bg_s, (*border_col, alpha), bg_s.get_rect(), 2, border_radius=8)
            self._screen.blit(bg_s, (x, y))

            for li, ln in enumerate(lines):
                txt = self._fmd.render(ln, True, TEXT_COL)
                ts  = pygame.Surface(txt.get_size(), pygame.SRCALPHA)
                ts.fill((0, 0, 0, 0))
                ts.blit(txt, (0, 0))
                ts.set_alpha(alpha)
                self._screen.blit(ts, (x + PAD + 6, y + PAD + li * (self._fmd.get_height() + 2)))

    # -----------------------------------------------------------------------
    # Modal overlays
    # -----------------------------------------------------------------------

    def _render_overlay_bg(self, title: str) -> None:
        veil = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 175))
        self._screen.blit(veil, (0, 0))
        t = self._flg.render(title, True, TEXT_COL)
        self._screen.blit(t, t.get_rect(center=(WIN_W // 2, WIN_H // 2 - 90)))

    def _render_color_pick(self, mouse: tuple) -> None:
        self._render_overlay_bg("Choose a color:")
        for color, btn in self._color_btns.items():
            base = UNO_COLORS[color]
            hov  = tuple(min(255, v + 45) for v in base)
            col  = hov if btn.rect.collidepoint(mouse) else base
            shadow = pygame.Surface((btn.rect.width + 6, btn.rect.height + 6), pygame.SRCALPHA)
            shadow.fill((0, 0, 0, 70))
            self._screen.blit(shadow, (btn.rect.x + 3, btn.rect.y + 4))
            pygame.draw.rect(self._screen, col, btn.rect, border_radius=12)
            pygame.draw.rect(self._screen, TEXT_COL, btn.rect, 2, border_radius=12)
            lbl = self._fmd.render(color.capitalize(), True, TEXT_COL)
            self._screen.blit(lbl, lbl.get_rect(center=btn.rect.center))

    def _render_direction_pick(self, mouse: tuple) -> None:
        self._render_overlay_bg("Choose direction to pass hands:")
        for btn in self._dir_btns.values():
            btn.draw(self._screen, self._fmd, mouse)

    def _render_swap_pick(self, mouse: tuple) -> None:
        self._render_overlay_bg("Swap hands with:")
        for btn in self._swap_btns.values():
            btn.draw(self._screen, self._fmd, mouse)

    # -----------------------------------------------------------------------
    # Layout helpers
    # -----------------------------------------------------------------------

    def _hand_rects(self, n: int) -> List[pygame.Rect]:
        if n == 0:
            return []
        avail_w = WIN_W - 120
        spacing = max(20, min(CARD_W + 5, (avail_w - CARD_W) // (n - 1))) if n > 1 else CARD_W + 5
        total_w = CARD_W + spacing * (n - 1)
        start_x = (WIN_W - total_w) // 2
        rects   = []
        for i in range(n):
            x = start_x + i * spacing
            y = HAND_CARD_Y
            if i == self._sel_idx:   y -= 30
            elif i == self._hover_idx: y -= 15
            rects.append(pygame.Rect(x, y, CARD_W, CARD_H))
        return rects

    def _card_at(self, pos: tuple) -> int:
        hand = self._my_hand()
        if not hand:
            return -1
        rects = self._hand_rects(len(hand))
        for i in range(len(rects) - 1, -1, -1):
            if rects[i].collidepoint(pos):
                return i
        return -1

    # -----------------------------------------------------------------------
    # State helpers
    # -----------------------------------------------------------------------

    def _is_my_turn(self) -> bool:
        players = self._state.get("players", [])
        idx     = self._state.get("current_player_index", -1)
        return 0 <= idx < len(players) and players[idx]["player_id"] == self._my_id

    def _my_hand(self) -> list:
        for p in self._state.get("players", []):
            if p["player_id"] == self._my_id:
                return p.get("hand", [])
        return []

    def _is_playable(self, card: dict) -> bool:
        discard = self._state.get("discard_pile", [])
        if not discard:
            return True
        top   = Card.from_dict(discard[-1])
        c     = Card.from_dict(card)
        return c.is_playable_on(
            top,
            self._state.get("active_color"),
            self._state.get("stacking_type"),
        )

    def _player_name(self, pid: str) -> str:
        for p in self._state.get("players", []):
            if p["player_id"] == pid:
                return p["name"]
        for p in self._lobby:
            if p["player_id"] == pid:
                return p["name"]
        return pid
