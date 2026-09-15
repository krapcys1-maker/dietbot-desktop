"""Wartości odżywcze i definicja energii."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .common import DomainModel

# Tolerancja na sumę składników > 100 g (zaokrąglenia w tabelach)
_MASS_TOLERANCE_G = 3.0
# Tolerancja porównań sum dnia z widełkami na liczbach zmiennoprzecinkowych — jedna dla silnika i weryfikatora,
# żeby silnik nigdy nie uznał za poprawny dnia, który weryfikator odrzuci (nie jest to „luz” w zasadach)
BAND_TOLERANCE: dict[str, float] = {"kcal": 0.01, "protein": 0.001, "fat": 0.001, "carbs": 0.001}


class EnergyMethod(StrEnum):
    """Jak liczymy kcal w całym systemie (jedna, spójna metoda)."""

    MACROS_4_9_4 = "makro_4_9_4"  # 4·B + 9·T + 4·W (+7·alkohol) — tak liczone są cele użytkownika
    LABEL = "etykieta"  # wartość energetyczna z tabeli/etykiety (UE 1169/2011, błonnik 2 kcal/g)


class Nutrients(DomainModel):
    """Wartości na 100 g części jadalnej. Węglowodany = przyswajalne (definicja UE, bez błonnika)."""

    kcal: float = Field(ge=0, le=950)
    protein: float = Field(alias="bialko", ge=0, le=100)
    fat: float = Field(alias="tluszcz", ge=0, le=100)
    carbs: float = Field(alias="weglowodany", ge=0, le=100)
    fiber: float | None = Field(default=None, alias="blonnik", ge=0, le=100)
    sugars: float | None = Field(default=None, alias="cukry", ge=0, le=100)
    saturated_fat: float | None = Field(default=None, alias="kwasy_nasycone", ge=0, le=100)
    salt: float | None = Field(default=None, alias="sol", ge=0, le=100)
    alcohol: float | None = Field(default=None, alias="alkohol", ge=0, le=100)
    organic_acids: float | None = Field(default=None, alias="kwasy_organiczne", ge=0, le=100)
    polyols: float | None = Field(default=None, alias="poliole", ge=0, le=100)

    @model_validator(mode="after")
    def _consistency(self) -> Nutrients:
        mass = self.protein + self.fat + self.carbs + (self.fiber or 0) + (self.alcohol or 0)
        if mass > 100 + _MASS_TOLERANCE_G:
            raise ValueError(f"suma białka, tłuszczu, węglowodanów i błonnika = {mass:.1f} g > 100 g")
        if self.saturated_fat is not None and self.saturated_fat > self.fat + 0.5:
            raise ValueError("kwasy nasycone nie mogą przekraczać tłuszczu")
        if self.sugars is not None and self.sugars > self.carbs + 0.5:
            raise ValueError("cukry nie mogą przekraczać węglowodanów")
        return self

    def energy(self, method: EnergyMethod) -> float:
        """kcal na 100 g według wybranej metody."""
        if method is EnergyMethod.LABEL:
            return self.kcal
        return 4 * self.protein + 9 * self.fat + 4 * self.carbs + 7 * (self.alcohol or 0)

    def label_energy_from_macros(self) -> float:
        """Energia wg współczynników UE (rozp. 1169/2011, zał. XIV) — do kontroli spójności danych z tabel.

        Węglowodany przyswajalne zawierają poliole (tak jak w BLS), które mają 2,4 kcal/g zamiast 4.
        """
        polyols = self.polyols or 0
        return (
            4 * self.protein
            + 9 * self.fat
            + 4 * max(self.carbs - polyols, 0)
            + 2.4 * polyols
            + 2 * (self.fiber or 0)
            + 7 * (self.alcohol or 0)
            + 3 * (self.organic_acids or 0)
        )

    def kcal_mismatch(self) -> float:
        """Względna różnica kcal z tabeli vs wyliczonych z makro (UE). 0.05 = 5%."""
        computed = self.label_energy_from_macros()
        if max(self.kcal, computed) < 20:  # produkty prawie bez energii: porównuj bezwzględnie
            return abs(self.kcal - computed) / 20
        return abs(self.kcal - computed) / max(self.kcal, computed)


class MacroTotals(DomainModel):
    """Suma dla posiłku/dnia. Energia wg metody wybranej w założeniach."""

    kcal: float = 0.0
    protein: float = Field(default=0.0, alias="bialko")
    fat: float = Field(default=0.0, alias="tluszcz")
    carbs: float = Field(default=0.0, alias="weglowodany")
    fiber: float = Field(default=0.0, alias="blonnik")

    def add(self, other: MacroTotals) -> MacroTotals:
        return MacroTotals(
            kcal=self.kcal + other.kcal,
            protein=self.protein + other.protein,
            fat=self.fat + other.fat,
            carbs=self.carbs + other.carbs,
            fiber=self.fiber + other.fiber,
        )

    def get(self, metric: str) -> float:
        return float(getattr(self, metric))


METRICS: tuple[str, ...] = ("kcal", "protein", "fat", "carbs")
METRIC_LABELS_PL: dict[str, str] = {
    "kcal": "kcal",
    "protein": "białko",
    "fat": "tłuszcz",
    "carbs": "węglowodany",
    "fiber": "błonnik",
}
METRIC_UNITS: dict[str, str] = {"kcal": "kcal", "protein": "g", "fat": "g", "carbs": "g", "fiber": "g"}
