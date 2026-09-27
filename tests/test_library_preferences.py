from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from dietbot.app.api import create_app
from dietbot.app.service import DietService
from dietbot.app.store import PlanRecord, PlanStore
from dietbot.domain.assumptions import Weekday
from dietbot.domain.dish import MealType
from dietbot.domain.plan import WeekPlan
from dietbot.planner.compose import build_compose_prompt, compose_fallback, prompt_pools
from dietbot.planner.pipeline import WeekPlanner
from dietbot.planner.pools import Pools, build_pools


@pytest.fixture
def service(repo, tmp_path):
    return DietService(repo.paths, use_llm=False, db_path=tmp_path / "library.sqlite3")


def test_save_library_persistence_and_history(service):
    rec = service.store.save(PlanRecord(plan=WeekPlan(days=[Weekday.MONDAY], meals=[]),
                                       title="Przykładowy tydzień", source="algorytm", ok=False))
    client = TestClient(create_app(service))
    original = service.store.get(rec.id).plan.model_dump_json()
    assert client.put(f"/api/diety/{rec.id}", json={"title": "  Moja dieta  "}).status_code == 200
    assert client.put(f"/api/diety/{rec.id}", json={"title": "Nowa nazwa"}).status_code == 200
    saved = PlanStore(service.store.path).saved_diets()
    assert len(saved) == 1 and saved[rec.id]["title"] == "Nowa nazwa"
    child = service.store.save(PlanRecord(plan=rec.plan, title=rec.title, parent_id=rec.id, source="zamiana", ok=False))
    library = client.get("/api/diety").json()
    assert len(library) == 2
    assert next(r for r in library if r["id"] == child.id)["saved_diet"] is None
    assert client.delete(f"/api/plany/{rec.id}").status_code == 400  # zapis chroni konkretną wersję
    assert client.delete(f"/api/diety/{rec.id}").status_code == 200
    assert not service.store.saved_diets()
    assert len(client.get("/api/diety").json()) == 2
    assert service.store.get(rec.id).plan.model_dump_json() == original
    assert client.put("/api/diety/missing", json={"title": "Nie ma"}).status_code == 400
    assert client.put(f"/api/diety/{rec.id}", json={"title": "  "}).status_code == 400


def test_ratings_filter_all_pools_and_snacks_and_persist(service):
    client = TestClient(create_app(service))
    pools = service.pools()
    meal_id = pools.by_meal[MealType.BREAKFAST][0]
    batch_id = next(iter(pools.batch.values()))[0]
    treat_id = pools.treat_snacks[0]
    snack_id = service.repo.assumptions.day.snacks.allowed[0]
    blocked = {meal_id, batch_id, treat_id, snack_id}
    original = service.repo.assumptions.model_dump_json()
    for did in blocked:
        assert client.put(f"/api/oceny/{did}", json={"rating": -1}).status_code == 200
    assert PlanStore(service.store.path).ratings() == dict.fromkeys(blocked, -1)
    service.reload()
    pools = service.pools()
    eligible = set().union(*map(set, pools.by_meal.values()), *map(set, pools.batch.values()), set(pools.treat_snacks))
    assert not blocked & eligible
    planner = WeekPlanner(service.repo)
    assert not blocked & set(planner.repo.approved_dishes())
    assert not blocked & set(planner.repo.assumptions.day.snacks.allowed)
    assert service.repo.assumptions.model_dump_json() == original
    assert all("nie lubisz" in "; ".join(pools.excluded[d]) for d in blocked)
    assert client.put(f"/api/oceny/{meal_id}", json={"rating": 1}).status_code == 200
    assert meal_id in service.pools().by_meal[MealType.BREAKFAST]
    assert meal_id in service.pools().liked
    assert "ULUBIONE" in service.pools().profiles[meal_id].line()
    assert client.put(f"/api/oceny/{meal_id}", json={"rating": 0}).status_code == 200
    assert meal_id not in client.get("/api/oceny").json()
    assert client.put(f"/api/oceny/{meal_id}", json={"rating": 2}).status_code == 400
    assert client.put("/api/oceny/missing", json={"rating": 1}).status_code == 400


def test_likes_increase_selection_without_forcing(repo):
    # Dwie identyczne opcje: różni je wyłącznie ocena. Porównanie wielu deterministycznych losowań.
    a = repo.assumptions.model_copy(deep=True)
    a.day.meals = [MealType.BREAKFAST]
    a.week.batch = []
    base = build_pools(repo.approved_dishes(), repo.products, a)
    did = base.by_meal[MealType.BREAKFAST][0]
    dish = repo.dishes[did]
    dishes = {did: dish, "neutral": dish.model_copy(update={"id": "neutral"})}
    profiles = {did: base.profiles[did], "neutral": replace(base.profiles[did], dish_id="neutral")}
    plain = Pools({MealType.BREAKFAST: [did, "neutral"]}, {}, profiles, {})
    liked = replace(plain, liked={did})
    def count(p):
        return sum(compose_fallback(p, dishes, a, [Weekday.MONDAY], seed=s).slots[0].dish_id == did for s in range(100))
    assert count(liked) > count(plain) + 20
    assert count(liked) < 100  # preferencja, nie bezwzględny nakaz
    shown = prompt_pools(liked, dishes, 1, per_meal=1)
    assert did in shown.by_meal[MealType.BREAKFAST] and shown.liked == {did}
    assert "ULUBIONE" in build_compose_prompt(shown, a, [Weekday.MONDAY], None, set())
