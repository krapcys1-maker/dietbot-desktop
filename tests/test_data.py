"""Kontrola danych: produkty, dania, założenia, zgodność z BLS."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dietbot.domain import Nutrients, Product
from dietbot.domain.assumptions import Band
from dietbot.food.bls import BlsIndex
from dietbot.food.refresh import bls_nutrients
from dietbot.textnorm import normalize


def test_repository_consistent(repo):
    report = repo.check()
    assert report.errors == []


def test_product_count_and_sources(repo):
    assert len(repo.products) >= 150
    for p in repo.products.values():
        assert p.source.database is not None
        if p.source.database.value == "BLS":
            assert p.source.code and p.source.version and p.source.original_name


def test_energy_consistent_with_eu_factors(repo):
    """kcal z tabeli zgodne z makro wg współczynników UE (z kwasami organicznymi i poliolami) — max 3%."""
    bad = {}
    for p in repo.products.values():
        n = p.per_100g
        if p.negligible:
            continue
        diff = abs(n.kcal - n.label_energy_from_macros())
        if n.kcal_mismatch() > 0.03 and diff > 1.0:  # przy produktach prawie bez energii liczy się różnica bezwzględna
            bad[p.id] = (round(n.kcal_mismatch(), 3), round(diff, 1))
    assert bad == {}


def test_values_match_bls_index(repo):
    """Wartości w YAML są dokładnie takie jak w BLS (nikt nie przepisał liczb ręcznie z błędem)."""
    index_path = repo.paths.bls_index
    if not index_path.exists():
        pytest.skip("brak lokalnego indeksu BLS (dane/raw jest poza gitem)")
    index = BlsIndex.load(index_path)
    for p in repo.products.values():
        if p.source.database.value != "BLS":
            continue
        expected = Nutrients.model_validate(bls_nutrients(index, p.source.code))
        assert p.per_100g == expected, p.id


def test_aliases_unique_after_normalization(repo):
    owner = {}
    for p in repo.products.values():
        for name in [p.name, *p.aliases]:
            key = normalize(name)
            assert owner.get(key, p.id) == p.id, f"alias '{name}' w {p.id} i {owner.get(key)}"
            owner[key] = p.id


def test_alias_split_by_yaml_comma_is_rejected():
    base = dict(id="x_y", nazwa="Mleko", kategoria="nabial", stan="gotowy_do_jedzenia",
                na_100g={"kcal": 44, "bialko": 3.5, "tluszcz": 1.5, "weglowodany": 4.8},
                zrodlo={"baza": "BLS", "kod": "M111200"})
    with pytest.raises(ValidationError):
        Product.model_validate({**base, "aliasy": ["lapte 1", "5%"]})


def test_nutrients_reject_impossible_values():
    with pytest.raises(ValidationError):
        Nutrients.model_validate({"kcal": 400, "bialko": 60, "tluszcz": 30, "weglowodany": 30})
    with pytest.raises(ValidationError):
        Nutrients.model_validate({"kcal": 100, "bialko": 5, "tluszcz": 2, "weglowodany": 10, "cukry": 15})


def test_band_validation():
    with pytest.raises(ValidationError):
        Band.model_validate({"min": 100, "max": 90})
    with pytest.raises(ValidationError):
        Band.model_validate({"min": 90, "max": 100, "cel": 120})


def test_dishes_reference_existing_products_and_measures(repo):
    for d in repo.dishes.values():
        for ing in d.ingredients:
            assert ing.product_id in repo.products, (d.id, ing.product_id)
            if ing.unit:
                assert repo.products[ing.product_id].measure(ing.unit) is not None


def test_egg_measure_has_source(repo):
    egg = repo.products["jajko_kurze"].measure("szt")
    assert egg is not None and 45 <= egg.grams <= 60 and egg.source


def test_assumptions_match_user_rules(repo):
    a = repo.assumptions
    w, m = a.person("kobieta"), a.person("mezczyzna")
    assert (w.targets.kcal.min, w.targets.kcal.max) == (1600, 1700)
    assert (m.targets.kcal.min, m.targets.kcal.max) == (2350, 2400)
    assert (w.protein_anchor.min, w.protein_anchor.max) == (30, 40)
    assert (m.protein_anchor.min, m.protein_anchor.max) == (45, 55)
    assert a.day.snacks.max_per_person_per_day == 1
    assert "rukola" in a.prohibitions.products and "platki_owsiane" in a.prohibitions.products
    assert "kasze" not in a.prohibitions.allowed_carb_categories
    assert a.week.sweet_suppers_max == 2
    assert (a.week.treat_meals.min, a.week.treat_meals.max) == (2, 3)


def test_plate_density_warning_for_high_energy_targets(repo):
    from dietbot.domain.assumptions import Assumptions

    data = repo.assumptions.model_dump(mode="json", by_alias=True)
    assert not any("kcal na gram" in w for w in repo.assumptions.consistency_warnings())
    man = next(p for p in data["osoby"] if p["id"] == "mezczyzna")
    man["cele_dzienne"]["kcal"] = {"min": 3100, "cel": 3150, "max": 3200}
    man["cele_dzienne"]["weglowodany"] = {"min": 400, "cel": 420, "max": 440}
    warnings = Assumptions.model_validate(data).consistency_warnings()
    assert any("Mężczyzna: 3150 kcal przy limitach talerza razem 2130 g" in w for w in warnings)
