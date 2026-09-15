"""Wejście i wyjście silnika (JSON — m.in. raport diagnostyczny dla LLM)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from ..domain.assumptions import Weekday, parse_day
from ..domain.dish import MealType
from ..domain.plan import WeekPlan


class EngineSlot(BaseModel):
    """Posiłek do obsadzenia: dzień (lub kilka dni dla dania na zapas) + kandydaci w kolejności preferencji."""

    days: list[Weekday] = Field(min_length=1)
    meal: MealType
    candidates: list[str] = Field(min_length=1, max_length=6)

    @field_validator("days", mode="before")
    @classmethod
    def _days(cls, v: list[str | int]) -> list[Weekday]:
        return [parse_day(d) for d in v]

    @model_validator(mode="after")
    def _checks(self) -> EngineSlot:
        # przekąski bilansujące dobiera silnik sam; slot „przekąska” to zaplanowana przekąska z puli 20% (jeden dzień)
        if self.meal is MealType.SNACK and len(self.days) != 1:
            raise ValueError("przekąska z puli 20% zajmuje jeden dzień")
        days = sorted(self.days)
        if len(set(days)) != len(days):
            raise ValueError("powtórzony dzień w slocie")
        if any(b - a != 1 for a, b in zip(days, days[1:], strict=False)):
            raise ValueError("dni slotu na zapas muszą następować po sobie")
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("powtórzony kandydat w slocie")
        self.days = days
        return self

    @property
    def key(self) -> str:
        return "-".join(str(int(d)) for d in self.days) + f"_{self.meal.value}"


class EngineRequest(BaseModel):
    slots: list[EngineSlot] = Field(min_length=1)
    allow_snacks: bool = True

    @model_validator(mode="after")
    def _no_overlap(self) -> EngineRequest:
        seen: set[tuple[int, str]] = set()
        for s in self.slots:
            for d in s.days:
                k = (int(d), s.meal.value)
                if k in seen:
                    raise ValueError(f"posiłek {s.meal.value} w dniu {int(d)} obsadzony więcej niż raz")
                seen.add(k)
        return self


class BlockStatus(StrEnum):
    OK = "ok"
    SOFT = "miekkie_odchylenie"  # kotwice białka lub masa porcji poza zakresem, widełki dzienne OK
    HARD = "twarde_odchylenie"  # widełki dzienne przekroczone
    MODEL_ERROR = "blad_modelu"
    NO_SOLUTION = "brak_rozwiazania"


STATUS_ORDER = [BlockStatus.OK, BlockStatus.SOFT, BlockStatus.HARD, BlockStatus.NO_SOLUTION, BlockStatus.MODEL_ERROR]


def worst(statuses: list[BlockStatus]) -> BlockStatus:
    return max(statuses, key=STATUS_ORDER.index) if statuses else BlockStatus.OK


class Totals(BaseModel):
    day: Weekday
    person_id: str
    kcal: float
    protein: float
    fat: float
    carbs: float
    fiber: float
    in_bands: bool


class BandViolation(BaseModel):
    day: Weekday
    person_id: str
    metric: str
    actual: float
    min: float
    max: float
    gap: float  # ujemne = brakuje, dodatnie = za dużo


class AnchorViolation(BaseModel):
    days: list[Weekday]
    meal: MealType
    dish_id: str
    person_id: str
    protein_g: float
    min: float
    max: float
    gap_g: float
    dish_protein_range_g: tuple[float, float]
    structural: bool  # danie fizycznie nie da białka w zakresie kotwicy


class MassViolation(BaseModel):
    """Posiłek cięższy niż limit masy talerza z założeń (np. 2 kg zupy)."""

    days: list[Weekday]
    meal: MealType
    dish_id: str
    person_id: str
    grams: float
    cap: float

    @property
    def excess_g(self) -> float:
        return self.grams - self.cap


class BindingBound(BaseModel):
    violation: str  # np. "protein:mezczyzna:1"
    days: list[Weekday]
    meal: MealType
    dish_id: str
    product_id: str
    person_id: str
    grams: float
    bound: str  # "max" | "min"
    per_step: dict[str, float]


class SnackUse(BaseModel):
    day: Weekday
    person_id: str
    dish_id: str
    grams: dict[str, float]
    reason: str


class BlockReport(BaseModel):
    days: list[Weekday]
    status: BlockStatus
    solver_status: str
    objective: float | None = None
    wall_ms: float = 0.0
    deterministic_time: float = 0.0
    choices: dict[str, str] = Field(default_factory=dict)  # klucz slotu -> id dania
    totals: list[Totals] = Field(default_factory=list)
    violations: list[BandViolation] = Field(default_factory=list)
    anchor_violations: list[AnchorViolation] = Field(default_factory=list)
    mass_violations: list[MassViolation] = Field(default_factory=list)
    binding_bounds: list[BindingBound] = Field(default_factory=list)
    conflict_core: list[str] = Field(default_factory=list)
    snacks: list[SnackUse] = Field(default_factory=list)
    message: str | None = None


class EngineResult(BaseModel):
    status: BlockStatus
    plan: WeekPlan | None
    blocks: list[BlockReport]
    engine: str
    input_hash: str
