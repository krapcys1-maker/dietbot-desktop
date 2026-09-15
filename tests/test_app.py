"""Warstwa aplikacji: magazyn planów, zadania, zamiany dań, założenia, produkty z etykiety i API (bez LLM)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dietbot.app.api import create_app
from dietbot.app.jobs import JobStatus, UserError
from dietbot.app.service import DietService, default_week_start, merge_yaml, week_title
from dietbot.domain.assumptions import Weekday
from dietbot.storage.paths import Paths
from dietbot.storage.yamlio import load_yaml, to_plain

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def service(tmp_path_factory) -> DietService:
    home = tmp_path_factory.mktemp("dietbot_home")
    for part in ("dane/produkty", "dane/dania", "dane/sklepy", "konfiguracja"):
        shutil.copytree(ROOT / part, home / part)
    (home / "wyniki").mkdir()
    return DietService(Paths(home), use_llm=False)


@pytest.fixture(scope="module")
def generated(service) -> dict:
    job = service.generate(use_llm=False, seed=3)
    job = service.jobs.wait(job.id, timeout=600)
    assert job.status is JobStatus.DONE, (job.error, job.log)
    return job.result


def test_week_helpers():
    from datetime import date

    assert default_week_start(date(2026, 9, 13)) == date(2026, 9, 14)  # niedziela → następny tydzień
    assert default_week_start(date(2026, 9, 15)) == date(2026, 9, 14)  # wtorek → bieżący
    assert week_title(date(2026, 9, 14), list(Weekday)) == "Tydzień 14–20 września"
    assert week_title(date(2026, 9, 28), list(Weekday)) == "Tydzień 28 września – 4 października"


def test_status(service):
    st = service.status()
    assert st["products"] > 150 and st["approved_dishes"] > 100
    assert st["llm"]["enabled"] is False
    assert not st["data_errors"]


def test_generate_and_view(service, generated):
    view = service.plan(generated["plan_id"])
    assert view["version"] == 1 and view["active"]
    assert len(view["days"]) == 7
    for day in view["days"]:
        # trzy posiłki, a w niektóre dni jeszcze przekąska z puli 20% (lody, chipsy…) na końcu dnia
        assert [m["meal"] for m in day["meals"]][:3] == ["sniadanie", "obiad", "kolacja"]
        assert [m["meal"] for m in day["meals"]][3:] in ([], ["przekaska"])
        assert set(day["breakdown"]) == {"kobieta", "mezczyzna"} and all(b["consistent"] for b in day["breakdown"].values())
        for m in day["meals"]:
            assert set(m["portions"]) == {"kobieta", "mezczyzna"}
            assert all(i["grams"] >= 0 for p in m["portions"].values() for i in p["items"])
        assert set(day["totals"]) == {"kobieta", "mezczyzna"}
    if generated["ok"]:
        assert all(s == "ok" for d in view["days"] for t in d["totals"].values() for s in t["states"].values())
    batch = [m for d in view["days"] for m in d["meals"] if m["batch_group"]]
    assert batch and batch[0]["pot"] and not batch[0]["reheat"] and batch[1]["reheat"]
    assert batch[0]["slot_key"] == "1-2_obiad"
    assert any(f["label"] == "Posiłki z puli 20%" for f in view["facts"])
    assert [w["person_id"] for w in view["week_summary"]] == ["kobieta", "mezczyzna"]
    assert all(w["days_count"] == 7 for w in view["week_summary"])

    shop = service.shopping(generated["plan_id"])
    assert shop["groups"] and all(g["items"] for g in shop["groups"])
    exp = service.export(generated["plan_id"])
    assert Path(exp["plan_path"]).exists() and exp["shopping_md"].startswith("# Lista zakupów")


def test_swap_keeps_other_days(service, generated):
    pid = generated["plan_id"]
    before = service.store.get(pid)
    opts = service.swap_options(pid, "4_kolacja")
    clean = [o for o in opts["options"] if not o["notes"]]
    assert clean, "brak zamienników bez uwag"
    job = service.jobs.wait(service.swap(pid, "4_kolacja", clean[0]["id"]).id, timeout=300)
    assert job.status is JobStatus.DONE, (job.error, job.log)
    after = service.store.get(job.result["plan_id"])
    assert after.parent_id == pid and after.version == 2 and after.family_id == before.family_id
    assert after.plan.meal(Weekday.FRIDAY, "kolacja").dish_id == clean[0]["id"]
    for day in Weekday:
        if day is Weekday.FRIDAY:
            continue
        old = [m.model_dump() for m in before.plan.meals if m.day == day]
        new = [m.model_dump() for m in after.plan.meals if m.day == day]
        assert old == new, f"zmienił się dzień {day}"
    assert after.report is not None
    assert service.store.get_setting("active_plan") == after.id

    with pytest.raises(UserError):
        service.swap(pid, "4_kolacja", "przekaska_jablko")  # przekąska nie jest kolacją
    with pytest.raises(UserError):
        service.swap(pid, "9_kolacja", clean[0]["id"])


def test_recompute_creates_version(service, generated):
    job = service.jobs.wait(service.recompute(generated["plan_id"]).id, timeout=600)
    assert job.status is JobStatus.DONE, (job.error, job.log)
    rec = service.store.get(job.result["plan_id"])
    assert rec.source == "przeliczenie" and rec.version >= 2


def test_assumptions_validation_and_comment_preserving_save(service):
    data = service.assumptions()["data"]
    bad = to_plain(data)
    bad["osoby"][0]["cele_dzienne"]["kcal"]["min"] = 1900  # min > max
    _, errors, _ = service.validate_assumptions(bad)
    assert errors and any("min" in e for e in errors)
    bad2 = to_plain(data)
    bad2["zakazy"]["produkty"] = ["nie_ma_takiego"]
    _, errors2, _ = service.validate_assumptions(bad2)
    assert any("nie_ma_takiego" in e for e in errors2)

    good = to_plain(data)
    good["tydzien"]["slodkie_kolacje_max"] = 3
    res = service.save_assumptions(good)
    assert res["ok"], res
    text = service.paths.assumptions.read_text("utf-8")
    assert "# słodka kolacja należy do puli 80%" in text
    assert "slodkie_kolacje_max: 3" in text
    assert service.repo.assumptions.week.sweet_suppers_max == 3
    good["tydzien"]["slodkie_kolacje_max"] = 2
    assert service.save_assumptions(good)["ok"]


def test_merge_yaml_keeps_flow_style(tmp_path):
    from dietbot.storage.yamlio import dump_yaml

    p = tmp_path / "a.yaml"
    p.write_text("# komentarz\nlista: [a, b]  # obok\nmapa:\n  x: 1\n", encoding="utf-8")
    doc = merge_yaml(load_yaml(p), {"lista": ["a", "c"], "mapa": {"x": 2, "y": 3}})
    dump_yaml(doc, p)
    out = p.read_text("utf-8")
    assert "# komentarz" in out and "lista: [a, c]" in out and "x: 2" in out and "y: 3" in out


def test_dish_status_roundtrip(service):
    did = next(d for d in service.repo.approved_dishes())
    assert service.set_dish_status(did, "odrzucone")["status"] == "odrzucone"
    assert did not in service.repo.approved_dishes()
    assert service.set_dish_status(did, "zatwierdzone")["status"] == "zatwierdzone"
    with pytest.raises(UserError):
        service.set_dish_status(did, "cokolwiek")


def test_add_product_from_label(service):
    payload = {"name": "Brânză de vaci Test 0,5%", "aliases": "twaróg chudy testowy", "category": "sery",
               "kcal": 150, "protein": 12, "fat": 0.5, "carbs": 4, "store": "Lidl RO", "brand": "Pilos",
               "package_g": 250, "ean": "5940000000000"}
    with pytest.raises(UserError, match="nie zgadzają"):
        service.add_product(payload)  # 150 kcal przy tych makro to błąd przepisania
    payload["kcal"] = 69
    view = service.add_product(payload)
    assert view["id"] == "branza_de_vaci_test_0_5"
    assert view["source"]["baza"] == "etykieta"
    assert view["stores"] and view["stores"][0]["sklep"] == "Lidl RO"
    assert service.match_product("twaróg chudy testowy")["product_id"] == view["id"]
    with pytest.raises(UserError):
        service.add_product(payload)  # duplikat
    own = to_plain(load_yaml(service.paths.products_dir / "wlasne.yaml"))
    assert own[0]["id"] == view["id"]


def test_api_endpoints(service, generated):
    client = TestClient(create_app(service))
    assert client.get("/api/status").status_code == 200
    plans = client.get("/api/plany").json()
    assert plans and any(p["active"] for p in plans)
    active = client.get("/api/plany/aktywny").json()
    assert active["days"]
    r = client.get(f"/api/plany/{generated['plan_id']}/zamienniki", params={"slot": "0_sniadanie"})
    assert r.status_code == 200 and r.json()["options"]
    r = client.post(f"/api/plany/{generated['plan_id']}/zamien", json={"slot_key": "0_sniadanie", "dish_id": "nie_ma"})
    assert r.status_code == 400 and "Nie ma takiego dania" in r.json()["detail"]
    assert client.get("/api/plany/nie-ma-takiego").status_code == 400
    assert client.get("/api/dania", params={"q": "kurczak"}).json()
    assert client.get("/api/produkty", params={"q": "skyr"}).json()
    assert client.get("/api/zalozenia").json()["data"]["osoby"]
    chk = client.post("/api/zalozenia/sprawdz", json={"data": {"osoby": []}}).json()
    assert not chk["ok"] and chk["errors"]
    reply = client.post("/api/czat", json={"text": "Ile białka mam w środę?"}).json()
    assert "modelu językowego" in reply["text"]
    assert client.get("/api/zadania").json()
    # ochrona przed DNS rebinding: tylko localhost
    assert client.get("/api/status", headers={"host": "zly.example.com"}).status_code == 400


def test_api_validation_errors_are_polish(service):
    client = TestClient(create_app(service))
    r = client.post("/api/plany/generuj", json={"use_llm": False, "wishes": "x" * 1500})
    assert r.status_code == 400 and r.json()["detail"] == "życzenia: za długi tekst (maks. 1000 znaków)"
    r = client.post("/api/plany/generuj", json={"use_llm": "może", "week_start": "wczoraj"})
    detail = r.json()["detail"]
    assert r.status_code == 400 and "tak albo nie" in detail and "RRRR-MM-DD" in detail
    assert "should" not in detail and "Input" not in detail
