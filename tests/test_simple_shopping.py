"""Uproszczona lista we wszystkich kanałach, bez zmian gramatur i wartości planu."""
from collections import Counter

import pytest

from dietbot.app.telegram_bot import format_shopping
from dietbot.domain.plan import PlanItem, PlannedSnack, WeekPlan
from dietbot.report.markdown import render_shopping_list
from dietbot.report.text import markdown_to_text
from dietbot.shopping import build_shopping_list


def test_variants_are_summed_and_spices_and_stores_removed(repo):
    a = repo.assumptions.model_copy(deep=True)
    a.day.fixed_items = []
    quantities = {"mozzarella": 100, "mozzarella_light": 135, "jogurt_naturalny_1_5": 123,
                  "jogurt_naturalny_3_5": 87, "jogurt_owocowy_light": 100, "feta": 125,
                  "feta_light": 100, "skyr_naturalny": 180, "mleko_1_5": 130,
                  "mleko_3_5": 230, "sol": 6, "pieprz_czarny": 4, "bazylia_swieza": 10,
                  "natka_pietruszki": 8, "cukier_waniliowy": 10, "piers_z_kurczaka": 320,
                  "serek_wiejski": 100, "serek_wiejski_lekki": 120}
    plan = WeekPlan(days=[0], meals=[], snacks=[PlannedSnack(day=0, person_id=a.persons[0].id,
                    dish_id="test", items=[PlanItem(product_id=p, grams=g) for p, g in quantities.items()])])
    before = plan.model_dump_json()
    rows = build_shopping_list(plan, a, repo.products, repo.store_products)
    by_name = {r.name: r for r in rows}
    assert len(rows) == len(by_name) == 7
    for name, grams in {"Mozzarella": 235, "Jogurt": 310, "Feta": 225, "Skyr": 180,
                        "Mleko": 360, "Serek wiejski": 220}.items():
        assert by_name[name].grams == grams
        assert "%" not in by_name[name].name and "light" not in by_name[name].name
    assert by_name["Mozzarella"].amount_text == "240 g"
    assert by_name["Feta"].source_product_ids == ("feta", "feta_light")
    assert all(r.store is None and r.packs is None for r in rows)
    assert all(not r.negligible and r.category.value != "przyprawy" for r in rows)
    assert plan.model_dump_json() == before
    for row in rows:
        assert row.grams == pytest.approx(sum(quantities[p] for p in row.source_product_ids))
    assert not any(n > 1 for n in Counter(p for r in rows for p in r.source_product_ids).values())
    md = render_shopping_list(plan, a, repo.products, repo.store_products)
    data = {"title": "Test", "groups": [{"label": "Zakupy", "items": [r.to_dict() for r in rows]}]}
    for text in (md, markdown_to_text(md), format_shopping(data)):
        assert "Lidl" not in text and "Kaufland" not in text
        assert "Pieprz" not in text and "Sól" not in text and "Bazylia" not in text
        assert text.count("Mozzarella") == 1 and text.count("Feta") == 1


def test_family_key_stays_stable_when_variant_changes(repo):
    a = repo.assumptions.model_copy(deep=True)
    a.day.fixed_items = []
    keys = []
    for pid in ("mozzarella", "mozzarella_light"):
        plan = WeekPlan(days=[0], meals=[], snacks=[PlannedSnack(day=0, person_id=a.persons[0].id,
                        dish_id="test", items=[PlanItem(product_id=pid, grams=100)])])
        keys.append(build_shopping_list(plan, a, repo.products)[0].product_id)
    assert keys == ["zakup_mozzarella", "zakup_mozzarella"]
