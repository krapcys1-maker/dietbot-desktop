"""Parametry silnika gramatur. Użytkownik zwykle ich nie zmienia (plik konfiguracja/silnik.yaml)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..domain.dish import Role

# Jednostki całkowite w modelu CP-SAT: 0,01 kcal i 0,001 g. Przy wartościach na 100 g z 2 miejscami po przecinku
# i krokach w pełnych gramach współczynniki są praktycznie dokładne (brak kumulacji błędu zaokrągleń).
SCALE: dict[str, int] = {"kcal": 100, "protein": 1000, "fat": 1000, "carbs": 1000}

DEFAULT_ROLE_BOUNDS: dict[Role, tuple[float, float]] = {
    Role.PROTEIN: (0.5, 1.8),
    Role.CARBS: (0.3, 1.8),
    Role.FAT: (0.3, 1.6),
    Role.VEGETABLES: (0.5, 2.0),
    Role.FRUIT: (0.5, 2.0),
    Role.DAIRY: (0.5, 1.8),
    Role.SAUCE: (0.5, 1.6),
    Role.ADDON: (0.5, 1.5),
    Role.SPICE: (1.0, 1.0),
}


class EngineSettings(BaseModel):
    reference_kcal: float = Field(default=2000.0, gt=0)  # typowe gramatury w daniach są dla 2000 kcal
    role_bounds: dict[Role, tuple[float, float]] = Field(default_factory=lambda: dict(DEFAULT_ROLE_BOUNDS))

    # Piętra celu (im wyżej, tym ważniejsze)
    band_cost_per_g: int = 1_000_000  # 1 g poza widełkami dziennymi
    band_cost_per_kcal: int = 250_000  # 1 kcal poza widełkami dziennymi
    anchor_cost_per_g: int = 100_000  # 1 g białka poza kotwicą posiłku
    goal_cost_per_percent: int = 10_000  # 1% odchylenia od środka celu (poza strefą martwą)
    goal_dead_zone: float = Field(default=0.5, ge=0, le=1)  # część połowy widełek bez kary wokół celu
    # Realizm porcji — kara wypukła: do ±20% za darmo, 20–50% umiarkowanie, powyżej 50% drogo
    realism_dead_zone: float = Field(default=0.2, ge=0, le=1)
    realism_mid_zone: float = Field(default=0.5, ge=0, le=3)
    realism_cost_mid: int = 30_000  # za odchylenie równe 100% typowej porcji w strefie 20–50%
    realism_cost_high: int = 150_000  # j.w. powyżej 50%
    snack_cost: int = 30_000  # użycie przekąski bilansującej
    liked_dishes: set[str] = Field(default_factory=set)  # miękka preferencja; nie zmienia celów ani ograniczeń
    meal_mass_cost_per_g: int = 2_500  # 1 g ponad limit masy posiłku (100 g ≈ 2,5 g poza kotwicą białka)
    meal_mass_tolerance: float = 0.02  # ile ponad limit nie zgłaszamy jako odchylenie (zaokrąglenia kroków)
    alternate_rank_cost: int = 50_000  # wybór dania zapasowego zamiast pierwszego
    lighter_supper_cost_per_kcal: int = 200  # kolacja cięższa niż obiad

    # Margines bezpieczeństwa na resztkowe zaokrąglenia (weryfikator liczy na liczbach rzeczywistych, a gramatury
    # w planie są zaokrąglane do 0,1 g na składnik — w sumie dnia potrafi to dać ok. 0,15 g białka)
    band_safety_kcal: float = 1.0
    band_safety_g: float = 0.2
    # z_garnka: porcje osób mogą odbiegać od idealnej proporcji o tyle kroków
    shared_ratio_tolerance_steps: int = 1
    # grupy składników (ciasto, panierka, sos): dopuszczalne względne odchylenie udziału składnika od przepisu
    group_ratio_tolerance: float = Field(default=0.05, gt=0, le=0.5)

    random_seed: int = 7
    relative_gap_limit: float = 0.002  # 0,2% od optimum — deterministyczne przy 1 wątku
    use_hints: bool = True  # podpowiedź startowa: typowe porcje pierwszego kandydata
    linearization_level: int = 2  # najszybsze w benchmarku (13.09.2026)
    parallel_blocks: bool = True  # bloki są niezależne; każdy liczy się na 1 wątku, więc wynik jest powtarzalny
    # limit na dzień w bloku (jednostki deterministyczne CP-SAT, wynik powtarzalny). Benchmark 72 bloków (13.09.2026):
    # przy 5,0 / 2,0 / 1,0 / 0,5 statusy identyczne, a funkcja celu gorsza o 5–10% tylko w 1–2 blokach przy 1,0;
    # czas ściany 31 s → 17 s. Dłużej trwa głównie dowodzenie optimum, nie znalezienie poprawnych gramatur.
    max_deterministic_time: float = 1.0
    # najpierw liczymy same pierwsze wybory (szybko); zapasowe dopiero, gdy pierwsze nie mieszczą się w celach
    main_first: bool = True
    # False: silnik liczy tylko pierwsze wybory, a zapasowe sprawdza planer (równolegle, w naprawie planu)
    use_alternates: bool = True
    alternates_deterministic_time: float = 2.0  # na dzień, gdy do wyboru są dania zapasowe
    swap_deterministic_time: float = 0.4  # na dzień przy sprawdzaniu zamian w naprawie planu (tylko status zamiany)
    max_wall_time_s: float = 120.0  # tylko bezpiecznik; normalnie nieosiągany
    conflict_core: bool = True  # przy naruszeniach szukaj zbioru sprzecznych ograniczeń

    def cache_view(self) -> dict:
        """Ustawienia wpływające na wynik bloku z jednym kandydatem na slot (do klucza pamięci podręcznej)."""
        skip = {"main_first", "use_alternates", "parallel_blocks", "alternates_deterministic_time", "max_wall_time_s",
                "conflict_core", "swap_deterministic_time"}
        return {k: v for k, v in self.model_dump(mode="json").items() if k not in skip}
