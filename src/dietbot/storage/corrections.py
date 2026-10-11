"""Korekty błędnych wartości w bazie produktów — także w istniejących instalacjach.

Instalator nigdy nie nadpisuje plików danych użytkownika (mogły zostać zmienione w aplikacji), więc poprawka wpisana
tylko do `dane/produkty/*.yaml` trafiłaby jedynie do nowych instalacji. Plik korekt (`dane/korekty/*.yaml`) jest
nowym plikiem, więc trafia do każdej instalacji, a korekta działa przy wczytywaniu danych: produkt jest poprawiany
tylko wtedy, gdy nadal ma dokładnie stare, błędne wartości (`bylo`) — własnych zmian użytkownika nie ruszamy.
Zapisane plany zachowują swoją kopię danych; aplikacja zaproponuje ich przeliczenie.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..domain.product import Product
from .yamlio import load_plain

MATCH_TOLERANCE = 0.011  # wartości w YAML mają najwyżej dwa miejsca po przecinku


class Correction(BaseModel):
    product_id: str = Field(alias="produkt")
    reason: str = Field(alias="powod", min_length=5)
    before: dict[str, float] = Field(alias="bylo", min_length=1)  # klucze jak w na_100g (kcal, tluszcz, …)
    change: dict = Field(alias="zmiana", min_length=1)  # pola wpisu produktu (np. na_100g, zrodlo) — w całości


def _matches(product: Product, before: dict[str, float]) -> bool:
    current = product.per_100g.model_dump(by_alias=True)
    for key, value in before.items():
        have = current.get(key)
        if have is None or abs(float(have) - float(value)) > MATCH_TOLERANCE:
            return False
    return True


def apply_corrections(products: MutableMapping[str, Product], directory: Path, cache_dir: Path | None = None,
                      problems: list[str] | None = None) -> list[str]:
    """Poprawia produkty w miejscu. Zwraca opisy zastosowanych korekt (do dziennika)."""
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
