"""Czytelny jadłospis i lista zakupów po polsku (Markdown). Liczby w podsumowaniach pochodzą z weryfikatora."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from ..checker.verify import VerificationReport
from ..domain.assumptions import DAY_LABELS_PL, Assumptions
from ..domain.dish import MEAL_LABELS_PL, DishTemplate
from ..domain.plan import WeekPlan
from ..domain.product import Category, Product, StoreProduct
from ..measures import household
from ..shopping import SHOPPING_NOTE, SHOPPING_NUTRITION_NOTE, build_shopping_list
from ..textfmt import fmt_num, plural_pl
from .summary import day_summaries, week_summaries

METRICS = [("kcal", "Energia", "kcal"), ("protein", "Białko", "g"), ("fat", "Tłuszcz", "g"), ("carbs", "Węglowodany", "g")]


def fmt_amount(product: Product, grams: float) -> str:
    """'10 g (≈ ¾ łyżki)', '100 g (≈ 2 szt.)'; przyprawy pomijalne jako 'szczypta' / 'do smaku'."""
    if product.negligible:
        return "szczypta" if grams < 1.5 else "do smaku"
    g = fmt_num(grams, 0 if grams >= 3 else 1)
    hint = household(product, grams)
    return f"{g} g (≈ {hint})" if hint else f"{g} g"


def _status_icon(ok: bool) -> str:
    return "✅" if ok else "⚠️"


def render_plan(plan: WeekPlan, a: Assumptions, products: Mapping[str, Product], dishes: Mapping[str, DishTemplate],
                report: VerificationReport, title: str = "Jadłospis na tydzień") -> str:
    persons = a.persons
    lines: list[str] = [f"# {title}", "",
                       "Kalorie: suma wartości kcal produktów z tabel/etykiet. Białko, tłuszcze i węglowodany "
                       "są liczone osobno z tych samych gramatur (łącznie z przekąskami i stałymi pozycjami).", ""]
    errors = report.errors()
    warnings = [i for i in report.issues if i.severity.value == "ostrzezenie"]
    if not errors and not warnings:
        lines.append("**Stan: ✅ wszystkie dni mieszczą się w widełkach i spełniają założenia.**")
    else:
        lines.append(f"**Stan: {'❌ są naruszenia założeń' if errors else '⚠️ są uwagi'}** — szczegóły na końcu.")
    if plan.created_at:
        lines.append(f"_Wygenerowano: {plan.created_at.strftime('%d.%m.%Y %H:%M')} UTC · {plan.engine or ''}_")
    lines.append("")

    if plan.comment:
        lines += [f"> {plan.comment}", ""]
    lines += week_facts(plan, a, dishes, products)

    totals = {(int(t.day), t.person_id): t for t in report.totals}
    fixed = ", ".join(
        fx.description if fx.description
        else f"{(products[fx.product_id].name if fx.product_id in products else fx.product_id).lower()} {fmt_num(fx.grams, 0)} g"
        for fx in a.day.fixed_items
    )
    person_names = {p.id: p.name for p in persons}
    summaries = day_summaries(plan, report, a, products, dishes)
    batch_done: set[str] = set()
    for day in plan.days:
        lines += [f"## {DAY_LABELS_PL[day]}", ""]
        for meal in [m for m in plan.meals if m.day == day]:
            dish = dishes.get(meal.dish_id)
            name = dish.name if dish else meal.dish_id
            tags = []
            if dish and dish.is_treat:
                tags.append("posiłek z puli 20%")
            if dish and dish.is_sweet:
                tags.append("słodki, pula 80%")
            if meal.batch_group:
                days = [DAY_LABELS_PL[m.day].lower() for m in plan.meals if m.batch_group == meal.batch_group]
                tags.append(f"na zapas: {', '.join(days)}")
            tag_txt = f" _({'; '.join(tags)})_" if tags else ""
            lines += [f"### {MEAL_LABELS_PL[meal.meal]} — {name}{tag_txt}", ""]
            if dish and dish.description:
                lines += [dish.description, ""]
            header = "| Składnik | " + " | ".join(p.name for p in persons) + " |"
            lines += [header, "|---|" + "---:|" * len(persons)]
            order = [i.product_id for i in dish.ingredients] if dish else []
            grams_by = {p.person_id: {i.product_id: i.grams for i in p.items} for p in meal.portions}
            pids = sorted({pid for g in grams_by.values() for pid in g},
                          key=lambda x: order.index(x) if x in order else 999)
            for pid in pids:
                product = products.get(pid)
                label = product.name if product else pid
                cells = []
                for person in persons:
                    g = grams_by.get(person.id, {}).get(pid)
                    cells.append(fmt_amount(product, g) if (g and product) else "—")
                lines.append(f"| {label} | " + " | ".join(cells) + " |")
            lines.append("")
            if meal.batch_group and meal.batch_pot_g and meal.batch_group not in batch_done:
                batch_done.add(meal.batch_group)
                # do garnka nie trafia pieczywo podawane obok
                pot = ", ".join(f"{products[p].name.lower()} {fmt_amount(products[p], g)}"
                                for p, g in meal.batch_pot_g.items()
                                if p in products and products[p].category is not Category.BREAD)
                lines += [f"**Gotujesz raz, na wszystkie porcje:** {pot}.", ""]
            if dish and dish.steps and (not meal.batch_group or meal.batch_group in batch_done):
                if not meal.batch_group or meal == next(m for m in plan.meals if m.batch_group == meal.batch_group):
                    lines.append("_Przygotowanie:_")
                    lines += [f"{i}. {s}" for i, s in enumerate(dish.steps, start=1)]
                    lines.append("")
                else:
                    lines += ["_Odgrzewasz porcję ugotowaną wcześniej._", ""]

        day_snacks = [s for s in plan.snacks if s.day == day]
        if day_snacks:
            lines += ["### Przekąski bilansujące", ""]
            for s in day_snacks:
                # plan mógł być liczony dla osób, których już nie ma w założeniach — eksport nie może się wywalić
                items = ", ".join(f"{products[i.product_id].name.lower()} {fmt_amount(products[i.product_id], i.grams)}"
                                  if i.product_id in products else f"{i.product_id} {fmt_num(i.grams, 0)} g"
                                  for i in s.items)
                lines.append(f"- **{person_names.get(s.person_id, s.person_id)}:** {items}. {s.reason or ''}")
            lines.append("")
        lines += [f"_Stałe pozycje (obie osoby): {fixed}._", "", f"**Suma dnia — {DAY_LABELS_PL[day].lower()}**", ""]
        mine = [summaries.get((int(day), p.id)) for p in persons]
        if all(s is not None for s in mine):
            lines += ["| Co | " + " | ".join(p.name for p in persons) + " |", "|---|" + "---:|" * len(persons)]
            # przekąski są osobne dla każdej osoby — jeden wiersz „Przekąska” z nazwą w komórce
            keys: list[str] = []
            for s in mine:
                for r in s.rows:
                    key = "Przekąska" if r.kind == "przekaska" else r.label
                    if key not in keys:
                        keys.append(key)
            for key in keys:
                cells = []
                for s in mine:
                    r = next((x for x in s.rows if ("Przekąska" if x.kind == "przekaska" else x.label) == key), None)
                    if r is None:
                        cells.append("—")
                    elif r.kind == "przekaska":
                        cells.append(f"{r.label.split(': ', 1)[-1]}: {_macro_text(r.values)}")
                    else:
                        cells.append(_macro_text(r.values))
                lines.append(f"| {key} | " + " | ".join(cells) + " |")
            lines.append("| **Razem dzień** | " + " | ".join(
                f"**{_macro_text(s.total)}** {_status_icon(s.ok)}" for s in mine) + " |")
            lines.append("| _Widełki_ | " + " | ".join(_bands_text(p) for p in persons) + " |")
        else:  # plan liczony dla innych osób niż w założeniach — same sumy z weryfikatora
            lines += ["| Osoba | " + " | ".join(m[1] for m in METRICS) + " |", "|---|" + "---:|" * len(METRICS)]
            for person in persons:
                t = totals.get((int(day), person.id))
                if t is None:
                    continue
                cells = []
                for key, _, unit in METRICS:
                    band = person.targets.band(key)
                    v = getattr(t, key)
                    ok = band.min - 0.01 <= v <= band.max + 0.01
                    cells.append(f"{fmt_num(v, 0)} {unit} {_status_icon(ok)} ({fmt_num(band.min)}–{fmt_num(band.max)})")
                lines.append(f"| {person.name} | " + " | ".join(cells) + " |")
        lines.append("")

    weeks = week_summaries(plan, report, a, products, dishes, summaries)
    if weeks:
        lines += ["## Podsumowanie tygodnia", "",
                  "_Sumy dni z weryfikatora; średnia dzienna porównana z widełkami dziennymi._", ""]
        for w in weeks:
            person = a.person(w.person_id)
            lines += [f"**{person.name}**", "", "| Dzień | " + " | ".join(m[1] for m in METRICS) + " |",
                      "|---|" + "---:|" * len(METRICS)]
            for d in w.days:
                lines.append(f"| {DAY_LABELS_PL[d.day]} | " + " | ".join(
                    f"{fmt_num(d.total[k], 0)} {unit}{'' if d.states[k] == 'ok' else ' ⚠️'}" for k, _, unit in METRICS) + " |")
            lines.append("| **Razem tydzień** | " + " | ".join(
                f"**{fmt_num(w.total[k], 0)} {unit}**" for k, _, unit in METRICS) + " |")
            lines.append("| **Średnio dziennie** | " + " | ".join(
                f"**{fmt_num(w.average[k], 0)} {unit}** {_status_icon(w.average_states[k] == 'ok')}"
                for k, _, unit in METRICS) + " |")
            lines.append("| _Widełki dzienne_ | " + " | ".join(
                f"{fmt_num(person.targets.band(k).min)}–{fmt_num(person.targets.band(k).max)} {unit}" for k, _, unit in METRICS)
                + " |")
            lines += ["", f"Dni w widełkach: {w.days_ok} z {len(w.days)}.", ""]

    if report.issues:
        lines += ["## Uwagi weryfikatora", ""]
        for i in report.issues:
            icon = "❌" if i.severity.value == "blad" else "⚠️"
            lines.append(f"- {icon} {i.message}")
        lines.append("")
    lines.append("_Wartości odżywcze: BLS 4.0 (Max Rubner-Institut, CC BY 4.0) i etykiety produktów. "
                 "Gramatury policzył deterministyczny silnik, a sumy sprawdził niezależny weryfikator._")
    return "\n".join(lines) + "\n"


def _macro_text(values: Mapping[str, float]) -> str:
    return (f"{fmt_num(values['kcal'], 0)} kcal · B {fmt_num(values['protein'], 0)} · T {fmt_num(values['fat'], 0)} · "
            f"W {fmt_num(values['carbs'], 0)} g")


def _bands_text(person) -> str:
    t = person.targets
    return (f"{fmt_num(t.kcal.min)}–{fmt_num(t.kcal.max)} kcal · B {fmt_num(t.protein.min)}–{fmt_num(t.protein.max)} · "
            f"T {fmt_num(t.fat.min)}–{fmt_num(t.fat.max)} · W {fmt_num(t.carbs.min)}–{fmt_num(t.carbs.max)} g")


def week_facts(plan: WeekPlan, a: Assumptions, dishes: Mapping[str, DishTemplate],
               products: Mapping[str, Product]) -> list[str]:
    """Fakty tygodnia liczone przez kod (nie przez LLM)."""
    def where(meals) -> str:
        return ", ".join(f"{DAY_LABELS_PL[m.day].lower()} ({MEAL_LABELS_PL[m.meal].lower()})" for m in meals)

    treats = [m for m in plan.meals if m.dish_id in dishes and dishes[m.dish_id].is_treat]
    sweets = [m for m in plan.meals if m.meal.value == "kolacja" and m.dish_id in dishes and dishes[m.dish_id].is_sweet]
    batch = sorted({m.batch_group for m in plan.meals if m.batch_group})
    eggs = 0.0
    egg = products.get("jajko_kurze")
    for m in plan.meals:
        for p in m.portions:
            eggs += sum(i.grams for i in p.items if i.product_id == "jajko_kurze")
    lines = ["**Fakty tygodnia** _(policzone przez program)_", ""]
    lines.append(f"- Posiłki z puli 20%: {len(treats)} — {where(treats) or 'brak'}")
    lines.append(f"- Słodkie kolacje: {len(sweets)} — {where(sweets) or 'brak'}")
    for group in batch:
        meals = [m for m in plan.meals if m.batch_group == group]
        name = dishes[meals[0].dish_id].name if meals[0].dish_id in dishes else meals[0].dish_id
        lines.append(f"- Gotowanie na zapas: {name} — {where(meals)}")
    lines.append(f"- Przekąski bilansujące: {len(plan.snacks)}")
    if egg and egg.measure("szt") and eggs > 0:  # to samo zaokrąglenie co w widoku aplikacji
        lines.append(f"- Jajka w tygodniu (obie osoby): ok. {math.ceil(eggs / egg.measure('szt').grams - 0.05)} szt.")
    lines.append("")
    return lines


def render_shopping_list(plan: WeekPlan, a: Assumptions, products: Mapping[str, Product],
                         store_products: Sequence[StoreProduct] = ()) -> str:
    rows = build_shopping_list(plan, a, products, store_products)
    lines = ["# Lista zakupów", "",
             f"_Na {len(plan.days)} {plural_pl(len(plan.days), 'dzień', 'dni', 'dni')} dla "
             f"{len(a.persons)} {plural_pl(len(a.persons), 'osoby', 'osób', 'osób')}. "
             "Ilości w stanie jak w przepisach (ryż i makaron suche, mięso surowe)._", "",
             SHOPPING_NOTE, "", SHOPPING_NUTRITION_NOTE, ""]
    current = None
    for row in rows:
        if row.group != current:
            if current is not None:
                lines.append("")
            lines += [f"## {row.group_label}", ""]
            current = row.group
        lines.append(f"- [ ] {row.name} — {row.amount_text}")
    lines.append("")
    return "\n".join(lines) + "\n"
