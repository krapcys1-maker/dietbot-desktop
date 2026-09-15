"""Planer tygodnia bez LLM (algorytm zapasowy) + walidacja zasad tygodnia."""

from __future__ import annotations

import pytest

from dietbot.domain import MealType, Weekday
from dietbot.planner.compose import (
    Composition,
    SlotPlan,
    WeekChoice,
    clean_alternates,
    compose_fallback,
    validate_composition,
)
from dietbot.planner.pipeline import WeekPlanner
from dietbot.planner.pools import build_pools

DAYS = [Weekday(i) for i in range(7)]


@pytest.fixture(scope="module")
def pools(repo):
    return build_pools(repo.approved_dishes(), repo.products, repo.assumptions)


def test_pools_respect_prohibitions(repo, pools):
    for meal, ids in pools.by_meal.items():
        for dish_id in ids:
            dish = repo.dishes[dish_id]
            assert meal in dish.meal_types
            assert not dish.is_batch
            if meal in (MealType.BREAKFAST, MealType.LUNCH):
                assert "slodkie" not in dish.tags
    assert pools.batch[0], "brak zupy na zapas w puli"


def test_fallback_composition_is_valid_and_deterministic(repo, pools):
    dishes = repo.approved_dishes()
    c1 = compose_fallback(pools, dishes, repo.assumptions, DAYS, seed=5)
    c2 = compose_fallback(pools, dishes, repo.assumptions, DAYS, seed=5)
    assert [(s.key, s.dish_id, s.alternates) for s in c1.slots] == [(s.key, s.dish_id, s.alternates) for s in c2.slots]
    assert validate_composition(c1, pools, dishes, repo.assumptions, DAYS) == []
    meals = [s for s in c1.slots if s.meal is not MealType.SNACK]
    assert len(meals) == 20  # 21 posiłków, zupa wt+śr to jeden slot
    # plus najwyżej jedna przekąska z puli 20% (lody, chipsy…) w dniu bez innego posiłku z puli
    snacks = [s for s in c1.slots if s.meal is MealType.SNACK]
    assert len(snacks) <= 1 and all(s.dish_id in pools.treat_snacks for s in snacks)


def test_validation_catches_rule_breaks(repo, pools):
    dishes = repo.approved_dishes()
    comp = compose_fallback(pools, dishes, repo.assumptions, DAYS, seed=2)
    # powtórka dania
    broken = Composition(slots=[SlotPlan(s.days, s.meal, s.dish_id) for s in comp.slots])
    lunches = [s for s in broken.slots if s.meal is MealType.LUNCH and len(s.days) == 1]
    lunches[1].dish_id = lunches[0].dish_id
    assert any("powtórzone" in e for e in validate_composition(broken, pools, dishes, repo.assumptions, DAYS))
    # danie spoza puli (słodkie na obiad nie istnieje w puli) i nieznane danie
    broken.slots[0].dish_id = "nie_ma_takiego"
    assert any("nie może być użyte" in e for e in validate_composition(broken, pools, dishes, repo.assumptions, DAYS))


def test_validation_one_treat_per_day_and_egg_limit(repo, pools):
    dishes = repo.approved_dishes()
    comp = compose_fallback(pools, dishes, repo.assumptions, DAYS, seed=4)
    slots = [SlotPlan(s.days, s.meal, s.dish_id) for s in comp.slots]
    friday = {s.meal: s for s in slots if s.days == [Weekday.FRIDAY]}
    used = {s.dish_id for s in slots}
    friday[MealType.LUNCH].dish_id = next(d for d in pools.by_meal[MealType.LUNCH] if dishes[d].is_treat and d not in used)
    friday[MealType.SUPPER].dish_id = next(d for d in pools.by_meal[MealType.SUPPER] if dishes[d].is_treat and d not in used)
    errors = validate_composition(Composition(slots=slots), pools, dishes, repo.assumptions, DAYS)
    assert any("Piątek: więcej niż jeden posiłek z puli 20%" in e for e in errors)

    eggs = iter([d for d in pools.by_meal[MealType.BREAKFAST] if pools.profiles[d].with_eggs])
    slots = [SlotPlan(s.days, s.meal, next(eggs) if s.meal is MealType.BREAKFAST else s.dish_id) for s in comp.slots]
    errors = validate_composition(Composition(slots=slots), pools, dishes, repo.assumptions, DAYS)
    assert any(e.startswith("jajka są w ") and "maksimum 5" in e for e in errors)  # 7 śniadań z jajkami + ewentualne kolacje


def test_alternates_cleaned_to_same_status(repo, pools):
    dishes = repo.approved_dishes()
    comp = compose_fallback(pools, dishes, repo.assumptions, DAYS, seed=3)
    treat_lunch = next(d for d in pools.by_meal[MealType.LUNCH] if dishes[d].is_treat)
    normal_lunch = next(s for s in comp.slots if s.meal is MealType.LUNCH and len(s.days) == 1
                        and not dishes[s.dish_id].is_treat)
    normal_lunch.alternates = [treat_lunch]
    notes = clean_alternates(comp, pools, dishes, repo.assumptions)
    assert treat_lunch not in normal_lunch.alternates
    assert notes


def test_llm_comment_drops_sentences_with_numbers():
    # liczby podaje program — zdanie z liczbą wypada, ale reszta odpowiedzi (cały tydzień) zostaje
    choice = WeekChoice.model_validate({"dni": [], "komentarz": "Tydzień ma 3 posiłki z puli 20%. Lekki, różnorodny "
                                                               "tydzień! Ryba raz w tygodniu."})
    assert choice.komentarz == "Lekki, różnorodny tydzień!"
    assert WeekChoice.model_validate({"dni": [], "komentarz": "Kilka zup."}).komentarz == ""


def test_full_week_without_llm_passes_checker(repo):
    out = WeekPlanner(repo, llm=None).plan_week(seed=1)
    assert out.ok, (out.log, [i.message for i in out.report.issues] if out.report else None)
    assert out.plan is not None and len([m for m in out.plan.meals if m.meal is not MealType.SNACK]) == 21


def test_autofix_repairs_countable_rules(repo, pools):
    from dietbot.planner.autofix import autofix_composition

    dishes = repo.approved_dishes()
    a = repo.assumptions
    comp = compose_fallback(pools, dishes, a, DAYS, seed=4)
    comp.slots = [s for s in comp.slots if s.meal is not MealType.SNACK]  # bez przekąski z puli 20%
    used = {s.dish_id for s in comp.slots}
    normal = {m: [d for d in pools.by_meal[m] if not dishes[d].is_treat and not dishes[d].is_sweet and d not in used]
              for m in (MealType.LUNCH, MealType.SUPPER)}
    for s in comp.slots:  # usuń wszystkie posiłki z puli 20%
        if dishes[s.dish_id].is_treat:
            s.dish_id = normal[s.meal].pop(0)
    sweet = [d for d in pools.by_meal[MealType.SUPPER] if dishes[d].is_sweet and d not in {x.dish_id for x in comp.slots}]
    for day, d in zip((1, 2, 3), sweet, strict=False):  # trzy słodkie kolacje
        next(s for s in comp.slots if s.days == [Weekday(day)] and s.meal is MealType.SUPPER).dish_id = d
    before = validate_composition(comp, pools, dishes, a, DAYS)
    assert any("puli 20%" in e for e in before) and any("słodkich kolacji" in e for e in before)
    notes = autofix_composition(comp, pools, dishes, a, DAYS)
    assert notes and validate_composition(comp, pools, dishes, a, DAYS) == []
    treat_days = [int(s.days[0]) for s in comp.slots if dishes[s.dish_id].is_treat]
    assert len(treat_days) == len(set(treat_days)) >= a.week.treat_meals.min


def test_wish_exclusions_filter_pools(repo):
    from dietbot.domain.product import Category
    from dietbot.llm.client import LLMResult
    from dietbot.planner.wishes import extract_exclusions

    class Fake:
        def complete_json(self, task, system, user, schema, **kw):
            return LLMResult(value=schema.model_validate({"bez_kategorii": ["ryby"], "bez_produktow": ["pieczarki"]}),
                             attempts=1, cost_usd=0.0, prompt_tokens=0, completion_tokens=0, seconds=0.0)

    excl = extract_exclusions(Fake(), "bez ryb i bez grzybów", repo.products)
    assert "pieczarki" in excl.product_ids and all(repo.products[p].category is Category.FISH or p == "pieczarki"
                                                  for p in excl.product_ids)
    filtered = build_pools(repo.approved_dishes(), repo.products, repo.assumptions, extra_banned=excl.product_ids)
    for ids in filtered.by_meal.values():
        for d in ids:
            ings = [i for i in repo.dishes[d].ingredients if not i.optional]
            assert not any(i.product_id in excl.product_ids for i in ings), d
    assert sum(map(len, filtered.by_meal.values())) < sum(map(len, build_pools(
        repo.approved_dishes(), repo.products, repo.assumptions).by_meal.values()))
