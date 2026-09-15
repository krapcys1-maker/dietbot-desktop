"""Wczytywanie i kontrola spójności danych: produkty, dania, założenia."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from ..domain.assumptions import Assumptions
from ..domain.dish import DishStatus, DishTemplate, MealType, Role
from ..domain.product import Category, Product, StoreProduct
from ..textnorm import normalize
from .paths import Paths
from .yamlio import load_plain


class DataError(ValueError):
    """Błąd w plikach danych — komunikat po polsku z nazwą pliku i pozycji."""


def _format_validation(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def _yaml_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.yaml") if not p.name.startswith("_"))


def load_products(directory: Path, cache_dir: Path | None = None) -> dict[str, Product]:
    products: dict[str, Product] = {}
    for path in _yaml_files(directory):
        raw = load_plain(path, cache_dir) or []
        if not isinstance(raw, list):
            raise DataError(f"{path.name}: plik produktów musi być listą")
        for i, entry in enumerate(raw):
            ident = entry.get("id", f"pozycja {i + 1}") if isinstance(entry, dict) else f"pozycja {i + 1}"
            try:
                product = Product.model_validate(entry)
            except ValidationError as exc:
                raise DataError(f"{path.name} / {ident}: {_format_validation(exc)}") from None
            if product.id in products:
                raise DataError(f"{path.name}: zduplikowane id produktu '{product.id}'")
            products[product.id] = product
    return products


def load_dishes(directory: Path, cache_dir: Path | None = None) -> dict[str, DishTemplate]:
    dishes: dict[str, DishTemplate] = {}
    for path in _yaml_files(directory):
        raw = load_plain(path, cache_dir) or []
        if not isinstance(raw, list):
            raise DataError(f"{path.name}: plik dań musi być listą")
        for i, entry in enumerate(raw):
            ident = entry.get("id", f"pozycja {i + 1}") if isinstance(entry, dict) else f"pozycja {i + 1}"
            try:
                dish = DishTemplate.model_validate(entry)
            except ValidationError as exc:
                raise DataError(f"{path.name} / {ident}: {_format_validation(exc)}") from None
            if dish.id in dishes:
                raise DataError(f"{path.name}: zduplikowane id dania '{dish.id}'")
            dishes[dish.id] = dish
    return dishes


def load_store_products(directory: Path, cache_dir: Path | None = None) -> list[StoreProduct]:
    items: list[StoreProduct] = []
    seen: set[str] = set()
    for path in _yaml_files(directory):
        raw = load_plain(path, cache_dir) or []
        for i, entry in enumerate(raw):
            ident = entry.get("id", f"pozycja {i + 1}") if isinstance(entry, dict) else f"pozycja {i + 1}"
            try:
                sp = StoreProduct.model_validate(entry)
            except ValidationError as exc:
                raise DataError(f"{path.name} / {ident}: {_format_validation(exc)}") from None
            if sp.id in seen:
                raise DataError(f"{path.name}: zduplikowane id produktu sklepowego '{sp.id}'")
            seen.add(sp.id)
            items.append(sp)
    return items


def load_assumptions(path: Path, cache_dir: Path | None = None) -> Assumptions:
    try:
        return Assumptions.model_validate(load_plain(path, cache_dir))
    except ValidationError as exc:
        raise DataError(f"{path.name}: {_format_validation(exc)}") from None


@dataclass
class CheckReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class Repository:
    paths: Paths
    products: dict[str, Product]
    dishes: dict[str, DishTemplate]
    assumptions: Assumptions
    store_products: list[StoreProduct] = field(default_factory=list)

    @classmethod
    def load(cls, paths: Paths | None = None, use_cache: bool = True) -> Repository:
        """Wczytuje dane; sparsowane pliki trzyma w pamięci podręcznej (wyniki/cache), więc kolejne uruchomienia
        i przeładowania po edycji jednego pliku trwają ułamek sekundy zamiast kilku sekund."""
        paths = paths or Paths.discover()
        cache = paths.cache if use_cache else None
        return cls(
            paths=paths,
            products=load_products(paths.products_dir, cache),
            dishes=load_dishes(paths.dishes_dir, cache),
            assumptions=load_assumptions(paths.assumptions, cache),
            store_products=load_store_products(paths.stores_dir, cache),
        )

    def preferred_store_product(self, product_id: str) -> StoreProduct | None:
        options = [s for s in self.store_products if s.product_id == product_id]
        preferred = [s for s in options if s.preferred]
        return (preferred or options or [None])[0]

    def approved_dishes(self) -> dict[str, DishTemplate]:
        return {k: d for k, d in self.dishes.items() if d.status is DishStatus.APPROVED}

    def check(self) -> CheckReport:
        rep = check_consistency(self.products, self.dishes, self.assumptions)
        preferred: dict[str, list[str]] = {}
        for sp in self.store_products:
            if sp.product_id not in self.products:
                rep.errors.append(f"sklep: '{sp.id}' wskazuje nieistniejący produkt '{sp.product_id}'")
            if sp.preferred:
                preferred.setdefault(sp.product_id, []).append(sp.id)
            if sp.per_100g is not None and sp.per_100g.kcal_mismatch() > 0.15:
                rep.warnings.append(f"sklep: '{sp.id}' — kcal z etykiety nie zgadzają się z makro (sprawdź przepisanie)")
        for pid, ids in preferred.items():
            if len(ids) > 1:
                rep.errors.append(f"sklep: produkt '{pid}' ma kilka preferowanych pozycji: {', '.join(ids)}")
        return rep


def check_consistency(products: dict[str, Product], dishes: dict[str, DishTemplate], a: Assumptions) -> CheckReport:
    rep = CheckReport()

    # aliasy: ten sam znormalizowany alias nie może wskazywać dwóch produktów
    alias_owner: dict[str, str] = {}
    for p in products.values():
        for name in [p.name, *p.aliases]:
            key = normalize(name)
            owner = alias_owner.get(key)
            if owner and owner != p.id:
                rep.errors.append(f"alias '{name}' wskazuje dwa produkty: {owner} i {p.id}")
            alias_owner[key] = p.id
        if not p.negligible:
            mismatch = p.per_100g.kcal_mismatch()
            if mismatch > 0.08:
                rep.warnings.append(
                    f"{p.id}: kcal z tabeli ({p.per_100g.kcal:.0f}) różni się o {mismatch:.0%} od wyliczonych z makro "
                    f"({p.per_100g.label_energy_from_macros():.0f}) — sprawdź źródło"
                )
        if p.state.value == "surowy" and p.category in (Category.BREAD,):
            rep.warnings.append(f"{p.id}: pieczywo oznaczone jako surowe")

    for d in dishes.values():
        for ing in d.ingredients:
            if ing.product_id not in products:
                rep.errors.append(f"danie {d.id}: brak produktu '{ing.product_id}' w bazie")
                continue
            product = products[ing.product_id]
            if ing.unit and product.measure(ing.unit) is None:
                rep.errors.append(f"danie {d.id}: produkt {ing.product_id} nie ma miary '{ing.unit}'")
            if ing.role is Role.SPICE and not product.negligible and ing.typical_g > 5:
                rep.warnings.append(f"danie {d.id}: {ing.product_id} jako przyprawa w ilości {ing.typical_g} g")
        if MealType.SNACK in d.meal_types and len(d.meal_types) > 1:
            rep.warnings.append(f"danie {d.id}: przekąska oznaczona też jako posiłek główny")

    for item in a.day.fixed_items:
        if item.product_id not in products:
            rep.errors.append(f"założenia: stała pozycja — brak produktu '{item.product_id}'")
    for snack_id in a.day.snacks.allowed:
        dish = dishes.get(snack_id)
        if dish is None:
            rep.errors.append(f"założenia: przekąska '{snack_id}' nie istnieje w bibliotece dań")
        elif MealType.SNACK not in dish.meal_types:
            rep.errors.append(f"założenia: '{snack_id}' nie jest daniem typu przekąska")
    for pid in a.prohibitions.products:
        if pid not in products:
            rep.errors.append(f"założenia: zakazany produkt '{pid}' nie istnieje w bazie")
    valid_categories = {c.value for c in Category}
    for cat in a.prohibitions.allowed_carb_categories:
        if cat not in valid_categories:
            rep.errors.append(f"założenia: nieznana kategoria węglowodanów '{cat}'")
    for person in a.persons:
        for pid in person.excluded_products:
            if pid not in products:
                rep.errors.append(f"założenia: {person.name} wyklucza nieistniejący produkt '{pid}'")
    rep.warnings.extend(a.consistency_warnings())
    return rep


def iter_dish_products(dishes: Iterable[DishTemplate]) -> set[str]:
    return {i.product_id for d in dishes for i in d.ingredients}
