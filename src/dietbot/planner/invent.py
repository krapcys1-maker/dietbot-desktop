"""Wymyślanie nowych dań na prośbę domowników („kuchnia włoska 2 razy, zupa, resztę wymyśl”).

1. Model zamienia prośbę na listę zamówień: posiłek, kuchnia, ile dań, szczegóły.
2. Generator tworzy przepisy WYŁĄCZNIE z produktów z bazy (typowe porcje, kroki po polsku).
3. Kod sprawdza każde danie tymi samymi zasadami co całą bibliotekę (produkty, białko, zakazy, polszczyzna, duble);
   odrzucone dostają jedną rundę poprawek.
Liczb nie podaje model — gramatury w planie liczy silnik.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from ..domain.assumptions import Assumptions
from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType
from ..llm.generate_dishes import GenOrder, run_generation_plan
from ..textnorm import normalize

MAX_PER_BATCH = 6
SOUP_DETAILS = "zupa na zapas na dwa dni (tagi zupa, na_zapas, na_cieplo; na_ile_dni 2), sycąca, z wkładką białkową"


class InventItem(BaseModel):
    posilek: Literal["sniadanie", "obiad", "kolacja"]
    kuchnia: str = Field(description="jedna z kuchni z listy")
    ile: int = Field(ge=1, le=MAX_PER_BATCH)
    szczegoly: str = Field(default="", description="krótko po polsku, czego dotyczy zamówienie (np. „z krewetkami”)")
    zupa_na_zapas: bool = Field(default=False, description="true, gdy chodzi o zupę na obiad gotowaną na dwa dni")


class InventionPlan(BaseModel):
    zamowienia: list[InventItem] = Field(min_length=1, max_length=8)


INVENT_SYSTEM = """Jesteś szefem kuchni w polskiej aplikacji z jadłospisami dla domowników. Domownicy proszą o NOWE dania.
Zamień prośbę na listę zamówień dla kucharza: posiłek (sniadanie / obiad / kolacja), kuchnia, ile dań, szczegóły.
Zasady:
- Łącznie wszystkich dań najwyżej {max_total}.
- Wiernie oddaj prośbę: „kuchnia włoska 2 razy” = dwa dania kuchni włoskiej (najczęściej obiady lub kolacje).
  Liczba przy kuchni dotyczy całego tygodnia — pozostałe dania (także z „resztę wymyśl”) są z INNYCH kuchni.
- „zupa” = zupa na obiad gotowana na zapas na dwa dni (zupa_na_zapas: true, posilek: obiad) — najwyżej jedna zupa;
  pozostałe zamówienia to NIE zupy (danie kuchni włoskiej to np. makaron, a nie zupa).
- „resztę wymyśl” / „wymyśl cały tydzień” = dołóż zróżnicowane dania do wymienionych, tak żeby łącznie było
  mniej więcej po równo śniadań, obiadów i kolacji (śniadania wytrawne, kolacje lekkie), w różnych kuchniach.
- Gdy prośba jest ogólna („wymyśl coś nowego z krewetkami”), dopasuj posiłki do produktu.
- Kuchnia tylko z listy: {cuisines}.
- Szczegóły krótko, po polsku, bez liczb kalorii."""


@dataclass
class InventionResult:
    accepted: list[DishTemplate] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    orders: list[InventItem] = field(default_factory=list)
    cost_usd: float = 0.0


def _cuisine_key(value: str, a: Assumptions) -> str:
    key = normalize(value).replace(" ", "_")
    if not key:
        return "miedzynarodowa"
    for c in a.cuisines:
        if key.startswith(c[:5]) or c.startswith(key[:5]):
            return c
    return "miedzynarodowa"


def plan_invention(llm, request: str, a: Assumptions, max_total: int = 9) -> tuple[list[InventItem], float]:
    system = INVENT_SYSTEM.format(max_total=max_total, cuisines=", ".join(a.cuisines))
    res = llm.complete_json("wymyslanie_dan_plan", system, f"Prośba domowników: {request}", InventionPlan,
                            temperature=0.2, max_tokens=800)
    orders: list[InventItem] = []
    total = 0
    soups_left = max(1, len(a.week.batch))  # tydzień ma tyle miejsc na zupę na zapas — więcej zup i tak by nie weszło
    for item in res.value.zamowienia:
        if total >= max_total:
            break
        if item.zupa_na_zapas:
            if soups_left <= 0:
                continue
            item.ile = min(item.ile, soups_left)
            soups_left -= item.ile
            item.posilek = "obiad"
        item.ile = min(item.ile, max_total - total)
        item.kuchnia = _cuisine_key(item.kuchnia, a)
        orders.append(item)
        total += item.ile
    return orders, res.cost_usd


def invent_dishes(repo, llm, request: str, max_total: int = 9, log=print) -> InventionResult:
    """Wymyśla i sprawdza nowe dania; nie zapisuje ich (robi to warstwa usług)."""
    orders, cost = plan_invention(llm, request, repo.assumptions, max_total)
    out = InventionResult(orders=orders, cost_usd=cost)
    if not orders:
        return out
    log("Zamówienia dla kucharza: " + "; ".join(
        f"{MEAL_LABELS_PL[MealType(o.posilek)].lower()} × {o.ile} ({o.kuchnia}"
        + (", zupa na zapas" if o.zupa_na_zapas else "") + (f", {o.szczegoly}" if o.szczegoly else "") + ")"
        for o in orders))
    plan = []
    for o in orders:
        details = SOUP_DETAILS if o.zupa_na_zapas else ("danie główne na talerzu, NIE zupa" if o.posilek == "obiad" else "")
        special = "; ".join(x for x in (o.szczegoly, details, f"prośba domowników: {request}") if x)
        # zapas propozycji: część odpada w kontroli (duble, za mało białka), a przyjmujemy tylko zamówioną liczbę
        plan.append(GenOrder(MealType(o.posilek), o.kuchnia, ask=o.ile + max(2, o.ile // 2), special=special, keep=o.ile))
    source = f"wymyślone na prośbę ({date.today():%d.%m.%Y}): {request.strip()[:160]}"
    accepted, rejected, gen_cost = run_generation_plan(repo, llm, plan=plan, workers=min(8, len(plan)), log=log,
                                                       source=source, review=True, engine_check=True)
    out.accepted, out.rejected, out.cost_usd = accepted, rejected, cost + gen_cost
    return out
