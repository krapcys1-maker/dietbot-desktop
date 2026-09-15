"""Konkretne dania z życzeń: odczyt, dopasowanie do biblioteki, przypięcie do planu (w podanym dniu), wymyślanie brakujących."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dietbot.domain import MealType, Weekday
from dietbot.llm.client import LLMResult
from dietbot.planner.autofix import autofix_composition
from dietbot.planner.compose import SlotPlan, compose_fallback, validate_composition, wish_errors
from dietbot.planner.pools import build_pools
from dietbot.planner.wishes import (
    RequestedDish,
    WishDish,
    WishExclusions,
    extract_exclusions,
    is_snack_request,
    request_match_problem,
    resolve_requested_dishes,
    shortlist_dishes,
)

ROOT = Path(__file__).resolve().parents[1]
DAYS = list(Weekday)
CUTLET = "kotlet_z_kurczaka_w_panierce_z_ziemniakami_i_surowka_z_kapusty_pekinskiej"
TOMATO = "zupa_pomidorowa_z_makaronem_i_kurczakiem"
ICE_CREAM = "przekaska20_lody_waniliowe"
USER_WISH = ("w 2 dni zupa moze byc pomidporowa chce w jeden dzien schabopwego ale z piersci kurczaka ziemniaki "
             "i surowka reszte dni wymysl")


class FakeLLM:
    def __init__(self, values: dict):
        self.values = values
        self.calls: list[tuple[str, str]] = []

    class settings:  # noqa: N801 — udaje LLMSettings (tylko nazwa modelu)
        model = "udawany"

    def complete_json(self, task, system, user, schema, **kw):
        self.calls.append((task, user))
        value = self.values[task] if not callable(self.values[task]) else self.values[task](user)
        return LLMResult(value=schema.model_validate(value), attempts=1, cost_usd=0.001, prompt_tokens=0,
                         completion_tokens=0, seconds=0.0)


@pytest.fixture(scope="module")
def setup(repo):
    dishes = repo.approved_dishes()
    return repo.assumptions, dishes, build_pools(dishes, repo.products, repo.assumptions)


def test_user_wish_is_read_as_concrete_dishes(repo):
    fake = FakeLLM({"zyczenia_wykluczenia": {"dania": [
        {"opis": "zupa pomidorowa", "posilek": "obiad", "zupa_na_zapas": True},
        {"opis": "kotlet schabowy z piersi kurczaka z ziemniakami i surówką", "posilek": "obiad", "kuchnia": "polska"}]}})
    excl = extract_exclusions(fake, USER_WISH, repo.products, repo.assumptions.cuisines)
    assert [d.opis for d in excl.dishes] == ["zupa pomidorowa", "kotlet schabowy z piersi kurczaka z ziemniakami i surówką"]
    assert excl.dishes[0].zupa_na_zapas and not excl.product_ids and not excl.cuisine_counts
    assert USER_WISH in fake.calls[0][1]


def test_shortlist_puts_the_real_dishes_on_top(repo, setup):
    _, dishes, pools = setup
    soup = shortlist_dishes(WishDish(opis="zupa pomidorowa", zupa_na_zapas=True), pools, dishes, repo.products, k=5)
    assert soup and all(dishes[d].name.startswith("Zupa pomidorowa") for d, _ in soup)
    assert "krem_pomidorowy_z_soczewica_i_kurczakiem" not in [d for d, _ in soup]
    cutlet = shortlist_dishes(WishDish(opis="kotlet schabowy z piersi kurczaka z ziemniakami i surówką", posilek="obiad"),
                              pools, dishes, repo.products, k=3)
    assert cutlet[0][0] == CUTLET
    assert "schab_patelnia_pieczarki_cebula_ziemniaki" not in [d for d, _ in cutlet]


def test_model_picks_from_candidates_and_unknown_ids_are_ignored(repo, setup):
    _, dishes, pools = setup
    requests = [WishDish(opis="zupa pomidorowa", zupa_na_zapas=True),
                WishDish(opis="schabowy z piersi kurczaka z ziemniakami i surówką", posilek="obiad", dni=["piątek"]),
                WishDish(opis="naleśniki z dżemem truskawkowym", posilek="kolacja")]
    fake = FakeLLM({"zyczenia_dania": {"wybory": [{"nr": 1, "danie": TOMATO}, {"nr": 2, "danie": CUTLET},
                                                   {"nr": 3, "danie": "nie_ma_takiego_dania"}]}})
    resolved, cost = resolve_requested_dishes(fake, requests, pools, dishes, repo.products)
    assert [r.dish_id for r in resolved] == [TOMATO, CUTLET, None]
    assert resolved[0].meal is MealType.LUNCH and resolved[1].days == [Weekday.FRIDAY] and cost > 0
    prompt = fake.calls[0][1]
    assert TOMATO in prompt and CUTLET in prompt  # model widzi właściwych kandydatów


def test_without_model_only_clear_name_matches_are_used(repo, setup):
    _, dishes, pools = setup
    resolved, _ = resolve_requested_dishes(None, [WishDish(opis="zupa ogórkowa", zupa_na_zapas=True),
                                                  WishDish(opis="jajka na miękko z pieczywem", posilek="sniadanie"),
                                                  WishDish(opis="pierogi ruskie z cebulką", posilek="obiad")],
                                           pools, dishes, repo.products)
    assert resolved[0].dish_id and dishes[resolved[0].dish_id].name.startswith("Zupa ogórkowa")
    assert resolved[1].dish_id == "jajka_na_miekko_z_pieczywem_i_szynka"  # nie „jajka w koszulkach”
    assert resolved[2].dish_id is None  # „pierogi z mięsem” to nie ruskie


def test_autofix_moves_a_requested_dish_to_its_day(setup):
    a, dishes, pools = setup
    comp = compose_fallback(pools, dishes, a, DAYS, seed=21)
    monday = next(s for s in comp.slots if [int(d) for d in s.days] == [0] and s.meal is MealType.LUNCH)
    for s in comp.slots:
        if s.dish_id == CUTLET:
            s.dish_id = monday.dish_id
    monday.dish_id = CUTLET
    pins = {CUTLET: [Weekday.FRIDAY]}
    errors = wish_errors(comp, pools, dishes, a, DAYS, must_use={CUTLET}, pins=pins)
    assert any("piątek" in e for e in errors)
    autofix_composition(comp, pools, dishes, a, DAYS, must_use={CUTLET}, pins=pins)
    friday = next(s for s in comp.slots if [int(d) for d in s.days] == [4] and s.meal is MealType.LUNCH)
    assert friday.dish_id == CUTLET
    assert wish_errors(comp, pools, dishes, a, DAYS, must_use={CUTLET}, pins=pins) == []


def test_planner_puts_requested_dishes_in_the_plan(repo):
    from dietbot.planner.pipeline import WeekPlanner

    wish = WishExclusions(
        dishes=[WishDish(opis="zupa pomidorowa", zupa_na_zapas=True),
                WishDish(opis="schabowy z piersi kurczaka", posilek="obiad", dni=["piatek"])],
        requested=[RequestedDish(request=WishDish(opis="zupa pomidorowa", zupa_na_zapas=True), dish_id=TOMATO,
                                 meal=MealType.LUNCH),
                   RequestedDish(request=WishDish(opis="schabowy z piersi kurczaka", posilek="obiad", dni=["piatek"]),
                                 dish_id=CUTLET, meal=MealType.LUNCH, days=[Weekday.FRIDAY]),
                   RequestedDish(request=WishDish(opis="pierogi ruskie", posilek="obiad"), meal=MealType.LUNCH)])
    out = WeekPlanner(repo, None).plan_week(seed=5, wish_plan=wish)
    meals = {(int(m.day), m.meal.value): m.dish_id for m in out.plan.meals}
    assert meals[(1, "obiad")] == TOMATO and meals[(2, "obiad")] == TOMATO
    assert meals[(4, "obiad")] == CUTLET
    assert [r["danie"] for r in out.requested] == [TOMATO, CUTLET, None]
    assert any(line.startswith("Z życzeń: „schabowy z piersi kurczaka”") for line in out.log)
    # danie, którego nie ma w bibliotece, zostaje w uwagach planu — domownicy widzą, czemu go nie ma
    assert [i.message for i in out.report.issues if i.code == "zyczenie_pominiete"] == [
        "Danie z życzeń „pierogi ruskie”: nie znalazłem go w bibliotece — model układający tydzień dostał życzenie "
        "jako tekst"]
    assert out.ok


def test_service_invents_a_requested_dish_missing_from_the_library(tmp_path, repo, monkeypatch):
    from dietbot.app.service import DietService
    from dietbot.llm import generate_dishes
    from dietbot.storage.paths import Paths

    for part in ("dane/produkty", "dane/dania", "dane/sklepy", "konfiguracja"):
        shutil.copytree(ROOT / part, tmp_path / part)
    (tmp_path / "wyniki").mkdir()
    service = DietService(Paths(tmp_path), use_llm=False)
    base = service.repo.dishes["jajka_w_koszulkach_z_szynka_i_pieczywem"]
    soft_eggs = base.model_copy(update={"id": "jajka_na_miekko_z_pieczywem_test", "name": "Jajka na miękko z pieczywem"})
    orders = []

    def fake_generation(repo, client, plan, **kw):
        orders.extend(plan)
        return [soft_eggs], [], 0.002

    monkeypatch.setattr(generate_dishes, "run_generation_plan", fake_generation)
    fake = FakeLLM({
        "zyczenia_wykluczenia": {"dania": [{"opis": "jajka na miękko z pieczywem", "posilek": "sniadanie", "dni": ["sobota"]}]},
        "zyczenia_dania": {"wybory": [{"nr": 1, "danie": None}]},
    })
    progress: list[str] = []
    excl = service._resolve_wishes(fake, "w sobotę jajka na miękko", progress.append)  # noqa: SLF001
    assert excl.requested[0].dish_id == soft_eggs.id and excl.requested[0].invented
    assert excl.requested[0].days == [Weekday.SATURDAY]
    assert orders and orders[0].meal is MealType.BREAKFAST and "jajka na miękko" in orders[0].special
    assert soft_eggs.id in service.repo.dishes  # zapisane w bibliotece i wczytane
    assert any("model je wymyśla" in line for line in progress)
    assert orders[0].name_check("Kotlet schabowy z ziemniakami") and orders[0].name_check(soft_eggs.name) is None


def test_service_rejects_an_invented_dish_that_is_not_the_wish_and_never_invents_snacks(tmp_path, repo, monkeypatch):
    from dietbot.app.service import DietService
    from dietbot.llm import generate_dishes
    from dietbot.storage.paths import Paths

    for part in ("dane/produkty", "dane/dania", "dane/sklepy", "konfiguracja"):
        shutil.copytree(ROOT / part, tmp_path / part)
    (tmp_path / "wyniki").mkdir()
    service = DietService(Paths(tmp_path), use_llm=False)
    wrong = service.repo.dishes[CUTLET].model_copy(update={"id": "kotlet_testowy", "name": "Kotlet schabowy z ziemniakami"})
    orders = []

    def fake_generation(repo, client, plan, **kw):
        orders.extend(plan)
        return [wrong], [], 0.002

    monkeypatch.setattr(generate_dishes, "run_generation_plan", fake_generation)
    fake = FakeLLM({
        "zyczenia_wykluczenia": {"dania": [{"opis": "pierogi ruskie z cebulką", "posilek": "obiad"},
                                           {"opis": "lody", "posilek": "przekaska", "dni": ["sobota"]}]},
        "zyczenia_dania": {"wybory": [{"nr": 1, "danie": None}, {"nr": 2, "danie": None}]},
    })
    progress: list[str] = []
    excl = service._resolve_wishes(fake, "pierogi ruskie, w sobotę lody", progress.append)  # noqa: SLF001
    assert [r.dish_id for r in excl.requested] == [None, None]
    # dwie próby dla pierogów (propozycja z inną nazwą odpada), lodów nie wymyślamy jako dania
    assert len(orders) == 2 and all("pierogi ruskie" in o.special for o in orders)
    assert "kotlet_testowy" not in service.repo.dishes
    assert any("próbuję jeszcze raz" in line for line in progress)
    assert any("Nie udało się wymyślić dania „pierogi ruskie z cebulką”" in line for line in progress)
    assert any("przekąski „lody”" in line for line in progress)


def test_snack_wishes_match_treat_snacks_and_take_the_snack_slot(repo, setup):
    _, dishes, pools = setup
    assert is_snack_request(WishDish(opis="lody", dni=["sobota"]))
    assert is_snack_request(WishDish(opis="popcorn do filmu", posilek="przekaska"))
    assert not is_snack_request(WishDish(opis="czekoladowe naleśniki z bananem"))
    assert not is_snack_request(WishDish(opis="zupa pomidorowa", zupa_na_zapas=True))
    requests = [WishDish(opis="lody", dni=["sobota"]), WishDish(opis="chipsy", posilek="przekaska")]
    resolved, _ = resolve_requested_dishes(None, requests, pools, dishes, repo.products)
    assert dishes[resolved[0].dish_id].name.startswith("Lody") and resolved[0].meal is MealType.SNACK
    assert resolved[0].days == [Weekday.SATURDAY] and resolved[0].snack and not resolved[0].batch
    assert resolved[1].dish_id == "przekaska20_chipsy" and resolved[1].meal is MealType.SNACK
    # z przekąską z życzeń model dostaje tylko przekąski z puli 20% — nie obiady
    fake = FakeLLM({"zyczenia_dania": {"wybory": [{"nr": 1, "danie": ICE_CREAM}]}})
    resolved, _ = resolve_requested_dishes(fake, [WishDish(opis="lody", posilek="przekaska")], pools, dishes, repo.products)
    assert resolved[0].dish_id == ICE_CREAM and set(resolved[0].candidates) <= set(pools.treat_snacks)


@pytest.mark.parametrize("seed", [3, 4, 21])
def test_autofix_puts_a_requested_treat_snack_on_its_day(setup, seed):
    a, dishes, pools = setup
    comp = compose_fallback(pools, dishes, a, DAYS, seed=seed)
    pins = {ICE_CREAM: [Weekday.SATURDAY]}
    assert wish_errors(comp, pools, dishes, a, DAYS, must_use={ICE_CREAM}, pins=pins)
    autofix_composition(comp, pools, dishes, a, DAYS, must_use={ICE_CREAM}, pins=pins)
    assert [(int(s.days[0]), s.meal) for s in comp.slots if s.dish_id == ICE_CREAM] == [(5, MealType.SNACK)]
    assert validate_composition(comp, pools, dishes, a, DAYS) == []  # w sobotę nic innego z puli 20%, pula tygodnia OK
    assert wish_errors(comp, pools, dishes, a, DAYS, must_use={ICE_CREAM}, pins=pins) == []


def test_autofix_moves_a_requested_snack_to_its_day(setup):
    a, dishes, pools = setup
    comp = compose_fallback(pools, dishes, a, DAYS, seed=21)
    comp.slots = [s for s in comp.slots if s.meal is not MealType.SNACK]
    comp.slots.append(SlotPlan(days=[Weekday.MONDAY], meal=MealType.SNACK, dish_id=ICE_CREAM))
    pins = {ICE_CREAM: [Weekday.SATURDAY]}
    autofix_composition(comp, pools, dishes, a, DAYS, must_use={ICE_CREAM}, pins=pins)
    assert [int(s.days[0]) for s in comp.slots if s.dish_id == ICE_CREAM] == [5]
    assert validate_composition(comp, pools, dishes, a, DAYS) == []


def test_invented_dish_name_must_match_the_wish():
    assert request_match_problem("lody", "Kotlet schabowy z ziemniakami i surówką z buraków")
    assert request_match_problem("zupa ogórkowa", "Zupa pomidorowa z ryżem")
    assert request_match_problem("kotlet schabowy z piersi kurczaka z ziemniakami i surówką",
                                 "Kotlet z piersi kurczaka w panierce z ziemniakami i surówką") is None
    assert request_match_problem("prawdziwa zupa pomidorowa na zapas", "Zupa pomidorowa z makaronem na rosole") is None
    assert request_match_problem("kurczak curry z ryżem", "Curry z kurczakiem i ryżem jaśminowym") is None
    assert request_match_problem("pizza bez cebuli", "Pizza margherita z bazylią") is None
