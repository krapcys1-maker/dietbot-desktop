"""Naprawa planu: kod sprawdza (silnikiem, równolegle) zamiany dań w dniach z problemami; LLM wybiera spośród działających.

Kandydaci są ustawiani w kolejności, która ma sens dla problemu: jeśli brakuje węglowodanów, a białka jest za dużo,
najpierw próbujemy dań z większą ilością węglowodanów i mniejszą białka (typowe porcje z profilu dania).
Gdy żadna pojedyncza zamiana nie naprawia całego bloku (np. wtorek i środa z zupą na zapas), przyjmujemy zamiany,
które poprawiają poszczególne dni, i sprawdzamy dalej w kolejnej rundzie.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..domain.assumptions import DAY_KEYS_PL, DAY_LABELS_PL, Assumptions, Weekday
from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType
from ..domain.product import Product
from ..engine import BlockStatus, EngineRequest, EngineResult, EngineSettings, solve_week
from ..engine.solver import context_hash
from ..engine.types import BlockReport
from ..textfmt import fmt_num
from .compose import Composition, SlotPlan
from .pools import Pools

MAX_CANDIDATES_PER_SLOT = 4
MAX_CANDIDATES_NEAR_KEPT = 12  # w dniach z daniem z życzeń (albo nowym) — szukamy szerzej, zanim ruszymy to danie
METRIC_PL = {"kcal": "energia", "protein": "białko", "fat": "tłuszcz", "carbs": "węglowodany"}
NORM = {"kcal": 100.0, "protein": 10.0, "fat": 10.0, "carbs": 20.0, "mass": 100.0}  # "jednostka" zmiany


@dataclass
class SwapOption:
    slot_key: str
    days: list[Weekday]
    meal: MealType
    current: str
    candidate: str
    status: BlockStatus
    objective: float | None
    block_days: list[int] = field(default_factory=list)
    day_badness: dict[int, float] = field(default_factory=dict)  # dzień -> naruszenia po zamianie
    from_alternates: bool = False


def day_badness(report: BlockReport) -> dict[int, float]:
    """Miara naruszeń dla każdego dnia bloku: widełki (kcal/10 i gramy) ważą dużo więcej niż kotwice białka."""
    out = {int(d): 0.0 for d in report.days}
    if report.status in (BlockStatus.MODEL_ERROR, BlockStatus.NO_SOLUTION):
        return {d: float("inf") for d in out}
    for v in report.violations:  # każde wyjście poza widełki liczy się, nawet o ułamek grama
        out[int(v.day)] = out.get(int(v.day), 0.0) + max(100 * abs(v.gap) / (10 if v.metric == "kcal" else 1), 1.0)
    for a in report.anchor_violations:
        for d in a.days:
            out[int(d)] = out.get(int(d), 0.0) + abs(a.gap_g) / len(a.days)
    for m in report.mass_violations:  # 25 g ponad limit ≈ 1 g poza kotwicą białka
        for d in m.days:
            out[int(d)] = out.get(int(d), 0.0) + m.excess_g / 25
    return out


def problem_slots(result: EngineResult, comp: Composition) -> dict[str, str]:
    """Sloty w dniach z problemami -> opis problemu po polsku (do podpowiedzi dla LLM i do dziennika)."""
    problems: dict[str, str] = {}
    for block in result.blocks:
        if block.status is BlockStatus.OK:
            continue
        for av in block.anchor_violations:
            slot = comp.slot(av.days[0], av.meal)
            if slot:
                lo, hi = av.dish_protein_range_g
                problems[slot.key] = (f"{', '.join(DAY_LABELS_PL[d] for d in av.days)}, {MEAL_LABELS_PL[av.meal].lower()}: "
                                      f"białko {fmt_num(av.protein_g)} g poza zakresem {fmt_num(av.min)}–{fmt_num(av.max)} g "
                                      f"(danie daje {fmt_num(lo, 0)}–{fmt_num(hi, 0)} g)")
        for mv in block.mass_violations:
            slot = comp.slot(mv.days[0], mv.meal)
            if slot and slot.key not in problems:
                problems[slot.key] = (f"{', '.join(DAY_LABELS_PL[d] for d in mv.days)}, {MEAL_LABELS_PL[mv.meal].lower()}: "
                                      f"porcja {fmt_num(mv.grams, 0)} g powyżej limitu {fmt_num(mv.cap, 0)} g ({mv.person_id})")
        by_day: dict[int, list[str]] = {}
        for v in block.violations:
            by_day.setdefault(int(v.day), []).append(
                f"{v.person_id}: {METRIC_PL[v.metric]} {fmt_num(v.actual)} poza {fmt_num(v.min)}–{fmt_num(v.max)}")
        for day, texts in by_day.items():
            for s in comp.slots:
                if day in {int(d) for d in s.days} and s.key not in problems:
                    problems[s.key] = f"{DAY_LABELS_PL[Weekday(day)]}: " + "; ".join(texts)
        if block.status in (BlockStatus.MODEL_ERROR, BlockStatus.NO_SOLUTION):
            for s in comp.slots:
                if any(d in block.days for d in s.days):
                    problems.setdefault(s.key, block.message or "błąd modelu")
    return problems


def _needs(block: BlockReport, day: int, meal: MealType | None = None) -> dict[str, float]:
    """Czego brakuje (+) lub jest za dużo (−) w dniu — w jednostkach NORM, zsumowane dla osób."""
    needs = {m: 0.0 for m in NORM}
    for v in block.violations:
        if int(v.day) == day:
            needs[v.metric] -= v.gap / NORM[v.metric]
    for a in block.anchor_violations:
        if meal is not None and a.meal == meal and day in {int(d) for d in a.days}:
            needs["protein"] -= a.gap_g / NORM["protein"]
    for m in block.mass_violations:
        if meal is not None and m.meal == meal and day in {int(d) for d in m.days}:
            needs["mass"] -= m.excess_g / NORM["mass"]
    return needs


def rank_candidates(slot: SlotPlan, current: str, candidates: list[str], pools: Pools, needs: Mapping[str, float]) -> list[str]:
    cur = pools.profiles.get(current)

    def score(d: str) -> float:
        prof = pools.profiles.get(d)
        if prof is None or cur is None:
            return 0.0
        return sum(need * (prof.typical.get(m, 0.0) - cur.typical.get(m, 0.0)) / NORM[m] for m, need in needs.items())

    return sorted(candidates, key=lambda d: (-score(d), d))


def _slot_pool(slot: SlotPlan, pools: Pools, a: Assumptions) -> list[str]:
    if slot.meal is MealType.SNACK:
        return list(pools.treat_snacks)
    for idx, rule in enumerate(a.week.batch):
        if rule.meal == slot.meal and [int(d) for d in rule.days] == [int(d) for d in slot.days]:
            return list(pools.batch.get(idx, []))
    return list(pools.by_meal.get(slot.meal, []))


def evaluate_swaps(comp: Composition, result: EngineResult, pools: Pools, a: Assumptions,
                   products: Mapping[str, Product], dishes: Mapping[str, DishTemplate], settings: EngineSettings,
                   cache: dict, alternates: Mapping[str, list[str]] | None = None,
                   max_per_slot: int = MAX_CANDIDATES_PER_SLOT,
                   cuisine_counts: Mapping[str, int] | None = None, keep: set[str] | None = None,
                   kept_only: bool = False, blocks: set[tuple[int, ...]] | None = None) -> list[SwapOption]:
    """Dla slotów w dniach z problemami sprawdza silnikiem zamienniki (najpierw zapasowe z układu, potem z puli).
    `cuisine_counts` (życzenia „włoska 2 razy”): danie kuchni z życzeń wymieniamy tylko na danie tej samej kuchni,
    a inne nie na danie z tych kuchni — kandydatów odsiewamy przed liczeniem, żeby limit na slot nie przepadł
    na zamiany, które i tak zostałyby odrzucone.
    `keep` (dania z życzeń i nowe dania): ich sloty pomijamy, a w ich dniach sprawdzamy szerzej pozostałe posiłki —
    także wtedy, gdy problem (np. za ciężki talerz) dotyczy samego chronionego dania. `kept_only` sprawdza
    wyłącznie sloty chronionych dań (ostatnia deska ratunku), `blocks` zawęża sprawdzanie do podanych bloków."""
    used = {s.dish_id for s in comp.slots} | {d for b in result.blocks for d in b.choices.values()}
    alternates = alternates or {}
    keep = keep or set()
    # te same ustawienia co przy końcowym liczeniu (poza zbiorem sprzeczności) — wybrana zamiana trafi do pamięci
    # sprawdzamy tylko, czy zamiana działa — krótszy limit (kilka razy szybciej przy ~1% gorszym koszcie);
    # wybraną zamianę silnik i tak liczy potem od nowa z pełnym limitem
    quick = settings.model_copy(update={"conflict_core": False, "parallel_blocks": False,
                                        "max_deterministic_time": min(settings.max_deterministic_time,
                                                                      settings.swap_deterministic_time)})
    tasks: list[tuple[BlockReport, SlotPlan, str, list[dict], bool]] = []
    for block in result.blocks:
        if block.status is BlockStatus.OK or (blocks is not None and tuple(int(d) for d in block.days) not in blocks):
            continue
        bad = {d: b for d, b in day_badness(block).items() if b > 0}
        block_day_set = {int(d) for d in block.days}
        block_slots = [s for s in comp.slots if {int(d) for d in s.days} & block_day_set]
        chosen = {s.key: block.choices.get(s.key, s.dish_id) for s in block_slots}
        seen: set[tuple[str, str]] = set()
        for day in sorted(bad, key=lambda d: -bad[d]):
            day_slots = [s for s in block_slots if day in {int(d) for d in s.days}]
            near_kept = any(chosen[s.key] in keep for s in day_slots)
            if not any(int(v.day) == day for v in block.violations):
                # dzień w widełkach, a problem to białko albo masa talerza w konkretnym posiłku — wymieniamy tylko ten
                # posiłek (chyba że to danie chronione: wtedy szukamy zamian wśród pozostałych posiłków dnia)
                flagged = ({x.meal for x in block.anchor_violations if day in {int(d) for d in x.days}}
                           | {x.meal for x in block.mass_violations if day in {int(d) for d in x.days}})
                flagged_slots = [s for s in day_slots if s.meal in flagged]
                if kept_only or not any(chosen[s.key] in keep for s in flagged_slots):
                    day_slots = flagged_slots or day_slots
            per_slot = max_per_slot if kept_only or not near_kept else max(max_per_slot, MAX_CANDIDATES_NEAR_KEPT)
            for slot in day_slots:
                current = chosen[slot.key]
                if (current in keep) != kept_only:
                    continue
                main = dishes.get(current)
                if main is None:
                    continue
                pool = [d for d in _slot_pool(slot, pools, a)
                        if d != current and d not in used and d in dishes
                        and dishes[d].is_treat == main.is_treat
                        and (slot.meal is not MealType.SUPPER or dishes[d].is_sweet == main.is_sweet)
                        and (not cuisine_counts or dishes[d].cuisine == main.cuisine
                             or (dishes[d].cuisine not in cuisine_counts and main.cuisine not in cuisine_counts))]
                alts = [d for d in alternates.get(slot.key, []) if d in pool]
                ranked = alts + [d for d in rank_candidates(slot, current, pool, pools, _needs(block, day, slot.meal))
                                 if d not in alts]
                for cand in ranked[:per_slot]:
                    if (slot.key, cand) in seen:
                        continue
                    seen.add((slot.key, cand))
                    req_slots = [{"days": [int(x) for x in s.days], "meal": s.meal.value,
                                  "candidates": [cand if s.key == slot.key else chosen[s.key]]} for s in block_slots]
                    tasks.append((block, slot, cand, req_slots, cand in alts))

    if not tasks:
        return []
    # skrót danych wspólnych liczymy raz (przy kilkudziesięciu zamianach to kilkadziesiąt zbędnych zrzutów całej biblioteki)
    context = context_hash(a, products, dishes, quick, True)

    def run(task) -> SwapOption:
        block, slot, cand, req_slots, from_alts = task
        res = solve_week(EngineRequest.model_validate({"slots": req_slots}), a, products, dishes, quick, cache=cache,
                         context=context)
        rep = res.blocks[0] if res.blocks else None
        return SwapOption(slot_key=slot.key, days=slot.days, meal=slot.meal,
                          current=block.choices.get(slot.key, slot.dish_id), candidate=cand, status=res.status,
                          objective=rep.objective if rep else None, block_days=[int(d) for d in block.days],
                          day_badness=day_badness(rep) if rep else {}, from_alternates=from_alts)

    workers = max(1, min(len(tasks), (os.cpu_count() or 2) - 1))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="naprawa") as pool:
        return list(pool.map(run, tasks))


def select_swaps(result: EngineResult, options: list[SwapOption]) -> dict[str, str]:
    """Bez LLM: dla każdego złego bloku zamiana naprawiająca cały blok, a jeśli takiej nie ma — poprawiające dni."""
    swaps: dict[str, str] = {}
    for block in result.blocks:
        if block.status is BlockStatus.OK:
            continue
        days = [int(d) for d in block.days]
        mine = [o for o in options if o.block_days == days]
        full = [o for o in mine if o.status is BlockStatus.OK]
        if full:
            best = sorted(full, key=lambda o: (not o.from_alternates, o.objective or 0, o.candidate))[0]
            swaps[best.slot_key] = best.candidate
            continue
        current = day_badness(block)
        for day in sorted((d for d, b in current.items() if b > 0), key=lambda d: -current[d]):
            better = [o for o in mine if day in {int(x) for x in o.days}
                      and o.day_badness.get(day, float("inf")) < current[day] - 1e-6
                      and o.slot_key not in swaps and o.candidate not in swaps.values()]
            if better:
                best = min(better, key=lambda o: (o.day_badness[day], sum(o.day_badness.values()), o.candidate))
                swaps[best.slot_key] = best.candidate
    return swaps


class Swap(BaseModel):
    slot: str = Field(description="klucz slotu z listy opcji, np. 3_kolacja")
    danie: str = Field(description="id dania z listy działających opcji dla tego slotu")
    uzasadnienie: str = Field(description="1 zdanie po polsku")


class RepairChoice(BaseModel):
    zamiany: list[Swap]


REPAIR_SYSTEM = """Jesteś dietetykiem. Program policzył jadłospis i w niektórych posiłkach nie da się zmieścić w celach.
Dla każdego takiego posiłku program SPRAWDZIŁ już zamienniki, które działają. Wybierz dla każdego problematycznego
dnia jeden zamiennik, który najlepiej pasuje kulinarnie do reszty dnia i tygodnia. Nie wymyślaj innych dań."""


def build_repair_prompt(problems: Mapping[str, str], options: list[SwapOption], comp: Composition,
                        pools: Pools, wishes: str | None = None) -> str:
    lines = []
    if wishes:
        lines += [f"Życzenia domowników na ten tydzień (uwzględnij przy wyborze): {wishes}", ""]
    lines.append("Aktualny tydzień:")
    for s in comp.slots:
        lines.append(f"- {s.key}: {pools.profiles[s.dish_id].name if s.dish_id in pools.profiles else s.dish_id}")
    lines.append("\nProblemy i sprawdzone zamienniki (każdy naprawia swój dzień):")
    for key in sorted({o.slot_key for o in options if o.status is BlockStatus.OK}):
        ok = [o for o in options if o.slot_key == key and o.status is BlockStatus.OK]
        lines.append(f"\n[{key}] {problems.get(key, 'dzień poza celami')}")
        for o in sorted(ok, key=lambda o: o.objective or 0):
            lines.append(f"  - {pools.profiles[o.candidate].line()}")
    return "\n".join(lines)


def apply_swaps(comp: Composition, swaps: Mapping[str, str]) -> Composition:
    slots = []
    for s in comp.slots:
        if s.key in swaps:
            slots.append(SlotPlan(days=s.days, meal=s.meal, dish_id=swaps[s.key], alternates=[]))
        else:
            slots.append(SlotPlan(days=s.days, meal=s.meal, dish_id=s.dish_id, alternates=list(s.alternates)))
    return Composition(slots=slots, comment=comp.comment, source=comp.source)


def day_of(key: str) -> Weekday:
    return Weekday(int(key.split("_")[0].split("-")[0]))


__all__ = ["DAY_KEYS_PL", "RepairChoice", "SwapOption", "apply_swaps", "build_repair_prompt", "day_badness",
           "evaluate_swaps", "problem_slots", "select_swaps"]
