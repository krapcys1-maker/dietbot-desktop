"""Regresje dla ustaleń audytu z 09.10.2026 (docs/AUDYT_2026-10-09.md): K1, W1–W6, S1–S3, S5, S7–S10, S12, S14, N."""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from dietbot.app.api import create_app
from dietbot.app.jobs import JobManager, JobStatus, UserError
from dietbot.app.service import DietService, unique_id
from dietbot.checker import verify_plan
from dietbot.domain.dish import MealType
from dietbot.domain.plan import PlanItem, PlannedSnack, WeekPlan
from dietbot.engine import EngineRequest, solve_week
from dietbot.llm import scope
from dietbot.llm.client import BudgetExceeded, CostLedger, LLMClient, LLMError, LLMSettings, save_dotenv_value
from dietbot.storage.paths import Paths
from dietbot.storage.repository import DataError, Repository
from dietbot.storage.yamlio import dump_yaml, load_yaml

ROOT = Path(__file__).resolve().parents[1]
DAY = [{"days": [2], "meal": "sniadanie", "candidates": ["twarozek_z_zatar_i_warzywami"]},
       {"days": [2], "meal": "obiad", "candidates": ["kurczak_teriyaki_z_ryzem_i_brokulami"]},
       {"days": [2], "meal": "kolacja", "candidates": ["kanapki_z_pasta_jajeczna_i_szczypiorkiem"]}]


def _home(tmp_path: Path) -> Path:
    for part in ("dane/produkty", "dane/dania", "dane/sklepy", "dane/korekty", "konfiguracja"):
        shutil.copytree(ROOT / part, tmp_path / part)
    (tmp_path / "wyniki").mkdir()
    return tmp_path


@pytest.fixture()
def service(tmp_path):
    s = DietService(Paths(_home(tmp_path)), use_llm=False)
    yield s
    s.jobs.shutdown()


# ---------------------------------------------------------------- K1
def test_unique_store_ids_and_app_survives_bad_user_file(service):
    assert unique_id("a" * 70, set()) == "a" * 60
    assert unique_id("a" * 70, {"a" * 60}) == "a" * 58 + "_2"
    base = {"category": "sery", "kcal": 356, "protein": 25, "fat": 28, "carbs": 0.1, "store": "Kaufland RO",
            "brand": "Hochland Sandwich Scheiben"}
    service.add_product({**base, "name": "Ser żółty Gouda w plastrach młody 45% tłuszczu"})
    service.add_product({**base, "name": "Ser żółty Gouda w plastrach dojrzewający 45% tłuszczu"})
    own = [e["id"] for e in load_yaml(service.paths.stores_dir / "wlasne.yaml")]
    assert len(own) == 2 and len(set(own)) == 2  # wcześniej: to samo id i aplikacja przestawała się uruchamiać
    DietService(service.paths, use_llm=False).jobs.shutdown()  # kolejny start działa
    # zduplikowany wpis wpisany ręcznie: aplikacja startuje i ostrzega, tryb ścisły (CLI) zgłasza błąd
    doc = load_yaml(service.paths.stores_dir / "wlasne.yaml")
    doc.append(dict(doc[0]))
    dump_yaml(doc, service.paths.stores_dir / "wlasne.yaml")
    fresh = DietService(service.paths, use_llm=False)
    assert any("zduplikowane id" in w for w in fresh.status()["data_warnings"])
    fresh.jobs.shutdown()
    with pytest.raises(DataError):
        Repository.load(service.paths)


def test_add_product_is_rolled_back_when_new_state_is_invalid(service, monkeypatch):
    before = (service.paths.products_dir / "wlasne.yaml").exists()
    real = Repository.load

    def broken(paths, use_cache=True, strict=True):
        repo = real(paths, use_cache, strict)
        repo.load_problems.append("wlasne.yaml: zduplikowane id produktu 'x' — pominięto ten wpis")
        return repo

    monkeypatch.setattr(Repository, "load", staticmethod(broken))
    with pytest.raises(UserError, match="nie zapisał się"):
        service.add_product({"name": "Testowy serek", "category": "sery", "kcal": 100, "protein": 10, "fat": 5,
                             "carbs": 3})
    assert (service.paths.products_dir / "wlasne.yaml").exists() == before


# ---------------------------------------------------------------- W1
def test_low_energy_targets_need_confirmation_and_are_flagged(service):
    data = service.assumptions()["data"]
    data["osoby"][0]["cele_dzienne"]["kcal"] = {"min": 600, "cel": 650, "max": 700}
    out = service.save_assumptions(data, expected_version=service.assumptions_version())
    assert not out["ok"] and "1200" in out["needs_confirmation"]
    out = service.save_assumptions(data, expected_version=service.assumptions_version(), confirm_low_kcal=True)
    assert out["ok"] and any("poniżej ostrożnego minimum" in w for w in out["warnings"])
    report = verify_plan(WeekPlan(days=[0], meals=[]), service.repo.assumptions, service.repo.products,
                         service.repo.dishes)
    assert "cele_ponizej_minimum" in report.codes()


def test_assumptions_save_refuses_stale_version(service):
    data = service.assumptions()["data"]
    old = service.assumptions_version()
    service.save_assumptions(data, expected_version=old)
    data["tydzien"]["slodkie_kolacje_max"] = 1
    service.save_assumptions(data)  # np. zmiana z czatu
    with pytest.raises(UserError, match="zmieniły się w międzyczasie"):
        service.save_assumptions(data, expected_version=old)


# ---------------------------------------------------------------- W2
def test_bans_reach_balancing_snacks_and_checker(repo):
    a = repo.assumptions.model_copy(deep=True)
    a.prohibitions.products = [*a.prohibitions.products, "migdaly"]
    from dataclasses import replace

    planning = replace(repo, assumptions=a).for_planning()
    assert "przekaska_migdaly" not in planning.assumptions.day.snacks.allowed
    plan = WeekPlan(days=[0], meals=[], snacks=[PlannedSnack(day=0, person_id="mezczyzna", dish_id="przekaska_migdaly",
                                                             items=[PlanItem(product_id="migdaly", grams=35)])])
    assert "zakazany_produkt" in verify_plan(plan, a, repo.products, repo.dishes).codes()
    # silnik nie bierze przekąski z zakazanym produktem, nawet gdy jest na liście dozwolonych
    a.day.snacks.allowed = ["przekaska_migdaly"]
    res = solve_week(EngineRequest.model_validate({"slots": DAY}), a, repo.products, repo.dishes)
    assert not any(s.dish_id == "przekaska_migdaly" for s in res.plan.snacks)
    # wish exclusions („bez orzechów”) też filtrują przekąski
    wishes = replace(repo, assumptions=repo.assumptions).for_planning({"migdaly"})
    assert "przekaska_migdaly" not in wishes.assumptions.day.snacks.allowed


# ---------------------------------------------------------------- W3
def test_ham_correction_applies_only_to_untouched_values(tmp_path):
    home = _home(tmp_path)
    path = home / "dane" / "produkty" / "produkty.yaml"
    text = path.read_text(encoding="utf-8")
    i = text.index("- id: szynka_parmenska")
    j = text.index("- id:", i + 5)
    old_block = text[i:j].replace("kcal: 307", "kcal: 123").replace("tluszcz: 21.8", "tluszcz: 2.08") \
        .replace("bialko: 26.72", "bialko: 24.43").replace("kwasy_nasycone: 8.07", "kwasy_nasycone: 0.71")
    path.write_text(text[:i] + old_block + text[j:], encoding="utf-8")
    repo = Repository.load(Paths(home), use_cache=False, strict=False)
    ham = repo.products["szynka_parmenska"]
    assert ham.per_100g.kcal == 307 and ham.source.code == "W441000"
    assert any("Szynka dojrzewająca" in p for p in repo.load_problems)
    # wartości zmienione przez użytkownika zostają
    path.write_text(text[:i] + old_block.replace("kcal: 123", "kcal: 250") + text[j:], encoding="utf-8")
    assert Repository.load(Paths(home), use_cache=False, strict=False).products["szynka_parmenska"].per_100g.kcal == 250


# ---------------------------------------------------------------- W5
def _client(tmp_path, monkeypatch, responses):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x" * 32)
    home = _home(tmp_path)
    client = LLMClient(paths=Paths(home))
    calls = []

    def create(**kw):
        calls.append(kw)
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))  # noqa: SLF001
    return client, calls


def _ok(content: str, usage=True):
    u = SimpleNamespace(prompt_tokens=1000, completion_tokens=200, prompt_tokens_details=None) if usage else None
    return SimpleNamespace(usage=u, choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")])


class _Answer(__import__("pydantic").BaseModel):
    ok: bool


def _errors():
    import httpx2
    import openai

    req = httpx2.Request("POST", "https://api.example.invalid/v1/chat")
    return (openai.APITimeoutError(request=req),
            openai.RateLimitError("limit", response=httpx2.Response(429, request=req), body=None),
            openai.AuthenticationError("klucz", response=httpx2.Response(401, request=req), body=None))


def test_llm_retries_breaker_and_failed_attempt_costs(tmp_path, monkeypatch):
    scope.reset_breaker()
    timeout, rate, auth = _errors()
    monkeypatch.setattr("dietbot.llm.client.RETRY_PAUSE_S", 0)
    client, calls = _client(tmp_path, monkeypatch, [rate, _ok('{"ok": true}')])
    assert client.complete_json("t", "s", "u", _Answer).value.ok and len(calls) == 2  # 429: jedna ponowna próba
    client, calls = _client(tmp_path / "b", monkeypatch, [auth, _ok('{"ok": true}')])
    with pytest.raises(LLMError, match="401"):
        client.complete_json("t", "s", "u", _Answer)
    assert len(calls) == 1  # błąd trwały (4xx) bez ponawiania
    client, calls = _client(tmp_path / "c", monkeypatch, [timeout, _ok('{"ok": true}')])
    with scope.task_scope() as sc, pytest.raises(LLMError, match="nie odpowiada"):
        client.complete_json("t", "s", "u" * 3000, _Answer)
    assert sc.spent_usd > 0  # przekroczony czas: liczymy ostrożnie tokeny wejścia
    with pytest.raises(LLMError, match="niedostępny"):  # bezpiecznik: kolejne wywołanie nie czeka znowu minutami
        client.complete_json("t", "s", "u", _Answer)
    assert len(calls) == 1
    scope.reset_breaker()


def test_task_limit_reservation_and_robust_ledger(tmp_path, monkeypatch):
    scope.reset_breaker()
    client, _ = _client(tmp_path, monkeypatch, [_ok('{"ok": true}', usage=False)] * 3)
    with scope.task_scope(limit_usd=0.00001), pytest.raises(BudgetExceeded, match="jednego polecenia"):
        client.complete_json("t", "s", "u", _Answer)
    with scope.task_scope() as sc:
        client.complete_json("t", "s", "u", _Answer)
    assert sc.spent_usd >= client.ledger.cost(1, 0, client.settings.max_tokens)  # brak „usage” = górny szacunek
    ledger_path = client.ledger.path
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write('{"koszt_usd": NaN}\n{"koszt_usd": null}\nnie-json\n{"koszt_usd": -5}\n')
    spent = client.ledger.spent_usd()
    assert spent > 0 and spent == spent  # NaN nie wyłącza limitu, uszkodzone linie nie wywracają odczytu
    # rezerwacja: równoległe wywołania nie przekroczą budżetu
    settings = LLMSettings.load(Paths(tmp_path)).model_copy(update={"budget_usd": spent + 0.015})
    ledger = CostLedger(ledger_path, settings)
    passed, refused = [], []

    def worker():
        try:
            ledger.reserve(0.01)
            passed.append(1)
        except BudgetExceeded:
            refused.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(passed) == 1 and len(refused) == 7


def test_other_model_uses_conservative_prices(tmp_path):
    settings = LLMSettings.load(Paths(_home(tmp_path)))
    other = settings.model_copy(update={"model": "inny-model", "prices_model": settings.model})
    assert not other.prices_match and other.effective_prices().output > settings.prices.output


# ---------------------------------------------------------------- W6
def test_jobs_can_be_cancelled_and_shutdown_waits():
    jobs = JobManager()
    started = threading.Event()

    def long(progress):
        started.set()
        while True:
            progress("liczę…")
            time.sleep(0.01)

    job = jobs.submit("test", "długie", long)
    started.wait(5)
    queued = jobs.submit("test", "w kolejce", lambda p: {"ok": True})
    assert jobs.shutdown(wait_s=5)
    assert job.status is JobStatus.CANCELLED and "Przerwano" in job.error
    assert queued.status in (JobStatus.CANCELLED, JobStatus.QUEUED)  # nie wykona się po zamknięciu
    with pytest.raises(UserError):
        jobs.submit("test", "po zamknięciu", lambda p: {})


# ---------------------------------------------------------------- S1, S2, S3
def test_swap_refused_when_plan_changed_meanwhile_and_status_blocked_during_job(service):
    job = service.jobs.wait(service.generate(use_llm=False, seed=5).id, timeout=600)
    rec = service.store.get(job.result["plan_id"])
    slot = "0_sniadanie"
    options = [o["id"] for o in service.swap_options(rec.id, slot)["options"] if not o["notes"]]
    gate = threading.Event()
    blocker = service.jobs.submit("test", "blokada", lambda p: gate.wait(30) and {})
    first = service.swap(rec.id, slot, options[0])
    second = service.swap(rec.id, slot, options[1])
    with pytest.raises(UserError, match="Poczekaj"):
        service.set_dish_status(options[2], "odrzucone")
    gate.set()
    service.jobs.wait(blocker.id)
    assert service.jobs.wait(first.id, timeout=300).status is JobStatus.DONE
    done = service.jobs.wait(second.id, timeout=300)
    assert done.status is JobStatus.FAILED and "zmienił się w międzyczasie" in done.error
    # odrzucenie dania w bibliotece nie blokuje zamian w zapisanej diecie (porównujemy tylko pola obliczeń)
    newest = service.store.get(service.jobs.wait(first.id).result["plan_id"])
    service.set_dish_status(newest.plan.meals[-1].dish_id, "odrzucone")
    from dietbot.app.snapshots import matches_current

    assert matches_current(service.store.snapshot(newest.id), service.repo, newest.plan)


# ---------------------------------------------------------------- S5
def test_session_token_headers_and_hosts(service):
    client = TestClient(create_app(service, session_token="tajny", docs=False), base_url="http://127.0.0.1")
    assert client.get("/api/status").status_code == 401
    r = client.get("/api/status", headers={"X-Porcja-Token": "tajny"})
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    assert client.get("/api/docs", headers={"X-Porcja-Token": "tajny"}).status_code == 404
    assert TestClient(create_app(service)).get("/api/status").status_code == 400  # Host: testserver już nie przechodzi


# ---------------------------------------------------------------- S7, S8, S9, S10
def test_duplicate_fixed_item_is_a_config_error(repo):
    from dietbot.domain.assumptions import fixed_grams
    from dietbot.storage.repository import check_consistency

    a = repo.assumptions.model_copy(deep=True)
    a.day.fixed_items = [*a.day.fixed_items, a.day.fixed_items[0].model_copy()]
    assert any("dwa razy" in e for e in check_consistency(repo.products, repo.dishes, a).errors)
    assert fixed_grams(a)[a.day.fixed_items[0].product_id] == 2 * a.day.fixed_items[0].grams


def test_checker_catches_portion_vs_recipe_mutations(repo):
    res = solve_week(EngineRequest.model_validate({"slots": DAY}), repo.assumptions, repo.products, repo.dishes)
    a, products, dishes = repo.assumptions, repo.products, repo.dishes
    assert not ({"skladnik_spoza_przepisu", "brak_skladnika_przepisu", "jednostka_przepisu"}
                & verify_plan(res.plan, a, products, dishes).codes())
    lunch = next(m for m in res.plan.meals if m.meal is MealType.LUNCH)
    portion = lunch.portions[0]

    def mutated(fn):
        plan = res.plan.model_copy(deep=True)
        meal = next(m for m in plan.meals if m.meal is MealType.LUNCH)
        fn(meal.portions[0])
        return verify_plan(plan, a, products, dishes).codes()

    chicken = next(i.product_id for i in portion.items if i.product_id == "piers_z_kurczaka")
    assert "brak_skladnika_przepisu" in mutated(lambda p: setattr(p, "items", [i for i in p.items if i.product_id != chicken]))
    assert "skladnik_spoza_przepisu" in mutated(lambda p: p.items.append(PlanItem(product_id="baton_czekoladowy", grams=50)))
    supper = next(m for m in res.plan.meals if m.meal is MealType.SUPPER)
    egg = next((i for i in supper.portions[0].items if i.product_id == "jajko_kurze"), None)
    if egg is not None and dishes[supper.dish_id].ingredients and any(
            i.product_id == "jajko_kurze" and i.unit for i in dishes[supper.dish_id].ingredients):
        plan = res.plan.model_copy(deep=True)
        meal = next(m for m in plan.meals if m.meal is MealType.SUPPER)
        next(i for i in meal.portions[0].items if i.product_id == "jajko_kurze").grams += 25  # pół jajka
        assert "jednostka_przepisu" in verify_plan(plan, a, products, dishes).codes()


def test_salt_and_saturated_fat_are_reported(repo):
    from dietbot.checker.verify import nutrition_of

    n = nutrition_of({"sol": 2.0, "szynka_parmenska": 50}, repo.products, repo.assumptions.energy.method)
    assert n["salt"] == pytest.approx(2.0 * 0.995 + 50 * 0.053, abs=0.01)  # sól kuchenna liczy się do soli
    assert n["kcal"] == pytest.approx(153.5, abs=0.1)  # …ale nie do kcal
    res = solve_week(EngineRequest.model_validate({"slots": DAY}), repo.assumptions, repo.products, repo.dishes)
    report = verify_plan(res.plan, repo.assumptions, repo.products, repo.dishes)
    assert all(t.salt > 0 and t.saturated_fat > 0 for t in report.totals)


def test_vegetarian_lists_cover_plain_plant_products(repo):
    from dietbot.app.views import dish_diets
    from dietbot.domain.diet import compatible

    for pid in ("sezam", "koperek", "kolendra", "hummus", "tortilla_pszenna", "bulka_tarta", "bulion_warzywny"):
        assert compatible(repo.products[pid], "wegan"), pid
    assert not compatible(repo.products["parmezan"], "wege")
    parm = next(d for d in repo.dishes.values() if "wege" in d.tags
                and any(i.product_id == "parmezan" for i in d.ingredients))
    assert "wege" not in dish_diets(parm, repo.products)  # plakietka z obliczenia, nie z tagu


# ---------------------------------------------------------------- S12, S14, N
def test_numbers_filters_and_polish_day_names(repo):
    from dietbot.planner.compose import WeekChoice, composition_from_llm
    from dietbot.planner.portion_review import PortionIssue
    from dietbot.textnumbers import has_numbers

    for text in ("Dodaj pół kilo ryżu", "ponad sto gramów", "Porcja ½", "Rozdział Ⅻ", "tysiąc osiemset kcal"):
        assert has_numbers(text), text
    assert not has_numbers("Lekki tydzień z rybą i warzywami.")
    issue = PortionIssue(slot="0_obiad", produkt="ryz_bialy", kierunek="za_duzo", problem="Ma 650 kcal i 45 g białka.",
                         waga="drobna")
    assert issue.problem == "porcja wygląda nietypowo"
    dish = repo.approved_dishes()
    pick = {m: next(d for d, x in dish.items() if m in x.meal_types and not x.is_batch and not x.is_treat)
            for m in (MealType.BREAKFAST, MealType.LUNCH, MealType.SUPPER)}
    choice = WeekChoice.model_validate({"komentarz": "Smacznie.", "dni": [
        {"dzien": "Środa", "sniadanie": {"danie": pick[MealType.BREAKFAST]}, "obiad": {"danie": pick[MealType.LUNCH]},
         "kolacja": {"danie": pick[MealType.SUPPER]}}]})
    comp = composition_from_llm(choice, repo.assumptions, [2])
    assert {s.dish_id for s in comp.slots} == set(pick.values())  # „Środa” z polskimi znakami nie ginie


def test_chef_failure_rejects_unreviewed_dishes(repo):
    from dietbot.llm.chef_review import review_dishes

    class Down:
        def complete_json(self, *a, **k):
            raise LLMError("brak połączenia")

    dishes = list(repo.approved_dishes().values())[:3]
    out = review_dishes(Down(), dishes, repo.products, log=lambda _: None)
    assert not out.kept and len(out.rejected) == 3


def test_wish_exclusions_that_empty_pools_are_ignored(repo):
    from dietbot.planner.pipeline import WeekPlanner
    from dietbot.planner.wishes import WishExclusions

    everything = {pid for pid, p in repo.products.items() if not p.negligible}
    log: list[str] = []
    out = WeekPlanner(repo).plan_week([0], None, 3, progress=log.append,
                                      wish_plan=WishExclusions(product_ids=set(everything)))
    assert out.plan is not None and any("pomijam je" in line for line in log)


def test_shopping_shows_variants_with_different_calories(repo):
    from dietbot.shopping import build_shopping_list

    a = repo.assumptions.model_copy(deep=True)
    a.day.fixed_items = []
    plan = WeekPlan(days=[0], meals=[], snacks=[
        PlannedSnack(day=0, person_id=a.persons[0].id, dish_id="x", items=[
            PlanItem(product_id="jogurt_naturalny_1_5", grams=90), PlanItem(product_id="jogurt_owocowy", grams=300)])])
    row = next(r for r in build_shopping_list(plan, a, repo.products) if r.name == "Jogurt")
    assert row.variants_differ and "jogurt owocowy" in row.variants_text and len(row.to_dict()["warianty"]) == 2


def test_small_fixes(tmp_path, service):
    from dietbot.app.telegram_bot import LIMIT, chunks
    from dietbot.shopping import _volume_text

    assert _volume_text(991) == "1 l" and _volume_text(250) == "250 ml"
    parts = chunks("<b>" + "słowo &amp; " * 900 + "</b>")
    assert all(p.count("<b>") == p.count("</b>") and len(p) <= LIMIT for p in parts)
    env = tmp_path / "e"
    env.mkdir()
    (env / ".env").write_text("INNY=1\nDEEPSEEK_API_KEY = stary\n", encoding="utf-8")
    save_dotenv_value(SimpleNamespace(root=env), "DEEPSEEK_API_KEY", None)
    assert "stary" not in (env / ".env").read_text(encoding="utf-8")
    client = TestClient(create_app(service), base_url="http://127.0.0.1")
    assert client.get("/api/plany/nie-ma").status_code == 404
    assert json.loads(client.post("/api/zadania/nie-ma/przerwij").text)["detail"]
