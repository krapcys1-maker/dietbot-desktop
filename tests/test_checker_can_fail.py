"""Weryfikator musi wykrywać błędy — każdy test psuje dokładnie jedną rzecz w poprawnym planie."""

from __future__ import annotations

import pytest

from dietbot.checker import verify_plan
from dietbot.domain import MealType, PlanItem, PlannedSnack, Weekday


@pytest.fixture()
def plan(week_result):
    return week_result.plan.model_copy(deep=True)


def check(plan, repo):
    return verify_plan(plan, repo.assumptions, repo.products, repo.dishes)


def meal(plan, day: int, meal: MealType):
    return next(m for m in plan.meals if int(m.day) == day and m.meal is meal)


def test_baseline_plan_is_ok(plan, repo):
    assert check(plan, repo).ok


def test_detects_extra_oil_breaking_band(plan, repo):
    m = meal(plan, 0, MealType.LUNCH)
    m.portion("kobieta").items.append(PlanItem(product_id="oliwa_z_oliwek", grams=40))
    assert "widelki" in check(plan, repo).codes()


def test_detects_missing_meal(plan, repo):
    plan.meals = [m for m in plan.meals if not (int(m.day) == 4 and m.meal is MealType.SUPPER)]
    assert "brak_posilku" in check(plan, repo).codes()


def test_detects_missing_portion(plan, repo):
    m = meal(plan, 5, MealType.BREAKFAST)
    m.portions = [p for p in m.portions if p.person_id != "mezczyzna"]
    assert "brak_porcji" in check(plan, repo).codes()


def test_detects_batch_portion_mismatch(plan, repo):
    m = meal(plan, 2, MealType.LUNCH)
    m.portion("mezczyzna").items[0].grams += 10
    assert "danie_na_zapas" in check(plan, repo).codes()


def test_detects_batch_rule_broken_by_different_dish(plan, repo):
    m = meal(plan, 2, MealType.LUNCH)
    m.dish_id = "kurczak_curry_z_ryzem"
    m.batch_group = None
    assert "danie_na_zapas" in check(plan, repo).codes()


def test_detects_forbidden_product(plan, repo):
    m = meal(plan, 0, MealType.SUPPER)
    m.portion("kobieta").items.append(PlanItem(product_id="rukola", grams=30))
    assert "zakazany_produkt" in check(plan, repo).codes()


def test_detects_person_exclusion(plan, repo):
    a = repo.assumptions.model_copy(deep=True)
    a.persons[0].excluded_products = ["pomidor"]
    report = verify_plan(plan, a, repo.products, repo.dishes)
    assert "zakazany_produkt" in report.codes()


def test_detects_sweet_breakfast(plan, repo):
    dishes = dict(repo.dishes)
    m = meal(plan, 0, MealType.BREAKFAST)
    sweet = dishes[m.dish_id].model_copy(deep=True)
    sweet.tags = [*sweet.tags, "slodkie"]
    dishes[m.dish_id] = sweet
    report = verify_plan(plan, repo.assumptions, repo.products, dishes)
    assert "zakazany_tag" in report.codes()


def test_detects_too_many_treats(plan, repo):
    dishes = dict(repo.dishes)
    for day, meal_type in [(0, MealType.LUNCH), (3, MealType.LUNCH)]:
        m = meal(plan, day, meal_type)
        treat = dishes[m.dish_id].model_copy(deep=True)
        treat.tags = [*treat.tags, "pula_20"]
        dishes[m.dish_id] = treat
    report = verify_plan(plan, repo.assumptions, repo.products, dishes)
    assert "pula_20" in report.codes()


def test_detects_too_many_sweet_suppers(plan, repo):
    dishes = dict(repo.dishes)
    for day in (0, 3):
        m = meal(plan, day, MealType.SUPPER)
        sweet = dishes[m.dish_id].model_copy(deep=True)
        sweet.tags = [*sweet.tags, "slodkie"]
        dishes[m.dish_id] = sweet
    report = verify_plan(plan, repo.assumptions, repo.products, dishes)
    assert "slodkie_kolacje" in report.codes()


def test_detects_repeated_dish(plan, repo):
    m = meal(plan, 6, MealType.SUPPER)
    m.dish_id = meal(plan, 0, MealType.SUPPER).dish_id
    assert "powtorka_dania" in check(plan, repo).codes()


def test_detects_forbidden_carb_source(plan, repo):
    dishes = dict(repo.dishes)
    m = meal(plan, 0, MealType.LUNCH)
    dish = dishes[m.dish_id].model_copy(deep=True)
    for ing in dish.ingredients:
        if ing.product_id == "ryz_bialy":
            ing.product_id = "kasza_bulgur"
    dishes[m.dish_id] = dish
    for portion in m.portions:
        for item in portion.items:
            if item.product_id == "ryz_bialy":
                item.product_id = "kasza_bulgur"
    report = verify_plan(plan, repo.assumptions, repo.products, dishes)
    assert "zrodlo_weglowodanow" in report.codes()


def test_detects_second_snack(plan, repo):
    plan.snacks.append(PlannedSnack(day=Weekday.MONDAY, person_id="kobieta", dish_id="przekaska_jablko",
                                    items=[PlanItem(product_id="jablko", grams=150)]))
    plan.snacks.append(PlannedSnack(day=Weekday.MONDAY, person_id="kobieta", dish_id="przekaska_banan",
                                    items=[PlanItem(product_id="banan", grams=120)]))
    assert "przekaski_limit" in check(plan, repo).codes()


def test_detects_snack_not_allowed(plan, repo):
    plan.snacks.append(PlannedSnack(day=Weekday.FRIDAY, person_id="kobieta", dish_id="burger_wolowy_z_frytkami",
                                    items=[PlanItem(product_id="frytki_mrozone", grams=10)]))
    assert "przekaska_niedozwolona" in check(plan, repo).codes()


def test_detects_unknown_product(plan, repo):
    m = meal(plan, 1, MealType.SUPPER)
    m.portion("kobieta").items.append(PlanItem(product_id="produkt_widmo", grams=10))
    assert "nieznany_produkt" in check(plan, repo).codes()


def test_detects_engine_totals_mismatch(week_result, repo):
    totals = [t.model_dump(mode="json") for b in week_result.blocks for t in b.totals]
    totals[0]["kcal"] += 50
    report = verify_plan(week_result.plan, repo.assumptions, repo.products, repo.dishes, totals)
    assert "niezgodnosc_z_silnikiem" in report.codes()


def test_detects_protein_anchor_miss_as_warning(plan, repo):
    m = meal(plan, 0, MealType.BREAKFAST)
    for item in m.portion("mezczyzna").items:
        if item.product_id in ("szynka_gotowana", "jajko_kurze"):
            item.grams = 0
    report = check(plan, repo)
    assert "kotwica_bialka" in report.codes()
