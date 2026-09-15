"""Dopasowanie nazw produktów — przypadki z ręcznego przeglądu jadłospisów użytkownika (13.09.2026)."""

from __future__ import annotations

import pytest

from dietbot.food.matcher import MatchStatus, ProductMatcher


@pytest.fixture(scope="module")
def matcher(repo):
    return ProductMatcher(repo.products)


SURE = [
    ("Bułka (100 g)", "bulka_pszenna"),
    ("Jajka (100 g - 2 szt.)", "jajko_kurze"),
    ("Jajko do panierki (20 g)", "jajko_kurze"),
    ("Jajka ugotowane i rozgniecione (100 g)", "jajko_kurze"),
    ("Oliwa do smażenia (5 g)", "oliwa_z_oliwek"),
    ("Pomidor obok (100 g)", "pomidor"),
    ("Makaron (70 g suchego)", "makaron_pszenny"),
    ("Ryż biały (50 g suchego)", "ryz_bialy"),
    ("Szynka z piersi kurczaka/indyka (50 g)", "wedlina_z_indyka"),
    ("Szynka z piersi kurczaka (30 g)", "wedlina_z_indyka"),
    ("Ser Parmezan/Grana Padano (15 g)", "parmezan"),
    ("pomidorów", "pomidor"),
    ("piersi z kurczaka", "piers_z_kurczaka"),
    ("mleko 3,2%", "mleko_3_5"),
    ("mleko 1.5%", "mleko_1_5"),
    ("losos wedzony", "losos_wedzony"),
    ("brokuly", "brokul"),
    # warianty mają własne produkty (z etykiet) — i nie przejmują nazw zwykłych produktów
    ("Ser Feta light (30 g)", "feta_light"),
    ("Ser feta (30 g)", "feta"),
    ("Majonez light (15 g)", "majonez_lekki"),
    ("Majonez (15 g)", "majonez"),
    ("Chuda wołowina mielona 5% (100 g)", "mieso_mielone_wolowe_chude"),
    ("Twaróg chudy (100 g)", "twarog_chudy"),
    ("Odżywka białkowa WPC (30 g)", "odzywka_bialkowa_wpc"),
    ("Pierogi ruskie z dobrym składem (200 g)", "pierogi_ruskie"),
    ("Włoszczyzna (100 g)", "wloszczyzna"),
]

NOT_SURE = [
    # wariant zmieniający wartości — nie wolno pewnie przypisać zwykłego produktu
    "Mleczko kokosowe light (100 g)",
    "Jogurt naturalny gęsty (150 g)",
    "jogurt naturalny gesty",
    "jogurt grecki 10%",
    "mleko 2%",
    # alternatywy wskazują różne produkty / kilka produktów w jednej pozycji
    "Serek wiejski lekki/zwykły (100 g)",
    "Rzodkiewka i ogórek (100 g)",
    # waga po ugotowaniu przy produkcie suchym
    "ryż (150 g po ugotowaniu)",
    # brak w bazie
    "Pudding proteinowy czekoladowy (200 g)",
    "Ulubione warzywa (150 g)",
]


@pytest.mark.parametrize(("query", "expected"), SURE)
def test_sure_matches(matcher, query, expected):
    r = matcher.match(query)
    assert r.status is MatchStatus.SURE, (query, r.candidates[:3], r.warnings)
    assert r.product_id == expected


@pytest.mark.parametrize("query", NOT_SURE)
def test_not_sure_is_never_silently_assigned(matcher, query):
    r = matcher.match(query)
    assert r.status is not MatchStatus.SURE, (query, r.candidates[:3])
    assert r.product_id is None


def test_grams_and_pieces_parsed(matcher):
    r = matcher.match("Jajka (150 g - ok. 3 sztuki)")
    assert r.grams == 150 and r.pieces == 3


def test_unsure_keeps_useful_candidates(matcher):
    r = matcher.match("Jogurt typu Skyr naturalny (150 g)")
    assert r.status is MatchStatus.UNSURE
    assert "skyr_naturalny" in [c.product_id for c in r.candidates[:2]]
    r = matcher.match("Masło do podsmażenia cebulki (10 g)")
    assert r.candidates[0].product_id == "maslo"
