"""Limit masy talerza: silnik go pilnuje (miękko, z karą), zgłasza przekroczenia, a weryfikator ostrzega."""

from __future__ import annotations

from dietbot.checker import verify_plan
from dietbot.domain import MealType, PlanItem
from dietbot.engine import BlockStatus, EngineRequest, solve_week


def _masses(plan):
    return {(int(m.day), m.meal.value, p.person_id): sum(i.grams for i in p.items) for m in plan.meals for p in m.portions}


def test_week_respects_mass_caps(repo, week_result):
    for (_day, meal, pid), grams in _masses(week_result.plan).items():
        cap = repo.assumptions.person(pid).meal_mass_cap(MealType(meal))
        assert grams <= cap * 1.02 + 1, f"{meal} {pid}: {grams} g > limit {cap} g"
    assert not any(b.mass_violations for b in week_result.blocks)


def test_low_cap_is_reported_as_soft_deviation(repo):
    a = repo.assumptions.model_copy(deep=True)
    for p in a.persons:
        p.max_meal_mass_g = {MealType.BREAKFAST: 150.0, MealType.LUNCH: 750.0, MealType.SUPPER: 650.0}
    slots = [
        {"days": [0], "meal": "sniadanie", "candidates": ["kanapki_z_szynka_jajkiem_i_pomidorem"]},
        {"days": [0], "meal": "obiad", "candidates": ["kurczak_curry_z_ryzem"]},
        {"days": [0], "meal": "kolacja", "candidates": ["salatka_grecka_z_kurczakiem"]},
    ]
    res = solve_week(EngineRequest.model_validate({"slots": slots}), a, repo.products, repo.dishes)
    block = res.blocks[0]
    assert block.mass_violations and all(v.meal is MealType.BREAKFAST for v in block.mass_violations)
    assert block.status in (BlockStatus.SOFT, BlockStatus.HARD)
    report = verify_plan(res.plan, a, repo.products, repo.dishes)
    assert "duza_porcja" in report.codes() and report.ok == (block.status is not BlockStatus.HARD)


def test_default_caps_scale_with_energy(repo):
    woman, man = repo.assumptions.person("kobieta"), repo.assumptions.person("mezczyzna")
    for meal in (MealType.BREAKFAST, MealType.LUNCH, MealType.SUPPER):
        assert woman.meal_mass_cap(meal) < man.meal_mass_cap(meal)
    bare = woman.model_copy(update={"max_meal_mass_g": {}})
    assert 400 <= bare.meal_mass_cap(MealType.LUNCH) <= 800


def test_checker_warns_about_huge_portion(repo, week_result):
    plan = week_result.plan.model_copy(deep=True)
    lunch = next(m for m in plan.meals if int(m.day) == 0 and m.meal is MealType.LUNCH)
    lunch.portion("kobieta").items.append(PlanItem(product_id="ogorek", grams=900))
    issues = [i for i in verify_plan(plan, repo.assumptions, repo.products, repo.dishes).issues if i.code == "duza_porcja"]
    assert issues and issues[0].severity.value == "ostrzezenie" and "Kobieta" in issues[0].message
