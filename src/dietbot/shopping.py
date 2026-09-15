"""Lista zakupów jako dane: suma gramatur z planu + podpowiedź produktu z półki (np. Lidl RO) i liczby opakowań."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .domain.assumptions import Assumptions
from .domain.plan import WeekPlan
from .domain.product import CATEGORY_LABELS_PL, Category, Product, StoreProduct
from .textfmt import fmt_num, plural_pl
from .textnorm import normalize

# produkty, których nie kupuje się na liście (woda z kranu, napar liczony jako stała pozycja)
NOT_BOUGHT = frozenset({"woda", "kawa_napar"})
# jedzenie z pizzerii, budki czy baru — nie do koszyka w sklepie, tylko do zamówienia (osobna grupa na liście)
TAKEAWAY_TAG = "na_wynos"
TAKEAWAY_GROUP = "na_wynos"
TAKEAWAY_LABEL = "Na wynos — do zamówienia"


@dataclass(frozen=True)
class ShoppingRow:
    product_id: str
    name: str
    category: Category
    category_label: str
    grams: float
    amount_text: str  # "280 g (≈ 6 szt.)", "1,5 l", "" dla przypraw pomijalnych
    negligible: bool
    store: StoreProduct | None = None
    packs: int | None = None
    pack_text: str | None = None  # "2 × 180 g", "1 × 750 ml"
    takeaway: bool = False

    @property
    def group(self) -> str:
        return TAKEAWAY_GROUP if self.takeaway else self.category.value

    @property
    def group_label(self) -> str:
        return TAKEAWAY_LABEL if self.takeaway else self.category_label

    def to_dict(self) -> dict:
        store = None
        if self.store is not None:
            store = {"id": self.store.id, "sklep": self.store.store, "nazwa": self.store.name_local,
                     "marka": self.store.brand, "opakowanie_g": self.store.package_g, "uwagi": self.store.notes}
        return {"produkt": self.product_id, "nazwa": self.name, "kategoria": self.category.value,
                "kategoria_nazwa": self.category_label, "gramy": round(self.grams, 1), "ilosc": self.amount_text,
                "pomijalny": self.negligible, "sklep": store, "opakowania": self.packs, "opakowania_tekst": self.pack_text,
                "na_wynos": self.takeaway}


def totals_grams(plan: WeekPlan, a: Assumptions) -> dict[str, float]:
    """Suma gramatur na cały plan: posiłki, przekąski i stałe pozycje (np. mleko do kawy) dla wszystkich osób."""
    total: dict[str, float] = defaultdict(float)
    for m in plan.meals:
        for p in m.portions:
            for i in p.items:
                total[i.product_id] += i.grams
    for s in plan.snacks:
        for i in s.items:
            total[i.product_id] += i.grams
    for fx in a.day.fixed_items:
        total[fx.product_id] += fx.grams * len(plan.days) * len(a.persons)
    return dict(total)


def _volume_text(ml: float) -> str:
    if ml >= 995:  # 999 ml zaokrąglone w górę to litr, nie „1000 ml”
        return f"{fmt_num(math.ceil(ml / 100) / 10, 1)} l"
    return f"{fmt_num(max(math.ceil(ml / 10) * 10, 10), 0)} ml"


def _preferred(store_products: Sequence[StoreProduct], product_id: str) -> StoreProduct | None:
    options = [s for s in store_products if s.product_id == product_id]
    return next((s for s in options if s.preferred), None)


def build_shopping_list(plan: WeekPlan, a: Assumptions, products: Mapping[str, Product],
                        store_products: Sequence[StoreProduct] = ()) -> list[ShoppingRow]:
    rows: list[ShoppingRow] = []
    for pid, grams in totals_grams(plan, a).items():
        product = products.get(pid)
        if product is None or grams <= 0 or pid in NOT_BOUGHT:
            continue
        label = CATEGORY_LABELS_PL[product.category]
        if TAKEAWAY_TAG in product.tags:
            piece = product.measure("kawałek") or product.measure("szt")
            amount = f"{fmt_num(round(grams), 0)} g"
            if piece:
                count = math.ceil(grams / piece.grams - 0.05)
                unit = plural_pl(count, "kawałek", "kawałki", "kawałków") if piece.unit == "kawałek" else "szt."
                amount += f" (≈ {count} {unit})"
            rows.append(ShoppingRow(pid, product.name, product.category, label, grams, amount, False, takeaway=True))
            continue
        if product.negligible:
            rows.append(ShoppingRow(pid, product.name, product.category, label, grams, "", True))
            continue
        if product.density_g_per_ml:
            amount = _volume_text(grams / product.density_g_per_ml)
        else:
            amount = f"{fmt_num(max(math.ceil(grams / 10) * 10, 10), 0)} g"
            piece = product.measure("szt")
            if piece:
                amount += f" (≈ {math.ceil(grams / piece.grams - 0.05)} szt.)"
        store = _preferred(store_products, pid)
        packs = pack_text = None
        if store is not None and store.package_g:
            packs = max(1, math.ceil(grams / store.package_g - 0.02))  # 2% luzu: 505 g przy paczce 500 g to 1 paczka
            piece = product.measure("szt")
            pieces = round(store.package_g / piece.grams) if piece else 0
            if product.density_g_per_ml:
                size = f"{fmt_num(round(store.package_g / product.density_g_per_ml / 10) * 10, 0)} ml"
            elif piece and pieces >= 1 and abs(store.package_g - pieces * piece.grams) <= 0.1 * store.package_g:
                size = f"{pieces} szt."  # jajka po 10, bułki na sztuki
            else:
                size = f"{fmt_num(store.package_g, 0)} g"
            pack_text = f"{packs} × {size}" if size != "1 szt." else f"{packs} szt."
        rows.append(ShoppingRow(pid, product.name, product.category, label, grams, amount, False, store, packs,
                                pack_text))
    order = {c: i for i, c in enumerate(Category)}
    rows.sort(key=lambda r: (r.takeaway, order[r.category], normalize(r.name)))
    return rows
