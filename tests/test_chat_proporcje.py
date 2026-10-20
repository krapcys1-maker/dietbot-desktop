"""Czat poprawia proporcje dania zamiast je zamieniać (przypadki z czatu domowników z 17.10.2026).

Udawany model językowy (bez sieci i kosztów) wskazuje tylko posiłek, składnik i kierunek — gramy liczy silnik.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from fixtures_week import WEEK_SLOTS

from dietbot.app.chat import ChatAssistant, ingredient_ids
from dietbot.app.jobs import JobStatus
from dietbot.app.service import DietService
from dietbot.app.snapshots import capture, matches_current
from dietbot.app.store import PlanRecord
from dietbot.app.views import assumptions_hash
from dietbot.domain import MealType
from dietbot.domain.assumptions import parse_day
from dietbot.llm.client import LLMResult
from dietbot.planner.compose import Composition, SlotPlan
from dietbot.planner.pipeline import WeekPlanner
from dietbot.planner.portion_review import apply_corrections, grams_in_slot, plate_findings
from dietbot.storage.paths import Paths

ROOT = Path(__file__).resolve().parents[1]
CURRY = "roslinne_020_curry"  # „Curry z tofu, ciecierzycą i ryżem”
EGGS = "jajka_na_miekko_z_pieczywem_i_szynka"
# przepis curry sprzed korekty z 20.10.2026 (tyle mleczka i tofu miał plan domowników z 17.10)
OLD_CURRY = {"tofu": (180, 120, 280), "pomidory_z_puszki": (120, 72, 180), "mleczko_kokosowe": (35, 15, 60),
             "oliwa_z_oliwek": (10, 3, 20)}


class FakeLLM:
    """Zwraca kolejne przygotowane odpowiedzi i zapamiętuje zapytania."""

    def __init__(self):
        self.queue: list = []
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, task, system, user, schema, *, temperature=None, max_tokens=None):
        self.calls.append((task, user))
        return LLMResult(value=schema.model_validate(self.queue.pop(0)), attempts=1, cost_usd=0.0, prompt_tokens=0,
                         completion_tokens=0, seconds=0.0)


def week(changes: dict[str, str]) -> Composition:
    slots = []
    for s in WEEK_SLOTS:
        key = f"{s['days'][0]}_{s['meal']}"
        slots.append(SlotPlan(days=[parse_day(d) for d in s["days"]], meal=MealType(s["meal"]),
                              dish_id=changes.get(key, s["candidates"][0])))
    return Composition(slots=slots)


def save_plan(service: DietService, repo, comp: Composition, title: str) -> PlanRecord:
    out = WeekPlanner(repo, None, service.settings, cache=service.engine_cache).finish(comp, repair=False)
    # stary plan może mieć drobne odchylenia od kotwic białka (np. cztery jajka na śniadanie), ale nie błędy
    assert out.plan is not None and out.report.ok, [i.message for i in out.report.errors()]
    rec = PlanRecord(plan=out.plan, title=title, source="algorytm", ok=out.ok, report=out.report,
                     composition=comp.to_dict(), log=out.log, week_start=date(2026, 10, 19),
                     assumptions_hash=assumptions_hash(repo), snapshot=capture(repo, out.plan))
    service.store.save(rec)
    service.store.set_setting("active_plan", rec.id)
    return rec


def copy_of(service: DietService, rec: PlanRecord, plan=None) -> PlanRecord:
    """Osobna kopia planu dla testu (inne testy tworzą wersje pochodne oryginału)."""
    new = PlanRecord(plan=plan or rec.plan, title=rec.title, source=rec.source, ok=rec.ok, report=rec.report,
                     composition=rec.composition, log=rec.log, week_start=rec.week_start,
                     assumptions_hash=rec.assumptions_hash, snapshot=rec.snapshot or service.store.snapshot(rec.id))
    service.store.save(new)
    return new


def old_data(repo):
    """Dane jak w instalacji domowników przed 0.5.1: suche curry, limit sześciu jajek na posiłek i śniadanie, w którym
    silnik dawał cztery jajka na miękko (tu: przepis z czterema jajkami)."""
    curry = repo.dishes[CURRY]
    ingredients = [i.model_copy(update=dict(zip(("typical_g", "min_g", "max_g"), OLD_CURRY[i.product_id],
                                                strict=True)))
                   if i.product_id in OLD_CURRY else i for i in curry.ingredients]
    old_curry = curry.model_copy(update={"ingredients": ingredients, "ratios": []})  # migawka sprzed proporcji sosu
    eggs = repo.dishes[EGGS]
    four = eggs.model_copy(update={"ingredients": [
        i.model_copy(update={"typical_g": 200.0, "min_g": 200.0}) if i.product_id == "jajko_kurze" else i
        for i in eggs.ingredients]})
    egg = repo.products["jajko_kurze"].model_copy(update={"max_portion_g": 300.0})
    return replace(repo, dishes={**repo.dishes, CURRY: old_curry, EGGS: four},
                   products={**repo.products, "jajko_kurze": egg})


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    home = tmp_path_factory.mktemp("dietbot_proporcje")
    for part in ("dane/produkty", "dane/dania", "dane/korekty", "dane/sklepy", "konfiguracja"):
        shutil.copytree(ROOT / part, home / part)
    (home / "wyniki").mkdir()
    service = DietService(Paths(home), use_llm=False)
    old = save_plan(service, old_data(service.repo), week({"poniedzialek_obiad": CURRY, "czwartek_sniadanie": EGGS}),
                    "Tydzień 19–25 października")
    current = save_plan(service, service.repo, week({"poniedzialek_obiad": CURRY}), "Tydzień bieżący")
    fake = FakeLLM()
    service.use_llm = True
    service._llm = fake  # noqa: SLF001 — test podmienia klienta LLM
    return service, fake, old, current


def finished(service: DietService, reply: dict) -> PlanRecord:
    job = service.jobs.wait(reply["data"]["job_id"], timeout=300)
    assert job.status is JobStatus.DONE, (job.error, job.log)
    return service.store.get(job.result["plan_id"])


def unchanged_elsewhere(before: PlanRecord, after: PlanRecord, days: set[int]) -> bool:
    return [m for m in before.plan.meals if int(m.day) not in days] == \
        [m for m in after.plan.meals if int(m.day) not in days]


def test_case1_more_coconut_milk_less_tofu_same_dish(env):
    """„Curry z tofu… za mało mleczka, dodaj, a za dużo tofu i ciecierzycy” — wcześniej czat zamieniał danie na
    prawie identyczne curry, a gramatury się nie zmieniały."""
    service, fake, old, _ = env
    rec = copy_of(service, old)
    assert not matches_current(rec.snapshot, service.repo, rec.plan)  # plan sprzed korekty przepisu curry
    before = {p: grams_in_slot(rec.plan, "0_obiad", p) for p in ("mleczko_kokosowe", "tofu", "ciecierzyca_konserwowa")}
    message = ("Curry z tofu, ciecierzycą i ryżem — za mało mleczka, dodaj, a za dużo tofu i ciecierzycy, zrób tak, "
               "żeby nie było takie suche")

    # model wybrał zamianę dania, bez uwag o składnikach: program nie zamienia dania i nie woła modelu drugi raz
    fake.queue.append({"typ": "zamien", "tekst": "Poszukam innego curry.", "dzien": "poniedzialek", "posilek": "obiad",
                       "opis_dania": "curry z większą ilością sosu"})
    calls = len(fake.calls)
    reply = ChatAssistant(service).handle(message, rec.id)
    assert len(fake.calls) == calls + 1 and not (reply["data"] or {}).get("job_id")
    assert "danie zostanie" in reply["text"]

    curry = "Curry z tofu, ciecierzycą i ryżem"
    fake.queue.append({"typ": "proporcje", "tekst": "Dobrze, curry zostaje, zmienię proporcje.",
                       "porcje": [{"danie": curry, "produkt": "mleczko kokosowe", "kierunek": "za_malo"},
                                  {"danie": curry, "produkt": "tofu", "kierunek": "za_duzo"},
                                  {"danie": curry, "produkt": "ciecierzyca", "kierunek": "za_duzo"}]})
    reply = ChatAssistant(service).handle(message, rec.id)
    assert reply["data"]["kind"] == "proporcje", reply["text"]
    assert "mniej — tofu" in reply["text"] and "więcej — mleczko kokosowe" in reply["text"], reply["text"]
    assert "wcześniejszych przepisów" in reply["text"] and "bez kosztów AI" in reply["text"], reply["text"]
    new = finished(service, reply)

    assert new.parent_id == rec.id and new.source == "proporcje"
    assert new.plan.meal(0, MealType.LUNCH).dish_id == CURRY  # to samo danie
    after = {p: grams_in_slot(new.plan, "0_obiad", p) for p in before}
    for person in before["tofu"]:
        assert after["mleczko_kokosowe"][person] > before["mleczko_kokosowe"][person], (before, after)
        assert after["tofu"][person] < before["tofu"][person], (before, after)
    assert sum(after["ciecierzyca_konserwowa"].values()) < sum(before["ciecierzyca_konserwowa"].values())
    assert service.checked_record(new).ok, [i.message for i in service.checked_record(new).report.errors()]
    assert unchanged_elsewhere(rec, new, {0})
    stored = {(e["danie"], e["produkt"]) for e in new.composition["korekty_porcji"]}
    assert {(CURRY, "mleczko_kokosowe"), (CURRY, "tofu")} <= stored
    assert any("mleczko kokosowe" in line and "→" in line for line in new.log)


def test_case2_verify_gives_culinary_findings_and_proposal(env):
    """„Zweryfikuj ten plan pod kątem gramatur… za dużo tofu… dla kobiety tylko 80 g kurczaka” — wcześniej czat
    pokazał tylko średnie kcal i makro, a potem obiecał plan, którego nie zrobił."""
    service, fake, _, current = env
    # talerz kobiety z podwójnym tofu: weryfikacja ma to nazwać, a nie podać sumy dnia
    plan = current.plan.model_copy(deep=True)
    for portion in plan.meal(0, MealType.LUNCH).portions:
        if portion.person_id == "kobieta":
            for item in portion.items:
                if item.product_id == "tofu":
                    item.grams *= 2
    rec = copy_of(service, current, plan)

    fake.queue.append({"typ": "pokaz", "tekst": "Sprawdzam plan.", "fakty": [{"rodzaj": "tydzien"}],
                       "porcje": [{"dzien": "poniedzialek", "posilek": "obiad", "produkt": "tofu",
                                   "kierunek": "za_duzo"},
                                  {"dzien": "poniedzialek", "posilek": "kolacja", "produkt": "kurczak",
                                   "kierunek": "za_malo", "osoba": "kobieta"}]})
    reply = ChatAssistant(service).handle(
        "Czy możesz zweryfikować ten plan pod kątem gramatur? 2 kostki tofu na obiad to za dużo, a dla kobiety "
        "tylko 80 g kurczaka", rec.id)
    text = reply["text"]
    assert "Proporcje talerzy względem przepisów" in text and "Średnio na dzień" not in text
    tofu_line = next(line for line in text.splitlines() if line.startswith("- Poniedziałek — obiad"))
    assert "tofu" in tofu_line and "przepis ≈" in tofu_line and "+" in tofu_line
    assert "Mogę poprawić" in text and "Poprawić?" in text and "bez kosztów AI" in text
    assert reply["data"]["kind"] == "potwierdz_akcje" and not reply["data"].get("job_id")
    assert service.store.children(rec.id) == set()  # nic nie zmieniono bez zatwierdzenia

    chicken_before = grams_in_slot(rec.plan, "0_kolacja", "piers_z_kurczaka")
    tofu_before = grams_in_slot(rec.plan, "0_obiad", "tofu")
    done = ChatAssistant(service).confirm(reply["data"]["proposal_id"])
    new = finished(service, done)
    assert new.parent_id == rec.id and new.source == "proporcje"
    assert grams_in_slot(new.plan, "0_kolacja", "piers_z_kurczaka")["kobieta"] > chicken_before["kobieta"]
    assert sum(grams_in_slot(new.plan, "0_obiad", "tofu").values()) < sum(tofu_before.values())
    assert service.checked_record(new).ok, [i.message for i in service.checked_record(new).report.errors()]
    assert "wygasła" in ChatAssistant(service).confirm(reply["data"]["proposal_id"])["text"]


def test_case3_four_soft_boiled_eggs(env):
    """„4 jajka na miękko na śniadanie” — bez dnia: program znajduje śniadanie z tym daniem i zmniejsza jajka."""
    service, fake, old, _ = env
    rec = copy_of(service, old)
    eggs_before = grams_in_slot(rec.plan, "3_sniadanie", "jajko_kurze")
    assert max(eggs_before.values()) >= 200  # cztery jajka (lub więcej) na talerzu
    fake.queue.append({"typ": "proporcje", "tekst": "Zmniejszę liczbę jajek.",
                       "porcje": [{"posilek": "sniadanie", "danie": "jajka na miękko", "produkt": "jajka",
                                   "kierunek": "za_duzo"}]})
    reply = ChatAssistant(service).handle("4 jajka na miękko na śniadanie", rec.id)
    assert "Czwartek — śniadanie" in reply["text"] and "jajko kurze" in reply["text"]
    new = finished(service, reply)
    eggs_after = grams_in_slot(new.plan, "3_sniadanie", "jajko_kurze")
    most = max(eggs_before, key=eggs_before.get)
    assert eggs_after[most] < eggs_before[most], (eggs_before, eggs_after)
    assert all(g % 50 == 0 for g in eggs_after.values())  # całe jajka
    assert new.plan.meal(3, MealType.BREAKFAST).dish_id == EGGS
    assert service.checked_record(new).ok and unchanged_elsewhere(rec, new, {3})


def test_correction_kept_when_swapping_another_meal_that_day(env):
    """Zamiana śniadania liczy poniedziałek od nowa — poprawione curry nie może wrócić do starego tofu."""
    service, _, _, current = env
    rec = copy_of(service, current)
    fix = {"slot": "0_obiad", "produkt": "tofu", "kierunek": "za_duzo", "osoba": None}
    fixed = finished(service, {"data": {"job_id": service.adjust_portions(rec.id, [fix]).id}})
    entry = next(e for e in fixed.composition["korekty_porcji"] if e["produkt"] == "tofu")
    option = next(o for o in service.swap_options(fixed.id, "0_sniadanie")["options"] if not o["notes"])
    swapped = finished(service, {"data": {"job_id": service.swap(fixed.id, "0_sniadanie", option["id"]).id}})
    assert swapped.plan.meal(0, MealType.BREAKFAST).dish_id == option["id"]
    assert swapped.composition["korekty_porcji"] == fixed.composition["korekty_porcji"]
    scale = {p.id: p.energy_center / service.settings.reference_kcal for p in service.repo.assumptions.persons}
    for person, grams in grams_in_slot(swapped.plan, "0_obiad", "tofu").items():
        assert grams <= entry["max_g"] * scale[person] + 1e-6


def test_stale_proposal_is_refused(env):
    service, fake, _, current = env
    rec = copy_of(service, current)
    fake.queue.append({"typ": "sprawdz_porcje", "tekst": "Sprawdzam.",
                       "porcje": [{"dzien": "poniedzialek", "posilek": "obiad", "produkt": "ryż", "kierunek": "za_duzo"}]})
    reply = ChatAssistant(service).handle("sprawdź porcje, ryżu za dużo w poniedziałek", rec.id)
    assert reply["data"]["kind"] == "potwierdz_akcje"
    # w międzyczasie powstała nowsza wersja planu (np. zamiana w oknie aplikacji)
    service.store.save(PlanRecord(plan=rec.plan, title=rec.title, source="zamiana", ok=True, parent_id=rec.id,
                                  snapshot=rec.snapshot))
    done = ChatAssistant(service).confirm(reply["data"]["proposal_id"])
    assert "zmienił się w międzyczasie" in done["text"] and not (done["data"] or {}).get("job_id")


def test_swap_request_still_swaps(env):
    """„Coś mniej tłustego” to prośba o inne danie — straż przed zamianą nie może jej przechwycić."""
    service, fake, _, current = env
    rec = copy_of(service, current)
    option = next(o for o in service.swap_options(rec.id, "3_kolacja")["options"] if not o["notes"])
    fake.queue.append({"typ": "zamien", "tekst": "Dobrze.", "dzien": "czwartek", "posilek": "kolacja",
                       "opis_dania": "coś lżejszego"})
    fake.queue.append({"danie": option["id"], "uzasadnienie": "Lżejsze."})
    reply = ChatAssistant(service).handle("w czwartek na kolację coś mniej tłustego", rec.id)
    assert reply["data"]["kind"] == "zamiana" and fake.calls[-1][0] == "czat_zamiana"
    service.jobs.wait(reply["data"]["job_id"], timeout=300)


def test_unknown_ingredient_explains_without_job(env):
    service, fake, _, current = env
    rec = copy_of(service, current)
    fake.queue.append({"typ": "proporcje", "tekst": "Sprawdzę.",
                       "porcje": [{"dzien": "poniedzialek", "posilek": "obiad", "produkt": "kapusta kiszona",
                                   "kierunek": "za_duzo"}]})
    reply = ChatAssistant(service).handle("w poniedziałkowym obiedzie za dużo kapusty", rec.id)
    assert "nie znalazłem składnika" in reply["text"] and not (reply["data"] or {}).get("job_id")


def test_ingredient_matching_and_findings(repo):
    curry = repo.dishes[CURRY]
    assert ingredient_ids("mleczka", curry, repo.products) == ["mleczko_kokosowe"]
    assert ingredient_ids("ciecierzycy", curry, repo.products) == ["ciecierzyca_konserwowa"]
    assert ingredient_ids("80 g tofu", curry, repo.products) == ["tofu"]
    assert set(ingredient_ids("sosu", curry, repo.products)) == {"mleczko_kokosowe", "pomidory_z_puszki"}
    assert ingredient_ids("łosoś", curry, repo.products) == []

    entry = {"danie": CURRY, "produkt": "tofu", "typowo_g": 130.0, "min_g": 90.0, "max_g": 110.0}
    tofu = next(i for i in apply_corrections(repo.dishes, [entry])[CURRY].ingredients if i.product_id == "tofu")
    assert tofu.max_g == 110.0
    # przepis od tego czasu poprawiony (inna typowa porcja) — stara korekta nie obowiązuje
    assert apply_corrections(repo.dishes, [{**entry, "typowo_g": 180.0}]) == {}

    out = WeekPlanner(repo, None).finish(week({"poniedzialek_obiad": CURRY}), repair=False)
    deviations, dry = plate_findings(out.plan, repo.dishes, repo.products, repo.assumptions, 2000.0)
    assert all(abs(f.ratio - 1) > 0.5 for f in deviations)
    lunch = out.plan.meal(0, MealType.LUNCH)
    for portion in lunch.portions:  # wysuszony talerz: sos znika, ryż zostaje
        for item in portion.items:
            if item.product_id in ("mleczko_kokosowe", "pomidory_z_puszki"):
                item.grams = round(item.grams * 0.3)
    _, dry = plate_findings(out.plan, repo.dishes, repo.products, repo.assumptions, 2000.0)
    assert {f.slot for f in dry} >= {"0_obiad"}
