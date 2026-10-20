"""Spójność dania (20.10.2026): sos nie znika względem ryżu, makaronu czy ziemniaków ani względem białka.

Przykład z planu użytkownika 19–25.10: curry z ciecierzycą i tofu, mężczyzna 105 g ryżu (119% przepisu)
i 60 g mleczka kokosowego (55% przepisu) — „za sucho, za dużo tofu i ciecierzycy”.
"""
import shutil
from pathlib import Path

from dietbot.checker import verify_plan
from dietbot.domain.dish import MealType
from dietbot.domain.plan import PersonPortion, PlanItem, PlannedMeal, WeekPlan
from dietbot.engine import EngineRequest, solve_week
from dietbot.storage.paths import Paths
from dietbot.storage.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
CURRY = "curry_z_ciecierzycy_i_tofu_z_ryzem"


def _plan(repo, dish_id, grams_for):
    dish = repo.dishes[dish_id]
    return WeekPlan(days=[0], meals=[PlannedMeal(day=0, meal="obiad", dish_id=dish_id, portions=[
        PersonPortion(person_id=p.id, items=[PlanItem(product_id=i.product_id, grams=grams_for(p.id, i))
                                             for i in dish.ingredients])
        for p in repo.assumptions.persons])])


def _ratio_errors(repo, plan):
    report = verify_plan(plan, repo.assumptions, repo.products, repo.dishes)
    return [i.message for i in report.errors() if i.code == "proporcje_przepisu"]


def test_dry_curry_from_user_plan_is_rejected(repo):
    user = {"ryz_bialy": 105, "tofu": 150, "ciecierzyca_konserwowa": 130, "mleczko_kokosowe": 60,
            "pomidory_z_puszki": 130}
    errors = _ratio_errors(repo, _plan(repo, CURRY, lambda pid, i: user.get(i.product_id, i.typical_g)))
    assert any(e.startswith("Curry z ciecierzycy") and "Mleczko kokosowe" in e and "ryż" in e for e in errors)
    assert any("Mleczko kokosowe" in e and "tofu" in e for e in errors)


def test_whole_dish_may_scale(repo):
    for factor in (.6, 1.0, 1.4):
        plan = _plan(repo, CURRY, lambda pid, i, f=factor: round(i.typical_g * f, 1))
        assert not _ratio_errors(repo, plan)


def test_yogurt_sauce_in_dish_name_counts_as_sauce(repo):
    dish = repo.dishes["domowy_kebab_na_talerzu_z_ryzem_i_sosem_czosnkowym"]
    assert any(r.numerator == ["jogurt_naturalny_3_5"] and r.denominator == ["ryz_bialy"] for r in dish.ratios)
    assert not any("losos" in "".join(r.numerator) for r in repo.dishes["pieczony_losos_z_ryzem_i_warzywami"].ratios
                   if "sos" in (r.description or ""))


def test_balancing_snacks_keep_working(repo):
    snacks = [d for d in repo.dishes.values() if MealType.SNACK in d.meal_types]
    assert snacks and not any("proporcji przepisu" in (r.description or "") for d in snacks for r in d.ratios)


def test_engine_keeps_sauce_with_rice_and_protein(repo):
    request = EngineRequest.model_validate({"slots": [{"days": [0], "meal": "obiad", "candidates": [CURRY]}]})
    result = solve_week(request, repo.assumptions, repo.products, repo.dishes)
    assert result.plan is not None and result.plan.meals
    recipe = {i.product_id: i.typical_g for i in repo.dishes[CURRY].ingredients}
    for portion in result.plan.meals[0].portions:
        g = {it.product_id: it.grams for it in portion.items}
        protein = g["tofu"] + g["ciecierzyca_konserwowa"]
        assert g["mleczko_kokosowe"] / g["ryz_bialy"] >= .85 * recipe["mleczko_kokosowe"] / recipe["ryz_bialy"] - 1e-3
        assert (g["mleczko_kokosowe"] / protein
                >= .8 * recipe["mleczko_kokosowe"] / (recipe["tofu"] + recipe["ciecierzyca_konserwowa"]) - 1e-3)


def _home(tmp_path: Path) -> Path:
    for part in ("dane/produkty", "dane/dania", "dane/sklepy", "dane/korekty", "konfiguracja"):
        shutil.copytree(ROOT / part, tmp_path / part)
    (tmp_path / "wyniki").mkdir()
    return tmp_path


def _replace(path: Path, start: str, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    i = text.index(start)
    j = text.find("- id:", i + 5)
    j = len(text) if j < 0 else j
    assert old in text[i:j]
    path.write_bytes((text[:i] + text[i:j].replace(old, new) + text[j:]).encode("utf-8"))


def test_recipe_correction_reaches_old_install_but_keeps_user_edits(tmp_path):
    home = _home(tmp_path)
    dishes = home / "dane" / "dania" / "obiady.yaml"
    _replace(dishes, "- id: makaron_z_kurczakiem_i_szpinakiem", "typowo_g: 90}", "typowo_g: 50}")
    _replace(dishes, "- id: makaron_z_kurczakiem_i_szpinakiem", "z_garnka, typowo_g: 100}", "z_garnka, typowo_g: 80}")
    repo = Repository.load(Paths(home), use_cache=False, strict=False)
    cream = {i.product_id: i.typical_g for i in repo.dishes["makaron_z_kurczakiem_i_szpinakiem"].ingredients}
    assert cream["smietana_10"] == 90 and cream["szpinak"] == 100
    assert any(p.startswith("Korekta przepisu: Makaron z kurczakiem") for p in repo.load_problems)
    # użytkownik sam zmienił przepis — nie ruszamy
    _replace(dishes, "- id: makaron_z_kurczakiem_i_szpinakiem", "typowo_g: 50}", "typowo_g: 60}")
    repo = Repository.load(Paths(home), use_cache=False, strict=False)
    cream = {i.product_id: i.typical_g for i in repo.dishes["makaron_z_kurczakiem_i_szpinakiem"].ingredients}
    assert cream["smietana_10"] == 60


def test_egg_limit_correction(tmp_path):
    home = _home(tmp_path)
    products = home / "dane" / "produkty" / "produkty.yaml"
    _replace(products, "- id: jajko_kurze", "max_porcja_g: 150", "max_porcja_g: 300")
    assert Repository.load(Paths(home), use_cache=False, strict=False).products["jajko_kurze"].max_portion_g == 150
    _replace(products, "- id: jajko_kurze", "max_porcja_g: 300", "max_porcja_g: 200")
    assert Repository.load(Paths(home), use_cache=False, strict=False).products["jajko_kurze"].max_portion_g == 200
