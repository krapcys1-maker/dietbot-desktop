"""Propozycje dań w guście domowników (25.10.2026): kod zbiera sygnały i kandydatów, model tylko wybiera i opisuje.

Udawany model — bez sieci i kosztów. Sprawdzamy, że model nie przemyci dania spoza listy ani liczb, że wybór
„do następnego planu” naprawdę trafia do planu i że czat pyta przed płatnym odświeżeniem.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from dietbot.app.chat import ChatAssistant
from dietbot.app.jobs import JobStatus
from dietbot.app.service import DietService
from dietbot.app.taste import Taste, affinity, candidates, load_state, pending_dishes
from dietbot.domain.dish import MealType
from dietbot.llm.client import LLMResult
from dietbot.storage.paths import Paths

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = re.compile(r"^([a-z0-9_]+) \| ", re.M)


class FakeLLM:
    def __init__(self):
        self.queue: list = []
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, task, system, user, schema, *, temperature=None, max_tokens=None):
        self.calls.append((task, user))
        value = self.queue.pop(0)
        value = value(user) if callable(value) else value
        return LLMResult(value=schema.model_validate(value), attempts=1, cost_usd=0.0001, prompt_tokens=0,
                         completion_tokens=0, seconds=0.0)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    home = tmp_path_factory.mktemp("dietbot_gust")
    for part in ("dane/produkty", "dane/dania", "dane/sklepy", "dane/korekty", "konfiguracja"):
        shutil.copytree(ROOT / part, home / part)
    (home / "wyniki").mkdir()
    service = DietService(Paths(home), use_llm=False)
    job = service.jobs.wait(service.generate(use_llm=False, seed=5).id, timeout=600)
    assert job.status is JobStatus.DONE, job.error
    old_key = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = "x" * 32  # llm_ready() patrzy tylko na obecność klucza
    fake = FakeLLM()
    service.use_llm = True
    service._llm = fake  # noqa: SLF001 — test podmienia klienta LLM
    yield service, fake
    service.jobs.shutdown()
    if old_key is None:
        os.environ.pop("DEEPSEEK_API_KEY", None)
    else:
        os.environ["DEEPSEEK_API_KEY"] = old_key


def test_candidates_skip_eaten_and_follow_taste(repo):
    lunch = [d for d in repo.approved_dishes() if MealType.LUNCH in repo.dishes[d].meal_types]
    chicken = [d for d in lunch if any(i.product_id == "piers_z_kurczaka" and i.typical_g >= 100
                                       for i in repo.dishes[d].ingredients)]
    fish = [d for d in lunch if any(i.product_id == "losos_surowy" for i in repo.dishes[d].ingredients)]
    liked, disliked = chicken[:3], fish[:2]
    weights = affinity(repo.dishes, liked, disliked, [], [])
    by_meal = {MealType.LUNCH: lunch}
    eaten = set(chicken[3:6])
    out = candidates(repo.dishes, by_meal, eaten, set(liked), weights, seed=1, per_meal=10)
    assert out and not set(out) & (eaten | set(liked))
    assert not set(out) & set(disliked)
    with_chicken = sum(any(i.product_id == "piers_z_kurczaka" for i in repo.dishes[d].ingredients) for d in out)
    assert with_chicken >= 6, out  # gust: kurczak polubiony, łosoś nie


def test_refresh_keeps_only_library_candidates_and_no_numbers(env):
    service, fake = env
    plan = service.store.active().plan
    liked = plan.meals[0].dish_id
    service.rate_dish(liked, 1)

    def answer(prompt: str) -> dict:
        assert "Polubione dania" in prompt and "KANDYDACI" in prompt
        ids = CANDIDATE.findall(prompt.split("KANDYDACI", 1)[1])
        assert ids and liked not in ids and not set(ids) & {m.dish_id for m in plan.meals}
        return {"podsumowanie": "Lubicie proste dania z kurczakiem. Jecie 3 razy dziennie.",
                "lubia": ["kurczak", "200 g ryżu"], "nie_lubia": ["ryby"],
                "propozycje": [{"danie": ids[0], "powod": "Podobne do tego, co lubicie. Ma 30 g białka."},
                               {"danie": "danie_spoza_listy", "powod": "x"},
                               {"danie": ids[0], "powod": "dubel"},
                               {"danie": ids[-1], "powod": "Coś z innej kuchni."}],
                "nowe_pomysly": [{"posilek": "obiad", "kuchnia": "włoska",
                                  "opis": "makaron z kurczakiem w sosie z suszonych pomidorów",
                                  "powod": "Lubicie makarony i kurczaka."}]}

    fake.queue.append(answer)
    job = service.jobs.wait(Taste(service).refresh().id, timeout=300)
    assert job.status is JobStatus.DONE, job.error
    assert fake.calls[-1][0] == "gust"
    v = Taste(service).view()
    assert len(v["propozycje"]) == 2 and all(p["status"] == "nowa" for p in v["propozycje"])
    assert all(not re.search(r"\d", p["powod"]) for p in v["propozycje"])
    assert v["podsumowanie"] == "Lubicie proste dania z kurczakiem." and v["lubia"] == ["kurczak"]
    assert v["pomysly"][0]["kuchnia"] == "wloska" and v["pomysly"][0]["status"] == "nowy"
    assert any(line.startswith("Propozycja:") for line in job.log)


def test_chosen_dish_goes_to_the_next_plan(env):
    service, _fake = env
    taste = Taste(service)
    dish_id = taste.view()["propozycje"][0]["danie"]
    view = taste.set_status(dish_id, "wybrana")
    assert view["do_planu"] and pending_dishes(service.store) == [dish_id]
    job = service.jobs.wait(service.generate(use_llm=False, seed=11).id, timeout=600)
    assert job.status is JobStatus.DONE, job.error
    assert dish_id in {m.dish_id for m in service.store.active().plan.meals}
    state = load_state(service.store)
    assert next(p for p in state["propozycje"] if p["danie"] == dish_id)["status"] == "w_planie"
    assert not pending_dishes(service.store)
    # gust trafia też jako miękka wskazówka do modelu układającego tydzień
    assert any("Gust domowników" in h for h in service.repo.preference_hints)


def test_dismissed_proposal_becomes_a_signal(env):
    service, fake = env
    taste = Taste(service)
    other = next(p["danie"] for p in taste.view()["propozycje"] if p["status"] == "nowa")
    taste.set_status(other, "odrzucona")
    assert "odrzucili" in "\n".join(taste.signals()["lines"])
    with pytest.raises(Exception, match="Tej propozycji"):
        taste.set_status("nie_ma_takiego", "wybrana")


def test_chat_asks_before_paid_refresh(env):
    service, fake = env
    fake.queue.append({"typ": "propozycje_gustu", "tekst": "Chętnie coś zaproponuję."})
    reply = ChatAssistant(service).handle("Zaproponuj coś, co nam zasmakuje")
    assert reply["data"]["kind"] == "potwierdz_akcje" and "Uruchomić?" in reply["text"]
    fake.queue.append(lambda prompt: {"podsumowanie": "Na razie widać, że lubicie kurczaka.", "propozycje": []})
    done = ChatAssistant(service).confirm(reply["data"]["proposal_id"])
    assert done["data"]["kind"] == "gust"
    job = service.jobs.wait(done["data"]["job_id"], timeout=300)
    assert job.status is JobStatus.DONE, job.error
