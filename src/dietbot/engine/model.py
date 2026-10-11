"""Model CP-SAT dla jednego bloku dni (dzień albo kilka dni połączonych daniem na zapas).

Programowanie celowe na liczbach całkowitych:
- gramatury = krok × liczba całkowita (zaokrąglanie w modelu, nie po fakcie),
- widełki dzienne i kotwice białka są elastyczne (luz z bardzo wysoką karą) — zawsze jest wynik i diagnoza,
- require_targets wymusza cele także podczas optymalizacji realizmu porcji,
- tryb ścisły (strict) służy do wyznaczenia zbioru sprzecznych ograniczeń (assumptions).
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from ..domain.assumptions import Assumptions, Weekday
from ..domain.dish import DishIngredient, DishTemplate, MealType, Portioning
from .catalog import Catalog, EngineInputError
from .portions import StepBounds, cap_conflict, grams_range, is_fixed, person_bounds
from .settings import SCALE, EngineSettings
from .types import EngineSlot

METRICS = ("kcal", "protein", "fat", "carbs")
GOAL_SCALE_FLOOR = {"kcal": 100.0, "protein": 10.0, "fat": 10.0, "carbs": 10.0}
GROUP_PRECISION = 10_000  # udziały w grupach składników z dokładnością 0,01%


class Expr:
    """Liniowe wyrażenie na zmiennych CP-SAT ze współczynnikami rzeczywistymi (zaokrąglane przy dodawaniu)."""

    __slots__ = ("coefs", "const")

    def __init__(self) -> None:
        self.coefs: dict[int, float] = {}
        self.const: float = 0.0

    def add(self, var_index: int, coef: float) -> None:
        if coef:
            self.coefs[var_index] = self.coefs.get(var_index, 0.0) + coef

    def merge(self, other: Expr, mult: float = 1.0) -> None:
        for i, c in other.coefs.items():
            self.add(i, c * mult)
        self.const += other.const * mult


@dataclass
class IngVar:
    slot_key: str
    dish_id: str
    product_id: str
    kind: str  # "person" | "shared" | "equal" | "fixed"
    var_index: int | None
    bounds: StepBounds | None
    # gramy na 1 krok zmiennej dla każdej osoby (na dzień); dla "fixed" — stałe gramy
    grams_per_step: dict[str, float] = field(default_factory=dict)
    fixed_grams: dict[str, float] = field(default_factory=dict)


@dataclass
class SnackVar:
    day: Weekday
    person_id: str
    dish_id: str
    z_index: int
    items: list[IngVar]


@dataclass
class BlockSolution:
    solver_status: str
    feasible: bool
    objective: float | None
    wall_ms: float
    deterministic_time: float
    choices: dict[str, str]
    ingredient_values: dict[int, int]  # indeks zmiennej -> wartość
    snacks: list[tuple[SnackVar, dict[str, float]]]
    core: list[str]


class BlockModel:
    def __init__(
        self,
        slots: Sequence[EngineSlot],
        assumptions: Assumptions,
        catalog: Catalog,
        dishes: Mapping[str, DishTemplate],
        settings: EngineSettings,
        allow_snacks: bool = True,
        strict: bool = False,
        fixed_choices: Mapping[str, str] | None = None,
        fixed_snacks: Mapping[tuple[int, str], str] | None = None,
        snacks_fixed: bool = False,
    ):
        self.slots = list(slots)
        self.days: list[Weekday] = sorted({d for s in self.slots for d in s.days})
        self.a = assumptions
        self.catalog = catalog
        self.dishes = dishes
        self.settings = settings
        self.allow_snacks = allow_snacks
        self.strict = strict
        self.fixed_choices = dict(fixed_choices or {})
        self.fixed_snacks = dict(fixed_snacks or {})
        self.snacks_fixed = snacks_fixed  # True: przekąski dokładnie jak w fixed_snacks (brak wpisu = brak)

        self.m = cp_model.CpModel()
        self._zero = self.m.new_constant(0)
        self._vars: dict[int, cp_model.IntVar] = {}
        self._cost: dict[int, float] = {}
        self._group_lits: dict[str, cp_model.IntVar] = {}

        persons = assumptions.persons
        self.person_ids = [p.id for p in persons]
        self.scales = {p.id: p.energy_center / settings.reference_kcal for p in persons}
        total = sum(self.scales.values())
        self.day_share = {pid: s / total for pid, s in self.scales.items()}

        self.y: dict[tuple[str, str], int] = {}  # (slot, danie) -> indeks zmiennej wyboru
        self.ings: list[IngVar] = []
        self.snack_vars: list[SnackVar] = []
        # wyrażenia: (slot, osoba, metryka) na dzień
        self.meal_expr: dict[tuple[str, str, str], Expr] = {}
        self.day_expr: dict[tuple[int, str, str], Expr] = {}
        self.slacks: dict[str, int] = {}

    # ------------------------------------------------------------------ narzędzia
    def _new_int(self, lo: int, hi: int, name: str) -> int:
        v = self.m.new_int_var(int(lo), int(hi), name)
        self._vars[v.index] = v
        return v.index

    def _new_bool(self, name: str) -> int:
        v = self.m.new_bool_var(name)
        self._vars[v.index] = v
        return v.index

    def _lin(self, coefs: Mapping[int, float]) -> cp_model.LinearExprT:
        items = [(self._vars[i], int(round(c))) for i, c in coefs.items() if int(round(c)) != 0]
        if not items:
            # CP-SAT nie przyjmie stałej Pythona w ograniczeniu — używamy stałej modelu
            return self._zero
        return cp_model.LinearExpr.weighted_sum([v for v, _ in items], [c for _, c in items])

    def _row(self, coefs: Mapping[int, float], lo: float | None = None, hi: float | None = None, group: str | None = None):
        expr = self._lin(coefs)
        cts = []
        if lo is not None:
            cts.append(self.m.add(expr >= int(math.ceil(lo - 1e-9))))
        if hi is not None:
            cts.append(self.m.add(expr <= int(math.floor(hi + 1e-9))))
        if self.strict and group:
            lit = self._group_lits.get(group)
            if lit is None:
                lit = self.m.new_bool_var(group)
                self._group_lits[group] = lit
            for ct in cts:
                ct.only_enforce_if(lit)

    def _cost_add(self, index: int, w: float) -> None:
        if not self.strict and w:
            self._cost[index] = self._cost.get(index, 0.0) + w

    def _expr(self, key: tuple) -> Expr:
        store = self.meal_expr if isinstance(key[0], str) else self.day_expr
        if key not in store:
            store[key] = Expr()
        return store[key]

    # ------------------------------------------------------------------ budowa
    def build(self, *, require_targets: bool | str = False) -> BlockModel:
        """`require_targets`: True/"all" — zero odchyleń od widełek, kotwic i mas talerzy; "bands" — tylko widełki
        dzienne muszą się zgadzać (kotwice i masy zostają miękkie)."""
        for slot in self.slots:
            self._build_slot(slot)
        for day in self.days:
            self._build_snacks(day)
            self._build_day(day)
        if require_targets:
            # Duża kara nie jest twardym ograniczeniem: solver może zatrzymać się na rozwiązaniu
            # poza widełkami. Ta ścieżka nadal optymalizuje realizm, lecz zabrania naruszeń celów.
            only_bands = require_targets == "bands"
            for name, index in self.slacks.items():
                if not only_bands or name.startswith("widelki_"):
                    self._row({index: 1}, hi=0)
        if not self.strict:
            self._build_lighter_supper()
            self.m.minimize(self._lin({i: round(c) for i, c in self._cost.items()}))
        return self

    def _realism(self, var_index: int, y_index: int, b: StepBounds, grams_default: float) -> None:
        """Kara wypukła za odejście od typowej porcji: v − d·y = (p1 + p2) − (m1 + m2) w strefie martwej ±free."""
        st = self.settings
        if self.strict or b.default <= 0 or (st.realism_cost_mid <= 0 and st.realism_cost_high <= 0):
            return
        free = int(st.realism_dead_zone * b.default)
        mid_cap = max(int(round((st.realism_mid_zone - st.realism_dead_zone) * b.default)), 1)
        big = max(b.hi, b.default) + 1
        p1 = self._new_int(0, mid_cap, f"r1p|{var_index}")
        m1 = self._new_int(0, mid_cap, f"r1m|{var_index}")
        p2 = self._new_int(0, big, f"r2p|{var_index}")
        m2 = self._new_int(0, big, f"r2m|{var_index}")
        self._row({var_index: 1, y_index: -b.default, p1: -1, p2: -1, m1: 1, m2: 1}, lo=-free, hi=free)
        per_step = b.step_g / max(grams_default, b.step_g)
        self._cost_add(p1, st.realism_cost_mid * per_step)
        self._cost_add(m1, st.realism_cost_mid * per_step)
        self._cost_add(p2, st.realism_cost_high * per_step)
        self._cost_add(m2, st.realism_cost_high * per_step)

    def _bounded_var(self, name: str, b: StepBounds, y_index: int, group: str | None, hint_on: bool = False) -> int:
        if self.strict:
            v = self._new_int(0, max(3 * b.hi, b.hi + 20), name)
            self._row({v: 1, y_index: -b.lo}, lo=0, group=f"{group}:min" if group else None)
            self._row({v: 1, y_index: -b.hi}, hi=0, group=f"{group}:max" if group else None)
            # poza wybranym daniem zmienna musi być zerem niezależnie od grup
            self._row({v: 1, y_index: -(3 * b.hi + 20)}, hi=0)
        else:
            v = self._new_int(0, b.hi, name)
            self._row({v: 1, y_index: -b.lo}, lo=0)
            self._row({v: 1, y_index: -b.hi}, hi=0)
            if self.settings.use_hints:
                self.m.add_hint(self._vars[v], b.default if hint_on else 0)
        return v

    def _add_nutrients(self, key_slot: str, person_id: str, var_index: int, product_id: str, grams: float) -> None:
        facts = self.catalog.facts(product_id)
        for metric in METRICS:
            self._expr((key_slot, person_id, metric)).add(var_index, facts.metric(metric) * grams * SCALE[metric])

    def _excludes(self, pid: str, dish_id: str, ing: DishIngredient) -> bool:
        """Czy osoba wyklucza składnik. Wykluczony składnik obowiązkowy to błąd danych wejściowych (pule dań
        odfiltrowują takie dania wcześniej), opcjonalny — osoba dostaje go zero."""
        person = self.a.person(pid)
        if ing.product_id not in person.excluded_products:
            return False
        if not ing.optional:
            raise EngineInputError(f"danie {dish_id}: {person.name} wyklucza obowiązkowy składnik {ing.product_id}")
        return True

    def _cap(self, pid: str, product, meal: MealType) -> float | None:
        return self.a.person(pid).portion_cap(product, meal)

    def _fixed_grams(self, ing: DishIngredient, product, pid: str, meal: MealType) -> float:
        """Stała ilość składnika (przyprawa lub produkt pomijalny) dla osoby — z limitem porcji produktu i limitem
        z preferencji osoby, zaokrąglona jak gramatury w planie."""
        _, typical, _ = grams_range(ing, self.scales[pid], self.settings)
        if product.max_portion_g is not None and ing.max_g is None:
            typical = min(typical, product.max_portion_g)
        cap = self._cap(pid, product, meal)
        if cap is not None:
            typical = min(typical, cap)
        return round(typical, 1)

    def _build_slot(self, slot: EngineSlot) -> None:
        key = slot.key
        n_days = len(slot.days)
        y_indices: list[int] = []
        treat_y: list[int] = []
        nontreat_y: list[int] = []
        grams_expr: dict[tuple[str, str, str], Expr] = {}  # (danie, produkt, osoba) -> gramy ×10
        fixed_mass: dict[tuple[str, str], float] = {}  # (danie, osoba) -> gramy składników o stałej ilości

        for rank, dish_id in enumerate(slot.candidates):
            dish = self.dishes.get(dish_id)
            if dish is None:
                raise EngineInputError(f"brak dania '{dish_id}' w bibliotece")
            if slot.meal not in dish.meal_types:
                raise EngineInputError(f"danie '{dish_id}' nie jest przewidziane na posiłek {slot.meal.value}")
            if n_days > 1 and (not dish.is_batch or (dish.batch_days or 0) < n_days):
                raise EngineInputError(
                    f"slot na {n_days} dni wymaga dania na zapas (tag na_zapas, na_ile_dni ≥ {n_days}); "
                    f"'{dish_id}' nim nie jest"
                )
            y = self._new_bool(f"y|{key}|{dish_id}")
            self.y[(key, dish_id)] = y
            first = rank == 0
            if self.settings.use_hints and not self.strict:
                self.m.add_hint(self._vars[y], 1 if first else 0)
            y_indices.append(y)
            (treat_y if dish.is_treat else nontreat_y).append(y)
            if key in self.fixed_choices:
                self._row({y: 1}, lo=1 if self.fixed_choices[key] == dish_id else 0,
                          hi=1 if self.fixed_choices[key] == dish_id else 0)
            if rank:
                self._cost_add(y, rank * self.settings.alternate_rank_cost)
            if dish.culinary_errors:
                self._row({y: 1}, hi=0)

            grouped = dish.grouped_products()
            for ing in dish.ingredients:
                product = self.catalog.product(ing.product_id)
                in_group = ing.product_id in grouped
                if is_fixed(ing, product):
                    fixed = {}
                    for pid in self.person_ids:
                        typical = self._fixed_grams(ing, product, pid, slot.meal)
                        fixed[pid] = typical
                        fixed_mass[(dish_id, pid)] = fixed_mass.get((dish_id, pid), 0.0) + typical
                        # stała ilość wchodzi do bilansu razem z wyborem dania: produkt pomijalny (sól, suszone zioła)
                        # ma zera, ale np. świeży czosnek, imbir czy sok z cytryny w roli przyprawy mają kalorie
                        # i białko — bez tego model liczył co innego niż diagnostyka i weryfikator
                        self._add_nutrients(key, pid, y, ing.product_id, typical)
                    self.ings.append(IngVar(key, dish_id, ing.product_id, "fixed", None, None, {}, fixed))
                    continue

                group_base = f"porcja:{key}:{dish_id}:{ing.product_id}"
                if ing.portioning is Portioning.SHARED:
                    # Porcje z jednego garnka: każda osoba ma własną zmienną w pełnych krokach,
                    # a porcje trzymają proporcję udziałów osób (± tolerancja w krokach).
                    iv = None
                    shared_vars: dict[str, int] = {}
                    excluded = {pid for pid in self.person_ids if self._excludes(pid, dish_id, ing)}
                    for pid in self.person_ids:
                        b = person_bounds(ing, product, self.scales[pid], self.settings, in_group,
                                          self._cap(pid, product, slot.meal))
                        if pid in excluded:
                            b = StepBounds(b.step_g, 0, 0, 0)
                        v = self._bounded_var(f"g|{key}|{dish_id}|{ing.product_id}|{pid}", b, y, f"{group_base}:{pid}", first)
                        self._realism(v, y, b, b.default_g)
                        shared_vars[pid] = v
                        self.ings.append(IngVar(key, dish_id, ing.product_id, "shared", v, b, {pid: b.step_g}))
                        self._add_nutrients(key, pid, v, ing.product_id, b.step_g)
                        grams_expr.setdefault((dish_id, ing.product_id, pid), Expr()).add(v, b.step_g * 10)
                    # proporcję trzymają tylko osoby, które składnik jedzą (wykluczona ma zero)
                    sharing = [pid for pid in self.person_ids if pid not in excluded]
                    tol = self.settings.shared_ratio_tolerance_steps
                    for pid in sharing[1:]:
                        ref = sharing[0]
                        s_ref = round(self.day_share[ref] * 1000)
                        s_p = round(self.day_share[pid] * 1000)
                        # |s_ref·n_p − s_p·n_ref| ≤ tol·max(s) ; przy wyborze innego dania obie zmienne = 0
                        row = {shared_vars[pid]: s_ref, shared_vars[ref]: -s_p}
                        bound = tol * max(s_ref, s_p)
                        self._row(row, lo=-bound, hi=bound, group=f"proporcja_garnka:{key}:{dish_id}:{ing.product_id}")
                elif ing.portioning is Portioning.EQUAL:
                    # po równo: wszyscy dostają tyle samo, więc obowiązuje najostrzejszy limit porcji
                    caps = [c for c in (self._cap(pid, product, slot.meal) for pid in self.person_ids) if c is not None]
                    b = person_bounds(ing, product, 1.0, self.settings, in_group, min(caps) if caps else None)
                    if any(self._excludes(pid, dish_id, ing) for pid in self.person_ids):
                        b = StepBounds(b.step_g, 0, 0, 0)  # po równo: gdy ktoś nie je składnika, nikt go nie dostaje
                    v = self._bounded_var(f"eq|{key}|{dish_id}|{ing.product_id}", b, y, group_base, first)
                    self._realism(v, y, b, b.default_g)
                    gps = dict.fromkeys(self.person_ids, b.step_g)
                    iv = IngVar(key, dish_id, ing.product_id, "equal", v, b, gps)
                else:
                    iv = None
                    for pid in self.person_ids:
                        b = person_bounds(ing, product, self.scales[pid], self.settings, in_group,
                                          self._cap(pid, product, slot.meal))
                        if self._excludes(pid, dish_id, ing):
                            b = StepBounds(b.step_g, 0, 0, 0)
                        v = self._bounded_var(f"q|{key}|{dish_id}|{ing.product_id}|{pid}", b, y, f"{group_base}:{pid}", first)
                        self._realism(v, y, b, b.default_g)
                        piv = IngVar(key, dish_id, ing.product_id, "person", v, b, {pid: b.step_g})
                        self.ings.append(piv)
                        self._add_nutrients(key, pid, v, ing.product_id, b.step_g)
                        grams_expr.setdefault((dish_id, ing.product_id, pid), Expr()).add(v, b.step_g * 10)
                if iv is not None:
                    self.ings.append(iv)
                    for pid, g in iv.grams_per_step.items():
                        self._add_nutrients(key, pid, iv.var_index, ing.product_id, g)
                        grams_expr.setdefault((dish_id, ing.product_id, pid), Expr()).add(iv.var_index, g * 10)

            # proporcje w daniu, dla każdej osoby: licznik ∈ [min, max] × mianownik (gramy)
            for r_i, ratio in enumerate(dish.ratios):
                for pid in self.person_ids:
                    num, den = Expr(), Expr()
                    for p in ratio.numerator:
                        if (dish_id, p, pid) in grams_expr:
                            num.merge(grams_expr[(dish_id, p, pid)])
                    for p in ratio.denominator:
                        if (dish_id, p, pid) in grams_expr:
                            den.merge(grams_expr[(dish_id, p, pid)])
                    grp = f"proporcja:{key}:{dish_id}:{r_i}:{pid}"
                    if ratio.min is not None:
                        row = Expr()
                        row.merge(num, GROUP_PRECISION)
                        row.merge(den, -math.ceil(ratio.min * GROUP_PRECISION - 1e-8))
                        self._row(row.coefs, lo=0, group=grp)
                    if ratio.max is not None:
                        row = Expr()
                        row.merge(num, GROUP_PRECISION)
                        row.merge(den, -math.floor(ratio.max * GROUP_PRECISION + 1e-8))
                        self._row(row.coefs, hi=0, group=grp)

            # grupy (ciasto, panierka, sos): udział składnika w masie grupy jak w przepisie, dla każdej osoby.
            # g_i ∈ [(1−tol)·u_i·G − krok_i, (1+tol)·u_i·G + krok_i], gdzie G = masa grupy, u_i = udział z przepisu.
            # Jeden krok luzu pozwala zaokrąglić gramatury (np. olej co 5 g) bez utraty proporcji całej masy.
            fixed_ids = {i.product_id for i in dish.ingredients if is_fixed(i, self.catalog.product(i.product_id))}
            for g_i, grp_def in enumerate(dish.groups):
                shares = grp_def.shares(dish.ingredients, fixed_ids)
                tol = grp_def.tolerance if grp_def.tolerance is not None else self.settings.group_ratio_tolerance
                for pid in self.person_ids:
                    members = {p: grams_expr[(dish_id, p, pid)] for p in shares if (dish_id, p, pid) in grams_expr}
                    if len(members) < 2:
                        continue
                    total = Expr()
                    for expr in members.values():
                        total.merge(expr)
                    grp = f"grupa:{key}:{dish_id}:{g_i}:{pid}"
                    for p, own in members.items():
                        step10 = max(abs(c) for c in own.coefs.values())
                        lo_row, hi_row = Expr(), Expr()
                        lo_row.merge(own, GROUP_PRECISION)
                        lo_row.merge(total, -math.floor((1 - tol) * shares[p] * GROUP_PRECISION))
                        hi_row.merge(own, GROUP_PRECISION)
                        hi_row.merge(total, -math.ceil((1 + tol) * shares[p] * GROUP_PRECISION))
                        self._row(lo_row.coefs, lo=-step10 * GROUP_PRECISION, group=grp)
                        self._row(hi_row.coefs, hi=step10 * GROUP_PRECISION, group=grp)

        self._row(dict.fromkeys(y_indices, 1), lo=1, hi=1)

        # masa talerza: suma gramów wszystkich składników ≤ limit osoby dla posiłku (miękko, z karą)
        for pid in self.person_ids:
            cap10 = int(round(self.a.person(pid).meal_mass_cap(slot.meal) * 10))
            row: dict[int, float] = {}
            for (_dish, _product, p_id), expr in grams_expr.items():
                if p_id == pid:
                    for idx, c in expr.coefs.items():
                        row[idx] = row.get(idx, 0.0) + c
            for (d_id, p_id), grams in fixed_mass.items():
                if p_id == pid:
                    y = self.y[(key, d_id)]
                    row[y] = row.get(y, 0.0) + grams * 10
            if not row:
                continue
            grp = f"masa:{key}:{pid}"
            if self.strict:
                self._row(row, hi=cap10, group=grp)
            else:
                over = self._new_int(0, 50_000, f"masa|{key}|{pid}")
                row[over] = row.get(over, 0.0) - 1
                self._row(row, hi=cap10)
                self._cost_add(over, self.settings.meal_mass_cost_per_g / 10)
                self.slacks[f"masa|{key}|{pid}"] = over

        # kotwice białka na posiłek (posiłki z puli 20% zwolnione)
        safety = self.settings.band_safety_g
        for pid in self.person_ids:
            anchor = self.a.person(pid).protein_anchor
            p_expr = self.meal_expr.get((key, pid, "protein"), Expr())
            s = SCALE["protein"]
            lo_row = dict(p_expr.coefs)
            hi_row = dict(p_expr.coefs)
            a_min, a_max = anchor.min, anchor.max
            if a_max - a_min > 4 * safety:  # margines na resztkowe zaokrąglenia, jak przy widełkach dziennych
                a_min, a_max = a_min + safety, a_max - safety
            for y in nontreat_y:
                lo_row[y] = lo_row.get(y, 0.0) - a_min * s
            big = 400 * s
            for y in treat_y:
                hi_row[y] = hi_row.get(y, 0.0) - big
            grp = f"kotwica:{key}:{pid}"
            if self.strict:
                self._row(lo_row, lo=0, group=f"{grp}:min")
                self._row(hi_row, hi=a_max * s, group=f"{grp}:max")
            else:
                am = self._new_int(0, int(a_min * s) + 1, f"am|{key}|{pid}")
                ap = self._new_int(0, big, f"ap|{key}|{pid}")
                lo_row[am] = lo_row.get(am, 0.0) + 1
                hi_row[ap] = hi_row.get(ap, 0.0) - 1
                self._row(lo_row, lo=0)
                self._row(hi_row, hi=a_max * s)
                w = self.settings.anchor_cost_per_g / s
                self._cost_add(am, w)
                self._cost_add(ap, w)
                self.slacks[f"kotwica_min|{key}|{pid}"] = am
                self.slacks[f"kotwica_max|{key}|{pid}"] = ap

        for day in slot.days:
            for pid in self.person_ids:
                for metric in METRICS:
                    self._expr((int(day), pid, metric)).merge(self._expr((key, pid, metric)))

    def _build_snacks(self, day: Weekday) -> None:
        rules = self.a.day.snacks
        if not self.allow_snacks or rules.max_per_person_per_day <= 0:
            return
        for pid in self.person_ids:
            person = self.a.person(pid)
            z_list: list[int] = []
            for dish_id in rules.allowed:
                dish = self.dishes.get(dish_id)
                if dish is None:
                    raise EngineInputError(f"przekąska '{dish_id}' z założeń nie istnieje w bibliotece dań")
                # Balancing snacks use a simpler portion model. A compound recipe
                # must use a regular meal slot, never bypass its cooking rules here.
                if dish.culinary_errors or dish.groups or dish.ratios:
                    continue
                # zakazy globalne i wykluczenia osoby — także składniki opcjonalne (model przekąsek ich nie zeruje)
                banned = set(self.a.prohibitions.products) | set(person.excluded_products)
                if any(i.product_id in banned for i in dish.ingredients):
                    continue
                if any(cap_conflict(i, self.catalog.product(i.product_id), self.scales[pid], self.settings, False,
                                    self._cap(pid, self.catalog.product(i.product_id), MealType.SNACK))
                       for i in dish.ingredients if not is_fixed(i, self.catalog.product(i.product_id))):
                    continue  # limit porcji z preferencji nie pozwala zrobić tej przekąski
                z = self._new_bool(f"z|{int(day)}|{pid}|{dish_id}")
                if self.snacks_fixed:
                    on = 1 if self.fixed_snacks.get((int(day), pid)) == dish_id else 0
                    self._row({z: 1}, lo=on, hi=on)
                z_list.append(z)
                self._cost_add(z, self.settings.snack_cost * (0.75 if dish_id in self.settings.liked_dishes else 1))
                items: list[IngVar] = []
                key = f"przekaska|{int(day)}|{pid}|{dish_id}"
                for ing in dish.ingredients:
                    product = self.catalog.product(ing.product_id)
                    if is_fixed(ing, product):
                        typical = self._fixed_grams(ing, product, pid, MealType.SNACK)
                        items.append(IngVar(key, dish_id, ing.product_id, "fixed", None, None, {}, {pid: typical}))
                        facts = self.catalog.facts(ing.product_id)
                        for metric in METRICS:  # stała ilość liczy się razem z wyborem przekąski
                            self._expr((int(day), pid, metric)).add(z, facts.metric(metric) * typical * SCALE[metric])
                        continue
                    b = person_bounds(ing, product, self.scales[pid], self.settings,
                                      cap_g=self._cap(pid, product, MealType.SNACK))
                    v = self._bounded_var(f"s|{key}|{ing.product_id}", b, z, None)
                    self._realism(v, z, b, b.default_g)
                    items.append(IngVar(key, dish_id, ing.product_id, "person", v, b, {pid: b.step_g}))
                    facts = self.catalog.facts(ing.product_id)
                    for metric in METRICS:
                        self._expr((int(day), pid, metric)).add(v, facts.metric(metric) * b.step_g * SCALE[metric])
                self.snack_vars.append(SnackVar(day, pid, dish_id, z, items))
            if z_list:
                self._row(dict.fromkeys(z_list, 1), hi=rules.max_per_person_per_day)

    def _fixed_const(self, pid: str, metric: str) -> float:
        total = 0.0
        for item in self.a.day.fixed_items:
            total += self.catalog.facts(item.product_id).metric(metric) * item.grams * SCALE[metric]
        return total

    def _build_day(self, day: Weekday) -> None:
        st = self.settings
        for pid in self.person_ids:
            person = self.a.person(pid)
            for metric in METRICS:
                expr = self._expr((int(day), pid, metric))
                const = self._fixed_const(pid, metric)
                band = person.targets.band(metric)
                s = SCALE[metric]
                safety = st.band_safety_kcal if metric == "kcal" else st.band_safety_g
                lo_v, hi_v = band.min, band.max
                if hi_v - lo_v > 4 * safety:
                    lo_v, hi_v = lo_v + safety, hi_v - safety
                grp = f"widelki:{int(day)}:{pid}:{metric}"
                if self.strict:
                    self._row(expr.coefs, lo=lo_v * s - const, group=f"{grp}:min")
                    self._row(expr.coefs, hi=hi_v * s - const, group=f"{grp}:max")
                    continue
                big = int(band.max * s * 3) + 1
                em = self._new_int(0, big, f"em|{int(day)}|{pid}|{metric}")
                ep = self._new_int(0, big, f"ep|{int(day)}|{pid}|{metric}")
                lo_row = dict(expr.coefs)
                lo_row[em] = lo_row.get(em, 0.0) + 1
                hi_row = dict(expr.coefs)
                hi_row[ep] = hi_row.get(ep, 0.0) - 1
                self._row(lo_row, lo=lo_v * s - const)
                self._row(hi_row, hi=hi_v * s - const)
                w_band = (st.band_cost_per_kcal if metric == "kcal" else st.band_cost_per_g) / s
                self._cost_add(em, w_band)
                self._cost_add(ep, w_band)
                self.slacks[f"widelki_min|{int(day)}|{pid}|{metric}"] = em
                self.slacks[f"widelki_max|{int(day)}|{pid}|{metric}"] = ep

                # cel: odchylenie od środka poza strefą martwą
                center = band.center
                half = min(center - band.min, band.max - center)
                dz = st.goal_dead_zone * half * s
                dp = self._new_int(0, big, f"dp|{int(day)}|{pid}|{metric}")
                dm = self._new_int(0, big, f"dm|{int(day)}|{pid}|{metric}")
                r1 = dict(expr.coefs)
                r1[dp] = r1.get(dp, 0.0) - 1
                r2 = dict(expr.coefs)
                r2[dm] = r2.get(dm, 0.0) + 1
                self._row(r1, hi=center * s + dz - const)
                self._row(r2, lo=center * s - dz - const)
                # przy celu bliskim zera waga rosłaby bez ograniczeń — dolna granica skali (audyt S6)
                w_goal = st.goal_cost_per_percent * 100 / max(center * s, GOAL_SCALE_FLOOR[metric] * s)
                self._cost_add(dp, w_goal)
                self._cost_add(dm, w_goal)

    def _build_lighter_supper(self) -> None:
        if not self.a.day.supper_lighter_than_lunch:
            return
        for day in self.days:
            lunch = [s for s in self.slots if day in s.days and s.meal is MealType.LUNCH]
            supper = [s for s in self.slots if day in s.days and s.meal is MealType.SUPPER]
            if not lunch or not supper:
                continue
            for pid in self.person_ids:
                row = Expr()
                row.merge(self._expr((supper[0].key, pid, "kcal")))
                row.merge(self._expr((lunch[0].key, pid, "kcal")), -1)
                sl = self._new_int(0, 2_000_000, f"lekka|{int(day)}|{pid}")  # do 20 000 kcal różnicy — zawsze kara, nie sprzeczność
                row.add(sl, -1)
                self._row(row.coefs, hi=0)
                self._cost_add(sl, self.settings.lighter_supper_cost_per_kcal / SCALE["kcal"])

    # ------------------------------------------------------------------ rozwiązanie
    def solve(self) -> BlockSolution:
        solver = cp_model.CpSolver()
        p = solver.parameters
        p.num_workers = 1
        p.random_seed = self.settings.random_seed
        p.max_deterministic_time = self.settings.max_deterministic_time * max(len(self.days), 1)
        p.max_time_in_seconds = self.settings.max_wall_time_s
        p.linearization_level = self.settings.linearization_level
        if not self.strict and self.settings.relative_gap_limit > 0:
            p.relative_gap_limit = self.settings.relative_gap_limit
        if self.strict:
            self.m.add_assumptions(list(self._group_lits.values()))
        t0 = time.perf_counter()
        status = solver.solve(self.m)
        wall_ms = (time.perf_counter() - t0) * 1000
        feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        values: dict[int, int] = {}
        choices: dict[str, str] = {}
        snacks: list[tuple[SnackVar, dict[str, float]]] = []
        core: list[str] = []
        if feasible:
            for idx, var in self._vars.items():
                values[idx] = solver.value(var)
            for (key, dish_id), y in self.y.items():
                if values[y] == 1:
                    choices[key] = dish_id
            for sv in self.snack_vars:
                if values[sv.z_index] == 1:
                    grams = {}
                    for it in sv.items:
                        if it.kind == "fixed":
                            grams[it.product_id] = it.fixed_grams[sv.person_id]
                        else:
                            grams[it.product_id] = values[it.var_index] * it.bounds.step_g
                    snacks.append((sv, grams))
        elif self.strict and status == cp_model.INFEASIBLE:
            inv = {lit.index: name for name, lit in self._group_lits.items()}
            core = sorted(inv[i] for i in solver.sufficient_assumptions_for_infeasibility() if i in inv)
        return BlockSolution(
            solver_status=solver.status_name(status),
            feasible=feasible,
            objective=solver.objective_value if feasible and not self.strict else None,
            wall_ms=wall_ms,
            deterministic_time=solver.deterministic_time,
            choices=choices,
            ingredient_values=values,
            snacks=snacks,
            core=core,
        )
