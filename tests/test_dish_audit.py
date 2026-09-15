"""Test silnika dla nowych dań: stałe zestawy towarzyszy, potem losowe, zbilansowane dni (6 z 10)."""

from __future__ import annotations

import threading

import pytest

from dietbot.domain import DishTemplate, MealType, Weekday
from dietbot.engine import BlockReport, BlockStatus, EngineRequest, solve_week
from dietbot.engine.types import MassViolation
from dietbot.planner import dish_audit as da


def block(status: BlockStatus, solver_status: str = "OPTIMAL", objective: float | None = 100.0, mass=None) -> BlockReport:
    return BlockReport(days=[Weekday.MONDAY], status=status, solver_status=solver_status, objective=objective,
                       wall_ms=10.0, deterministic_time=0.5, mass_violations=mass or [])


def plate(grams: float, cap: float) -> MassViolation:
    return MassViolation(days=[Weekday.MONDAY], meal=MealType.LUNCH, dish_id="x", person_id="mezczyzna",
                         grams=grams, cap=cap)


def test_small_plate_excess_does_not_fail_a_sampled_day():
    assert da._acceptable(block(BlockStatus.OK))
    assert da._acceptable(block(BlockStatus.SOFT, mass=[plate(840, 820)]))  # +2,4%
    assert not da._acceptable(block(BlockStatus.SOFT, mass=[plate(900, 820)]))  # +9,8%
    assert not da._acceptable(block(BlockStatus.HARD))


@pytest.mark.parametrize(("pattern", "passes"), [
    ("OOOOOO", True),          # rozstrzygnięte po pierwszej fali
    ("OXOXOOOXXO", True),      # 6 z 10
    ("OXOXOOXXXO", False),     # 5 z 10
    ("XXXXX", False),          # 5 porażek — 6 dobrych dni już niemożliwe
])
def test_sampled_stage_needs_six_good_days_of_ten(repo, monkeypatch, pattern, passes):
    dish = repo.dishes["salatka_grecka_z_kurczakiem"]
    outcomes = iter(pattern)
    calls = []

    def fake_solve(d, meal, days, library, products, a, settings):
        if all(day in da.COMPANIONS for day in days):
            return block(BlockStatus.OK)
        assert len(days) == 1
        calls.append(days[0])
        return block(BlockStatus.OK if next(outcomes) == "O" else BlockStatus.HARD)

    monkeypatch.setattr(da, "_solve_days", fake_solve)
    partners = da.partner_pool(repo.approved_dishes(), repo.products, repo.assumptions)
    problems = da.audit_dish(dish, repo.approved_dishes(), repo.products, repo.assumptions, partners=partners)
    assert (problems == []) is passes
    if not passes:
        assert "dni z losowymi, dobrze dobranymi posiłkami" in problems[0]
    # towarzysze to inne posiłki z biblioteki, bez puli 20% i dań na zapas
    for others in calls:
        assert set(others) == {MealType.BREAKFAST, MealType.LUNCH}
        assert all(not repo.dishes[x].is_treat and not repo.dishes[x].is_batch for x in others.values())


def test_waves_give_the_same_verdicts_as_one_by_one(repo, monkeypatch):
    names = ["salatka_grecka_z_kurczakiem", "wrap_z_kurczakiem_i_warzywami", "kanapki_z_szynka_jajkiem_i_pomidorem"]
    dishes = [repo.dishes[n] for n in names]
    patterns = {names[0]: "OXOXOOOXXO", names[1]: "OXOXOOXXXO", names[2]: "OOOOOOXXXX"}
    counters = {n: 0 for n in names}
    lock = threading.Lock()

    def fake_solve(d, meal, days, library, products, a, settings):
        if all(day in da.COMPANIONS for day in days):
            return block(BlockStatus.OK)
        with lock:  # w fali dni liczą się równolegle — liczy się, ile wyników z wzoru przypadło na falę
            k = counters[d.id]
            counters[d.id] += 1
        return block(BlockStatus.OK if patterns[d.id][k] == "O" else BlockStatus.SOFT, mass=[plate(1000, 820)])

    monkeypatch.setattr(da, "_solve_days", fake_solve)
    passed, failed = da.audit_dishes(dishes, repo.approved_dishes(), repo.products, repo.assumptions, workers=2)
    assert {d.id for d in passed} == {names[0], names[2]}
    assert [d.id for d, _ in failed] == [names[1]]
    assert counters[names[2]] == 6  # zaliczone po pierwszej fali — reszty dni nie liczymy


def test_snacks_are_skipped_and_soups_are_audited_as_two_day_blocks(repo, monkeypatch):
    snack = next(d for d in repo.dishes.values() if MealType.SNACK in d.meal_types)
    assert da.audit_dish(snack, repo.approved_dishes(), repo.products, repo.assumptions) == []
    spans = []

    def fake_solve(d, meal, days, library, products, a, settings):
        spans.append(len(days))
        return block(BlockStatus.OK)

    monkeypatch.setattr(da, "_solve_days", fake_solve)
    soup = repo.dishes["zupa_gulaszowa_z_ziemniakami"]
    partners = da.partner_pool(repo.approved_dishes(), repo.products, repo.assumptions)
    assert da.audit_dish(soup, repo.approved_dishes(), repo.products, repo.assumptions, partners=partners) == []
    assert spans and set(spans) == {2}


def test_watery_soup_fails_and_hearty_soup_passes(repo):
    hearty = repo.dishes["zupa_gulaszowa_z_ziemniakami"]
    data = hearty.model_dump(by_alias=True, mode="json")
    data["id"], data["nazwa"] = "wodnista_zupa_test", "Wodnista zupa gulaszowa"
    data["skladniki"].append({"produkt": "bulion_warzywny", "rola": "sos", "porcjowanie": "z_garnka", "typowo_g": 900})
    data["grupy"] = [{"nazwa": "wywar", "skladniki": ["bulion_warzywny", "ziemniaki", "papryka_czerwona"]}]
    watery = DishTemplate.model_validate(data)
    passed, failed = da.audit_dishes([hearty, watery], repo.approved_dishes(), repo.products, repo.assumptions)
    assert [d.id for d in passed] == [hearty.id]
    assert [d.id for d, _ in failed] == ["wodnista_zupa_test"] and "talerz" in failed[0][1][0]


def test_engine_request_still_solves_a_real_day(repo):
    slots = [{"days": [0], "meal": m, "candidates": [d]} for m, d in (
        ("sniadanie", "kanapki_z_szynka_jajkiem_i_pomidorem"), ("obiad", "kurczak_curry_z_ryzem"),
        ("kolacja", "salatka_grecka_z_kurczakiem"))]
    res = solve_week(EngineRequest.model_validate({"slots": slots}), repo.assumptions, repo.products, repo.dishes)
    assert res.blocks[0].status is BlockStatus.OK
