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


def matches_current(snapshot: dict, repo: Repository, plan: WeekPlan) -> bool:
    now = capture(repo, plan)
    return all(snapshot.get(k) == now[k] for k in ("assumptions", "products", "dishes"))
