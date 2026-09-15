"""Kontrola kucharza: model czyta gotowy przepis jak redaktor książki kucharskiej, zanim danie trafi do biblioteki.

Kod sprawdza liczby i zasady (produkty z bazy, białko, zakazy), ale nie oceni, czy „kurczak z bananem w sosie
sojowym” to dobre danie ani czy kroki mają sens. Tę ocenę robi model:
- "dobre" — danie zostaje bez zmian,
- "popraw_tekst" — poprawiona nazwa/opis/kroki (kod przyjmuje je tylko, jeśli przejdą te same testy co nowe dania;
  składników i ilości model zmienić nie może),
- "odrzuc" — danie nierealne, niesmaczne albo z proporcjami, których nie naprawi sam tekst.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from ..domain.dish import MEAL_LABELS_PL, DishTemplate
from ..domain.product import Product
from ..food.matcher import ProductMatcher

BATCH = 8

CHEF_SYSTEM = """Jesteś doświadczonym szefem kuchni i redaktorem polskiej książki kucharskiej. Oceniasz przepisy, zanim trafią
do aplikacji z jadłospisami dla dwóch osób (gramatury porcji dobiera osobny program — oceniaj proporcje i sens, nie kalorie).
Dla każdego dania sprawdź:
1. Czy to realne, smaczne danie, które zwykły człowiek ugotuje w domu z tych składników (albo kupi, jeśli to jedzenie na wynos).
2. Czy proporcje mają sens kulinarny — zwłaszcza w masach (grupy): ciasto, panierka, masa mielona, farsz, sos, surówka.
3. Czy kroki są kompletne, w dobrej kolejności i używają tylko składników z listy (sól, pieprz i woda są dozwolone).
4. Czy nazwa, opis, kroki i nazwy mas są poprawną, naturalną polszczyzną (z polskimi znakami) i zgadzają się ze składnikami.
5. Zasady domowników: śniadania i obiady są wytrawne — danie na słodko na śniadanie albo obiad odrzuć (na słodko tylko
   kolacje z tagiem slodkie; pula_20 to posiłki rekreacyjne typu pizza).
Oceny:
- "dobre" — bez zmian (większość poprawnych dań),
- "popraw_tekst" — tylko tekst ma błędy: podaj poprawione pola (nazwa, opis, przygotowanie, nazwy_grup w tej samej
  kolejności co masy); składników i ilości nie zmieniaj,
- "odrzuc" — dziwne połączenie smaków, danie nierealne albo proporcje, których nie naprawi poprawka tekstu.
Nie odrzucaj dań za prostotę. W polu powod napisz krótko po polsku, co jest nie tak."""


class DishReview(BaseModel):
    id: str
    ocena: Literal["dobre", "popraw_tekst", "odrzuc"]
    powod: str = ""
    nazwa: str | None = None
    opis: str | None = None
    przygotowanie: list[str] | None = Field(default=None, max_length=8)
    nazwy_grup: list[str] | None = Field(default=None, description="poprawione nazwy mas w kolejności z przepisu")


class ReviewBatch(BaseModel):
    oceny: list[DishReview]


@dataclass
class ReviewResult:
    kept: list[DishTemplate] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    corrected: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0


def dish_card(dish: DishTemplate, products: Mapping[str, Product]) -> str:
    lines = [f"[{dish.id}] {dish.name}",
             f"posiłki: {', '.join(MEAL_LABELS_PL[m].lower() for m in dish.meal_types)}; kuchnia: {dish.cuisine}; "
             f"tagi: {', '.join(dish.tags) or '—'}",
             f"opis: {dish.description or '—'}", "składniki (typowa porcja dla 1 osoby):"]
    for ing in dish.ingredients:
        name = products[ing.product_id].name if ing.product_id in products else ing.product_id
        grp = dish.group_of(ing.product_id)
        lines.append(f"- {name}: {ing.typical_g:g} g" + (f" [masa: {grp.name}]" if grp else ""))
    lines.append("przygotowanie:")
    lines += [f"{i}. {s}" for i, s in enumerate(dish.steps, 1)]
    return "\n".join(lines)


def _text_problems(dish: DishTemplate, matcher: ProductMatcher, banned: set[str]) -> list[str]:
    from .generate_dishes import TECH_WORDS, _polish_ok, foreign_name_words, mentioned_missing_products

    problems = []
    text = " ".join([dish.name, dish.description or "", *dish.steps])
    if foreign_name_words(dish.name):
        problems.append("nazwa nie jest po polsku")
    if not _polish_ok(text) or TECH_WORDS.search(text):
        problems.append("angielskie słowa albo nazwy techniczne")
    if mentioned_missing_products(dish, matcher, banned):
        problems.append("tekst wspomina produkty spoza składników")
    return problems


def apply_review(dish: DishTemplate, review: DishReview, matcher: ProductMatcher, banned: set[str],
                 other_names: set[str]) -> tuple[DishTemplate | None, str | None]:
    """Zwraca (danie po poprawce albo None przy odrzuceniu, opis zmiany). Poprawka tekstu, która nie przejdzie
    testów (obce słowa, produkty spoza składników, dubel nazwy), jest ignorowana — danie zostaje w wersji kodu."""
    from .generate_dishes import foreign_name_words

    if review.ocena == "odrzuc":
        return None, review.powod or "odrzucone przez kontrolę kucharza"
    if review.ocena != "popraw_tekst":
        return dish, None
    update: dict = {}
    if review.nazwa and review.nazwa.strip() and review.nazwa.strip() != dish.name:
        update["name"] = review.nazwa.strip()
    if review.opis and review.opis.strip() and review.opis.strip() != (dish.description or ""):
        update["description"] = review.opis.strip()
    steps = [s.strip() for s in (review.przygotowanie or []) if s and s.strip()]
    if steps and steps != dish.steps:
        update["steps"] = steps
    names = [" ".join(n.lower().split())[:60] for n in (review.nazwy_grup or []) if n and n.strip()]
    names_ok = len(set(names)) == len(names) and all(len(n) >= 2 and not foreign_name_words(n) for n in names)
    if len(names) == len(dish.groups) and names != [g.name for g in dish.groups] and names_ok:
        update["groups"] = [g.model_copy(update={"name": n}) for g, n in zip(dish.groups, names, strict=True)]
    if not update:
        return dish, None
    candidate = dish.model_copy(update=update)
    if "name" in update and update["name"].lower() in other_names:
        return dish, None
    if _text_problems(candidate, matcher, banned):
        return dish, None
    return candidate, ", ".join({"name": "nazwa", "description": "opis", "steps": "kroki", "groups": "nazwy mas"}[k]
                                for k in update)


def review_dishes(llm, dishes: list[DishTemplate], products: Mapping[str, Product], banned: set[str] | None = None,
                  existing_names: set[str] | None = None, workers: int = 4,
                  log: Callable[[str], None] = print) -> ReviewResult:
    out = ReviewResult()
    if not dishes:
        return out
    matcher = ProductMatcher(products)
    banned = banned or set()
    names = {n.lower() for n in (existing_names or set())}
    batches = [dishes[i:i + BATCH] for i in range(0, len(dishes), BATCH)]

    def call(batch: list[DishTemplate]):
        user = "Oceń te przepisy:\n\n" + "\n\n".join(dish_card(d, products) for d in batch)
        return batch, llm.complete_json("kontrola_kucharza", CHEF_SYSTEM, user, ReviewBatch, temperature=0.1,
                                        max_tokens=5000)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = [ex.submit(call, b) for b in batches]
        for fut in as_completed(futures):
            try:
                batch, res = fut.result()
            except Exception as exc:  # brak oceny nie blokuje dań, które przeszły kontrolę kodu
                log(f"kontrola kucharza niedostępna dla części dań ({exc}) — zostają bez oceny")
                continue
            out.cost_usd += res.cost_usd
            by_id = {r.id.strip(): r for r in res.value.oceny}
            for dish in batch:
                review = by_id.get(dish.id)
                if review is None:
                    out.kept.append(dish)
                    continue
                others = names | {d.name.lower() for d in out.kept if d.id != dish.id}
                fixed, note = apply_review(dish, review, matcher, banned, others)
                if fixed is None:
                    out.rejected.append({"nazwa": dish.name, "id": dish.id, "powody": [f"kucharz: {note}"]})
                    continue
                if note:
                    out.corrected.append({"id": dish.id, "zmiany": note, "powod": review.powod})
                out.kept.append(fixed)
    reviewed = {d.id for b in batches for d in b}
    order = {d.id: i for i, d in enumerate(dishes)}
    out.kept.sort(key=lambda d: order.get(d.id, len(order)))
    missing = [d for d in dishes if d.id in reviewed and d.id not in {k.id for k in out.kept}
               and d.id not in {r["id"] for r in out.rejected}]
    out.kept.extend(missing)  # partie z błędem połączenia — dania zostają
    return out
