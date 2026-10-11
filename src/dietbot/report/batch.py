"""One cooking batch, shared by screen and exports, including historical plans.

Totals come from the stored portions, never from an independently cached pot.
This is presentation only: nutrition, shopping and saved plans are unchanged.
"""
import re
from collections import defaultdict

from ..domain.assumptions import DAY_LABELS_PL
from ..domain.product import Category
from ..textfmt import plural_pl

WATER = re.compile(r"(?:ok\.\s*)?\d+(?:[.,]\d+)?\s*(?:ml|l|litr(?:a|y|ów)?)\s+wody", re.I)


def batch_recipe(plan, meal, dish, products, persons):
    if not meal.batch_group:
        return None
    meals = sorted((m for m in plan.meals if m.batch_group == meal.batch_group), key=lambda m: int(m.day))
    days = sorted({m.day for m in meals})
    names = {p.id: p.name for p in persons}
    totals = defaultdict(float)
    portions = []
    for m in meals:
        for portion in m.portions:
            for item in portion.items:
                totals[item.product_id] += item.grams
            portions.append({"day": DAY_LABELS_PL[m.day], "person": names.get(portion.person_id, portion.person_id)})
    count = len(portions)
    people = len({p.person_id for m in meals for p in m.portions})
    title = (f"Całość na {len(days)} {'dzień' if len(days) == 1 else 'dni'} "
             f"dla {people} {'osoby' if people == 1 else 'osób'} — "
             f"{count} {plural_pl(count, 'porcja', 'porcje', 'porcji')}")
    steps = list(dish.steps) if dish else []
    water = any(WATER.search(s) for s in steps)
    # Old free-text water volumes have no declared reference yield. Do not
    # silently multiply them or pretend they were calculated for this pot.
    if water:
        steps = [re.sub(r"Zalej\s+wodę", "Dodaj wodę", WATER.sub(
            "wodę w ilości potrzebnej do przykrycia składników w całym garnku", s), flags=re.I) for s in steps]
    items = [{"product_id": pid, "grams": round(g, 3),
              "side": pid in products and products[pid].category is Category.BREAD and pid != "bulka_tarta"}
             for pid, g in totals.items() if g > 0]
    has_side = any(i["side"] for i in items)
    instructions = (
        f"Gotujesz tylko raz, na dni: {', '.join(DAY_LABELS_PL[d].lower() for d in days)}. "
        "Wszystkie ilości poniżej są już zsumowane — nie mnożysz ich ponownie. "
        "Rozdziel gotowe danie do opisanych pojemników: "
        + "; ".join(f"{p['day'].lower()} — {p['person']}" for p in portions) + "."
        + (" Pieczywo oznaczone jako dodatek podaj osobno, nie wkładaj do garnka." if has_side else "")
    )
    return {"title": title, "days": [DAY_LABELS_PL[d] for d in days], "portions_count": count,
            "portions_text": f"{count} {plural_pl(count, 'porcja', 'porcje', 'porcji')}",
            "first_day": int(days[0]), "instructions": instructions, "steps": steps,
            "water": "Do przykrycia składników w całym garnku; uzupełniaj podczas gotowania według gęstości."
                     if water else None,
            "has_side": has_side, "items": items}
