"""Bilans makro dnia: docelowy rozkład, etykiety dla modelu i wymiany dań w dniach o złym rozkładzie."""

from __future__ import annotations

import pytest

from dietbot.domain import MealType, Weekday
from dietbot.planner import compose as compose_mod
from dietbot.planner.autofix import rebalance_days
from dietbot.planner.balance import BALANCE_WARN_PP, day_deviation, macro_labels, split_of, target_split
from dietbot.planner.compose import compose_fallback, validate_composition, wish_errors
from dietbot.planner.pools import build_pools

DAYS = list(Weekday)


@pytest.fixture(scope="module")
def setup(repo):
    dishes = repo.approved_dishes()
    return repo.assumptions, dishes, build_pools(dishes, repo.products, repo.assumptions)


def day_devs(comp, pools):
    return {d: day_deviation([pools.profiles[s.dish_id].typical for s in comp.slots if d in {int(x) for x in s.days}],
                             pools.target) for d in range(7)}


def unbalanced_week(setup, seed, monkeypatch):
    a, dishes, pools = setup
    monkeypatch.setattr(compose_mod, "day_deviation", lambda *args, **kw: 0.0)  # algorytm bez bilansu
    comp = compose_fallback(pools, dishes, a, DAYS, seed=seed)
    monkeypatch.undo()
    return comp


def test_target_split_from_assumptions(repo, setup):
    _, _, pools = setup
    t = target_split(repo.assumptions, repo.products)
    assert pools.target == t
    assert t.protein == pytest.approx(0.25, abs=0.02) and t.fat == pytest.approx(0.30, abs=0.02)
    assert t.carbs == pytest.approx(0.45, abs=0.02) and t.protein + t.fat + t.carbs == pytest.approx(1.0)


def test_macro_labels_and_split():
    assert split_of([{"protein": 0, "fat": 0, "carbs": 0}]) is None
    fatty = {"protein": 30, "fat": 40, "carbs": 20}  # 9·40 = 360 kcal z 560
    assert "tłuste" in macro_labels(fatty) and "mało węgli" in macro_labels(fatty)
    carby = {"protein": 20, "fat": 8, "carbs": 110}
    assert macro_labels(carby) == ["dużo węgli"]


def test_profile_line_shows_macro_labels(setup):
    _, _, pools = setup
    lines = [pools.profiles[d].line() for d in pools.by_meal[MealType.SUPPER]]
    assert any("tłuste" in line for line in lines) and any("mało węgli" in line for line in lines)


@pytest.mark.parametrize("seed", [1, 4, 9])
def test_rebalance_improves_days_without_breaking_rules(setup, seed, monkeypatch):
    a, dishes, pools = setup
    comp = unbalanced_week(setup, seed, monkeypatch)
    assert validate_composition(comp, pools, dishes, a, DAYS) == []
    before = day_devs(comp, pools)
    notes = rebalance_days(comp, pools, dishes, a, DAYS)
    after = day_devs(comp, pools)
    assert validate_composition(comp, pools, dishes, a, DAYS) == []
    assert max(after.values()) <= max(before.values())
    if max(before.values()) > BALANCE_WARN_PP:
        assert notes and all("bilans makro dnia" in n for n in notes)
        assert sum(after.values()) < sum(before.values())
    # dania na zapas i posiłki z puli 20% zostają nietknięte
    for s_before, s_after in zip(unbalanced_week(setup, seed, monkeypatch).slots, comp.slots, strict=True):
        if len(s_before.days) > 1 or dishes[s_before.dish_id].is_treat:
            assert s_before.dish_id == s_after.dish_id


def test_rebalance_keeps_protected_dishes_and_cuisine_counts(setup, monkeypatch):
    a, dishes, pools = setup
    comp = unbalanced_week(setup, 4, monkeypatch)
    worst = max(day_devs(comp, pools).items(), key=lambda kv: kv[1])[0]
    protected = {s.dish_id for s in comp.slots if worst in {int(d) for d in s.days}}
    counts = {"polska": sum(1 for s in comp.slots if dishes[s.dish_id].cuisine == "polska")}
    rebalance_days(comp, pools, dishes, a, DAYS, cuisine_counts=counts, protected=protected)
    assert protected <= {s.dish_id for s in comp.slots}
    assert wish_errors(comp, pools, dishes, a, DAYS, cuisine_counts=counts) == []


def test_rebalance_still_works_when_a_wish_is_unmet(setup, monkeypatch):
    a, dishes, pools = setup
    comp = unbalanced_week(setup, 9, monkeypatch)
    counts = {"wloska": 6}  # w tym układzie nie do spełnienia — układ zgodny z zasadami, ale nie z życzeniem
    before = set(wish_errors(comp, pools, dishes, a, DAYS, cuisine_counts=counts))
    assert before
    devs = day_devs(comp, pools)
    notes = rebalance_days(comp, pools, dishes, a, DAYS, cuisine_counts=counts)
    assert set(wish_errors(comp, pools, dishes, a, DAYS, cuisine_counts=counts)) <= before
    assert validate_composition(comp, pools, dishes, a, DAYS) == []
    if max(devs.values()) > BALANCE_WARN_PP:
        assert notes and max(day_devs(comp, pools).values()) <= max(devs.values())
