"""Poprawki po audycie silnika (13.09.2026): spójność modelu z diagnostyką, tolerancje, wykluczenia, pamięć YAML."""

from __future__ import annotations

import time
from datetime import date

import pytest

from dietbot.checker import verify_plan
from dietbot.domain import MealType
from dietbot.domain.assumptions import Band
from dietbot.engine import BlockStatus, EngineRequest, EngineSettings, solve_week
from dietbot.engine.catalog import Catalog
from dietbot.engine.diagnostics import items_totals
from dietbot.engine.model import METRICS, BlockModel
from dietbot.engine.settings import SCALE
from dietbot.engine.solver import _extract
from dietbot.storage.repository import Repository
from dietbot.storage.yamlio import load_plain

# kurczak curry ma świeży czosnek w roli przyprawy (produkt niepomijalny: ma kalorie i białko)
DAY = [
    {"days": [0], "meal": "sniadanie", "candidates": ["kanapki_z_szynka_jajkiem_i_pomidorem"]},
    {"days": [0], "meal": "obiad", "candidates": ["kurczak_curry_z_ryzem"]},
    {"days": [0], "meal": "kolacja", "candidates": ["salatka_grecka_z_kurczakiem"]},
]


def test_model_expressions_match_plan_totals(repo):
    """To, co model optymalizuje (wyrażenia posiłków), musi być tym, co potem liczy diagnostyka z gramatur planu —
    także dla składników o stałej ilości (czosnek, sok z cytryny jako przyprawa)."""
    a = repo.assumptions
    catalog = Catalog(repo.products, a.energy.method)
    slots = EngineRequest.model_validate({"slots": DAY}).slots
    bm = BlockModel(slots, a, catalog, repo.dishes, EngineSettings(parallel_blocks=False)).build()
    sol = bm.solve()
    assert sol.feasible
    meals, _ = _extract(bm, sol, slots, repo.dishes)
    checked = 0
    for slot in slots:
        meal = next(m for m in meals if m.day == slot.days[0] and m.meal == slot.meal)
        for pid in bm.person_ids:
            plan_totals = items_totals(catalog, {i.product_id: i.grams for i in meal.portion(pid).items})
            for metric in METRICS:
                expr = bm.meal_expr[(slot.key, pid, metric)]
                model_value = sum(c * sol.ingredient_values[i] for i, c in expr.coefs.items()) / SCALE[metric]
                assert abs(model_value - plan_totals[metric]) < 0.05, (slot.key, pid, metric, model_value, plan_totals[metric])
                checked += 1
    assert checked == 24
    garlic = [i for i in meals[1].portion("mezczyzna").items if i.product_id == "czosnek"]
    assert garlic and garlic[0].grams > 0  # składnik stały jest w planie i w modelu


def test_engine_status_agrees_with_checker_on_point_band(repo):
    """Widełki punktowe (min = max): silnik i weryfikator używają tej samej tolerancji, więc ich werdykty są zgodne."""
    a = repo.assumptions.model_copy(deep=True)
    for p in a.persons:
        c = p.targets.kcal.center
        p.targets.kcal = Band(min=c, max=c, cel=c)
    res = solve_week(EngineRequest.model_validate({"slots": DAY}), a, repo.products, repo.dishes,
                     EngineSettings(parallel_blocks=False, conflict_core=False))
    report = verify_plan(res.plan, a, repo.products, repo.dishes,
                         [t.model_dump(mode="json") for b in res.blocks for t in b.totals])
    assert (res.status is BlockStatus.OK) == (not any(i.code == "widelki" for i in report.errors()))


def test_excluded_optional_shared_ingredient_goes_to_zero(repo):
    """Składnik opcjonalny z garnka wykluczony przez jedną osobę: ona dostaje zero, druga normalną porcję."""
    dish = repo.dishes["kurczak_curry_z_ryzem"].model_copy(deep=True)
    onion = next(i for i in dish.ingredients if i.product_id == "cebula")
    assert onion.portioning.value == "z_garnka"
    onion.optional = True
    dishes = {**repo.dishes, dish.id: dish}
    a = repo.assumptions.model_copy(deep=True)
    a.persons[0].excluded_products = ["cebula"]
    res = solve_week(EngineRequest.model_validate({"slots": DAY}), a, repo.products, dishes,
                     EngineSettings(parallel_blocks=False, conflict_core=False))
    assert res.status is BlockStatus.OK, [b.message for b in res.blocks]
    lunch = next(m for m in res.plan.meals if m.meal is MealType.LUNCH)
    woman = {i.product_id: i.grams for i in lunch.portion("kobieta").items}
    man = {i.product_id: i.grams for i in lunch.portion("mezczyzna").items}
    assert woman.get("cebula", 0) == 0 and man.get("cebula", 0) > 0
    assert verify_plan(res.plan, a, repo.products, dishes).ok


def test_excluded_mandatory_shared_ingredient_is_input_error(repo):
    a = repo.assumptions.model_copy(deep=True)
    a.persons[0].excluded_products = ["cebula"]
    res = solve_week(EngineRequest.model_validate({"slots": DAY}), a, repo.products, repo.dishes,
                     EngineSettings(parallel_blocks=False, conflict_core=False))
    assert res.status is BlockStatus.MODEL_ERROR and "cebula" in res.blocks[0].message


def test_first_choice_report_gets_conflict_core_when_requested(repo):
    """Szybka ścieżka pierwszych wyborów (z kandydatami zapasowymi) też oddaje zbiór sprzecznych ograniczeń."""
    slots = [
        {"days": ["poniedzialek"], "meal": "sniadanie", "candidates": ["serek_wiejski_z_rzodkiewka_i_ogorkiem"]},
        {"days": ["poniedzialek"], "meal": "obiad", "candidates": ["dorsz_pieczony_z_ziemniakami_i_fasolka",
                                                                  "kurczak_curry_z_ryzem"]},
        {"days": ["poniedzialek"], "meal": "kolacja", "candidates": ["salatka_z_tunczykiem_i_jajkiem"]},
    ]
    req = EngineRequest.model_validate({"slots": slots, "allow_snacks": False})
    settings = EngineSettings(parallel_blocks=False, use_alternates=False, conflict_core=True)
    cache: dict = {}
    res = solve_week(req, repo.assumptions, repo.products, repo.dishes, settings, cache=cache)
    assert res.status is BlockStatus.HARD and res.blocks[0].conflict_core
    t0 = time.perf_counter()
    again = solve_week(req, repo.assumptions, repo.products, repo.dishes, settings, cache=cache)
    assert again.blocks[0].conflict_core == res.blocks[0].conflict_core
    assert time.perf_counter() - t0 < 0.5  # z pamięci podręcznej, bez ponownego liczenia


def test_repair_pools_respect_wish_exclusions(repo, monkeypatch):
    """Przeliczenie z naprawą po zmianie założeń buduje pule bez produktów wykluczonych życzeniami planu."""
    from dietbot.domain.assumptions import Weekday
    from dietbot.planner import pipeline
    from dietbot.planner.compose import Composition, SlotPlan

    seen: list = []
    original = pipeline.evaluate_swaps

    def spy(comp, result, pools, *args, **kw):
        seen.append(pools)
        return original(comp, result, pools, *args, **kw)

    monkeypatch.setattr(pipeline, "evaluate_swaps", spy)
    comp = Composition(slots=[
        SlotPlan(days=[Weekday.MONDAY], meal=MealType.BREAKFAST, dish_id="serek_wiejski_z_rzodkiewka_i_ogorkiem"),
        SlotPlan(days=[Weekday.MONDAY], meal=MealType.LUNCH, dish_id="dorsz_pieczony_z_ziemniakami_i_fasolka"),
        SlotPlan(days=[Weekday.MONDAY], meal=MealType.SUPPER, dish_id="salatka_z_tunczykiem_i_jajkiem"),
    ])
    # tłuszcz 10–20 g nie do osiągnięcia — dzień jest poza widełkami, więc naprawa musi szukać zamienników
    a = repo.assumptions.model_copy(deep=True)
    for p in a.persons:
        p.targets.fat = Band(min=10, max=20, cel=15)
    repo2 = Repository(paths=repo.paths, products=repo.products, dishes=repo.dishes, assumptions=a,
                       store_products=repo.store_products)
    banned = {"piers_z_kurczaka", "udko_z_kurczaka"} & set(repo.products)
    out = pipeline.WeekPlanner(repo2, llm=None, settings=EngineSettings(conflict_core=False)).finish(
        comp, repair=True, extra_banned=banned)
    assert seen, "naprawa powinna sprawdzać zamienniki (dzień jest poza widełkami)"
    for pools in seen:
        for ids in pools.by_meal.values():
            for d in ids:
                assert not any(i.product_id in banned and not i.optional for i in repo.dishes[d].ingredients), d
    assert out.banned_products == banned


def test_generate_requires_monday(tmp_path):
    from dietbot.app.jobs import UserError
    from dietbot.app.service import DietService

    service = DietService(use_llm=False, db_path=tmp_path / "t.sqlite3")
    with pytest.raises(UserError, match="poniedziałkiem"):
        service.generate(week_start=date(2026, 9, 16), use_llm=False)  # środa
    service.jobs.shutdown()


def test_yaml_cache_roundtrip(tmp_path):
    path = tmp_path / "dane.yaml"
    path.write_text("- {id: a, x: 1}\n- {id: b, x: [1, 2]}\n", encoding="utf-8")
    cache = tmp_path / "cache"
    first = load_plain(path, cache)
    assert first == [{"id": "a", "x": 1}, {"id": "b", "x": [1, 2]}]
    assert list(cache.glob("*.pkl"))
    assert load_plain(path, cache) == first  # z pamięci podręcznej
    time.sleep(0.01)
    path.write_text("- {id: c, x: 3}\n", encoding="utf-8")
    assert load_plain(path, cache) == [{"id": "c", "x": 3}]  # zmiana pliku unieważnia wpis
    assert load_plain(path, None) == [{"id": "c", "x": 3}]
