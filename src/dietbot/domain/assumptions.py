"""Założenia diety: osoby, cele z widełkami, struktura dnia i tygodnia, zakazy."""

from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import DomainModel, check_slug
from .dish import MealType
from .nutrition import EnergyMethod
from .product import Category, Product

# średnia gęstość energii domowych dań z biblioteki to ok. 1,1–1,4 kcal/g (obiady i kolacje ok. 1,1) — powyżej tej wartości
# dzień nie zmieści się w limitach masy talerza bez dużych odchyleń (testy wariantów celów, 13.09.2026)
MAX_PLATE_ENERGY_DENSITY = 1.17


class Weekday(IntEnum):
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


DAY_KEYS_PL: dict[str, Weekday] = {
    "poniedzialek": Weekday.MONDAY,
    "wtorek": Weekday.TUESDAY,
    "sroda": Weekday.WEDNESDAY,
    "czwartek": Weekday.THURSDAY,
    "piatek": Weekday.FRIDAY,
    "sobota": Weekday.SATURDAY,
    "niedziela": Weekday.SUNDAY,
}
DAY_LABELS_PL: dict[Weekday, str] = {
    Weekday.MONDAY: "Poniedziałek",
    Weekday.TUESDAY: "Wtorek",
    Weekday.WEDNESDAY: "Środa",
    Weekday.THURSDAY: "Czwartek",
    Weekday.FRIDAY: "Piątek",
    Weekday.SATURDAY: "Sobota",
    Weekday.SUNDAY: "Niedziela",
}


def parse_day(value: str | int) -> Weekday:
    if isinstance(value, int):
        return Weekday(value)
    key = value.strip().lower().replace("ś", "s").replace("ą", "a").replace("ł", "l")
    if key not in DAY_KEYS_PL:
        raise ValueError(f"nieznany dzień tygodnia: {value}")
    return DAY_KEYS_PL[key]


def _n(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}".replace(".", ",")


# Ostrożne minimum energii dla dorosłych bez nadzoru specjalisty (typowe progi diet niskokalorycznych: ok. 1200 kcal
# u kobiet i 1500 kcal u mężczyzn). To granica ostrzeżenia w aplikacji, nie norma kliniczna.
SAFE_MIN_KCAL: dict[str, float] = {"kobieta": 1200.0, "mezczyzna": 1500.0}
# kcal z makro (4/9/4) a cel kcal — większa rozbieżność oznacza sprzeczne cele
TARGETS_MACRO_MISMATCH = 0.15

# domyślne limity masy posiłku dla 2000 kcal (osoby z większym zapotrzebowaniem: × pierwiastek ze stosunku energii)
DEFAULT_MEAL_MASS_G: dict[MealType, float] = {MealType.BREAKFAST: 550.0, MealType.LUNCH: 750.0, MealType.SUPPER: 650.0}


class Band(DomainModel):
    """Widełki: twarde min–max i opcjonalny środek celu (domyślnie środek widełek)."""

    min: float = Field(ge=0)
    max: float = Field(gt=0)
    target: float | None = Field(default=None, alias="cel", ge=0)

    @model_validator(mode="after")
    def _order(self) -> Band:
        if self.min > self.max:
            raise ValueError(f"minimum {_n(self.min)} jest większe niż maksimum {_n(self.max)}")
        if self.target is not None and not (self.min <= self.target <= self.max):
            raise ValueError(f"cel {_n(self.target)} jest poza widełkami {_n(self.min)}–{_n(self.max)}")
        return self

    @property
    def center(self) -> float:
        return self.target if self.target is not None else (self.min + self.max) / 2


class DailyTargets(DomainModel):
    kcal: Band
    protein: Band = Field(alias="bialko")
    fat: Band = Field(alias="tluszcz")
    carbs: Band = Field(alias="weglowodany")

    def band(self, metric: str) -> Band:
        return getattr(self, metric)


class PortionCap(DomainModel):
    """Największa porcja produktu (albo całej kategorii, np. ryżu) w jednym posiłku osoby — z preferencji domowników."""

    product_id: str | None = Field(default=None, alias="produkt")
    category: Category | None = Field(default=None, alias="kategoria")
    meals: list[MealType] = Field(default_factory=list, alias="posilki")  # puste = każdy posiłek
    max_g: float = Field(alias="maks_g", gt=0, le=2000)

    @model_validator(mode="after")
    def _target(self) -> PortionCap:
        if (self.product_id is None) == (self.category is None):
            raise ValueError("limit porcji dotyczy produktu albo kategorii (dokładnie jednego z nich)")
        return self

    def applies(self, product: Product, meal: MealType | None) -> bool:
        if meal is not None and self.meals and meal not in self.meals:
            return False
        return product.id == self.product_id if self.product_id else product.category is self.category


class Person(DomainModel):
    id: str
    name: str = Field(alias="nazwa")
    targets: DailyTargets = Field(alias="cele_dzienne")
    protein_anchor: Band = Field(alias="kotwica_bialka_na_posilek")
    # "celuj w około": odchylenie w tej granicy nie jest błędem (silnik i tak dąży do zakresu)
    protein_anchor_tolerance: float = Field(default=5.0, alias="tolerancja_kotwicy_g", ge=0, le=30)
    excluded_products: list[str] = Field(default_factory=list, alias="wyklucza_produkty")
    # największa rozsądna masa talerza (wszystkie składniki razem) — chroni przed np. 2 kg zupy na obiad
    max_meal_mass_g: dict[MealType, float] = Field(default_factory=dict, alias="maks_masa_posilku_g")
    # np. „ryż suchy najwyżej 65 g na posiłek” — twardy limit silnika, ważniejszy niż typowa porcja z przepisu
    portion_caps: list[PortionCap] = Field(default_factory=list, alias="limity_porcji")
    # tylko do progów bezpieczeństwa (np. minimum kcal); brak = jak w id osoby albo ostrożny próg dla kobiet
    sex: Literal["kobieta", "mezczyzna"] | None = Field(default=None, alias="plec")

    @property
    def safe_min_kcal(self) -> float:
        sex = self.sex or (self.id if self.id in SAFE_MIN_KCAL else None)
        return SAFE_MIN_KCAL.get(sex or "kobieta", SAFE_MIN_KCAL["kobieta"])

    @property
    def below_safe_kcal(self) -> bool:
        return self.targets.kcal.min < self.safe_min_kcal

    def portion_cap(self, product: Product, meal: MealType | None = None) -> float | None:
        """Najostrzejszy limit porcji produktu w posiłku (None = brak limitu). `meal` None = limity każdego posiłku."""
        caps = [c.max_g for c in self.portion_caps if c.applies(product, meal)]
        return min(caps) if caps else None

    @field_validator("max_meal_mass_g")
    @classmethod
    def _masses(cls, v: dict[MealType, float]) -> dict[MealType, float]:
        for meal, grams in v.items():
            if not 150 <= grams <= 3000:
                raise ValueError(f"maksymalna masa posiłku {meal.value}: {grams} g poza zakresem 150–3000 g")
        return v

    def meal_mass_cap(self, meal: MealType) -> float:
        """Limit masy posiłku; bez wpisu w założeniach — domyślny, zależny od zapotrzebowania energii."""
        if meal in self.max_meal_mass_g:
            return self.max_meal_mass_g[meal]
        base = DEFAULT_MEAL_MASS_G.get(meal, 700.0)
        return round(base * (self.energy_center / 2000.0) ** 0.5 / 10) * 10

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        return check_slug(v, "id osoby")

    @property
    def energy_center(self) -> float:
        return self.targets.kcal.center


class FixedItem(DomainModel):
    product_id: str = Field(alias="produkt")
    grams: float = Field(alias="gramy", gt=0, le=2000)
    description: str | None = Field(default=None, alias="opis")


class SnackRules(DomainModel):
    max_per_person_per_day: int = Field(default=1, alias="max_na_osobe_dziennie", ge=0, le=3)
    allowed: list[str] = Field(default_factory=list, alias="dozwolone")  # id dań typu przekąska


class DayStructure(DomainModel):
    meals: list[MealType] = Field(alias="posilki", min_length=1)
    supper_lighter_than_lunch: bool = Field(default=True, alias="kolacja_lzejsza_niz_obiad")
    fixed_items: list[FixedItem] = Field(default_factory=list, alias="stale_pozycje")
    snacks: SnackRules = Field(default_factory=SnackRules, alias="przekaski")

    @field_validator("meals")
    @classmethod
    def _no_snack_slot(cls, v: list[MealType]) -> list[MealType]:
        if MealType.SNACK in v:
            raise ValueError("przekąski konfiguruje się w sekcji 'przekaski', nie jako posiłek")
        if len(v) != len(set(v)):
            raise ValueError("posiłek powtórzony w strukturze dnia")
        return v


class BatchRule(DomainModel):
    """Danie na zapas z wymaganym tagiem w danym posiłku w podanych dniach (np. zupa wt+śr)."""

    tag: str
    meal: MealType = Field(alias="posilek")
    days: list[Weekday] = Field(alias="dni", min_length=2)

    @field_validator("days", mode="before")
    @classmethod
    def _days(cls, v: list[str | int]) -> list[Weekday]:
        return [parse_day(d) for d in v]

    @model_validator(mode="after")
    def _consecutive(self) -> BatchRule:
        days = sorted(self.days)
        if any(b - a != 1 for a, b in zip(days, days[1:], strict=False)):
            raise ValueError("dni gotowania na zapas muszą następować po sobie")
        if days != self.days:  # kolejność w pliku nie ma znaczenia: klucze slotów liczymy od najwcześniejszego dnia
            self.days = days
        return self


class WeekRules(DomainModel):
    treat_meals: Band = Field(alias="posilki_20_procent")
    sweet_suppers_max: int = Field(default=2, alias="slodkie_kolacje_max", ge=0, le=7)
    batch: list[BatchRule] = Field(default_factory=list, alias="na_zapas")
    no_repeats: bool = Field(default=True, alias="bez_powtorek")


class Prohibitions(DomainModel):
    products: list[str] = Field(default_factory=list, alias="produkty")
    dish_tags_by_meal: dict[MealType, list[str]] = Field(default_factory=dict, alias="tagi_dan_w_posilkach")
    allowed_carb_categories: list[str] = Field(default_factory=list, alias="dozwolone_zrodla_weglowodanow")
    # kolacja nie może być typowym obiadem: bez tych źródeł węglowodanów (poza pulą 20%)
    supper_forbidden_carb_categories: list[str] = Field(default_factory=list, alias="kolacja_bez_weglowodanow_z")


class EnergySettings(DomainModel):
    method: EnergyMethod = Field(default=EnergyMethod.LABEL, alias="metoda")

    @field_validator("method")
    @classmethod
    def _table_calories(cls, value: EnergyMethod) -> EnergyMethod:
        if value is not EnergyMethod.LABEL:
            raise ValueError("Kalorie liczymy z wartości kcal produktów (metoda: etykieta), nie ze skrótu 4/9/4.")
        return value


def fixed_grams(a: Assumptions) -> dict[str, float]:
    """Stałe pozycje dnia jako produkt → gramy; ten sam produkt wpisany dwa razy jest sumowany (jak w silniku)."""
    out: dict[str, float] = {}
    for item in a.day.fixed_items:
        out[item.product_id] = out.get(item.product_id, 0.0) + item.grams
    return out


class Assumptions(DomainModel):
    diet: Literal["tradycyjna", "wege", "wegan"] = Field(default="tradycyjna", alias="rodzaj_diety")
    version: int = Field(default=1, alias="wersja")
    energy: EnergySettings = Field(default_factory=EnergySettings, alias="energia")
    persons: list[Person] = Field(alias="osoby", min_length=1)
    day: DayStructure = Field(alias="dzien")
    week: WeekRules = Field(alias="tydzien")
    prohibitions: Prohibitions = Field(default_factory=Prohibitions, alias="zakazy")
    cuisines: list[str] = Field(default_factory=list, alias="kuchnie")

    @model_validator(mode="after")
    def _checks(self) -> Assumptions:
        ids = [p.id for p in self.persons]
        if len(ids) != len(set(ids)):
            raise ValueError("id osób muszą być unikalne")
        taken: set[tuple[MealType, int]] = set()
        for rule in self.week.batch:
            if rule.meal not in self.day.meals:
                raise ValueError(f"zasada na zapas dotyczy posiłku {rule.meal}, którego nie ma w strukturze dnia")
            for d in rule.days:
                if (rule.meal, int(d)) in taken:
                    raise ValueError(f"dwie zasady na zapas dotyczą tego samego posiłku ({rule.meal.value}) "
                                     f"w dniu {DAY_LABELS_PL[d].lower()}")
                taken.add((rule.meal, int(d)))
        return self

    def person(self, person_id: str) -> Person:
        for p in self.persons:
            if p.id == person_id:
                return p
        raise KeyError(person_id)

    def consistency_warnings(self) -> list[str]:
        """Sprawdza ograniczenia porcji i kotwic białka; kcal z tabel nie są tożsame z 4/9/4."""
        warnings: list[str] = []
        for p in self.persons:
            t = p.targets
            meals = len(self.day.meals)
            caps = sum(p.meal_mass_cap(m) for m in self.day.meals)
            if caps > 0 and t.kcal.center / caps > MAX_PLATE_ENERGY_DENSITY:
                warnings.append(
                    f"{p.name}: {t.kcal.center:.0f} kcal przy limitach talerza razem {caps:.0f} g to średnio "
                    f"{t.kcal.center / caps:.2f} kcal na gram jedzenia — więcej niż mają zwykłe domowe dania "
                    "(ok. 1,1–1,2); silnik może przekraczać limity masy talerza. Zwiększ limity masy posiłków "
                    "albo liczbę przekąsek."
                )
            if p.below_safe_kcal:
                warnings.append(
                    f"{p.name}: dolna granica energii {t.kcal.min:.0f} kcal jest poniżej ostrożnego minimum "
                    f"ok. {p.safe_min_kcal:.0f} kcal dziennie dla dorosłej osoby. Tak niskie cele ustal ze specjalistą "
                    "(lekarzem lub dietetykiem); plan będzie oznaczony uwagą."
                )
            macro_kcal = 4 * t.protein.center + 9 * t.fat.center + 4 * t.carbs.center
            if t.kcal.center and abs(macro_kcal - t.kcal.center) / t.kcal.center > TARGETS_MACRO_MISMATCH:
                warnings.append(
                    f"{p.name}: makro z celów daje ok. {macro_kcal:.0f} kcal (4/9/4), a cel energii to "
                    f"{t.kcal.center:.0f} kcal — cele są ze sobą sprzeczne, silnik nie spełni wszystkich naraz."
                )
            if (p.protein_anchor.min - p.protein_anchor_tolerance) * meals > t.protein.max:
                warnings.append(
                    f"{p.name}: kotwice białka ({p.protein_anchor.min:.0f} g × {meals} posiłki) "
                    f"przekraczają maksimum białka {t.protein.max:.0f} g"
                )
        return warnings
