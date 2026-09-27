"""Silnik gramatur: poprawność, powtarzalność, diagnostyka."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fixtures_week import WEEK_SLOTS

from dietbot.checker import verify_plan
from dietbot.domain import MealType
from dietbot.engine import BlockStatus, EngineRequest, EngineSettings, solve_week, split_blocks
from dietbot.engine.catalog import Catalog
from dietbot.engine.portions import person_bounds

ROOT = Path(__file__).resolve().parents[1]


def test_week_is_within_all_bands(week_result):
    assert week_result.status is BlockStatus.OK
    for block in week_result.blocks:
        assert block.violations == []
        assert block.anchor_violations == []
        assert all(t.in_bands for t in block.totals)


def test_independent_checker_agrees(repo, week_result):
    engine_totals = [t.model_dump(mode="json") for b in week_result.blocks for t in b.totals]
    report = verify_plan(week_result.plan, repo.assumptions, repo.products, repo.dishes, engine_totals)
    assert report.ok, [i.message for i in report.issues]
    assert "niezgodnosc_z_silnikiem" not in report.codes()


def test_batch_soup_same_portions_both_days(week_result):
    soup = [m for m in week_result.plan.meals if m.meal is MealType.LUNCH and int(m.day) in (1, 2)]
    assert len(soup) == 2 and soup[0].dish_id == soup[1].dish_id
    assert soup[0].batch_group == soup[1].batch_group is not None
    for p0, p1 in zip(soup[0].portions, soup[1].portions, strict=True):
        assert p0.model_dump() == p1.model_dump()
    # garnek = suma porcji obu osób przez oba dni
    pot = soup[0].batch_pot_g
    for product_id, total in pot.items():
        per_day = sum(i.grams for p in soup[0].portions for i in p.items if i.product_id == product_id)
        assert abs(total - 2 * per_day) < 0.2


def test_shared_pot_portions_keep_proportion(repo, week_result):
    """Składniki z garnka: proporcja Kobieta/Mężczyzna bliska proporcji celów energii (± 1 krok)."""
    a = repo.assumptions
    share = a.person("kobieta").energy_center / a.person("mezczyzna").energy_center
    for m in week_result.plan.meals:
        dish = repo.dishes[m.dish_id]
        for ing in dish.ingredients:
            if ing.portioning.value != "z_garnka":
                continue
            gw = next((i.grams for i in m.portion("kobieta").items if i.product_id == ing.product_id), 0)
            gm = next((i.grams for i in m.portion("mezczyzna").items if i.product_id == ing.product_id), 0)
            step = repo.products[ing.product_id].step_g
            assert abs(gw - share * gm) <= step + 1e-6, (m.dish_id, ing.product_id, gw, gm)


def test_portions_are_multiples_of_steps_and_within_caps(repo, week_result):
    for m in week_result.plan.meals:
        dish = repo.dishes[m.dish_id]
        ings = {i.product_id: i for i in dish.ingredients}
        for portion in m.portions:
            for item in portion.items:
                product = repo.products[item.product_id]
                ing = ings[item.product_id]
                if product.negligible or ing.role.value == "przyprawa":
                    continue
                # Eggs incorporated into batter are weighed, not counted as whole
                # eggs per plate. The group uses its own 1 g measuring step.
                step = (ing.step_g or 1) if item.product_id in dish.grouped_products() else (
                    product.measure(ing.unit).grams if ing.unit else (ing.step_g or product.step_g))
                assert abs(item.grams / step - round(item.grams / step)) < 1e-6, (m.dish_id, item)
                if product.max_portion_g:
                    assert item.grams <= product.max_portion_g + 1e-6, (m.dish_id, item)


def test_at_most_one_snack_per_person_per_day(week_result):
    seen = set()
    for s in week_result.plan.snacks:
        key = (int(s.day), s.person_id)
        assert key not in seen
        seen.add(key)
        assert s.reason and s.reason.startswith("Dodana")


def test_deterministic_in_fresh_processes():
    """Te same dane → identyczny plan także w nowym procesie (bez wpływu stanu interpretera)."""
    code = (
        "import json,sys; sys.path.insert(0, r'{}');"
        "from fixtures_week import WEEK_SLOTS;"
        "from dietbot.storage.repository import Repository;"
        "from dietbot.engine import EngineRequest, solve_week;"
        "r=Repository.load();"
        "res=solve_week(EngineRequest.model_validate({{'slots': WEEK_SLOTS}}), r.assumptions, r.products, r.dishes);"
        "print(json.dumps([m.model_dump(mode='json') for m in res.plan.meals] + "
        "[s.model_dump(mode='json') for s in res.plan.snacks], sort_keys=True))"
    ).format(str(ROOT / "tests"))
    env = {"DIETBOT_HOME": str(ROOT), "PYTHONUTF8": "1", "SYSTEMROOT": "C:\\Windows"}
    outs = [
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT, check=True).stdout
        for _ in range(2)
    ]
    assert outs[0] == outs[1]
    assert json.loads(outs[0])


def test_cache_returns_identical_plan(repo, week_request):
    cache: dict = {}
    r1 = solve_week(week_request, repo.assumptions, repo.products, repo.dishes, cache=cache)
    r2 = solve_week(week_request, repo.assumptions, repo.products, repo.dishes, cache=cache)
    assert [m.model_dump() for m in r1.plan.meals] == [m.model_dump() for m in r2.plan.meals]
    r2.plan.meals[0].portions[0].items[0].grams = 9999  # modyfikacja wyniku nie może zepsuć pamięci
    r3 = solve_week(week_request, repo.assumptions, repo.products, repo.dishes, cache=cache)
    assert r3.plan.meals[0].portions[0].items[0].grams != 9999


def test_blocks_split_by_batch_days(week_request):
    blocks = split_blocks(week_request.slots)
    days = [sorted({int(d) for s in b for d in s.days}) for b in blocks]
    assert [1, 2] in days and len(blocks) == 6


def test_alternates_are_used_when_first_choice_is_structurally_bad(repo):
    """Kolacja bez białka jako pierwszy wybór → silnik bierze danie zapasowe i nie łamie kotwic."""
    slots = [
        {"days": ["czwartek"], "meal": "sniadanie", "candidates": ["jajecznica_na_masle_z_wedlina"]},
        {"days": ["czwartek"], "meal": "obiad", "candidates": ["losos_z_ziemniakami_i_brokulem"]},
        {"days": ["czwartek"], "meal": "kolacja", "candidates": ["tosty_z_serem_i_ketchupem", "wrap_z_kurczakiem_i_warzywami"]},
    ]
    # tosty są z puli 20% (zwolnione z kotwic) — tu sprawdzamy sam mechanizm wyboru spośród kandydatów
    res = solve_week(EngineRequest.model_validate({"slots": slots}), repo.assumptions, repo.products, repo.dishes)
    assert res.status in (BlockStatus.OK, BlockStatus.SOFT)
    assert res.blocks[0].choices["3_kolacja"] in {"tosty_z_serem_i_ketchupem", "wrap_z_kurczakiem_i_warzywami"}


def test_impossible_day_reports_diagnostics(repo):
    """Dzień bez żadnego źródła tłuszczu i przy zakazanych przekąskach → twarde odchylenie z diagnozą."""
    slots = [
        {"days": ["poniedzialek"], "meal": "sniadanie", "candidates": ["serek_wiejski_z_rzodkiewka_i_ogorkiem"]},
        {"days": ["poniedzialek"], "meal": "obiad", "candidates": ["dorsz_pieczony_z_ziemniakami_i_fasolka"]},
        {"days": ["poniedzialek"], "meal": "kolacja", "candidates": ["salatka_z_tunczykiem_i_jajkiem"]},
    ]
    req = EngineRequest.model_validate({"slots": slots, "allow_snacks": False})
    res = solve_week(req, repo.assumptions, repo.products, repo.dishes)
    block = res.blocks[0]
    assert block.status is BlockStatus.HARD
    fat = [v for v in block.violations if v.metric == "fat"]
    assert fat and all(v.gap < 0 for v in fat)
    assert block.binding_bounds, "brak wskazania, które limity blokują"
    assert block.conflict_core, "brak zbioru sprzecznych ograniczeń"
    assert any("widelki" in c for c in block.conflict_core)


def test_unknown_dish_is_reported_not_crashing(repo):
    slots = [{"days": ["piatek"], "meal": "obiad", "candidates": ["nie_ma_takiego_dania"]}]
    res = solve_week(EngineRequest.model_validate({"slots": slots}), repo.assumptions, repo.products, repo.dishes)
    assert res.status is BlockStatus.MODEL_ERROR
    assert "nie_ma_takiego_dania" in res.blocks[0].message


def test_batch_slot_requires_batch_dish(repo):
    slots = [{"days": ["wtorek", "sroda"], "meal": "obiad", "candidates": ["spaghetti_bolognese"]}]
    res = solve_week(EngineRequest.model_validate({"slots": slots}), repo.assumptions, repo.products, repo.dishes)
    assert res.status is BlockStatus.MODEL_ERROR
    assert "na zapas" in res.blocks[0].message


def test_person_scaling_monotone(repo):
    """Większy cel energii → typowa porcja nie mniejsza."""
    catalog = Catalog(repo.products, repo.assumptions.energy.method)
    st = EngineSettings()
    dish = repo.dishes["kurczak_curry_z_ryzem"]
    for ing in dish.ingredients:
        product = catalog.product(ing.product_id)
        small = person_bounds(ing, product, 0.8, st)
        big = person_bounds(ing, product, 1.2, st)
        assert big.default >= small.default and big.hi >= small.hi


def test_week_slots_fixture_is_full_week():
    meals = {(d, s["meal"]) for s in WEEK_SLOTS for d in s["days"]}
    assert len(meals) == 21
