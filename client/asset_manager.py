"""
AssetManager — loads and caches every card image exactly once at startup.
Images are scaled to CARD_W × CARD_H on load; scaled copies for other
sizes are cached on first request so pygame.transform.smoothscale is
never called inside the game loop.
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import pygame

CARD_W, CARD_H = 90, 126          # canonical card size used in the player's hand
_COLORS = ("red", "yellow", "green", "blue")


def _card_key(card: dict) -> str:
    ct    = card["card_type"]
    color = card.get("color")
    value = card.get("value")
    if ct == "number":        return f"num_{color}_{value}"
    if ct == "skip":          return f"skip_{color}"
    if ct == "reverse":       return f"rev_{color}"
    if ct == "draw_two":      return f"d2_{color}"
    if ct == "wild":          return "wild"
    if ct == "wild_draw_four": return "wd4"
    return "back"


class AssetManager:
    """
    Preloads all card images at construction time.
    Call get(card_dict) or get_back() to retrieve a surface.
    An optional size=(w, h) returns a cached scaled copy.
    """

    def __init__(self, assets_root: str) -> None:
        self._base:   Dict[str, pygame.Surface] = {}
        self._scaled: Dict[Tuple, pygame.Surface] = {}
        self._load_all(assets_root)

    # ------------------------------------------------------------------
    # Internal loading helpers
    # ------------------------------------------------------------------

    def _load(self, path: str) -> pygame.Surface:
        img = pygame.image.load(path).convert_alpha()
        return pygame.transform.smoothscale(img, (CARD_W, CARD_H))

    def _load_all(self, root: str) -> None:
        for color in _COLORS:
            d = os.path.join(root, color)
            for v in range(10):
                self._base[f"num_{color}_{v}"] = self._load(
                    os.path.join(d, f"{v}_{color}.png"))
            self._base[f"skip_{color}"] = self._load(
                os.path.join(d, f"block_{color}.png"))
            self._base[f"rev_{color}"] = self._load(
                os.path.join(d, f"inverse_{color}.png"))
            self._base[f"d2_{color}"] = self._load(
                os.path.join(d, f"2plus_{color}.png"))

        wild_dir = os.path.join(root, "wild")
        self._base["wild"] = self._load(os.path.join(wild_dir, "wild_card.png"))
        self._base["wd4"]  = self._load(os.path.join(wild_dir, "4_plus.png"))

        # Note: the folder name has a space, not an underscore
        back_dir = os.path.join(root, "card back")
        self._base["back"] = self._load(os.path.join(back_dir, "card_back.png"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, card: dict,
            size: Optional[Tuple[int, int]] = None) -> pygame.Surface:
        """Return the surface for *card* (a dict from the server JSON)."""
        key  = _card_key(card)
        surf = self._base.get(key, self._base["back"])
        return self._at_size(key, surf, size)

    def get_back(self,
                 size: Optional[Tuple[int, int]] = None) -> pygame.Surface:
        """Return the card-back surface, optionally at a different size."""
        return self._at_size("back", self._base["back"], size)

    def _at_size(self, key: str, surf: pygame.Surface,
                 size: Optional[Tuple[int, int]]) -> pygame.Surface:
        if size is None or size == (CARD_W, CARD_H):
            return surf
        cache_key = (key, size)
        if cache_key not in self._scaled:
            self._scaled[cache_key] = pygame.transform.smoothscale(surf, size)
        return self._scaled[cache_key]
