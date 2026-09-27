from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dietbot.app.api import create_app
from dietbot.app.service import DietService
from dietbot.app.snapshots import capture
from dietbot.app.store import PlanRecord
from dietbot.checker.verify import verify_plan
from dietbot.domain.diet import compatible, dish_compatible
from dietbot.domain.goal_calculator import GoalInput, calculate_goals
from dietbot.domain.plan import WeekPlan
from dietbot.storage.repository import check_consistency


def profile(**kw):
    return GoalInput.model_validate(dict(age=35, height_cm=180, weight_kg=85, sex="male", activity="light",
                                        goal="lose", target_weight_kg=80, adult_without_special_needs=True, **kw))


def test_calculator_formula_and_bands():
    result = calculate_goals(profile())
    assert result["resting_kcal"] == 1805
    assert result["maintenance_kcal"] == 2482
    assert result["targets"]["kcal"]["cel"] == 2230
    assert result["targets"]["bialko"]["cel"] == 112
    assert result["loss_kg"] == 5
    for band in result["targets"].values():
        assert band["min"] <= band["cel"] <= band["max"]


@pytest.mark.parametrize("change", [{"age": 17}, {"adult_without_special_needs": False}, {"weight_kg": float("nan")},
                                   {"height_cm": 0}, {"target_weight_kg": 50}, {"target_weight_kg": 90}])
def test_calculator_rejects_unsafe_input(change):
    data = profile().model_dump()
    with pytest.raises(ValidationError):
        GoalInput.model_validate({**data, **change})


def test_low_energy_not_silently_clamped():
    data = profile().model_dump()
    data.update(age=65, height_cm=150, weight_kg=60, target_weight_kg=57, sex="female", activity="low")
    with pytest.raises(ValueError, match="1500"):
        calculate_goals(GoalInput.model_validate(data))


def test_diets_cover_fixed_snacks_optional_and_unknown(repo):
    a = repo.assumptions.model_copy(deep=True)
    a.diet = "wegan"
    assert any("Stała pozycja" in e for e in check_consistency(repo.products, repo.dishes, a).errors)
    assert not compatible(repo.products["miod"], "wegan")
    assert not compatible(repo.products["parmezan"], "wege")
    assert not compatible(repo.products["makaron_jajeczny"], "wegan")
    assert compatible(repo.products["tofu"], "wegan")
    assert not compatible(repo.products["tofu"].model_copy(update={"id": "nieznany_produkt"}), "wegan")
    r = replace(repo, assumptions=a).for_planning()
    assert all(dish_compatible(r.dishes[d], r.products, "wegan") for d in r.assumptions.day.snacks.allowed)
    rep = verify_plan(WeekPlan(days=[0], meals=[]), a, repo.products, repo.dishes)
    assert "rodzaj_diety" in rep.codes()


def test_new_recipes_classification(repo):
    added = [d for d in repo.dishes.values() if d.id.startswith("roslinne_020_")]
    assert len(added) == 34
    assert sum(dish_compatible(d, repo.products, "wegan") for d in added) == 26
    assert all(dish_compatible(d, repo.products, "wege") and d.steps and d.prep_minutes for d in added)


def test_snapshot_keeps_fixed_items_targets_tables_and_exports(repo, tmp_path):
    s = DietService(repo.paths, use_llm=False, db_path=tmp_path / "snap.sqlite3")
    plan = WeekPlan(days=[0], meals=[])
    rec = s.store.save(PlanRecord(plan=plan, title="Historia", source="test", ok=False, snapshot=capture(s.repo, plan)))
    original = s.text_export(rec.id, "shopping")
    old_totals = s.plan(rec.id)["days"]
    s.repo = replace(s.repo, assumptions=s.repo.assumptions.model_copy(deep=True), products=dict(s.repo.products))
    s.repo.assumptions.day.fixed_items = []
    s.repo.assumptions.persons[0].targets.kcal.min = 1000
    milk = s.repo.products["mleko_1_5"].model_copy(deep=True)
    milk.per_100g.kcal = 88
    s.repo.products[milk.id] = milk
    assert s.text_export(rec.id, "shopping") == original
    assert s.plan(rec.id)["days"] == old_totals
    assert s.plan(rec.id)["assumptions_changed"]
    assert s.store.snapshot(rec.id)["legacy"] is False
    s.jobs.shutdown()


def test_legacy_migration_freezes_once_and_backs_up(repo, tmp_path):
    import sqlite3

    from dietbot.app.store import PlanStore

    db = tmp_path / "legacy.sqlite3"
    store = PlanStore(db)
    rec = store.save(PlanRecord(plan=WeekPlan(days=[0], meals=[]), title="Stary", source="test", ok=False))
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE plan_snapshots")
    s = DietService(repo.paths, use_llm=False, db_path=db)
    snap = s.store.snapshot(rec.id)
    assert snap["legacy"]
    assert db.with_name(db.name + ".before-snapshots.bak").exists()
    assert s.plan(rec.id)["snapshot_legacy"]
    s.jobs.shutdown()
    again = DietService(repo.paths, use_llm=False, db_path=db)
    assert again.store.snapshot(rec.id) == snap
    again.jobs.shutdown()


def test_active_plan_outside_latest_hundred_still_in_list(repo, tmp_path):
    s = DietService(repo.paths, use_llm=False, db_path=tmp_path / "many.sqlite3")
    old = None
    for n in range(102):
        rec = s.store.save(PlanRecord(plan=WeekPlan(days=[0], meals=[]), title=str(n), source="test", ok=False))
        if old is None:
            old = rec
    s.store.set_setting("active_plan", old.id)
    assert any(r["id"] == old.id and r["active"] for r in s.plans())
    s.jobs.shutdown()


def test_origin_rejects_other_local_ports(repo, tmp_path):
    s = DietService(repo.paths, use_llm=False, db_path=tmp_path / "origin.sqlite3")
    client = TestClient(create_app(s))
    for origin in ("https://example.org", "http://testserver:9999", "http://127.0.0.1:9999", "null"):
        assert client.post("/api/cele/oblicz", json=profile().model_dump(), headers={"Origin": origin}).status_code == 403
    assert client.post("/api/cele/oblicz", json=profile().model_dump(), headers={"Origin": "http://testserver"}).status_code == 200
    s.jobs.shutdown()


def test_calculator_api_has_no_settings_side_effects(repo, tmp_path):
    s = DietService(repo.paths, use_llm=False, db_path=tmp_path / "api.sqlite3")
    before = s.repo.assumptions.model_dump_json()
    client = TestClient(create_app(s))
    assert client.post("/api/cele/oblicz", json=profile().model_dump()).status_code == 200
    assert s.repo.assumptions.model_dump_json() == before
    assert not s.store.list()
    s.jobs.shutdown()
