"""Bilans makro dnia: czy dania jednego dnia razem mają rozkład białko / tłuszcz / węglowodany zbliżony do celów.

Silnik skaluje porcje (ok. 0,3–1,8 typowej), więc kaloryczność dnia prawie zawsze da się osiągnąć, ale rozkładu makro
nie da się zmienić dowolnie: dzień z trzema tłustymi daniami nie zejdzie do 30% energii z tłuszczu bez okrajania
składników poniżej granic przepisu. Wskaźnik liczony z typowych porcji dań (w punktach procentowych energii)
przewiduje wynik silnika — na 400 losowych dniach z biblioteki (13.09.2026):

    odchylenie < 15 pp → 94% dni w celach · 15–22 pp → 79% · 22–29 pp → 59% · > 29 pp → 16%

Dlatego układ tygodnia (algorytm i autokorekta po modelu językowym) składa dni zrównoważone, zanim policzy je silnik.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..domain.assumptions import Assumptions
from ..domain.product import Product

MACROS = ("protein", "fat", "carbs")
KCAL_PER_G = {"protein": 4.0, "fat": 9.0, "carbs": 4.0}
BALANCE_OK_PP = 15.0  # poniżej — dzień zrównoważony
BALANCE_WARN_PP = 22.0  # powyżej — dzień ryzykowny (autokorekta próbuje wymienić jedno danie)
FAT_HEAVY_SHARE = 0.45  # etykieta „tłuste” dla modelu układającego tydzień
LOW_CARB_SHARE = 0.25  # etykieta „mało węgli”
HIGH_CARB_SHARE = 0.55  # etykieta „dużo węgli”


@dataclass(frozen=True)
class MacroSplit:
    """Udziały energii z białka, tłuszczu i węglowodanów (0–1)."""

    protein: float
    fat: float
    carbs: float


DEFAULT_TARGET = MacroSplit(0.25, 0.30, 0.45)


def split_of(typicals: Iterable[Mapping[str, float]]) -> MacroSplit | None:
    grams = {m: 0.0 for m in MACROS}
    for t in typicals:
        for m in MACROS:
            grams[m] += t.get(m, 0.0)
    kcal = sum(KCAL_PER_G[m] * grams[m] for m in MACROS)
    if kcal <= 0:
        return None
    return MacroSplit(*(KCAL_PER_G[m] * grams[m] / kcal for m in MACROS))


def target_split(a: Assumptions, products: Mapping[str, Product]) -> MacroSplit:
    """Docelowy rozkład energii posiłków: środki celów osób minus stałe pozycje (mleko do kawy, siemię), średnio dla osób."""
    fixed = {m: 0.0 for m in MACROS}
    for fx in a.day.fixed_items:
        product = products.get(fx.product_id)
        if product is None or product.negligible:
            continue
        for m in MACROS:
            fixed[m] += getattr(product.per_100g, m) * fx.grams / 100
    splits = []
    for person in a.persons:
        rest = {m: max(person.targets.band(m).center - fixed[m], 0.0) for m in MACROS}
        s = split_of([rest])
        if s is not None:
            splits.append(s)
    if not splits:
        return DEFAULT_TARGET
    return MacroSplit(*(sum(getattr(s, m) for s in splits) / len(splits) for m in MACROS))


def deviation_pp(split: MacroSplit, target: MacroSplit) -> float:
    """Odchylenie rozkładu w punktach procentowych energii (białko z wagą ½ — pilnują go też kotwice posiłków)."""
    return (abs(split.fat - target.fat) + abs(split.carbs - target.carbs)
            + 0.5 * abs(split.protein - target.protein)) * 100


def day_deviation(typicals: Iterable[Mapping[str, float]], target: MacroSplit) -> float:
    s = split_of(typicals)
    return 0.0 if s is None else deviation_pp(s, target)


def macro_labels(typical: Mapping[str, float]) -> list[str]:
    """Słowne znaczniki dania dla modelu układającego tydzień (względem energii samego dania)."""
    s = split_of([typical])
    if s is None:
        return []
    labels = []
    if s.fat >= FAT_HEAVY_SHARE:
        labels.append("tłuste")
    if s.carbs <= LOW_CARB_SHARE:
        labels.append("mało węgli")
    elif s.carbs >= HIGH_CARB_SHARE:
        labels.append("dużo węgli")
    return labels
