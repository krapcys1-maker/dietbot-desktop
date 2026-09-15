"""Miary domowe po polsku: gramy → "≈ ¾ łyżki", "2 kromki", "3 szt." (tylko dla miar ze źródłem w bazie)."""

from __future__ import annotations

import math

from .domain.product import Category, Product

# jednostka -> (1, 2–4, 5+, forma przy ułamku)
FORMS: dict[str, tuple[str, str, str, str]] = {
    "łyżka": ("łyżka", "łyżki", "łyżek", "łyżki"),
    "łyżeczka": ("łyżeczka", "łyżeczki", "łyżeczek", "łyżeczki"),
    "szklanka": ("szklanka", "szklanki", "szklanek", "szklanki"),
    "kromka": ("kromka", "kromki", "kromek", "kromki"),
    "plaster": ("plaster", "plastry", "plastrów", "plastra"),
    "ząbek": ("ząbek", "ząbki", "ząbków", "ząbka"),
    "kulka": ("kulka", "kulki", "kulek", "kulki"),
    "garść": ("garść", "garście", "garści", "garści"),
    "szczypta": ("szczypta", "szczypty", "szczypt", "szczypty"),
    "kawałek": ("kawałek", "kawałki", "kawałków", "kawałka"),
    "szt": ("szt.", "szt.", "szt.", "szt."),
}
# dokładność zaokrąglenia dla jednostki (nikt nie odmierza 1¾ ząbka czosnku)
STEP: dict[str, float] = {"szt": 0.5, "kawałek": 1.0, "kulka": 0.25, "plaster": 0.5, "kromka": 0.5, "ząbek": 0.5, "garść": 0.5,
                          "szklanka": 0.25, "łyżka": 0.25, "łyżeczka": 0.5, "szczypta": 1.0}
PREFERENCE = ["szt", "kawałek", "kulka", "plaster", "kromka", "ząbek", "garść", "szklanka", "łyżka", "łyżeczka", "szczypta"]
FRACTIONS = {0.25: "¼", 0.5: "½", 0.75: "¾"}
TOLERANCE = 0.15  # względna różnica między gramaturą a miarą
PRODUCE_TOLERANCE = 0.3  # owoce i warzywa na sztuki różnią się wielkością — „≈ 1½ szt.” jest wtedy wystarczające
MAX_COUNT = {"łyżka": 6, "łyżeczka": 6, "szczypta": 3}  # 8 łyżek masła to już nie miara domowa
PRODUCE = {Category.FRUIT, Category.VEGETABLES, Category.POTATOES}


def plural_form(unit: str, n: int) -> str:
    one, few, many, _ = FORMS.get(unit, (unit, unit, unit, unit))
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_count(unit: str, count: float, tolerance: float = TOLERANCE) -> str | None:
    step = STEP.get(unit, 0.25)
    # sąsiednie wielokrotności kroku — wybieramy tę o mniejszym błędzie względnym (0,73 szt. → 1 szt., nie ½)
    options = [m * step for m in {math.floor(count / step), math.ceil(count / step)} if m > 0]
    if not options:
        return None
    rounded = min(options, key=lambda r: (abs(count - r) / r, r))
    if abs(count - rounded) / rounded > tolerance:
        return None
    whole = int(rounded)
    frac = round(rounded - whole, 2)
    if frac == 0:
        return f"{whole} {plural_form(unit, whole)}"
    gen_sg = FORMS.get(unit, (unit,) * 4)[3]
    frac_txt = FRACTIONS[frac]
    return f"{frac_txt} {gen_sg}" if whole == 0 else f"{whole}{frac_txt} {gen_sg}"


def household(product: Product, grams: float) -> str | None:
    """Najczytelniejsza miara domowa dla gramatury albo None, gdy żadna nie pasuje."""
    if grams <= 0 or not product.measures:
        return None
    by_unit = {m.unit: m for m in product.measures}
    for unit in PREFERENCE:
        m = by_unit.get(unit)
        if m is None:
            continue
        count = grams / m.grams
        if count > MAX_COUNT.get(unit, 12) or count < STEP.get(unit, 0.25) * (1 - TOLERANCE):
            continue
        if unit == "łyżka" and count < 0.5:
            continue  # poniżej pół łyżki czytelniej w łyżeczkach
        tolerance = PRODUCE_TOLERANCE if unit == "szt" and product.category in PRODUCE else TOLERANCE
        txt = format_count(unit, count, tolerance)
        if txt:
            return txt
    return None
