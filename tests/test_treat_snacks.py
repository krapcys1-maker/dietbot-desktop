"""Przekąski z puli 20% (lody, chipsy, popcorn…): slot „przekąska” w dniu, liczony do puli tygodnia."""

from __future__ import annotations

import pytest

from dietbot.checker import verify_plan
from dietbot.domain import MealType, Weekday
from dietbot.engine import BlockStatus, EngineRequest, solve_week
from dietbot.planner.autofix import autofix_composition
from dietbot.planner.compose import (
    SlotPlan,
    WeekChoice,
    compose_fallback,
    composition_from_llm,
    snack_kind,
    validate_composition,
    vary_treat_snacks,
)
from dietbot.planner.pools import build_pools

DAYS = list(Weekday)


@pytest.fixture(scope="module")
def setup(repo):
    dishes = repo.approved_dishes()
    return repo.assumptions, dishes, build_pools(dishes, repo.products, repo.assumptions)


def test_treat_snacks_are_in_their_own_pool(setup):
    _, dishes, pools = setup
    assert {"przekaska20_lody_waniliowe", "przekaska20_chipsy", "przekaska20_popcorn"} <= set(pools.treat_snacks)
    assert all(dishes[d].is_treat and MealType.SNACK in dishes[d].meal_types for d in pools.treat_snacks)
    assert not set(pools.treat_snacks) & {d for ids in pools.by_meal.values() for d in ids}


def test_engine_scales_a_planned_treat_snack_and_fits_the_day(repo):
    slots = [{"days": [5], "meal": "sniadanie", "candidates": ["kanapki_z_szynka_jajkiem_i_pomidorem"]},
             {"days": [5], "meal": "obiad", "candidates": ["kurczak_curry_z_ryzem"]},
             {"days": [5], "meal": "kolacja", "candidates": ["salatka_grecka_z_kurczakiem"]},
             {"days": [5], "meal": "przekaska", "candidates": ["przekaska20_lody_waniliowe"]}]
    res = solve_week(EngineRequest.model_validate({"slots": slots}), repo.assumptions, repo.products, repo.dishes)
    assert res.blocks[0].status is BlockStatus.OK
    ice = next(m for m in res.plan.meals if m.meal is MealType.SNACK)
    grams = {p.person_id: sum(i.grams for i in p.items) for p in ice.portions}
    assert 80 <= grams["kobieta"] < grams["mezczyzna"] <= 240  # porcja rośnie z zapotrzebowaniem
    report = verify_plan(res.plan, repo.assumptions, repo.products, repo.dishes)
    assert not report.errors()


def test_engine_rejects_a_multi_day_snack_slot():
    with pytest.raises(ValueError):
        EngineRequest.model_validate({"slots": [{"days": [1, 2], "meal": "przekaska", "candidates": ["przekaska20_chipsy"]}]})


def test_model_can_plan_a_treat_snack_and_rules_count_it(setup):
    a, dishes, pools = setup
    base = compose_fallback(pools, dishes, a, DAYS, seed=8)
    by_day: dict[int, dict] = {}
    for s in base.slots:
        if s.meal is MealType.SNACK:
            continue
        for d in s.days:
            by_day.setdefault(int(d), {})[s.meal.value] = {"danie": s.dish_id}
    names = ["poniedzialek", "wtorek", "sroda", "czwartek", "piatek", "sobota", "niedziela"]
    treat_days = {int(d) for s in base.slots if s.meal is not MealType.SNACK and dishes[s.dish_id].is_treat for d in s.days}
    free = next(d for d in (5, 6, 4, 3) if d not in treat_days)
    choice = WeekChoice.model_validate({"komentarz": "Smaczny tydzień.", "dni": [
        {"dzien": names[d], **by_day[d], "przekaska_20": "przekaska20_popcorn" if d == free else None} for d in range(7)]})
    comp = composition_from_llm(choice, a, DAYS)
    snack = [s for s in comp.slots if s.meal is MealType.SNACK]
    assert [(int(s.days[0]), s.dish_id) for s in snack] == [(free, "przekaska20_popcorn")]
    treats = sum(1 for s in comp.slots if dishes[s.dish_id].is_treat for _ in s.days)
    errors = validate_composition(comp, pools, dishes, a, DAYS)
    if treats > a.week.treat_meals.max:
        assert any("puli 20%" in e for e in errors)
    else:
        assert errors == []


def test_autofix_drops_the_snack_on_a_day_with_a_treat_meal(setup):
    a, dishes, pools = setup
    comp = compose_fallback(pools, dishes, a, DAYS, seed=8)
    comp.slots = [s for s in comp.slots if s.meal is not MealType.SNACK]
    treat_slot = next(s for s in comp.slots if dishes[s.dish_id].is_treat)
    day = treat_slot.days[0]
    comp.slots.append(SlotPlan(days=[day], meal=MealType.SNACK, dish_id="przekaska20_chipsy"))
    errors = validate_composition(comp, pools, dishes, a, DAYS)
    assert any("więcej niż jeden posiłek z puli 20%" in e for e in errors)
    notes = autofix_composition(comp, pools, dishes, a, DAYS)
    assert any("bez przekąski" in n for n in notes)
    assert treat_slot in comp.slots and not any(s.meal is MealType.SNACK for s in comp.slots)
    assert validate_composition(comp, pools, dishes, a, DAYS) == []


def test_unknown_snack_in_the_plan_is_an_error(setup):
    a, dishes, pools = setup
    comp = compose_fallback(pools, dishes, a, DAYS, seed=8)
    comp.slots = [s for s in comp.slots if s.meal is not MealType.SNACK]
    comp.slots.append(SlotPlan(days=[Weekday.SUNDAY], meal=MealType.SNACK, dish_id="przekaska_jablko"))
    assert any("nie jest przekąską z puli 20%" in e for e in validate_composition(comp, pools, dishes, a, DAYS))


def test_treat_snacks_of_one_week_are_of_different_kinds(setup):
    a, dishes, pools = setup
    comp = compose_fallback(pools, dishes, a, DAYS, seed=8)
    comp.slots = [s for s in comp.slots if s.meal is not MealType.SNACK]
    comp.slots += [SlotPlan(days=[Weekday.TUESDAY], meal=MealType.SNACK, dish_id="przekaska20_lody_waniliowe"),
                   SlotPlan(days=[Weekday.SATURDAY], meal=MealType.SNACK, dish_id="przekaska20_lody_czekoladowe")]
    assert snack_kind("przekaska20_lody_na_patyku") == "lody"
    notes = vary_treat_snacks(comp, pools, dishes, seed=3)
    snacks = {int(s.days[0]): s.dish_id for s in comp.slots if s.meal is MealType.SNACK}
    assert len(notes) == 1 and snacks[1] == "przekaska20_lody_waniliowe" and snack_kind(snacks[5]) != "lody"
    # przekąska z życzeń zostaje — wymieniana jest ta druga, nawet gdy jest wcześniej w tygodniu
    comp.slots[-1].dish_id = "przekaska20_lody_czekoladowe"
    vary_treat_snacks(comp, pools, dishes, protected={"przekaska20_lody_czekoladowe"}, seed=3)
    snacks = {int(s.days[0]): s.dish_id for s in comp.slots if s.meal is MealType.SNACK}
    assert snacks[5] == "przekaska20_lody_czekoladowe" and snack_kind(snacks[1]) != "lody"
    # liczba dań kuchni z życzeń się nie zmienia (zamiana w obrębie tej samej kuchni albo poza nimi)
    comp.slots[-2].dish_id = "przekaska20_lody_czekoladowe"
    comp.slots[-1].dish_id = "przekaska20_lody_waniliowe"
    cuisines = [dishes[s.dish_id].cuisine for s in comp.slots]
    counts = {c: cuisines.count(c) for c in ("polska", "amerykanska", "meksykanska")}
    vary_treat_snacks(comp, pools, dishes, seed=5, cuisine_counts=counts)
    cuisines = [dishes[s.dish_id].cuisine for s in comp.slots]
    assert {c: cuisines.count(c) for c in counts} == counts
    assert len({snack_kind(s.dish_id) for s in comp.slots if s.meal is MealType.SNACK}) == 2
