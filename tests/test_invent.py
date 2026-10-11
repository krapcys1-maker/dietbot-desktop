"""Wymyślanie nowych dań na prośbę (udawany LLM): plan zamówień → przepisy → kontrola kodu → biblioteka."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dietbot.app.chat import ChatAssistant
from dietbot.app.jobs import JobStatus
from dietbot.app.service import DietService
from dietbot.domain import MealType, Weekday
from dietbot.llm.client import LLMResult
from dietbot.planner.compose import build_compose_prompt
from dietbot.storage.paths import Paths

ROOT = Path(__file__).resolve().parents[1]

PASTA = {
    "id": "makaron_z_indykiem_cukinia_i_passata", "nazwa": "Makaron z indykiem, cukinią i passatą",
    "opis": "Makaron z kawałkami piersi z indyka, cukinią i passatą, posypany parmezanem.",
    "posilki": ["obiad"], "kuchnia": "włoska", "tagi": ["na_cieplo"],
    "skladniki": [
        {"produkt": "piers_z_indyka", "rola": "bialko", "typowo_g": 170},
        {"produkt": "makaron_pszenny", "rola": "weglowodany", "typowo_g": 85},
        {"produkt": "cukinia", "rola": "warzywa", "typowo_g": 150},
        {"produkt": "passata", "rola": "sos", "typowo_g": 150, "porcjowanie": "z_garnka"},
        {"produkt": "oliwa_z_oliwek", "rola": "tluszcz", "typowo_g": 8},
        {"produkt": "parmezan", "rola": "dodatek", "typowo_g": 10},
        {"produkt": "sol", "rola": "przyprawa", "typowo_g": 1}],
    "przygotowanie": ["Makaron ugotuj al dente.", "Indyka pokrój i podsmaż na oliwie, dodaj cukinię.",
                      "Wlej passatę, duś 5 minut, wymieszaj z makaronem i posyp parmezanem."],
    "czas_min": 25,
}
SHRIMP = {
    "id": "salatka_z_krewetkami_mango_i_ogorkiem", "nazwa": "Sałatka z krewetkami, mango i ogórkiem",
    "opis": "Lekka sałatka z krewetkami, mango, ogórkiem i sałatą masłową, skropiona sokiem z limonki.",
    "posilki": ["kolacja"], "kuchnia": "azjatycka", "tagi": ["szybkie"],
    "skladniki": [
        {"produkt": "krewetki_surowe", "rola": "bialko", "typowo_g": 180},
        {"produkt": "mango", "rola": "owoce", "typowo_g": 80},
        {"produkt": "ogorek", "rola": "warzywa", "typowo_g": 120},
        {"produkt": "salata_maslowa", "rola": "warzywa", "typowo_g": 60},
        {"produkt": "chleb_pelnoziarnisty", "rola": "weglowodany", "typowo_g": 60},
        {"produkt": "oliwa_z_oliwek", "rola": "tluszcz", "typowo_g": 8},
        {"produkt": "limonka_sok", "rola": "przyprawa", "typowo_g": 5}],
    "przygotowanie": ["Krewetki podsmaż 3 minuty.", "Mango i ogórka pokrój, wymieszaj z sałatą i krewetkami.",
                      "Skrop oliwą i sokiem z limonki, podawaj z pieczywem."],
    "czas_min": 15,
}
BAD = {**PASTA, "id": "makaron_z_gorgonzola", "nazwa": "Makaron z gorgonzolą i gruszką",
       "opis": "Makaron w sosie serowym.",
       "skladniki": [{"produkt": "gorgonzola", "rola": "bialko", "typowo_g": 60},
                     {"produkt": "makaron_pszenny", "rola": "weglowodany", "typowo_g": 85}]}


class FakeLLM:
    def __init__(self):
        self.tasks: list[str] = []

    class settings:  # noqa: N801 — udaje LLMSettings (tylko nazwa modelu)
        model = "udawany"

    def complete_json(self, task, system, user, schema, **kw):
        self.tasks.append(task)
        if task == "wymyslanie_dan_plan":
            value = {"zamowienia": [{"posilek": "obiad", "kuchnia": "włoska", "ile": 2, "szczegoly": "makaron"},
                                    {"posilek": "kolacja", "kuchnia": "azjatycka", "ile": 1, "szczegoly": "z krewetkami"}]}
        elif task.startswith("generacja_dan_obiad"):
            value = {"dania": [PASTA, BAD]}
        elif task.startswith("generacja_dan_kolacja"):
            value = {"dania": [SHRIMP]}
        elif task == "poprawa_dan":
            value = {"dania": []}  # model nie potrafi poprawić dania z produktem spoza bazy
        elif task == "kontrola_kucharza":
            value = {"oceny": [{"id": "salatka_z_krewetkami_mango_i_ogorkiem", "ocena": "popraw_tekst",
                                "powod": "literówka", "opis": "Lekka sałatka z krewetkami, mango, ogórkiem i sałatą masłową."}]}
        elif task == "czat":
            value = {"typ": "wymysl_dania", "tekst": "Już wymyślam.", "zyczenia": "dania z krewetkami", "ile_dan": 3}
        else:  # pragma: no cover
            raise AssertionError(task)
        return LLMResult(value=schema.model_validate(value), attempts=1, cost_usd=0.001, prompt_tokens=0,
                         completion_tokens=0, seconds=0.0)


@pytest.fixture()
def env(tmp_path):
    for part in ("dane/produkty", "dane/dania", "dane/sklepy", "konfiguracja"):
        shutil.copytree(ROOT / part, tmp_path / part)
    (tmp_path / "wyniki").mkdir()
    service = DietService(Paths(tmp_path), use_llm=False)
    fake = FakeLLM()
    service.use_llm = True
    service._llm = fake  # noqa: SLF001
    return service, fake


def test_invent_adds_validated_dishes_to_library(env):
    service, fake = env
    before = set(service.repo.dishes)
    job = service.jobs.wait(service.invent("dwa obiady włoskie i kolacja z krewetkami", 3).id, timeout=120)
    assert job.status is JobStatus.DONE, (job.error, job.log)
    names = {d["name"] for d in job.result["dishes"]}
    assert names == {"Makaron z indykiem, cukinią i passatą", "Sałatka z krewetkami, mango i ogórkiem"}
    new = set(service.repo.dishes) - before
    assert new == {"makaron_z_indykiem_cukinia_i_passata", "salatka_z_krewetkami_mango_i_ogorkiem"}
    pasta = service.repo.dishes["makaron_z_indykiem_cukinia_i_passata"]
    assert pasta.source.startswith("wymyślone na prośbę") and pasta.cuisine == "wloska"
    assert "makaron_z_gorgonzola" not in service.repo.dishes  # produkt spoza bazy — odrzucone
    assert any("Odrzucone przez kontrolę" in line and "gorgonzola" in line for line in job.log)
    assert "makaron_z_indykiem_cukinia_i_passata" in service.pools().by_meal[MealType.LUNCH]
    listed = {d["id"]: d for d in service.dishes()}
    assert listed["salatka_z_krewetkami_mango_i_ogorkiem"]["invented"] is True
    assert (service.paths.dishes_dir / "gen_obiad.yaml").read_text("utf-8").count("makaron_z_indykiem") == 1
    # kontrola kucharza poprawiła opis sałatki (tekst przeszedł testy kodu)
    assert service.repo.dishes["salatka_z_krewetkami_mango_i_ogorkiem"].description.endswith("sałatą masłową.")


def test_invent_without_llm_fails_politely(tmp_path):
    for part in ("dane/produkty", "dane/dania", "dane/sklepy", "konfiguracja"):
        shutil.copytree(ROOT / part, tmp_path / part)
    (tmp_path / "wyniki").mkdir()
    service = DietService(Paths(tmp_path), use_llm=False)
    job = service.jobs.wait(service.invent("coś z krewetkami").id, timeout=60)
    assert job.status is JobStatus.FAILED and "modelu językowego" in job.error


def test_chat_routes_invention_request(env):
    """Płatne wymyślanie dań z czatu startuje dopiero po „Tak, uruchom” (audyt 09.10.2026, W4)."""
    service, fake = env
    reply = ChatAssistant(service).handle("wymyśl mi trzy dania z krewetkami")
    assert reply["data"]["kind"] == "potwierdz_akcje" and "płatne" in reply["text"]
    assert "generacja_dan_obiad" not in " ".join(fake.tasks)  # nic jeszcze nie wymyślono
    started = ChatAssistant(service).confirm(reply["data"]["proposal_id"])
    assert started["data"]["kind"] == "wymyslanie"
    job = service.jobs.wait(started["data"]["job_id"], timeout=120)
    assert job.status is JobStatus.DONE


def test_chat_does_not_force_invention_on_negation_or_question(env):
    service, fake = env

    def answer(task, system, user, schema, **kw):
        return LLMResult(value=schema.model_validate({"typ": "odpowiedz", "tekst": "Białko liczy program."}), attempts=1,
                         cost_usd=0.0, prompt_tokens=0, completion_tokens=0, seconds=0.0)

    fake.complete_json = answer
    for text in ("Nie wymyślaj nowych dań, powiedz tylko, jak liczycie białko", "Jak wymyślacie nowe dania?",
                 "Czy macie jakieś nowe dania?"):
        reply = ChatAssistant(service).handle(text)
        assert reply["data"] is None and reply["text"] == "Białko liczy program.", text


def test_compose_prompt_prefers_new_dishes(repo):
    from dietbot.planner.pools import build_pools

    pools = build_pools(repo.approved_dishes(), repo.products, repo.assumptions)
    dish = pools.by_meal[MealType.LUNCH][0]
    prompt = build_compose_prompt(pools, repo.assumptions, list(Weekday), None, prefer={dish, "nie_ma_takiego"})
    assert f"NOWE dania wymyślone na prośbę domowników — wykorzystaj je w tym tygodniu (każde raz, w pasującym posiłku): {dish}" in prompt
    assert "nie_ma_takiego" not in prompt


def test_chef_review_rejects_and_fixes_text(repo):
    from dietbot.food.matcher import ProductMatcher
    from dietbot.llm.chef_review import DishReview, apply_review, dish_card

    dish = repo.dishes["nalesniki_z_twarogiem_i_miodem"]
    matcher = ProductMatcher(repo.products)
    card = dish_card(dish, repo.products)
    assert "[masa: ciasto naleśnikowe]" in card and "Mąka pszenna typ 450: 55 g" in card
    gone, why = apply_review(dish, DishReview(id=dish.id, ocena="odrzuc", powod="dziwne proporcje"), matcher, set(), set())
    assert gone is None and why == "dziwne proporcje"
    fixed, note = apply_review(dish, DishReview(id=dish.id, ocena="popraw_tekst", opis="Cienkie naleśniki z twarogiem i miodem."),
                               matcher, set(), set())
    assert fixed.description == "Cienkie naleśniki z twarogiem i miodem." and note == "opis"
    # poprawka wspominająca produkt spoza składników jest ignorowana
    kept, note = apply_review(dish, DishReview(id=dish.id, ocena="popraw_tekst", opis="Naleśniki z twarogiem i łososiem."),
                              matcher, set(), set())
    assert kept is dish and note is None


CHICKEN_PASTA = {
    **PASTA, "id": "makaron_z_kurczakiem_brokulami_i_passata", "nazwa": "Makaron z kurczakiem, brokułami i passatą",
    "opis": "Makaron z kawałkami piersi z kurczaka, brokułami i passatą, posypany parmezanem.",
    "skladniki": [
        {"produkt": "piers_z_kurczaka", "rola": "bialko", "typowo_g": 170},
        {"produkt": "makaron_pszenny", "rola": "weglowodany", "typowo_g": 85},
        {"produkt": "brokul", "rola": "warzywa", "typowo_g": 150},
        {"produkt": "passata", "rola": "sos", "typowo_g": 150, "porcjowanie": "z_garnka"},
        {"produkt": "oliwa_z_oliwek", "rola": "tluszcz", "typowo_g": 8},
        {"produkt": "parmezan", "rola": "dodatek", "typowo_g": 10},
        {"produkt": "sol", "rola": "przyprawa", "typowo_g": 1}],
}


@pytest.mark.parametrize("audit_rejects_first", [True, False])
def test_spare_dish_replaces_one_rejected_by_engine_test(repo, monkeypatch, audit_rejects_first):
    from dietbot.llm.generate_dishes import GenOrder, run_generation_plan
    from dietbot.planner import dish_audit

    class TwoPastas(FakeLLM):
        def complete_json(self, task, system, user, schema, **kw):
            assert task.startswith("generacja_dan_obiad")
            return LLMResult(value=schema.model_validate({"dania": [PASTA, CHICKEN_PASTA]}), attempts=1, cost_usd=0.001,
                             prompt_tokens=0, completion_tokens=0, seconds=0.0)

    def fake_audit(candidates, dishes, products, a, workers=None, sampled=True):
        if audit_rejects_first:
            return candidates[1:], [(candidates[0], ["silnik nie układa poprawnego dnia z tym daniem"])]
        return candidates, []

    monkeypatch.setattr(dish_audit, "audit_dishes", fake_audit)
    logs: list[str] = []
    accepted, rejected, _ = run_generation_plan(repo, TwoPastas(), plan=[GenOrder(MealType.LUNCH, "wloska", 2, keep=1)],
                                                engine_check=True, log=logs.append)
    if audit_rejects_first:  # zapasowe danie zajęło miejsce odrzuconego
        assert [d.id for d in accepted] == [CHICKEN_PASTA["id"]]
        assert any(r.get("test_silnika") for r in rejected)
    else:  # oba przeszły — zamówienie dostaje dokładnie tyle, ile chciano
        assert [d.id for d in accepted] == [PASTA["id"]]
        assert any("danie zapasowe niepotrzebne" in line for line in logs)
