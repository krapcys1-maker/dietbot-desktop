"""Diagnostyka rozwiązania: sumy, naruszenia, zakres możliwości dania, wiążące limity, powody przekąsek."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..domain.assumptions import Assumptions, Weekday
from ..domain.dish import DishTemplate
from ..domain.nutrition import BAND_TOLERANCE
from ..domain.plan import PlannedMeal, PlannedSnack
from ..textfmt import fmt_num
from .catalog import Catalog
from .model import METRICS, BlockModel, BlockSolution
from .types import AnchorViolation, BandViolation, BindingBound, EngineSlot, MassViolation, Totals

METRIC_PL = {"kcal": "energia", "protein": "białko", "fat": "tłuszcz", "carbs": "węglowodany"}
METRIC_PL_GEN = {"kcal": "energii", "protein": "białka", "fat": "tłuszczu", "carbs": "węglowodanów"}  # dopełniacz
METRIC_PL_ACC = {"kcal": "energię", "protein": "białko", "fat": "tłuszcz", "carbs": "węglowodany"}  # biernik
UNIT = {"kcal": "kcal", "protein": "g", "fat": "g", "carbs": "g"}
# ta sama tolerancja co w weryfikatorze — przy widełkach punktowych (min = max) silnik z luźniejszą tolerancją
# zgłaszał „ok”, a weryfikator błąd
_TOL = BAND_TOLERANCE
# minimalny wpływ jednego kroku składnika, żeby uznać go za istotny limit (np. szczypiorek nie jest)
_BINDING_MIN_PER_STEP = {"kcal": 5.0, "protein": 0.5, "fat": 0.5, "carbs": 1.0}


def items_totals(catalog: Catalog, items: Mapping[str, float]) -> dict[str, float]:
    out = dict.fromkeys((*METRICS, "fiber"), 0.0)
    for pid, grams in items.items():
        f = catalog.facts(pid)
        for m in out:
            out[m] += f.metric(m) * grams
    return out


def day_totals(
    a: Assumptions,
    catalog: Catalog,
    day: Weekday,
    person_id: str,
    meals: Sequence[PlannedMeal],
    snacks: Sequence[PlannedSnack],
    include_snacks: bool = True,
) -> dict[str, float]:
    total = items_totals(catalog, {i.product_id: i.grams for i in a.day.fixed_items})
    for meal in meals:
        if meal.day != day:
            continue
        portion = meal.portion(person_id)
        t = items_totals(catalog, {i.product_id: i.grams for i in portion.items})
        for k in total:
            total[k] += t[k]
    if include_snacks:
        for s in snacks:
            if s.day == day and s.person_id == person_id:
                t = items_totals(catalog, {i.product_id: i.grams for i in s.items})
                for k in total:
                    total[k] += t[k]
    return total


def band_check(a: Assumptions, person_id: str, day: Weekday, totals: Mapping[str, float]) -> list[BandViolation]:
    person = a.person(person_id)
    out = []
    for m in METRICS:
        band = person.targets.band(m)
        v = totals[m]
        # luka z dokładnością 0,01 — przy zaokrągleniu do 0,1 przekroczenie o 0,04 g wyglądało jak brak problemu
        # i naprawa planu go pomijała, a weryfikator słusznie zgłaszał błąd
        if v < band.min - _TOL[m]:
            out.append(BandViolation(day=day, person_id=person_id, metric=m, actual=round(v, 2),
                                     min=band.min, max=band.max, gap=round(v - band.min, 2)))
        elif v > band.max + _TOL[m]:
            out.append(BandViolation(day=day, person_id=person_id, metric=m, actual=round(v, 2),
                                     min=band.min, max=band.max, gap=round(v - band.max, 2)))
    return out


def analyze(
    bm: BlockModel,
    sol: BlockSolution,
    slots: Sequence[EngineSlot],
    meals: Sequence[PlannedMeal],
    snacks: Sequence[PlannedSnack],
    dishes: Mapping[str, DishTemplate],
) -> tuple[list[Totals], list[BandViolation], list[AnchorViolation], list[BindingBound], list[MassViolation]]:
    a, catalog = bm.a, bm.catalog
    totals: list[Totals] = []
    violations: list[BandViolation] = []
    for day in bm.days:
        for pid in bm.person_ids:
            t = day_totals(a, catalog, day, pid, meals, snacks)
            v = band_check(a, pid, day, t)
            violations.extend(v)
            totals.append(Totals(day=day, person_id=pid, kcal=round(t["kcal"], 1), protein=round(t["protein"], 1),
                                 fat=round(t["fat"], 1), carbs=round(t["carbs"], 1), fiber=round(t["fiber"], 1),
                                 in_bands=not v))

    anchors: list[AnchorViolation] = []
    for slot in slots:
        dish_id = sol.choices[slot.key]
        dish = dishes[dish_id]
        if dish.is_treat:
            continue
        meal = next(m for m in meals if m.day == slot.days[0] and m.meal == slot.meal)
        for pid in bm.person_ids:
            person = a.person(pid)
            anchor = person.protein_anchor
            tol = person.protein_anchor_tolerance
            protein = items_totals(catalog, {i.product_id: i.grams for i in meal.portion(pid).items})["protein"]
            if anchor.min - tol - _TOL["protein"] <= protein <= anchor.max + tol + _TOL["protein"]:
                continue
            lo, hi = protein_envelope(bm, slot.key, dish_id, pid)
            gap = protein - anchor.min if protein < anchor.min else protein - anchor.max
            anchors.append(AnchorViolation(
                days=slot.days, meal=slot.meal, dish_id=dish_id, person_id=pid, protein_g=round(protein, 1),
                min=anchor.min, max=anchor.max, gap_g=round(gap, 1), dish_protein_range_g=(round(lo, 1), round(hi, 1)),
                structural=hi < anchor.min or lo > anchor.max,
            ))

    masses: list[MassViolation] = []
    tol = bm.settings.meal_mass_tolerance
    for slot in slots:
        dish_id = sol.choices[slot.key]
        meal = next(m for m in meals if m.day == slot.days[0] and m.meal == slot.meal)
        for pid in bm.person_ids:
            grams = sum(i.grams for i in meal.portion(pid).items)
            cap = a.person(pid).meal_mass_cap(slot.meal)
            if grams > cap * (1 + tol):
                masses.append(MassViolation(days=slot.days, meal=slot.meal, dish_id=dish_id, person_id=pid,
                                            grams=round(grams), cap=cap))

    binding: list[BindingBound] = []
    for viol in violations:
        want_more = viol.gap < 0
        for slot in slots:
            if viol.day not in slot.days:
                continue
            dish_id = sol.choices[slot.key]
            for iv in bm.ings:
                if iv.slot_key != slot.key or iv.dish_id != dish_id or iv.kind == "fixed":
                    continue
                if viol.person_id not in iv.grams_per_step:
                    continue
                per_step_g = iv.grams_per_step[viol.person_id]
                f = catalog.facts(iv.product_id)
                if f.metric(viol.metric) * per_step_g < _BINDING_MIN_PER_STEP[viol.metric]:
                    continue
                value = sol.ingredient_values[iv.var_index]
                at_max, at_min = value >= iv.bounds.hi, value <= iv.bounds.lo
                if (want_more and at_max) or (not want_more and at_min):
                    binding.append(BindingBound(
                        violation=f"{viol.metric}:{viol.person_id}:{int(viol.day)}", days=slot.days, meal=slot.meal,
                        dish_id=dish_id, product_id=iv.product_id, person_id=viol.person_id,
                        grams=round(value * per_step_g, 1), bound="max" if want_more else "min",
                        per_step={"g": round(per_step_g, 1), **{m: round(f.metric(m) * per_step_g, 2) for m in METRICS}},
                    ))
    return totals, violations, anchors, binding, masses


def protein_envelope(bm: BlockModel, slot_key: str, dish_id: str, person_id: str) -> tuple[float, float]:
    """Najmniej i najwięcej białka, jakie danie może dać osobie w swoich zakresach gramatur."""
    lo = hi = 0.0
    for iv in bm.ings:
        if iv.slot_key != slot_key or iv.dish_id != dish_id:
            continue
        p = bm.catalog.facts(iv.product_id).protein
        if iv.kind == "fixed":
            g = iv.fixed_grams.get(person_id, 0.0)
            lo, hi = lo + p * g, hi + p * g
        elif person_id in iv.grams_per_step:
            gps = iv.grams_per_step[person_id]
            lo += p * iv.bounds.lo * gps
            hi += p * iv.bounds.hi * gps
    return lo, hi


def snack_reason(a: Assumptions, catalog: Catalog, snack: PlannedSnack, meals: Sequence[PlannedMeal],
                 snacks: Sequence[PlannedSnack]) -> str:
    person = a.person(snack.person_id)
    others = [s for s in snacks if s is not snack]
    without = day_totals(a, catalog, snack.day, snack.person_id, meals, others)
    with_ = day_totals(a, catalog, snack.day, snack.person_id, meals, snacks)
    missing = []
    for m in METRICS:
        band = person.targets.band(m)
        if without[m] < band.min - _TOL[m] and with_[m] > without[m]:
            decimals = 0 if m == "kcal" else 1
            missing.append(
                f"{METRIC_PL_GEN[m]} ({fmt_num(without[m], decimals)} z min. {fmt_num(band.min)} {UNIT[m]})"
            )
    if missing:
        joined = missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + " i " + missing[-1]
        return f"Dodana, bo bez niej zabrakłoby {joined}."
    best, best_gain = None, 0.0
    for m in METRICS:
        c = person.targets.band(m).center
        gain = (abs(without[m] - c) - abs(with_[m] - c)) / max(c, 1)
        if gain > best_gain:
            best, best_gain = m, gain
    if best:
        c = person.targets.band(best).center
        decimals = 0 if best == "kcal" else 1
        return (f"Dodana, żeby przybliżyć {METRIC_PL_ACC[best]} do celu: {fmt_num(without[best], decimals)} → "
                f"{fmt_num(with_[best], decimals)} {UNIT[best]} (cel {fmt_num(c)} {UNIT[best]}).")
    return "Dodana do zbilansowania dnia."
