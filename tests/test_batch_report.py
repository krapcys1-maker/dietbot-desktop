from copy import deepcopy

from dietbot.checker import verify_plan
from dietbot.domain.plan import WeekPlan
from dietbot.report.batch import batch_recipe
from dietbot.report.markdown import render_plan


def example_plan():
    amounts = {
        "wolowina_gulaszowa": (100, 140), "ziemniaki": (270, 400),
        "papryka_czerwona": (70, 90), "cebula": (40, 50),
        "koncentrat_pomidorowy": (15, 25), "olej_rzepakowy": (5, 10),
        "chleb_mieszany": (50, 100),
    }
    return WeekPlan.model_validate({"dni": [1, 2], "posilki": [
        {"dzien": day, "posilek": "obiad", "danie": "zupa_gulaszowa_z_ziemniakami",
         "partia": "zupa", "garnek_g": {"ziemniaki": 1},
         "porcje": [{"osoba": person, "skladniki": [
             {"produkt": pid, "gramy": grams[index]} for pid, grams in amounts.items()
         ]} for index, person in enumerate(("kobieta", "mezczyzna"))]}
        for day in (1, 2)
    ]})


def test_complete_batch_from_actual_portions_not_cached_pot(repo):
    plan = example_plan()
    before = deepcopy(plan.model_dump())
    dish = repo.dishes[plan.meals[0].dish_id]
    original_steps = list(dish.steps)
    batch = batch_recipe(plan, plan.meals[0], dish, repo.products, repo.assumptions.persons)
    totals = {i["product_id"]: i["grams"] for i in batch["items"]}
    assert totals["wolowina_gulaszowa"] == 480
    assert totals["ziemniaki"] == 1340
    assert totals["papryka_czerwona"] == 320
    assert totals["cebula"] == 180
    assert totals["koncentrat_pomidorowy"] == 80
    assert totals["olej_rzepakowy"] == 30
    assert totals["chleb_mieszany"] == 300
    assert next(i for i in batch["items"] if i["product_id"] == "chleb_mieszany")["side"]
    assert batch["title"] == "Całość na 2 dni dla 2 osób — 4 porcje"
    assert batch["water"] and "1,5 l" not in " ".join(batch["steps"])
    assert dish.steps == original_steps and plan.model_dump() == before
    assert batch_recipe(plan, plan.meals[1], dish, repo.products, repo.assumptions.persons) == batch


def test_export_recipe_once_then_reheat(repo):
    plan = example_plan()
    report = verify_plan(plan, repo.assumptions, repo.products, repo.dishes)
    text = render_plan(plan, repo.assumptions, repo.products, repo.dishes, report)
    assert text.count("Przygotowanie całej ilości — wykonujesz raz") == 1
    assert text.count("Całość na 2 dni dla 2 osób — 4 porcje") == 1
    assert "1340 g" in text and "480 g" in text
    wednesday = text.split("## Środa", 1)[1].split("### Przekąski", 1)[0]
    assert "Dziś tylko odgrzewasz" in wednesday
    assert "Łącznie na wszystkie dni i osoby" not in wednesday


def test_no_batch_does_not_change_normal_recipe(repo):
    plan = example_plan()
    plan.meals[0].batch_group = None
    assert batch_recipe(plan, plan.meals[0], None, repo.products, repo.assumptions.persons) is None


def test_batch_sums_unequal_days_and_any_number_of_days(repo):
    plan = example_plan()
    extra = plan.meals[1].model_copy(deep=True)
    extra.day = 3
    extra.portions[0].items[0].grams = 120
    plan.meals.append(extra)
    plan.days.append(3)
    batch = batch_recipe(plan, plan.meals[0], None, repo.products, repo.assumptions.persons)
    assert batch["title"] == "Całość na 3 dni dla 2 osób — 6 porcji"
    assert next(i["grams"] for i in batch["items"] if i["product_id"] == "wolowina_gulaszowa") == 740
    assert batch["water"] is None


def test_batch_without_bread_does_not_mention_it_and_plural_is_polish(repo):
    plan = example_plan()
    for meal in plan.meals:
        for portion in meal.portions:
            portion.items = [i for i in portion.items if i.product_id != "chleb_mieszany"]
    extra = plan.meals[1].model_copy(deep=True)
    extra.day = 3
    plan.meals.append(extra)
    plan.days.append(3)
    batch = batch_recipe(plan, plan.meals[0], None, repo.products, repo.assumptions.persons)
    assert not batch["has_side"] and "Pieczywo" not in batch["instructions"]
    assert batch["portions_text"] == "6 porcji"
    report = verify_plan(plan, repo.assumptions, repo.products, repo.dishes)
    text = render_plan(plan, repo.assumptions, repo.products, repo.dishes, report)
    reheat = text.split("## Środa", 1)[1].split("## Czwartek", 1)[0]
    assert "(6 porcji)" in reheat and "Pieczywo" not in reheat
