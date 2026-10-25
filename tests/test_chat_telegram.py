"""Czat (z udawanym LLM — bez kosztów) i formatowanie wiadomości Telegrama."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from dietbot.app.chat import ChatAction, ChatAssistant, DishPick
from dietbot.app.jobs import JobStatus
from dietbot.app.service import DietService
from dietbot.app.telegram_bot import chunks, format_day, format_shopping, format_week, md_to_html
from dietbot.llm.client import LLMResult
from dietbot.storage.paths import Paths
from dietbot.storage.yamlio import load_yaml, to_plain

ROOT = Path(__file__).resolve().parents[1]


class FakeLLM:
    """Zwraca kolejne przygotowane odpowiedzi i zapamiętuje zapytania."""

    def __init__(self):
        self.queue: list = []
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, task, system, user, schema, *, temperature=None, max_tokens=None):
        self.calls.append((task, user))
        value = self.queue.pop(0)
        value = value(user) if callable(value) else value
        return LLMResult(value=schema.model_validate(value), attempts=1, cost_usd=0.0, prompt_tokens=0,
                         completion_tokens=0, seconds=0.0)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    home = tmp_path_factory.mktemp("dietbot_czat")
    for part in ("dane/produkty", "dane/dania", "dane/sklepy", "konfiguracja"):
        shutil.copytree(ROOT / part, home / part)
    (home / "wyniki").mkdir()
    service = DietService(Paths(home), use_llm=False)
    job = service.jobs.wait(service.generate(use_llm=False, seed=8).id, timeout=600)
    assert job.status is JobStatus.DONE, job.error
    fake = FakeLLM()
    service.use_llm = True
    service._llm = fake  # noqa: SLF001 — test podmienia klienta LLM
    return service, fake


def test_chat_text_cannot_contain_numbers():
    with pytest.raises(ValidationError):
        ChatAction.model_validate({"typ": "odpowiedz", "tekst": "Masz dziś 1650 kcal."})
    with pytest.raises(ValidationError):
        ChatAction.model_validate({"typ": "odpowiedz", "tekst": "Masz dwie słodkie kolacje."})
    assert ChatAction.model_validate({"typ": "odpowiedz", "tekst": "Jeden z obiadów jest rybny."})


def test_chat_facts_come_from_checker(env):
    service, fake = env
    rec = service.store.active()
    wednesday = next(t for t in rec.report.totals if int(t.day) == 2 and t.person_id == "kobieta")
    feta_like = next(i.product_id for m in rec.plan.meals for p in m.portions for i in p.items
                     if not service.repo.products[i.product_id].negligible)
    name = service.repo.products[feta_like].name
    fake.queue.append({"typ": "pokaz", "tekst": "Już sprawdzam.",
                       "fakty": [{"rodzaj": "dzien", "dzien": "sroda", "osoba": "żona"},
                                 {"rodzaj": "produkt", "produkt": name}]})
    reply = ChatAssistant(service).handle("Ile kalorii ma żona w środę?")
    assert f"{round(wednesday.kcal)} kcal" in reply["text"]
    assert name in reply["text"] and "Liczby: weryfikator" in reply["text"]
    assert "WIADOMOŚĆ UŻYTKOWNIKA" in fake.calls[-1][1] and "Środa" in fake.calls[-1][1]


def test_chat_swap_uses_engine_and_validated_pick(env):
    service, fake = env
    rec = service.store.active()
    options = service.swap_options(rec.id, "3_kolacja")["options"]
    target = next(o for o in options if not o["notes"])
    fake.queue.append({"typ": "zamien", "tekst": "Dobrze.", "dzien": "czwartek", "posilek": "kolacja",
                       "opis_dania": "coś innego"})
    fake.queue.append({"danie": "nie_ma_takiego", "uzasadnienie": "x"})
    reply = ChatAssistant(service).handle("Zamień czwartkową kolację")
    assert "Nie znalazłem" in reply["text"] and not (reply["data"] or {}).get("job_id")

    fake.queue.append({"typ": "zamien", "tekst": "Dobrze.", "dzien": "czwartek", "posilek": "kolacja",
                       "opis_dania": "coś innego"})
    fake.queue.append(DishPick(danie=target["id"], uzasadnienie="Pasuje.").model_dump())
    reply = ChatAssistant(service).handle("Zamień czwartkową kolację")
    job = service.jobs.wait(reply["data"]["job_id"], timeout=300)
    assert job.status is JobStatus.DONE, job.error
    new = service.store.get(job.result["plan_id"])
    assert new.plan.meal(3, "kolacja").dish_id == target["id"] and new.parent_id == rec.id
    assert "Pasuje." in reply["text"] and ".." not in reply["text"]


def test_chat_assumption_change_needs_confirmation(env):
    service, fake = env
    before = to_plain(load_yaml(service.paths.assumptions))
    fake.queue.append({"typ": "zmien_zalozenia", "tekst": "Proponuję zmianę.",
                       "zmiany": [{"sciezka": "osoby.mezczyzna.cele_dzienne.kcal", "operacja": "zmniejsz", "wartosc": 50},
                                  {"sciezka": "tydzien.slodkie_kolacje_max", "operacja": "ustaw", "wartosc": 3}]})
    reply = ChatAssistant(service).handle("Zmniejsz mi kalorie o 50 i pozwól na trzy słodkie kolacje")
    pid = reply["data"]["proposal_id"]
    assert "2350–2400, cel 2375 → 2300–2350, cel 2325" in reply["text"]
    assert to_plain(load_yaml(service.paths.assumptions)) == before  # nic nie zapisano bez potwierdzenia

    done = ChatAssistant(service).confirm(pid)
    assert "Zapisałem" in done["text"]
    after = service.repo.assumptions
    assert after.person("mezczyzna").targets.kcal.min == 2300 and after.week.sweet_suppers_max == 3
    assert "# słodka kolacja należy do puli 80%" in service.paths.assumptions.read_text("utf-8")
    assert "wygasła" in ChatAssistant(service).confirm(pid)["text"]

    fake.queue.append({"typ": "zmien_zalozenia", "tekst": "Zmieniam.",
                       "zmiany": [{"sciezka": "osoby.kobieta.cele_dzienne.kcal.cel", "operacja": "ustaw", "wartosc": 2500}]})
    bad = ChatAssistant(service).handle("Ustaw żonie cel 2500 kcal")
    assert "niespójne" in bad["text"] and "Kobieta" in bad["text"] and "poza widełkami" in bad["text"]

    fake.queue.append({"typ": "zmien_zalozenia", "tekst": "Zmieniam.",
                       "zmiany": [{"sciezka": "energia.metoda", "operacja": "ustaw", "wartosc": "etykieta"}]})
    assert "nie da się zmienić z czatu" in ChatAssistant(service).handle("zmień metodę")["text"]


def test_chat_without_llm_explains(env):
    service, _ = env
    service.use_llm, saved = False, service._llm  # noqa: SLF001
    service._llm = None  # noqa: SLF001
    try:
        assert "modelu językowego" in ChatAssistant(service).handle("cześć")["text"]
    finally:
        service.use_llm, service._llm = True, saved  # noqa: SLF001


def test_telegram_formatting(env):
    service, _ = env
    rec = service.store.active()
    view = service.plan(rec.id)
    day = format_day(view, 0)
    assert day.startswith("<b>Poniedziałek") and "Śniadanie" in day and "kcal" in day
    assert "<script" not in day
    week = format_week(view)
    assert week.count("\n<b>") >= 7 - 1
    shop = format_shopping(service.shopping(rec.id))
    assert shop.startswith("<b>Lista zakupów — ")
    parts = chunks(shop * 5, limit=500)
    assert all(len(p) <= 500 for p in parts) and "".join(parts).count("Lista zakupów") == 5
    assert md_to_html("**Środa** & <b>x</b> _uwaga_") == "<b>Środa</b> &amp; &lt;b&gt;x&lt;/b&gt; <i>uwaga</i>"
    assert md_to_html("snake_case_id") == "snake_case_id"
