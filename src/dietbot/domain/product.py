"""Produkt spożywczy (ogólny) i jego produkty sklepowe."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .common import DomainModel, check_slug
from .nutrition import Nutrients


class Category(StrEnum):
    DAIRY = "nabial"
    CHEESE = "sery"
    EGGS = "jaja"
    MEAT = "mieso"
    POULTRY = "drob"
    COLD_CUTS = "wedliny"
    FISH = "ryby"
    BREAD = "pieczywo"
    RICE = "ryz"
    PASTA = "makaron"
    POTATOES = "ziemniaki"
    GROATS = "kasze"
    FLOUR = "maka"
    CEREALS = "platki"
    LEGUMES = "straczki"
    VEGETABLES = "warzywa"
    FRUIT = "owoce"
    NUTS_SEEDS = "orzechy_i_nasiona"
    FATS = "tluszcze"
    SWEETS = "slodycze"
    SAUCES = "sosy_i_dodatki"
    SPICES = "przyprawy"
    DRINKS = "napoje"
    PROTEIN_PRODUCTS = "produkty_bialkowe"
    READY_MEALS = "dania_gotowe"
    SNACKS = "przekaski"


CATEGORY_LABELS_PL: dict[Category, str] = {
    Category.DAIRY: "Nabiał",
    Category.CHEESE: "Sery",
    Category.EGGS: "Jaja",
    Category.MEAT: "Mięso",
    Category.POULTRY: "Drób",
    Category.COLD_CUTS: "Wędliny",
    Category.FISH: "Ryby i owoce morza",
    Category.BREAD: "Pieczywo",
    Category.RICE: "Ryż",
    Category.PASTA: "Makaron",
    Category.POTATOES: "Ziemniaki",
    Category.GROATS: "Kasze",
    Category.FLOUR: "Mąka i gotowe ciasto",
    Category.CEREALS: "Płatki",
    Category.LEGUMES: "Strączki",
    Category.VEGETABLES: "Warzywa",
    Category.FRUIT: "Owoce",
    Category.NUTS_SEEDS: "Orzechy i nasiona",
    Category.FATS: "Tłuszcze",
    Category.SWEETS: "Słodycze i słodziki",
    Category.SAUCES: "Sosy i dodatki",
    Category.SPICES: "Przyprawy",
    Category.DRINKS: "Napoje",
    Category.PROTEIN_PRODUCTS: "Produkty białkowe",
    Category.READY_MEALS: "Dania gotowe",
    Category.SNACKS: "Przekąski",
}


class State(StrEnum):
    """W jakim stanie waży się produkt w przepisie — musi się zgadzać z wartościami na 100 g."""

    RAW = "surowy"
    DRY = "suchy"  # ryż, makaron, soczewica przed gotowaniem
    COOKED = "gotowany"
    READY = "gotowy_do_jedzenia"  # pieczywo, sery, wędliny, jogurty
    DRAINED = "odsaczony"  # konserwy po odsączeniu


class SourceDatabase(StrEnum):
    BLS = "BLS"  # Bundeslebensmittelschlüssel 4.0, Max Rubner-Institut, CC BY 4.0
    CIQUAL = "CIQUAL"  # ANSES CIQUAL 2025, Etalab-2.0
    OPEN_FOOD_FACTS = "OFF"  # Open Food Facts, ODbL
    LABEL = "etykieta"  # przepisane z opakowania
    USDA = "USDA"  # FoodData Central, CC0 (tylko w ostateczności)
    MANUAL = "recznie"  # wartość wpisana ręcznie z opisem pochodzenia


class Source(DomainModel):
    database: SourceDatabase = Field(alias="baza")
    version: str | None = Field(default=None, alias="wersja")
    code: str | None = Field(default=None, alias="kod")
    original_name: str | None = Field(default=None, alias="nazwa_oryginalna")
    url: str | None = None
    note: str | None = Field(default=None, alias="uwagi")
    retrieved: date | None = Field(default=None, alias="pobrano")

    @model_validator(mode="after")
    def _code_required(self) -> Source:
        if self.database in (SourceDatabase.BLS, SourceDatabase.CIQUAL, SourceDatabase.USDA) and not self.code:
            raise ValueError(f"źródło {self.database} wymaga kodu wpisu")
        if self.database is SourceDatabase.MANUAL and not self.note:
            raise ValueError("źródło 'recznie' wymaga uwagi, skąd pochodzi wartość")
        return self


class Measure(DomainModel):
    """Miara domowa przeliczona na gramy części jadalnej."""

    unit: str = Field(alias="jednostka")  # szt, łyżka, łyżeczka, szklanka, plaster, kromka, opakowanie, garść, ml
    grams: float = Field(alias="gramy", gt=0, le=5000)
    description: str | None = Field(default=None, alias="opis")
    source: str | None = Field(default=None, alias="zrodlo")


class Product(DomainModel):
    id: str
    name: str = Field(alias="nazwa", min_length=2)
    aliases: list[str] = Field(default_factory=list, alias="aliasy")
    category: Category = Field(alias="kategoria")
    state: State = Field(alias="stan")
    per_100g: Nutrients = Field(alias="na_100g")
    source: Source = Field(alias="zrodlo")
    measures: list[Measure] = Field(default_factory=list, alias="miary")
    step_g: float = Field(default=5.0, alias="krok_g", gt=0, le=500)
    # największa rozsądna porcja na osobę w jednym posiłku (np. suchy makaron 140 g) — chroni przed nierealnymi talerzami
    max_portion_g: float | None = Field(default=None, alias="max_porcja_g", gt=0, le=2000)
    density_g_per_ml: float | None = Field(default=None, alias="gestosc_g_ml", gt=0.3, lt=2.0)
    negligible: bool = Field(default=False, alias="pomijalny")  # przyprawy: w przepisie, ale nie w bilansie
    tags: list[str] = Field(default_factory=list, alias="tagi")
    verified: bool = Field(default=False, alias="zweryfikowany")
    notes: str | None = Field(default=None, alias="uwagi")

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        return check_slug(v, "id produktu")

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str]) -> list[str]:
        cleaned = []
        for alias in v:
            alias = str(alias).strip()
            letters = sum(ch.isalpha() for ch in alias)
            # chroni m.in. przed rozbiciem "mleko 1,5%" przez przecinek w liście YAML na "mleko 1" i "5%"
            if letters < 3:
                raise ValueError(f"alias '{alias}' jest za krótki lub bez liter — sprawdź przecinki w liście YAML")
            cleaned.append(alias)
        if len({a.lower() for a in cleaned}) != len(cleaned):
            raise ValueError("powtórzony alias")
        return cleaned

    def measure(self, unit: str) -> Measure | None:
        for m in self.measures:
            if m.unit == unit:
                return m
        return None


class StoreProduct(DomainModel):
    """Konkretny produkt z półki sklepu (np. Lidl Rumunia), powiązany z produktem ogólnym."""

    id: str
    product_id: str = Field(alias="produkt")
    store: str = Field(alias="sklep")  # np. "Lidl RO"
    name_local: str = Field(alias="nazwa_w_sklepie")  # nazwa z etykiety, np. po rumuńsku
    brand: str | None = Field(default=None, alias="marka")
    ean: str | None = None
    package_g: float | None = Field(default=None, alias="opakowanie_g", gt=0)
    per_100g: Nutrients | None = Field(default=None, alias="na_100g")  # z etykiety, jeśli znane
    source: Source | None = Field(default=None, alias="zrodlo")
    verified_on: date | None = Field(default=None, alias="sprawdzono")
    preferred: bool = Field(default=False, alias="preferowany")
    notes: str | None = Field(default=None, alias="uwagi")

    @field_validator("id", "product_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        return check_slug(v, "id")
