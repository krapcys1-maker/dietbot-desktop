"""A plan's immutable nutrition and recipe context, independent of current settings."""

from datetime import UTC, datetime

from ..domain.assumptions import Assumptions
from ..domain.dish import DishTemplate
from ..domain.plan import WeekPlan
from ..domain.product import Product, StoreProduct
from ..storage.repository import Repository


def capture(repo: Repository, plan: WeekPlan, *, legacy: bool = False) -> dict:
    dish_ids = {m.dish_id for m in plan.meals} | {s.dish_id for s in plan.snacks}
    dishes = {k: v for k, v in repo.dishes.items() if k in dish_ids}
    product_ids = {i.product_id for d in dishes.values() for i in d.ingredients}
    product_ids.update(f.product_id for f in repo.assumptions.day.fixed_items)
    product_ids.update(i.product_id for m in plan.meals for p in m.portions for i in p.items)
    product_ids.update(i.product_id for s in plan.snacks for i in s.items)
    return {"schema": 1, "captured_at": datetime.now(UTC).isoformat(), "legacy": legacy,
            "assumptions": repo.assumptions.model_dump(mode="json"),
            "products": {k: p.model_dump(mode="json") for k, p in repo.products.items() if k in product_ids},
            "dishes": {k: d.model_dump(mode="json") for k, d in dishes.items()},
            "store_products": [s.model_dump(mode="json") for s in repo.store_products if s.product_id in product_ids]}


def restore(snapshot: dict, current: Repository) -> Repository:
    if snapshot.get("schema") != 1:
        raise ValueError("Nieobsługiwana wersja kopii danych jadłospisu.")
    return Repository(paths=current.paths, assumptions=Assumptions.model_validate(snapshot["assumptions"]),
                      products={k: Product.model_validate(p) for k, p in snapshot["products"].items()},
                      dishes={k: DishTemplate.model_validate(d) for k, d in snapshot["dishes"].items()},
                      store_products=[StoreProduct.model_validate(s) for s in snapshot["store_products"]],
                      dish_ratings=current.dish_ratings)


# Pola bez wpływu na gramatury i zasady planu: ich zmiana (np. odrzucenie dania w bibliotece, poprawka opisu) nie
# może blokować zamian w zapisanych dietach (audyt 09.10.2026, S2).
DISPLAY_ONLY = {"dishes": ("status", "source", "description", "steps", "name", "prep_minutes"),
                "products": ("name", "aliases", "source", "notes", "verified")}


# z wartości odżywczych silnik i weryfikator celów używają tylko tych pól — np. dopisana zawartość soli (podsumowanie
# soli od 0.5.0) nie unieważnia zapisanych planów
CALCULATED_NUTRIENTS = ("kcal", "protein", "fat", "carbs")


def _calculation_view(section: str, data: dict) -> dict:
    skip = DISPLAY_ONLY.get(section)
    if not skip:
        return data
    out = {}
    for k, item in data.items():
        view = {f: v for f, v in item.items() if f not in skip}
        if section == "products" and isinstance(view.get("per_100g"), dict):
            view["per_100g"] = {f: view["per_100g"].get(f) for f in CALCULATED_NUTRIENTS}
        out[k] = view
    return out


def _normalized(snapshot: dict) -> dict | None:
    """Kopia zapisana starszą wersją nie ma nowych pól z wartościami domyślnymi (np. płeć, limity porcji) — przed
    porównaniem przepuszczamy ją przez obecne modele, żeby zmiana schematu nie oznaczała każdego planu jako zmienionego."""
    try:
        return {"assumptions": Assumptions.model_validate(snapshot["assumptions"]).model_dump(mode="json"),
                "products": {k: Product.model_validate(p).model_dump(mode="json")
                             for k, p in (snapshot.get("products") or {}).items()},
                "dishes": {k: DishTemplate.model_validate(d).model_dump(mode="json")
                           for k, d in (snapshot.get("dishes") or {}).items()}}
    except (KeyError, TypeError, ValueError):
        return None


def matches_current(snapshot: dict, repo: Repository, plan: WeekPlan) -> bool:
    saved = _normalized(snapshot)
    if saved is None:
        return False
    now = capture(repo, plan)
    return all(_calculation_view(k, saved[k]) == _calculation_view(k, now[k])
               for k in ("assumptions", "products", "dishes"))
