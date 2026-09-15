"""Dopasowanie polskich nazw produktów (z jadłospisów, od LLM, od użytkownika) do produktów w bazie.

Etapy (od najpewniejszego):
1. dokładny alias po normalizacji (małe litery, bez polskich znaków),
2. alias po lematyzacji (pomidorów → pomidor) — lematyzujemy oryginalne słowa, bo "gesty" bez ogonków
   lematyzator czyta jako "gest",
3. to samo po usunięciu słów opisowych (do panierki, na deser, ugotowane...),
4. dopasowanie rozmyte — daje kandydatów; "pewne" tylko przy zgodnym głównym rzeczowniku, wyraźnej przewadze
   i braku słów wariantu (light, chudy, kiszony, 5%...), których kandydat nie ma.
Niepewne dopasowania rozstrzyga LLM spośród kandydatów albo użytkownik — nigdy nie zgadujemy po cichu.
Procenty ("3,2%") są jednym słowem — nie wolno ich rozbijać ani usuwać (inaczej "mleko 3,2%" dałoby "mleko 2%").
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache

from rapidfuzz import fuzz

from ..domain.product import Product, State
from ..textnorm import normalize

# Słowa opisujące użycie/przygotowanie — nie zmieniają produktu (formy bez polskich znaków)
DESCRIPTOR_WORDS = {
    "do", "na", "ok", "okolo", "szt", "sztuki", "sztuka", "sztuk", "g", "gram", "gramow", "ml", "dodatkowo", "obok",
    "ulubiony", "ulubiona", "ulubione", "ulubionych", "deser", "deseru", "panierki", "panierke", "smazenia", "smazenie",
    "dressingu", "dressing", "dressingiem", "mizerii", "sos", "sosu", "posypania", "podania", "kupne", "kupny", "kupna",
    "dobrym", "skladem", "swiezy", "swieza", "swieze", "swiezego", "surowy", "surowa", "surowe", "surowego", "surowej",
    "rozgniecione", "rozgnieciony", "pokrojone", "pokrojony", "upieczone", "upieczony", "zgrillowana",
    "zgrillowany", "grillowana", "grillowany", "krojone", "krojony", "plastry", "plasterki", "srednie", "sredni",
    "duze", "duzy", "male", "maly", "zwykly", "zwykla", "zwykle", "lub", "podsmazenia", "podsmazania", "duszenia",
    "pieczenia", "gotowania", "posypki", "garnirunku", "mrozone", "mrozony", "moga", "byc", "jako",
}
# Słowa wariantu zmieniające wartości odżywcze
VARIANT_WORDS = {
    "light", "lekki", "lekka", "lekkie", "lekkiego", "chudy", "chuda", "chude", "chudego", "chudej", "tlusty", "tlusta",
    "tluste", "pelnotlusty", "pelnotlusta", "pelnotluste", "odtluszczony", "odtluszczona", "odtluszczone", "zero",
    "proteinowy", "proteinowa", "proteinowe", "wysokobialkowy", "wysokobialkowa", "wysokobialkowe", "bez", "laktozy",
    "weganski", "weganska", "roslinny", "roslinna", "fit", "grecki", "grecka", "greckiego", "gesty", "gesta", "geste",
    "slodzony", "slodzona", "slodzone", "smakowy", "smakowa", "smakowe", "owocowy", "owocowa", "owocowe",
    "kiszony", "kiszona", "kiszone", "kiszonego", "marynowany", "marynowana", "marynowane", "suszony", "suszona",
    "suszone", "kandyzowany", "wedzony", "wedzona", "wedzone", "panierowany", "panierowana", "smazony", "smazona",
}
COOKED_HINTS = {"ugotowany", "ugotowana", "ugotowane", "ugotowanego", "gotowany", "gotowana", "gotowane",
                "gotowanego", "ugotowaniu"}
DRY_HINTS = {"suchy", "sucha", "suche", "suchego", "suchej"}
CONJUNCTIONS = ("i", "oraz")

_PAREN = re.compile(r"\(([^)]*)\)")
_GRAMS = re.compile(r"(\d+(?:[.,]\d+)?)\s*g\b")
_PIECES = re.compile(r"(\d+)\s*(?:szt|sztuk|sztuki)")
_WORD = re.compile(r"\d+(?:[.,]\d+)?%|[^\W\d_]+|\d+", re.UNICODE)


class MatchStatus(StrEnum):
    SURE = "pewne"
    UNSURE = "niepewne"
    NONE = "brak"


@dataclass
class Candidate:
    product_id: str
    name: str
    score: float
    via: str  # alias | lemat | bez_opisow | rozmyte


@dataclass
class MatchResult:
    query: str
    status: MatchStatus
    product_id: str | None
    candidates: list[Candidate] = field(default_factory=list)
    grams: float | None = None
    pieces: int | None = None
    warnings: list[str] = field(default_factory=list)


def _fold(word: str) -> str:
    """Pojedyncze słowo bez polskich znaków; procenty zostają jednym słowem ("3,2%" -> "3,2%")."""
    if word.endswith("%"):
        return word.replace(".", ",")
    return normalize(word).replace(" ", "")


@lru_cache(maxsize=50_000)
def _lemma(word: str) -> str:
    """Lemat słowa w oryginalnej pisowni, zwrócony bez polskich znaków."""
    if word.endswith("%") or word.isdigit():
        return _fold(word)
    try:
        import simplemma

        return _fold(simplemma.lemmatize(word, lang="pl")) or _fold(word)
    except Exception:  # brak słownika nie może zatrzymać aplikacji
        return _fold(word)


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def key_of(ws: list[str]) -> str:
    return " ".join(_fold(w) for w in ws)


def lemma_of(ws: list[str]) -> str:
    return " ".join(_lemma(w) for w in ws)


def strip_descriptor_words(ws: list[str]) -> list[str]:
    skip = DESCRIPTOR_WORDS | COOKED_HINTS | DRY_HINTS
    return [w for w in ws if _fold(w) not in skip and not w.isdigit()]


def split_alternatives(text: str) -> list[str]:
    """'Szynka z piersi kurczaka/indyka' -> ['Szynka z piersi kurczaka', 'Szynka z piersi indyka'];
    'Sałata/Mix sałat' -> ['Sałata', 'Mix sałat']."""
    parts = [p.strip() for p in text.split("/") if p.strip()]
    if len(parts) < 2:
        return parts
    out = [parts[0]]
    head_words = parts[0].split()
    for p in parts[1:]:
        out.append(" ".join([*head_words[:-1], p]) if len(p.split()) == 1 and len(head_words) > 1 else p)
    return out


class ProductMatcher:
    def __init__(self, products: Mapping[str, Product]):
        self.products = dict(products)
        self.exact: dict[str, str] = {}
        self.lemmas: dict[str, str] = {}
        self.entries: list[tuple[str, str, frozenset[str], str]] = []  # (klucz, lemat, słowa, id)
        for p in self.products.values():
            base_name = _PAREN.sub(" ", p.name).strip()
            for name in [p.name, base_name, *p.aliases]:
                ws = words(name)
                if not ws:
                    continue
                key, lk = key_of(ws), lemma_of(ws)
                self.exact.setdefault(key, p.id)
                self.lemmas.setdefault(lk, p.id)
                stripped = strip_descriptor_words(ws)
                if stripped and stripped != ws:
                    self.lemmas.setdefault(lemma_of(stripped), p.id)
                tokens = frozenset(key.split()) | frozenset(lk.split())
                self.entries.append((key, lk, tokens, p.id))

    def _candidate(self, pid: str, score: float, via: str) -> Candidate:
        return Candidate(product_id=pid, name=self.products[pid].name, score=round(score, 1), via=via)

    def _match_one(self, text: str) -> list[Candidate]:
        ws = words(text)
        # "Jajka ugotowane i rozgniecione": część po spójniku to same opisy → pomijamy ją
        for conj in CONJUNCTIONS:
            if conj in ws:
                i = ws.index(conj)
                if i > 0 and not strip_descriptor_words(ws[i + 1:]):
                    ws = ws[:i]
        if not ws:
            return []
        key = key_of(ws)
        if key in self.exact:
            return [self._candidate(self.exact[key], 100, "alias")]
        lk = lemma_of(ws)
        if lk in self.lemmas:
            return [self._candidate(self.lemmas[lk], 98, "lemat")]
        stripped = strip_descriptor_words(ws)
        if stripped and stripped != ws:
            skey = key_of(stripped)
            if skey in self.exact:
                return [self._candidate(self.exact[skey], 95, "bez_opisow")]
            if lemma_of(stripped) in self.lemmas:
                return [self._candidate(self.lemmas[lemma_of(stripped)], 94, "bez_opisow")]
        base_ws = stripped or ws
        q_lemmas = lemma_of(base_ws).split()
        q_key = key_of(base_ws)
        q_tokens = set(q_key.split()) | set(q_lemmas)
        q_head = q_lemmas[0]
        q_variants = {t for t in q_tokens if t in VARIANT_WORDS or t.endswith("%")}
        best: dict[str, float] = {}
        for k, klem, tokens, pid in self.entries:
            s = max(fuzz.token_sort_ratio(" ".join(q_lemmas), klem), fuzz.ratio(q_key, k))
            k_lemmas = klem.split()
            if k_lemmas and k_lemmas[0] != q_head:
                s -= 15
            elif k_lemmas and set(k_lemmas) <= q_tokens:
                s = max(s, 88 + 2 * min(len(k_lemmas), 5))
            if q_variants - tokens:
                s = min(s, 85)  # np. "feta light" nie może być pewnie zwykłą fetą
            if s > best.get(pid, 0):
                best[pid] = s
        ranked = sorted(best.items(), key=lambda kv: -kv[1])[:5]
        return [self._candidate(pid, s, "rozmyte") for pid, s in ranked if s >= 55]

    def match(self, text: str) -> MatchResult:
        raw = text.strip()
        grams = pieces = None
        hints: set[str] = set()
        for inner in _PAREN.findall(raw):
            g = _GRAMS.search(inner)
            if g and grams is None:
                grams = float(g.group(1).replace(",", "."))
            pc = _PIECES.search(inner)
            if pc:
                pieces = int(pc.group(1))
            hints |= set(key_of(words(inner)).split())
        base = _PAREN.sub(" ", raw)
        hints |= set(key_of(words(base)).split())

        best: list[Candidate] = []
        alt_tops: set[str] = set()
        for alt in split_alternatives(base):
            cands = self._match_one(alt)
            if cands:
                alt_tops.add(cands[0].product_id)
            if cands and (not best or cands[0].score > best[0].score):
                best = cands
        result = MatchResult(query=raw, status=MatchStatus.NONE, product_id=None, candidates=best, grams=grams,
                             pieces=pieces)
        if not best:
            return result
        top = best[0]
        second = best[1].score if len(best) > 1 else 0
        if top.via != "rozmyte" or (top.score >= 92 and top.score - second >= 8):
            result.status, result.product_id = MatchStatus.SURE, top.product_id
        else:
            result.status = MatchStatus.UNSURE
        if len(alt_tops) > 1:
            result.status = MatchStatus.UNSURE
            result.warnings.append(f"'{raw}': alternatywy wskazują różne produkty ({', '.join(sorted(alt_tops))})")
        base_words = key_of(words(base)).split()
        if top.via == "rozmyte" and ("i" in base_words or "oraz" in base_words or "+" in base):
            result.status = MatchStatus.UNSURE
            result.warnings.append(f"'{raw}': pozycja wygląda na kilka produktów naraz — trzeba ją rozdzielić")
        product = self.products[top.product_id]
        if product.state is State.DRY and hints & COOKED_HINTS and not hints & DRY_HINTS:
            result.status = MatchStatus.UNSURE
            result.warnings.append(
                f"'{raw}': w bazie jest produkt suchy ({product.name}), a opis wskazuje na wagę po ugotowaniu"
            )
        if result.status is not MatchStatus.SURE:
            result.product_id = None  # niepewne nigdy nie wskazuje produktu — decyduje LLM albo użytkownik
        return result
