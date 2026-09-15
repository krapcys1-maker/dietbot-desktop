"""Kontrola kucharza porcji po silniku: poważna uwaga zawęża składnik i silnik liczy dzień od nowa."""

from __future__ import annotations

from fixtures_week import WEEK_SLOTS

from dietbot.domain import MealType
from dietbot.domain.assumptions import parse_day
from dietbot.llm.client import LLMResult
from dietbot.planner.compose import Composition, SlotPlan
from dietbot.planner.pipeline import WeekPlanner
from dietbot.planner.portion_review import TIGHT_MAX, build_portion_prompt, grams_in_slot, tighten


class FakeChef:
    def __init__(self, issues):
        self.issues = issues
        self.prompts: list[str] = []

    def complete_json(self, task, system, user, schema, **kw):
        assert task == "kontrola_porcji"
        self.prompts.append(user)
        return LLMResult(value=schema.model_validate({"uwagi": self.issues}), attempts=1, cost_usd=0.001,
                         prompt_tokens=0, completion_tokens=0, seconds=0.0)


def week_composition() -> Composition:
    return Composition(slots=[SlotPlan(days=[parse_day(d) for d in s["days"]], meal=MealType(s["meal"]),
                                       dish_id=s["candidates"][0]) for s in WEEK_SLOTS])


def test_serious_issue_tightens_ingredient_and_resolves_day(repo):
    rice_before = WeekPlanner(repo, None).finish(week_composition(), repair=False)
    before = grams_in_slot(rice_before.plan, "0_obiad", "ryz_bialy")
    chef = FakeChef([
        {"slot": "0_obiad", "produkt": "ryz_bialy", "kierunek": "za_duzo", "problem": "za dużo ryżu do curry", "waga": "powazna"},
        {"slot": "3_kolacja", "produkt": "tortilla_pszenna", "kierunek": "za_malo", "problem": "mała tortilla", "waga": "drobna"},
        {"slot": "9_obiad", "produkt": "ryz_bialy", "kierunek": "za_duzo", "problem": "slot spoza planu", "waga": "powazna"},
    ])
    planner = WeekPlanner(repo, chef)
    out = planner.finish(week_composition(), repair=False, chef=True)
    assert out.ok, [i.message for i in out.report.errors()]
    after = grams_in_slot(out.plan, "0_obiad", "ryz_bialy")
    dish = repo.dishes["kurczak_curry_z_ryzem"]
    typical = next(i.typical_g for i in dish.ingredients if i.product_id == "ryz_bialy")
    for person in repo.assumptions.persons:
        scale = person.energy_center / planner.settings.reference_kcal
        assert after[person.id] <= typical * TIGHT_MAX * scale + 5 + 1e-6, (before, after)
    assert any("kucharz: za dużo ryżu do curry" in line for line in out.log)
    # drobne uwagi trafiają tylko do dziennika (nie zaśmiecają planu ostrzeżeniami)
    assert not [i for i in out.report.issues if i.code == "uwaga_kucharza"]
    assert any("drobna uwaga" in line and "mała tortilla" in line for line in out.log)
    # ryż zmniejszony względem talerza, który ocenił kucharz (a nie tylko względem przepisu)
    assert all(after[p] < before[p] for p in before), (before, after)
    assert "[0_obiad] Kurczak curry" in chef.prompts[0] and "[ryz_bialy]" in chef.prompts[0]


def test_prompt_lists_batch_soup_once_and_tighten_bounds(repo):
    out = WeekPlanner(repo, None).finish(week_composition(), repair=False)
    prompt, slots = build_portion_prompt(out.plan, repo.dishes, repo.products, repo.assumptions)
    assert "[1-2_obiad]" in prompt and "[2_obiad]" not in prompt and len(slots) == 20
    dish = repo.dishes["kurczak_curry_z_ryzem"]
    tight = tighten(dish, "ryz_bialy", "za_duzo")
    rice = next(i for i in tight.ingredients if i.product_id == "ryz_bialy")
    assert rice.max_g == round(rice.typical_g * TIGHT_MAX, 1)
    assert tighten(tight, "ryz_bialy", "za_duzo") is None  # już zawężone
    low = tighten(dish, "piers_z_kurczaka", "za_malo")
    assert next(i for i in low.ingredients if i.product_id == "piers_z_kurczaka").min_g is not None
