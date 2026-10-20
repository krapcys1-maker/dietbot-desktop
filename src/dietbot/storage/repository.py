"""Wczytywanie i kontrola spójności danych: produkty, dania, założenia."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic import ValidationError

from ..domain.assumptions import Assumptions
from ..domain.culinary import protect_recipe
from ..domain.diet import DIET_INSTRUMENTAL_PL, compatible, dish_compatible
from ..domain.dish import DishStatus, DishTemplate, MealType, Role
from ..domain.product import Category, Product, StoreProduct
from ..textfmt import fmt_num
from ..textnorm import normalize
from .corrections import apply_corrections
from .paths import Paths
from .yamlio import dump_yaml, load_plain, load_yaml


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


def _fail(message: str, problems: list[str] | None) -> None:
    """Tryb ścisły (testy, `dietbot dane sprawdz`): błąd. Tryb aplikacji: wpis pomijamy i zapisujemy ostrzeżenie —
    jeden wadliwy wpis w pliku użytkownika nie może zablokować startu programu."""
    if problems is None:
        raise DataError(message)
    problems.append(message + " — pominięto ten wpis")


def _entries(path: Path, cache_dir: Path | None, what: str, problems: list[str] | None) -> list:
    try:
        raw = load_plain(path, cache_dir) or []
    except Exception as exc:  # uszkodzony YAML (np. ręczna edycja) — w aplikacji pomijamy cały plik
        _fail(f"{path.name}: nie da się odczytać pliku ({type(exc).__name__})", problems)
        return []
    if not isinstance(raw, list):
        _fail(f"{path.name}: plik {what} musi być listą", problems)
        return []
    return raw


def _ident(entry, i: int) -> str:
    return str(entry.get("id", f"pozycja {i + 1}")) if isinstance(entry, dict) else f"pozycja {i + 1}"


def load_products(directory: Path, cache_dir: Path | None = None,
                  problems: list[str] | None = None) -> dict[str, Product]:
    products: dict[str, Product] = {}
    for path in _yaml_files(directory):
        for i, entry in enumerate(_entries(path, cache_dir, "produktów", problems)):
            try:
                product = Product.model_validate(entry)
            except ValidationError as exc:
                _fail(f"{path.name} / {_ident(entry, i)}: {_format_validation(exc)}", problems)
                continue
            if product.id in products:
                _fail(f"{path.name}: zduplikowane id produktu '{product.id}'", problems)
                continue
            products[product.id] = product
    return products


def load_dishes(directory: Path, cache_dir: Path | None = None,
                problems: list[str] | None = None) -> dict[str, DishTemplate]:
    dishes: dict[str, DishTemplate] = {}
    for path in _yaml_files(directory):
        for i, entry in enumerate(_entries(path, cache_dir, "dań", problems)):
            try:
                dish = DishTemplate.model_validate(entry)
            except ValidationError as exc:
                _fail(f"{path.name} / {_ident(entry, i)}: {_format_validation(exc)}", problems)
                continue
            if dish.id in dishes:
                _fail(f"{path.name}: zduplikowane id dania '{dish.id}'", problems)
                continue
            dishes[dish.id] = dish
    return dishes


def load_store_products(directory: Path, cache_dir: Path | None = None,
                        problems: list[str] | None = None) -> list[StoreProduct]:
    items: list[StoreProduct] = []
    seen: set[str] = set()
    for path in _yaml_files(directory):
        for i, entry in enumerate(_entries(path, cache_dir, "produktów sklepowych", problems)):
            try:
                sp = StoreProduct.model_validate(entry)
            except ValidationError as exc:
                _fail(f"{path.name} / {_ident(entry, i)}: {_format_validation(exc)}", problems)
                continue
            if sp.id in seen:
                _fail(f"{path.name}: zduplikowane id produktu sklepowego '{sp.id}'", problems)
                continue
            seen.add(sp.id)
            items.append(sp)
    return items


def load_assumptions(path: Path, cache_dir: Path | None = None) -> Assumptions:
    raw = load_plain(path, cache_dir)
    energy = raw.get("energia", {}) if isinstance(raw, dict) else None
    migrate = isinstance(energy, dict) and energy.get("metoda") in (None, "makro_4_9_4")
    if migrate:
        # Instalator zachowuje własny YAML użytkownika. Migrujemy wyłącznie metodę liczenia,
        # nie cele ani gramatury. Błędna konfiguracja nie jest zapisywana.
        raw = {**raw, "energia": {**energy, "metoda": "etykieta"}}
    try:
        assumptions = Assumptions.model_validate(raw)
    except ValidationError as exc:
        raise DataError(f"{path.name}: {_format_validation(exc)}") from None
    if migrate:
        backup = path.with_name(path.name + ".przed-kaloriami-z-tabel.bak")
        try:
            with backup.open("xb") as f:
                f.write(path.read_bytes())
        except FileExistsError:
            pass  # nie nadpisujemy pierwszej kopii przy kolejnych uruchomieniach
        doc = load_yaml(path)
        doc["energia"] = raw["energia"]
        dump_yaml(doc, path)
    return assumptions


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
    dish_ratings: dict[str, int] = field(default_factory=dict)
    # wskazówki z notatnika preferencji dla układającego tydzień (założenia z zasadami nakłada usługa aplikacji)
    preference_hints: list[str] = field(default_factory=list)
    # wpisy pominięte przy wczytywaniu w trybie aplikacji (np. zduplikowane id w pliku użytkownika)
    load_problems: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, paths: Paths | None = None, use_cache: bool = True, strict: bool = True) -> Repository:
        """Wczytuje dane; sparsowane pliki trzyma w pamięci podręcznej (wyniki/cache), więc kolejne uruchomienia
        i przeładowania po edycji jednego pliku trwają ułamek sekundy zamiast kilku sekund.

        `strict=False` (aplikacja): wadliwe wpisy produktów, dań i półek są pomijane z ostrzeżeniem w
        `load_problems`, zamiast blokować start. Błędne założenia nadal zatrzymują wczytanie."""
        paths = paths or Paths.discover()
        cache = paths.cache if use_cache else None
        problems: list[str] | None = None if strict else []
        products = load_products(paths.products_dir, cache, problems)
        dishes = load_dishes(paths.dishes_dir, cache, problems)
        corrections = apply_corrections(products, paths.data / "korekty", cache, problems, dishes)
        return cls(
            paths=paths,
            products=products,
            dishes={k: protect_recipe(d, products) for k, d in dishes.items()},
            assumptions=load_assumptions(paths.assumptions, cache),
            store_products=load_store_products(paths.stores_dir, cache, problems),
            load_problems=[*(problems or []), *corrections],
        )

    def preferred_store_product(self, product_id: str) -> StoreProduct | None:
        options = [s for s in self.store_products if s.product_id == product_id]
        preferred = [s for s in options if s.preferred]
        return (preferred or options or [None])[0]

    def approved_dishes(self) -> dict[str, DishTemplate]:
        return {k: d for k, d in self.dishes.items()
                if d.status is DishStatus.APPROVED and self.dish_ratings.get(k, 0) >= 0}

    def for_planning(self, extra_banned: Iterable[str] = ()) -> Repository:
        """Oceny nie zmieniają zapisanych założeń ani starych planów; wyłączają też przekąski bilansujące.
        Przekąski z zakazanym produktem (z założeń albo z życzeń tego planu: `extra_banned`) też odpadają."""
        a = self.assumptions.model_copy(deep=True)
        banned = set(a.prohibitions.products) | set(extra_banned)
        a.day.snacks.allowed = [d for d in a.day.snacks.allowed if self.dish_ratings.get(d, 0) >= 0
                               and d in self.dishes and dish_compatible(self.dishes[d], self.products, a.diet)
                               and not any(i.product_id in banned for i in self.dishes[d].ingredients)]
        return replace(self, assumptions=a, dish_ratings=dict(self.dish_ratings))

    def check(self) -> CheckReport:
        rep = check_consistency(self.products, self.dishes, self.assumptions)
        rep.warnings.extend(p for p in self.load_problems if "pominięto" in p)
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

    for item in a.day.fixed_items:
        if item.product_id in products and not compatible(products[item.product_id], a.diet):
            rep.errors.append(f"Stała pozycja „{products[item.product_id].name}” nie ma potwierdzonej zgodności z dietą {DIET_INSTRUMENTAL_PL.get(a.diet, a.diet)}. "
                              "Usuń ją lub wybierz zgodny zamiennik w sekcji Dzień (np. napój sojowy zamiast mleka).")

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
                rep.warnings.append(f"Danie „{d.name}”: {product.name.lower()} jako przyprawa w ilości {fmt_num(ing.typical_g)} g")
        if MealType.SNACK in d.meal_types and len(d.meal_types) > 1:
            rep.warnings.append(f"danie {d.id}: przekąska oznaczona też jako posiłek główny")

    seen_fixed: set[str] = set()
    for item in a.day.fixed_items:
        if item.product_id not in products:
            rep.errors.append(f"założenia: stała pozycja — brak produktu '{item.product_id}'")
            continue
        name = products[item.product_id].name
        if item.product_id in seen_fixed:
            rep.errors.append(f"Stała pozycja „{name}” jest wpisana dwa razy — połącz ilości w jednej pozycji.")
        seen_fixed.add(item.product_id)
        if item.product_id in a.prohibitions.products:
            rep.errors.append(f"Stała pozycja „{name}” jest na liście zakazanych produktów — usuń ją z dnia albo z zakazów.")
        for person in a.persons:
            if item.product_id in person.excluded_products:
                rep.errors.append(f"Stała pozycja „{name}” jest wykluczona przez osobę {person.name} — stałe pozycje "
                                  "je każdy, usuń ją z dnia albo z wykluczeń.")
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
