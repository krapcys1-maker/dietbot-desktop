"""Prosta lista zakupów: połączone warianty, bez sklepów i przypraw. Plan pozostaje bez zmian."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from .domain.assumptions import Assumptions
from .domain.plan import WeekPlan
from .domain.product import CATEGORY_LABELS_PL, Category, Product, StoreProduct
from .textfmt import fmt_num, plural_pl
from .textnorm import normalize

# produkty, których nie kupuje się na liście (woda z kranu, napar liczony jako stała pozycja)
NOT_BOUGHT = frozenset({"woda", "kawa_napar"})
# Zioła występują także w kategorii warzywa, ale na uproszczonej liście pomijamy je jak przyprawy.
SHOPPING_HERBS = frozenset({"bazylia_swieza", "natka_pietruszki", "szczypiorek"})
SHOPPING_FAMILIES = {
    "mozzarella": ("Mozzarella", {"mozzarella", "mozzarella_light"}),
    "feta": ("Feta", {"feta", "feta_light"}),
    "jogurt": ("Jogurt", {"jogurt_naturalny_1_5", "jogurt_naturalny_3_5", "jogurt_owocowy",
                           "jogurt_owocowy_light", "jogurt_waniliowy"}),
    "skyr": ("Skyr", {"skyr_naturalny"}),
    "mleko": ("Mleko", {"mleko_1_5", "mleko_3_5"}),
    "twarog": ("Twaróg", {"twarog_chudy", "twarog_poltlusty"}),
    "serek_wiejski": ("Serek wiejski", {"serek_wiejski", "serek_wiejski_lekki"}),
    "gouda": ("Gouda", {"gouda", "ser_zolty_light"}),
    "smietana": ("Śmietana", {"smietana_10", "smietana_20"}),
    "kefir": ("Kefir", {"kefir"}),
}
VARIANT_KCAL_SPREAD = 0.15
SHOPPING_FAMILY_BY_PRODUCT = {pid: (key, name) for key, (name, ids) in SHOPPING_FAMILIES.items() for pid in ids}
SHOPPING_NOTE = "Warianty produktów są połączone, ilości zsumowane. Bez przypraw, ziół i podpowiedzi sklepów."
SHOPPING_NUTRITION_NOTE = (
    "Uproszczona nazwa nie zmienia obliczeń planu. Zakup innego wariantu (np. o innej zawartości tłuszczu) "
    "może zmienić rzeczywiste kcal i makro."
)
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
    source_product_ids: tuple[str, ...] = ()
    # połączone warianty (nazwa, gramy) i czy różnią się kalorycznością na tyle, że zakup ma znaczenie dla planu
    variants: tuple[tuple[str, float], ...] = ()
    variants_differ: bool = False

    @property
    def variants_text(self) -> str:
        return ", ".join(f"{name.lower()} {fmt_num(math.ceil(g / 10) * 10, 0)} g" for name, g in self.variants)

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
                "na_wynos": self.takeaway, "produkty_zrodlowe": list(self.source_product_ids or (self.product_id,)),
                "warianty": [{"nazwa": n, "gramy": round(g, 1)} for n, g in self.variants],
                "warianty_rozne": self.variants_differ}


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
    rounded = max(math.ceil(round(ml, 6) / 10) * 10, 10)
    if rounded >= 1000:  # 991 ml zaokrąglone w górę to litr, nie „1000 ml” (audyt N15)
        return f"{fmt_num(math.ceil(round(ml, 6) / 100) / 10, 1)} l"
    return f"{fmt_num(rounded, 0)} ml"


def _preferred(store_products: Sequence[StoreProduct], product_id: str) -> StoreProduct | None:
    options = [s for s in store_products if s.product_id == product_id]
    return next((s for s in options if s.preferred), None)


def build_detailed_shopping_list(plan: WeekPlan, a: Assumptions, products: Mapping[str, Product],
                                 store_products: Sequence[StoreProduct] = ()) -> list[ShoppingRow]:
    """Szczegółowe ilości źródłowe do kontroli; nie są listą prezentowaną użytkownikowi."""
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
                count = math.ceil(round(grams / piece.grams, 9))
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
                amount += f" (≈ {math.ceil(round(grams / piece.grams, 9))} szt.)"
        store = _preferred(store_products, pid)
        packs = pack_text = None
        if store is not None and store.package_g:
            # Zaokrąglamy wyłącznie szum float, nigdy rzeczywisty niedobór produktu.
            packs = max(1, math.ceil(round(grams / store.package_g, 9)))
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


def build_shopping_list(plan: WeekPlan, a: Assumptions, products: Mapping[str, Product],
                        store_products: Sequence[StoreProduct] = ()) -> list[ShoppingRow]:
    # Bez sklepu: po połączeniu wariantów nie ma jednego wiarygodnego rozmiaru opakowania.
    detailed = build_detailed_shopping_list(plan, a, products)
    grouped: dict[str, list[ShoppingRow]] = defaultdict(list)
    for row in detailed:
        if row.category is Category.SPICES or row.negligible or row.product_id in SHOPPING_HERBS:
            continue
        family = SHOPPING_FAMILY_BY_PRODUCT.get(row.product_id)
        key = f"zakup_{family[0]}" if family else row.product_id
        grouped[key].append(row)
    rows = []
    for key, members in grouped.items():
        first = members[0]
        family = SHOPPING_FAMILY_BY_PRODUCT.get(first.product_id)
        grams = math.fsum(row.grams for row in members)
        amount = first.amount_text
        if family:
            if all(products[row.product_id].density_g_per_ml for row in members):
                amount = _volume_text(math.fsum(row.grams / products[row.product_id].density_g_per_ml for row in members))
            else:
                amount = f"{fmt_num(math.ceil(round(grams / 10, 9)) * 10, 0)} g"
        variants: tuple[tuple[str, float], ...] = ()
        differ = False
        if family and len(members) > 1:
            variants = tuple((products[r.product_id].name, r.grams) for r in sorted(members, key=lambda r: -r.grams))
            kcal = [products[r.product_id].per_100g.kcal for r in members]
            # np. jogurt naturalny 62 kcal i owocowy 96 kcal: kupno samego naturalnego zmienia plan (audyt S14)
            differ = max(kcal) > 0 and (max(kcal) - min(kcal)) / max(kcal) > VARIANT_KCAL_SPREAD
        rows.append(replace(first, product_id=key, name=family[1] if family else first.name,
                            grams=grams, amount_text=amount, store=None, packs=None, pack_text=None,
                            source_product_ids=tuple(sorted(row.product_id for row in members)),
                            variants=variants, variants_differ=differ))
    order = {c: i for i, c in enumerate(Category)}
    return sorted(rows, key=lambda row: (row.takeaway, order[row.category], normalize(row.name)))
