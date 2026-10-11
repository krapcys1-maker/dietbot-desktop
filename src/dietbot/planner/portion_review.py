"""Kontrola kucharza po silniku: model czyta policzone porcje i zgłasza dziwne proporcje na talerzu.

Silnik trzyma widełki, kotwice i proporcje mas, ale nie wie, że „300 g ogórka w mizerii do kotleta” wygląda dziwnie.
Model nie zmienia liczb: poważna uwaga zawęża zakres tego składnika w daniu (za dużo → najwyżej 80% tego, co było
na talerzu i 120% przepisu; za mało → co najmniej 125% talerza i 85% przepisu), a silnik liczy dotknięte dni od nowa.
Nową wersję bierzemy tylko wtedy, gdy silnik mieści się w widełkach, a niezależny weryfikator nie ma zastrzeżeń —
w przeciwnym razie zostaje poprzednia, a uwaga trafia do ostrzeżeń planu. Drobne uwagi (kwestia gustu) zapisujemy
tylko w dzienniku.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..domain.assumptions import DAY_LABELS_PL, Assumptions, Weekday
from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType
from ..domain.plan import PlannedMeal, WeekPlan
from ..domain.product import Product
from ..textfmt import fmt_num
from ..textnumbers import without_numbers

TIGHT_MAX = 1.2  # „za dużo”: składnik najwyżej 120% typowej porcji (przed skalowaniem na osobę)…
TIGHT_MIN = 0.85  # „za mało”: składnik co najmniej 85% typowej porcji…
FROM_PLATE_MAX = 0.8  # …i najwyżej 80% tego, co było na talerzu (w gramach dla osoby 2000 kcal)
FROM_PLATE_MIN = 1.25  # …i co najmniej 125% tego, co było na talerzu

PORTION_SYSTEM = """Jesteś szefem kuchni. Program policzył gramatury tygodniowego jadłospisu dla domowników — porcje różnią się,
bo osoby mają inne cele kalorii. Kalorie i białko są już sprawdzone; oceniasz WYŁĄCZNIE, czy talerz ma sens kulinarny:
- czy proporcje składników w daniu są normalne (sos do makaronu, surówka do mięsa, ser na kanapce, dodatki do mięsa),
- czy któregoś składnika nie ma absurdalnie dużo albo mało jak na to danie (np. 300 g ogórka w mizerii do kotleta,
  15 g makaronu do obiadu, 4 jajka w sałatce dla jednej osoby).
Porcje białka są duże CELOWO (każdy posiłek ma dać określoną ilość białka, patrz niżej), a talerz ma limit masy —
duża porcja chudego mięsa, ryby, twarogu, skyru czy serka nie jest błędem. Nie zgłaszaj też, że porcja jednej osoby
jest większa niż drugiej. Zgłaszaj tylko wyraźne problemy kulinarne — większość posiłków jest w porządku,
a pusta lista to dobra odpowiedź.
Dla każdej uwagi podaj: slot (klucz w nawiasie kwadratowym), produkt (id w nawiasie kwadratowym przy składniku),
kierunek (za_duzo albo za_malo), problem (krótko po polsku) i wagę: "powazna" — danie w tej postaci byłoby dziwne
do zjedzenia, "drobna" — kwestia gustu."""


class PortionIssue(BaseModel):
    slot: str = Field(description="klucz slotu, np. 3_obiad")
    produkt: str = Field(description="id produktu z listy tego posiłku")
    kierunek: Literal["za_duzo", "za_malo"]
    problem: str
    waga: Literal["drobna", "powazna"]

    @field_validator("problem")
    @classmethod
    def _plain(cls, v: str) -> str:
        # opis trafia do ostrzeżeń planu — liczby („ma 650 kcal”) podaje tylko program (audyt S12)
        return without_numbers(v) or "porcja wygląda nietypowo"


class PortionReview(BaseModel):
    uwagi: list[PortionIssue] = Field(default_factory=list)


def slot_key(meal: PlannedMeal, plan: WeekPlan) -> str:
    if meal.batch_group:
        days = sorted({int(m.day) for m in plan.meals if m.batch_group == meal.batch_group})
        return "-".join(str(d) for d in days) + f"_{meal.meal.value}"
    return f"{int(meal.day)}_{meal.meal.value}"


def slot_label(key: str) -> str:
    days, _, meal = key.partition("_")
    return (", ".join(DAY_LABELS_PL[Weekday(int(d))] for d in days.split("-")) + " — "
            + MEAL_LABELS_PL[MealType(meal)].lower())


def build_portion_prompt(plan: WeekPlan, dishes: Mapping[str, DishTemplate], products: Mapping[str, Product],
                         a: Assumptions) -> tuple[str, dict[str, PlannedMeal]]:
    names = {p.id: p.name for p in a.persons}
    persons = "; ".join(f"{p.name}: ok. {p.energy_center:.0f} kcal dziennie, {p.protein_anchor.min:.0f}–"
                        f"{p.protein_anchor.max:.0f} g białka na posiłek" for p in a.persons)
    lines = [f"Osoby: {persons}", ""]
    slots: dict[str, PlannedMeal] = {}
    for m in plan.meals:
        key = slot_key(m, plan)
        if key in slots:
            continue  # danie na zapas — te same porcje w kolejne dni
        slots[key] = m
        dish = dishes.get(m.dish_id)
        lines.append(f"[{key}] {dish.name if dish else m.dish_id}")
        for portion in m.portions:
            items = [f"{products[i.product_id].name} [{i.product_id}] {fmt_num(i.grams, 0)} g" for i in portion.items
                     if i.product_id in products and not products[i.product_id].negligible and i.grams > 0]
            lines.append(f"  {names.get(portion.person_id, portion.person_id)}: " + ", ".join(items))
    return "\n".join(lines), slots


def review_portions(llm, plan: WeekPlan, dishes: Mapping[str, DishTemplate], products: Mapping[str, Product],
                    a: Assumptions) -> tuple[list[PortionIssue], dict[str, PlannedMeal], float]:
    prompt, slots = build_portion_prompt(plan, dishes, products, a)
    res = llm.complete_json("kontrola_porcji", PORTION_SYSTEM, prompt, PortionReview, temperature=0.1, max_tokens=2500)
    seen: set[tuple[str, str]] = set()
    issues: list[PortionIssue] = []
    for issue in res.value.uwagi:
        issue.slot, issue.produkt = issue.slot.strip(), issue.produkt.strip()
        meal = slots.get(issue.slot)
        if meal is None or (issue.slot, issue.produkt) in seen:
            continue
        dish = dishes.get(meal.dish_id)
        in_meal = any(i.product_id == issue.produkt for p in meal.portions for i in p.items)
        if dish is None or not in_meal or all(i.product_id != issue.produkt for i in dish.ingredients):
            continue  # model wskazał produkt spoza posiłku — pomijamy
        seen.add((issue.slot, issue.produkt))
        issues.append(issue)
    return issues, slots, res.cost_usd


def tighten(dish: DishTemplate, product_id: str, direction: str,
            plate_ref_g: float | None = None) -> DishTemplate | None:
    """Kopia dania z węższym zakresem składnika; None, gdy zakres już jest tak wąski albo zawężenie jest sprzeczne.

    `plate_ref_g` — ile składnika było na talerzu, w gramach przeliczonych na osobę 2000 kcal: zawężamy względem
    talerza, który ocenił kucharz (gdy sam przepis ma zawyżoną porcję, limit z przepisu niczego by nie zmienił)."""
    ingredient = next((i for i in dish.ingredients if i.product_id == product_id), None)
    if ingredient is None or ingredient.typical_g <= 0:
        return None
    if direction == "za_duzo":
        new_max = round(ingredient.typical_g * TIGHT_MAX, 1)
        if plate_ref_g:
            new_max = round(min(new_max, plate_ref_g * FROM_PLATE_MAX), 1)
        if (ingredient.max_g is not None and ingredient.max_g <= new_max) or \
                (ingredient.min_g is not None and ingredient.min_g > new_max):
            return None
        updated = ingredient.model_copy(update={"max_g": new_max})
    else:
        new_min = round(ingredient.typical_g * TIGHT_MIN, 1)
        if plate_ref_g:
            new_min = round(max(new_min, plate_ref_g * FROM_PLATE_MIN), 1)
            if ingredient.max_g is not None:
                new_min = min(new_min, ingredient.max_g)
        if (ingredient.min_g is not None and ingredient.min_g >= new_min) or \
                (ingredient.max_g is not None and ingredient.max_g < new_min):
            return None
        updated = ingredient.model_copy(update={"min_g": new_min})
    return dish.model_copy(update={"ingredients": [updated if i.product_id == product_id else i
                                                   for i in dish.ingredients]})


def grams_in_slot(plan: WeekPlan, key: str, product_id: str) -> dict[str, float]:
    for m in plan.meals:
        if slot_key(m, plan) == key:
            return {p.person_id: sum(i.grams for i in p.items if i.product_id == product_id) for p in m.portions}
    return {}
