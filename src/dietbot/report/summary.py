"""Sumy dnia z rozbiciem na posiłki, przekąski i stałe pozycje oraz podsumowanie tygodnia (aplikacja, eksport, Telegram).

Suma dnia to zawsze wartość z niezależnego weryfikatora; rozbicie liczymy tą samą funkcją (`nutrition_of`) z gramatur
w planie, więc da się sprawdzić, że wiersze sumują się do wyniku weryfikatora.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..checker.verify import VerificationReport, nutrition_of
from ..domain.assumptions import DAY_LABELS_PL, Assumptions, Weekday
from ..domain.dish import MEAL_LABELS_PL, DishTemplate
from ..domain.plan import WeekPlan
from ..domain.product import Product

METRICS = ("kcal", "protein", "fat", "carbs")
# różnica sumy wierszy i sumy weryfikatora większa niż zaokrąglenia oznacza błąd w danych planu
SUM_TOLERANCE = {"kcal": 0.5, "protein": 0.05, "fat": 0.05, "carbs": 0.05}


@dataclass
class BreakdownRow:
    kind: str  # posilek | przekaska | stale
    label: str
    values: dict[str, float]


@dataclass
class DaySummary:
    day: Weekday
    person_id: str
    rows: list[BreakdownRow]
    total: dict[str, float]  # z weryfikatora
    states: dict[str, str]  # ok | za_malo | za_duzo
    rows_sum: dict[str, float] = field(default_factory=dict)

    @property
    def consistent(self) -> bool:
        return all(abs(self.rows_sum[m] - self.total[m]) <= SUM_TOLERANCE[m] for m in METRICS)

    @property
    def ok(self) -> bool:
        return all(s == "ok" for s in self.states.values())


@dataclass
class WeekSummary:
    person_id: str
    days: list[DaySummary]
    total: dict[str, float]
    average: dict[str, float]
    average_states: dict[str, str]
    week_band: dict[str, tuple[float, float]]  # widełki dzienne × liczba dni
    total_states: dict[str, str]

    @property
    def days_ok(self) -> int:
        return sum(1 for d in self.days if d.ok)


def band_state(value: float, lo: float, hi: float) -> str:
    if value < lo - 0.01:
        return "za_malo"
    if value > hi + 0.01:
        return "za_duzo"
    return "ok"


def fixed_items_label(a: Assumptions, products: Mapping[str, Product]) -> str:
    return ", ".join(fx.description or (products[fx.product_id].name.lower() if fx.product_id in products else fx.product_id)
                     for fx in a.day.fixed_items)


def day_summaries(plan: WeekPlan, report: VerificationReport, a: Assumptions, products: Mapping[str, Product],
                  dishes: Mapping[str, DishTemplate]) -> dict[tuple[int, str], DaySummary]:
    method = a.energy.method
    totals = {(int(t.day), t.person_id): t for t in report.totals}

    def values(items: Mapping[str, float]) -> dict[str, float]:
        n = nutrition_of({k: v for k, v in items.items() if k in products}, products, method)
        return {m: n[m] for m in METRICS}

    out: dict[tuple[int, str], DaySummary] = {}
    fixed = values({fx.product_id: fx.grams for fx in a.day.fixed_items}) if a.day.fixed_items else None
    for day in plan.days:
        day_meals = sorted((m for m in plan.meals if m.day == day),
                           key=lambda m: a.day.meals.index(m.meal) if m.meal in a.day.meals else 9)
        for person in a.persons:
            t = totals.get((int(day), person.id))
            if t is None:
                continue
            rows: list[BreakdownRow] = []
            for m in day_meals:
                portion = next((p for p in m.portions if p.person_id == person.id), None)
                if portion is None:
                    continue
                grams: dict[str, float] = {}
                for i in portion.items:
                    grams[i.product_id] = grams.get(i.product_id, 0.0) + i.grams
                name = dishes[m.dish_id].name if m.dish_id in dishes else m.dish_id
                rows.append(BreakdownRow("posilek", f"{MEAL_LABELS_PL[m.meal]}: {name}", values(grams)))
            for s in plan.snacks:
                if s.day == day and s.person_id == person.id:
                    name = dishes[s.dish_id].name if s.dish_id in dishes else s.dish_id
                    grams = {}
                    for i in s.items:
                        grams[i.product_id] = grams.get(i.product_id, 0.0) + i.grams
                    rows.append(BreakdownRow("przekaska", f"Przekąska: {name}", values(grams)))
            if fixed is not None:
                rows.append(BreakdownRow("stale", f"Stałe pozycje: {fixed_items_label(a, products)}", dict(fixed)))
            total = {m: float(getattr(t, m)) for m in METRICS}
            states = {m: band_state(total[m], person.targets.band(m).min, person.targets.band(m).max) for m in METRICS}
            rows_sum = {m: sum(r.values[m] for r in rows) for m in METRICS}
            out[(int(day), person.id)] = DaySummary(day=day, person_id=person.id, rows=rows, total=total, states=states,
                                                    rows_sum=rows_sum)
    return out


def week_summaries(plan: WeekPlan, report: VerificationReport, a: Assumptions, products: Mapping[str, Product],
                   dishes: Mapping[str, DishTemplate],
                   days: Mapping[tuple[int, str], DaySummary] | None = None) -> list[WeekSummary]:
    days = days if days is not None else day_summaries(plan, report, a, products, dishes)
    out: list[WeekSummary] = []
    for person in a.persons:
        mine = [days[(int(d), person.id)] for d in plan.days if (int(d), person.id) in days]
        if not mine:
            continue
        n = len(mine)
        total = {m: sum(d.total[m] for d in mine) for m in METRICS}
        average = {m: total[m] / n for m in METRICS}
        bands = {m: person.targets.band(m) for m in METRICS}
        out.append(WeekSummary(
            person_id=person.id, days=mine, total=total, average=average,
            average_states={m: band_state(average[m], bands[m].min, bands[m].max) for m in METRICS},
            week_band={m: (bands[m].min * n, bands[m].max * n) for m in METRICS},
            total_states={m: band_state(total[m], bands[m].min * n, bands[m].max * n) for m in METRICS},
        ))
    return out


def day_label(day: Weekday) -> str:
    return DAY_LABELS_PL[day]
