"""Grupy składników (ciasto, panierka, masa mielona): silnik zachowuje proporcje przepisu, weryfikator to sprawdza."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dietbot.checker import verify_plan
from dietbot.domain import DishTemplate, MealType
from dietbot.engine import BlockStatus, EngineRequest, solve_week

# dzień z trzema daniami z grupami: naleśniki na słodko, schabowy w panierce, a śniadanie zwykłe
DAY = [
    {"days": ["poniedzialek"], "meal": "sniadanie", "candidates": ["kanapki_z_szynka_jajkiem_i_pomidorem"]},
    {"days": ["poniedzialek"], "meal": "obiad", "candidates": ["kotlet_schabowy_z_ziemniakami_i_buraczkami"]},
    {"days": ["poniedzialek"], "meal": "kolacja", "candidates": ["nalesniki_z_twarogiem_i_miodem"]},
    {"days": ["wtorek"], "meal": "sniadanie", "candidates": ["serek_wiejski_z_rzodkiewka_i_ogorkiem"]},
    {"days": ["wtorek"], "meal": "obiad", "candidates": ["kotlety_mielone_z_ziemniakami_i_surowka_z_marchewki"]},
    {"days": ["wtorek"], "meal": "kolacja", "candidates": ["salatka_grecka_z_kurczakiem"]},
]


@pytest.fixture(scope="module")
def result(repo):
    return solve_week(EngineRequest.model_validate({"slots": DAY}), repo.assumptions, repo.products, repo.dishes)


def shares_in_plan(repo, meal):
    dish = repo.dishes[meal.dish_id]
    out = []
    for portion in meal.portions:
        grams = {i.product_id: i.grams for i in portion.items}
        for grp in dish.groups:
            recipe = grp.shares(dish.ingredients)
            total = sum(grams[p] for p in recipe)
            out.append((portion.person_id, grp.name, total, {p: (grams[p], recipe[p] * total) for p in recipe}))
    return out


def test_days_with_groups_are_within_bands_and_verified(repo, result):
    assert result.status is BlockStatus.OK, [b.message for b in result.blocks]
    engine_totals = [t.model_dump(mode="json") for b in result.blocks for t in b.totals]
    report = verify_plan(result.plan, repo.assumptions, repo.products, repo.dishes, engine_totals)
    assert report.ok, [i.message for i in report.errors()]
    assert "proporcje_grupy" not in report.codes()


def test_engine_keeps_recipe_proportions(repo, result):
    checked = 0
    for meal in result.plan.meals:
        for person, name, total, parts in shares_in_plan(repo, meal):
            assert total > 0, (meal.dish_id, person, name)
            for pid, (got, expected) in parts.items():
                # 5% względnie + 1 g kroku + zaokrąglenie
                assert abs(got - expected) <= 0.05 * expected + 1.15, (meal.dish_id, person, name, pid, got, expected)
                checked += 1
    assert checked >= 20


def test_batter_is_scaled_not_fixed(repo, result):
    """Silnik skaluje całą masę (mężczyzna dostaje więcej ciasta), ale proporcje zostają."""
    meal = next(m for m in result.plan.meals if m.dish_id == "nalesniki_z_twarogiem_i_miodem")
    batter = {person: total for person, name, total, _ in shares_in_plan(repo, meal) if name == "ciasto naleśnikowe"}
    assert batter["mezczyzna"] > batter["kobieta"] * 1.15


def test_checker_detects_broken_proportions(repo, result):
    plan = result.plan.model_copy(deep=True)
    meal = next(m for m in plan.meals if m.dish_id == "nalesniki_z_twarogiem_i_miodem")
    flour = next(i for i in meal.portion("kobieta").items if i.product_id == "maka_pszenna")
    flour.grams = round(flour.grams * 1.6, 1)
    report = verify_plan(plan, repo.assumptions, repo.products, repo.dishes)
    issues = [i for i in report.errors() if i.code == "proporcje_grupy"]
    assert issues and all(i.meal is MealType.SUPPER for i in issues)
    assert any("ciasto naleśnikowe" in i.message for i in issues)


def _dish(**overrides):
    data = {
        "id": "test_nalesniki", "nazwa": "Naleśniki testowe", "posilki": ["kolacja"], "status": "zatwierdzone",
        "skladniki": [
            {"produkt": "maka_pszenna", "rola": "weglowodany", "porcjowanie": "z_garnka", "typowo_g": 50},
            {"produkt": "mleko_1_5", "rola": "nabial", "porcjowanie": "z_garnka", "typowo_g": 120},
            {"produkt": "twarog_poltlusty", "rola": "bialko", "typowo_g": 150},
            {"produkt": "sol", "rola": "przyprawa", "typowo_g": 1},
        ],
        "grupy": [{"nazwa": "ciasto", "skladniki": ["maka_pszenna", "mleko_1_5"]}],
    }
    data.update(overrides)
    return DishTemplate.model_validate(data)


def test_group_validation():
    assert _dish().groups[0].shares(_dish().ingredients) == pytest.approx({"maka_pszenna": 50 / 170, "mleko_1_5": 120 / 170})
    with pytest.raises(ValidationError, match="nieobecnego składnika"):
        _dish(grupy=[{"nazwa": "ciasto", "skladniki": ["maka_pszenna", "jajko_kurze"]}])
    with pytest.raises(ValidationError, match="nie może być w grupie"):
        _dish(grupy=[{"nazwa": "ciasto", "skladniki": ["maka_pszenna", "sol"]}])
    with pytest.raises(ValidationError, match="to samo porcjowanie"):
        _dish(grupy=[{"nazwa": "farsz", "skladniki": ["maka_pszenna", "twarog_poltlusty"]}])
    with pytest.raises(ValidationError, match="w dwóch grupach"):
        _dish(grupy=[{"nazwa": "ciasto", "skladniki": ["maka_pszenna", "mleko_1_5"]},
                     {"nazwa": "sos", "skladniki": ["mleko_1_5", "maka_pszenna"]}])


def test_generator_builds_groups_from_llm_fields(repo):
    from dietbot.engine.settings import EngineSettings
    from dietbot.food.matcher import ProductMatcher
    from dietbot.llm.generate_dishes import GenDish, validate_generated

    gen = GenDish.model_validate({
        "id": "nalesniki_z_serkiem_wiejskim_i_pomidorem", "nazwa": "Naleśniki z serkiem wiejskim i pomidorem",
        "opis": "Cienkie naleśniki z serkiem wiejskim i pomidorem.", "posilki": ["kolacja"], "kuchnia": "polska",
        "tagi": ["na_cieplo"],
        "skladniki": [
            {"produkt": "maka_pszenna", "rola": "weglowodany", "porcjowanie": "z_garnka", "typowo_g": 50, "grupa": "Ciasto  naleśnikowe"},
            {"produkt": "mleko_1_5", "rola": "nabial", "porcjowanie": "z_garnka", "typowo_g": 120, "grupa": "ciasto naleśnikowe"},
            {"produkt": "jajko_kurze", "rola": "dodatek", "porcjowanie": "osobno", "typowo_g": 30, "grupa": "ciasto naleśnikowe"},
            {"produkt": "olej_rzepakowy", "rola": "tluszcz", "porcjowanie": "z_garnka", "typowo_g": 5, "grupa": "ciasto naleśnikowe"},
            {"produkt": "sol", "rola": "przyprawa", "typowo_g": 1, "grupa": "ciasto naleśnikowe"},
            {"produkt": "serek_wiejski", "rola": "bialko", "typowo_g": 200, "grupa": "farsz"},
            {"produkt": "pomidor", "rola": "warzywa", "typowo_g": 100},
        ],
        "przygotowanie": ["Zmiksuj ciasto i usmaż naleśniki.", "Nałóż serek i pomidora, zwiń."],
        "czas_min": 25,
    })
    dish, reasons = validate_generated(gen, repo.products, ProductMatcher(repo.products), repo.assumptions,
                                       repo.dishes, EngineSettings(), "test")
    assert dish is not None, reasons
    assert [(g.name, g.products) for g in dish.groups] == [
        ("ciasto naleśnikowe", ["maka_pszenna", "mleko_1_5", "jajko_kurze", "olej_rzepakowy"])]  # farsz z 1 składnikiem pominięty
    by_id = {i.product_id: i for i in dish.ingredients}
    assert by_id["jajko_kurze"].portioning.value == "z_garnka" and by_id["jajko_kurze"].unit is None
    assert DishTemplate.model_validate(dish.to_data()).groups == dish.groups  # zapis i odczyt z pliku


def test_generator_merges_repeated_ingredient(repo):
    from dietbot.engine.settings import EngineSettings
    from dietbot.food.matcher import ProductMatcher
    from dietbot.llm.generate_dishes import GenDish, validate_generated

    base = {
        "id": "pierogi_z_miesem_testowe", "nazwa": "Pierogi z mięsem i cebulką", "opis": "Domowe pierogi z mięsem.",
        "posilki": ["obiad"], "kuchnia": "polska", "tagi": ["na_cieplo"], "czas_min": 60,
        "przygotowanie": ["Zagnieć ciasto, nałóż farsz, zlep pierogi i ugotuj."],
    }
    ingredients = [
        {"produkt": "maka_pszenna", "rola": "weglowodany", "porcjowanie": "z_garnka", "typowo_g": 100, "grupa": "ciasto na pierogi"},
        {"produkt": "jajko_kurze", "rola": "dodatek", "porcjowanie": "z_garnka", "typowo_g": 20, "grupa": "ciasto na pierogi"},
        {"produkt": "mieso_mielone_wolowe_chude", "rola": "bialko", "porcjowanie": "z_garnka", "typowo_g": 130, "grupa": "farsz"},
        {"produkt": "cebula", "rola": "warzywa", "porcjowanie": "z_garnka", "typowo_g": 40, "grupa": "farsz"},
        {"produkt": "maka_pszenna", "rola": "dodatek", "porcjowanie": "z_garnka", "typowo_g": 10},  # do podsypania
    ]
    matcher, settings = ProductMatcher(repo.products), EngineSettings()
    dish, reasons = validate_generated(GenDish.model_validate({**base, "skladniki": ingredients}), repo.products,
                                       matcher, repo.assumptions, repo.dishes, settings, "test")
    assert dish is not None, reasons
    flour = next(i for i in dish.ingredients if i.product_id == "maka_pszenna")
    assert flour.typical_g == 110 and dish.group_of("maka_pszenna").name == "ciasto na pierogi"
    # duży drugi raz (np. mąka i do ciasta, i do sosu) — produkt wypada z masy, żeby nie psuć proporcji
    ingredients[-1]["typowo_g"] = 60
    dish, reasons = validate_generated(GenDish.model_validate({**base, "id": "pierogi_z_miesem_testowe_2", "skladniki": ingredients}),
                                 repo.products, matcher, repo.assumptions, repo.dishes, settings, "test")
    # Removing ambiguous flour from its group leaves the dough unprotected.
    # Such a recipe must now be corrected, not accepted with freely varying flour.
    assert dish is None and any("Brak chronionych proporcji" in r for r in reasons)


def test_generator_rejects_sweet_lunch(repo):
    from dietbot.engine.settings import EngineSettings
    from dietbot.food.matcher import ProductMatcher
    from dietbot.llm.generate_dishes import GenDish, validate_generated

    gen = GenDish.model_validate({
        "id": "leniwe_z_cukrem", "nazwa": "Leniwe z twarogiem", "opis": "Kluski leniwe z twarogiem.", "posilki": ["obiad"],
        "kuchnia": "polska", "tagi": [], "czas_min": 30, "przygotowanie": ["Zagnieć, pokrój i ugotuj kluski."],
        "skladniki": [
            {"produkt": "twarog_poltlusty", "rola": "bialko", "typowo_g": 220},
            {"produkt": "maka_pszenna", "rola": "weglowodany", "typowo_g": 80},
            {"produkt": "cukier", "rola": "dodatek", "typowo_g": 20},
        ]})
    dish, reasons = validate_generated(gen, repo.products, ProductMatcher(repo.products), repo.assumptions, repo.dishes,
                                       EngineSettings(), "test")
    assert dish is None and any("na słodko" in r for r in reasons)


def test_engine_audit_catches_breakfast_without_carbs(repo):
    from dietbot.planner.dish_audit import audit_dish

    ok = repo.dishes["nalesniki_z_twarogiem_i_miodem"]
    assert audit_dish(ok, repo.approved_dishes(), repo.products, repo.assumptions) == []
    omelet = DishTemplate.model_validate({
        "id": "omlet_bez_pieczywa_test", "nazwa": "Omlet z serem bez pieczywa", "posilki": ["sniadanie"],
        "status": "zatwierdzone",
        "skladniki": [{"produkt": "jajko_kurze", "rola": "bialko", "typowo_g": 150},
                      {"produkt": "gouda", "rola": "dodatek", "typowo_g": 40},
                      {"produkt": "maslo", "rola": "tluszcz", "typowo_g": 10}],
    })
    problems = audit_dish(omelet, repo.approved_dishes(), repo.products, repo.assumptions)
    assert problems and "silnik nie układa poprawnego dnia" in problems[0]


def test_generator_order_macro_rules():
    from dietbot.llm.generate_dishes import GenOrder

    order = GenOrder(MealType.BREAKFAST, "polska", 4, max_fat_share=0.32, min_carb_share=0.40)
    assert "tłuszcz najwyżej 32% energii" in order.macro_rule()
    fatty = {"protein": 30, "fat": 30, "carbs": 30}  # 270 z 510 kcal z tłuszczu
    problems = order.macro_problems(fatty)
    assert len(problems) == 2 and "za tłuste" in problems[0] and "za mało węglowodanów" in problems[1]
    assert order.macro_problems({"protein": 30, "fat": 12, "carbs": 70}) == []
    assert GenOrder(MealType.LUNCH, "polska", 2).macro_rule() is None


def test_generator_rejects_watery_soup(repo):
    from dietbot.engine.settings import EngineSettings
    from dietbot.food.matcher import ProductMatcher
    from dietbot.llm.generate_dishes import GenDish, validate_generated

    def soup(bulion: float, grupa: str | None, dish_id: str):
        broth = {"produkt": "bulion_drobiowy", "rola": "sos", "porcjowanie": "z_garnka", "typowo_g": bulion}
        if grupa:
            broth["grupa"] = grupa
        return GenDish.model_validate({
            "id": dish_id, "nazwa": "Zupa marchewkowa z kurczakiem", "opis": "Zupa z kurczakiem, marchewką i ziemniakami.",
            "posilki": ["obiad"], "kuchnia": "polska", "tagi": ["zupa", "na_zapas", "na_cieplo"], "na_ile_dni": 2,
            "czas_min": 45, "przygotowanie": ["Ugotuj kurczaka z marchewką w bulionie.", "Dodaj ziemniaki."],
            "skladniki": [
                {"produkt": "piers_z_kurczaka", "rola": "bialko", "porcjowanie": "z_garnka", "typowo_g": 170, "grupa": "wywar"},
                {"produkt": "ziemniaki", "rola": "weglowodany", "porcjowanie": "z_garnka", "typowo_g": 200, "grupa": "wywar"},
                {"produkt": "marchew", "rola": "warzywa", "porcjowanie": "z_garnka", "typowo_g": 150, "grupa": "wywar"},
                broth,
                {"produkt": "chleb_mieszany", "rola": "weglowodany", "typowo_g": 60},
            ]})

    matcher, settings = ProductMatcher(repo.products), EngineSettings()
    dish, reasons = validate_generated(soup(600, "wywar", "zupa_marchewkowa_wodnista"), repo.products, matcher,
                                       repo.assumptions, repo.dishes, settings, "test")
    text = " ".join(reasons)
    assert dish is None and "za dużo płynu" in text and "za duża porcja" in text and "grupie proporcji" in text
    dish, reasons = validate_generated(soup(250, None, "zupa_marchewkowa_gesta"), repo.products, matcher,
                                       repo.assumptions, repo.dishes, settings, "test")
    assert dish is not None, reasons
