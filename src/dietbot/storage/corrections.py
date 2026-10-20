"""Korekty błędnych wartości w bazie produktów — także w istniejących instalacjach.

Instalator nigdy nie nadpisuje plików danych użytkownika (mogły zostać zmienione w aplikacji), więc poprawka wpisana
tylko do `dane/produkty/*.yaml` trafiłaby jedynie do nowych instalacji. Plik korekt (`dane/korekty/*.yaml`) jest
nowym plikiem, więc trafia do każdej instalacji, a korekta działa przy wczytywaniu danych: produkt jest poprawiany
tylko wtedy, gdy nadal ma dokładnie stare, błędne wartości (`bylo`) — własnych zmian użytkownika nie ruszamy.
Zapisane plany zachowują swoją kopię danych; aplikacja zaproponuje ich przeliczenie.

Ten sam folder poprawia przepisy (wpis z kluczem `danie`): typowe gramatury składników zmieniamy tylko wtedy, gdy
przepis nadal ma dokładnie stare ilości z pola `bylo`.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..domain.dish import DishTemplate
from ..domain.product import Product
from .yamlio import load_plain

MATCH_TOLERANCE = 0.011  # wartości w YAML mają najwyżej dwa miejsca po przecinku


class Correction(BaseModel):
    product_id: str = Field(alias="produkt")
    reason: str = Field(alias="powod", min_length=5)
    before: dict[str, float] = Field(alias="bylo", min_length=1)  # klucze jak w na_100g albo pole wpisu
    change: dict = Field(alias="zmiana", min_length=1)  # pola wpisu produktu (np. na_100g, zrodlo) — w całości


class DishCorrection(BaseModel):
    dish_id: str = Field(alias="danie")
    reason: str = Field(alias="powod", min_length=5)
    before: dict[str, float] = Field(alias="bylo", min_length=1)  # produkt → typowo_g w przepisie przed poprawką
    # produkt → nowe typowo_g albo pola składnika ({typowo_g, min_g, max_g}), gdy przepis ma własne granice
    change: dict[str, float | dict[str, float]] = Field(alias="zmiana", min_length=1)


def _dish_matches(dish: DishTemplate, before: dict[str, float]) -> bool:
    typical = {i.product_id: i.typical_g for i in dish.ingredients}
    return all(pid in typical and abs(typical[pid] - g) <= MATCH_TOLERANCE for pid, g in before.items())


def _matches(product: Product, before: dict[str, float]) -> bool:
    # wartości na 100 g albo liczbowe pola wpisu (np. max_porcja_g)
    data = product.to_data()
    current = {**{k: v for k, v in data.items() if isinstance(v, int | float)},
               **product.per_100g.model_dump(by_alias=True)}
    for key, value in before.items():
        have = current.get(key)
        if have is None or abs(float(have) - float(value)) > MATCH_TOLERANCE:
            return False
    return True


def apply_corrections(products: MutableMapping[str, Product], directory: Path, cache_dir: Path | None = None,
                      problems: list[str] | None = None,
                      dishes: MutableMapping[str, DishTemplate] | None = None) -> list[str]:
    """Poprawia produkty (i przepisy, gdy podano `dishes`) w miejscu. Zwraca opisy zastosowanych korekt."""
    applied: list[str] = []
    if not directory.is_dir():
        return applied
    for path in sorted(p for p in directory.glob("*.yaml") if not p.name.startswith("_")):
        try:
            raw = load_plain(path, cache_dir) or []
        except Exception as exc:  # uszkodzony plik korekt nie może zablokować startu
            if problems is not None:
                problems.append(f"{path.name}: nie da się odczytać korekt ({type(exc).__name__}) — pominięto")
                continue
            raise
        for entry in raw:
            if isinstance(entry, dict) and "danie" in entry:
                if dishes is not None:
                    _apply_dish(dishes, entry, path.name, applied, problems)
                continue
            try:
                fix = Correction.model_validate(entry)
            except ValidationError as exc:
                if problems is None:
                    raise ValueError(f"{path.name}: błędna korekta: {exc}") from None
                problems.append(f"{path.name}: błędna korekta — pominięto")
                continue
            product = products.get(fix.product_id)
            if product is None or not _matches(product, fix.before):
                continue
            try:
                products[fix.product_id] = Product.model_validate({**product.to_data(), **fix.change})
            except ValidationError as exc:
                if problems is None:
                    raise ValueError(f"{path.name} / {fix.product_id}: korekta daje błędny produkt: {exc}") from None
                problems.append(f"{path.name} / {fix.product_id}: korekta daje błędny produkt — pominięto")
                continue
            applied.append(f"Korekta danych: {product.name} — {fix.reason}")
    return applied


def _apply_dish(dishes: MutableMapping[str, DishTemplate], entry: dict, file: str, applied: list[str],
                problems: list[str] | None) -> None:
    def fail(message: str) -> None:
        if problems is None:
            raise ValueError(f"{file}: {message}")
        problems.append(f"{file}: {message} — pominięto")

    try:
        fix = DishCorrection.model_validate(entry)
    except ValidationError as exc:
        fail(f"błędna korekta przepisu: {exc}" if problems is None else "błędna korekta przepisu")
        return
    dish = dishes.get(fix.dish_id)
    if dish is None or not _dish_matches(dish, fix.before):
        return  # przepis usunięty albo zmieniony przez użytkownika — nie ruszamy
    data = dish.to_data()
    found = set()
    for ing in data["skladniki"]:
        new = fix.change.get(ing["produkt"])
        if new is not None:
            ing.update(new if isinstance(new, dict) else {"typowo_g": new})
            found.add(ing["produkt"])
    if found != set(fix.change):
        fail(f"{fix.dish_id}: korekta zmienia składniki spoza przepisu ({', '.join(sorted(set(fix.change) - found))})")
        return
    try:
        dishes[fix.dish_id] = DishTemplate.model_validate(data)
    except ValidationError as exc:
        fail(f"{fix.dish_id}: korekta daje błędny przepis: {exc}" if problems is None
             else f"{fix.dish_id}: korekta daje błędny przepis")
        return
    applied.append(f"Korekta przepisu: {dish.name} — {fix.reason}")
