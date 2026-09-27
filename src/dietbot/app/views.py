"""Widoki danych dla aplikacji (JSON). Wszystkie liczby odżywcze liczy weryfikator z tabel — nie silnik i nie LLM."""

from __future__ import annotations

import math
from collections.abc import Mapping

from ..checker.verify import nutrition_of
from ..domain.assumptions import DAY_LABELS_PL, Assumptions
from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType, cuisine_label
from ..domain.plan import PlanItem, PlannedMeal, WeekPlan
from ..domain.product import CATEGORY_LABELS_PL, Category, Product
from ..measures import household
from ..planner.pools import DishProfile
from ..report.batch import batch_recipe
from ..report.summary import day_summaries, week_summaries
from ..storage.repository import Repository
from ..textfmt import fmt_num
from .store import PlanRecord

METRICS = ("kcal", "protein", "fat", "carbs")
METRIC_LABELS = {"kcal": "Energia", "protein": "Białko", "fat": "Tłuszcz", "carbs": "Węglowodany", "fiber": "Błonnik"}
SOURCE_LABELS = {"llm": "ułożony przez model językowy", "algorytm": "ułożony algorytmem", "zamiana": "zamiana dania",
                 "przeliczenie": "przeliczony po zmianie założeń", "naprawa": "naprawiony automatycznie"}


def _r(v: float, nd: int = 1) -> float:
    return round(v, nd)


def amount_text(product: Product | None, grams: float) -> str:
    if product is None:
        return f"{fmt_num(grams, 0)} g"
    if product.negligible:
        return "szczypta" if grams < 1.5 else "do smaku"
    return f"{fmt_num(grams, 0 if grams >= 3 else 1)} g"


def item_view(item: PlanItem, products: Mapping[str, Product]) -> dict:
    p = products.get(item.product_id)
    return {"product_id": item.product_id, "name": p.name if p else item.product_id,
            "category": p.category.value if p else None, "grams": _r(item.grams),
            "amount": amount_text(p, item.grams),
            "household": household(p, item.grams) if (p and not p.negligible) else None,
            "negligible": bool(p and p.negligible)}


def groups_view(dish: DishTemplate | None, meal: PlannedMeal, products: Mapping[str, Product]) -> list[dict]:
    """Masy do przygotowania razem (ciasto, panierka, farsz): suma dla wszystkich osób, a dla dania na zapas — cały garnek."""
    if dish is None or not dish.groups:
        return []
    out = []
    for grp in dish.groups:
        totals: dict[str, float] = {}
        if meal.batch_pot_g:
            totals = {pid: g for pid, g in meal.batch_pot_g.items() if pid in grp.products}
        else:
            for portion in meal.portions:
                for it in portion.items:
                    if it.product_id in grp.products:
                        totals[it.product_id] = totals.get(it.product_id, 0.0) + it.grams
        mass = sum(totals.values())
        if mass <= 0:
            continue
        recipe = grp.shares(dish.ingredients)
        items = []
        for pid in grp.products:
            if pid not in totals:
                continue
            product = products.get(pid)
            g = totals[pid]
            items.append({"product_id": pid, "name": product.name if product else pid, "amount": amount_text(product, g),
                          "household": household(product, g) if product and not product.negligible else None,
                          "share_pct": _r(g / mass * 100), "recipe_pct": _r(recipe.get(pid, 0) * 100)})
        out.append({"name": grp.name, "total_g": round(mass), "items": items})
    return out


def nutrition_view(items: Mapping[str, float], products: Mapping[str, Product], a: Assumptions) -> dict:
    n = nutrition_of({k: v for k, v in items.items() if k in products}, products, a.energy.method)
    return {k: _r(v) for k, v in n.items()}


def band_state(value: float, lo: float, hi: float) -> str:
    if value < lo - 0.01:
        return "za_malo"
    if value > hi + 0.01:
        return "za_duzo"
    return "ok"


def persons_view(a: Assumptions) -> list[dict]:
    return [{"id": p.id, "name": p.name,
             "targets": {m: {"min": p.targets.band(m).min, "max": p.targets.band(m).max,
                             "target": p.targets.band(m).center} for m in METRICS},
             "anchor": {"min": p.protein_anchor.min, "max": p.protein_anchor.max,
                        "tolerance": p.protein_anchor_tolerance}}
            for p in a.persons]


def week_facts(plan: WeekPlan, a: Assumptions, dishes: Mapping[str, DishTemplate],
               products: Mapping[str, Product]) -> list[dict]:
    """Fakty tygodnia policzone przez program (do wyświetlenia obok komentarza modelu)."""
    def where(meals) -> str:
        return ", ".join(f"{DAY_LABELS_PL[m.day].lower()} ({MEAL_LABELS_PL[m.meal].lower()})" for m in meals)

    treats = [m for m in plan.meals if m.dish_id in dishes and dishes[m.dish_id].is_treat]
    sweets = [m for m in plan.meals if m.meal is MealType.SUPPER and m.dish_id in dishes and dishes[m.dish_id].is_sweet]
    tb = a.week.treat_meals
    facts = [
        {"label": "Posiłki z puli 20%", "value": str(len(treats)), "detail": where(treats) or "brak",
         "ok": tb.min <= len(treats) <= tb.max if len(plan.days) == 7 else len(treats) <= tb.max},
        {"label": "Słodkie kolacje", "value": str(len(sweets)), "detail": where(sweets) or "brak",
         "ok": len(sweets) <= a.week.sweet_suppers_max},
    ]
    for group in sorted({m.batch_group for m in plan.meals if m.batch_group}):
        meals = [m for m in plan.meals if m.batch_group == group]
        name = dishes[meals[0].dish_id].name if meals[0].dish_id in dishes else meals[0].dish_id
        facts.append({"label": "Gotowanie na zapas", "value": name, "detail": where(meals), "ok": True})
    facts.append({"label": "Przekąski bilansujące", "value": str(len(plan.snacks)),
                  "detail": ", ".join(sorted({DAY_LABELS_PL[s.day].lower() for s in plan.snacks},
                                             key=lambda d: [x.lower() for x in DAY_LABELS_PL.values()].index(d)))
                  or "brak", "ok": True})
    egg = products.get("jajko_kurze")
    grams = sum(i.grams for m in plan.meals for p in m.portions for i in p.items if i.product_id == "jajko_kurze")
    if egg and egg.measure("szt") and grams > 0:
        facts.append({"label": "Jajka (obie osoby)", "value": f"ok. {math.ceil(grams / egg.measure('szt').grams - 0.05)} szt.",
                      "detail": "", "ok": True})
    return facts


def plan_view(rec: PlanRecord, repo: Repository) -> dict:
    plan, a, products, dishes = rec.plan, repo.assumptions, repo.products, repo.dishes
    report = rec.report
    totals = {(int(t.day), t.person_id): t for t in (report.totals if report else [])}
    issues = [i.model_dump(mode="json") for i in (report.issues if report else [])]
    batch_first: dict[str, int] = {}
    for m in sorted(plan.meals, key=lambda m: int(m.day)):
        if m.batch_group:
            batch_first.setdefault(m.batch_group, int(m.day))

    summaries = day_summaries(plan, report, a, products, dishes) if report else {}
    days = []
    for day in plan.days:
        meals = []
        day_meals = sorted((m for m in plan.meals if m.day == day), key=lambda m: a.day.meals.index(m.meal)
                           if m.meal in a.day.meals else 9)
        for m in day_meals:
            dish = dishes.get(m.dish_id)
            order = [i.product_id for i in dish.ingredients] if dish else []
            portions = {}
            for portion in m.portions:
                items = sorted(portion.items, key=lambda i: order.index(i.product_id) if i.product_id in order else 999)
                grams = {i.product_id: i.grams for i in portion.items}
                person = next((p for p in a.persons if p.id == portion.person_id), None)
                nut = nutrition_view(grams, products, a)
                anchor = None
                if person and dish and not dish.is_treat:
                    lo = person.protein_anchor.min - person.protein_anchor_tolerance
                    hi = person.protein_anchor.max + person.protein_anchor_tolerance
                    anchor = band_state(nut["protein"], lo, hi)
                mass = sum(i.grams for i in portion.items)
                cap = person.meal_mass_cap(m.meal) if person else None
                portions[portion.person_id] = {"items": [item_view(i, products) for i in items], "nutrition": nut,
                                               "anchor_state": anchor, "mass_g": round(mass),
                                               "mass_cap_g": cap, "mass_over": bool(cap and mass > cap * 1.02)}
            first_batch_day = batch_first.get(m.batch_group) if m.batch_group else None
            batch = batch_recipe(plan, m, dish, products, a.persons)
            if batch:
                batch["items"] = [{**i, "name": products[i["product_id"]].name if i["product_id"] in products else i["product_id"],
                                   "amount": amount_text(products.get(i["product_id"]), i["grams"])} for i in batch["items"]]
            pot = None
            if m.batch_group and m.batch_pot_g and first_batch_day == int(day):
                pot = [{"product_id": pid, "name": products[pid].name if pid in products else pid,
                        "amount": amount_text(products.get(pid), g),
                        "household": household(products[pid], g) if pid in products and not products[pid].negligible else None}
                       for pid, g in m.batch_pot_g.items()
                       if pid not in products or products[pid].category is not Category.BREAD]
            meals.append({
                "slot_key": "-".join(str(int(d)) for d in sorted({x.day for x in plan.meals if m.batch_group
                                                                   and x.batch_group == m.batch_group} or {m.day}))
                            + f"_{m.meal.value}",
                "meal": m.meal.value, "meal_label": MEAL_LABELS_PL[m.meal], "dish_id": m.dish_id,
                "dish_name": dish.name if dish else m.dish_id, "description": dish.description if dish else None,
                "cuisine": cuisine_label(dish.cuisine) if dish else None, "tags": dish.tags if dish else [],
                "is_treat": bool(dish and dish.is_treat), "is_sweet": bool(dish and dish.is_sweet),
                "prep_minutes": dish.prep_minutes if dish else None,
                "steps": batch["steps"] if batch else (dish.steps if dish else []),
                "batch_recipe": batch,
                "batch_group": m.batch_group,
                "batch_days": [DAY_LABELS_PL[x.day] for x in plan.meals if m.batch_group and x.batch_group == m.batch_group],
                "reheat": bool(m.batch_group and first_batch_day is not None and first_batch_day != int(day)),
                "pot": pot, "portions": portions, "groups": groups_view(dish, m, products),
            })
        snacks = []
        for s in plan.snacks:
            if s.day != day:
                continue
            dish = dishes.get(s.dish_id)
            grams = {i.product_id: i.grams for i in s.items}
            snacks.append({"person_id": s.person_id, "dish_id": s.dish_id, "dish_name": dish.name if dish else s.dish_id,
                           "items": [item_view(i, products) for i in s.items], "reason": s.reason,
                           "nutrition": nutrition_view(grams, products, a)})
        day_totals = {}
        for person in a.persons:
            t = totals.get((int(day), person.id))
            if t is None:
                continue
            # 2 miejsca: aplikacja i Telegram pokazują pełne gramy — podwójne zaokrąglanie (152,46 → 152,5 → 153)
            # dawałoby 1 g różnicy względem odpowiedzi czatu liczonej z tych samych sum weryfikatora
            values = {k: _r(getattr(t, k), 2) for k in (*METRICS, "fiber")}
            states = {k: band_state(getattr(t, k), person.targets.band(k).min, person.targets.band(k).max)
                      for k in METRICS}
            day_totals[person.id] = {"values": values, "states": states}
        breakdown = {}
        for person in a.persons:
            s = summaries.get((int(day), person.id))
            if s is None:
                continue
            breakdown[person.id] = {
                "rows": [{"kind": r.kind, "label": r.label, "values": {k: _r(v) for k, v in r.values.items()}}
                         for r in s.rows],
                "total": {k: _r(v, 2) for k, v in s.total.items()}, "states": s.states, "consistent": s.consistent,
            }
        days.append({"day": int(day), "label": DAY_LABELS_PL[day], "meals": meals, "snacks": snacks,
                     "totals": day_totals, "breakdown": breakdown,
                     "issues": [i for i in issues if i.get("day") == int(day)]})

    fixed = [{"product_id": fx.product_id, "name": products[fx.product_id].name if fx.product_id in products else fx.product_id,
              "grams": fx.grams, "description": fx.description} for fx in a.day.fixed_items]
    return {
        **rec.summary(),
        "source_label": SOURCE_LABELS.get(rec.source, rec.source),
        "comment": plan.comment,
        "persons": persons_view(a),
        "metric_labels": METRIC_LABELS,
        "days": days,
        "fixed_items": fixed,
        "facts": week_facts(plan, a, dishes, products),
        "week_summary": [{
            "person_id": w.person_id,
            "days": [{"day": int(d.day), "label": DAY_LABELS_PL[d.day], "values": {k: _r(v, 2) for k, v in d.total.items()},
                      "states": d.states} for d in w.days],
            "total": {k: _r(v, 2) for k, v in w.total.items()},
            "average": {k: _r(v, 2) for k, v in w.average.items()},
            "average_states": w.average_states, "total_states": w.total_states,
            "week_band": {k: [_r(lo), _r(hi)] for k, (lo, hi) in w.week_band.items()},
            "days_ok": w.days_ok, "days_count": len(w.days),
        } for w in (week_summaries(plan, report, a, products, dishes, summaries) if report else [])],
        "issues": issues,
        "week_issues": [i for i in issues if i.get("day") is None],
        "log": rec.log,
        "engine": plan.engine,
        "assumptions_changed": bool(rec.assumptions_hash and rec.assumptions_hash != assumptions_hash(repo)),
    }


def assumptions_hash(repo: Repository) -> str:
    import hashlib

    return hashlib.sha256(repo.assumptions.model_dump_json().encode()).hexdigest()[:16]


def dish_view(dish: DishTemplate, products: Mapping[str, Product], profile: DishProfile | None = None) -> dict:
    return {
        "id": dish.id, "name": dish.name, "description": dish.description,
        "meal_types": [m.value for m in dish.meal_types], "meal_labels": [MEAL_LABELS_PL[m] for m in dish.meal_types],
        "cuisine": cuisine_label(dish.cuisine), "cuisine_key": dish.cuisine, "tags": dish.tags,
        "status": dish.status.value, "prep_minutes": dish.prep_minutes,
        "is_treat": dish.is_treat, "is_sweet": dish.is_sweet, "is_batch": dish.is_batch, "batch_days": dish.batch_days,
        "source": dish.source, "steps": dish.steps, "culinary_notes": dish.culinary_notes,
        "ingredients": [{"product_id": i.product_id,
                         "name": products[i.product_id].name if i.product_id in products else i.product_id,
                         "role": i.role.value, "portioning": i.portioning.value, "typical_g": i.typical_g,
                         "min_g": i.min_g, "max_g": i.max_g, "optional": i.optional,
                         "household": household(products[i.product_id], i.typical_g)
                         if i.product_id in products and not products[i.product_id].negligible and i.typical_g else None}
                        for i in dish.ingredients],
        "groups": [{"name": g.name, "tolerance_pct": _r((g.tolerance or 0.05) * 100),
                    "items": [{"product_id": pid, "name": products[pid].name if pid in products else pid,
                               "share_pct": _r(share * 100)} for pid, share in g.shares(dish.ingredients).items()]}
                   for g in dish.groups],
        "profile": None if profile is None else {
            "protein_sources": profile.protein_sources, "carb_sources": profile.carb_sources,
            "protein_label": profile.protein_label, "energy_label": profile.energy_label,
            "protein_range": {k: [_r(v[0]), _r(v[1])] for k, v in profile.protein_range.items()},
        },
    }


def product_view(p: Product) -> dict:
    n = p.per_100g
    return {
        "id": p.id, "name": p.name, "aliases": p.aliases, "category": p.category.value,
        "category_label": CATEGORY_LABELS_PL[p.category], "state": p.state.value,
        "per_100g": {"kcal": n.kcal, "protein": n.protein, "fat": n.fat, "carbs": n.carbs, "fiber": n.fiber,
                     "sugars": n.sugars, "salt": n.salt},
        "source": p.source.model_dump(mode="json", by_alias=True, exclude_none=True),
        "measures": [{"unit": m.unit, "grams": m.grams, "description": m.description, "source": m.source}
                     for m in p.measures],
        "step_g": p.step_g, "max_portion_g": p.max_portion_g, "density_g_per_ml": p.density_g_per_ml,
        "negligible": p.negligible, "notes": p.notes,
    }
