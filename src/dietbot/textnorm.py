"""Normalizacja polskich nazw produktów do porównań (aliasy, wyszukiwanie)."""

from __future__ import annotations

import re
import unicodedata

_PL_MAP = str.maketrans({"ł": "l", "Ł": "l"})
_NON_WORD = re.compile(r"[^a-z0-9%]+")


def fold(text: str) -> str:
    """Małe litery, bez polskich (i rumuńskich) znaków diakrytycznych."""
    text = text.translate(_PL_MAP).lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """'Pierś z kurczaka (surowa)' -> 'piers z kurczaka surowa'; '1,5%' -> '1 5%'."""
    folded = fold(text)
    folded = _NON_WORD.sub(" ", folded)
    return " ".join(folded.split())
