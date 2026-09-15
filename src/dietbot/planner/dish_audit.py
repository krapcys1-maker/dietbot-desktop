"""Test silnika dla dania: czy da się ułożyć z nim poprawny dzień.

Każde danie wstawiamy do dnia z neutralnymi, sprawdzonymi towarzyszami (np. kanapki na śniadanie, kurczak curry
na obiad) i liczymy gramatury. Jeśli silnik nie mieści dnia w widełkach i kotwicach w dwóch różnych zestawach,
danie jest strukturalnie słabe w tej diecie (np. śniadanie bez węglowodanów, kolacja z za małą ilością białka
przy limicie masy talerza) — nie trafia do biblioteki, zanim zdąży zepsuć plan.

Drugi etap (gdy podano towarzyszy z biblioteki): dni z losowymi daniami dobranymi tak jak robi planer — z bilansem
makro dnia w normie. Danie, które mieści się tylko w części takich dni (np. sałatka bez pieczywa wymagająca
idealnych towarzyszy albo obiad z dużą ilością sosu przy limicie talerza), też odpada.

Danie na zapas (zupa na wtorek i środę) sprawdzamy jako blok dwóch dni: ten sam garnek w obu dniach, każdy dzień
z własnymi towarzyszami — tak jak liczy je planer.
"""

from __future__ import annotations

import os
import random
import zlib
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..domain.assumptions import Assumptions
from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType
from ..domain.product import Product
from ..engine import BlockReport, BlockStatus, EngineRequest, EngineSettings, solve_week
from .balance import BALANCE_OK_PP, MacroSplit, day_deviation
from .pools import build_pools

METRIC_PL = {"kcal": "kcal", "protein": "białko", "fat": "tłuszcz", "carbs": "węglowodany"}
# dwa zestawy towarzyszy: danie musi zmieścić się w dniu z przynajmniej jednym z nich
COMPANIONS: tuple[dict[MealType, str], ...] = (
    {MealType.BREAKFAST: "kanapki_z_szynka_jajkiem_i_pomidorem", MealType.LUNCH: "kurczak_curry_z_ryzem",
     MealType.SUPPER: "salatka_grecka_z_kurczakiem"},
    {MealType.BREAKFAST: "serek_wiejski_z_rzodkiewka_i_ogorkiem", MealType.LUNCH: "losos_z_ziemniakami_i_brokulem",
     MealType.SUPPER: "wrap_z_kurczakiem_i_warzywami"},
)
MEAL_ORDER = (MealType.BREAKFAST, MealType.LUNCH, MealType.SUPPER)
BATCH_DAYS = 2  # zasada tygodnia: zupa na zapas we wtorek i środę
# drugi etap: dni z losowymi, zbilansowanymi towarzyszami (w bibliotece ok. 90% takich dni wychodzi poprawnie).
# Pomiar 13.09.2026: 6 z 10 przepuściło 60 z 60 losowych dań biblioteki, a odrzuciło sałatki-kolacje bez pieczywa
# i obiady z dużą ilością sosu, które mieszczą się tylko w co drugim takim dniu.
SAMPLE_DAYS = 10
SAMPLE_MIN_OK = 6
PARTNER_TRIES = 40
# w próbach talerz najwyżej 5% ponad limit nie przekreśla dania (w planie to tylko uwaga o dużej porcji)
MASS_SLACK = 0.05

Days = Sequence[Mapping[MealType, str]]  # towarzysze na kolejne dni bloku (jeden słownik na dzień)


@dataclass(frozen=True)
class Partners:
    """Dania biblioteki do losowania towarzyszy: bez puli 20% i dań na zapas, z typowymi makro."""

    by_meal: dict[MealType, list[str]]
    typical: dict[str, dict[str, float]]
    target: MacroSplit


def partner_pool(dishes: Mapping[str, DishTemplate], products: Mapping[str, Product], a: Assumptions) -> Partners:
    pools = build_pools(dishes, products, a)
    by_meal = {m: [d for d in pools.by_meal.get(m, []) if d in pools.profiles and not dishes[d].is_treat
                   and not dishes[d].is_batch] for m in MEAL_ORDER}
    return Partners(by_meal, {d: p.typical for d, p in pools.profiles.items()}, pools.target)


def _describe(block: BlockReport, dish: DishTemplate, meal: MealType, a: Assumptions) -> str:
    def who(pid: str) -> str:
        return next((p.name for p in a.persons if p.id == pid), pid)

    def num(x: float) -> str:
        return (f"{x:+.2f}" if abs(x) < 1 else f"{x:+.0f}").replace(".", ",")

    problems = [f"{who(v.person_id)}: {METRIC_PL.get(v.metric, v.metric)} {num(v.gap)}" for v in block.violations]
    problems += [f"{who(x.person_id)}: białko w posiłku {num(x.gap_g)} g" if x.dish_id == dish.id else
                 f"{who(x.person_id)}: białko ({MEAL_LABELS_PL[x.meal].lower()}) {num(x.gap_g)} g"
                 for x in block.anchor_violations]
    problems += [f"{who(x.person_id)}: talerz +{x.excess_g:.0f} g" if x.meal is meal else
                 f"{who(x.person_id)}: talerz ({MEAL_LABELS_PL[x.meal].lower()}) +{x.excess_g:.0f} g"
                 for x in block.mass_violations]
    return "; ".join(problems) or (block.message or block.status.value)


def _acceptable(block: BlockReport) -> bool:
    if block.status is BlockStatus.OK:
        return True
    return (block.status is BlockStatus.SOFT and not block.violations and not block.anchor_violations
            and all(x.grams <= x.cap * (1 + MASS_SLACK) for x in block.mass_violations))


def _span(dish: DishTemplate) -> int:
    return BATCH_DAYS if dish.is_batch else 1


def _solve_days(dish: DishTemplate, meal: MealType, days: Days, library: Mapping[str, DishTemplate],
                products: Mapping[str, Product], a: Assumptions, settings: EngineSettings) -> BlockReport:
    """Danie w posiłku przez wszystkie dni bloku (jeden slot — przy daniu na zapas wspólny garnek), pozostałe
    posiłki każdego dnia z jego towarzyszy."""
    slots = [{"days": list(range(len(days))), "meal": meal.value, "candidates": [dish.id]}]
    slots += [{"days": [k], "meal": m.value, "candidates": [others[m]]}
              for k, others in enumerate(days) for m in MEAL_ORDER if m is not meal]
    return solve_week(EngineRequest.model_validate({"slots": slots}), a, products, library, settings).blocks[0]


def _balanced_day(dish_id: str, typical: dict[str, float], meal: MealType, partners: Partners,
                  rnd: random.Random) -> dict[MealType, str] | None:
    """Towarzysze na pozostałe posiłki z bilansem makro dnia w normie (albo najlepszy z prób)."""
    options = {m: [d for d in partners.by_meal.get(m, []) if d != dish_id] for m in MEAL_ORDER if m is not meal}
    if any(not ids for ids in options.values()):
        return None
    best: tuple[float, dict[MealType, str]] | None = None
    for _ in range(PARTNER_TRIES):
        pick = {m: rnd.choice(ids) for m, ids in options.items()}
        dev = day_deviation([typical, *(partners.typical[d] for d in pick.values())], partners.target)
        if best is None or dev < best[0]:
            best = (dev, pick)
        if dev <= BALANCE_OK_PP:
            break
    return best[1] if best else None


def _audited_meal(dish: DishTemplate) -> MealType | None:
    """Przekąski pomijamy (inne zasady układania)."""
    if MealType.SNACK in dish.meal_types:
        return None
    return next((m for m in MEAL_ORDER if m in dish.meal_types), None)


def _fixed_sets(dish: DishTemplate, meal: MealType, library: Mapping[str, DishTemplate], products: Mapping[str, Product],
                a: Assumptions, settings: EngineSettings) -> list[str]:
    last = None
    for companions in COMPANIONS:
        if any(companions[m] not in library for m in MEAL_ORDER if m is not meal):
            continue
        block = _solve_days(dish, meal, [companions] * _span(dish), library, products, a, settings)
        if block.status is BlockStatus.OK:
            return []
        last = block
    if last is None:
        return []
    what = "poprawnych dni z tym daniem na zapas" if dish.is_batch else "poprawnego dnia z tym daniem"
    return [f"silnik nie układa {what} ({MEAL_LABELS_PL[meal].lower()}): {_describe(last, dish, meal, a)}"]


def _sample_days(dish: DishTemplate, meal: MealType, partners: Partners, products: Mapping[str, Product],
                 a: Assumptions) -> list[list[dict[MealType, str]]]:
    """Powtarzalne losowanie (ziarno z id dania): SAMPLE_DAYS bloków, każdy z towarzyszami na każdy dzień bloku.
    Pusta lista = nie da się dobrać towarzyszy."""
    profile = build_pools({dish.id: dish}, products, a).profiles.get(dish.id)
    if profile is None:
        return []
    rnd = random.Random(zlib.crc32(dish.id.encode("utf-8")))
    samples = []
    for _ in range(SAMPLE_DAYS):
        block = [_balanced_day(dish.id, profile.typical, meal, partners, rnd) for _ in range(_span(dish))]
        if any(day is None for day in block):
            return []
        samples.append(block)
    return samples


def _verdict(dish: DishTemplate, meal: MealType, blocks: list[BlockReport], a: Assumptions) -> list[str] | None:
    """[] — przechodzi, [powód] — odpada, None — za mało policzonych dni, żeby rozstrzygnąć."""
    ok = sum(1 for b in blocks if _acceptable(b))
    failed = len(blocks) - ok
    if ok >= SAMPLE_MIN_OK:
        return []
    if failed <= SAMPLE_DAYS - SAMPLE_MIN_OK and len(blocks) < SAMPLE_DAYS:
        return None
    worst = next(b for b in blocks if not _acceptable(b))
    what = "dwudniowych bloków" if dish.is_batch else "dni"
    return [f"danie mieści się tylko w {ok} z {len(blocks)} {what} z losowymi, dobrze dobranymi posiłkami "
            f"({MEAL_LABELS_PL[meal].lower()}): {_describe(worst, dish, meal, a)}"]


def _settings(settings: EngineSettings | None) -> EngineSettings:
    # pełny limit czasu także w próbach: krótszy (0,4 jak przy sprawdzaniu zamian) zmieniał status co dziesiątego
    # granicznego dnia (pomiar 13.09.2026 na 180 dniach)
    return (settings or EngineSettings()).model_copy(update={"conflict_core": False, "parallel_blocks": False})


def audit_dish(dish: DishTemplate, dishes: Mapping[str, DishTemplate], products: Mapping[str, Product],
               a: Assumptions, settings: EngineSettings | None = None, partners: Partners | None = None) -> list[str]:
    """Pusta lista = danie mieści się w dniu. `partners` włącza drugi etap (losowe, zbilansowane dni);
    bez nich tylko dwa stałe zestawy."""
    meal = _audited_meal(dish)
    if meal is None:
        return []
    settings = _settings(settings)
    library = {**dishes, dish.id: dish}
    problems = _fixed_sets(dish, meal, library, products, a, settings)
    if problems or partners is None:
        return problems
    blocks: list[BlockReport] = []
    for days in _sample_days(dish, meal, partners, products, a):
        blocks.append(_solve_days(dish, meal, days, library, products, a, settings))
        verdict = _verdict(dish, meal, blocks, a)
        if verdict is not None:
            return verdict
    return []


def audit_dishes(candidates: list[DishTemplate], dishes: Mapping[str, DishTemplate], products: Mapping[str, Product],
                 a: Assumptions, workers: int | None = None,
                 sampled: bool = True) -> tuple[list[DishTemplate], list[tuple[DishTemplate, list[str]]]]:
    """Równolegle (CP-SAT zwalnia GIL). Zwraca (przeszły, [(nie przeszło, powody)]).
    `sampled` — także drugi etap z losowymi, zbilansowanymi dniami (towarzysze z `dishes`). Dni wszystkich dań
    liczą się naraz, falami: najpierw tyle, ile trzeba do zaliczenia, potem reszta tylko dla nierozstrzygniętych."""
    if not candidates:
        return [], []
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    settings = _settings(None)
    library = {**dishes, **{d.id: d for d in candidates}}
    meals = [_audited_meal(d) for d in candidates]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="audyt") as ex:
        results = list(ex.map(lambda i: _fixed_sets(candidates[i], meals[i], library, products, a, settings)
                              if meals[i] is not None else [], range(len(candidates))))
        if sampled:
            partners = partner_pool(dishes, products, a)
            samples = {i: _sample_days(candidates[i], meals[i], partners, products, a)
                       for i in range(len(candidates)) if meals[i] is not None and not results[i]}
            blocks: dict[int, list[BlockReport]] = {i: [] for i in samples}
            open_ = {i for i in samples if samples[i]}
            for wave in (range(SAMPLE_MIN_OK), range(SAMPLE_MIN_OK, SAMPLE_DAYS)):
                jobs = [(i, k) for i in sorted(open_) for k in wave]
                solved = list(ex.map(lambda job: _solve_days(candidates[job[0]], meals[job[0]],
                                                             samples[job[0]][job[1]], library, products, a, settings),
                                     jobs))
                for (i, _k), block in zip(jobs, solved, strict=True):
                    blocks[i].append(block)
                for i in sorted(open_):
                    verdict = _verdict(candidates[i], meals[i], blocks[i], a)
                    if verdict is not None:
                        results[i] = verdict
                        open_.discard(i)
    passed = [d for d, r in zip(candidates, results, strict=True) if not r]
    failed = [(d, r) for d, r in zip(candidates, results, strict=True) if r]
    return passed, failed
