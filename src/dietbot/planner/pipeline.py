"""Planer tygodnia od początku do końca: pule → układ (LLM/algorytm) → zasady → silnik → naprawy → weryfikator."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..checker import VerificationReport, verify_plan
from ..checker.verify import Issue, Severity
from ..domain.assumptions import DAY_LABELS_PL, Weekday
from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType
from ..domain.plan import WeekPlan
from ..engine import BlockStatus, EngineRequest, EngineResult, EngineSettings, solve_week, split_blocks
from ..llm.client import LLMClient, LLMError
from ..storage.repository import Repository
from ..textfmt import fmt_num
from .autofix import autofix_composition, rebalance_days, replace_disallowed
from .compose import (
    COMPOSE_SYSTEM,
    Composition,
    SlotPlan,
    WeekChoice,
    build_compose_prompt,
    clean_alternates,
    compose_fallback,
    composition_from_llm,
    feasible_cuisine_counts,
    limit_new_dishes_to_counts,
    prompt_pools,
    validate_composition,
    vary_treat_snacks,
    wish_errors,
    wish_focus,
)
from .pools import Pools, build_pools
from .portion_review import grams_in_slot, review_portions, slot_label, tighten
from .repair import (
    REPAIR_SYSTEM,
    RepairChoice,
    apply_swaps,
    build_repair_prompt,
    day_badness,
    evaluate_swaps,
    problem_slots,
    select_swaps,
)
from .wishes import WishExclusions, extract_exclusions, resolve_requested_dishes

MAX_COMPOSE_ROUNDS = 3
MAX_REPAIR_ROUNDS = 3


class LiveLog(list[str]):
    """Dziennik planowania, który od razu przekazuje wpisy dalej (np. do paska postępu w aplikacji)."""

    def __init__(self, progress: Callable[[str], None] | None = None):
        super().__init__()
        self.progress = progress

    def append(self, item: str) -> None:
        super().append(item)
        if self.progress is not None:
            self.progress(item)

    def extend(self, items: Iterable[str]) -> None:
        for item in items:
            self.append(item)


@dataclass
class PlanOutcome:
    plan: WeekPlan | None
    report: VerificationReport | None
    engine: EngineResult | None
    composition: Composition | None
    log: list[str] = field(default_factory=list)
    llm_cost_usd: float = 0.0
    seconds: float = 0.0
    # reguły z życzeń na ten tydzień („bez ryb” → produkty, „włoska 2 razy” → liczby kuchni) — zapisywane przy planie,
    # żeby przeliczenie z naprawą po zmianie założeń dalej ich pilnowało
    banned_products: set[str] = field(default_factory=set)
    cuisine_counts: dict[str, int] = field(default_factory=dict)
    # dania z życzeń („zupa pomidorowa”, „schabowy z kurczaka”) → wybrane danie z biblioteki i dni
    requested: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.report and self.report.ok and self.engine and self.engine.status is BlockStatus.OK)

    def chosen(self) -> dict[str, str]:
        """Klucz slotu -> danie wybrane przez silnik (spośród głównego i zapasowych)."""
        out: dict[str, str] = {}
        if self.engine is not None:
            for b in self.engine.blocks:
                out.update(b.choices)
        return out


class WeekPlanner:
    def __init__(self, repo: Repository, llm: LLMClient | None = None, settings: EngineSettings | None = None,
                 cache: dict | None = None):
        self.repo = repo.for_planning()
        self.llm = llm
        # zapasowe dania sprawdza naprawa (równolegle, z krótkim limitem) — szybciej niż jeden duży model z wyborem
        self.settings = (settings or EngineSettings()).model_copy(update={
            "use_alternates": False, "liked_dishes": {d for d, rating in repo.dish_ratings.items() if rating > 0}})
        self.cache: dict = cache if cache is not None else {}

    def dishes_for(self, comp: Composition | None = None) -> dict[str, DishTemplate]:
        """Zatwierdzone dania + dania już obecne w układzie (plan zapisany wcześniej może zawierać danie,
        które potem wycofano z biblioteki — przeliczamy je, ale nowych takich nie wybieramy)."""
        dishes = self.repo.approved_dishes()
        if comp is not None:
            for s in comp.slots:
                for d in (s.dish_id, *s.alternates):
                    if d not in dishes and d in self.repo.dishes:
                        dishes[d] = self.repo.dishes[d]
        return dishes

    def _solve(self, comp: Composition) -> EngineResult:
        req = EngineRequest.model_validate({"slots": comp.to_engine_slots()})
        a, r = self.repo.assumptions, self.repo
        return solve_week(req, a, r.products, self.dishes_for(comp), self.settings, cache=self.cache)

    def pools(self, avoid: set[str] | None = None, extra_banned: set[str] | None = None) -> Pools:
        return build_pools(self.repo.approved_dishes(), self.repo.products, self.repo.assumptions, self.settings,
                           avoid=avoid or set(), extra_banned=extra_banned, ratings=self.repo.dish_ratings)

    def compose(self, pools: Pools, days: list[Weekday], wishes: str | None, seed: int, avoid: set[str],
                log: list[str], prefer: set[str] | None = None,
                cuisine_counts: dict[str, int] | None = None,
                pins: dict[str, list[Weekday]] | None = None) -> tuple[Composition, float]:
        """Układ tygodnia. `prefer` — nowe dania i dania z życzeń, które muszą trafić do planu; `cuisine_counts` —
        liczby z życzeń; `pins` — danie z życzeń → dni, w które ma być (pusta lista = dowolny dzień)."""
        dishes = self.repo.approved_dishes()
        a = self.repo.assumptions
        cost = 0.0
        pins = {d: [x for x in ds if x in days] for d, ds in (pins or {}).items() if d in pools.profiles}
        must_use = {d for d in (prefer or ()) if d in pools.profiles} | set(pins)

        def check(c: Composition) -> tuple[list[str], list[str]]:
            return (validate_composition(c, pools, dishes, a, days),
                    wish_errors(c, pools, dishes, a, days, cuisine_counts, must_use, pins))

        def balance(c: Composition) -> None:
            notes = vary_treat_snacks(c, pools, dishes, protected=must_use, seed=seed, cuisine_counts=cuisine_counts)
            if notes:
                log.append("Różne przekąski w tygodniu:")
                log.extend(f"    - {n}" for n in notes)
            # dni o rozkładzie makro dalekim od celu — wymiana jednego dania na podobne (bez łamania zasad i życzeń)
            notes = rebalance_days(c, pools, dishes, a, days, cuisine_counts,
                                   protected=must_use | wish_focus(wishes, pools, dishes, skip_cuisines=True))
            if notes:
                log.append(f"Bilans makro dni — wymiany ({len(notes)}):")
                log.extend(f"    - {n}" for n in notes)

        if self.llm is not None:
            shown = prompt_pools(pools, dishes, seed, focus=must_use | wish_focus(wishes, pools, dishes),
                                 cuisines={c for c, n in (cuisine_counts or {}).items() if n > 0})
            prompt = build_compose_prompt(shown, a, days, wishes, avoid, must_use, cuisine_counts, pins,
                                          self.repo.preference_hints)
            log.append("Lista dań dla modelu: " + ", ".join(f"{MEAL_LABELS_PL[m].lower()} {len(ids)} z {len(pools.by_meal[m])}"
                                                           for m, ids in shown.by_meal.items()))
            messages_note = ""
            best: tuple[Composition, list[str]] | None = None  # zgodny z zasadami, ale nie ze wszystkimi życzeniami
            llm_down = False
            for round_no in range(1, MAX_COMPOSE_ROUNDS + 1):
                log.append(f"Model językowy układa tydzień (próba {round_no})…")
                try:
                    res = self.llm.complete_json("uklad_tygodnia", COMPOSE_SYSTEM, prompt + messages_note, WeekChoice,
                                                 temperature=0.7, max_tokens=4000)
                except LLMError as exc:
                    log.append(f"Model językowy niedostępny ({exc}) — układam tydzień algorytmem zapasowym")
                    llm_down = True
                    break
                cost += res.cost_usd
                comp = composition_from_llm(res.value, a, days)
                hard, soft = check(comp)
                if hard or soft:
                    # policzalne zasady i życzenia (pula 20%, jajka, kuchnie, nowe dania) poprawia kod — bez kolejnej rundy
                    fixed = composition_from_llm(res.value, a, days)
                    fixes = autofix_composition(fixed, pools, dishes, a, days, cuisine_counts, must_use, pins)
                    f_hard, f_soft = check(fixed)
                    if fixes and not f_hard and (hard or len(f_soft) < len(soft)):
                        log.append(f"Układ łamał zasady ({len(hard) + len(soft)}) — poprawiono automatycznie:")
                        log.extend(f"    - {f}" for f in fixes)
                        comp, hard, soft = fixed, f_hard, f_soft
                if not hard and not soft:
                    log.append(f"Układ tygodnia zgodny z zasadami (koszt {res.cost_usd:.4f} $)")
                    balance(comp)
                    for note in clean_alternates(comp, pools, dishes, a):
                        log.append(f"    {note}")
                    return comp, cost
                if not hard and (best is None or len(soft) < len(best[1])):
                    best = (comp, soft)
                errors = hard + soft
                log.append(f"Układ łamie zasady ({len(errors)}) — proszę model o poprawki:")
                log.extend(f"    - {e}" for e in errors)
                messages_note = ("\n\nPOPRZEDNIA PROPOZYCJA MIAŁA BŁĘDY — popraw je i podaj cały tydzień od nowa:\n- "
                                 + "\n- ".join(errors))
            if best is not None:
                comp, soft = best
                log.append("Układ zgodny z zasadami, ale nie ze wszystkimi życzeniami: " + "; ".join(soft))
                balance(comp)
                for note in clean_alternates(comp, pools, dishes, a):
                    log.append(f"    {note}")
                return comp, cost
            if not llm_down:
                log.append("Model nie ułożył poprawnego tygodnia — używam algorytmu zapasowego")
        else:
            log.append("Układam tydzień algorytmem (bez modelu językowego)…")
        comp = compose_fallback(pools, dishes, a, days, seed=seed)
        hard, soft = check(comp)
        if hard or soft:
            log.extend(f"    poprawka: {f}" for f in autofix_composition(comp, pools, dishes, a, days, cuisine_counts,
                                                                         must_use, pins))
            hard, soft = check(comp)
        for e in hard + soft:
            log.append(f"    algorytm zapasowy: {e}")
        balance(comp)
        clean_alternates(comp, pools, dishes, a)
        return comp, cost

    def plan_week(self, days: list[Weekday] | None = None, wishes: str | None = None, seed: int = 1,
                  avoid: set[str] | None = None, progress: Callable[[str], None] | None = None,
                  prefer: set[str] | None = None, wish_plan: WishExclusions | None = None) -> PlanOutcome:
        """`wish_plan` — życzenia już odczytane przez usługę (z dopasowanymi, a w razie potrzeby wymyślonymi daniami);
        bez niego planer sam odczytuje życzenia i dopasowuje dania z biblioteki."""
        t0 = time.perf_counter()
        days = days or [Weekday(i) for i in range(7)]
        avoid = avoid or set()
        log = LiveLog(progress)
        extra_banned: set[str] = set()
        cuisine_counts: dict[str, int] = {}
        cost_wishes = 0.0
        excl = wish_plan
        if excl is None and wishes and self.llm is not None:
            try:
                excl = extract_exclusions(self.llm, wishes, self.repo.products, self.repo.assumptions.cuisines)
            except LLMError as exc:
                log.append(f"Nie udało się odczytać wykluczeń z życzeń ({exc}) — przekażę je modelowi w całości")
        if excl is not None:
            extra_banned, cuisine_counts = set(excl.product_ids), dict(excl.cuisine_counts)
            if excl.notes:
                log.append("Z życzeń na ten tydzień: " + "; ".join(excl.notes))
            if excl.dishes and not excl.requested:
                try:
                    excl.requested, c = resolve_requested_dishes(self.llm, excl.dishes, self.pools(set(), extra_banned),
                                                                 self.repo.approved_dishes(), self.repo.products)
                    excl.cost_usd += c
                except LLMError as exc:
                    log.append(f"Nie udało się dopasować dań z życzeń ({exc}) — przekażę je modelowi w całości")
            cost_wishes = excl.cost_usd
        pins: dict[str, list[Weekday]] = {}
        requested: list[dict] = []
        for r in (excl.requested if excl else []):
            entry = {"opis": r.request.opis, "danie": r.dish_id, "dni": [int(d) for d in r.days], "wymyslone": r.invented}
            requested.append(entry)
            if r.dish_id is None:
                log.append(f"Z życzeń: nie znalazłem w bibliotece dania „{r.request.opis}” — przekażę życzenie modelowi")
                continue
            pins[r.dish_id] = [d for d in r.days if d in days]
            name = self.repo.dishes[r.dish_id].name if r.dish_id in self.repo.dishes else r.dish_id
            log.append(f"Z życzeń: „{r.request.opis}” → {name}{r.where()}" + (" (nowe danie)" if r.invented else ""))
        # nowe dania i dania z życzeń są świeże — nie wykluczamy ich jako „niedawno użytych”
        fresh = set(prefer or ()) - set(pins)
        avoid = avoid - fresh - set(pins)
        pools = self.pools(avoid, extra_banned)
        sizes = ", ".join(f"{MEAL_LABELS_PL[m].lower()} {len(ids)}" for m, ids in pools.by_meal.items())
        log.append(f"Pule dań: {sizes}")
        if cuisine_counts:
            cuisine_counts, notes = feasible_cuisine_counts(cuisine_counts, pools, self.repo.approved_dishes(),
                                                            self.repo.assumptions, days)
            log.extend(notes)
            if fresh:
                fresh, notes = limit_new_dishes_to_counts(fresh, cuisine_counts, self.repo.approved_dishes())
                log.extend(notes)
        prefer = fresh | set(pins)
        comp, cost = self.compose(pools, days, wishes, seed, avoid, log, prefer, cuisine_counts, pins)
        log.append(f"⏱ układ tygodnia gotowy po {time.perf_counter() - t0:.0f} s")
        out = self.finish(comp, log, cost=cost + cost_wishes, t0=t0, pools=pools, wishes=wishes, keep=prefer,
                          chef=True, cuisine_counts=cuisine_counts, extra_banned=extra_banned)
        out.requested = requested
        if out.plan is not None and out.report is not None:
            in_plan = {m.dish_id for m in out.plan.meals}
            for d in pins:
                if d not in in_plan:
                    name = self.repo.dishes[d].name if d in self.repo.dishes else d
                    msg = f"Danie z życzeń „{name}” nie zmieściło się w celach tego tygodnia — zastąpiło je inne danie"
                    out.log.append(msg)
                    out.report.issues.append(Issue(code="zyczenie_pominiete", severity=Severity.WARNING, message=msg))
            for r in (excl.requested if excl else []):
                if r.dish_id is None:  # w dzienniku już jest („nie znalazłem w bibliotece…”) — tu uwaga przy planie
                    out.report.issues.append(Issue(
                        code="zyczenie_pominiete", severity=Severity.WARNING,
                        message=f"Danie z życzeń „{r.request.opis}”: nie znalazłem go w bibliotece — model układający "
                                "tydzień dostał życzenie jako tekst"))
        return out

    def finish(self, comp: Composition, log: list[str] | None = None, *, cost: float = 0.0, t0: float | None = None,
               repair: bool = True, pools: Pools | None = None, wishes: str | None = None,
               keep: set[str] | None = None, chef: bool = False,
               cuisine_counts: dict[str, int] | None = None, extra_banned: set[str] | None = None) -> PlanOutcome:
        """Silnik liczy gramatury dla układu, opcjonalnie naprawia problematyczne posiłki, weryfikator sprawdza wynik.
        `extra_banned` — produkty wykluczone życzeniami tego planu; bez gotowych `pools` naprawa buduje pule bez nich."""
        t0 = t0 if t0 is not None else time.perf_counter()
        log = log if log is not None else LiveLog()
        a = self.repo.assumptions
        if repair:
            # dania, które przestały pasować (np. nowa zasada z notatnika: zakaz produktu, limit porcji, ocena 👎) —
            # silnik policzyłby je bez odchyleń, a weryfikator i tak by je odrzucił
            pools = pools or self.pools(extra_banned=extra_banned)
            replaced = replace_disallowed(comp, pools, self.repo.approved_dishes(), a, keep)
            for note in replaced:
                log.append(f"Zamiana na dozwolone danie — {note}")
        dishes = self.dishes_for(comp)
        log.append("Silnik liczy gramatury…")
        result = self._solve(comp)
        log.append(f"Silnik: {_status_pl(result.status)}")

        for round_no in range(1, (MAX_REPAIR_ROUNDS if repair else 0) + 1):
            if result.status is BlockStatus.OK:
                break
            pools = pools or self.pools(extra_banned=extra_banned)
            problems = problem_slots(result, comp)
            bad_days = sorted({int(d) for b in result.blocks if b.status is not BlockStatus.OK for d in b.days})
            log.append("Szukam zamienników: " + ", ".join(DAY_LABELS_PL[Weekday(d)].lower() for d in bad_days)
                       + f" (runda {round_no})…")
            last_round = round_no == MAX_REPAIR_ROUNDS
            swap_args = (comp, result, pools, a, self.repo.products, dishes, self.settings, self.cache)
            swap_kw = {"alternates": {s.key: list(s.alternates) for s in comp.slots}, "cuisine_counts": cuisine_counts,
                       "keep": keep}
            options = evaluate_swaps(*swap_args, **swap_kw)
            stuck = blocks_without_help(result, options, keep, last_round)
            if keep and stuck:
                # danie z życzeń (albo nowe) wymieniamy dopiero wtedy, gdy nic innego nie pomaga temu blokowi
                options += evaluate_swaps(*swap_args, **swap_kw, kept_only=True, blocks=stuck)
            options = respect_cuisine_counts(protect_new_dishes(options, keep, result, last_round), dishes,
                                             cuisine_counts)
            working = [o for o in options if o.status is BlockStatus.OK]
            log.append(f"    sprawdzono {len(options)} zamian, w pełni działających: {len(working)}")
            swaps: dict[str, str] = {}
            block_of = {o.slot_key: tuple(o.block_days) for o in options}
            current = {s.key: s.dish_id for s in comp.slots}

            def swap_text(key: str, dish_id: str, current: dict[str, str] = current) -> str:
                # dziennik czyta użytkownik („Jak powstał ten plan”) — nazwy dań i dni zamiast identyfikatorów
                try:
                    where = slot_label(key)
                except (ValueError, KeyError):
                    where = key
                old = self.repo.dishes.get(current.get(key, ""))
                new = self.repo.dishes.get(dish_id)
                return (f"{where}: {old.name if old else current.get(key, '?')} → "
                        f"{new.name if new else dish_id}")
            llm_blocks: set[tuple[int, ...]] = set()
            if self.llm is not None and working:
                try:
                    res = self.llm.complete_json("naprawa_planu", REPAIR_SYSTEM,
                                                 build_repair_prompt(problems, options, comp, pools, wishes), RepairChoice,
                                                 temperature=0.2, max_tokens=1500)
                    cost += res.cost_usd
                    valid = {(o.slot_key, o.candidate) for o in working}
                    for sw in res.value.zamiany:
                        block = block_of.get(sw.slot)
                        if (sw.slot, sw.danie) in valid and block not in llm_blocks and sw.danie not in swaps.values():
                            swaps[sw.slot] = sw.danie
                            llm_blocks.add(block)
                            log.append(f"    zamiana {swap_text(sw.slot, sw.danie)}: {sw.uzasadnienie}")
                        else:
                            log.append(f"    pominięto propozycję modelu {swap_text(sw.slot, sw.danie)}")
                except LLMError as exc:
                    log.append(f"    model językowy niedostępny przy naprawie ({exc})")
            for key, cand in select_swaps(result, options).items():
                if block_of.get(key) in llm_blocks or cand in swaps.values():
                    continue
                swaps[key] = cand
                log.append(f"    zamiana {swap_text(key, cand)} (według silnika)")
            if not swaps:
                log.append("    żadna zamiana nie poprawia planu")
                break
            # w naprawianych blokach zostają dania wybrane przez silnik (tak jak przy sprawdzaniu zamian)
            repaired_days = {d for k in swaps for d in block_of.get(k, ())}
            chosen = {k: v for b in result.blocks for k, v in b.choices.items()}
            slots = [SlotPlan(days=list(sl.days), meal=sl.meal, dish_id=chosen.get(sl.key, sl.dish_id))
                     if {int(d) for d in sl.days} & repaired_days else sl for sl in comp.slots]
            comp = apply_swaps(Composition(slots=slots, comment=comp.comment, source=comp.source), swaps)
            dishes = self.dishes_for(comp)
            result = self._solve(comp)
            log.append(f"Silnik po naprawie {round_no}: {_status_pl(result.status)}")

        report = None
        if result.plan is not None:
            engine_totals = [t.model_dump(mode="json") for b in result.blocks for t in b.totals]
            report = verify_plan(result.plan, a, self.repo.products, dishes, engine_totals,
                                 group_tolerance=self.settings.group_ratio_tolerance)
            n_err = len(report.errors())
            log.append("Weryfikator: plan poprawny" if report.ok else f"Weryfikator: błędy ({n_err})")
            log.append(f"⏱ silnik i weryfikacja po {time.perf_counter() - t0:.0f} s")
            if chef and self.llm is not None and report.ok and result.status is BlockStatus.OK:
                result, report, cost = self._chef_pass(comp, result, report, dishes, log, cost)
                log.append(f"⏱ kontrola kucharza po {time.perf_counter() - t0:.0f} s")
            result.plan.comment = comp.comment
        return PlanOutcome(plan=result.plan, report=report, engine=result, composition=comp, log=list(log),
                           llm_cost_usd=cost, seconds=time.perf_counter() - t0,
                           banned_products=set(extra_banned or ()), cuisine_counts=dict(cuisine_counts or {}))

    def _chef_pass(self, comp: Composition, result: EngineResult, report: VerificationReport,
                   dishes: dict[str, DishTemplate], log: list[str], cost: float):
        """Kontrola kucharza porcji: poważne uwagi zawężają zakres składnika i silnik liczy te dni od nowa."""
        a, products = self.repo.assumptions, self.repo.products
        try:
            issues, slots, c = review_portions(self.llm, result.plan, dishes, products, a)
        except LLMError as exc:
            log.append(f"Kontrola kucharza porcji niedostępna ({exc})")
            return result, report, cost
        cost += c
        if not issues:
            log.append(f"Kontrola kucharza porcji: bez uwag (koszt {c:.4f} $)")
            return result, report, cost
        serious = [i for i in issues if i.waga == "powazna"]
        log.append(f"Kontrola kucharza porcji: uwagi {len(issues)}, poważne {len(serious)} (koszt {c:.4f} $)")
        overrides: dict[str, DishTemplate] = {}
        applied = []
        ref_kcal = self.settings.reference_kcal
        for issue in serious:
            meal = slots[issue.slot]
            dish_id = meal.dish_id
            refs = [sum(i.grams for i in p.items if i.product_id == issue.produkt) * ref_kcal
                    / a.person(p.person_id).energy_center for p in meal.portions]
            plate_ref = (max(refs) if issue.kierunek == "za_duzo" else min(refs)) if refs else None
            tightened = tighten(overrides.get(dish_id, dishes[dish_id]), issue.produkt, issue.kierunek, plate_ref)
            if tightened is not None:
                overrides[dish_id] = tightened
                applied.append(issue)
        for issue in issues:
            if issue.waga != "powazna":
                log.append(f"    drobna uwaga: {slot_label(issue.slot)} — {issue.problem}")
        remaining = [i for i in serious if i not in applied]
        if applied:
            chosen = {k: v for b in result.blocks for k, v in b.choices.items()}
            fixed = comp.fixed_to_chosen(chosen)
            request = EngineRequest.model_validate({"slots": fixed.to_engine_slots()})
            touched = {i.slot for i in applied}
            blocks = [b for b in split_blocks(request.slots) if any(s.key in touched for s in b)]
            sub = EngineRequest.model_validate({"slots": [
                {"days": [int(d) for d in s.days], "meal": s.meal.value, "candidates": list(s.candidates)}
                for b in blocks for s in b]})
            dishes2 = {**dishes, **overrides}
            res2 = solve_week(sub, a, products, dishes2, self.settings, cache=self.cache)
            accepted = False
            if res2.status is BlockStatus.OK and res2.plan is not None:
                days2 = {int(d) for b in res2.blocks for d in b.days}
                plan2 = result.plan.model_copy(deep=True)
                meal_order = {m: i for i, m in enumerate(a.day.meals)}
                person_order = {p.id: i for i, p in enumerate(a.persons)}
                plan2.meals = sorted([m for m in plan2.meals if int(m.day) not in days2] + res2.plan.meals,
                                     key=lambda m: (int(m.day), meal_order.get(m.meal, 9)))
                plan2.snacks = sorted([s for s in plan2.snacks if int(s.day) not in days2] + res2.plan.snacks,
                                      key=lambda s: (int(s.day), person_order.get(s.person_id, 9)))
                blocks2 = sorted([b for b in result.blocks if not {int(d) for d in b.days} & days2] + res2.blocks,
                                 key=lambda b: int(b.days[0]))
                totals = [t.model_dump(mode="json") for b in blocks2 for t in b.totals]
                report2 = verify_plan(plan2, a, products, dishes2, totals,
                                      group_tolerance=self.settings.group_ratio_tolerance)
                if report2.ok:
                    for issue in applied:
                        before = grams_in_slot(result.plan, issue.slot, issue.produkt)
                        after = grams_in_slot(plan2, issue.slot, issue.produkt)
                        change = ", ".join(f"{fmt_num(before.get(p, 0), 0)} → {fmt_num(after.get(p, 0), 0)} g"
                                           for p in before)
                        log.append(f"    {slot_label(issue.slot)}: {products[issue.produkt].name.lower()} {change} "
                                   f"(kucharz: {issue.problem})")
                    result = EngineResult(status=BlockStatus.OK, plan=plan2, blocks=blocks2, engine=result.engine,
                                          input_hash=result.input_hash)
                    report = report2
                    accepted = True
            if not accepted:
                log.append("    poprawka porcji nie mieści się w celach — zostaje poprzednia wersja")
                remaining = serious
        for issue in remaining:
            days = [Weekday(int(d)) for d in issue.slot.partition("_")[0].split("-")]
            meal = MealType(issue.slot.partition("_")[2])
            log.append(f"    uwaga (bez zmiany): {slot_label(issue.slot)} — {issue.problem}")
            report.issues.append(Issue(code="uwaga_kucharza", severity=Severity.WARNING,
                                       message=f"{slot_label(issue.slot)}: {issue.problem} (uwaga kucharza)",
                                       day=days[0], meal=meal))
        return result, report, cost


def respect_cuisine_counts(options: list, dishes: dict[str, DishTemplate], counts: dict[str, int] | None) -> list:
    """Zamiana w naprawie nie może zmienić liczby dań kuchni, o którą prosili domownicy („włoska 2 razy”)."""
    if not counts:
        return options

    def cuisine(d: str) -> str | None:
        return dishes[d].cuisine if d in dishes else None

    return [o for o in options if cuisine(o.current) == cuisine(o.candidate)
            or (cuisine(o.current) not in counts and cuisine(o.candidate) not in counts)]


def blocks_without_help(result: EngineResult, options: list, keep: set[str] | None,
                        last_round: bool = True) -> set[tuple[int, ...]]:
    """Złe bloki, którym nie pomaga żadna zamiana dania spoza `keep`: żadna ich w pełni nie naprawia, a przed ostatnią
    rundą naprawy — żadna nie poprawia też któregoś dnia (poprawiony blok sprawdzamy dalej w kolejnej rundzie)."""
    helped: set[tuple[int, ...]] = set()
    badness = {tuple(int(d) for d in b.days): day_badness(b) for b in result.blocks if b.status is not BlockStatus.OK}
    for o in options:
        block = tuple(o.block_days)
        if o.current in (keep or ()) or block not in badness:
            continue
        improves = any(o.day_badness.get(d, float("inf")) < bad - 1e-6 for d, bad in badness[block].items() if bad > 0)
        if o.status is BlockStatus.OK or (improves and not last_round):
            helped.add(block)
    return set(badness) - helped


def protect_new_dishes(options: list, keep: set[str] | None, result: EngineResult | None = None,
                       last_round: bool = True) -> list:
    """Naprawa nie wyrzuca dań z życzeń ani nowych dań wymyślonych na prośbę, jeśli blok da się naprawić inną
    zamianą — a przed ostatnią rundą także wtedy, gdy inna zamiana choć poprawia któryś dzień bloku. Takie danie
    wymieniamy tylko na zamianę, która naprawia cały blok (nie dla samej poprawy)."""
    if not keep:
        return options
    if result is None:
        protected = {tuple(o.block_days) for o in options if o.status is BlockStatus.OK and o.current not in keep}
    else:
        bad = {tuple(int(d) for d in b.days) for b in result.blocks if b.status is not BlockStatus.OK}
        protected = bad - blocks_without_help(result, options, keep, last_round)
    return [o for o in options if not (o.current in keep and (tuple(o.block_days) in protected
                                                              or (result is not None and o.status is not BlockStatus.OK)))]


STATUS_PL = {
    BlockStatus.OK: "wszystkie dni w widełkach",
    BlockStatus.SOFT: "drobne odchylenia od kotwic białka",
    BlockStatus.HARD: "niektóre dni poza widełkami",
    BlockStatus.NO_SOLUTION: "brak rozwiązania dla części dni",
    BlockStatus.MODEL_ERROR: "błąd modelu",
}


def _status_pl(status: BlockStatus) -> str:
    return STATUS_PL.get(status, status.value)
