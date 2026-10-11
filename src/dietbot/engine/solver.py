"""Publiczne API silnika: gramatury na tydzień z podziałem na bloki dni."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import ortools

from .. import __version__
from ..domain.assumptions import Assumptions
from ..domain.diet import DIET_GENITIVE_PL, compatible, dish_compatible
from ..domain.dish import DishTemplate
from ..domain.plan import PersonPortion, PlanItem, PlannedMeal, PlannedSnack, WeekPlan
from ..domain.product import Product
from .catalog import Catalog, EngineInputError
from .diagnostics import analyze, snack_reason
from .model import BlockModel, BlockSolution
from .settings import EngineSettings
from .types import BlockReport, BlockStatus, EngineRequest, EngineResult, EngineSlot, SnackUse, worst

ENGINE_NAME = f"dietbot-silnik {__version__} / OR-Tools CP-SAT {ortools.__version__}"


def split_blocks(slots: Sequence[EngineSlot]) -> list[list[EngineSlot]]:
    """Dni połączone daniem na zapas tworzą jeden blok; pozostałe dni są niezależne."""
    parent: dict[int, int] = {}

    def find(d: int) -> int:
        parent.setdefault(d, d)
        while parent[d] != d:
            parent[d] = parent[parent[d]]
            d = parent[d]
        return d

    for s in slots:
        first = find(int(s.days[0]))
        for d in s.days[1:]:
            parent[find(int(d))] = first
    groups: dict[int, list[EngineSlot]] = {}
    for s in slots:
        groups.setdefault(find(int(s.days[0])), []).append(s)
    return [groups[k] for k in sorted(groups, key=lambda r: min(int(d) for s in groups[r] for d in s.days))]


def input_hash(request: EngineRequest, assumptions: Assumptions, products: Mapping[str, Product],
               dishes: Mapping[str, DishTemplate], settings: EngineSettings) -> str:
    used_dishes = sorted({c for s in request.slots for c in s.candidates} | set(assumptions.day.snacks.allowed))
    used_products = sorted(
        {i.product_id for d in used_dishes if d in dishes for i in dishes[d].ingredients}
        | {f.product_id for f in assumptions.day.fixed_items}
    )
    payload = {
        "engine": ENGINE_NAME,
        "request": request.model_dump(mode="json"),
        "assumptions": assumptions.model_dump(mode="json"),
        "dishes": {d: dishes[d].model_dump(mode="json") for d in used_dishes if d in dishes},
        "products": {p: products[p].model_dump(mode="json") for p in used_products if p in products},
        "settings": settings.model_dump(mode="json"),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _meal_order(assumptions: Assumptions) -> dict[str, int]:
    return {m.value: i for i, m in enumerate(assumptions.day.meals)}


def _extract(bm: BlockModel, sol: BlockSolution, slots: Sequence[EngineSlot],
             dishes: Mapping[str, DishTemplate]) -> tuple[list[PlannedMeal], list[PlannedSnack]]:
    meals: list[PlannedMeal] = []
    for slot in slots:
        dish_id = sol.choices[slot.key]
        order = {ing.product_id: i for i, ing in enumerate(dishes[dish_id].ingredients)}
        per_person: dict[str, dict[str, float]] = {pid: {} for pid in bm.person_ids}
        n_days = len(slot.days)
        for iv in bm.ings:
            if iv.slot_key != slot.key or iv.dish_id != dish_id:
                continue
            if iv.kind == "fixed":
                for pid, g in iv.fixed_grams.items():
                    per_person[pid][iv.product_id] = round(g, 1)
                continue
            value = sol.ingredient_values[iv.var_index]
            for pid, gps in iv.grams_per_step.items():
                per_person[pid][iv.product_id] = round(value * gps, 1)
        batch_pot = None
        if n_days > 1:
            # ile ugotować na wszystkie porcje (wszystkie osoby × wszystkie dni)
            batch_pot = {}
            for items in per_person.values():
                for product_id, grams in items.items():
                    batch_pot[product_id] = round(batch_pot.get(product_id, 0.0) + grams * n_days, 1)
        for day in slot.days:
            portions = [
                PersonPortion(
                    person_id=pid,
                    items=[PlanItem(product_id=p, grams=g)
                           for p, g in sorted(per_person[pid].items(), key=lambda kv: order[kv[0]]) if g > 0],
                )
                for pid in bm.person_ids
            ]
            meals.append(PlannedMeal(day=day, meal=slot.meal, dish_id=dish_id, portions=portions,
                                     batch_group=slot.key if n_days > 1 else None, batch_pot_g=batch_pot))
    snacks = [
        PlannedSnack(day=sv.day, person_id=sv.person_id, dish_id=sv.dish_id,
                     items=[PlanItem(product_id=p, grams=round(g, 1)) for p, g in grams.items() if g > 0])
        for sv, grams in sol.snacks
    ]
    return meals, snacks


def solve_block(slots: Sequence[EngineSlot], assumptions: Assumptions, catalog: Catalog,
                dishes: Mapping[str, DishTemplate], settings: EngineSettings,
                allow_snacks: bool = True) -> tuple[BlockReport, list[PlannedMeal], list[PlannedSnack]]:
    """Najpierw pierwsze wybory (model bez wyboru dań jest mały i szybki); dania zapasowe tylko, gdy trzeba."""
    if not settings.main_first or all(len(s.candidates) == 1 for s in slots):
        return _solve_block(slots, assumptions, catalog, dishes, settings, allow_snacks)
    main = [s.model_copy(update={"candidates": s.candidates[:1]}) for s in slots]
    quick = settings.model_copy(update={"conflict_core": False})
    first = _solve_block(main, assumptions, catalog, dishes, quick, allow_snacks)
    if first[0].status is BlockStatus.OK:
        return first
    if not settings.use_alternates:
        return _with_core(first, main, assumptions, catalog, dishes, settings, allow_snacks)
    wide = settings.model_copy(update={"max_deterministic_time": settings.alternates_deterministic_time})
    report, meals, snacks = _solve_block(slots, assumptions, catalog, dishes, wide, allow_snacks)
    report.wall_ms = round(report.wall_ms + first[0].wall_ms, 1)
    if report.status is not BlockStatus.OK and _badness(first[0]) < _badness(report):
        first[0].wall_ms = report.wall_ms
        # zapasowe nic nie pomogły — zostaje pierwszy wybór
        return _with_core(first, main, assumptions, catalog, dishes, settings, allow_snacks)
    return report, meals, snacks


def _conflict_core(slots: Sequence[EngineSlot], assumptions: Assumptions, catalog: Catalog,
                   dishes: Mapping[str, DishTemplate], settings: EngineSettings, allow_snacks: bool,
                   choices: Mapping[str, str], snacks: Sequence[PlannedSnack]) -> list[str]:
    """Zbiór sprzecznych ograniczeń dla wybranych dań i przekąsek (model ścisły z założeniami)."""
    strict = BlockModel(
        slots, assumptions, catalog, dishes, settings, allow_snacks=allow_snacks, strict=True,
        fixed_choices=choices, fixed_snacks={(int(s.day), s.person_id): s.dish_id for s in snacks},
        snacks_fixed=True,
    ).build()
    return strict.solve().core


def _with_core(out, slots, assumptions, catalog, dishes, settings, allow_snacks):
    """Szybka ścieżka pierwszych wyborów pomija zbiór sprzeczności — uzupełniamy go, gdy wywołujący go chce.
    Bez tego wynik z pustym zbiorem nigdy nie trafiał do pamięci podręcznej bloków (liczony w każdej rundzie)."""
    report, meals, snacks = out
    if settings.conflict_core and report.status is not BlockStatus.OK and meals and not report.conflict_core:
        report.conflict_core = _conflict_core(slots, assumptions, catalog, dishes, settings, allow_snacks,
                                              report.choices, snacks)
    return out


def _badness(report: BlockReport) -> float:
    if report.status in (BlockStatus.MODEL_ERROR, BlockStatus.NO_SOLUTION):
        return float("inf")
    return (sum(abs(v.gap) / (10 if v.metric == "kcal" else 1) for v in report.violations) * 100
            + sum(abs(a.gap_g) for a in report.anchor_violations)
            + sum(m.excess_g / 25 for m in report.mass_violations))


def _solve_block(slots: Sequence[EngineSlot], assumptions: Assumptions, catalog: Catalog,
                 dishes: Mapping[str, DishTemplate], settings: EngineSettings,
                 allow_snacks: bool = True) -> tuple[BlockReport, list[PlannedMeal], list[PlannedSnack]]:
    bm = BlockModel(slots, assumptions, catalog, dishes, settings, allow_snacks=allow_snacks).build()
    sol = bm.solve()
    days = bm.days
    if not sol.feasible:
        status = BlockStatus.NO_SOLUTION if sol.solver_status == "UNKNOWN" else BlockStatus.MODEL_ERROR
        msg = ("Solver nie znalazł rozwiązania w limicie czasu." if status is BlockStatus.NO_SOLUTION
               else "Model sprzeczny: zakresy gramatur lub proporcje w daniu wykluczają się nawzajem.")
        return BlockReport(days=days, status=status, solver_status=sol.solver_status, wall_ms=sol.wall_ms,
                           deterministic_time=sol.deterministic_time, message=msg), [], []

    meals, snacks = _extract(bm, sol, slots, dishes)
    totals, violations, anchors, binding, masses = analyze(bm, sol, slots, meals, snacks, dishes)

    if violations or anchors or masses:
        # Model z karami mógł utknąć poza celami, chociaż poprawne porcje istnieją.
        # Przed diagnozą lub wymianą dań sprawdzamy te same dania z twardymi celami,
        # zachowując funkcję jakości porcji. Nie poszerzamy żadnych limitów użytkownika.
        # Najpierw wszystko naraz; gdy to niemożliwe, a dzień ma naruszone widełki — same widełki dzienne (kotwice
        # i masy talerza mogą wtedy zostać miękkie). Wcześniej jedna nieosiągalna kotwica przekreślała dzień
        # mieszczący się w widełkach (audyt 09.10.2026, S6).
        elapsed, deterministic = sol.wall_ms, sol.deterministic_time
        for mode in ("all", "bands") if violations else ("all",):
            bounded = BlockModel(slots, assumptions, catalog, dishes, settings,
                                 allow_snacks=allow_snacks).build(require_targets=mode)
            candidate = bounded.solve()
            elapsed += candidate.wall_ms
            deterministic += candidate.deterministic_time
            if not candidate.feasible:
                continue
            new_meals, new_snacks = _extract(bounded, candidate, slots, dishes)
            checked = analyze(bounded, candidate, slots, new_meals, new_snacks, dishes)
            accepted = not checked[1] and (mode == "bands" or (not checked[2] and not checked[4]))
            if accepted:
                bm, sol, meals, snacks = bounded, candidate, new_meals, new_snacks
                totals, violations, anchors, binding, masses = checked
                break
        sol.wall_ms, sol.deterministic_time = elapsed, deterministic

    for s in snacks:
        s.reason = snack_reason(assumptions, catalog, s, meals, snacks)

    status = BlockStatus.HARD if violations else BlockStatus.SOFT if (anchors or masses) else BlockStatus.OK
    core: list[str] = []
    if status is not BlockStatus.OK and settings.conflict_core:
        core = _conflict_core(slots, assumptions, catalog, dishes, settings, allow_snacks, sol.choices, snacks)

    report = BlockReport(
        days=days, status=status, solver_status=sol.solver_status, objective=sol.objective, wall_ms=round(sol.wall_ms, 1),
        deterministic_time=round(sol.deterministic_time, 4), choices=sol.choices, totals=totals,
        violations=violations, anchor_violations=anchors, mass_violations=masses, binding_bounds=binding,
        conflict_core=core,
        snacks=[SnackUse(day=s.day, person_id=s.person_id, dish_id=s.dish_id,
                         grams={i.product_id: i.grams for i in s.items}, reason=s.reason or "") for s in snacks],
    )
    return report, meals, snacks


def context_hash(assumptions: Assumptions, products: Mapping[str, Product], dishes: Mapping[str, DishTemplate],
                 settings: EngineSettings, allow_snacks: bool) -> str:
    payload = {
        "engine": ENGINE_NAME,
        "assumptions": assumptions.model_dump(mode="json"),
        "dishes": {k: dishes[k].model_dump(mode="json") for k in sorted(dishes)},
        "products": {k: products[k].model_dump(mode="json") for k in sorted(products)},
        "settings": settings.cache_view(),
        "allow_snacks": allow_snacks,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _block_key(block: Sequence[EngineSlot], base: str, settings: EngineSettings) -> str:
    extra = ""
    if any(len(s.candidates) > 1 for s in block):  # przy wyborze dań wynik zależy też od strategii wyboru
        extra = json.dumps([settings.main_first, settings.use_alternates, settings.alternates_deterministic_time])
    raw = json.dumps([s.model_dump(mode="json") for s in block], sort_keys=True) + base + extra
    return hashlib.sha256(raw.encode()).hexdigest()


def solve_week(request: EngineRequest, assumptions: Assumptions, products: Mapping[str, Product],
               dishes: Mapping[str, DishTemplate], settings: EngineSettings | None = None,
               cache: dict | None = None, context: str | None = None) -> EngineResult:
    """Liczy gramatury dla całego żądania. `cache` (słownik) pozwala nie liczyć ponownie niezmienionych bloków.
    `context` — gotowy `context_hash` tych samych założeń, produktów, dań i ustawień (wywołujący, który liczy
    wiele żądań na tych samych danych, np. sprawdzanie zamian, liczy go raz zamiast dla każdego żądania)."""
    settings = settings or EngineSettings()
    if assumptions.diet != "tradycyjna":
        assumptions = assumptions.model_copy(deep=True)
        assumptions.day.snacks.allowed = [d for d in assumptions.day.snacks.allowed
                                         if d in dishes and dish_compatible(dishes[d], products, assumptions.diet)]
        for slot in request.slots:
            for did in slot.candidates:
                if did in dishes and not dish_compatible(dishes[did], products, assumptions.diet):
                    raise EngineInputError(f"Danie {dishes[did].name} nie pasuje do diety {DIET_GENITIVE_PL.get(assumptions.diet, assumptions.diet)}.")
        for item in assumptions.day.fixed_items:
            if item.product_id in products and not compatible(products[item.product_id], assumptions.diet):
                raise EngineInputError(f"Stała pozycja {products[item.product_id].name} nie pasuje do diety {DIET_GENITIVE_PL.get(assumptions.diet, assumptions.diet)}.")
    catalog = Catalog(products, assumptions.energy.method)
    for item in assumptions.day.fixed_items:
        catalog.product(item.product_id)  # wczesny, czytelny błąd przy braku produktu
    ihash = input_hash(request, assumptions, products, dishes, settings)
    # klucz bloku: jego sloty + dane wspólne (bez pozostałych bloków — zmiana jednego dnia nie unieważnia reszty)
    ihash_base = context or context_hash(assumptions, products, dishes, settings, request.allow_snacks)

    blocks = split_blocks(request.slots)

    def run(block: list[EngineSlot]) -> tuple[BlockReport, list[PlannedMeal], list[PlannedSnack]]:
        key = None
        if cache is not None:
            key = _block_key(block, ihash_base, settings)
            hit = cache.get(key)
            # wynik bez zbioru sprzecznych ograniczeń nie wystarczy, gdy wywołujący go potrzebuje
            if hit is not None and not (settings.conflict_core and hit[0].status is not BlockStatus.OK
                                        and not hit[0].conflict_core and hit[1]):
                return copy.deepcopy(hit)  # wynik z pamięci nie może być zmieniany przez wywołującego
        try:
            out = solve_block(block, assumptions, catalog, dishes, settings, request.allow_snacks)
        except EngineInputError as exc:
            days = sorted({d for s in block for d in s.days})
            out = (BlockReport(days=days, status=BlockStatus.MODEL_ERROR, solver_status="INPUT_ERROR",
                               message=str(exc)), [], [])
        if cache is not None and key is not None:
            cache[key] = copy.deepcopy(out)
        return out

    if settings.parallel_blocks and len(blocks) > 1:
        with ThreadPoolExecutor(max_workers=min(len(blocks), os.cpu_count() or 2)) as ex:
            outputs = list(ex.map(run, blocks))
    else:
        outputs = [run(b) for b in blocks]

    reports: list[BlockReport] = [o[0] for o in outputs]
    all_meals: list[PlannedMeal] = [m for o in outputs for m in o[1]]
    all_snacks: list[PlannedSnack] = [s for o in outputs for s in o[2]]

    order = _meal_order(assumptions)
    all_meals.sort(key=lambda m: (int(m.day), order.get(m.meal.value, 99)))
    all_snacks.sort(key=lambda s: (int(s.day), s.person_id))
    days = sorted({d for s in request.slots for d in s.days})
    status = worst([r.status for r in reports])
    plan = WeekPlan(days=days, meals=all_meals, snacks=all_snacks, created_at=datetime.now(UTC),
                    engine=ENGINE_NAME, input_hash=ihash) if all_meals else None
    return EngineResult(status=status, plan=plan, blocks=reports, engine=ENGINE_NAME, input_hash=ihash)
