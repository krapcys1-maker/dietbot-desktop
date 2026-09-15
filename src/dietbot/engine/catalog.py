"""Wartości odżywcze na 1 g dla silnika (energia wg metody z założeń)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..domain.nutrition import EnergyMethod
from ..domain.product import Product


class EngineInputError(ValueError):
    """Błędne dane wejściowe silnika (np. brak produktu) — komunikat po polsku."""


@dataclass(frozen=True)
class Facts:
    kcal: float
    protein: float
    fat: float
    carbs: float
    fiber: float

    def metric(self, name: str) -> float:
        return float(getattr(self, name))


ZERO = Facts(0.0, 0.0, 0.0, 0.0, 0.0)


class Catalog:
    def __init__(self, products: Mapping[str, Product], method: EnergyMethod):
        self._products = dict(products)
        self.method = method
        self._facts: dict[str, Facts] = {}

    def product(self, product_id: str) -> Product:
        try:
            return self._products[product_id]
        except KeyError:
            raise EngineInputError(f"brak produktu '{product_id}' w bazie produktów") from None

    def __contains__(self, product_id: str) -> bool:
        return product_id in self._products

    def facts(self, product_id: str) -> Facts:
        """Wartości na 1 g; produkty pomijalne (przyprawy) mają zera."""
        if product_id not in self._facts:
            p = self.product(product_id)
            if p.negligible:
                self._facts[product_id] = ZERO
            else:
                n = p.per_100g
                self._facts[product_id] = Facts(
                    kcal=n.energy(self.method) / 100,
                    protein=n.protein / 100,
                    fat=n.fat / 100,
                    carbs=n.carbs / 100,
                    fiber=(n.fiber or 0.0) / 100,
                )
        return self._facts[product_id]
