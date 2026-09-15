"""Rozstrzyganie niepewnych nazw produktów przez LLM — wyłącznie wybór spośród kandydatów znalezionych przez kod."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel, Field

from ..domain.product import Category, Product, State
from ..food.matcher import MatchResult, MatchStatus, ProductMatcher
from .client import LLMClient

MAX_CANDIDATES = 8


class DecisionKind(StrEnum):
    PRODUCT = "produkt"
    NEW_PRODUCT = "nowy_produkt"
    SPLIT = "rozdziel"
    AMBIGUOUS = "niejednoznaczne"


class ProductDecision(BaseModel):
    nr: int = Field(description="numer pozycji z listy")
    decyzja: DecisionKind
    produkt_id: str | None = Field(default=None, description="tylko przy decyzji 'produkt' — id z listy kandydatów")
    skladniki: list[str] = Field(default_factory=list, description="przy 'rozdziel' — nazwy produktów składowych")
    proponowana_nazwa: str | None = Field(default=None, description="przy 'nowy_produkt' — polska nazwa produktu")
    kategoria: Category | None = None
    stan: State | None = None
    uzasadnienie: str = Field(description="1 zdanie po polsku")


class ProductDecisions(BaseModel):
    decyzje: list[ProductDecision]


SYSTEM = """Jesteś asystentem dietetyka i porządkujesz nazwy produktów spożywczych w polskiej aplikacji do jadłospisów.
Dla każdej pozycji dostajesz nazwę z jadłospisu i listę kandydatów z bazy (id, nazwa, kategoria, stan, wartości na 100 g).

Zasady:
1. "produkt" — tylko gdy któryś kandydat to TEN SAM produkt. Podaj jego produkt_id dokładnie z listy kandydatów.
2. Warianty zmieniające wartości odżywcze to INNY produkt: light/lekki, chudy, % tłuszczu, kiszony, wędzony,
   proteinowy, grecki, słodzony, w oleju vs w sosie własnym, suchy vs ugotowany. Wtedy "nowy_produkt",
   chyba że kandydat ma dokładnie ten wariant.
3. "rozdziel" — gdy pozycja zawiera kilka produktów naraz (np. "rzodkiewka i ogórek", "szejk: odżywka + banan").
   W "skladniki" wypisz polskie nazwy produktów składowych, bez ilości.
4. "nowy_produkt" — gdy w bazie nie ma odpowiednika. Podaj proponowana_nazwa, kategoria i stan.
5. "niejednoznaczne" — gdy nazwa jest zbyt ogólna, żeby wskazać produkt (np. "ulubione warzywa").
6. Nie podajesz żadnych liczb: kalorii, makroskładników ani gramatur. Uzasadnienie: jedno krótkie zdanie po polsku."""


def _candidate_block(products: Mapping[str, Product], result: MatchResult) -> str:
    lines = []
    for c in result.candidates[:MAX_CANDIDATES]:
        p = products[c.product_id]
        n = p.per_100g
        lines.append(f"    - id={p.id} | {p.name} | kategoria={p.category.value} | stan={p.state.value} | "
                     f"na 100 g: {n.kcal:.0f} kcal, B {n.protein:.1f}, T {n.fat:.1f}, W {n.carbs:.1f}")
    return "\n".join(lines) if lines else "    (brak kandydatów w bazie)"


def resolve_with_llm(client: LLMClient, products: Mapping[str, Product], results: Sequence[MatchResult],
                     matcher: ProductMatcher) -> tuple[list[ProductDecision], list[str]]:
    """Zwraca decyzje zweryfikowane przez kod oraz listę uwag (np. model wskazał id spoza kandydatów)."""
    parts = []
    for i, r in enumerate(results, start=1):
        parts.append(f"{i}. nazwa: \"{r.query}\"\n  kandydaci:\n{_candidate_block(products, r)}")
    user = "Rozstrzygnij pozycje:\n\n" + "\n\n".join(parts)
    out = client.complete_json("rozstrzyganie_produktow", SYSTEM, user, ProductDecisions, temperature=0.0)
    notes: list[str] = []
    by_nr = {d.nr: d for d in out.value.decyzje}
    decisions: list[ProductDecision] = []
    for i, r in enumerate(results, start=1):
        d = by_nr.get(i)
        if d is None:
            notes.append(f"pozycja {i} ('{r.query}'): model jej nie rozstrzygnął")
            decisions.append(ProductDecision(nr=i, decyzja=DecisionKind.AMBIGUOUS, uzasadnienie="brak decyzji modelu"))
            continue
        allowed = {c.product_id for c in r.candidates[:MAX_CANDIDATES]}
        if d.decyzja is DecisionKind.PRODUCT and d.produkt_id not in allowed:
            notes.append(f"pozycja {i} ('{r.query}'): model wskazał '{d.produkt_id}' spoza kandydatów — odrzucone")
            d = d.model_copy(update={"decyzja": DecisionKind.AMBIGUOUS, "produkt_id": None})
        if d.decyzja is not DecisionKind.PRODUCT and d.produkt_id:
            d = d.model_copy(update={"produkt_id": None})
        if d.decyzja is DecisionKind.SPLIT:
            unresolved = [s for s in d.skladniki if matcher.match(s).status is not MatchStatus.SURE]
            if unresolved:
                notes.append(f"pozycja {i} ('{r.query}'): składniki bez pewnego dopasowania: {', '.join(unresolved)}")
        decisions.append(d)
    return decisions, notes
