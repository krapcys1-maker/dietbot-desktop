"""Cooking constraints remain hard even when calorie targets cannot be met."""
import pytest

from dietbot.app.snapshots import capture, matches_current, restore
from dietbot.checker import verify_plan
from dietbot.domain.culinary import protect_recipe
from dietbot.domain.dish import DishTemplate
from dietbot.domain.plan import PersonPortion, PlanItem, PlannedMeal, WeekPlan
from dietbot.engine import EngineRequest, solve_week
from dietbot.storage.repository import load_dishes

TOAST = "tosty_z_szynka_i_serem"


def toast_plan(repo, bread=90, cheese=15):
    dish = repo.dishes[TOAST]
    return WeekPlan(days=[0], meals=[PlannedMeal(day=0, meal="sniadanie", dish_id=TOAST,
        portions=[PersonPortion(person_id=p.id, items=[PlanItem(product_id=i.product_id,
            grams=bread if i.product_id == "chleb_tostowy" else cheese if i.product_id == "gouda"
            else i.typical_g) for i in dish.ingredients]) for p in repo.assumptions.persons])])


@pytest.mark.parametrize("bread,cheese", [(90, 15), (140, 20), (100, 20)])
def test_screenshot_toasts_are_rejected(repo, bread, cheese):
    report = verify_plan(toast_plan(repo, bread, cheese), repo.assumptions, repo.products, repo.dishes)
    assert "proporcje_przepisu" in report.codes()
    if bread % 50:
        assert "jednostka_przepisu" in report.codes()


def test_live_overlay_valid_and_idempotent(repo):
    assert not {d.id: d.culinary_errors for d in repo.dishes.values() if d.culinary_errors}
    for dish in repo.dishes.values():
        assert DishTemplate.model_validate(dish.to_data()) == dish
        assert protect_recipe(dish, repo.products) == dish
    toast = repo.dishes[TOAST]
    assert next(i for i in toast.ingredients if i.product_id == "chleb_tostowy").step_g == 50
    assert next(i for i in toast.ingredients if i.product_id == "gouda").typical_g >= 40


def test_missing_batter_proportions_fail_closed(repo):
    dish = repo.dishes["nalesniki_z_twarogiem_i_miodem"].model_copy(deep=True)
    dish.groups = []
    dish.culinary_version = 0
    guarded = protect_recipe(dish, repo.products)
    assert guarded.culinary_errors


def test_batter_group_cannot_leave_egg_unconstrained(repo):
    dish = repo.dishes["nalesniki_z_twarogiem_i_miodem"].model_copy(deep=True)
    dish.culinary_version = 0
    dish.group_of("jajko_kurze").products.remove("jajko_kurze")
    assert any("Niepełne proporcje" in e for e in protect_recipe(dish, repo.products).culinary_errors)


def test_old_snapshot_keeps_original_recipe(repo):
    import copy
    old = copy.copy(repo)
    old.dishes = load_dishes(repo.paths.root / "dane" / "dania")
    plan = toast_plan(repo)
    snapshot = capture(old, plan)
    loaded = restore(snapshot, repo)
    assert loaded.dishes[TOAST].culinary_version == 0
    assert next(i for i in loaded.dishes[TOAST].ingredients if i.product_id == "gouda").typical_g == 25
    assert not matches_current(snapshot, repo, plan)


def test_solver_cannot_purchase_missing_cheese_with_calories(repo):
    dish = repo.dishes[TOAST].model_copy(deep=True)
    for i in dish.ingredients:
        if i.product_id == "chleb_tostowy":
            i.min_g = i.max_g = i.typical_g = 100
        if i.product_id == "gouda":
            i.typical_g = 15
            i.min_g = i.max_g = 15
    dishes = {**repo.dishes, TOAST: dish}
    request = EngineRequest.model_validate({"slots": [
        {"days": [0], "meal": "sniadanie", "candidates": [TOAST]}]})
    result = solve_week(request, repo.assumptions, repo.products, dishes)
    assert result.plan is None or not result.plan.meals
