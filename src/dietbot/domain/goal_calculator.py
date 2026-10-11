"""Optional local adult estimate, separate from food-table calculations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .assumptions import SAFE_MIN_KCAL, Band, DailyTargets

ACTIVITY = {"low": 1.2, "light": 1.375, "moderate": 1.55, "high": 1.725}
SOURCES = [
    {"label": "Mifflin–St Jeor (1990)", "url": "https://pubmed.ncbi.nlm.nih.gov/2305711/"},
    {"label": "NIDDK — ograniczenia kalkulatorów", "url": "https://www.niddk.nih.gov/health-information/weight-management/body-weight-planner"},
    {"label": "National Academies — makroskładniki", "url": "https://www.nationalacademies.org/publications/10490"},
]


class GoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    age: int = Field(ge=18, le=75)
    height_cm: float = Field(ge=130, le=220)
    weight_kg: float = Field(ge=40, le=200)
    sex: Literal["female", "male"]
    activity: Literal["low", "light", "moderate", "high"]
    goal: Literal["maintain", "lose"]
    target_weight_kg: float | None = Field(default=None, ge=40, le=200)
    pace: Literal["gentle", "moderate"] = "gentle"
    adult_without_special_needs: bool = False
    meals: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def safe_scope(self):
        if not self.adult_without_special_needs:
            raise ValueError("Kalkulator jest dla dorosłych bez szczególnych potrzeb żywieniowych. W ciąży, podczas karmienia, przy zaburzeniach odżywiania lub chorobach wymagających diety ustal cele ze specjalistą.")
        bmi = self.weight_kg / (self.height_cm / 100) ** 2
        if bmi < 18.5 or bmi >= 40:
            raise ValueError("Parametry są poza zakresem uproszczonego kalkulatora. Ustal cele ze specjalistą; możesz wpisać je ręcznie.")
        if self.goal == "lose":
            if self.target_weight_kg is None or self.target_weight_kg >= self.weight_kg:
                raise ValueError("Podaj masę docelową mniejszą od obecnej.")
            if self.target_weight_kg / (self.height_cm / 100) ** 2 < 18.5:
                raise ValueError("Nie proponujemy redukcji do masy poniżej BMI 18,5. Ustal odpowiedni cel ze specjalistą.")
        return self


def calculate_goals(profile: GoalInput) -> dict:
    resting = 10 * profile.weight_kg + 6.25 * profile.height_cm - 5 * profile.age
    resting += 5 if profile.sex == "male" else -161
    maintenance = resting * ACTIVITY[profile.activity]
    deficit = min(500, maintenance * (0.10 if profile.pace == "gentle" else 0.15)) if profile.goal == "lose" else 0
    kcal = round((maintenance - deficit) / 10) * 10
    # Conservative product boundary, not a universal clinical minimum. Never silently clamp. Jeden próg 1500 kcal
    # odrzucał większość typowych kobiet (audyt 09.10.2026, W1) — próg zależy od płci, jak ostrzeżenie przy celach.
    floor = SAFE_MIN_KCAL["mezczyzna" if profile.sex == "male" else "kobieta"]
    if kcal * 0.95 < floor or kcal > 4000:
        raise ValueError(f"Wynik jest poza ostrożnym zakresem aplikacji (dolna granica celu {floor:.0f} kcal, środek do 4000 kcal). Nie zmieniamy go automatycznie. Wpisz cele ustalone indywidualnie ze specjalistą.")
    values = {"kcal": kcal, "protein": round(kcal * 0.20 / 4), "fat": round(kcal * 0.30 / 9), "carbs": round(kcal * 0.50 / 4)}
    bands = {key: Band(min=round(value * (0.95 if key == "kcal" else 0.90)),
                       max=round(value * (1.05 if key == "kcal" else 1.10)), target=value)
             for key, value in values.items()}
    targets = DailyTargets(**bands)
    anchor = values["protein"] / profile.meals
    return {
        "resting_kcal": round(resting), "maintenance_kcal": round(maintenance),
        "deficit_kcal": round(maintenance - kcal), "targets": targets.model_dump(by_alias=True),
        "sex": "mezczyzna" if profile.sex == "male" else "kobieta",
        "protein_anchor": {"min": round(anchor * 0.70), "max": round(anchor * 1.20)},
        "loss_kg": round(profile.weight_kg - profile.target_weight_kg, 1) if profile.goal == "lose" else 0,
        "method": "Mifflin–St Jeor × przybliżony współczynnik aktywności. Propozycja B/T/W: 20% / 30% / 50% energii.",
        "warnings": [
            "To punkt startowy, nie pomiar zapotrzebowania ani porada medyczna. Aktywność i deficyt są uproszczeniem aplikacji.",
            "Masa docelowa nie wyznacza terminu ani większego deficytu. Obserwuj samopoczucie i trend masy przez kilka tygodni; zmiana nie jest liniowa.",
            "Makro jest edytowalną propozycją. Kalorie posiłków nadal pochodzą z tabel produktów, nie ze skrótu 4/9/4.",
        ], "sources": SOURCES,
    }
