"""Miary domowe (łyżki, kromki, sztuki) i lista zakupów z produktami z półki."""

from __future__ import annotations

import math

import pytest

from dietbot.domain.product import Product
from dietbot.measures import household, plural_form
from dietbot.report.markdown import fmt_amount, render_shopping_list
from dietbot.shopping import NOT_BOUGHT, build_shopping_list, totals_grams


def _product(measures: list[dict], **extra) -> Product:
    return Product.model_validate({
        "id": "test_produkt", "nazwa": "Produkt testowy", "kategoria": "tluszcze", "stan": "gotowy_do_jedzenia",
        "zrodlo": {"baza": "recznie", "uwagi": "test"},
        "na_100g": {"kcal": 884, "bialko": 0, "tluszcz": 100, "weglowodany": 0},
        "miary": measures, **extra,
    })


OIL = _product([{"jednostka": "łyżka", "gramy": 13}, {"jednostka": "łyżeczka", "gramy": 4.5}])


@pytest.mark.parametrize(("grams", "expected"), [
    (10, "¾ łyżki"),
    (13, "1 łyżka"),
    (26, "2 łyżki"),
    (65, "5 łyżek"),
    (4.5, "1 łyżeczka"),
    (8, "2 łyżeczki"),  # 0,6 łyżki nie pasuje do ćwiartek — wtedy łyżeczki
    (3, None),  # ⅔ łyżeczki: brak czytelnej miary, zostają same gramy
    (500, None),  # ponad 12 łyżek to już nie miara domowa
])
def test_spoons(grams, expected):
    assert household(OIL, grams) == expected


def test_pieces_slices_balls():
    egg = _product([{"jednostka": "szt", "gramy": 50}])
    assert household(egg, 100) == "2 szt."
    assert household(egg, 75) == "1½ szt."
    bread = _product([{"jednostka": "kromka", "gramy": 30}])
    assert household(bread, 90) == "3 kromki"
    assert household(bread, 150) == "5 kromek"
    assert household(bread, 15) == "½ kromki"
    ball = _product([{"jednostka": "kulka", "gramy": 125}])
    assert household(ball, 60) == "½ kulki"
    assert household(ball, 125) == "1 kulka"


@pytest.mark.parametrize(("n", "form"), [(1, "kromka"), (2, "kromki"), (4, "kromki"), (5, "kromek"), (12, "kromek"),
                                         (14, "kromek"), (22, "kromki"), (25, "kromek")])
def test_polish_plurals(n, form):
    assert plural_form("kromka", n) == form


def test_fmt_amount_spices_and_hints():
    salt = _product([], pomijalny=True)
    assert fmt_amount(salt, 1) == "szczypta"
    assert fmt_amount(salt, 3) == "do smaku"
    assert fmt_amount(OIL, 10) == "10 g (≈ ¾ łyżki)"
    assert fmt_amount(_product([]), 7.4) == "7 g"
    assert fmt_amount(_product([]), 2.5) == "2,5 g"


def test_real_measures_have_sources(repo):
    with_measures = [p for p in repo.products.values() if p.measures]
    assert len(with_measures) >= 25
    for p in with_measures:
        for m in p.measures:
            assert m.source, f"{p.id}: miara '{m.unit}' bez źródła"


def test_shopping_list_totals_and_stores(repo, week_result):
    plan = week_result.plan
    a = repo.assumptions
    totals = totals_grams(plan, a)
    rows = build_shopping_list(plan, a, repo.products, repo.store_products)
    by_id = {r.product_id: r for r in rows}

    # stałe pozycje: mleko do kawy dla obu osób na każdy dzień
    milk = next(fx for fx in a.day.fixed_items if fx.product_id.startswith("mleko"))
    assert totals[milk.product_id] >= milk.grams * len(plan.days) * len(a.persons)
    # suma listy = suma planu (nic nie ginie po drodze poza wodą i naparem)
    for pid, grams in totals.items():
        if pid in NOT_BOUGHT or grams <= 0:
            assert pid not in by_id
        else:
            assert by_id[pid].grams == pytest.approx(grams)
    # podpowiedź sklepu i liczba opakowań wystarcza na potrzebną ilość (z 2% luzu)
    with_store = [r for r in rows if r.store is not None and r.store.package_g]
    assert with_store, "brak podpowiedzi sklepowych na liście zakupów"
    for r in with_store:
        assert r.store.preferred and r.store.product_id == r.product_id
        assert r.packs >= 1 and r.packs * r.store.package_g >= r.grams * 0.98
        assert r.packs == max(1, math.ceil(r.grams / r.store.package_g - 0.02))
    # płyny w ml/l, a nie w gramach
    liquids = [r for r in rows if repo.products[r.product_id].density_g_per_ml and not r.negligible]
    for r in liquids:
        assert r.amount_text.endswith(("ml", " l")), r.amount_text

    md = render_shopping_list(plan, a, repo.products, repo.store_products)
    assert md.startswith("# Lista zakupów")
    assert "🛒" in md
    for r in rows:
        assert r.name in md


def test_produce_pieces_and_spoon_cap():
    apple = _product([{"jednostka": "szt", "gramy": 182}], kategoria="owoce")
    assert household(apple, 230) == "1½ szt."  # owoce różnią się wielkością — większa tolerancja
    onion = _product([{"jednostka": "szt", "gramy": 110}], kategoria="warzywa")
    assert household(onion, 80) == "1 szt."  # bliżej 1 niż ½
    assert household(OIL, 13 * 8) is None  # 8 łyżek to już nie miara domowa
    cheese = _product([{"jednostka": "szt", "gramy": 182}], kategoria="sery")
    assert household(cheese, 230) is None  # poza owocami i warzywami zwykła tolerancja


def test_takeaway_food_is_a_separate_ordering_group(repo):
    from dietbot.engine import EngineRequest, solve_week
    from dietbot.report.markdown import render_shopping_list
    from dietbot.shopping import build_shopping_list

    slots = [{"days": [0], "meal": "sniadanie", "candidates": ["kanapki_z_szynka_jajkiem_i_pomidorem"]},
             {"days": [0], "meal": "obiad", "candidates": ["pizza_margherita_zamowiona"]},
             {"days": [0], "meal": "kolacja", "candidates": ["salatka_grecka_z_kurczakiem"]}]
    res = solve_week(EngineRequest.model_validate({"slots": slots}), repo.assumptions, repo.products, repo.dishes)
    rows = build_shopping_list(res.plan, repo.assumptions, repo.products, repo.store_products)
    pizza = next(r for r in rows if r.product_id == "pizza_margherita_z_pizzerii")
    assert pizza.takeaway and pizza.group == "na_wynos" and pizza.store is None
    assert "kawałk" in pizza.amount_text and rows[-1].takeaway
    assert not any(r.takeaway for r in rows if r.product_id != "pizza_margherita_z_pizzerii")
    md = render_shopping_list(res.plan, repo.assumptions, repo.products, repo.store_products)
    assert "## Na wynos — do zamówienia" in md and md.index("## Na wynos") > md.index("## Pieczywo")
