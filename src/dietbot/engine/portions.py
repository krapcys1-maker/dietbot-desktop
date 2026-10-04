"""Zakresy gramatur składników dla konkretnej osoby, w krokach całkowitych."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..domain.dish import GROUP_MEMBER_STEP_G, DishIngredient, Role
from ..domain.product import Product
from .catalog import EngineInputError
from .settings import EngineSettings

_EPS = 1e-9


@dataclass(frozen=True)
class StepBounds:
    step_g: float
    lo: int  # min liczba kroków (gdy danie wybrane)
    hi: int  # max liczba kroków
    default: int  # typowa porcja w krokach

    @property
    def lo_g(self) -> float:
        return self.lo * self.step_g

    @property
    def hi_g(self) -> float:
        return self.hi * self.step_g

    @property
    def default_g(self) -> float:
        return self.default * self.step_g


def is_fixed(ing: DishIngredient, product: Product) -> bool:
    """Przyprawy i produkty pomijalne nie są zmiennymi — mają stałą ilość i zerowy wkład w bilans."""
    return ing.role is Role.SPICE or product.negligible


def step_for(ing: DishIngredient, product: Product) -> float:
    if ing.unit:
        m = product.measure(ing.unit)
        if m is None:
            raise EngineInputError(
                f"składnik {ing.product_id}: jednostka '{ing.unit}' nie ma przelicznika na gramy w bazie produktów"
            )
        return m.grams
    if ing.step_g:
        return ing.step_g
    return product.step_g


def grams_range(ing: DishIngredient, scale: float, settings: EngineSettings) -> tuple[float, float, float]:
    """(min, typowo, max) w gramach dla współczynnika skali osoby."""
    s = scale if ing.scales else 1.0
    typical = ing.typical_g * s
    lo_mult, hi_mult = settings.role_bounds.get(ing.role, (0.5, 1.5))
    lo = ing.min_g * s if ing.min_g is not None else typical * lo_mult
    hi = ing.max_g * s if ing.max_g is not None else typical * hi_mult
    if ing.optional:
        lo = 0.0
    return lo, typical, max(hi, lo)


def to_steps(lo_g: float, typical_g: float, hi_g: float, step_g: float, optional: bool) -> StepBounds:
    lo = math.ceil(lo_g / step_g - _EPS)
    hi = math.floor(hi_g / step_g + _EPS)
    if not optional and typical_g > 0:
        lo = max(lo, 1)
    if hi < lo:
        # Zakres węższy niż jeden krok (np. 1 jajko = 50 g, a zakres 30–45 g): bierzemy najbliższy krok.
        hi = lo
    default = min(max(round(typical_g / step_g), lo), hi)
    return StepBounds(step_g=step_g, lo=lo, hi=hi, default=default)


def person_bounds(ing: DishIngredient, product: Product, scale: float, settings: EngineSettings,
                  in_group: bool = False, cap_g: float | None = None) -> StepBounds:
    """`in_group` — składnik grupy proporcji (ciasto, panierka): ważony co GROUP_MEMBER_STEP_G, bez jednostek sztuk.
    `cap_g` — limit porcji z preferencji osoby; obowiązuje także wtedy, gdy przepis podaje własne maksimum."""
    lo, typical, hi = grams_range(ing, scale, settings)
    if product.max_portion_g is not None and ing.max_g is None:
        # limit produktu obowiązuje, chyba że danie jawnie podaje własne maksimum
        hi = min(hi, product.max_portion_g)
        typical = min(typical, hi)
        lo = min(lo, hi)
    if cap_g is not None:
        hi = min(hi, cap_g)
        typical = min(typical, hi)
        lo = min(lo, hi)
    step = (ing.step_g or GROUP_MEMBER_STEP_G) if in_group else step_for(ing, product)
    bounds = to_steps(lo, typical, hi, step, ing.optional)
    if cap_g is not None:
        top = math.floor(cap_g / step + _EPS)
        if top < 1 and not ing.optional:
            # najmniejsza porcja to jeden krok (np. całe jajko), a limit jest mniejszy — składnik obowiązkowy się nie zmieści
            raise EngineInputError(f"składnik {ing.product_id}: najmniejsza porcja {step:g} g przekracza limit "
                                   f"{cap_g:g} g z preferencji")
        if bounds.hi > top:
            # minimum przepisu zaokrąglone w górę do kroku mogło przekroczyć limit — limit domownika jest ważniejszy
            bounds = StepBounds(step_g=step, lo=min(bounds.lo, top), hi=top, default=min(bounds.default, top))
    return bounds


def cap_conflict(ing: DishIngredient, product: Product, scale: float, settings: EngineSettings,
                 in_group: bool, cap_g: float | None) -> bool:
    """Czy limit porcji wyklucza obowiązkowy składnik (najmniejszy krok jest większy niż limit)."""
    if cap_g is None or ing.optional:
        return False
    try:
        person_bounds(ing, product, scale, settings, in_group, cap_g)
    except EngineInputError:
        return True
    return False

