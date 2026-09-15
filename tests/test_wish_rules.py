"""Policzalne życzenia: liczba dań danej kuchni, nowe dania do wykorzystania, ochrona nowych dań i dań z życzeń
w naprawie."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dietbot.domain import MealType, Weekday
from dietbot.engine import BlockStatus
from dietbot.llm.client import LLMResult
from dietbot.planner.autofix import autofix_composition
from dietbot.planner.compose import (
    compose_fallback,
    feasible_cuisine_counts,
    validate_composition,
    wish_errors,
)
from dietbot.planner.pipeline import blocks_without_help, protect_new_dishes
from dietbot.planner.pools import build_pools
from dietbot.planner.wishes import extract_exclusions

DAYS = list(Weekday)


class FakeWishes:
    def __init__(self, value):
        self.value = value

    def complete_json(self, task, system, user, schema, **kw):
        assert "Kuchnie: polska, wloska" in user
        return LLMResult(value=schema.model_validate(self.value), attempts=1, cost_usd=0.0, prompt_tokens=0,
                         completion_tokens=0, seconds=0.0)


@pytest.fixture()
def setup(repo):
    dishes = repo.approved_dishes()
    pools = build_pools(dishes, repo.products, repo.assumptions)
    return repo.assumptions, dishes, pools


def cuisine_count(comp, dishes, cuisine):
    return sum(1 for s in comp.slots if dishes[s.dish_id].cuisine == cuisine)


def test_extract_cuisine_counts(repo):
    fake = FakeWishes({"kuchnie": [{"kuchnia": "włoska", "ile": 2}, {"kuchnia": "etiopska", "ile": 1}]})
    excl = extract_exclusions(fake, "kuchnia włoska 2 razy i raz etiopska", repo.products, repo.assumptions.cuisines)
    assert excl.cuisine_counts == {"wloska": 2}
    assert "kuchnia włoska: 2 dania" in excl.notes
    assert any("etiopska" in n for n in excl.notes)


@pytest.mark.parametrize("want", [0, 2, 5])
def test_autofix_sets_exact_cuisine_count(setup, want):
    a, dishes, pools = setup
    comp = compose_fallback(pools, dishes, a, DAYS, seed=11)
    assert validate_composition(comp, pools, dishes, a, DAYS) == []
    counts, _ = feasible_cuisine_counts({"wloska": want}, pools, dishes, a, DAYS)
    notes = autofix_composition(comp, pools, dishes, a, DAYS, cuisine_counts=counts)
    assert validate_composition(comp, pools, dishes, a, DAYS) == []
    assert wish_errors(comp, pools, dishes, a, DAYS, cuisine_counts=counts) == [], notes
    assert cuisine_count(comp, dishes, "wloska") == want


def test_feasible_counts_cap_to_library(setup):
    a, dishes, pools = setup
    counts, notes = feasible_cuisine_counts({"indyjska": 21}, pools, dishes, a, DAYS)
    assert counts["indyjska"] < 21 and notes and "planuję" in notes[0]


def test_autofix_places_new_dishes_and_keeps_them(setup):
    a, dishes, pools = setup
    comp = compose_fallback(pools, dishes, a, DAYS, seed=3)
    used = {s.dish_id for s in comp.slots}
    lunch = next(d for d in pools.by_meal[MealType.LUNCH] if d not in used and not dishes[d].is_treat)
    supper = next(d for d in pools.by_meal[MealType.SUPPER] if d not in used and not dishes[d].is_treat
                  and not dishes[d].is_sweet and not pools.profiles[d].with_eggs)
    must = {lunch, supper}
    assert len(wish_errors(comp, pools, dishes, a, DAYS, must_use=must)) == 2
    autofix_composition(comp, pools, dishes, a, DAYS, cuisine_counts={"polska": 3}, must_use=must)
    assert must <= {s.dish_id for s in comp.slots}
    assert validate_composition(comp, pools, dishes, a, DAYS) == []
    assert wish_errors(comp, pools, dishes, a, DAYS, cuisine_counts={"polska": 3}, must_use=must) == []


def test_repair_prefers_swaps_that_keep_new_dishes():
    opt = lambda current, status, days: SimpleNamespace(current=current, status=status, block_days=days)  # noqa: E731
    options = [opt("nowe", BlockStatus.OK, [0, 1]), opt("stare", BlockStatus.OK, [0, 1]),
               opt("nowe", BlockStatus.OK, [2]), opt("stare", BlockStatus.SOFT, [2])]
    kept = protect_new_dishes(options, {"nowe"})
    assert [(o.current, o.block_days) for o in kept] == [("stare", [0, 1]), ("nowe", [2]), ("stare", [2])]
    assert protect_new_dishes(options, None) == options



SOUP = "zupa_ogorkowa_z_ziemniakami_i_kurczakiem"


def heavy_soup_result(choices=None):
    from dietbot.engine import BlockReport, EngineResult
    from dietbot.engine.types import MassViolation

    days = [Weekday.TUESDAY, Weekday.WEDNESDAY]
    block = BlockReport(days=days, status=BlockStatus.SOFT, solver_status="OPTIMAL", choices=choices or {},
                        mass_violations=[MassViolation(days=days, meal=MealType.LUNCH, dish_id=SOUP,
                                                       person_id="mezczyzna", grams=926, cap=820)])
    return EngineResult(status=BlockStatus.SOFT, plan=None, blocks=[block], engine="test", input_hash="test")


def test_repair_replaces_a_requested_dish_only_when_nothing_else_helps():
    result = heavy_soup_result()  # talerz zupy 106 g za ciężki → 4,24 na każdy dzień bloku
    opt = lambda current, status, bad: SimpleNamespace(current=current, status=status, block_days=[1, 2],  # noqa: E731
                                                       day_badness=bad)
    soup_fix = opt(SOUP, BlockStatus.OK, {1: 0.0, 2: 0.0})
    better = opt("sniadanie", BlockStatus.SOFT, {1: 1.0, 2: 1.0})
    # przed ostatnią rundą inna zamiana choć poprawia blok — zupy z życzeń nie ruszamy
    assert blocks_without_help(result, [soup_fix, better], {SOUP}, last_round=False) == set()
    assert protect_new_dishes([soup_fix, better], {SOUP}, result, last_round=False) == [better]
    # w ostatniej rundzie sama poprawa nie chroni, ale zupę wymieniamy tylko na zamianę naprawiającą cały blok
    assert blocks_without_help(result, [soup_fix, better], {SOUP}, last_round=True) == {(1, 2)}
    assert protect_new_dishes([soup_fix, better], {SOUP}, result, last_round=True) == [soup_fix, better]
    soup_partial = opt(SOUP, BlockStatus.SOFT, {1: 0.5, 2: 0.5})
    assert protect_new_dishes([soup_partial, better], {SOUP}, result, last_round=True) == [better]
    fix = opt("sniadanie", BlockStatus.OK, {1: 0.0, 2: 0.0})
    assert protect_new_dishes([soup_fix, fix], {SOUP}, result, last_round=True) == [fix]


def test_repair_checks_other_meals_of_a_requested_dish_day_first(repo, setup, monkeypatch):
    from dietbot.engine import EngineSettings
    from dietbot.planner import repair

    a, dishes, pools = setup
    comp = compose_fallback(pools, dishes, a, DAYS, seed=1)
    for s in comp.slots:
        if s.dish_id == SOUP:
            s.dish_id = "zupa_fasolowa_z_tofu"
    soup_slot = next(s for s in comp.slots if len(s.days) == 2)
    soup_slot.dish_id = SOUP
    block_slots = [s for s in comp.slots if {int(d) for d in s.days} & {1, 2}]
    result = heavy_soup_result({s.key: s.dish_id for s in block_slots})
    monkeypatch.setattr(repair, "MAX_CANDIDATES_NEAR_KEPT", 2)
    args = (comp, result, pools, a, repo.products, dishes, EngineSettings(), {})
    others = repair.evaluate_swaps(*args, max_per_slot=1, keep={SOUP})
    # problem jest w samej zupie z życzeń, więc sprawdzamy pozostałe posiłki jej dni — szerzej niż zwykle
    expected = {s.key for s in block_slots if s.dish_id != SOUP}
    assert {o.slot_key for o in others} == expected
    assert all(sum(o.slot_key == k for o in others) == 2 for k in expected)
    kept = repair.evaluate_swaps(*args, max_per_slot=1, keep={SOUP}, kept_only=True, blocks={(1, 2)})
    assert [o.slot_key for o in kept] == [soup_slot.key]
    # bez chronionych dań problem w obiedzie oznacza zamiany samego obiadu
    assert {o.slot_key for o in repair.evaluate_swaps(*args, max_per_slot=1)} == {soup_slot.key}


WISH = "Kuchnia włoska 2 razy, zupa, resztę wymyśl"
FATTY_MONDAY = {"sniadanie": "makrela_wedzona_z_twarozkiem_i_chlebem_zytnim",
                "obiad": "musaka_z_baklazanem_i_miesem_mielonym", "kolacja": "salatka_wloska_z_kurczakiem_i_pesto"}


def fatty_monday_week(setup):
    """Tydzień zgodny z zasadami, w którym poniedziałek to trzy tłuste dania (śniadanie i obiad „nowe”, kolacja włoska)."""
    a, dishes, pools = setup
    for seed in range(1, 30):
        comp = compose_fallback(pools, dishes, a, DAYS, seed=seed)
        if set(FATTY_MONDAY.values()) & {s.dish_id for s in comp.slots}:
            continue
        for s in comp.slots:
            if [int(d) for d in s.days] == [0] and s.meal.value in FATTY_MONDAY:
                s.dish_id, s.alternates = FATTY_MONDAY[s.meal.value], []
        if not validate_composition(comp, pools, dishes, a, DAYS):
            return comp
    pytest.skip("brak zgodnego tygodnia z tłustym poniedziałkiem")


def test_cuisine_word_in_a_dish_name_does_not_protect_it_from_rebalancing(setup):
    from dietbot.planner.autofix import rebalance_days
    from dietbot.planner.balance import day_deviation
    from dietbot.planner.compose import wish_focus

    a, dishes, pools = setup
    assert FATTY_MONDAY["kolacja"] in wish_focus(WISH, pools, dishes)  # nazwa zawiera „włoska”
    assert FATTY_MONDAY["kolacja"] not in wish_focus(WISH, pools, dishes, skip_cuisines=True)
    comp = fatty_monday_week(setup)
    counts = {"wloska": sum(1 for s in comp.slots if dishes[s.dish_id].cuisine == "wloska")}
    new = {FATTY_MONDAY["sniadanie"], FATTY_MONDAY["obiad"]}

    def monday_dev():
        return day_deviation([pools.profiles[s.dish_id].typical for s in comp.slots if 0 in {int(d) for d in s.days}],
                             pools.target)

    before = monday_dev()
    rebalance_days(comp, pools, dishes, a, DAYS, cuisine_counts=counts,
                   protected=new | wish_focus(WISH, pools, dishes, skip_cuisines=True))
    supper = next(s for s in comp.slots if [int(d) for d in s.days] == [0] and s.meal is MealType.SUPPER)
    assert supper.dish_id != FATTY_MONDAY["kolacja"] and dishes[supper.dish_id].cuisine == "wloska"
    assert new <= {s.dish_id for s in comp.slots} and monday_dev() < before
    assert wish_errors(comp, pools, dishes, a, DAYS, cuisine_counts=counts) == []


def test_repair_only_checks_swaps_that_keep_cuisine_counts(repo, setup):
    from dietbot.engine import BlockReport, EngineResult, EngineSettings
    from dietbot.engine.types import BandViolation
    from dietbot.planner.repair import evaluate_swaps

    a, dishes, pools = setup
    comp = fatty_monday_week(setup)
    monday = [s for s in comp.slots if [int(d) for d in s.days] == [0]]
    block = BlockReport(days=[Weekday.MONDAY], status=BlockStatus.HARD, solver_status="OPTIMAL",
                        choices={s.key: s.dish_id for s in monday},
                        violations=[BandViolation(day=Weekday.MONDAY, person_id="mezczyzna", metric="carbs", actual=200,
                                                  min=240, max=270, gap=-40)])
    result = EngineResult(status=BlockStatus.HARD, plan=None, blocks=[block], engine="test", input_hash="test")
    counts = {"wloska": 2}
    options = evaluate_swaps(comp, result, pools, a, repo.products, dishes, EngineSettings(), {}, max_per_slot=3,
                             cuisine_counts=counts)
    supper = [o for o in options if o.meal is MealType.SUPPER]
    assert supper and all(dishes[o.candidate].cuisine == "wloska" for o in supper)
    others = [o for o in options if o.meal is not MealType.SUPPER]
    assert others and all(dishes[o.candidate].cuisine != "wloska" for o in others)


def test_new_dishes_beyond_a_cuisine_count_are_not_required(setup):
    from dietbot.planner.compose import limit_new_dishes_to_counts

    a, dishes, pools = setup
    italian = {m: [d for d in pools.by_meal[m] if dishes[d].cuisine == "wloska" and not dishes[d].is_treat][:2]
               for m in (MealType.LUNCH, MealType.SUPPER)}
    new_italian = set(italian[MealType.LUNCH] + italian[MealType.SUPPER][:1])  # 2 obiady + kolacja
    other = next(d for d in pools.by_meal[MealType.BREAKFAST] if dishes[d].cuisine == "polska")
    kept, notes = limit_new_dishes_to_counts(new_italian | {other}, {"wloska": 2}, dishes)
    assert other in kept and len(kept & new_italian) == 2
    # różne posiłki najpierw: obiad i kolacja, a nie dwa obiady
    assert {dishes[d].meal_types[0] for d in kept & new_italian} == {MealType.LUNCH, MealType.SUPPER}
    assert notes and "zostają w bibliotece" in notes[0]
    assert limit_new_dishes_to_counts({other}, {"wloska": 2}, dishes) == ({other}, [])
