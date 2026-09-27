"""Szablon dania: składniki z rolami i zakresami gramatur (liczy je silnik, nie LLM)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .common import DomainModel, check_slug


class MealType(StrEnum):
    BREAKFAST = "sniadanie"
    LUNCH = "obiad"
    SUPPER = "kolacja"
    SNACK = "przekaska"


MEAL_LABELS_PL: dict[MealType, str] = {
    MealType.BREAKFAST: "Śniadanie",
    MealType.LUNCH: "Obiad",
    MealType.SUPPER: "Kolacja",
    MealType.SNACK: "Przekąska",
}

# klucze kuchni w danych są bez polskich znaków — w tekstach dla ludzi używamy pełnych nazw
CUISINE_LABELS_PL: dict[str, str] = {
    "polska": "polska",
    "wloska": "włoska",
    "azjatycka": "azjatycka",
    "miedzynarodowa": "międzynarodowa",
    "wege": "wegetariańska",
    "hiszpanska": "hiszpańska",
    "indyjska": "indyjska",
    "meksykanska": "meksykańska",
    "francuska": "francuska",
    "grecka": "grecka",
    "turecka": "turecka",
    "amerykanska": "amerykańska",
    "rumunska": "rumuńska",
    "slaska": "śląska",
    "bliskowschodnia": "bliskowschodnia",
}


def cuisine_label(key: str | None) -> str:
    if not key:
        return ""
    return CUISINE_LABELS_PL.get(key, key.replace("_", " "))


class Role(StrEnum):
    PROTEIN = "bialko"
    CARBS = "weglowodany"
    FAT = "tluszcz"
    VEGETABLES = "warzywa"
    FRUIT = "owoce"
    DAIRY = "nabial"
    SAUCE = "sos"
    ADDON = "dodatek"
    SPICE = "przyprawa"


class Portioning(StrEnum):
    PER_PERSON = "osobno"  # każdy talerz osobno (ryż, pieczywo, filet)
    SHARED = "z_garnka"  # jednorodna masa z jednego garnka/patelni — dzielona proporcjonalnie
    EQUAL = "po_rowno"  # tyle samo dla każdej osoby


class DishTag(StrEnum):
    SWEET = "slodkie"
    TREAT = "pula_20"  # posiłek z puli 20% (rekreacyjny)
    BATCH = "na_zapas"  # gotowane raz, jedzone przez kilka dni
    SOUP = "zupa"
    VEGETARIAN = "wege"
    WARM = "na_cieplo"
    QUICK = "szybkie"


class DishIngredient(DomainModel):
    product_id: str = Field(alias="produkt")
    role: Role = Field(alias="rola")
    portioning: Portioning = Field(default=Portioning.PER_PERSON, alias="porcjowanie")
    # Gramatury dla osoby referencyjnej (2000 kcal); dla konkretnej osoby skalowane jej celem energii.
    typical_g: float = Field(alias="typowo_g", ge=0, le=2000)
    min_g: float | None = Field(default=None, alias="min_g", ge=0, le=2000)
    max_g: float | None = Field(default=None, alias="max_g", ge=0, le=3000)
    unit: str | None = Field(default=None, alias="jednostka")  # np. "szt" — krok = miara produktu
    step_g: float | None = Field(default=None, alias="krok_g", gt=0, le=500)
    optional: bool = Field(default=False, alias="opcjonalny")
    scales: bool = Field(default=True, alias="skaluje_sie")
    note: str | None = Field(default=None, alias="uwagi")

    @field_validator("product_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        return check_slug(v, "id produktu")

    @model_validator(mode="after")
    def _bounds(self) -> DishIngredient:
        lo = self.min_g if self.min_g is not None else 0.0
        hi = self.max_g if self.max_g is not None else float("inf")
        if lo > hi:
            raise ValueError(f"{self.product_id}: min_g {lo} > max_g {hi}")
        if self.typical_g > 0 and not (lo <= self.typical_g <= hi):
            raise ValueError(f"{self.product_id}: typowo_g {self.typical_g} poza zakresem [{lo}, {hi}]")
        if self.typical_g == 0 and not self.optional and self.role is not Role.SPICE:
            raise ValueError(f"{self.product_id}: typowo_g = 0 dozwolone tylko dla składnika opcjonalnego")
        return self


class Ratio(DomainModel):
    """Proporcja w daniu: suma(licznik) / suma(mianownik) w [min, max] — w gramach, dla każdej osoby."""

    numerator: list[str] = Field(alias="licznik", min_length=1)
    denominator: list[str] = Field(alias="mianownik", min_length=1)
    min: float | None = Field(default=None, ge=0)
    max: float | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, alias="opis")

    @model_validator(mode="after")
    def _some_bound(self) -> Ratio:
        if self.min is None and self.max is None:
            raise ValueError("proporcja wymaga min lub max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("proporcja: min > max")
        return self


# składniki grup ważymy co 1 g (chyba że składnik ma własny krok_g) — przy krokach 5–10 g małe składniki masy
# (cebula w mielonym, olej w cieście) traciłyby proporcję przy zaokrąglaniu
GROUP_MEMBER_STEP_G = 1.0


class IngredientGroup(DomainModel):
    """Jedna masa w przepisie (ciasto, panierka, marynata, sos, farsz): silnik zmienia jej ilość, ale zachowuje
    proporcje składników z przepisu — udział każdego składnika w masie grupy jak w `typowo_g` (± tolerancja)."""

    name: str = Field(alias="nazwa", min_length=2, max_length=60)
    products: list[str] = Field(alias="skladniki", min_length=2)
    tolerance: float | None = Field(default=None, alias="tolerancja", gt=0, le=0.5)

    def shares(self, ingredients: list[DishIngredient], skip: set[str] | frozenset[str] = frozenset()) -> dict[str, float]:
        """Udział składnika w masie grupy według przepisu (typowe gramatury). `skip` — składniki o stałej ilości
        (np. woda, produkty pomijalne), które nie wchodzą do bilansu i nie są skalowane."""
        typical = {i.product_id: i.typical_g for i in ingredients if i.product_id in self.products
                   and i.product_id not in skip}
        total = sum(typical.values())
        return {pid: g / total for pid, g in typical.items()} if total > 0 else {}


class DishStatus(StrEnum):
    DRAFT = "szkic"
    APPROVED = "zatwierdzone"
    REJECTED = "odrzucone"


class DishTemplate(DomainModel):
    id: str
    name: str = Field(alias="nazwa", min_length=3)
    description: str | None = Field(default=None, alias="opis")
    meal_types: list[MealType] = Field(alias="posilki", min_length=1)
    cuisine: str = Field(default="polska", alias="kuchnia")
    tags: list[str] = Field(default_factory=list, alias="tagi")
    ingredients: list[DishIngredient] = Field(alias="skladniki", min_length=1)
    ratios: list[Ratio] = Field(default_factory=list, alias="proporcje")
    groups: list[IngredientGroup] = Field(default_factory=list, alias="grupy")
    steps: list[str] = Field(default_factory=list, alias="przygotowanie")
    prep_minutes: int | None = Field(default=None, alias="czas_min", ge=1, le=600)
    batch_days: int | None = Field(default=None, alias="na_ile_dni", ge=2, le=4)
    source: str | None = Field(default=None, alias="zrodlo")
    status: DishStatus = Field(default=DishStatus.DRAFT)
    culinary_version: int = 0  # zero in historical snapshots; never retroactively rewrite them
    culinary_notes: list[str] = Field(default_factory=list)
    culinary_errors: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        return check_slug(v, "id dania")

    @model_validator(mode="after")
    def _structure(self) -> DishTemplate:
        ids = [i.product_id for i in self.ingredients]
        if len(ids) != len(set(ids)):
            raise ValueError(f"danie {self.id}: składnik powtórzony na liście")
        for r in self.ratios:
            for pid in r.numerator + r.denominator:
                if pid not in ids:
                    raise ValueError(f"danie {self.id}: proporcja odwołuje się do nieobecnego składnika {pid}")
        by_id = {i.product_id: i for i in self.ingredients}
        grouped: set[str] = set()
        for g in self.groups:
            if len(set(g.products)) != len(g.products):
                raise ValueError(f"danie {self.id}: grupa „{g.name}” wymienia składnik dwa razy")
            for pid in g.products:
                ing = by_id.get(pid)
                if ing is None:
                    raise ValueError(f"danie {self.id}: grupa „{g.name}” odwołuje się do nieobecnego składnika {pid}")
                if pid in grouped:
                    raise ValueError(f"danie {self.id}: składnik {pid} jest w dwóch grupach")
                if ing.role is Role.SPICE or ing.optional or not ing.scales or ing.typical_g <= 0:
                    raise ValueError(f"danie {self.id}: {pid} nie może być w grupie „{g.name}” (przyprawa, składnik "
                                     "opcjonalny, nieskalowany albo bez typowej ilości)")
                grouped.add(pid)
            portioning = {by_id[pid].portioning for pid in g.products}
            if len(portioning) > 1:
                raise ValueError(f"danie {self.id}: składniki grupy „{g.name}” muszą mieć to samo porcjowanie")
        if DishTag.BATCH.value in self.tags and self.batch_days is None:
            raise ValueError(f"danie {self.id}: tag na_zapas wymaga pola na_ile_dni")
        if self.batch_days is not None and DishTag.BATCH.value not in self.tags:
            raise ValueError(f"danie {self.id}: na_ile_dni wymaga tagu na_zapas")
        return self

    def grouped_products(self) -> set[str]:
        return {p for g in self.groups for p in g.products}

    def group_of(self, product_id: str) -> IngredientGroup | None:
        return next((g for g in self.groups if product_id in g.products), None)

    def has_tag(self, tag: DishTag | str) -> bool:
        return str(tag) in self.tags

    @property
    def is_treat(self) -> bool:
        return self.has_tag(DishTag.TREAT)

    @property
    def is_sweet(self) -> bool:
        return self.has_tag(DishTag.SWEET)

    @property
    def is_batch(self) -> bool:
        return self.has_tag(DishTag.BATCH)
