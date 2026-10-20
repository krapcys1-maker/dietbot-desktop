"""Notatnik preferencji: zasady (zakaz, limit porcji, zamiana makro, wskazówka), ich wpływ na silnik, pule
i weryfikator oraz analiza uwag z udawanym modelem językowym (bez kosztów)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dietbot.app.chat import ChatAssistant
from dietbot.app.jobs import JobStatus, UserError
from dietbot.app.notebook import Notebook
from dietbot.app.service import DietService
from dietbot.checker import verify_plan
from dietbot.domain.dish import MealType
from dietbot.domain.preferences import PreferenceRule, apply_preferences, default_macro_grams
from dietbot.engine import EngineRequest, solve_week
from dietbot.llm.client import LLMResult
from dietbot.planner.pools import build_pools
from dietbot.storage.paths import Paths

ROOT = Path(__file__).resolve().parents[1]
TERIYAKI = "kurczak_teriyaki_z_ryzem_i_brokulami"
DAY = [{"days": [2], "meal": "sniadanie", "candidates": ["twarozek_z_zatar_i_warzywami"]},
       {"days": [2], "meal": "obiad", "candidates": [TERIYAKI]},
       {"days": [2], "meal": "kolacja", "candidates": ["kanapki_z_pasta_jajeczna_i_szczypiorkiem"]}]
# dokładna uwaga domownika z 4.10.2026 (z planem obiadu wklejonym w skrócie)
USER_NOTE = ("W diecie czasami jest zbyt duzo weglowodanow na obiad przykład: Kawałki piersi z kurczaka w słono-słodkiej "
             "glazurze, Ryż biały (suchy) 75 g 115 g. 75 i 115 gr suchego ryzu to lacznie 190 gram wolal bym, np 65 i 105 "
             "ryzu max a w zamian za to wiecej bilka moze te zwiekszyc i z weglowodanami moze sobie to obliczyc. "
             "NIwe stosuj samych bialek z jajek np jajecznic a z jajek plus jakies bialko z jajek tego nie robiomy. "
             "W liscie zakupow podaje mi - [ ] Jogurt — 410 g, tego nie chce bo wrzucam sobie to w notes.")


def _rules(**kw) -> list[PreferenceRule]:
    return [PreferenceRule.model_validate({"id": f"r{i}", **r}) for i, r in enumerate(kw["rules"])]


def _grams(plan, meal: MealType, product_id: str) -> dict[str, float]:
    m = next(x for x in plan.meals if x.meal is meal)
    return {p.person_id: sum(i.grams for i in p.items if i.product_id == product_id) for p in m.portions}


def test_overlay_keeps_original_assumptions(repo):
    a = repo.assumptions
    before = a.model_dump(mode="json")
    rules = _rules(rules=[
        {"typ": "zakaz_produktu", "produkt": "bialka_jaj"},
        {"typ": "limit_porcji", "kategoria": "ryz", "gramy": {"kobieta": 65, "mezczyzna": 105}, "posilki": ["obiad"]},
        {"typ": "zamiana_makro", "z": "weglowodany", "na": "bialko", "gramy": default_macro_grams(a, "weglowodany")},
        {"typ": "wskazowka", "tekst": "mniej dań z jajkami na kolację"},
        {"typ": "zakaz_produktu", "produkt": "rukola", "aktywna": False},
    ])
    out = apply_preferences(a, rules, repo.products)
    assert a.model_dump(mode="json") == before  # plik założeń i obiekt wejściowy bez zmian
    eff = out.assumptions
    assert "bialka_jaj" in eff.prohibitions.products
    rice = repo.products["ryz_bialy"]
    assert eff.person("kobieta").portion_cap(rice, MealType.LUNCH) == 65
    assert eff.person("mezczyzna").portion_cap(repo.products["ryz_parboiled"], MealType.LUNCH) == 105
    assert eff.person("kobieta").portion_cap(rice, MealType.BREAKFAST) is None  # limit tylko na obiad
    assert eff.person("kobieta").portion_cap(repo.products["makaron_pszenny"], MealType.LUNCH) is None
    for p in eff.persons:
        raw = a.person(p.id).targets
        shift = default_macro_grams(a, "weglowodany")[p.id]
        assert p.targets.carbs.min == raw.carbs.min - shift and p.targets.carbs.max == raw.carbs.max
        assert p.targets.protein.max == raw.protein.max + shift and p.targets.protein.min == raw.protein.min
        assert p.targets.kcal == raw.kcal  # energia bez zmian — zamiana gram za gram (4 kcal/g)
        assert p.targets.carbs.center < raw.carbs.center and p.targets.protein.center > raw.protein.center
    assert out.hints == ["mniej dań z jajkami na kolację"]
    assert len(out.effects) == 3 and not out.problems  # wskazówki są osobno (hints)


def test_engine_respects_rice_cap_and_checker_agrees(repo):
    req = EngineRequest.model_validate({"slots": DAY})
    base = solve_week(req, repo.assumptions, repo.products, repo.dishes)
    caps = {"kobieta": 55, "mezczyzna": 90}
    eff = apply_preferences(repo.assumptions, _rules(rules=[
        {"typ": "limit_porcji", "kategoria": "ryz", "gramy": caps}]), repo.products).assumptions
    res = solve_week(req, eff, repo.products, repo.dishes)
    assert res.plan is not None
    rice = _grams(res.plan, MealType.LUNCH, "ryz_bialy")
    assert all(rice[p] <= caps[p] + 1e-6 for p in caps), rice
    assert _grams(base.plan, MealType.LUNCH, "ryz_bialy")["mezczyzna"] > caps["mezczyzna"]  # limit coś zmienił
    report = verify_plan(res.plan, eff, repo.products, repo.dishes)
    assert "limit_porcji" not in report.codes()
    # ten sam plan bez limitu sprawdzony z limitem — weryfikator zgłasza przekroczenie
    old = verify_plan(base.plan, eff, repo.products, repo.dishes)
    assert "limit_porcji" in old.codes()


def test_ban_and_cap_conflicts_shrink_pools(repo):
    a = repo.assumptions
    plain = build_pools(repo.approved_dishes(), repo.products, a)
    users = [d for d in plain.profiles if any(i.product_id == "bialka_jaj" and not i.optional
                                               for i in repo.dishes[d].ingredients)]
    assert users
    eff = apply_preferences(a, _rules(rules=[{"typ": "zakaz_produktu", "produkt": "bialka_jaj"}]), repo.products)
    pools = build_pools(repo.approved_dishes(), repo.products, eff.assumptions)
    assert not any(d in ids for ids in pools.by_meal.values() for d in users)
    # limit mniejszy niż jedno jajko: dania z jajkiem „na sztuki” odpadają tylko w tym posiłku
    tiny = apply_preferences(a, _rules(rules=[{"typ": "limit_porcji", "produkt": "jajko_kurze", "gramy": {"kobieta": 20},
                                               "posilki": ["sniadanie"]}]), repo.products).assumptions
    pools = build_pools(repo.approved_dishes(), repo.products, tiny)
    piece = [d for d in plain.by_meal[MealType.BREAKFAST] if any(
        i.product_id == "jajko_kurze" and i.unit == "szt" and not i.optional for i in repo.dishes[d].ingredients)]
    assert piece and not set(piece) & set(pools.by_meal[MealType.BREAKFAST])
    assert any(d in pools.by_meal[MealType.SUPPER] for d in plain.by_meal[MealType.SUPPER]
               if any(i.product_id == "jajko_kurze" for i in repo.dishes[d].ingredients))
    assert any("limit porcji" in " ".join(r) for r in pools.excluded.values())


# ---------------------------------------------------------------- notatnik w aplikacji (udawany model)
class FakeLLM:
    def __init__(self):
        self.queue: list = []
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, task, system, user, schema, *, temperature=None, max_tokens=None):
        self.calls.append((task, user))
        value = self.queue.pop(0)
        return LLMResult(value=schema.model_validate(value), attempts=1, cost_usd=0.0, prompt_tokens=0,
                         completion_tokens=0, seconds=0.0)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    home = tmp_path_factory.mktemp("dietbot_notatnik")
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


def test_user_note_becomes_rules_numbers_from_text_or_code(env):
    service, fake = env
    raw_file = (service.paths.assumptions).read_bytes()
    fake.queue.append({
        "podsumowanie": "Mniej ryżu na obiad i więcej białka; bez samych białek jaj; lista zakupów bez kratek.",
        "zasady": [
            {"typ": "limit_porcji", "kategoria": "ryz", "gramy": {"kobieta": 65, "mezczyzna": 105},
             "posilki": ["obiad"], "cytat": "np 65 i 105 ryzu max"},
            {"typ": "zamiana_makro", "z": "weglowodany", "na": "bialko", "cytat": "w zamian więcej białka"},
            {"typ": "zakaz_produktu", "produkt": "białka jaj", "cytat": "nie stosuj samych białek z jajek"},
            # liczba spoza uwagi — kod jej nie przyjmie, limit policzy sam
            {"typ": "limit_porcji", "kategoria": "makaron", "gramy": {"kobieta": 70}, "posilki": ["obiad"],
             "cytat": "za dużo węglowodanów na obiad"},
        ],
        "do_poprawy": ["Lista zakupów: bez „- [ ]” przed pozycjami — notatki w telefonie same dodają pola do odhaczania."],
    })
    out = Notebook(service).add_comment(USER_NOTE)
    a = out["analysis"]
    assert len(a["rules_added"]) == 4, a
    assert any("65 g (Kobieta) i 105 g (Mężczyzna)" in r["description"] for r in a["rules_added"])
    assert any("białka jaj" in r["description"] for r in a["rules_added"])
    assert any("pominąłem liczby" in p for p in a["problems"])
    pasta = next(r for r in Notebook(service).rules() if r.category and r.category.value == "makaron")
    # limit policzył kod (największa porcja z ostatnich planów × 0,85) — może przypadkiem wyjść też 70 g
    expected = Notebook(service)._default_cap(None, "makaron", "kobieta", [MealType.LUNCH])  # noqa: SLF001
    assert pasta.grams["kobieta"] == expected and set(pasta.grams) == {"kobieta", "mezczyzna"}
    assert out["program_notes"][0]["text"].startswith("Lista zakupów")
    # zasady działają od razu w danych aplikacji, a plik założeń jest nietknięty
    eff = service.repo.assumptions
    assert "bialka_jaj" in eff.prohibitions.products
    assert eff.person("kobieta").portion_cap(service.repo.products["ryz_bialy"], MealType.LUNCH) == 65
    assert service.raw_assumptions.person("kobieta").portion_caps == []
    assert service.paths.assumptions.read_bytes() == raw_file
    assert out["targets"] and service.repo.preference_hints == []
    task, prompt = fake.calls[-1]
    assert task == "notatnik_uwaga" and "UWAGA DOMOWNIKA" in prompt and "Kobieta (id: kobieta)" in prompt
    # nowy plan stosuje zasady: ryż na obiad w limicie, żadnych białek jaj
    service.use_llm = False
    try:
        job = service.jobs.wait(service.generate(use_llm=False, seed=21).id, timeout=600)
    finally:
        service.use_llm = True
    assert job.status is JobStatus.DONE, job.error
    rec = service.store.get(job.result["plan_id"])
    caps = {"kobieta": 65, "mezczyzna": 105}
    for m in rec.plan.meals:
        for p in m.portions:
            for it in p.items:
                assert it.product_id != "bialka_jaj"
                if m.meal is MealType.LUNCH and service.repo.products[it.product_id].category.value == "ryz":
                    assert it.grams <= caps[p.person_id] + 1e-6
    assert "limit_porcji" not in service.checked_record(rec).report.codes()


def test_new_rule_replaces_old_and_can_be_switched_off(env):
    service, fake = env
    nb = Notebook(service)
    before = len(nb.rules())
    fake.queue.append({"podsumowanie": "Żonie jeszcze mniej ryżu.",
                       "zasady": [{"typ": "limit_porcji", "kategoria": "ryz", "gramy": {"kobieta": 60},
                                   "posilki": ["obiad"], "osoby": ["kobieta"], "cytat": "żonie 60 g ryżu"}]})
    nb.add_comment("Żonie dawaj 60 g ryżu na obiad, nie więcej.")
    rice = [r for r in nb.rules() if r.category and r.category.value == "ryz"]
    # nowy limit przejął tylko kobietę; limit mężczyzny z poprzedniej zasady został
    assert len(nb.rules()) == before + 1 and len(rice) == 2
    eff = service.repo.assumptions
    assert eff.person("kobieta").portion_cap(service.repo.products["ryz_bialy"], MealType.LUNCH) == 60
    assert eff.person("mezczyzna").portion_cap(service.repo.products["ryz_bialy"], MealType.LUNCH) == 105
    ban = next(r for r in nb.rules() if r.product_id == "bialka_jaj")
    nb.set_active(ban.id, False)
    assert "bialka_jaj" not in service.repo.assumptions.prohibitions.products
    nb.set_active(ban.id, True)
    assert "bialka_jaj" in service.repo.assumptions.prohibitions.products
    with pytest.raises(UserError):
        nb.delete_rule("nie-ma-takiej")


def test_manual_rules_without_llm_and_dish_rating(env):
    service, fake = env
    nb = Notebook(service)
    view = nb.manual_rule({"typ": "wskazowka", "tekst": "lubimy kuchnię azjatycką"})
    assert any(r["description"] == "Wskazówka: lubimy kuchnię azjatycką" for r in view["rules"])
    assert "lubimy kuchnię azjatycką" in service.repo.preference_hints
    view = nb.manual_rule({"typ": "limit_porcji", "produkt": "ziemniaki", "posilki": ["obiad"]})
    potato = next(r for r in nb.rules() if r.product_id == "ziemniaki")
    assert set(potato.grams) == {"kobieta", "mezczyzna"} and all(g >= 5 for g in potato.grams.values())
    with pytest.raises(UserError):
        nb.manual_rule({"typ": "limit_porcji", "produkt": "nie_ma_takiego", "gramy": {"kobieta": 50}})
    rec = service.store.active()
    dish = service.repo.dishes[rec.plan.meals[0].dish_id]
    fake.queue.append({"podsumowanie": "To danie nie smakuje.", "dania": [{"danie": dish.name, "ocena": "nie_lubie"}]})
    out = nb.add_comment(f"{dish.name} nam nie smakuje, nie dawaj go więcej.")
    assert out["analysis"]["ratings"] == [{"dish_id": dish.id, "name": dish.name, "rating": -1}]
    assert service.repo.dish_ratings[dish.id] == -1
    service.rate_dish(dish.id, 0)


def test_chat_remember_goes_to_notebook_and_habits(env):
    service, fake = env
    fake.queue.append({"typ": "zapamietaj", "tekst": "Zapisuję.", "zyczenia": "Mniej dań z jajkami na kolację."})
    fake.queue.append({"podsumowanie": "Mniej jajek na kolację.",
                       "zasady": [{"typ": "wskazowka", "tekst": "mniej dań z jajkami na kolację", "cytat": "mniej jajek"}]})
    reply = ChatAssistant(service).handle("Zapamiętaj: mniej dań z jajkami na kolację.")
    assert "notatniku" in reply["text"] and "Wskazówka: mniej dań z jajkami na kolację" in reply["text"]
    assert reply["data"]["kind"] == "notatnik"
    nb = Notebook(service)
    signals = nb.habit_signals()
    assert signals["counts"]["notes"] >= 3
    fake.queue.append({"obserwacje": ["Wolicie mniej węglowodanów w obiedzie i więcej mięsa."],
                       "propozycje": [{"typ": "zakaz_produktu", "kategoria": "ryby", "cytat": "x"}],
                       "do_poprawy": ["Pokazywać, z której uwagi powstała zasada."]})
    view = nb.analyze_habits()
    habits = view["habits"]
    assert habits["observations"] and habits["proposals"][0]["description"].startswith("Bez: ryby")
    assert not any(r.category and r.category.value == "ryby" for r in nb.rules())  # propozycja to jeszcze nie zasada
    view = nb.apply_proposal(habits["proposals"][0]["id"])
    assert view["habits"]["proposals"][0]["applied"]
    assert any(r["description"].startswith("Bez: ryby") for r in view["rules"])
    view = nb.add_program_note_from_habits(0)
    assert any(n["text"].startswith("Pokazywać") for n in view["program_notes"])
    text = nb.export_text()
    assert "ZASADY STOSOWANE W PLANACH" in text and "DO POPRAWY W PROGRAMIE" in text and "Lista zakupów" in text


def test_note_digits_rejected_in_summary():
    from pydantic import ValidationError

    from dietbot.app.notebook import NoteAnalysis

    with pytest.raises(ValidationError):
        NoteAnalysis.model_validate({"podsumowanie": "Limit 65 g ryżu."})


def test_shopping_text_has_no_checkboxes(env):
    service, _ = env
    rec = service.store.active()
    content = service.text_export(rec.id, "shopping")["content"]
    assert "[ ]" not in content
    items = [line for line in content.splitlines() if " — " in line]
    assert items and not any(line.startswith(("- ", "* ")) for line in items)


def test_repair_replaces_dishes_that_no_longer_fit_rules(repo):
    """„Przelicz i napraw” po nowej zasadzie: danie z zakazanym produktem wymienione przed liczeniem gramatur."""
    from dataclasses import replace

    from dietbot.app.service import composition_from_plan
    from dietbot.planner.pipeline import WeekPlanner

    whites = next(d for d, dish in sorted(repo.approved_dishes().items()) if MealType.SUPPER in dish.meal_types
                  and any(i.product_id == "bialka_jaj" and not i.optional for i in dish.ingredients)
                  and not dish.is_treat and not dish.is_sweet)
    slots = [{**s, "candidates": [whites]} if s["meal"] == "kolacja" else s for s in DAY]
    plan = solve_week(EngineRequest.model_validate({"slots": slots}), repo.assumptions, repo.products, repo.dishes).plan
    eff = apply_preferences(repo.assumptions, _rules(rules=[{"typ": "zakaz_produktu", "produkt": "bialka_jaj"}]),
                            repo.products).assumptions
    planner = WeekPlanner(replace(repo, assumptions=eff))
    log: list[str] = []
    out = planner.finish(composition_from_plan(plan), log, repair=True)
    supper = next(m for m in out.plan.meals if m.meal is MealType.SUPPER)
    assert supper.dish_id != whites
    assert not any(i.product_id == "bialka_jaj" for p in supper.portions for i in p.items)
    assert any(line.startswith("Zamiana na dozwolone danie") and "białka jaj" in line for line in log), log
    assert "zakazany_produkt" not in out.report.codes()
