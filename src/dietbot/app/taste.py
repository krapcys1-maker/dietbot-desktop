"""Propozycje dań w guście domowników: Porcja uczy się z tego, co robią, i proponuje, co jeszcze mogą polubić.

1. Kod zbiera sygnały (jak analiza nawyków w notatniku): oceny dań, ręczne zamiany w planach, uwagi, życzenia,
   prośby z czatu i wcześniej odrzucone propozycje.
2. Kod wybiera kandydatów z biblioteki: dania zgodne z dietą i zasadami, których domownicy jeszcze nie jedli,
   uszeregowane podobieństwem składników i kuchni do dań lubianych i wybieranych.
3. Model (DeepSeek) streszcza gust i wybiera propozycje WYŁĄCZNIE spośród kandydatów; może też podsunąć pomysły na
   nowe dania — te powstają dopiero na prośbę, przez generator z kontrolą kodu, silnika i kucharza.
Liczb nie podaje model. Danie wybrane „do następnego planu” trafia do najbliższego układania tygodnia.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator

from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType, Role, cuisine_label
from ..llm.client import LLMError
from ..llm.scope import check_cancelled
from ..textnorm import normalize
from ..textnumbers import has_digits, without_numbers
from .jobs import Job, UserError

if TYPE_CHECKING:
    from .service import DietService

TASTE_KEY = "gust"
MEALS = (MealType.BREAKFAST, MealType.LUNCH, MealType.SUPPER)
CANDIDATES_PER_MEAL = 12
MAX_PROPOSALS = 6
MAX_IDEAS = 3
MAIN_MIN_G = 30  # składnik, po którym rozpoznaje się danie (nie przyprawa ani łyżka oleju)
CHAT_MESSAGES = 25
SAME_DISH_SHARE = 0.85  # pomysł = danie z biblioteki, gdy tyle rdzeni słów jednej nazwy jest w drugiej

SYSTEM = """Jesteś doradcą kulinarnym w polskiej aplikacji Porcja, która układa tygodniowy jadłospis dla domowników.
Program podaje sygnały o ich gustach (oceny dań, zamiany w planach — co odrzucili, co wybrali — uwagi, życzenia,
prośby z czatu, wcześniej odrzucone propozycje) oraz listę KANDYDATÓW z biblioteki: dania zgodne z ich dietą
i zasadami, których jeszcze nie jedli.
Odpowiedz w JSON:
- "podsumowanie": dwa–trzy zdania po polsku, co lubią, a czego nie. Siła wniosku ma odpowiadać liczbie sygnałów:
  przy niewielu sygnałach pisz ostrożnie („na razie widać…”). Bez cyfr i liczebników.
- "lubia" i "nie_lubia": krótkie hasła (np. „makarony w kremowym sosie”, „kurczak”, „kuchnia azjatycka”), bez cyfr.
- "propozycje": od trzech do sześciu dań WYŁĄCZNIE z listy kandydatów (pole "danie" = id z listy), z różnych posiłków
  i o różnych źródłach białka, takie, które najpewniej im zasmakują. "powod": jedno krótkie zdanie, dlaczego pasuje
  do ich gustu (odwołaj się do tego, co lubią), bez cyfr.
- "nowe_pomysly": do trzech konkretnych dań, których NIE ma na liście, w ich guście i różnych od propozycji: posiłek,
  kuchnia (jedna z listy kuchni), "opis" — nazwa dania z najważniejszymi składnikami (np. „makaron z kurczakiem
  w sosie z suszonych pomidorów”), "powod". Bez produktów, których nie lubią albo których im nie wolno. Nie
  powtarzaj tu dań z sygnałów (np. polubionych) ani z listy kandydatów — tylko dania, których jeszcze nie znają.
Nie podawaj gramatur ani kalorii — liczy je program."""


class Proposal(BaseModel):
    danie: str = Field(max_length=80)
    powod: str = Field(default="", max_length=300)


class Idea(BaseModel):
    posilek: Literal["sniadanie", "obiad", "kolacja"]
    kuchnia: str = Field(max_length=40)
    opis: str = Field(min_length=5, max_length=200)
    powod: str = Field(default="", max_length=300)

    @field_validator("opis")
    @classmethod
    def _no_digits(cls, v: str) -> str:
        if has_digits(v):
            raise ValueError("opis bez cyfr — gramatury policzy program")
        return v


class TasteAnswer(BaseModel):
    podsumowanie: str = Field(default="", max_length=900)
    lubia: list[str] = Field(default_factory=list, max_length=10)
    nie_lubia: list[str] = Field(default_factory=list, max_length=10)
    propozycje: list[Proposal] = Field(default_factory=list, max_length=10)
    nowe_pomysly: list[Idea] = Field(default_factory=list, max_length=6)


# ---------------------------------------------------------------- stan zapisany w bazie (ustawienie „gust”)

def load_state(store) -> dict | None:
    raw = store.get_setting(TASTE_KEY)
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def save_state(store, state: dict) -> None:
    store.set_setting(TASTE_KEY, json.dumps(state, ensure_ascii=False))


def pending_dishes(store) -> list[str]:
    """Dania wybrane „do następnego planu”, jeszcze niewstawione do żadnego planu."""
    state = load_state(store) or {}
    return [p["danie"] for p in state.get("propozycje", []) if p.get("status") == "wybrana"]


def mark_planned(store, dish_ids: set[str]) -> list[str]:
    """Po ułożeniu planu: wybrane propozycje, które do niego trafiły, dostają status „w planie”."""
    state = load_state(store)
    if not state:
        return []
    done = []
    for p in state.get("propozycje", []):
        if p.get("status") == "wybrana" and p["danie"] in dish_ids:
            p["status"] = "w_planie"
            done.append(p["danie"])
    if done:
        save_state(store, state)
    return done


HINT_PREFIX = "Gust domowników"


def taste_hint(store) -> str | None:
    """Wskazówka dla modelu układającego tydzień (miękka, jak wskazówki z notatnika)."""
    state = load_state(store) or {}
    likes, dislikes = state.get("lubia") or [], state.get("nie_lubia") or []
    if not likes and not dislikes:
        return None
    parts = []
    if likes:
        parts.append("lubią: " + ", ".join(likes))
    if dislikes:
        parts.append("raczej nie lubią: " + ", ".join(dislikes))
    return f"{HINT_PREFIX} wyciągnięty z ich ocen i zamian — " + "; ".join(parts)


def refresh_hint(service: DietService) -> None:
    """Nowy opis gustu działa od razu w kolejnym układaniu tygodnia (bez ponownego wczytywania danych)."""
    hints = [h for h in service.repo.preference_hints if not h.startswith(HINT_PREFIX)]
    hint = taste_hint(service.store)
    service.repo.preference_hints = [*hints, hint] if hint else hints


# ---------------------------------------------------------------- kandydaci z biblioteki (bez modelu)

def _stems(text: str) -> set[str]:
    """Rdzenie słów nazwy (cztery litery) — „pasta z jajek” i „pastą jajeczną” mają te same."""
    return {w[:4] for w in normalize(text).replace(",", " ").split() if len(w) >= 3}


def library_match(opis: str, dishes: dict[str, DishTemplate]) -> str | None:
    """Danie z biblioteki, które jest tym pomysłem (model nie widzi całej biblioteki i potrafi „wymyślić” znane danie)."""
    idea = _stems(opis)
    best, best_share = None, 0.0
    for dish in dishes.values():
        name = _stems(dish.name)
        common = len(idea & name)
        if common < 3:
            continue
        share = max(common / len(idea), common / len(name))
        if share > best_share:
            best, best_share = dish.id, share
    return best if best_share >= SAME_DISH_SHARE else None


def main_products(dish: DishTemplate) -> list[str]:
    return [i.product_id for i in dish.ingredients
            if i.role in (Role.PROTEIN, Role.CARBS, Role.VEGETABLES, Role.SAUCE, Role.DAIRY)
            and not i.optional and i.typical_g >= MAIN_MIN_G]


def affinity(dishes: dict[str, DishTemplate], liked: list[str], disliked: list[str],
             swaps: list[tuple[str, str, MealType]], dismissed: list[str]) -> tuple[Counter, Counter]:
    """Wagi produktów i kuchni: plus za dania lubiane i wybierane w zamianach, minus za nielubiane i odrzucane."""
    products, cuisines = Counter(), Counter()

    def add(dish_id: str, w_product: float, w_cuisine: float) -> None:
        dish = dishes.get(dish_id)
        if dish is None:
            return
        for pid in main_products(dish):
            products[pid] += w_product
        cuisines[dish.cuisine] += w_cuisine

    for d in liked:
        add(d, 2, 2)
    for d in disliked:
        add(d, -2, -1)
    for old, new, _ in swaps:
        add(new, 1, 1)
        add(old, -1, -0.5)
    for d in dismissed:
        add(d, -0.5, -0.3)
    return products, cuisines


def candidates(dishes: dict[str, DishTemplate], by_meal: dict[MealType, list[str]], eaten: set[str],
               excluded: set[str], weights: tuple[Counter, Counter], seed: int,
               per_meal: int = CANDIDATES_PER_MEAL) -> list[str]:
    """Dania z pul (dieta, zakazy, zasady), których domownicy jeszcze nie jedli — najbliższe ich gustowi."""
    products, cuisines = weights
    rng = random.Random(seed)
    out: list[str] = []
    for meal in MEALS:
        options = [d for d in by_meal.get(meal, []) if d not in eaten and d not in excluded and d not in out]
        scored = []
        for d in options:
            dish = dishes[d]
            if dish.is_treat:
                continue
            score = sum(products[p] for p in main_products(dish)) + 1.5 * cuisines[dish.cuisine]
            scored.append((score + rng.random() * 0.5, d))  # remis: losowo, żeby lista nie stała w miejscu
        scored.sort(reverse=True)
        out += [d for _, d in scored[:per_meal]]
    return out


# ---------------------------------------------------------------- usługa

class Taste:
    def __init__(self, service: DietService):
        self.s = service

    # ---------------------------------------------------------- odczyt
    def view(self) -> dict:
        s = self.s
        state = load_state(s.store) or {}
        dishes = s.repo.dishes
        proposals = []
        for p in state.get("propozycje", []):
            dish = dishes.get(p["danie"])
            if dish is None:
                continue
            meals = [m for m in MEALS if m in dish.meal_types]
            proposals.append({**p, "nazwa": dish.name, "opis": dish.description,
                              "posilki": [MEAL_LABELS_PL[m] for m in meals], "kuchnia": cuisine_label(dish.cuisine)})
        ideas = [{**i, "posilek_nazwa": MEAL_LABELS_PL[MealType(i["posilek"])],
                  "kuchnia_nazwa": cuisine_label(i["kuchnia"]),
                  "danie_nazwa": dishes[i["danie"]].name if i.get("danie") in dishes else None}
                 for i in state.get("pomysly", [])]
        return {"utworzono": state.get("utworzono"), "podsumowanie": state.get("podsumowanie", ""),
                "lubia": state.get("lubia", []), "nie_lubia": state.get("nie_lubia", []),
                "propozycje": proposals, "pomysly": ideas, "sygnaly": state.get("sygnaly"),
                "koszt_usd": state.get("koszt_usd"), "llm_available": s.llm_ready(),
                "do_planu": [p["nazwa"] for p in proposals if p.get("status") == "wybrana"]}

    def signals(self) -> dict:
        """Sygnały z notatnika (oceny, zamiany, uwagi, życzenia) + prośby z czatu + odrzucone propozycje."""
        from .notebook import Notebook

        s = self.s
        notebook = Notebook(s)
        base = notebook.habit_signals()
        lines, counts = list(base["lines"]), dict(base["counts"])
        asked = []
        for channel in ("aplikacja", "telegram"):
            asked += [m["text"].strip() for m in s.store.messages(channel, limit=80) if m["role"] == "uzytkownik"]
        asked = [t for t in asked if len(t) > 3][-CHAT_MESSAGES:]
        if asked:
            lines.append("Wiadomości domowników w czacie (ostatnie):")
            lines += [f"- {t[:220]}" for t in asked]
        state = load_state(s.store) or {}
        dismissed = [p["danie"] for p in state.get("propozycje", []) if p.get("status") == "odrzucona"]
        dishes = s.repo.dishes
        if dismissed:
            lines.append("Wcześniejsze propozycje, które odrzucili („nie teraz”): "
                         + "; ".join(dishes[d].name for d in dismissed if d in dishes))
        counts["chat"], counts["dismissed"] = len(asked), len(dismissed)
        return {"lines": lines, "counts": counts, "swaps": notebook._swaps(), "dismissed": dismissed}  # noqa: SLF001

    # ---------------------------------------------------------- propozycje (płatne: jedno zapytanie do modelu)
    def refresh(self) -> Job:
        s = self.s
        if not s.llm_ready():
            raise UserError("Propozycje w Waszym guście przygotowuje model językowy — dodaj klucz DeepSeek "
                            "w Ustawieniach.")

        def run(progress: Callable[[str], None]) -> dict:
            llm = s.llm()
            if llm is None:
                raise UserError(f"Propozycje wymagają modelu językowego ({s._llm_error}).")  # noqa: SLF001
            progress("Zbieram, co lubicie: oceny dań, zamiany w planach, uwagi i prośby z czatu…")
            sig = self.signals()
            repo, pools = s.repo, s.pools()
            ratings = repo.dish_ratings
            liked = [d for d, r in ratings.items() if r > 0 and d in repo.dishes]
            disliked = [d for d, r in ratings.items() if r < 0 and d in repo.dishes]
            eaten = {m.dish_id for rec in s.store.list(limit=-1) for m in rec.plan.meals}
            state = load_state(s.store) or {}
            kept = [p for p in state.get("propozycje", []) if p.get("status") in ("wybrana", "odrzucona")]
            weights = affinity(repo.dishes, liked, disliked, sig["swaps"], sig["dismissed"])
            seed = int(datetime.now(UTC).timestamp()) // 3600
            cands = candidates(repo.dishes, pools.by_meal, eaten, set(liked) | {p["danie"] for p in kept}, weights,
                               seed)
            if not cands:
                raise UserError("Nie ma już w bibliotece dań, których nie jedliście — poproś o wymyślenie nowych.")
            progress(f"Kandydaci z biblioteki: {len(cands)} dań, których jeszcze nie jedliście — model wybiera…")
            prompt = self._prompt(sig["lines"], cands)
            try:
                res = llm.complete_json("gust", SYSTEM, prompt, TasteAnswer, temperature=0.4, max_tokens=1600)
            except LLMError as exc:
                raise UserError(f"Nie udało się przygotować propozycji: {exc}") from None
            check_cancelled()
            new_state = self._state_from(res.value, cands, kept, sig["counts"], res.cost_usd)
            save_state(s.store, new_state)
            refresh_hint(s)
            for p in new_state["propozycje"]:
                if p["status"] == "nowa":
                    progress(f"Propozycja: {repo.dishes[p['danie']].name} — {p['powod']}".rstrip(" —"))
            for i in new_state["pomysly"]:
                progress(f"Pomysł na nowe danie ({MEAL_LABELS_PL[MealType(i['posilek'])].lower()}): {i['opis']}")
            progress("Propozycje są w Notatniku preferencji — wybierz, co ma trafić do następnego planu.")
            return {"propozycje": sum(p["status"] == "nowa" for p in new_state["propozycje"]),
                    "pomysly": len(new_state["pomysly"])}

        return s.jobs.submit("gust", "Propozycje dań w Waszym guście", run)

    def _prompt(self, lines: list[str], cands: list[str]) -> str:
        s = self.s
        repo, profiles = s.repo, s.pools().profiles
        a = repo.assumptions
        out = ["DOMOWNICY: " + ", ".join(p.name for p in a.persons), f"DIETA: {a.diet}",
               "KUCHNIE (do pola kuchnia): " + ", ".join(a.cuisines), ""]
        if repo.preference_hints:
            out += ["STAŁE WSKAZÓWKI Z NOTATNIKA:", *[f"- {h}" for h in repo.preference_hints], ""]
        out += ["SYGNAŁY ZEBRANE PRZEZ PROGRAM:", *(lines or ["- na razie brak ocen, zamian i uwag"]), "",
                "KANDYDACI (id | nazwa | posiłki | kuchnia | białko | węgle):"]
        for d in cands:
            dish, prof = repo.dishes[d], profiles.get(d)
            meals = ", ".join(MEAL_LABELS_PL[m].lower() for m in MEALS if m in dish.meal_types)
            protein = ", ".join(prof.protein_sources) if prof else ""
            carbs = ", ".join(prof.carb_sources) if prof else ""
            out.append(f"{d} | {dish.name} | {meals} | {dish.cuisine} | {protein} | {carbs}")
        return "\n".join(out)

    def _state_from(self, answer: TasteAnswer, cands: list[str], kept: list[dict], counts: dict,
                    cost: float) -> dict:
        s = self.s
        allowed = set(cands)
        proposals = [p for p in kept if p["danie"] in s.repo.dishes]
        seen = {p["danie"] for p in proposals}
        fresh = 0
        for p in answer.propozycje:
            if p.danie in allowed and p.danie not in seen and fresh < MAX_PROPOSALS:
                proposals.append({"danie": p.danie, "powod": without_numbers(p.powod).strip(), "status": "nowa",
                                  "dodano": date.today().isoformat()})
                seen.add(p.danie)
                fresh += 1
        from ..planner.wishes import match_cuisine

        cuisines = s.repo.assumptions.cuisines
        ideas = []
        for i in answer.nowe_pomysly[:MAX_IDEAS]:
            known = library_match(i.opis, s.repo.dishes)
            if known is not None:
                # znane danie: jeśli go jeszcze nie jedli, staje się zwykłą propozycją z biblioteki
                if known in allowed and known not in seen:
                    proposals.append({"danie": known, "powod": without_numbers(i.powod).strip(), "status": "nowa",
                                      "dodano": date.today().isoformat()})
                    seen.add(known)
                continue
            cuisine = match_cuisine(i.kuchnia, cuisines) or ("polska" if "polska" in cuisines else cuisines[0])
            ideas.append({"posilek": i.posilek, "kuchnia": cuisine, "opis": i.opis.strip(),
                          "powod": without_numbers(i.powod).strip(), "status": "nowy"})
        plain = [x.strip() for x in answer.lubia if x.strip() and not has_digits(x)][:8]
        dislike = [x.strip() for x in answer.nie_lubia if x.strip() and not has_digits(x)][:8]
        return {"utworzono": datetime.now(UTC).isoformat(), "podsumowanie": without_numbers(answer.podsumowanie),
                "lubia": plain, "nie_lubia": dislike, "propozycje": proposals, "pomysly": ideas,
                "sygnaly": counts, "koszt_usd": round(cost, 5)}

    # ---------------------------------------------------------- decyzje domowników
    def set_status(self, dish_id: str, status: str) -> dict:
        if status not in ("nowa", "wybrana", "odrzucona"):
            raise UserError("Nieznany wybór propozycji.")
        s = self.s
        state = load_state(s.store)
        prop = next((p for p in (state or {}).get("propozycje", []) if p["danie"] == dish_id), None)
        if prop is None:
            raise UserError("Tej propozycji już nie ma — odśwież propozycje.")
        prop["status"] = status
        save_state(s.store, state)
        return self.view()

    def dismiss_idea(self, index: int) -> dict:
        s = self.s
        state = load_state(s.store) or {}
        ideas = state.get("pomysly", [])
        if not 0 <= index < len(ideas):
            raise UserError("Tego pomysłu już nie ma — odśwież propozycje.")
        ideas[index]["status"] = "odrzucony"
        save_state(s.store, state)
        return self.view()

    def invent_idea(self, index: int) -> Job:
        """Pomysł → prawdziwy przepis: generator z kontrolą kodu, silnika i kucharza (jak dania z życzeń)."""
        from ..llm.generate_dishes import GenOrder, run_generation_plan, save_generated
        from ..planner.wishes import request_match_problem

        s = self.s
        state = load_state(s.store) or {}
        ideas = state.get("pomysly", [])
        if not 0 <= index < len(ideas) or ideas[index].get("status") == "wymyslony":
            raise UserError("Tego pomysłu już nie ma albo danie jest już w bibliotece — odśwież propozycje.")
        if not s.llm_ready():
            raise UserError("Wymyślanie dań wymaga klucza DeepSeek w Ustawieniach.")
        idea = dict(ideas[index])

        def run(progress: Callable[[str], None]) -> dict:
            llm = s.llm()
            if llm is None:
                raise UserError(f"Wymyślanie dań wymaga modelu językowego ({s._llm_error}).")  # noqa: SLF001
            meal = MealType(idea["posilek"])
            details = "danie główne na talerzu, NIE zupa" if meal is MealType.LUNCH else ""
            special = "; ".join(x for x in (f"dokładnie to danie, które spodobało się domownikom: {idea['opis']}",
                                            details, "z podstawowych produktów z bazy") if x)
            check = lambda name: request_match_problem(idea["opis"], name)  # noqa: E731
            order = GenOrder(meal, idea["kuchnia"], ask=3, special=special, keep=1, name_check=check)
            progress(f"Model przygotowuje przepis: {idea['opis']}…")
            accepted, _rejected, cost = run_generation_plan(
                s.repo, llm, plan=[order], workers=1, log=progress, review=True, engine_check=True,
                source=f"z propozycji w guście domowników ({date.today():%d.%m.%Y}): {idea['opis'][:120]}")
            accepted = [d for d in accepted if check(d.name) is None]
            current = load_state(s.store) or {}
            target = next((i for i in current.get("pomysly", []) if i["opis"] == idea["opis"]), None)
            if not accepted:
                if target is not None:
                    target["status"] = "nieudany"
                    save_state(s.store, current)
                raise UserError("Żaden przepis nie przeszedł kontroli — spróbuj jeszcze raz albo odśwież propozycje.")
            dish = accepted[0]
            with s._lock:  # noqa: SLF001
                check_cancelled()
                save_generated([dish], s.paths.dishes_dir)
                s.reload()
            if target is not None:
                target.update({"status": "wymyslony", "danie": dish.id})
            current.setdefault("propozycje", []).append({"danie": dish.id, "powod": idea.get("powod", ""),
                                                         "status": "wybrana", "nowe": True,
                                                         "dodano": date.today().isoformat()})
            save_state(s.store, current)
            progress(f"Nowe danie w bibliotece: {dish.name} — trafi do następnego planu.")
            progress(f"Koszt wymyślania: {cost:.4f} $")
            return {"dish_id": dish.id, "name": dish.name}

        return s.jobs.submit("wymyslanie", f"Nowe danie: {idea['opis'][:60]}", run)

