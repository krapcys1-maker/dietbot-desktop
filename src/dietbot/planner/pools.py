"""Pule dań na posiłki: twarde filtry, profil dania (etykiety słowne dla LLM) i wstępna wykonalność kotwic białka."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..domain.assumptions import Assumptions
from ..domain.diet import dish_compatible
from ..domain.dish import DishStatus, DishTemplate, MealType, Role
from ..domain.product import Category, Product
from ..engine.catalog import Catalog
from ..engine.portions import grams_range, is_fixed, person_bounds
from ..engine.settings import EngineSettings
from .balance import DEFAULT_TARGET, MacroSplit, macro_labels, target_split

PROTEIN_SOURCE_PL: dict[Category, str] = {
    Category.POULTRY: "drób",
    Category.MEAT: "mięso",
    Category.FISH: "ryba",
    Category.EGGS: "jajka",
    Category.DAIRY: "nabiał",
    Category.CHEESE: "ser",
    Category.COLD_CUTS: "wędlina",
    Category.LEGUMES: "strączki",
}
CARB_SOURCE_PL: dict[Category, str] = {
    Category.BREAD: "pieczywo",
    Category.RICE: "ryż",
    Category.PASTA: "makaron",
    Category.POTATOES: "ziemniaki",
    Category.GROATS: "kasza",
    Category.FLOUR: "mąka/ciasto",
    Category.CEREALS: "płatki",
}


EGG_SIGNIFICANT_G = 40.0  # od ok. jednego jajka w typowej porcji


@dataclass
class DishProfile:
    dish_id: str
    name: str
    cuisine: str
    tags: list[str]
    protein_sources: list[str]
    carb_sources: list[str]
    protein_label: str  # wysokie/średnie/niskie — dla osoby referencyjnej przy typowych porcjach
    energy_label: str  # lekkie/średnie/sycące
    prep_minutes: int | None
    egg_based: bool
    with_eggs: bool = False  # jajka są wyraźnym składnikiem (nie tylko np. do panierki)
    protein_range: dict[str, tuple[float, float]] = field(default_factory=dict)  # osoba -> (min, max) g
    typical: dict[str, float] = field(default_factory=dict)  # kcal/protein/fat/carbs przy typowych porcjach (2000 kcal)
    liked: bool = False

    def line(self) -> str:
        tags = [t for t in self.tags if t in ("slodkie", "pula_20", "zupa", "na_zapas", "wege", "szybkie")]
        parts = [f"{self.dish_id} | {self.name} | kuchnia: {self.cuisine}"]
        if self.protein_sources:
            parts.append("białko z: " + ", ".join(self.protein_sources))
        if self.carb_sources:
            parts.append("węgle z: " + ", ".join(self.carb_sources))
        parts.append(f"białko {self.protein_label}, {self.energy_label}")
        if tags:
            parts.append("tagi: " + ", ".join(tags))
        macro = macro_labels(self.typical)
        if macro:
            parts.append(", ".join(macro))
        if self.egg_based:
            parts.append("główne białko: jajka")
        elif self.with_eggs:
            parts.append("z jajkami")
        if self.prep_minutes:
            parts.append(f"{self.prep_minutes} min")
        if self.liked:
            parts.append("ULUBIONE użytkownika — preferuj, jeśli pasuje do zasad")
        return " | ".join(parts)


@dataclass
class Pools:
    by_meal: dict[MealType, list[str]]
    batch: dict[int, list[str]]  # indeks zasady na zapas -> dania spełniające zasadę
    profiles: dict[str, DishProfile]
    excluded: dict[str, list[str]]  # danie -> powody wykluczenia
    target: MacroSplit = DEFAULT_TARGET  # docelowy rozkład energii posiłków (bilans makro dnia)
    treat_snacks: list[str] = field(default_factory=list)  # przekąski z puli 20% (lody, chipsy…) — slot „przekąska”
    liked: set[str] = field(default_factory=set)


def _typical_nutrition(dish: DishTemplate, catalog: Catalog, settings: EngineSettings) -> dict[str, float]:
    out = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0, "mass": 0.0}
    for ing in dish.ingredients:
        _, typical, _ = grams_range(ing, 1.0, settings)
        f = catalog.facts(ing.product_id)
        for metric in ("kcal", "protein", "fat", "carbs"):
            out[metric] += f.metric(metric) * typical
        out["mass"] += typical
    return out


def protein_range(dish: DishTemplate, catalog: Catalog, settings: EngineSettings, scale: float) -> tuple[float, float]:
    lo = hi = 0.0
    for ing in dish.ingredients:
        product = catalog.product(ing.product_id)
        p = catalog.facts(ing.product_id).protein
        if is_fixed(ing, product):
            _, typical, _ = grams_range(ing, scale, settings)
            lo, hi = lo + p * typical, hi + p * typical
            continue
        b = person_bounds(ing, product, scale, settings, ing.product_id in dish.grouped_products())
        lo += p * b.lo_g
        hi += p * b.hi_g
    return lo, hi


def build_pools(dishes: Mapping[str, DishTemplate], products: Mapping[str, Product], a: Assumptions,
                settings: EngineSettings | None = None, avoid: set[str] | None = None,
                extra_banned: set[str] | None = None, ratings: Mapping[str, int] | None = None) -> Pools:
    """`extra_banned` — produkty wykluczone tylko w tym planie (np. z życzeń „bez ryb”)."""
    settings = settings or EngineSettings()
    catalog = Catalog(products, a.energy.method)
    banned = set(a.prohibitions.products) | set(extra_banned or ())
    allowed_carbs = set(a.prohibitions.allowed_carb_categories)
    by_meal: dict[MealType, list[str]] = {m: [] for m in a.day.meals}
    profiles: dict[str, DishProfile] = {}
    excluded: dict[str, list[str]] = {}
    avoid = avoid or set()
    ratings = ratings or {}

    for dish in sorted(dishes.values(), key=lambda d: d.id):
        reasons: list[str] = []
        if not dish_compatible(dish, products, a.diet):
            reasons.append(f"składniki bez potwierdzonej zgodności z dietą {a.diet}")
        if ratings.get(dish.id, 0) < 0:
            reasons.append("nie lubisz tego dania — ocena 👎")
        if dish.status is not DishStatus.APPROVED:
            reasons.append("danie niezatwierdzone")
        if dish.id in avoid:
            reasons.append("danie było niedawno — pomijamy dla różnorodności")
        missing = [i.product_id for i in dish.ingredients if i.product_id not in products]
        if missing:
            reasons.append(f"brak produktów w bazie: {', '.join(missing)}")
            excluded[dish.id] = reasons
            continue
        for ing in dish.ingredients:
            if ing.optional:
                continue
            if ing.product_id in banned:
                reasons.append(f"zakazany produkt: {ing.product_id}")
            for person in a.persons:
                if ing.product_id in person.excluded_products:
                    reasons.append(f"{person.name} nie je: {ing.product_id}")
        if not dish.is_treat and allowed_carbs:
            for ing in dish.ingredients:
                if ing.role is Role.CARBS and products[ing.product_id].category.value not in allowed_carbs:
                    reasons.append(f"źródło węglowodanów spoza dozwolonych: {ing.product_id}")

        typical = _typical_nutrition(dish, catalog, settings)
        protein, kcal = typical["protein"], typical["kcal"]
        # główne źródło białka: składnik, który przy typowych porcjach daje najwięcej białka
        contrib = [(catalog.facts(i.product_id).protein * grams_range(i, 1.0, settings)[1], i.product_id)
                   for i in dish.ingredients]
        main_pid = max(contrib)[1] if contrib else None
        egg_based = main_pid is not None and products[main_pid].category is Category.EGGS
        with_eggs = egg_based or any(products[i.product_id].category is Category.EGGS and not i.optional
                                     and grams_range(i, 1.0, settings)[1] >= EGG_SIGNIFICANT_G for i in dish.ingredients)
        profile = DishProfile(
            dish_id=dish.id, name=dish.name, cuisine=dish.cuisine, tags=list(dish.tags),
            protein_sources=sorted({PROTEIN_SOURCE_PL[products[i.product_id].category] for i in dish.ingredients
                                    if i.role is Role.PROTEIN and products[i.product_id].category in PROTEIN_SOURCE_PL}),
            carb_sources=sorted({CARB_SOURCE_PL[products[i.product_id].category] for i in dish.ingredients
                                 if i.role is Role.CARBS and products[i.product_id].category in CARB_SOURCE_PL}),
            protein_label="wysokie" if protein >= 35 else "średnie" if protein >= 25 else "niskie",
            energy_label="sycące" if kcal >= 650 else "średnie" if kcal >= 450 else "lekkie",
            prep_minutes=dish.prep_minutes, egg_based=egg_based, with_eggs=with_eggs, typical={k: round(v, 1) for k, v in typical.items()},
            liked=ratings.get(dish.id, 0) > 0,
        )
        for person in a.persons:
            scale = person.energy_center / settings.reference_kcal
            profile.protein_range[person.id] = protein_range(dish, catalog, settings, scale)
            if not dish.is_treat and MealType.SNACK not in dish.meal_types:
                lo, hi = profile.protein_range[person.id]
                tol = person.protein_anchor_tolerance
                if hi < person.protein_anchor.min - tol:
                    reasons.append(f"{person.name}: danie da maks. {hi:.0f} g białka, a kotwica to "
                                   f"{person.protein_anchor.min:.0f}–{person.protein_anchor.max:.0f} g (±{tol:.0f})")
                if lo > person.protein_anchor.max + tol:
                    reasons.append(f"{person.name}: danie da min. {lo:.0f} g białka — powyżej kotwicy")
        profiles[dish.id] = profile

        forbidden_tags = {m: set(t) for m, t in a.prohibitions.dish_tags_by_meal.items()}
        meal_notes: list[str] = []
        supper_bad_carbs = set(a.prohibitions.supper_forbidden_carb_categories)
        lunch_like = any(i.role is Role.CARBS and products[i.product_id].category.value in supper_bad_carbs
                         for i in dish.ingredients)
        for meal in a.day.meals:
            if meal not in dish.meal_types:
                continue
            if forbidden_tags.get(meal, set()) & set(dish.tags):
                continue
            # słodka kolacja (np. ryż na mleku z owocami z założeń domowników) nie jest „obiadowa”
            if meal is MealType.SUPPER and lunch_like and not dish.is_treat and not dish.is_sweet:
                meal_notes.append("kolacja z ryżem/ziemniakami/makaronem to danie obiadowe")
                continue
            if dish.is_batch:  # dania na zapas trafiają tylko do slotów wielodniowych z zasad
                continue
            if not reasons:
                by_meal[meal].append(dish.id)
        if reasons:
            excluded[dish.id] = reasons
        elif meal_notes and not dish.is_batch and not any(dish.id in ids for ids in by_meal.values()):
            excluded[dish.id] = meal_notes

    batch: dict[int, list[str]] = {}
    for idx, rule in enumerate(a.week.batch):
        batch[idx] = [d.id for d in dishes.values() if d.id not in excluded and d.is_batch and rule.tag in d.tags
                      and rule.meal in d.meal_types and (d.batch_days or 0) >= len(rule.days)]
        batch[idx].sort()
    treat_snacks = sorted(d.id for d in dishes.values() if d.id not in excluded and d.id in profiles and d.is_treat
                          and MealType.SNACK in d.meal_types)
    return Pools(by_meal=by_meal, batch=batch, profiles=profiles, excluded=excluded, target=target_split(a, products),
                 treat_snacks=treat_snacks, liked={d for d, rating in ratings.items() if rating > 0})
