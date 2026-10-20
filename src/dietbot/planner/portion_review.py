"""Kontrola kucharza po silniku: model czyta policzone porcje i zgłasza dziwne proporcje na talerzu.

Silnik trzyma widełki, kotwice i proporcje mas, ale nie wie, że „300 g ogórka w mizerii do kotleta” wygląda dziwnie.
Model nie zmienia liczb: poważna uwaga zawęża zakres tego składnika w daniu (za dużo → najwyżej 80% tego, co było
na talerzu i 120% przepisu; za mało → co najmniej 125% talerza i 85% przepisu), a silnik liczy dotknięte dni od nowa.
Nową wersję bierzemy tylko wtedy, gdy silnik mieści się w widełkach, a niezależny weryfikator nie ma zastrzeżeń —
w przeciwnym razie zostaje poprzednia, a uwaga trafia do ostrzeżeń planu. Drobne uwagi (kwestia gustu) zapisujemy
tylko w dzienniku.

Ten sam mechanizm obsługuje uwagi domowników z czatu („za mało mleczka w curry”) — `DietService.adjust_portions`;
zawężenia zapisane przy planie (`korekty_porcji`) obowiązują też przy późniejszej zamianie dania w tych dniach.
`plate_findings` to wnioski kulinarne bez modelu: składniki daleko od przepisu i talerze, które mogą wyjść suche.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..domain.assumptions import DAY_LABELS_PL, Assumptions, Weekday
from ..domain.culinary import SAUCE_LIQUIDS, SAUCE_MIN_G
from ..domain.dish import MEAL_LABELS_PL, DishIngredient, DishTag, DishTemplate, MealType, Role
from ..domain.plan import PlannedMeal, WeekPlan
from ..domain.product import Category, Product
from ..textfmt import fmt_num
from ..textnumbers import without_numbers

TIGHT_MAX = 1.2  # „za dużo”: składnik najwyżej 120% typowej porcji (przed skalowaniem na osobę)…
TIGHT_MIN = 0.85  # „za mało”: składnik co najmniej 85% typowej porcji…
FROM_PLATE_MAX = 0.8  # …i najwyżej 80% tego, co było na talerzu (w gramach dla osoby 2000 kcal)
FROM_PLATE_MIN = 1.25  # …i co najmniej 125% tego, co było na talerzu
USER_FLOOR = 0.5  # prośba domownika: maksimum najniżej do połowy typowej porcji, nawet poniżej minimum przepisu…
USER_CEILING = 1.6  # …i najwyżej do 160% typowej porcji, nawet ponad maksimum przepisu

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
            plate_ref_g: float | None = None, *, beyond_recipe: bool = False) -> DishTemplate | None:
    """Kopia dania z węższym zakresem składnika; None, gdy zakres już jest tak wąski albo zawężenie jest sprzeczne.

    `plate_ref_g` — ile składnika było na talerzu, w gramach przeliczonych na osobę 2000 kcal: zawężamy względem
    talerza, który ocenił kucharz (gdy sam przepis ma zawyżoną porcję, limit z przepisu niczego by nie zmienił).
    `beyond_recipe` — prośba domownika z czatu („za dużo tofu”), gdy talerz już stoi na granicy przepisu (przepis bywa
    za ciężki w samych danych): granica przepisu ustępuje, ale nie dalej niż do USER_FLOOR / USER_CEILING typowej
    porcji. Limit produktu (np. trzy jajka) i limity z notatnika dalej obowiązują — liczy je silnik."""
    ingredient = next((i for i in dish.ingredients if i.product_id == product_id), None)
    if ingredient is None or ingredient.typical_g <= 0:
        return None
    typical, lo, hi = ingredient.typical_g, ingredient.min_g, ingredient.max_g
    if direction == "za_duzo":
        new_max = typical * TIGHT_MAX
        if plate_ref_g:
            new_max = min(new_max, plate_ref_g * FROM_PLATE_MAX)
        if beyond_recipe:
            new_max = max(new_max, typical * USER_FLOOR)
            if lo is None or lo > new_max:
                # bez minimum w przepisie silnik trzymałby dolną granicę roli składnika; minimum wyraźnie poniżej
                # nowego maksimum, bo sztuki (jajka) zaokrągla w górę do całych
                lo = round(min(typical * USER_FLOOR, new_max * 0.6), 1)
        new_max = round(new_max, 1)
        if (hi is not None and hi <= new_max) or (lo is not None and lo > new_max):
            return None
        updated = ingredient.model_copy(update={"max_g": new_max, "min_g": lo})
    else:
        new_min = typical * TIGHT_MIN
        if plate_ref_g:
            new_min = max(new_min, plate_ref_g * FROM_PLATE_MIN)
        if beyond_recipe:
            new_min = min(new_min, typical * USER_CEILING)
            if hi is not None and hi < new_min:
                hi = round(typical * USER_CEILING, 1)
        elif plate_ref_g and hi is not None:
            new_min = min(new_min, hi)
        new_min = round(new_min, 1)
        if (lo is not None and lo >= new_min) or (hi is not None and hi < new_min):
            return None
        updated = ingredient.model_copy(update={"min_g": new_min, "max_g": hi})
    return dish.model_copy(update={"ingredients": [updated if i.product_id == product_id else i
                                                   for i in dish.ingredients]})


def grams_in_slot(plan: WeekPlan, key: str, product_id: str) -> dict[str, float]:
    for m in plan.meals:
        if slot_key(m, plan) == key:
            return {p.person_id: sum(i.grams for i in p.items if i.product_id == product_id) for p in m.portions}
    return {}


# ---------------------------------------------------------------- korekty proporcji z czatu (bez modelu językowego)

def plate_reference(meal: PlannedMeal, product_id: str, direction: str, a: Assumptions, reference_kcal: float,
                    person_id: str | None = None) -> float | None:
    """Ile składnika było na talerzu, w gramach przeliczonych na osobę `reference_kcal` — dla wskazanej osoby,
    a bez niej: największy talerz przy „za dużo”, najmniejszy przy „za mało” (jak w kontroli kucharza)."""
    refs = {p.person_id: sum(i.grams for i in p.items if i.product_id == product_id) * reference_kcal
            / a.person(p.person_id).energy_center for p in meal.portions if p.person_id in {x.id for x in a.persons}}
    if person_id in refs:
        return refs[person_id]
    if not refs:
        return None
    return max(refs.values()) if direction == "za_duzo" else min(refs.values())


def correction_entry(dish: DishTemplate, product_id: str) -> dict:
    """Zawężony zakres składnika do zapisu przy planie — kolejna zamiana w tym dniu liczy danie z tą korektą."""
    ing = next(i for i in dish.ingredients if i.product_id == product_id)
    return {"danie": dish.id, "produkt": product_id, "typowo_g": ing.typical_g, "min_g": ing.min_g, "max_g": ing.max_g}


def apply_corrections(dishes: Mapping[str, DishTemplate], entries: list[dict]) -> dict[str, DishTemplate]:
    """Kopie dań z korektami proporcji zapisanymi przy planie (tylko dania z `dishes`). Korekty pomijamy, gdy przepis
    od tego czasu się zmienił (inna typowa porcja) — poprawiony przepis ma pierwszeństwo przed starą korektą."""
    out: dict[str, DishTemplate] = {}
    for e in entries or []:
        dish = out.get(e.get("danie")) or dishes.get(e.get("danie"))
        if dish is None:
            continue
        ing = next((i for i in dish.ingredients if i.product_id == e.get("produkt")), None)
        lo, hi = e.get("min_g"), e.get("max_g")
        if ing is None or ing.typical_g != e.get("typowo_g") or (lo is not None and hi is not None and lo > hi):
            continue
        updated = ing.model_copy(update={"min_g": lo, "max_g": hi})
        out[dish.id] = dish.model_copy(update={"ingredients": [updated if i.product_id == ing.product_id else i
                                                               for i in dish.ingredients]})
    return out


def merge_corrections(old: list[dict], new: list[dict], dish_ids: set[str]) -> list[dict]:
    """Korekty dań nadal obecnych w planie; nowsza korekta tego samego składnika zastępuje starszą."""
    merged = {(e["danie"], e["produkt"]): e for e in [*(old or []), *new] if e.get("danie") in dish_ids}
    return list(merged.values())


DEVIATION = 0.5  # „zweryfikuj gramatury”: składnik odbiega od przepisu o ponad połowę
DRY_SHARE = 0.7  # sos względem ryżu/makaronu i mięsa/tofu poniżej 70% proporcji przepisu — danie może wyjść suche


@dataclass
class PlateFinding:
    slot: str
    dish_id: str
    person_id: str
    product_ids: list[str]
    grams: float  # na talerzu (dla sprawdzenia „suche”: gramy sosu)
    recipe_g: float  # porcja przepisu przeliczona na kalorie osoby (dla „suche”: sos przepisu przy tych dodatkach)

    @property
    def ratio(self) -> float:
        return self.grams / self.recipe_g if self.recipe_g > 0 else 0.0


def _sauce_items(dish: DishTemplate, products: Mapping[str, Product]) -> list[DishIngredient]:
    return [i for i in dish.ingredients if i.product_id in products and i.typical_g > 0 and i.role is not Role.SPICE
            and not products[i.product_id].negligible and (i.role is Role.SAUCE or i.product_id in SAUCE_LIQUIDS)]


def plate_findings(plan: WeekPlan, dishes: Mapping[str, DishTemplate], products: Mapping[str, Product],
                   a: Assumptions, reference_kcal: float,
                   threshold: float = DEVIATION) -> tuple[list[PlateFinding], list[PlateFinding]]:
    """Wnioski kulinarne o policzonym planie, bez modelu językowego: (składniki odbiegające od przepisu o ponad
    `threshold`, talerze z sosem wyraźnie skąpszym względem dodatków niż w przepisie). Porcja przepisu jest
    przeliczona na kalorie osoby, jak w silniku; posiłki na zapas liczymy raz."""
    persons = {p.id: p for p in a.persons}
    deviations: list[PlateFinding] = []
    dry: list[PlateFinding] = []
    seen: set[str] = set()
    for m in plan.meals:
        key = slot_key(m, plan)
        dish = dishes.get(m.dish_id)
        if key in seen or dish is None:
            continue
        seen.add(key)
        sauce = _sauce_items(dish, products)
        in_sauce = {i.product_id for i in sauce}
        mains = [i for i in dish.ingredients if i.role in (Role.PROTEIN, Role.CARBS) and i.product_id not in in_sauce
                 and i.product_id in products and i.typical_g > 0
                 and products[i.product_id].category is not Category.BREAD]
        for portion in m.portions:
            person = persons.get(portion.person_id)
            if person is None:
                continue
            scale = person.energy_center / reference_kcal
            grams: dict[str, float] = {}
            for it in portion.items:
                grams[it.product_id] = grams.get(it.product_id, 0.0) + it.grams
            for ing in dish.ingredients:
                product = products.get(ing.product_id)
                if product is None or product.negligible or ing.role is Role.SPICE or ing.typical_g <= 0:
                    continue
                recipe = ing.typical_g * (scale if ing.scales else 1.0)
                got = grams.get(ing.product_id, 0.0)
                if (got > 0 or not ing.optional) and abs(got / recipe - 1) > threshold:
                    deviations.append(PlateFinding(key, dish.id, person.id, [ing.product_id], got, recipe))
            recipe_sauce = sum(i.typical_g for i in sauce)
            recipe_main = sum(i.typical_g for i in mains)
            plate_main = sum(grams.get(i.product_id, 0.0) for i in mains)
            if (recipe_sauce < SAUCE_MIN_G or recipe_main <= 0 or plate_main <= 0 or MealType.SNACK in dish.meal_types
                    or dish.has_tag(DishTag.SOUP)):
                continue
            plate_sauce = sum(grams.get(i.product_id, 0.0) for i in sauce)
            expected = plate_main * recipe_sauce / recipe_main  # tyle sosu do tych dodatków według przepisu
            if plate_sauce < expected * DRY_SHARE:
                dry.append(PlateFinding(key, dish.id, person.id, [i.product_id for i in sauce], plate_sauce, expected))
    return deviations, dry
