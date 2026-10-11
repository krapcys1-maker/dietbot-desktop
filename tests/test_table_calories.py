"""Regresje: kcal z tabel w całej aplikacji, migracja i niezależne sumowanie zakupów."""

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dietbot.app.api import create_app
from dietbot.app.chat import ChatAssistant
from dietbot.app.service import DietService
from dietbot.app.store import PlanRecord
from dietbot.app.telegram_bot import format_day
from dietbot.checker.verify import Issue, Severity, nutrition_of, verify_plan
from dietbot.domain.assumptions import EnergySettings, Weekday
from dietbot.domain.nutrition import EnergyMethod
from dietbot.domain.plan import PlanItem, PlannedSnack, WeekPlan
from dietbot.domain.product import StoreProduct
from dietbot.engine import BlockStatus, EngineSettings, solve_week
from dietbot.engine.catalog import Catalog
from dietbot.shopping import NOT_BOUGHT, build_detailed_shopping_list
from dietbot.storage.paths import Paths
from dietbot.storage.repository import DataError, load_assumptions
from dietbot.storage.yamlio import dump_yaml, load_yaml, to_plain
from dietbot.textfmt import fmt_num


def test_only_table_calories_are_accepted():
    assert EnergySettings().method is EnergyMethod.LABEL
    with pytest.raises(ValidationError, match="Kalorie liczymy"):
        EnergySettings(metoda="makro_4_9_4")


@pytest.mark.parametrize("legacy", [None, {}, {"metoda": "makro_4_9_4"}])
def test_upgrade_preserves_preferences_and_backs_up_once(repo, tmp_path, legacy):
    doc = load_yaml(repo.paths.assumptions)
    doc["osoby"][0]["nazwa"] = "Własna nazwa"
    if legacy is None:
        del doc["energia"]
    else:
        doc["energia"] = legacy
    path = tmp_path / "zalozenia.yaml"
    dump_yaml(doc, path)
    original = path.read_bytes()
    result = load_assumptions(path, tmp_path / "cache")
    assert result.energy.method is EnergyMethod.LABEL
    assert result.persons[0].name == "Własna nazwa"
    after = to_plain(load_yaml(path))
    assert after == {**to_plain(doc), "energia": {"metoda": "etykieta"}}
    backup = path.with_name(path.name + ".przed-kaloriami-z-tabel.bak")
    assert backup.read_bytes() == original
    stamp = path.stat().st_mtime_ns
    assert load_assumptions(path, tmp_path / "cache") == result
    assert path.stat().st_mtime_ns == stamp
    assert backup.read_bytes() == original


@pytest.mark.parametrize("method", ["nieznana", "makro_4_9_4"])
def test_invalid_config_is_not_overwritten(repo, tmp_path, method):
    doc = load_yaml(repo.paths.assumptions)
    doc["energia"] = {"metoda": method}
    if method == "makro_4_9_4":
        doc["osoby"][0]["cele_dzienne"]["kcal"]["min"] = 9999
    path = tmp_path / "zalozenia.yaml"
    dump_yaml(doc, path)
    original = path.read_bytes()
    with pytest.raises(DataError):
        load_assumptions(path)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.bak"))


def test_engine_and_checker_use_product_kcal_including_fiber(repo):
    assert repo.assumptions.energy.method is EnergyMethod.LABEL
    catalog = Catalog(repo.products, repo.assumptions.energy.method)
    discriminating = 0
    for product in repo.products.values():
        n = product.per_100g
        expected = 0 if product.negligible else n.kcal
        assert catalog.facts(product.id).kcal * 100 == pytest.approx(expected)
        assert nutrition_of({product.id: 100}, repo.products, repo.assumptions.energy.method)["kcal"] == expected
        if not product.negligible and (n.fiber or 0) > 1 and abs(n.kcal - n.energy(EnergyMethod.MACROS_4_9_4)) > 1:
            discriminating += 1
    assert discriminating > 10  # test odróżnia rzeczywistą poprawkę od zgodnych ze sobą dwóch błędnych sum


def test_whole_week_matches_independent_decimal_sums(repo, week_result):
    plan = week_result.plan
    assert plan is not None
    expected = defaultdict(lambda: defaultdict(Decimal))
    shopping = defaultdict(Decimal)

    def add(day, person, product_id, grams):
        amount = Decimal(str(grams))
        shopping[product_id] += amount
        product = repo.products[product_id]
        if not product.negligible:
            for metric in ("kcal", "protein", "fat", "carbs", "fiber"):
                expected[(day, person)][metric] += amount * Decimal(str(getattr(product.per_100g, metric) or 0)) / 100

    for meal in plan.meals:
        for portion in meal.portions:
            for item in portion.items:
                add(meal.day, portion.person_id, item.product_id, item.grams)
    for snack in plan.snacks:
        for item in snack.items:
            add(snack.day, snack.person_id, item.product_id, item.grams)
    for day in plan.days:
        for person in repo.assumptions.persons:
            for fixed in repo.assumptions.day.fixed_items:
                add(day, person.id, fixed.product_id, fixed.grams)
    report = verify_plan(plan, repo.assumptions, repo.products, repo.dishes)
    for total in report.totals:
        for metric, value in expected[(total.day, total.person_id)].items():
            assert getattr(total, metric) == pytest.approx(float(value), abs=0.011)
    rows = build_detailed_shopping_list(plan, repo.assumptions, repo.products, repo.store_products)
    assert {r.product_id for r in rows} == {pid for pid, g in shopping.items() if g > 0 and pid not in NOT_BOUGHT}
    for row in rows:
        assert row.grams == pytest.approx(float(shopping[row.product_id]))
        if row.packs is not None:
            assert row.packs * row.store.package_g + 1e-7 >= row.grams


def test_valid_targets_cannot_be_traded_for_soft_preferences(repo, week_request):
    # Właśnie ten blok zwracał przekroczenia po zmianie na kcal z tabel, mimo że rozwiązanie istnieje.
    request = week_request.model_copy(update={"slots": [s for s in week_request.slots
                                                       if any(d in (Weekday.TUESDAY, Weekday.WEDNESDAY) for d in s.days)]})
    result = solve_week(request, repo.assumptions, repo.products, repo.dishes,
                        EngineSettings(band_cost_per_g=0, band_cost_per_kcal=0, anchor_cost_per_g=0,
                                       meal_mass_cost_per_g=0, conflict_core=False))
    assert result.status is BlockStatus.OK
    assert verify_plan(result.plan, repo.assumptions, repo.products, repo.dishes).ok


def test_saved_reports_never_override_current_numbers(repo, week_result, tmp_path, monkeypatch):
    service = DietService(repo.paths, use_llm=False, db_path=tmp_path / "plans.sqlite3")
    service.paths = Paths(tmp_path)  # eksporty testowe nie trafiają do danych użytkownika
    plan = week_result.plan
    fresh = verify_plan(plan, repo.assumptions, repo.products, repo.dishes)
    stale = fresh.model_copy(deep=True)
    stale.ok = not fresh.ok
    stale.issues = [Issue(code="uwaga_kucharza", severity=Severity.WARNING, message="Uwaga testowa")]
    for total in stale.totals:
        total.kcal = 9999999
    rec = service.store.save(PlanRecord(plan=plan, title="Test", source="algorytm", ok=stale.ok, report=stale))
    stored = service.store.get(rec.id).report.model_dump_json()
    service.store.set_setting("active_plan", rec.id)

    client = TestClient(create_app(service), base_url="http://127.0.0.1")
    response = client.get(f"/api/plany/{rec.id}")
    assert response.status_code == 200
    view = response.json()
    assert view["ok"] == fresh.ok
    assert view["versions"][0]["ok"] == fresh.ok
    assert service.plans()[0]["ok"] == fresh.ok
    assert service.active_plan().report.totals == fresh.totals
    assert any(i["code"] == "uwaga_kucharza" for i in view["issues"])
    assert all(b["consistent"] for day in view["days"] for b in day["breakdown"].values())
    for total in fresh.totals:
        day = next(d for d in view["days"] if d["day"] == int(total.day))
        assert day["totals"][total.person_id]["values"]["kcal"] == total.kcal
    assert "9999999" not in service.text_export(rec.id, "plan")["content"]
    assert "9999999" not in service.export(rec.id)["plan_md"]

    first = fresh.totals[0]
    telegram = format_day(view, int(first.day))
    assert f"{fmt_num(first.kcal, 0)} kcal" in telegram
    fake = SimpleNamespace(complete_json=lambda task, system, user, schema, **kwargs: SimpleNamespace(
        value=schema.model_validate({"typ": "pokaz", "tekst": "Sprawdzam.",
                                     "fakty": [{"rodzaj": "dzien", "dzien": "poniedzialek"}]})))
    monkeypatch.setattr(service, "llm", lambda: fake)
    chat = ChatAssistant(service).handle("Podaj liczby na poniedziałek", rec.id)
    assert f"{fmt_num(first.kcal, 0)} kcal" in chat["text"]
    assert "9999999" not in chat["text"]

    # Aktualizacja danych produktu też nie może pozostawiać starej sumy z raportu.
    product = next(p for p in repo.products.values() if not p.negligible and any(
        i.product_id == p.id for m in plan.meals for portion in m.portions for i in portion.items))
    updated = product.model_copy(deep=True)
    updated.per_100g.kcal += 50
    service.repo = replace(service.repo, products={**repo.products, product.id: updated})
    after = service.plan(rec.id)
    assert [d["totals"] for d in after["days"]] != [d["totals"] for d in view["days"]]
    assert all(b["consistent"] for day in after["days"] for b in day["breakdown"].values())
    assert service.store.get(rec.id).report.model_dump_json() == stored
    assert service.store.get(rec.id).plan == plan
    assert len(service.store.list()) == 1


@pytest.mark.parametrize(("grams", "packs"), [(500, 1), (500.00000000001, 1), (500.1, 2), (505, 2), (1001, 3)])
def test_shopping_never_rounds_down_a_real_package_shortage(repo, grams, packs):
    a = repo.assumptions.model_copy(deep=True)
    a.day.fixed_items = []
    # Nazwa produktu nie jest istotna: wystarczy zwykły niepomijalny produkt bez gęstości.
    product = next(p for p in repo.products.values() if not p.negligible and not p.density_g_per_ml)
    pid = product.id
    plan = WeekPlan(days=[Weekday.MONDAY], meals=[], snacks=[PlannedSnack(
        day=Weekday.MONDAY, person_id=a.persons[0].id, dish_id="test", items=[PlanItem(product_id=pid, grams=grams)])])
    store = StoreProduct(id="test", product_id=pid, store="Test", name_local="Test", package_g=500, preferred=True)
    row = build_detailed_shopping_list(plan, a, repo.products, [store])[0]
    assert row.packs == packs
