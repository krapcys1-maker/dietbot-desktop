"""Warstwa usług: cała logika aplikacji w jednym miejscu — używają jej okno aplikacji (API), Telegram i czat."""

from __future__ import annotations

import os
import random
import re
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .. import __version__
from ..checker.verify import verify_plan
from ..domain.assumptions import DAY_LABELS_PL, Assumptions, Weekday
from ..domain.diet import compatible, dish_compatible
from ..domain.dish import MEAL_LABELS_PL, DishStatus, MealType, cuisine_label
from ..domain.plan import WeekPlan
from ..domain.product import CATEGORY_LABELS_PL, Category, Product, State, StoreProduct
from ..engine import BlockStatus, EngineRequest, solve_week
from ..engine.settings import EngineSettings
from ..food.matcher import ProductMatcher
from ..llm.client import CostLedger, LLMClient, LLMError, LLMSettings, load_dotenv_file, save_dotenv_value
from ..planner.compose import Composition, SlotPlan, validate_composition
from ..planner.pipeline import LiveLog, WeekPlanner
from ..planner.pools import Pools, build_pools
from ..planner.repair import apply_swaps
from ..report.markdown import render_plan, render_shopping_list
from ..report.text import markdown_to_text
from ..shopping import build_shopping_list
from ..storage.paths import Paths
from ..storage.repository import Repository, check_consistency
from ..storage.yamlio import dump_yaml, load_yaml, to_plain
from ..textfmt import plural_pl
from ..textnorm import normalize
from .jobs import Job, JobManager, UserError
from .snapshots import capture, matches_current, restore
from .store import PlanRecord, PlanStore
from .validation import polish_errors
from .views import assumptions_hash, dish_view, persons_view, plan_view, product_view

MONTHS_GEN = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września",
              "października", "listopada", "grudnia"]
OWN_PRODUCTS_FILE = "wlasne.yaml"
API_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{20,256}$")


def default_week_start(today: date | None = None) -> date:
    """Od piątku planujemy już następny tydzień; wcześniej — bieżący."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(days=7) if today.weekday() >= 4 else monday


def week_title(start: date | None, days: list[Weekday]) -> str:
    if start is None:
        return f"Plan z {datetime.now():%d.%m.%Y %H:%M}"
    first = start + timedelta(days=int(min(days)))
    last = start + timedelta(days=int(max(days)))
    if first.month == last.month:
        return f"Tydzień {first.day}–{last.day} {MONTHS_GEN[last.month - 1]}"
    return f"Tydzień {first.day} {MONTHS_GEN[first.month - 1]} – {last.day} {MONTHS_GEN[last.month - 1]}"


def composition_from_plan(plan: WeekPlan, comment: str | None = None, source: str = "plan") -> Composition:
    """Układ odtworzony z planu (dania wybrane przez silnik; posiłki na zapas łączone w jeden slot)."""
    slots: list[SlotPlan] = []
    seen_batch: set[str] = set()
    for m in sorted(plan.meals, key=lambda m: (int(m.day), m.meal.value)):
        if m.batch_group:
            if m.batch_group in seen_batch:
                continue
            seen_batch.add(m.batch_group)
            days = sorted({x.day for x in plan.meals if x.batch_group == m.batch_group})
            slots.append(SlotPlan(days=list(days), meal=m.meal, dish_id=m.dish_id))
        else:
            slots.append(SlotPlan(days=[m.day], meal=m.meal, dish_id=m.dish_id))
    return Composition(slots=slots, comment=comment, source=source)


def slug(text: str) -> str:
    return "_".join(normalize(text).replace("%", "").split())[:60].strip("_")


def merge_yaml(target, source):
    """Wpisuje nowe wartości do dokumentu YAML, zachowując komentarze i styl list/słowników."""
    if isinstance(target, CommentedMap) and isinstance(source, dict):
        for k in [k for k in target if k not in source]:
            del target[k]
        for k, v in source.items():
            if k in target:
                target[k] = merge_yaml(target[k], v)
            else:
                target[k] = v
        return target
    if isinstance(target, CommentedSeq) and isinstance(source, list):
        if len(target) == len(source) and all(isinstance(x, dict) for x in source) \
                and all(isinstance(x, CommentedMap) for x in target):
            for i, v in enumerate(source):
                target[i] = merge_yaml(target[i], v)
            return target
        new = CommentedSeq(source)
        if target.fa.flow_style():
            new.fa.set_flow_style()
        return new
    return source


RECENT_WEEKS = 3  # tyle ostatnich tygodni pomijamy przy układaniu nowego planu
MIN_POOL_AFTER_AVOID = 40  # ale każdy posiłek musi mieć tyle dań do wyboru
INVENT_ATTEMPTS = 2  # tyle razy zamawiamy u generatora danie z życzeń, którego nie ma w bibliotece
# reguły z życzeń zapisane razem z układem planu — kolejne wersje (zamiana, przeliczenie, naprawa) je dziedziczą
COMPOSITION_EXTRAS = ("wykluczone_produkty", "kuchnie_liczby")


def composition_extras(composition: dict | None) -> dict:
    """Wykluczone produkty i liczby kuchni z życzeń zapisane przy planie (puste dla starszych planów)."""
    src = composition or {}
    return {k: src[k] for k in COMPOSITION_EXTRAS if k in src}


class DietService:
    def __init__(self, paths: Paths | None = None, *, use_llm: bool = True, db_path: Path | None = None):
        self.paths = paths or Paths.discover()
        load_dotenv_file(self.paths)
        self.use_llm = use_llm
        # zbioru sprzecznych ograniczeń aplikacja nigdzie nie pokazuje — bez niego dni z odchyleniami liczą się szybciej
        self.settings = EngineSettings(use_alternates=False, conflict_core=False)
        self.engine_cache: dict = {}
        self._lock = threading.RLock()
        self.repo = Repository.load(self.paths)
        self._pools: Pools | None = None
        self.store = PlanStore(db_path or self.paths.out / "dietbot.sqlite3")
        self.repo.dish_ratings = self.store.ratings()
        # Legacy records lack historical source tables: freeze today's view and say so explicitly.
        for record in self.store.list(limit=-1):
            if self.store.snapshot(record.id) is None:
                self.store.freeze(record.id, capture(self.repo, record.plan, legacy=True))
        self.jobs = JobManager()
        self._llm: LLMClient | None = None
        self._llm_error: str | None = None
        self.pending_changes: dict[str, dict] = {}  # propozycje zmian założeń z czatu, czekające na potwierdzenie

    # ------------------------------------------------------------------ podstawy
    def reload(self) -> None:
        with self._lock:
            self.repo = Repository.load(self.paths)
            self.repo.dish_ratings = self.store.ratings()
            self._pools = None
            self.engine_cache.clear()

    def pools(self) -> Pools:
        with self._lock:
            if self._pools is None:
                self._pools = build_pools(self.repo.dishes, self.repo.products, self.repo.assumptions,
                                          self.settings, ratings=self.repo.dish_ratings)
            return self._pools

    def llm(self) -> LLMClient | None:
        if not self.use_llm:
            self._llm_error = "model językowy wyłączony"
            return None
        if self._llm is None:
            try:
                self._llm = LLMClient(paths=self.paths)
            except LLMError as exc:
                self._llm_error = str(exc)
                return None
        return self._llm

    def humanize(self, text: str) -> str:
        """Komunikaty zasad zawierają identyfikatory dań (potrzebne modelowi) — dla ludzi zamieniamy je na nazwy."""
        import re

        dishes = self.repo.dishes
        return re.sub(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b", lambda m: dishes[m.group(0)].name if m.group(0) in dishes else m.group(0),
                      text)

    def _record(self, plan_id: str) -> PlanRecord:
        rec = self.store.get(plan_id)
        if rec is None:
            raise UserError("Nie ma takiego planu (mógł zostać usunięty).")
        return self.checked_record(rec)

    def checked_record(self, rec: PlanRecord) -> PlanRecord:
        """Sprawdza gramatury wobec zapisanej kopii tabel i założeń; raport nie zastępuje obliczeń."""
        repo = self.record_repo(rec)
        report = verify_plan(rec.plan, repo.assumptions, repo.products, repo.dishes,
                             group_tolerance=self.settings.group_ratio_tolerance)
        if rec.report:
            report.issues.extend(i for i in rec.report.issues if i.code in {"uwaga_kucharza", "zyczenie_pominiete"})
        return replace(rec, report=report, ok=report.ok)

    def record_repo(self, rec: PlanRecord) -> Repository:
        snapshot = rec.snapshot or self.store.snapshot(rec.id)
        return restore(snapshot, self.repo) if snapshot else self.repo

    def _ensure_exists(self, plan_id: str) -> None:
        """Zadanie w kolejce mogło przeżyć usunięcie planu — nowa wersja bez rodzica kończyłaby się śladem stosu."""
        if self.store.get(plan_id) is None:
            raise UserError("Plan, którego dotyczyła ta zmiana, został w międzyczasie usunięty.")

    def status(self) -> dict:
        repo = self.repo
        settings = LLMSettings.load(self.paths)
        ledger = CostLedger(self.paths.out / "llm_koszty.jsonl", settings)
        check = repo.check()
        pools = self.pools()
        active = self.store.active()
        from .telegram_bot import allowed_users  # ta sama reguła czytania listy co w bocie

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        allowed = allowed_users()
        return {
            "version": __version__,
            "products": len(repo.products), "dishes": len(repo.dishes), "approved_dishes": len(repo.approved_dishes()),
            "pools": {m.value: len(ids) for m, ids in pools.by_meal.items()},
            "batch_pools": {str(i): len(ids) for i, ids in pools.batch.items()},
            "store_products": len(repo.store_products),
            "data_errors": check.errors, "data_warnings": check.warnings,
            "llm": {"provider": settings.provider, "model": settings.model,
                    "key_present": bool(os.environ.get(settings.api_key_env, "").strip()),
                    "enabled": self.use_llm, "spent_usd": round(ledger.spent_usd(), 4),
                    "budget_usd": settings.budget_usd},
            "telegram": {"configured": bool(token), "allowed_users": len(allowed)},
            "active_plan_id": active.id if active else None,
            "jobs_busy": self.jobs.busy(),
            "persons": persons_view(repo.assumptions),
            "default_week_start": default_week_start().isoformat(),
        }

    def save_api_key(self, key: str) -> dict:
        """Zapisuje własny klucz użytkownika; nigdy nie zwraca jego wartości."""
        key = key.strip()
        if not API_KEY_PATTERN.fullmatch(key):
            raise UserError("Klucz API powinien mieć 20–256 znaków i nie może zawierać spacji.")
        settings = LLMSettings.load(self.paths)
        save_dotenv_value(self.paths, settings.api_key_env, key)
        os.environ[settings.api_key_env] = key
        self._llm = None
        self._llm_error = None
        self.use_llm = True
        return self.status()

    def delete_api_key(self) -> dict:
        settings = LLMSettings.load(self.paths)
        save_dotenv_value(self.paths, settings.api_key_env, None)
        os.environ.pop(settings.api_key_env, None)
        self._llm = None
        self._llm_error = "brak klucza API"
        return self.status()

    # ------------------------------------------------------------------ plany
    def _invent(self, llm: LLMClient, request: str, max_total: int, progress: Callable[[str], None]) -> list:
        """Model wymyśla dania, kod je sprawdza; przyjęte zapisujemy w bibliotece (dane/dania/gen_*.yaml)."""
        from ..llm.generate_dishes import save_generated
        from ..planner.invent import invent_dishes

        progress("Model wymyśla nowe dania z produktów z bazy…")
        result = invent_dishes(self.repo, llm, request, max_total=max_total, log=progress)
        if result.accepted:
            with self._lock:
                save_generated(result.accepted, self.paths.dishes_dir)
                self.reload()
            progress(f"Nowe dania w bibliotece ({len(result.accepted)}): " + "; ".join(d.name for d in result.accepted))
        if result.rejected:
            progress(f"Odrzucone przez kontrolę ({len(result.rejected)}): " + "; ".join(
                f"{r.get('nazwa', '?')} — {r['powody'][0] if r.get('powody') else '?'}" for r in result.rejected[:6]))
        progress(f"Koszt wymyślania: {result.cost_usd:.4f} $")
        return result.accepted

    def _resolve_wishes(self, llm: LLMClient, wishes: str, progress: Callable[[str], None]):
        """Życzenia przed układaniem tygodnia: wykluczenia, liczby kuchni i konkretne dania. Dania z życzeń kod dopasowuje
        do biblioteki (model wskazuje właściwe spośród kandydatów); gdy biblioteka nie ma pasującego dania, model je
        wymyśla, a kontrola kodu, test silnika i kucharz sprawdzają je jak każde inne. None = nie udało się odczytać."""
        from ..planner.wishes import extract_exclusions, resolve_requested_dishes

        repo = self.repo
        try:
            excl = extract_exclusions(llm, wishes, repo.products, repo.assumptions.cuisines)
        except LLMError as exc:
            progress(f"Nie udało się odczytać życzeń ({exc}) — przekażę je modelowi w całości")
            return None
        if not excl.dishes:
            return excl
        progress("Szukam w bibliotece dań z życzeń: " + "; ".join(d.opis for d in excl.dishes))
        pools = build_pools(repo.approved_dishes(), repo.products, repo.assumptions, self.settings,
                            extra_banned=excl.product_ids, ratings=repo.dish_ratings)
        try:
            excl.requested, cost = resolve_requested_dishes(llm, excl.dishes, pools, repo.approved_dishes(), repo.products)
        except LLMError as exc:
            progress(f"Nie udało się dopasować dań z życzeń ({exc}) — przekażę je modelowi w całości")
            return excl
        excl.cost_usd += cost
        missing = [r for r in excl.requested if r.dish_id is None]
        for r in (r for r in missing if r.snack):
            # przekąski z puli 20% (lody, chipsy…) są w bibliotece przekąsek — nie wymyślamy ich jako dań
            progress(f"Nie znalazłem przekąski „{r.request.opis}” wśród przekąsek z puli 20% — przekażę życzenie modelowi")
        missing = [r for r in missing if not r.snack]
        if missing:
            excl.cost_usd += self._invent_requested(llm, missing, excl.product_ids, progress)
        return excl

    def _invent_requested(self, llm: LLMClient, missing: list, banned: set[str], progress: Callable[[str], None]) -> float:
        """Dania z życzeń, których nie ma w bibliotece: po jednym zamówieniu na danie (z zapasem propozycji)."""
        from ..llm.generate_dishes import GenOrder, run_generation_plan, save_generated
        from ..planner.invent import SOUP_DETAILS
        from ..planner.wishes import match_cuisine, request_match_problem

        cost = 0.0
        for r in missing:
            meal = r.meal if r.meal in (MealType.BREAKFAST, MealType.LUNCH, MealType.SUPPER) else MealType.LUNCH
            cuisines = self.repo.assumptions.cuisines
            cuisine = match_cuisine(r.request.kuchnia or "", cuisines) if r.request.kuchnia else None
            details = SOUP_DETAILS if r.batch else ("danie główne na talerzu, NIE zupa" if meal is MealType.LUNCH else "")
            without = ", ".join(sorted(self.repo.products[b].name.lower() for b in banned if b in self.repo.products)[:15])
            # z podstawowych produktów: gotowe „pierogi ruskie” ze sklepu łamią zasadę źródeł węglowodanów, a opis
            # z twarogiem i ziemniakami nie zgadza się wtedy ze składnikami (test na żywo 14.09.2026)
            special = "; ".join(x for x in (f"dokładnie to danie, o które prosili domownicy: {r.request.opis} — każda "
                                            "propozycja to właśnie to danie (mogą różnić się dodatkami)", details,
                                            "przygotowane z podstawowych produktów (np. pierogi: mąka, ziemniaki, twaróg), "
                                            "nie z gotowego produktu o tej samej nazwie",
                                            f"bez produktów: {without}" if without else "") if x)
            check = lambda name, opis=r.request.opis: request_match_problem(opis, name)  # noqa: E731
            order = GenOrder(meal, cuisine or ("polska" if "polska" in cuisines else cuisines[0]), ask=3, special=special,
                             keep=1, name_check=check)
            progress(f"W bibliotece nie ma dania „{r.request.opis}” — model je wymyśla…")
            accepted = []
            for attempt in range(1, INVENT_ATTEMPTS + 1):
                if attempt > 1:
                    # propozycje odpadają losowo (walidacja, test silnika, kucharz) — druga próba to kilka groszy
                    progress(f"Żadna propozycja dania „{r.request.opis}” nie przeszła kontroli — próbuję jeszcze raz…")
                accepted, _rejected, c = run_generation_plan(
                    self.repo, llm, plan=[order], workers=1, log=progress, review=True, engine_check=True,
                    source=f"z życzeń domowników ({date.today():%d.%m.%Y}): {r.request.opis[:120]}")
                cost += c
                # kucharz może poprawić danie (i nazwę) — nazwa ma dalej odpowiadać życzeniu
                accepted = [d for d in accepted if check(d.name) is None]
                if accepted:
                    break
            if not accepted:
                progress(f"Nie udało się wymyślić dania „{r.request.opis}” — przekażę życzenie modelowi")
                continue
            with self._lock:
                save_generated(accepted[:1], self.paths.dishes_dir)
                self.reload()
            r.dish_id, r.invented = accepted[0].id, True
            progress(f"Nowe danie z życzeń w bibliotece: {accepted[0].name}")
        return cost

    def recent_dishes(self, repo, weeks: int = RECENT_WEEKS, min_pool: int = MIN_POOL_AFTER_AVOID) -> tuple[set[str], int]:
        """Dania z ostatnich planów (najnowsza wersja każdego tygodnia) do pominięcia w nowym planie.

        Gdy po pominięciu któraś pula posiłku byłaby za mała (np. po odrzuceniu wielu dań), bierzemy mniej tygodni."""
        families: list[set[str]] = []
        seen: set[str] = set()
        for rec in self.store.list(limit=60):
            key = rec.family_id or rec.id
            if key in seen:
                continue
            seen.add(key)
            families.append({m.dish_id for m in rec.plan.meals})
            if len(families) >= weeks:
                break
        while families:
            avoid = set().union(*families) - {d for d, rating in repo.dish_ratings.items() if rating > 0}
            pools = build_pools(repo.approved_dishes(), repo.products, repo.assumptions, self.settings, avoid=avoid)
            if all(len(ids) >= min_pool for ids in pools.by_meal.values()):
                return avoid, len(families)
            families.pop()
        return set(), 0

    def invent(self, request: str, max_total: int = 6, channel: str = "aplikacja") -> Job:
        """Tylko nowe dania do biblioteki (bez układania planu)."""
        request = (request or "").strip()[:600]
        if len(request) < 3:
            raise UserError("Napisz, jakie dania wymyślić (np. „trzy kolacje z krewetkami”).")
        max_total = max(1, min(int(max_total), 12))

        def run(progress: Callable[[str], None]) -> dict:
            llm = self.llm()
            if llm is None:
                raise UserError(f"Wymyślanie dań wymaga modelu językowego ({self._llm_error}).")
            accepted = self._invent(llm, request, max_total, progress)
            if not accepted:
                raise UserError("Żadne z wymyślonych dań nie przeszło kontroli — spróbuj opisać prośbę inaczej.")
            return {"dishes": [{"id": d.id, "name": d.name, "meal_types": [m.value for m in d.meal_types]}
                               for d in accepted]}

        return self.jobs.submit("wymyslanie", f"Nowe dania: {request[:60]}", run)

    def generate(self, *, wishes: str | None = None, days: list[int] | None = None, avoid_recent: bool = True,
                 use_llm: bool = True, week_start: date | None = None, seed: int | None = None,
                 channel: str = "aplikacja", invent: bool = False, invent_count: int = 9) -> Job:
        wishes = (wishes or "").strip()[:1000] or None
        ds = [Weekday(d) for d in sorted(set(days))] if days else list(Weekday)
        start = week_start or default_week_start()
        if start.weekday() != 0:
            # dni planu liczymy od poniedziałku — inna data dawałaby złe daty i etykiety dni w całym planie
            monday = start - timedelta(days=start.weekday())
            raise UserError(f"Początek tygodnia musi być poniedziałkiem (np. {monday:%d.%m.%Y}).")
        seed = seed if seed is not None else random.randint(1, 1_000_000)
        conflicts = self.repo.check().errors
        if conflicts:
            raise UserError("Popraw cele i preferencje przed generowaniem: " + "; ".join(conflicts[:4]))

        def run(progress: Callable[[str], None]) -> dict:
            fresh: set[str] = set()
            wish_plan = None
            llm_first = self.llm() if use_llm else None
            if wishes and llm_first is not None:
                wish_plan = self._resolve_wishes(llm_first, wishes, progress)
            if invent:
                if llm_first is None:
                    progress("Wymyślanie nowych dań wymaga modelu językowego — układam z biblioteki")
                else:
                    request = wishes or "wymyśl zróżnicowane, nowe dania na cały tydzień"
                    chosen = [r.request.opis for r in (wish_plan.requested if wish_plan else []) if r.dish_id]
                    if chosen:  # dania z życzeń już są w bibliotece — nie wymyślamy ich drugi raz
                        request += "\n(Te dania z prośby są już wybrane, NIE wymyślaj ich: " + "; ".join(chosen) + ")"
                    fresh = {d.id for d in self._invent(llm_first, request, invent_count, progress)}
            repo = self.repo
            avoid: set[str] = set()
            if avoid_recent:
                avoid, weeks = self.recent_dishes(repo)
                if avoid:
                    progress(f"Pomijam dania z ostatnich tygodni ({weeks} {plural_pl(weeks, 'plan', 'plany', 'planów')}, "
                             f"{len(avoid)} dań), żeby jedzenie się nie powtarzało")
            llm = self.llm() if use_llm else None
            if use_llm and llm is None:
                progress(f"Model językowy niedostępny ({self._llm_error}) — ułożę tydzień algorytmem")
            planner = WeekPlanner(repo, llm, self.settings, cache=self.engine_cache)
            out = planner.plan_week(ds, wishes, seed, avoid, progress, prefer=fresh, wish_plan=wish_plan)
            if out.plan is None or out.composition is None:
                msgs = [b.message for b in (out.engine.blocks if out.engine else []) if b.message]
                raise UserError("Silnik nie znalazł planu. " + "; ".join(msgs[:3]))
            comp = out.composition.fixed_to_chosen(out.chosen())
            composition = {**comp.to_dict(), "wykluczone_produkty": sorted(out.banned_products),
                           "kuchnie_liczby": out.cuisine_counts, "dania_z_zyczen": out.requested}
            rec = PlanRecord(plan=out.plan, title=week_title(start, ds), source=comp.source, ok=out.ok,
                             report=out.report, composition=composition, log=out.log, wishes=wishes,
                             week_start=start, llm_cost_usd=out.llm_cost_usd, seconds=out.seconds,
                             assumptions_hash=assumptions_hash(repo), snapshot=capture(repo, out.plan))
            self.store.save(rec)
            self.store.set_setting("active_plan", rec.id)
            progress(f"Gotowe: {rec.title} ({'plan poprawny' if rec.ok else 'plan z uwagami'})")
            return {"plan_id": rec.id, "ok": rec.ok, "new_dishes": sorted(fresh)}

        return self.jobs.submit("generowanie", "Nowy plan tygodnia", run)

    def plans(self) -> list[dict]:
        active = self.store.active()
        records = self.store.list()
        if active and all(r.id != active.id for r in records):
            records.append(active)
        return [{**self.checked_record(r).summary(), "active": bool(active and r.id == active.id)}
                for r in records]

    def plan(self, plan_id: str) -> dict:
        rec = self._record(plan_id)
        snapshot = rec.snapshot or self.store.snapshot(rec.id)
        view = plan_view(rec, self.record_repo(rec))
        view["assumptions_changed"] = not matches_current(snapshot, self.repo, rec.plan) if snapshot else False
        view["snapshot_legacy"] = bool(snapshot and snapshot.get("legacy"))
        view["snapshot_date"] = snapshot.get("captured_at") if snapshot else None
        active = self.store.active()
        view["active"] = bool(active and active.id == rec.id)
        view["versions"] = [self.checked_record(v).summary() for v in self.store.family(rec.family_id)]
        view["saved_diet"] = self.store.saved_diets().get(rec.id)
        return view

    def library(self) -> list[dict]:
        saved = self.store.saved_diets()
        active = self.store.active()
        return [{**r.summary(), "saved_diet": saved.get(r.id), "active": bool(active and r.id == active.id)}
                for r in self.store.list(limit=-1)]

    def save_diet(self, plan_id: str, title: str) -> dict:
        title = title.strip()
        if not 2 <= len(title) <= 80:
            raise UserError("Nazwa diety musi mieć od 2 do 80 znaków.")
        self._ensure_exists(plan_id)
        self.store.save_diet(plan_id, title)
        return self.store.saved_diets()[plan_id]

    def unsave_diet(self, plan_id: str) -> None:
        self._ensure_exists(plan_id)
        self.store.unsave_diet(plan_id)

    def rate_dish(self, dish_id: str, rating: int) -> dict:
        if dish_id not in self.repo.dishes:
            raise UserError("Nie ma takiego dania.")
        if rating not in (-1, 0, 1):
            raise UserError("Nieznana ocena dania.")
        with self._lock:
            if self.jobs.busy():
                raise UserError("Poczekaj na koniec układania lub zmiany planu, a potem oceń danie.")
            self.store.rate_dish(dish_id, rating)
            self.repo.dish_ratings = self.store.ratings()
            self._pools = None
            self.engine_cache.clear()
        return {"dish_id": dish_id, "rating": rating}

    def active_plan(self) -> PlanRecord | None:
        rec = self.store.active()
        return self.checked_record(rec) if rec else None

    def set_active(self, plan_id: str) -> None:
        self._record(plan_id)
        self.store.set_setting("active_plan", plan_id)

    def rename(self, plan_id: str, title: str) -> None:
        title = title.strip()
        if not 2 <= len(title) <= 80:
            raise UserError("Nazwa planu musi mieć od 2 do 80 znaków.")
        self._record(plan_id)
        self.store.rename(plan_id, title)

    def delete(self, plan_id: str) -> None:
        if plan_id in self.store.saved_diets():
            raise UserError("Ta wersja jest w Moich dietach. Najpierw usuń ją z zapisanych — nie kasuje to historii.")
        self._record(plan_id)
        self.store.delete(plan_id)

    def _slot(self, rec: PlanRecord, slot_key: str) -> tuple[Composition, SlotPlan]:
        comp = composition_from_plan(rec.plan, rec.plan.comment, rec.source)
        slot = next((s for s in comp.slots if s.key == slot_key), None)
        if slot is None:
            raise UserError("Nie ma takiego posiłku w planie.")
        return comp, slot

    def _slot_pool(self, slot: SlotPlan) -> list[str]:
        pools, a = self.pools(), self.repo.assumptions
        if slot.meal is MealType.SNACK:
            return list(pools.treat_snacks)
        for idx, rule in enumerate(a.week.batch):
            if rule.meal == slot.meal and [int(d) for d in rule.days] == [int(d) for d in slot.days]:
                return list(pools.batch.get(idx, []))
        return list(pools.by_meal.get(slot.meal, []))

    def swap_options(self, plan_id: str, slot_key: str) -> dict:
        rec = self._record(plan_id)
        comp, slot = self._slot(rec, slot_key)
        dishes, pools = self.repo.dishes, self.pools()
        used = {s.dish_id for s in comp.slots}
        current = dishes.get(slot.dish_id)
        options = []
        for did in self._slot_pool(slot):
            if did == slot.dish_id:
                continue
            d = dishes[did]
            notes = []
            if did in used:
                notes.append("już jest w tym tygodniu")
            if current and d.is_treat != current.is_treat:
                notes.append("dodaje posiłek z puli 20%" if d.is_treat else "zabiera posiłek z puli 20%")
            if current and slot.meal is MealType.SUPPER and d.is_sweet != current.is_sweet:
                notes.append("dodaje słodką kolację" if d.is_sweet else "zabiera słodką kolację")
            view = dish_view(d, self.repo.products, pools.profiles.get(did))
            options.append({"id": did, "name": d.name, "description": d.description, "cuisine": cuisine_label(d.cuisine),
                            "tags": d.tags, "prep_minutes": d.prep_minutes, "profile": view["profile"], "notes": notes})
        options.sort(key=lambda o: (len(o["notes"]), self.repo.dish_ratings.get(o["id"], 0) != 1, normalize(o["name"])))
        return {"slot_key": slot_key, "meal": slot.meal.value, "meal_label": MEAL_LABELS_PL[slot.meal],
                "days": [DAY_LABELS_PL[d] for d in slot.days],
                "current": {"id": slot.dish_id, "name": current.name if current else slot.dish_id},
                "options": options}

    def _resolve_block(self, rec: PlanRecord, comp: Composition, slot: SlotPlan,
                       progress: Callable[[str], None]) -> tuple[WeekPlan, object, BlockStatus]:
        """Przelicza tylko dni powiązane ze zmienionym posiłkiem — reszta tygodnia zostaje co do grama."""
        repo = self.repo.for_planning()
        block_days = {int(d) for d in slot.days}
        changed = True
        while changed:  # dni połączone daniami na zapas tworzą jeden blok
            changed = False
            for s in comp.slots:
                ds = {int(d) for d in s.days}
                if ds & block_days and not ds <= block_days:
                    block_days |= ds
                    changed = True
        block = [s for s in comp.slots if {int(d) for d in s.days} & block_days]
        req = EngineRequest.model_validate({"slots": [{"days": [int(d) for d in s.days], "meal": s.meal.value,
                                                       "candidates": [s.dish_id]} for s in block]})
        dishes = {**repo.approved_dishes(), **{s.dish_id: repo.dishes[s.dish_id] for s in comp.slots
                                               if s.dish_id in repo.dishes}}
        progress("Silnik liczy gramatury: " + ", ".join(DAY_LABELS_PL[Weekday(d)].lower() for d in sorted(block_days)))
        settings = self.settings.model_copy(update={"liked_dishes": {d for d, r in repo.dish_ratings.items() if r > 0}})
        res = solve_week(req, repo.assumptions, repo.products, dishes, settings, cache=self.engine_cache)
        if res.plan is None:
            msgs = [b.message for b in res.blocks if b.message]
            raise UserError("Silnik nie znalazł rozwiązania dla tego dnia. " + "; ".join(msgs[:2]))
        plan = rec.plan.model_copy(deep=True)
        order = {m: i for i, m in enumerate(repo.assumptions.day.meals)}
        person_order = {p.id: i for i, p in enumerate(repo.assumptions.persons)}
        plan.meals = sorted([m for m in plan.meals if int(m.day) not in block_days] + res.plan.meals,
                            key=lambda m: (int(m.day), order.get(m.meal, 9)))
        plan.snacks = sorted([s for s in plan.snacks if int(s.day) not in block_days] + res.plan.snacks,
                             key=lambda s: (int(s.day), person_order.get(s.person_id, 9)))
        plan.created_at, plan.engine, plan.input_hash = res.plan.created_at, res.plan.engine, None
        plan.comment = rec.plan.comment
        engine_totals = [t.model_dump(mode="json") for b in res.blocks for t in b.totals]
        # Pozostałe dni mogą zawierać wcześniej ocenione danie/przekąskę. Ocena blokuje nowe
        # propozycje, ale nie unieważnia historycznych porcji, których tutaj nie zmieniamy.
        report = verify_plan(plan, self.repo.assumptions, repo.products, self.repo.dishes, engine_totals,
                             group_tolerance=self.settings.group_ratio_tolerance)
        return plan, report, res.status

    def swap(self, plan_id: str, slot_key: str, dish_id: str) -> Job:
        rec = self._record(plan_id)
        snapshot = rec.snapshot or self.store.snapshot(rec.id)
        if snapshot and not matches_current(snapshot, self.repo, rec.plan):
            raise UserError("Cele lub baza zmieniły się od zapisu tej diety. Najpierw utwórz nową wersję przyciskiem Przelicz; oryginał pozostanie bez zmian.")
        comp, slot = self._slot(rec, slot_key)
        dish = self.repo.dishes.get(dish_id)
        if dish is None:
            raise UserError("Nie ma takiego dania w bibliotece.")
        if dish_id not in self._slot_pool(slot):
            reasons = self.pools().excluded.get(dish_id) or ["danie nie pasuje do tego posiłku"]
            raise UserError(f"„{dish.name}” nie może trafić do tego posiłku: {'; '.join(reasons)}.")
        old = self.repo.dishes.get(slot.dish_id)
        note = (f"{', '.join(DAY_LABELS_PL[d] for d in slot.days)}, {MEAL_LABELS_PL[slot.meal].lower()}: "
                f"{old.name if old else slot.dish_id} → {dish.name}")

        def run(progress: Callable[[str], None]) -> dict:
            log = LiveLog(progress)
            log.append(f"Zamiana: {note}")
            new_comp = apply_swaps(comp, {slot_key: dish_id})
            rules = [self.humanize(r) for r in validate_composition(new_comp, self.pools(), self.repo.dishes,
                                                                    self.repo.assumptions, rec.plan.days)]
            for r in rules:
                log.append(f"Uwaga do zasad tygodnia: {r}")
            new_slot = next(s for s in new_comp.slots if s.key == slot_key)
            plan, report, status = self._resolve_block(rec, new_comp, new_slot, log.append)
            ok = bool(report.ok and status is BlockStatus.OK)
            log.append("Weryfikator: plan poprawny" if report.ok else f"Weryfikator: błędy ({len(report.errors())})")
            self._ensure_exists(rec.id)
            new = PlanRecord(plan=plan, title=rec.title, source="zamiana", ok=ok, report=report,
                             composition={**composition_extras(rec.composition), **new_comp.to_dict()}, log=list(log),
                             wishes=rec.wishes,
                             week_start=rec.week_start, change_note=note, parent_id=rec.id,
                             # pozostałe dni pochodzą z poprzedniej wersji — jeśli liczono je według innych założeń, plan dalej
                             # wymaga przeliczenia (dziedziczymy znacznik założeń rodzica)
                             assumptions_hash=rec.assumptions_hash or assumptions_hash(self.repo),
                             snapshot=capture(self.repo, plan))
            self.store.save(new)
            if self.store.active() is None or self.store.get_setting("active_plan") in (None, rec.id):
                self.store.set_setting("active_plan", new.id)
            return {"plan_id": new.id, "ok": ok, "rule_warnings": rules}

        return self.jobs.submit("zamiana", note, run)

    def recompute(self, plan_id: str, repair: bool = False) -> Job:
        """Przelicza plan po zmianie założeń (te same dania); z naprawą — może wymienić dania, które nie pasują."""
        rec = self._record(plan_id)

        def run(progress: Callable[[str], None]) -> dict:
            repo = self.repo
            log = LiveLog(progress)
            comp = composition_from_plan(rec.plan, rec.plan.comment, rec.source)
            extras = composition_extras(rec.composition)
            planner = WeekPlanner(repo, self.llm() if repair else None, self.settings, cache=self.engine_cache)
            # naprawa dobiera zamienniki z pul bez produktów wykluczonych życzeniami tego planu („bez ryb”)
            out = planner.finish(comp, log, repair=repair, wishes=rec.wishes,
                                 extra_banned=set(extras.get("wykluczone_produkty", [])),
                                 cuisine_counts=extras.get("kuchnie_liczby") or None)
            if out.plan is None or out.composition is None:
                raise UserError("Silnik nie znalazł planu po zmianie założeń.")
            new_comp = out.composition.fixed_to_chosen(out.chosen())
            self._ensure_exists(rec.id)
            new = PlanRecord(plan=out.plan, title=rec.title, source="naprawa" if repair else "przeliczenie",
                             ok=out.ok, report=out.report, composition={**extras, **new_comp.to_dict()}, log=out.log,
                             wishes=rec.wishes, week_start=rec.week_start, parent_id=rec.id,
                             change_note="przeliczone według aktualnych założeń" + (" z naprawą" if repair else ""),
                             llm_cost_usd=out.llm_cost_usd, seconds=out.seconds, assumptions_hash=assumptions_hash(repo),
                             snapshot=capture(repo, out.plan))
            self.store.save(new)
            if self.store.get_setting("active_plan") in (None, rec.id):
                self.store.set_setting("active_plan", new.id)
            return {"plan_id": new.id, "ok": new.ok}

        return self.jobs.submit("przeliczenie", f"Przeliczenie: {rec.title}", run)

    def shopping(self, plan_id: str) -> dict:
        rec = self._record(plan_id)
        repo = self.record_repo(rec)
        rows = build_shopping_list(rec.plan, repo.assumptions, repo.products, repo.store_products)
        groups: list[dict] = []
        for r in rows:
            if not groups or groups[-1]["category"] != r.group:
                groups.append({"category": r.group, "label": r.group_label, "items": []})
            groups[-1]["items"].append(r.to_dict())
        return {"plan_id": rec.id, "title": rec.title, "days": len(rec.plan.days),
                "persons": len(repo.assumptions.persons), "groups": groups}

    def export(self, plan_id: str) -> dict:
        rec = self._record(plan_id)
        repo = self.record_repo(rec)
        report = rec.report
        plan_md = render_plan(rec.plan, repo.assumptions, repo.products, repo.dishes, report, title=rec.title)
        shop_md = render_shopping_list(rec.plan, repo.assumptions, repo.products, repo.store_products)
        out = self.paths.out
        out.mkdir(parents=True, exist_ok=True)
        stem = slug(rec.title) or "plan"
        plan_path = out / f"jadlospis_{stem}_v{rec.version}.md"
        shop_path = out / f"zakupy_{stem}_v{rec.version}.md"
        plan_path.write_text(plan_md, encoding="utf-8-sig")
        shop_path.write_text(shop_md, encoding="utf-8-sig")
        return {"plan_path": str(plan_path), "shopping_path": str(shop_path), "plan_md": plan_md,
                "shopping_md": shop_md}

    def text_export(self, plan_id: str, kind: str) -> dict:
        """Treść całego tygodnia albo zakupów do zapisania w wybranym przez użytkownika miejscu."""
        rec = self._record(plan_id)
        repo = self.record_repo(rec)
        stem = slug(rec.title) or "plan"
        if kind == "plan":
            report = rec.report
            content = render_plan(rec.plan, repo.assumptions, repo.products, repo.dishes, report, title=rec.title)
            filename = f"jadlospis_{stem}_v{rec.version}.txt"
        elif kind == "shopping":
            content = render_shopping_list(rec.plan, repo.assumptions, repo.products, repo.store_products)
            filename = f"lista_zakupow_{stem}_v{rec.version}.txt"
        else:
            raise UserError("Nieznany rodzaj pliku do zapisania.")
        return {"filename": filename, "content": markdown_to_text(content)}

    # ------------------------------------------------------------------ założenia
    def assumptions(self) -> dict:
        repo = self.repo
        raw = to_plain(load_yaml(self.paths.assumptions))
        snack_ids = [d.id for d in repo.dishes.values() if MealType.SNACK in d.meal_types]
        return {
            "data": raw,
            "warnings": repo.assumptions.consistency_warnings(),
            "snack_options": [{"id": i, "name": repo.dishes[i].name,
                               "diets": [d for d in ("tradycyjna", "wege", "wegan") if dish_compatible(repo.dishes[i], repo.products, d)]}
                              for i in sorted(snack_ids)],
            "products": [{"id": p.id, "name": p.name, "diets": [d for d in ("tradycyjna", "wege", "wegan") if compatible(p, d)]}
                         for p in sorted(repo.products.values(), key=lambda p: normalize(p.name))],
            "categories": [{"id": c.value, "name": CATEGORY_LABELS_PL[c]} for c in Category],
            "cuisines": sorted({d.cuisine for d in repo.dishes.values()} | set(repo.assumptions.cuisines)),
            "cuisine_labels": {c: cuisine_label(c) for c in {d.cuisine for d in repo.dishes.values()}
                               | set(repo.assumptions.cuisines)},
            "tags": sorted({t for d in repo.dishes.values() for t in d.tags}),
            "meals": [{"id": m.value, "name": MEAL_LABELS_PL[m]} for m in MealType if m is not MealType.SNACK],
        }

    def validate_assumptions(self, data: dict) -> tuple[Assumptions | None, list[str], list[str]]:
        try:
            a = Assumptions.model_validate(data)
        except ValidationError as exc:
            return None, polish_errors(exc, data), []
        rep = check_consistency(self.repo.products, self.repo.dishes, a)
        return a, rep.errors, rep.warnings

    def save_assumptions(self, data: dict) -> dict:
        a, errors, warnings = self.validate_assumptions(data)
        if errors or a is None:
            return {"ok": False, "errors": errors, "warnings": warnings}
        with self._lock:
            if self.jobs.busy():
                raise UserError("Poczekaj na zakończenie obliczeń przed zmianą celów.")
            doc = load_yaml(self.paths.assumptions)
            doc = merge_yaml(doc, data)
            dump_yaml(doc, self.paths.assumptions)
            self.reload()
        return {"ok": True, "errors": [], "warnings": warnings + self.repo.assumptions.consistency_warnings()}

    # ------------------------------------------------------------------ dania
    def dishes(self, meal: str | None = None, q: str | None = None, status: str | None = None) -> list[dict]:
        pools = self.pools()
        in_pool = {d for ids in pools.by_meal.values() for d in ids} | {d for ids in pools.batch.values() for d in ids}
        out = []
        needle = normalize(q) if q else ""
        for d in sorted(self.repo.dishes.values(), key=lambda d: normalize(d.name)):
            if meal and meal not in [m.value for m in d.meal_types]:
                continue
            if status and d.status.value != status:
                continue
            if needle:
                hay = normalize(" ".join([d.name, d.description or "", d.cuisine, " ".join(d.tags),
                                          " ".join(self.repo.products[i.product_id].name for i in d.ingredients
                                                   if i.product_id in self.repo.products)]))
                if not all(w in hay for w in needle.split()):
                    continue
            prof = pools.profiles.get(d.id)
            is_snack = MealType.SNACK in d.meal_types
            available = (d.id in self.repo.assumptions.day.snacks.allowed) if is_snack else d.id in in_pool
            available = available and self.repo.dish_ratings.get(d.id, 0) >= 0 and dish_compatible(d, self.repo.products, self.repo.assumptions.diet)
            out.append({"id": d.id, "name": d.name, "description": d.description,
                        "meal_types": [m.value for m in d.meal_types], "cuisine": cuisine_label(d.cuisine), "tags": d.tags,
                        "has_groups": bool(d.groups),
                        "diets": [diet for diet in ("wege", "wegan") if dish_compatible(d, self.repo.products, diet)],
                        "status": d.status.value, "prep_minutes": d.prep_minutes, "in_pool": available,
                        "invented": (d.source or "").startswith("wymyślone"),
                        "excluded": pools.excluded.get(d.id, []),
                        "protein_label": prof.protein_label if prof else None,
                        "energy_label": prof.energy_label if prof else None})
        return out

    def dish(self, dish_id: str) -> dict:
        d = self.repo.dishes.get(dish_id)
        if d is None:
            raise UserError("Nie ma takiego dania.")
        pools = self.pools()
        view = dish_view(d, self.repo.products, pools.profiles.get(dish_id))
        view["excluded"] = pools.excluded.get(dish_id, [])
        view["person_names"] = {p.id: p.name for p in self.repo.assumptions.persons}
        return view

    def set_dish_status(self, dish_id: str, status: str) -> dict:
        try:
            new_status = DishStatus(status)
        except ValueError:
            raise UserError("Nieznany status dania (dozwolone: szkic, zatwierdzone, odrzucone).") from None
        with self._lock:
            for path in sorted(self.paths.dishes_dir.glob("*.yaml")):
                doc = load_yaml(path) or []
                for entry in doc:
                    if isinstance(entry, dict) and entry.get("id") == dish_id:
                        entry["status"] = new_status.value
                        dump_yaml(doc, path)
                        self.reload()
                        return self.dish(dish_id)
        raise UserError("Nie znaleziono dania w plikach biblioteki.")

    # ------------------------------------------------------------------ produkty
    def products(self, q: str | None = None, category: str | None = None) -> list[dict]:
        needle = normalize(q) if q else ""
        out = []
        for p in sorted(self.repo.products.values(), key=lambda p: normalize(p.name)):
            if category and p.category.value != category:
                continue
            if needle and not all(w in normalize(" ".join([p.name, *p.aliases])) for w in needle.split()):
                continue
            v = product_view(p)
            out.append({k: v[k] for k in ("id", "name", "category", "category_label", "per_100g", "negligible")}
                       | {"source": p.source.database.value})
        return out

    def product(self, product_id: str) -> dict:
        p = self.repo.products.get(product_id)
        if p is None:
            raise UserError("Nie ma takiego produktu.")
        view = product_view(p)
        view["used_in"] = [{"id": d.id, "name": d.name} for d in self.repo.dishes.values()
                           if any(i.product_id == product_id for i in d.ingredients)]
        view["stores"] = [s.model_dump(mode="json", by_alias=True, exclude_none=True)
                          for s in self.repo.store_products if s.product_id == product_id]
        return view

    def match_product(self, text: str) -> dict:
        r = ProductMatcher(self.repo.products).match(text)
        return {"query": text, "status": r.status.value, "product_id": r.product_id,
                "candidates": [{"product_id": c.product_id, "name": c.name, "score": c.score, "via": c.via}
                               for c in r.candidates], "warnings": r.warnings}

    def add_product(self, payload: dict) -> dict:
        """Nowy produkt przepisany z etykiety (+ opcjonalnie produkt z półki). Zapis do dane/*/wlasne.yaml."""
        name = str(payload.get("name", "")).strip()
        if len(name) < 2:
            raise UserError("Podaj nazwę produktu.")
        pid = slug(name)
        if not pid:
            raise UserError("Nazwa produktu musi zawierać litery.")
        if pid in self.repo.products:
            raise UserError(f"Produkt o takiej nazwie już jest w bazie ({self.repo.products[pid].name}).")
        raw_aliases = payload.get("aliases", "")
        if not isinstance(raw_aliases, list):
            raw_aliases = str(raw_aliases or "").split(",")
        aliases = [str(a).strip() for a in raw_aliases if str(a).strip()]
        per100 = {"kcal": payload.get("kcal"), "bialko": payload.get("protein"), "tluszcz": payload.get("fat"),
                  "weglowodany": payload.get("carbs")}
        for key, alias in (("fiber", "blonnik"), ("sugars", "cukry"), ("salt", "sol"), ("saturated_fat", "kwasy_nasycone")):
            if payload.get(key) not in (None, ""):
                per100[alias] = payload[key]
        store_name = str(payload.get("store", "")).strip()
        brand = str(payload.get("brand", "")).strip()
        ean = str(payload.get("ean", "")).strip()
        note = "przepisane z etykiety" + (f": {brand}" if brand else "") + (f", {store_name}" if store_name else "")
        entry = {"id": pid, "nazwa": name, "aliasy": aliases, "kategoria": payload.get("category"),
                 "stan": payload.get("state") or State.READY.value, "na_100g": per100,
                 "zrodlo": {"baza": "etykieta", **({"kod": ean} if ean else {}), "uwagi": note,
                            "pobrano": date.today().isoformat()},
                 "krok_g": payload.get("step_g") or 5}
        entry["zgodnosc_diety"] = payload.get("dietary_class") or "nieznany"
        if payload.get("notes"):
            entry["uwagi"] = str(payload["notes"])
        try:
            product = Product.model_validate(entry)
        except ValidationError as exc:
            raise UserError("Popraw dane produktu: " + "; ".join(polish_errors(exc))) from None
        mismatch = product.per_100g.kcal_mismatch()
        if mismatch > 0.08 and not payload.get("confirm_mismatch"):
            computed = product.per_100g.label_energy_from_macros()
            raise UserError(f"Kalorie z etykiety ({product.per_100g.kcal:.0f} kcal) nie zgadzają się z makro "
                            f"(wychodzi {computed:.0f} kcal, różnica {mismatch:.0%}). Sprawdź przepisanie albo zaznacz "
                            "„wartości są zgodne z etykietą”.")
        products = {**self.repo.products, pid: product}
        rep = check_consistency(products, self.repo.dishes, self.repo.assumptions)
        alias_errors = [e for e in rep.errors if pid in e]
        if alias_errors:
            raise UserError("; ".join(alias_errors))
        store_entry = None
        if store_name:
            package = payload.get("package_g")
            store_entry = {"id": slug(f"{store_name} {brand} {name}")[:60], "produkt": pid, "sklep": store_name,
                           "nazwa_w_sklepie": str(payload.get("store_name_local") or name), "preferowany": True,
                           "sprawdzono": date.today().isoformat()}
            if brand:
                store_entry["marka"] = brand
            if ean:
                store_entry["ean"] = ean
            if package not in (None, ""):
                store_entry["opakowanie_g"] = package
            try:
                StoreProduct.model_validate(store_entry)
            except ValidationError as exc:
                raise UserError("Popraw dane sklepu: " + "; ".join(polish_errors(exc))) from None
        with self._lock:
            self._append_yaml(self.paths.products_dir / OWN_PRODUCTS_FILE, product.to_data())
            if store_entry:
                for s in self.repo.store_products:  # nowy produkt z półki staje się preferowanym
                    if s.product_id == pid and s.preferred:
                        store_entry["preferowany"] = False
                self._append_yaml(self.paths.stores_dir / OWN_PRODUCTS_FILE, store_entry)
            self.reload()
        return self.product(pid)

    @staticmethod
    def _append_yaml(path: Path, entry: dict) -> None:
        doc = load_yaml(path) if path.exists() else None
        if doc is None:
            doc = CommentedSeq()
        doc.append(entry)
        dump_yaml(doc, path)

    # ------------------------------------------------------------------ koszty
    def costs(self) -> dict:
        settings = LLMSettings.load(self.paths)
        ledger = CostLedger(self.paths.out / "llm_koszty.jsonl", settings)
        entries = []
        if ledger.path.exists():
            import json

            for line in ledger.path.read_text(encoding="utf-8").splitlines():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        by_task: dict[str, dict] = {}
        for e in entries:
            name = str(e.get("zadanie", "?"))
            name = "generowanie_dan" if name.startswith("generacja_dan") else name
            t = by_task.setdefault(name, {"task": name, "calls": 0, "cost_usd": 0.0, "failed": 0})
            t["calls"] += 1
            t["cost_usd"] += float(e.get("koszt_usd", 0.0))
            t["failed"] += 0 if e.get("ok") else 1
        return {"spent_usd": round(ledger.spent_usd(), 4), "budget_usd": settings.budget_usd, "model": settings.model,
                "by_task": sorted(by_task.values(), key=lambda t: -t["cost_usd"]), "recent": entries[-30:][::-1]}

    # ------------------------------------------------------------------ dni
    def today_index(self, rec: PlanRecord, today: date | None = None) -> int | None:
        """Który dzień planu jest dziś (według daty początku tygodnia); None, gdy plan nie obejmuje dzisiaj."""
        today = today or date.today()
        if rec.week_start is None:
            return today.weekday() if Weekday(today.weekday()) in rec.plan.days else None
        delta = (today - rec.week_start).days
        return delta if 0 <= delta < 7 and Weekday(delta) in rec.plan.days else None
