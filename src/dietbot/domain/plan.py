"""Plan tygodnia — wynik silnika (gramatury) sprawdzany przez niezależny weryfikator."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .assumptions import Weekday
from .common import DomainModel
from .dish import MealType


class PlanItem(DomainModel):
    product_id: str = Field(alias="produkt")
    grams: float = Field(alias="gramy", ge=0)


class PersonPortion(DomainModel):
    person_id: str = Field(alias="osoba")
    items: list[PlanItem] = Field(alias="skladniki")


class PlannedMeal(DomainModel):
    day: Weekday = Field(alias="dzien")
    meal: MealType = Field(alias="posilek")
    dish_id: str = Field(alias="danie")
    portions: list[PersonPortion] = Field(alias="porcje")
    batch_group: str | None = Field(default=None, alias="partia")  # to samo danie gotowane raz na kilka dni
    batch_pot_g: dict[str, float] | None = Field(default=None, alias="garnek_g")  # ilości do ugotowania

    def portion(self, person_id: str) -> PersonPortion:
        for p in self.portions:
            if p.person_id == person_id:
                return p
        raise KeyError(person_id)


class PlannedSnack(DomainModel):
    day: Weekday = Field(alias="dzien")
    person_id: str = Field(alias="osoba")
    dish_id: str = Field(alias="danie")
    items: list[PlanItem] = Field(alias="skladniki")
    reason: str | None = Field(default=None, alias="powod")


class WeekPlan(DomainModel):
    days: list[Weekday] = Field(alias="dni")
    meals: list[PlannedMeal] = Field(alias="posilki")
    snacks: list[PlannedSnack] = Field(default_factory=list, alias="przekaski")
    created_at: datetime | None = Field(default=None, alias="utworzono")
    engine: str | None = Field(default=None, alias="silnik")
    input_hash: str | None = Field(default=None, alias="hash_wejscia")
    warnings: list[str] = Field(default_factory=list, alias="ostrzezenia")
    comment: str | None = Field(default=None, alias="komentarz")  # opis tygodnia od LLM (bez liczb)

    def meal(self, day: Weekday, meal: MealType) -> PlannedMeal | None:
        for m in self.meals:
            if m.day == day and m.meal == meal:
                return m
        return None
