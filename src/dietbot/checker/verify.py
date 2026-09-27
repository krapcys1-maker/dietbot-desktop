"""Niezależny weryfikator planu tygodnia.

Zasada: ten moduł NIE importuje niczego z `dietbot.engine`. Liczy wszystko od zera z gramatur zapisanych w planie
i wartości produktów, a zasady bierze z założeń. To jego liczby pokazujemy użytkownikowi.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, Field

from ..domain.assumptions import DAY_LABELS_PL, Assumptions, Weekday
from ..domain.diet import compatible
from ..domain.dish import GROUP_MEMBER_STEP_G, MEAL_LABELS_PL, DishIngredient, DishTemplate, MealType, Role
from ..domain.nutrition import BAND_TOLERANCE, EnergyMethod
from ..domain.plan import PlannedMeal, WeekPlan
from ..domain.product import Product
from ..textfmt import fmt_num

METRICS = ("kcal", "protein", "fat", "carbs")
METRIC_PL = {"kcal": "energia", "protein": "białko", "fat": "tłuszcz", "carbs": "węglowodany"}
UNIT = {"kcal": "kcal", "protein": "g", "fat": "g", "carbs": "g"}
# Tolerancja porównań na liczbach zmiennoprzecinkowych (nie jest to "luz" w zasadach) — ta sama co w silniku
TOL = BAND_TOLERANCE
# gramatury w planie są zaokrąglone do 0,1 g na składnik
GROUP_ROUNDING_G = 0.15


class Severity(StrEnum):
    ERROR = "blad"
    WARNING = "ostrzezenie"


class Issue(BaseModel):
    code: str
    severity: Severity
    message: str
    day: Weekday | None = None
    person_id: str | None = None
    meal: MealType | None = None


class DayTotals(BaseModel):
    day: Weekday
    person_id: str
    kcal: float
    protein: float
    fat: float
    carbs: float
    fiber: float


class VerificationReport(BaseModel):
    ok: bool
    totals: list[DayTotals] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}


def _energy_per_100g(p: Product, method: EnergyMethod) -> float:
    n = p.per_100g
    if method is EnergyMethod.LABEL:
        return n.kcal
    return 4 * n.protein + 9 * n.fat + 4 * n.carbs + 7 * (n.alcohol or 0)


def nutrition_of(items: Mapping[str, float], products: Mapping[str, Product], method: EnergyMethod) -> dict[str, float]:
    out = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0, "fiber": 0.0}
    for pid, grams in items.items():
        p = products[pid]
        if p.negligible:
            continue
        f = grams / 100.0
        out["kcal"] += _energy_per_100g(p, method) * f
        out["protein"] += p.per_100g.protein * f
        out["fat"] += p.per_100g.fat * f
        out["carbs"] += p.per_100g.carbs * f
        out["fiber"] += (p.per_100g.fiber or 0.0) * f
    return out


def _step_g(ing: DishIngredient, product: Product, in_group: bool = False) -> float:
    """Krok gramatury składnika liczony od zera z danych (bez silnika): jednostka → miara, inaczej krok produktu;
    składniki grup proporcji ważymy co GROUP_MEMBER_STEP_G."""
    if in_group:
        return ing.step_g or GROUP_MEMBER_STEP_G
    if ing.step_g:
        return ing.step_g
    if ing.unit:
        m = product.measure(ing.unit)
        if m is not None:
            return m.grams
    return product.step_g


def verify_plan(
    plan: WeekPlan,
    a: Assumptions,
    products: Mapping[str, Product],
    dishes: Mapping[str, DishTemplate],
    engine_totals: list[Mapping] | None = None,
    group_tolerance: float = 0.05,
) -> VerificationReport:
    issues: list[Issue] = []
    method = a.energy.method
    person_ids = [p.id for p in a.persons]

    def err(code: str, msg: str, **kw) -> None:
        issues.append(Issue(code=code, severity=Severity.ERROR, message=msg, **kw))

    def warn(code: str, msg: str, **kw) -> None:
        issues.append(Issue(code=code, severity=Severity.WARNING, message=msg, **kw))

    # --- produkty i dania istnieją
    def known_items(items, where: str, **kw) -> dict[str, float]:
        out: dict[str, float] = {}
        for it in items:
            if it.product_id not in products:
                err("nieznany_produkt", f"{where}: produkt '{it.product_id}' nie istnieje w bazie", **kw)
                continue
            if it.grams < 0:
                err("ujemna_gramatura", f"{where}: ujemna gramatura {it.product_id}", **kw)
            if it.grams > 0 and not compatible(products[it.product_id], a.diet):
                err("rodzaj_diety", f"{where}: {products[it.product_id].name} nie pasuje do diety {a.diet}", **kw)
            out[it.product_id] = out.get(it.product_id, 0.0) + it.grams
        return out

    for fixed in a.day.fixed_items:
        if fixed.product_id in products and not compatible(products[fixed.product_id], a.diet):
            err("rodzaj_diety", f"Stała pozycja {products[fixed.product_id].name} nie pasuje do diety {a.diet}")

    # --- struktura: każdy dzień ma wszystkie posiłki, każdy posiłek porcje dla każdej osoby
    by_slot: dict[tuple[int, MealType], list[PlannedMeal]] = defaultdict(list)
    for m in plan.meals:
        by_slot[(int(m.day), m.meal)].append(m)
    for day in plan.days:
        for meal in a.day.meals:
            found = by_slot.get((int(day), meal), [])
            label = f"{DAY_LABELS_PL[day]}, {MEAL_LABELS_PL[meal].lower()}"
            if not found:
                err("brak_posilku", f"{label}: brak posiłku w planie", day=day, meal=meal)
            elif len(found) > 1:
                err("zdublowany_posilek", f"{label}: posiłek występuje {len(found)} razy", day=day, meal=meal)
    for m in plan.meals:
        label = f"{DAY_LABELS_PL[m.day]}, {MEAL_LABELS_PL[m.meal].lower()}"
        dish = dishes.get(m.dish_id)
        if dish is None:
            err("nieznane_danie", f"{label}: danie '{m.dish_id}' nie istnieje w bibliotece", day=m.day, meal=m.meal)
        elif m.meal not in dish.meal_types:
            err("zly_typ_posilku", f"{label}: '{dish.name}' nie jest przewidziane na ten posiłek", day=m.day, meal=m.meal)
        got = [p.person_id for p in m.portions]
        for pid in person_ids:
            if got.count(pid) != 1:
                err("brak_porcji", f"{label}: brak porcji (lub zdublowana) dla osoby {pid}", day=m.day, meal=m.meal,
                    person_id=pid)

    # --- sumy dzienne i widełki
    totals: list[DayTotals] = []
    for day in plan.days:
        for person in a.persons:
            day_items: dict[str, float] = {}
            for fx in a.day.fixed_items:
                day_items[fx.product_id] = day_items.get(fx.product_id, 0.0) + fx.grams
            for m in plan.meals:
                if m.day != day:
                    continue
                for portion in m.portions:
                    if portion.person_id != person.id:
                        continue
                    for pid, g in known_items(portion.items, f"{DAY_LABELS_PL[day]}", day=day).items():
                        day_items[pid] = day_items.get(pid, 0.0) + g
            for s in plan.snacks:
                if s.day == day and s.person_id == person.id:
                    for pid, g in known_items(s.items, f"{DAY_LABELS_PL[day]} przekąska", day=day).items():
                        day_items[pid] = day_items.get(pid, 0.0) + g
            n = nutrition_of({k: v for k, v in day_items.items() if k in products}, products, method)
            totals.append(DayTotals(day=day, person_id=person.id, **{k: round(v, 2) for k, v in n.items()}))
            for metric in METRICS:
                band = person.targets.band(metric)
                v = n[metric]
                if v < band.min - TOL[metric] or v > band.max + TOL[metric]:
                    err("widelki",
                        f"{DAY_LABELS_PL[day]}, {person.name}: {METRIC_PL[metric]} {fmt_num(v, 2)} {UNIT[metric]} poza "
                        f"widełkami {fmt_num(band.min)}–{fmt_num(band.max)} {UNIT[metric]}",
                        day=day, person_id=person.id)

    # --- kotwice białka (posiłki z puli 20% zwolnione)
    for m in plan.meals:
        dish = dishes.get(m.dish_id)
        if dish is None or dish.is_treat:
            continue
        for portion in m.portions:
            if portion.person_id not in person_ids:
                continue
            person = a.person(portion.person_id)
            items = {k: v for k, v in known_items(portion.items, "kotwica", day=m.day).items() if k in products}
            prot = nutrition_of(items, products, method)["protein"]
            anchor = person.protein_anchor
            tol = person.protein_anchor_tolerance
            if prot < anchor.min - tol - TOL["protein"] or prot > anchor.max + tol + TOL["protein"]:
                warn("kotwica_bialka",
                     f"{DAY_LABELS_PL[m.day]}, {MEAL_LABELS_PL[m.meal].lower()}, {person.name}: białko {fmt_num(prot, 2)} g "
                     f"poza zakresem {fmt_num(anchor.min)}–{fmt_num(anchor.max)} g (tolerancja ±{fmt_num(tol)} g)", day=m.day, meal=m.meal,
                     person_id=person.id)

    # --- masa talerza (ostrzeżenie: porcja cięższa niż limit osoby z założeń)
    for m in plan.meals:
        for portion in m.portions:
            if portion.person_id not in person_ids:
                continue
            person = a.person(portion.person_id)
            grams = sum(it.grams for it in portion.items)
            cap = person.meal_mass_cap(m.meal)
            if grams > cap * 1.02:
                warn("duza_porcja",
                     f"{DAY_LABELS_PL[m.day]}, {MEAL_LABELS_PL[m.meal].lower()}, {person.name}: porcja {fmt_num(grams, 0)} g "
                     f"— powyżej limitu {fmt_num(cap, 0)} g", day=m.day, meal=m.meal, person_id=person.id)

    # --- grupy składników (ciasto, panierka, sos): proporcje jak w przepisie
    for m in plan.meals:
        dish = dishes.get(m.dish_id)
        if dish is None or not dish.groups:
            continue
        label = f"{DAY_LABELS_PL[m.day]}, {MEAL_LABELS_PL[m.meal].lower()}"
        by_id = {i.product_id: i for i in dish.ingredients}
        skip = {pid for pid in by_id if pid not in products or products[pid].negligible}
        for portion in m.portions:
            grams = {it.product_id: it.grams for it in portion.items}
            for grp in dish.groups:
                shares = grp.shares(dish.ingredients, skip)
                total = sum(grams.get(p, 0.0) for p in shares)
                if len(shares) < 2 or total <= 0:
                    continue
                tol = grp.tolerance if grp.tolerance is not None else group_tolerance
                for p, share in shares.items():
                    g = grams.get(p, 0.0)
                    slack = _step_g(by_id[p], products[p], in_group=True) + GROUP_ROUNDING_G
                    lo, hi = (1 - tol) * share * total - slack, (1 + tol) * share * total + slack
                    if g < lo or g > hi:
                        err("proporcje_grupy",
                            f"{label}: w „{grp.name}” {products[p].name.lower()} {fmt_num(g)} g — przy masie "
                            f"{fmt_num(total, 0)} g przepis wymaga {fmt_num(max(lo, 0))}–{fmt_num(hi)} g",
                            day=m.day, meal=m.meal, person_id=portion.person_id)

    # --- zasady tygodnia
    slot_dish = {(int(m.day), m.meal): m.dish_id for m in plan.meals}
    treat_count = sum(1 for m in plan.meals if m.dish_id in dishes and dishes[m.dish_id].is_treat)
    tb = a.week.treat_meals
    full_week = len(plan.days) == 7
    if treat_count > tb.max or (full_week and treat_count < tb.min):
        err("pula_20", f"posiłki i przekąski z puli 20%: {treat_count}, a założenia to {fmt_num(tb.min)}–{fmt_num(tb.max)}")
    sweet_suppers = sum(
        1 for m in plan.meals if m.meal is MealType.SUPPER and m.dish_id in dishes and dishes[m.dish_id].is_sweet
    )
    if sweet_suppers > a.week.sweet_suppers_max:
        err("slodkie_kolacje", f"słodkie kolacje: {sweet_suppers}, maksimum {a.week.sweet_suppers_max}")

    for rule in a.week.batch:
        if not all(d in plan.days for d in rule.days):
            continue
        ids = [slot_dish.get((int(d), rule.meal)) for d in rule.days]
        label = ", ".join(DAY_LABELS_PL[d] for d in rule.days)
        if len(set(ids)) != 1 or ids[0] is None:
            err("danie_na_zapas", f"{label}: {MEAL_LABELS_PL[rule.meal].lower()} ma być tym samym daniem na zapas")
            continue
        dish = dishes.get(ids[0])
        if dish is None or rule.tag not in dish.tags or not dish.is_batch:
            err("danie_na_zapas", f"{label}: danie '{ids[0]}' nie ma tagu '{rule.tag}' lub nie jest daniem na zapas")
        meals = [m for m in plan.meals if m.meal == rule.meal and m.day in rule.days]
        for pid in person_ids:
            portions = [sorted((i.product_id, round(i.grams, 1)) for i in m.portion(pid).items) for m in meals]
            if any(p != portions[0] for p in portions[1:]):
                err("danie_na_zapas", f"{label}: porcje osoby {pid} różnią się między dniami gotowania na zapas",
                    person_id=pid)

    if a.week.no_repeats:
        groups: dict[str, set[str]] = defaultdict(set)
        for m in plan.meals:
            groups[m.dish_id].add(m.batch_group or f"{int(m.day)}_{m.meal.value}")
        for dish_id, occurrences in groups.items():
            if len(occurrences) > 1:
                name = dishes[dish_id].name if dish_id in dishes else dish_id
                err("powtorka_dania", f"danie '{name}' powtarza się w tygodniu {len(occurrences)} razy")

    # --- zakazy
    banned = set(a.prohibitions.products)
    for m in plan.meals:
        dish = dishes.get(m.dish_id)
        label = f"{DAY_LABELS_PL[m.day]}, {MEAL_LABELS_PL[m.meal].lower()}"
        for portion in m.portions:
            excluded = banned | (set(a.person(portion.person_id).excluded_products) if portion.person_id in person_ids
                                 else set())
            for it in portion.items:
                if it.product_id in excluded and it.grams > 0:
                    err("zakazany_produkt", f"{label}: zakazany produkt '{it.product_id}'", day=m.day, meal=m.meal,
                        person_id=portion.person_id)
        if dish is None:
            continue
        for tag in a.prohibitions.dish_tags_by_meal.get(m.meal, []):
            if tag in dish.tags:
                err("zakazany_tag", f"{label}: '{dish.name}' ma zakazany w tym posiłku tag '{tag}'", day=m.day,
                    meal=m.meal)
        bad_supper = set(a.prohibitions.supper_forbidden_carb_categories)
        if m.meal is MealType.SUPPER and bad_supper and not dish.is_treat and not dish.is_sweet:
            for ing in dish.ingredients:
                if ing.role is Role.CARBS and ing.product_id in products \
                        and products[ing.product_id].category.value in bad_supper:
                    err("kolacja_obiadowa", f"{label}: '{dish.name}' to danie obiadowe (węglowodany: {ing.product_id})",
                        day=m.day, meal=m.meal)
                    break
        allowed = set(a.prohibitions.allowed_carb_categories)
        if allowed and not dish.is_treat:
            for ing in dish.ingredients:
                if ing.role is Role.CARBS and ing.product_id in products:
                    cat = products[ing.product_id].category.value
                    used = any(it.product_id == ing.product_id and it.grams > 0 for p in m.portions for it in p.items)
                    if used and cat not in allowed:
                        err("zrodlo_weglowodanow",
                            f"{label}: źródło węglowodanów '{ing.product_id}' (kategoria {cat}) spoza dozwolonych",
                            day=m.day, meal=m.meal)

    # --- przekąski
    per_day = Counter((int(s.day), s.person_id) for s in plan.snacks)
    for (day, pid), count in per_day.items():
        if count > a.day.snacks.max_per_person_per_day:
            err("przekaski_limit", f"{DAY_LABELS_PL[Weekday(day)]}: {count} przekąski dla {pid}, maksimum "
                                   f"{a.day.snacks.max_per_person_per_day}", day=Weekday(day), person_id=pid)
    for s in plan.snacks:
        if s.dish_id not in a.day.snacks.allowed:
            err("przekaska_niedozwolona", f"{DAY_LABELS_PL[s.day]}: przekąska '{s.dish_id}' spoza dozwolonej listy",
                day=s.day, person_id=s.person_id)

    # --- zgodność z liczbami podanymi przez silnik
    if engine_totals:
        mine = {(int(t.day), t.person_id): t for t in totals}
        for et in engine_totals:
            key = (int(et["day"]), et["person_id"])
            t = mine.get(key)
            if t is None:
                continue
            for metric in METRICS:
                if abs(getattr(t, metric) - float(et[metric])) > 0.2 + 1e-6:
                    err("niezgodnosc_z_silnikiem",
                        f"dzień {key[0]}, {key[1]}: {METRIC_PL[metric]} silnik {et[metric]} ≠ weryfikator "
                        f"{getattr(t, metric)}")

    ok = not any(i.severity is Severity.ERROR for i in issues)
    return VerificationReport(ok=ok, totals=totals, issues=issues)
