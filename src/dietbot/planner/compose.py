"""Układanie tygodnia: wybór dań i dań zapasowych na każdy posiłek (LLM albo algorytm zapasowy) + walidacja zasad."""

from __future__ import annotations

import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from ..domain.assumptions import DAY_KEYS_PL, DAY_LABELS_PL, Assumptions, Weekday
from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType, cuisine_label
from ..textfmt import plural_pl
from .balance import BALANCE_OK_PP, day_deviation
from .pools import Pools

NUMBER_WORDS = {"jeden", "jedna", "jedno", "jednym", "dwa", "dwie", "dwóch", "dwoma", "dwukrotnie", "trzy", "trzech",
                "trzema", "trzykrotnie", "cztery", "czterech", "pięć", "pięciu", "sześć", "sześciu", "siedem", "siedmiu",
                "raz", "razy", "kilka", "kilku", "para", "pary", "połowa", "połowie"}
MAX_ALTERNATES = 2
MAX_EGG_MEALS = 5
DAY_KEY_BY_DAY = {v: k for k, v in DAY_KEYS_PL.items()}


@dataclass
class SlotPlan:
    days: list[Weekday]
    meal: MealType
    dish_id: str
    alternates: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return "-".join(str(int(d)) for d in self.days) + f"_{self.meal.value}"


@dataclass
class Composition:
    slots: list[SlotPlan]
    comment: str | None = None
    source: str = "algorytm"

    def slot(self, day: Weekday, meal: MealType) -> SlotPlan | None:
        for s in self.slots:
            if day in s.days and s.meal == meal:
                return s
        return None

    def to_engine_slots(self) -> list[dict]:
        return [{"days": [int(d) for d in s.days], "meal": s.meal.value, "candidates": [s.dish_id, *s.alternates]}
                for s in self.slots]

    def to_dict(self) -> dict:
        return {"sloty": [{"dni": [int(d) for d in s.days], "posilek": s.meal.value, "danie": s.dish_id,
                           "zapasowe": list(s.alternates)} for s in self.slots],
                "komentarz": self.comment, "zrodlo": self.source}

    @classmethod
    def from_dict(cls, data: Mapping) -> Composition:
        slots = [SlotPlan(days=[Weekday(int(d)) for d in s["dni"]], meal=MealType(s["posilek"]), dish_id=s["danie"],
                          alternates=list(s.get("zapasowe", []))) for s in data["sloty"]]
        return cls(slots=slots, comment=data.get("komentarz"), source=data.get("zrodlo", "algorytm"))

    def fixed_to_chosen(self, chosen: Mapping[str, str]) -> Composition:
        """Kopia z daniami wybranymi przez silnik (klucz slotu -> danie) i bez zapasowych."""
        return Composition(slots=[SlotPlan(days=list(s.days), meal=s.meal, dish_id=chosen.get(s.key, s.dish_id))
                                  for s in self.slots], comment=self.comment, source=self.source)


def slot_layout(a: Assumptions, days: list[Weekday]) -> list[tuple[list[Weekday], MealType, int | None]]:
    """Lista slotów tygodnia: sloty wielodniowe z zasad na zapas + pozostałe posiłki. Trzeci element: indeks zasady."""
    taken: set[tuple[int, MealType]] = set()
    layout: list[tuple[list[Weekday], MealType, int | None]] = []
    for idx, rule in enumerate(a.week.batch):
        if all(d in days for d in rule.days):
            layout.append((list(rule.days), rule.meal, idx))
            taken |= {(int(d), rule.meal) for d in rule.days}
    for day in days:
        for meal in a.day.meals:
            if (int(day), meal) not in taken:
                layout.append(([day], meal, None))
    layout.sort(key=lambda x: (int(x[0][0]), a.day.meals.index(x[1])))
    return layout


def validate_composition(comp: Composition, pools: Pools, dishes: Mapping[str, DishTemplate], a: Assumptions,
                         days: list[Weekday]) -> list[str]:
    """Błędy zasad tygodnia (po polsku). Pusta lista = poprawnie."""
    errors: list[str] = []
    layout = slot_layout(a, days)
    expected = {("-".join(str(int(d)) for d in ds) + f"_{m.value}"): (ds, m, idx) for ds, m, idx in layout}
    got = {s.key: s for s in comp.slots}
    for key, (ds, meal, idx) in expected.items():
        label = f"{', '.join(DAY_LABELS_PL[d] for d in ds)} — {MEAL_LABELS_PL[meal].lower()}"
        slot = got.get(key)
        if slot is None:
            errors.append(f"{label}: brak dania")
            continue
        pool = pools.batch.get(idx, []) if idx is not None else pools.by_meal.get(meal, [])
        if slot.dish_id not in pool:
            why = "; ".join(pools.excluded.get(slot.dish_id, [])) or "danie spoza listy dozwolonej na ten posiłek"
            errors.append(f"{label}: '{slot.dish_id}' nie może być użyte ({why})")
    for key, slot in got.items():
        if key in expected:
            continue
        if slot.meal is MealType.SNACK and len(slot.days) == 1 and slot.days[0] in days:
            if slot.dish_id not in pools.treat_snacks:
                errors.append(f"{DAY_LABELS_PL[slot.days[0]]} — przekąska: '{slot.dish_id}' nie jest przekąską z puli 20% "
                              "z listy")
            continue
        errors.append(f"nieoczekiwany slot '{key}' (sprawdź dni i posiłek)")

    first = [s.dish_id for s in comp.slots]
    dup = {d for d in first if first.count(d) > 1}
    if dup and a.week.no_repeats:
        errors.append(f"powtórzone dania: {', '.join(sorted(dup))} (każde danie maks. raz w tygodniu)")
    treat_days = sum(len(s.days) for s in comp.slots if s.dish_id in dishes and dishes[s.dish_id].is_treat)
    band = a.week.treat_meals
    if len(days) == 7 and not (band.min <= treat_days <= band.max):
        errors.append(f"posiłków z puli 20% jest {treat_days}, a ma być {band.min:.0f}–{band.max:.0f}")
    sweet = sum(1 for s in comp.slots if s.meal is MealType.SUPPER and s.dish_id in dishes and dishes[s.dish_id].is_sweet)
    if sweet > a.week.sweet_suppers_max:
        errors.append(f"słodkich kolacji jest {sweet}, a maksimum to {a.week.sweet_suppers_max}")
    eggs = [s.dish_id for s in comp.slots if s.dish_id in pools.profiles and pools.profiles[s.dish_id].with_eggs]
    if len(eggs) > MAX_EGG_MEALS:
        errors.append(f"jajka są w {len(eggs)} posiłkach ({', '.join(eggs)}), maksimum {MAX_EGG_MEALS}")
    egg_days: dict[int, list[str]] = {}
    for s in comp.slots:
        prof = pools.profiles.get(s.dish_id)
        if prof and prof.with_eggs:
            for d in s.days:
                egg_days.setdefault(int(d), []).append(s.dish_id)
    for day, ids in sorted(egg_days.items()):
        if len(ids) > 1:
            errors.append(f"{DAY_LABELS_PL[Weekday(day)]}: jajka w więcej niż jednym posiłku ({', '.join(ids)}) — "
                          "najwyżej jeden posiłek z jajkami dziennie")
    treat_by_day: dict[int, list[str]] = {}
    for s in comp.slots:
        if s.dish_id in dishes and dishes[s.dish_id].is_treat:
            for d in s.days:
                treat_by_day.setdefault(int(d), []).append(s.dish_id)
    for day, ids in sorted(treat_by_day.items()):
        if len(ids) > 1:
            errors.append(f"{DAY_LABELS_PL[Weekday(day)]}: więcej niż jeden posiłek z puli 20% ({', '.join(ids)}) — "
                          "rozłóż je na różne dni")
    return errors


def slot_pool(slot: SlotPlan, pools: Pools, a: Assumptions, days: list[Weekday]) -> list[str]:
    if slot.meal is MealType.SNACK:
        return pools.treat_snacks
    for ds, meal, idx in slot_layout(a, days):
        if idx is not None and meal is slot.meal and [int(d) for d in ds] == [int(d) for d in slot.days]:
            return pools.batch.get(idx, [])
    return pools.by_meal.get(slot.meal, [])


def wish_errors(comp: Composition, pools: Pools, dishes: Mapping[str, DishTemplate], a: Assumptions,
                days: list[Weekday], cuisine_counts: Mapping[str, int] | None = None,
                must_use: set[str] | None = None, pins: Mapping[str, list[Weekday]] | None = None) -> list[str]:
    """Policzalne życzenia na ten tydzień: dokładna liczba dań danej kuchni, nowe dania i dania z życzeń, które mają
    się znaleźć w planie, oraz dni, w które domownicy chcą dane z życzeń („w piątek pizza”)."""
    errors: list[str] = []
    for cuisine, want in sorted((cuisine_counts or {}).items()):
        have = sum(1 for s in comp.slots if s.dish_id in dishes and dishes[s.dish_id].cuisine == cuisine)
        if have != want:
            errors.append(f"kuchnia {cuisine_label(cuisine)}: {have} {plural_pl(have, 'danie', 'dania', 'dań')} w tygodniu, "
                          f"a w życzeniach domowników {want}")
    must_use = must_use or set()
    used = {s.dish_id for s in comp.slots}
    for d in sorted(must_use - used):
        # wymagamy tylko wtedy, gdy jest wolne miejsce: pasujący posiłek zajęty przez danie spoza nowych
        # (przekąska z puli 20% ma zawsze miejsce — to dodatkowy slot dnia)
        if d in pools.treat_snacks or any(s.dish_id not in must_use and d in slot_pool(s, pools, a, days)
                                          for s in comp.slots):
            name = dishes[d].name if d in dishes else d
            kind = "danie z życzeń domowników" if d in (pins or {}) else "nowe danie"
            errors.append(f"{kind} „{name}” ({d}) nie trafiło do tygodnia — wstaw je do pasującego posiłku")
    for d, want_days in sorted((pins or {}).items()):
        slots = [s for s in comp.slots if s.dish_id == d]
        if not want_days or not slots or any(len(s.days) > 1 for s in slots):
            continue  # dowolny dzień, danie jeszcze nie w planie (błąd wyżej) albo danie na zapas (dni ze zasad)
        have = {int(x) for s in slots for x in s.days}
        if int(want_days[0]) not in have:
            name = dishes[d].name if d in dishes else d
            errors.append(f"danie z życzeń „{name}” ({d}) ma być w dniu: {DAY_LABELS_PL[want_days[0]].lower()}")
    return errors


def feasible_cuisine_counts(counts: Mapping[str, int], pools: Pools, dishes: Mapping[str, DishTemplate],
                            a: Assumptions, days: list[Weekday],
                            must_use: set[str] | None = None) -> tuple[dict[str, int], list[str]]:
    """Ogranicza liczby z życzeń do tego, co da się ułożyć (np. „7 razy indyjska” przy 5 obiadach indyjskich).

    Liczymy osobno każdy posiłek: dań danej kuchni w puli posiłku nie może być więcej niż slotów tego posiłku.
    Nowe dania wymyślone na prośbę mają pierwszeństwo — liczba nie spada poniżej liczby nowych dań tej kuchni.
    """
    layout = slot_layout(a, days)
    out: dict[str, int] = {}
    notes: list[str] = []
    for cuisine, want in counts.items():
        cap = 0
        for meal in a.day.meals:
            single = sum(1 for _, m, idx in layout if m is meal and idx is None)
            cap += min(single, sum(1 for d in pools.by_meal.get(meal, []) if d in dishes and dishes[d].cuisine == cuisine))
        for _ds, _, idx in layout:
            if idx is not None and any(d in dishes and dishes[d].cuisine == cuisine for d in pools.batch.get(idx, [])):
                cap += 1
        new = sum(1 for d in (must_use or ()) if d in dishes and d in pools.profiles and dishes[d].cuisine == cuisine)
        value = max(min(want, cap), new)
        if value < want:
            notes.append(f"kuchnia {cuisine_label(cuisine)}: w bibliotece za mało pasujących dań na {want} — planuję {value}")
        elif value > want:
            notes.append(f"kuchnia {cuisine_label(cuisine)}: nowych dań tej kuchni jest {new} — planuję {value}")
        out[cuisine] = value
    return out, notes


def limit_new_dishes_to_counts(prefer: set[str], counts: Mapping[str, int],
                               dishes: Mapping[str, DishTemplate]) -> tuple[set[str], list[str]]:
    """Nowe dania kuchni, o której liczbę prosili domownicy („włoska 2 razy, resztę wymyśl”), są wymagane w planie
    najwyżej w tej liczbie — pozostałe zostają w bibliotece. Inaczej trzy wymyślone dania włoskie łamałyby życzenie
    (test na żywo 14.09.2026: dwa plany z trzema daniami włoskimi). Najpierw po jednym daniu na posiłek."""
    out: set[str] = set()
    by_cuisine: dict[str, list[str]] = {}
    for d in sorted(prefer):
        cuisine = dishes[d].cuisine if d in dishes else None
        if cuisine in counts:
            by_cuisine.setdefault(cuisine, []).append(d)
        else:
            out.add(d)
    notes: list[str] = []
    for cuisine, ids in by_cuisine.items():
        want = counts[cuisine]
        ids.sort(key=lambda d: (dishes[d].meal_types[0].value if dishes[d].meal_types else "", d))
        picked: list[str] = []
        for d in ids:  # różne posiłki najpierw
            if len(picked) < want and all(dishes[d].meal_types[:1] != dishes[p].meal_types[:1] for p in picked):
                picked.append(d)
        picked += [d for d in ids if d not in picked][:max(0, want - len(picked))]
        out.update(picked)
        if len(ids) > len(picked):
            notes.append(f"Nowych dań kuchni {cuisine_label(cuisine)} jest {len(ids)}, a w życzeniach {want} — do planu "
                         f"trafi {len(picked)}, pozostałe zostają w bibliotece")
    return out, notes


def clean_alternates(comp: Composition, pools: Pools, dishes: Mapping[str, DishTemplate], a: Assumptions) -> list[str]:
    """Usuwa zapasowe, które mogłyby złamać zasady tygodnia (inny status 20%/słodkie, powtórki). Zwraca notatki."""
    notes: list[str] = []
    used = {s.dish_id for s in comp.slots}
    seen_alt: set[str] = set()
    batch_idx = {tuple(int(d) for d in r.days) + (r.meal.value,): i for i, r in enumerate(a.week.batch)}
    for s in comp.slots:
        idx = batch_idx.get(tuple(int(d) for d in s.days) + (s.meal.value,))
        pool = set(pools.batch.get(idx, [])) if idx is not None else set(pools.by_meal.get(s.meal, []))
        main = dishes.get(s.dish_id)
        keep: list[str] = []
        for alt in s.alternates:
            d = dishes.get(alt)
            reason = None
            if alt not in pool:
                reason = "spoza puli"
            elif alt in used or alt in seen_alt:
                reason = "używane gdzie indziej"
            elif main and (d.is_treat != main.is_treat or (s.meal is MealType.SUPPER and d.is_sweet != main.is_sweet)):
                reason = "inny status puli 20%/słodkiej kolacji"
            if reason:
                notes.append(f"{s.key}: pominięto zapasowe '{alt}' ({reason})")
                continue
            keep.append(alt)
            seen_alt.add(alt)
            if len(keep) >= MAX_ALTERNATES:
                break
        s.alternates = keep
    return notes


def snack_kind(dish_id: str) -> str:
    """Rodzaj przekąski z puli 20% z identyfikatora: przekaska20_lody_waniliowe → lody."""
    return dish_id.removeprefix("przekaska20_").split("_")[0]


def vary_treat_snacks(comp: Composition, pools: Pools, dishes: Mapping[str, DishTemplate],
                      protected: set[str] | None = None, seed: int = 1,
                      cuisine_counts: Mapping[str, int] | None = None) -> list[str]:
    """Przekąski z puli 20% w tygodniu różnego rodzaju (lody i popcorn, a nie dwa razy lody): powtórzony rodzaj
    zamieniamy na nieużyty rodzaj w tym samym dniu — pula 20% i dni się nie zmieniają. Przekąski z życzeń zostają,
    a liczby kuchni z życzeń też (zamiana w obrębie tej samej kuchni albo poza kuchniami z życzeń)."""
    protected = protected or set()
    notes: list[str] = []
    snacks = sorted((s for s in comp.slots if s.meal is MealType.SNACK), key=lambda s: (s.dish_id not in protected,
                                                                                         int(s.days[0])))
    kinds = {snack_kind(s.dish_id) for s in snacks if s.dish_id in protected}
    used = {s.dish_id for s in comp.slots}
    rng = random.Random(seed)
    for s in snacks:
        kind = snack_kind(s.dish_id)
        if kind not in kinds or s.dish_id in protected:
            kinds.add(kind)
            continue
        counts = cuisine_counts or {}
        cur = dishes[s.dish_id].cuisine if s.dish_id in dishes else None
        options = [d for d in pools.treat_snacks if d not in used and snack_kind(d) not in kinds and d in dishes
                   and (dishes[d].cuisine == cur or (dishes[d].cuisine not in counts and cur not in counts))]
        if not options:
            continue
        new = rng.choices(options, weights=[3 if d in pools.liked else 1 for d in options])[0]
        name = dishes[s.dish_id].name if s.dish_id in dishes else s.dish_id
        notes.append(f"{DAY_LABELS_PL[s.days[0]].lower()}, przekąska: {name} → {dishes[new].name} "
                     "(inny rodzaj przekąski niż w pozostałe dni)")
        used.add(new)
        s.dish_id, s.alternates = new, []
        kinds.add(snack_kind(new))
    return notes


# ---------------------------------------------------------------------------------------------- lista dań dla modelu
PROMPT_DISHES_PER_MEAL = 50  # przy ~500 daniach cała lista to ok. 40 tys. tokenów — model wybiera gorzej i wolniej
PROMPT_TREATS_PER_MEAL = 8
PROMPT_SWEET_SUPPERS = 6
PROMPT_BATCH = 12
WISH_STOPWORDS = {"chce", "chcemy", "chcialbym", "zeby", "wiecej", "mniej", "tydzien", "tygodniu", "razy", "danie", "dania",
                  "kuchnia", "kuchni", "kuchnie", "jakies", "jakis", "prosze", "resztę", "reszte", "wymysl", "nowe", "nowych",
                  "obiad", "obiady", "kolacja", "kolacje", "sniadanie", "sniadania", "dzien", "dni", "tego", "taki", "takie",
                  "bardzo", "troche", "lekkie", "zwykle"}


def wish_focus(wishes: str | None, pools: Pools, dishes: Mapping[str, DishTemplate], limit: int = 12,
               skip_cuisines: bool = False) -> set[str]:
    """Dania, których nazwa pasuje do słów z życzeń („w piątek pizza” → pizze) — zawsze trafiają na listę dla modelu.
    Słowa po „bez” pomijamy (to wykluczenia, obsługiwane osobno). `skip_cuisines` pomija nazwy kuchni („włoska 2 razy”
    pilnują liczby kuchni) — inaczej np. „Sałatka włoska z pesto” byłaby chroniona przed zamianą tylko przez nazwę."""
    from ..domain.dish import CUISINE_LABELS_PL
    from ..textnorm import normalize

    if not wishes:
        return set()

    def stem(w: str) -> str:
        return w[:4] if len(w) <= 5 else w[:5]

    tokens = normalize(wishes).split()
    words = {t for i, t in enumerate(tokens) if len(t) >= 4 and t not in WISH_STOPWORDS
             and not (i > 0 and tokens[i - 1] == "bez")}
    stems = {stem(w) for w in words}
    if skip_cuisines:
        stems -= {stem(normalize(x)) for key, label in CUISINE_LABELS_PL.items() for x in (key, label)}
    out: set[str] = set()
    for ids in [*pools.by_meal.values(), *pools.batch.values()]:
        hits = [d for d in ids if d in dishes and any(s in normalize(dishes[d].name) for s in stems)]
        out.update(sorted(hits)[:limit])
    return out


def prompt_pools(pools: Pools, dishes: Mapping[str, DishTemplate], seed: int, focus: set[str] | None = None,
                 cuisines: set[str] | None = None, per_meal: int = PROMPT_DISHES_PER_MEAL) -> Pools:
    """Zróżnicowana próbka puli do promptu: proporcjonalnie do pierwiastka z liczby dań kuchni (małe kuchnie też
    widać), z minimalną liczbą posiłków z puli 20% i słodkich kolacji. Dania z `focus` (nowe, z życzeń) i kuchnie
    z życzeń są zawsze na liście. Walidacja i autokorekta nadal używają pełnych pul."""
    import math

    rnd = random.Random(seed)
    focus = focus or set()
    cuisines = cuisines or set()

    def sample(ids: list[str], k: int, quotas: list[tuple[callable, int]]) -> list[str]:
        if len(ids) <= k:
            return list(ids)
        chosen = {d for d in ids if d in focus or dishes[d].cuisine in cuisines}
        rest = [d for d in ids if d not in chosen]
        rnd.shuffle(rest)
        rest.sort(key=lambda d: d not in pools.liked)
        for pred, n in quotas:
            have = sum(1 for d in chosen if pred(d))
            for d in [d for d in rest if pred(d)][:max(0, n - have)]:
                chosen.add(d)
        rest = [d for d in rest if d not in chosen]
        free = k - len(chosen)
        if free > 0:
            by_cuisine: dict[str, list[str]] = {}
            for d in rest:
                by_cuisine.setdefault(dishes[d].cuisine, []).append(d)
            weights = {c: math.sqrt(len(v)) for c, v in by_cuisine.items()}
            total = sum(weights.values()) or 1.0
            for c, v in sorted(by_cuisine.items()):
                chosen.update(v[:max(1, round(free * weights[c] / total))])
        return sorted(chosen, key=lambda d: ids.index(d))

    by_meal = {}
    for meal, ids in pools.by_meal.items():
        quotas = [(lambda d: dishes[d].is_treat, PROMPT_TREATS_PER_MEAL)] if meal is not MealType.BREAKFAST else []
        if meal is MealType.SUPPER:
            quotas.append((lambda d: dishes[d].is_sweet, PROMPT_SWEET_SUPPERS))
        by_meal[meal] = sample(ids, per_meal, quotas)
    batch = {idx: sample(ids, PROMPT_BATCH, []) for idx, ids in pools.batch.items()}
    return Pools(by_meal=by_meal, batch=batch, profiles=pools.profiles, excluded=pools.excluded, target=pools.target,
                 treat_snacks=list(pools.treat_snacks), liked=pools.liked)


# ---------------------------------------------------------------------------------------------- algorytm zapasowy
def compose_fallback(pools: Pools, dishes: Mapping[str, DishTemplate], a: Assumptions, days: list[Weekday],
                     seed: int = 1, fixed: Mapping[str, str] | None = None) -> Composition:
    """Deterministyczne ułożenie tygodnia bez LLM: zasady tygodnia + różnorodność źródeł białka i kuchni."""
    rng = random.Random(seed)
    layout = slot_layout(a, days)
    fixed = dict(fixed or {})
    chosen: dict[str, SlotPlan] = {}
    used: set[str] = set()

    def key_of(ds: list[Weekday], meal: MealType) -> str:
        return "-".join(str(int(d)) for d in ds) + f"_{meal.value}"

    # 1. zasady na zapas
    for ds, meal, idx in layout:
        if idx is None:
            continue
        options = [d for d in pools.batch.get(idx, []) if d not in used]
        if not options:
            continue
        pick = fixed.get(key_of(ds, meal)) or rng.choices(options, weights=[3 if d in pools.liked else 1 for d in options])[0]
        chosen[key_of(ds, meal)] = SlotPlan(days=ds, meal=meal, dish_id=pick)
        used.add(pick)

    single = [(ds, meal) for ds, meal, idx in layout if idx is None]
    # 2. posiłki z puli 20%: minimum z założeń, na obiad/kolację, w różne dni (najlepiej weekend)
    treat_target = int(a.week.treat_meals.min) if len(days) == 7 else 0
    treat_slots: list[tuple[list[Weekday], MealType]] = []
    pref = sorted([s for s in single if s[1] in (MealType.LUNCH, MealType.SUPPER)],
                  key=lambda s: (-(int(s[0][0]) >= 4), rng.random()))
    treat_left = {m: sum(1 for d in pools.by_meal.get(m, []) if dishes[d].is_treat and d not in used)
                  for m in (MealType.LUNCH, MealType.SUPPER)}
    for ds, meal in pref:
        if len(treat_slots) >= treat_target:
            break
        if any(ds[0] == t[0][0] for t in treat_slots):
            continue
        if treat_left.get(meal, 0) > 0:  # liczymy dostępne dania z puli 20% dla tego posiłku
            treat_slots.append((ds, meal))
            treat_left[meal] -= 1
    # 3. słodkie kolacje: maks. połowa limitu, nie w dni z posiłkiem 20%
    sweet_target = min(a.week.sweet_suppers_max, 1)
    sweet_slots: list[tuple[list[Weekday], MealType]] = []
    for ds, meal in sorted(single, key=lambda s: rng.random()):
        if meal is not MealType.SUPPER or len(sweet_slots) >= sweet_target:
            continue
        if any(t[0][0] == ds[0] for t in treat_slots):
            continue
        if any(dishes[d].is_sweet for d in pools.by_meal.get(meal, []) if d not in used):
            sweet_slots.append((ds, meal))

    def score(dish_id: str, ds: list[Weekday], meal: MealType) -> float:
        prof = pools.profiles[dish_id]
        sc = rng.random() + (0.65 if dish_id in pools.liked else 0)
        day = int(ds[0])
        for other in chosen.values():
            od = int(other.days[0])
            op = pools.profiles.get(other.dish_id)
            if op is None:
                continue
            same_source = bool(set(op.protein_sources) & set(prof.protein_sources))
            if od == day and same_source:
                sc -= 1.5
            if abs(od - day) == 1 and other.meal == meal and same_source:
                sc -= 0.8
            if od == day and op.cuisine == prof.cuisine:
                sc -= 0.3
        # bilans makro dnia: dania już wybrane na ten dzień + kandydat (patrz planner/balance.py)
        same_day = [pools.profiles[s.dish_id].typical for s in chosen.values()
                    if day in {int(d) for d in s.days} and s.dish_id in pools.profiles]
        if same_day:
            dev = day_deviation([*same_day, prof.typical], pools.target)
            sc -= max(0.0, dev - BALANCE_OK_PP) * 0.12
        eggs = [s for s in chosen.values() if pools.profiles.get(s.dish_id) and pools.profiles[s.dish_id].with_eggs]
        if prof.with_eggs and (len(eggs) >= MAX_EGG_MEALS or any(day in {int(d) for d in s.days} for s in eggs)):
            sc -= 5
        return sc

    order = sorted(single, key=lambda s: (len(pools.by_meal.get(s[1], [])), int(s[0][0])))
    for ds, meal in order:
        k = key_of(ds, meal)
        if k in fixed:
            chosen[k] = SlotPlan(days=ds, meal=meal, dish_id=fixed[k])
            used.add(fixed[k])
            continue
        want_treat = (ds, meal) in treat_slots
        want_sweet = (ds, meal) in sweet_slots
        options = [d for d in pools.by_meal.get(meal, []) if d not in used
                   and dishes[d].is_treat == want_treat and (meal is not MealType.SUPPER or dishes[d].is_sweet == want_sweet)]
        if not options:  # brak dania o żądanym statusie — bierzemy zwykłe
            options = [d for d in pools.by_meal.get(meal, []) if d not in used and not dishes[d].is_treat
                       and not (meal is MealType.SUPPER and dishes[d].is_sweet)]
        if not options:
            options = [d for d in pools.by_meal.get(meal, []) if not dishes[d].is_treat]
        if not options:
            continue
        pick = max(options, key=lambda d: score(d, ds, meal))
        chosen[k] = SlotPlan(days=ds, meal=meal, dish_id=pick)
        used.add(pick)

    # 4. zapasowe: z tej samej puli, ten sam status, nieużywane
    alt_used: set[str] = set()
    for s in sorted(chosen.values(), key=lambda s: (int(s.days[0]), s.meal.value)):
        if len(s.days) > 1:
            continue
        main = dishes[s.dish_id]
        opts = [d for d in pools.by_meal.get(s.meal, []) if d not in used and d not in alt_used
                and dishes[d].is_treat == main.is_treat and (s.meal is not MealType.SUPPER or dishes[d].is_sweet == main.is_sweet)]
        rng.shuffle(opts)
        s.alternates = opts[:MAX_ALTERNATES]
        alt_used |= set(s.alternates)

    slots = [chosen[key_of(ds, m)] for ds, m, _ in layout if key_of(ds, m) in chosen]
    # 5. przekąska z puli 20% (lody, chipsy…) w dniu bez posiłku z puli, gdy pula tygodnia ma jeszcze miejsce
    treats = sum(len(s.days) for s in slots if dishes[s.dish_id].is_treat) if slots else 0
    if len(days) == 7 and pools.treat_snacks and treats < a.week.treat_meals.max:
        treat_days = {int(d) for s in slots if dishes[s.dish_id].is_treat for d in s.days}
        free = [d for d in (5, 6, 4, 3, 2, 1, 0) if d not in treat_days and Weekday(d) in days]
        if free:
            slots.append(SlotPlan(days=[Weekday(free[rng.randrange(min(2, len(free)))])], meal=MealType.SNACK,
                                  dish_id=rng.choices(pools.treat_snacks,
                                                      weights=[3 if d in pools.liked else 1 for d in pools.treat_snacks])[0]))
    return Composition(slots=slots, source="algorytm")


# ---------------------------------------------------------------------------------------------- LLM
class MealChoice(BaseModel):
    danie: str
    zapasowe: list[str] = Field(default_factory=list, max_length=MAX_ALTERNATES)


class DayChoice(BaseModel):
    dzien: str = Field(description="poniedzialek | wtorek | sroda | czwartek | piatek | sobota | niedziela")
    sniadanie: MealChoice
    obiad: MealChoice
    kolacja: MealChoice
    przekaska_20: str | None = Field(default=None, description="id przekąski z puli 20% (lody, chipsy…) albo null")


class WeekChoice(BaseModel):
    dni: list[DayChoice]
    komentarz: str = Field(description="2–3 zdania po polsku o charakterze tygodnia; BEZ żadnych liczb i wyliczeń")

    @field_validator("komentarz")
    @classmethod
    def _no_numbers(cls, v: str) -> str:
        return without_numbers(v)


def without_numbers(text: str) -> str:
    """Liczby podaje program: zdania komentarza z liczbami albo liczebnikami („raz”, „kilka”) wypadają. Wcześniej taki
    komentarz odrzucał całą odpowiedź — model powtarzał to samo zdanie we wszystkich próbach i tydzień układał
    algorytm zapasowy (test na żywo 14.09.2026: 2 z 4 planów)."""
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    kept = [s for s in sentences if not any(ch.isdigit() for ch in s)
            and not set(re.findall(r"[a-ząćęłńóśźż]+", s.lower())) & NUMBER_WORDS]
    return " ".join(kept)


def composition_from_llm(choice: WeekChoice, a: Assumptions, days: list[Weekday]) -> Composition:
    layout = slot_layout(a, days)
    by_day: dict[int, DayChoice] = {}
    for d in choice.dni:
        key = d.dzien.strip().lower()
        if key in DAY_KEYS_PL:
            by_day[int(DAY_KEYS_PL[key])] = d
    slots: list[SlotPlan] = []
    for ds, meal, _ in layout:
        dc = by_day.get(int(ds[0]))
        if dc is None:
            continue
        mc: MealChoice = getattr(dc, meal.value)
        slots.append(SlotPlan(days=ds, meal=meal, dish_id=mc.danie.strip(), alternates=[x.strip() for x in mc.zapasowe]))
    for day in days:
        dc = by_day.get(int(day))
        snack = (dc.przekaska_20 or "").strip() if dc else ""
        if snack and snack.lower() not in ("null", "brak", "none", "-"):
            slots.append(SlotPlan(days=[day], meal=MealType.SNACK, dish_id=snack))
    return Composition(slots=slots, comment=choice.komentarz or None, source="llm")


def build_compose_prompt(pools: Pools, a: Assumptions, days: list[Weekday], wishes: str | None,
                         avoid: set[str] | None = None, prefer: set[str] | None = None,
                         cuisine_counts: Mapping[str, int] | None = None,
                         pins: Mapping[str, list[Weekday]] | None = None,
                         hints: Sequence[str] | None = None) -> str:
    """`hints` — stałe wskazówki domowników z notatnika preferencji (miękkie, jak życzenia na każdy tydzień)."""
    meal_blocks = []
    for meal in a.day.meals:
        lines = [pools.profiles[d].line() for d in pools.by_meal.get(meal, [])]
        meal_blocks.append(f"### {MEAL_LABELS_PL[meal]} — dozwolone dania ({len(lines)}):\n" + "\n".join(lines))
    batch_blocks = []
    for idx, rule in enumerate(a.week.batch):
        if not all(d in days for d in rule.days):
            continue
        lines = [pools.profiles[d].line() for d in pools.batch.get(idx, [])]
        dni = ", ".join(DAY_LABELS_PL[d] for d in rule.days)
        batch_blocks.append(f"### {MEAL_LABELS_PL[rule.meal]} na zapas ({dni}) — to samo danie w te dni:\n"
                            + "\n".join(lines))
    persons = "; ".join(f"{p.name}: {p.targets.kcal.min:.0f}–{p.targets.kcal.max:.0f} kcal" for p in a.persons)
    rules = [
        f"Plan na dni: {', '.join(DAY_LABELS_PL[d] for d in days)}. Posiłki: {', '.join(MEAL_LABELS_PL[m].lower() for m in a.day.meals)}.",
        f"Obie osoby jedzą te same dania (gramatury dobierze program). Cele: {persons}.",
        f"Pula 20% — WYMAGANE: łącznie od {a.week.treat_meals.min:.0f} do {a.week.treat_meals.max:.0f} w tygodniu (nie mniej), każda w inny dzień. "
        "Liczą się posiłki z tagiem pula_20 i przekąski z puli 20% (lody, chipsy, popcorn, batonik) wpisane w pole przekaska_20 "
        "dnia — przekąska jest dodatkiem do trzech posiłków (program dopasuje gramatury dnia); w pozostałe dni przekaska_20: null.",
        f"Słodkie kolacje (tag slodkie): maksymalnie {a.week.sweet_suppers_max} w tygodniu.",
        "Każde danie najwyżej raz w tygodniu (poza daniem na zapas, które zajmuje kilka dni).",
        "Kolacja lżejsza niż obiad. Dbaj o różnorodność: różne źródła białka w ciągu dnia i w kolejnych dniach, różne kuchnie.",
        f"Dania z jajkami (oznaczone „z jajkami” albo „główne białko: jajka”) najwyżej w {MAX_EGG_MEALS} posiłkach tygodnia.",
        "Najwyżej jedna rzecz z puli 20% dziennie (posiłek albo przekąska) i najwyżej jeden posiłek z jajkami dziennie.",
        "Przekąski z puli 20% w tygodniu różnego rodzaju (np. lody i popcorn, a nie dwa razy lody).",
        "Równoważ dzień: nie układaj trzech posiłków o niskim białku ani trzech bardzo lekkich naraz.",
        "Bilans makro dnia: najwyżej jedno danie „tłuste” dziennie; do dania „mało węgli” dobieraj w tym samym dniu "
        "dania bez tego znacznika (najlepiej „dużo węgli”). Program dobierze gramatury, ale nie zmieni natury dania — "
        "dzień z samych tłustych dań nie zmieści się w celach.",
        f"Do każdego posiłku podaj 0–{MAX_ALTERNATES} dania zapasowe z tej samej listy (nieużyte gdzie indziej, ten sam status pula_20/slodkie) — program użyje ich, jeśli pierwszy wybór nie zmieści się w celach.",
        "Używaj WYŁĄCZNIE identyfikatorów dań z list poniżej. Nie podawaj kalorii ani gramatur.",
        "Dania oznaczone ULUBIONE proponuj częściej, ale zachowaj różnorodność i wszystkie zasady. "
        "Ocena jest preferencją, nie nakazem umieszczenia dania w każdym tygodniu.",
    ]
    text = "Zasady:\n- " + "\n- ".join(rules)
    if hints:
        text += ("\n\nSTAŁE PREFERENCJE DOMOWNIKÓW z ich notatnika (uwzględniaj w każdym tygodniu, jeśli nie łamią "
                 "zasad; gramatury i tak dobiera program):\n- " + "\n- ".join(hints))
    if wishes:
        text += f"\n\nŻyczenia domowników na ten tydzień (uwzględnij, jeśli nie łamią zasad): {wishes}"
    if cuisine_counts:
        text += ("\n\nLiczba dań danej kuchni w całym tygodniu (z życzeń) — DOKŁADNIE tyle, licząc śniadania, obiady "
                 "i kolacje; pozostałe dania wybierz z innych kuchni: "
                 + ", ".join(f"{cuisine_label(c)} (kuchnia: {c}) — {n}" for c, n in sorted(cuisine_counts.items())))
    if avoid:
        text += f"\n\nUnikaj dań z ostatnich tygodni: {', '.join(sorted(avoid))}"
    pins = pins or {}
    wanted = sorted(d for d in pins if d in pools.profiles)
    if wanted:
        def where(d: str) -> str:
            parts = ["przekąska — pole przekaska_20"] if d in pools.treat_snacks else []
            if pins[d]:
                parts.append(", ".join(DAY_LABELS_PL[x].lower() for x in pins[d]))
            return f" ({'; '.join(parts)})" if parts else ""

        text += ("\n\nDANIA Z ŻYCZEŃ DOMOWNIKÓW — wybrane z biblioteki, MUSZĄ być w tygodniu (każde raz; danie na zapas "
                 "w dni zupy): " + ", ".join(d + where(d) for d in wanted))
    fresh = sorted(d for d in (prefer or ()) if d in pools.profiles and d not in pins)
    if fresh:
        text += ("\n\nNOWE dania wymyślone na prośbę domowników — wykorzystaj je w tym tygodniu (każde raz, "
                 f"w pasującym posiłku): {', '.join(fresh)}")
    snack_block = []
    if pools.treat_snacks:
        lines = [pools.profiles[d].line() for d in pools.treat_snacks if d in pools.profiles]
        snack_block.append("### Przekąski z puli 20% — pole przekaska_20 (najwyżej jedna dziennie, liczy się do puli 20%):\n"
                           + "\n".join(lines))
    return text + "\n\n" + "\n\n".join(batch_blocks + meal_blocks + snack_block)


COMPOSE_SYSTEM = """Jesteś doświadczonym dietetykiem i kucharzem. Układasz tygodniowy jadłospis dla dwóch osób,
wybierając dania z przygotowanej listy. Liczby (gramatury, kalorie) liczy osobny program — Ty dbasz o to,
żeby tydzień był smaczny, różnorodny, praktyczny w przygotowaniu i zgodny z zasadami."""
