"""Automatyczne poprawki układu tygodnia dla reguł „policzalnych”.

Reguły: pula 20%, słodkie kolacje, jajka, powtórki, a z życzeń domowników — dokładna liczba dań danej kuchni
i nowe dania wymyślone na prośbę (muszą trafić do tygodnia i nie wolno ich wymieniać na inne).

Model językowy dobrze dobiera smaki, ale często myli się w liczeniu (np. daje jeden posiłek z puli 20% zamiast
dwóch). Zamiast kolejnej rundy z modelem kod wymienia najmniejszą liczbę dań na najbardziej podobne z puli —
pozostałe wybory modelu zostają nietknięte. Wynik zawsze sprawdza później `validate_composition` / `wish_errors`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..domain.assumptions import DAY_LABELS_PL, Assumptions, Weekday
from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType, cuisine_label
from .compose import MAX_EGG_MEALS, Composition, SlotPlan, slot_layout
from .pools import Pools

# dni, w które najchętniej dokładamy posiłek z puli 20%: piątek, sobota, niedziela, potem reszta
TREAT_DAY_ORDER = [4, 5, 6, 3, 2, 1, 0]
MAX_ROUNDS = 30


def _similarity(pools: Pools, a_id: str, b_id: str) -> float:
    pa, pb = pools.profiles.get(a_id), pools.profiles.get(b_id)
    if pa is None or pb is None:
        return 0.0
    score = 0.0
    score += 2.0 if pa.cuisine == pb.cuisine else 0.0
    score += 1.5 * len(set(pa.protein_sources) & set(pb.protein_sources))
    score += 1.0 * len(set(pa.carb_sources) & set(pb.carb_sources))
    score += 1.0 if pa.protein_label == pb.protein_label else 0.0
    score += 0.5 if pa.energy_label == pb.energy_label else 0.0
    score -= abs(pa.typical.get("kcal", 0.0) - pb.typical.get("kcal", 0.0)) / 200
    return score


def replace_disallowed(comp: Composition, pools: Pools, dishes: Mapping[str, DishTemplate], a: Assumptions,
                       keep: set[str] | None = None) -> list[str]:
    """Wymienia w miejscu tylko dania, które przestały pasować do swojego posiłku (np. po nowej zasadzie z notatnika:
    zakaz produktu, limit porcji, ocena 👎) — na najbardziej podobne dozwolone danie tego samego rodzaju. Reszty układu
    (także ręcznych zamian domowników) nie rusza. Zwraca notatki po polsku."""
    notes: list[str] = []
    keep = keep or set()
    days = sorted({d for s in comp.slots for d in s.days})
    batch_idx = {"-".join(str(int(d)) for d in ds) + f"_{m.value}": idx
                 for ds, m, idx in slot_layout(a, days) if idx is not None}
    used = {s.dish_id for s in comp.slots}

    def name(d: str) -> str:
        return dishes[d].name if d in dishes else d

    def label(s: SlotPlan) -> str:
        return f"{', '.join(DAY_LABELS_PL[d] for d in s.days).lower()}, {MEAL_LABELS_PL[s.meal].lower()}"

    for slot in list(comp.slots):
        if slot.dish_id in keep:
            continue
        if slot.meal is MealType.SNACK:
            if slot.dish_id not in pools.treat_snacks:
                comp.slots.remove(slot)
                notes.append(f"{label(slot)}: bez przekąski {name(slot.dish_id)} (nie pasuje do obecnych zasad)")
            continue
        idx = batch_idx.get(slot.key)
        pool = pools.batch.get(idx, []) if idx is not None else pools.by_meal.get(slot.meal, [])
        if slot.dish_id in pool:
            continue
        old = dishes.get(slot.dish_id)
        candidates = [d for d in pool if d not in used and d in dishes and (old is None or (
            dishes[d].is_treat == old.is_treat
            and (slot.meal is not MealType.SUPPER or dishes[d].is_sweet == old.is_sweet)))]
        why = "; ".join(pools.excluded.get(slot.dish_id, [])) or "danie nie pasuje do obecnych zasad"
        if not candidates:
            notes.append(f"{label(slot)}: {name(slot.dish_id)} nie pasuje ({why}), a nie ma czym go zastąpić")
            continue
        best = max(candidates, key=lambda d: (_similarity(pools, slot.dish_id, d), d))
        notes.append(f"{label(slot)}: {name(slot.dish_id)} → {name(best)} ({why})")
        used.add(best)
        slot.dish_id, slot.alternates = best, []
    return notes


def autofix_composition(comp: Composition, pools: Pools, dishes: Mapping[str, DishTemplate], a: Assumptions,
                        days: list[Weekday], cuisine_counts: Mapping[str, int] | None = None,
                        must_use: set[str] | None = None, pins: Mapping[str, list[Weekday]] | None = None) -> list[str]:
    """Poprawia układ w miejscu. Zwraca notatki po polsku (co i dlaczego zamieniono). `pins` — dania z życzeń
    i dni, w które mają być (dania są też w `must_use`)."""
    notes: list[str] = []
    counts = dict(cuisine_counts or {})
    pins = {d: [x for x in ds if x in days] for d, ds in (pins or {}).items() if d in pools.profiles}
    keep = {d for d in (must_use or ()) if d in pools.profiles} | set(pins)
    layout = slot_layout(a, days)
    batch_idx = {"-".join(str(int(d)) for d in ds) + f"_{m.value}": idx for ds, m, idx in layout if idx is not None}
    batch_keys = set(batch_idx)

    def label(s: SlotPlan) -> str:
        return f"{', '.join(DAY_LABELS_PL[d] for d in s.days).lower()}, {MEAL_LABELS_PL[s.meal].lower()}"

    def name(d: str) -> str:
        return dishes[d].name if d in dishes else d

    def used() -> set[str]:
        return {s.dish_id for s in comp.slots}

    def is_treat(d: str) -> bool:
        return d in dishes and dishes[d].is_treat

    def is_sweet_supper(s: SlotPlan) -> bool:
        return s.meal is MealType.SUPPER and s.dish_id in dishes and dishes[s.dish_id].is_sweet

    def with_eggs(d: str) -> bool:
        prof = pools.profiles.get(d)
        return bool(prof and prof.with_eggs)

    def egg_day_taken(day: int, except_slot: SlotPlan) -> bool:
        return any(with_eggs(s.dish_id) and day in {int(x) for x in s.days} for s in comp.slots if s is not except_slot)

    def cuisine_of(d: str) -> str | None:
        return dishes[d].cuisine if d in dishes else None

    def cuisine_ok(old: str, new: str) -> bool:
        """Zamiana nie psuje liczby dań kuchni z życzeń (albo przybliża ją do celu)."""
        co, cn = cuisine_of(old), cuisine_of(new)
        if not counts or co == cn:
            return True
        have = {c: sum(1 for s in comp.slots if cuisine_of(s.dish_id) == c) for c in counts}
        return (co not in counts or have[co] > counts[co]) and (cn not in counts or have[cn] < counts[cn])

    def same_kind(s: SlotPlan, d: str) -> bool:
        return is_treat(d) == is_treat(s.dish_id) and (s.meal is not MealType.SUPPER
                                                       or dishes[d].is_sweet == is_sweet_supper(s))

    def eggs_ok(s: SlotPlan, d: str) -> bool:
        if not with_eggs(d) or with_eggs(s.dish_id):
            return True
        egg_meals = sum(1 for x in comp.slots if with_eggs(x.dish_id))
        return egg_meals < MAX_EGG_MEALS and not any(egg_day_taken(int(day), s) for day in s.days)

    def put(slot: SlotPlan, dish: str, why: str) -> None:
        notes.append(f"{label(slot)}: {name(slot.dish_id)} → {name(dish)} ({why})")
        slot.dish_id, slot.alternates = dish, []

    def replace(slot: SlotPlan, accept: Callable[[str], bool], why: str, force: bool = False) -> bool:
        if slot.key in batch_keys:
            return False  # dania na zapas zmieniają dwa dni naraz — zostawiamy modelowi/naprawie
        if slot.dish_id in keep and not force:
            return False  # nowe danie wymyślone na prośbę domowników zostaje
        pool = [d for d in pools.by_meal.get(slot.meal, []) if d not in used() and accept(d)]
        strict = [d for d in pool if cuisine_ok(slot.dish_id, d)]
        pool = strict or (pool if force else [])
        if not pool:
            return False
        best = max(pool, key=lambda d: (d in keep, _similarity(pools, slot.dish_id, d), d))
        put(slot, best, why)
        return True

    def replace_batch(slot: SlotPlan, accept: Callable[[str], bool], why: str) -> bool:
        """Zupa na zapas: wymiana na inną zupę z puli tej zasady (oba dni naraz)."""
        if slot.key not in batch_keys or slot.dish_id in keep:
            return False
        pool = [d for d in pools.batch.get(batch_idx[slot.key], []) if d not in used() and accept(d)
                and cuisine_ok(slot.dish_id, d)]
        if not pool:
            return False
        put(slot, max(pool, key=lambda d: (_similarity(pools, slot.dish_id, d), d)), why)
        return True

    def unprotected_first(slots: list[SlotPlan]) -> list[SlotPlan]:
        return sorted(slots, key=lambda x: x.dish_id in keep)  # sortowanie stabilne — reszta kolejności zostaje

    def drop_snack(slot: SlotPlan, why: str) -> None:
        """Przekąskę z puli 20% da się po prostu usunąć — dzień ma dalej trzy posiłki."""
        notes.append(f"{label(slot)}: bez przekąski {name(slot.dish_id)} ({why})")
        comp.slots.remove(slot)

    def treats_on(day: int, except_slot: SlotPlan | None = None) -> list[SlotPlan]:
        return [s for s in comp.slots if s is not except_slot and is_treat(s.dish_id) and day in {int(x) for x in s.days}]

    def snack_on(day: int) -> SlotPlan | None:
        return next((s for s in comp.slots if s.meal is MealType.SNACK and int(s.days[0]) == day), None)

    def place_snack(d: str, why: str) -> bool:
        """Przekąska z życzeń („w sobotę lody”): w dniu z życzeń — zamiast innej przekąski tego dnia albo jako
        dodatkowy slot; bez dnia w życzeniach — w dniu bez niczego z puli 20%."""
        want = [int(x) for x in pins.get(d, [])]
        for day in want + [x for x in TREAT_DAY_ORDER if Weekday(x) in days and x not in want]:
            current = snack_on(day)
            if current is not None and current.dish_id in keep:
                continue
            if day not in want and any(x is not current for x in treats_on(day)):
                continue
            if current is not None:
                put(current, d, why)
            else:
                slot = SlotPlan(days=[Weekday(day)], meal=MealType.SNACK, dish_id=d)
                comp.slots.append(slot)
                notes.append(f"{label(slot)}: {name(d)} ({why})")
            return True
        return False

    for _ in range(MAX_ROUNDS):  # każda runda naprawia jedną rzecz; limit chroni przed pętlą
        changed = False
        # 1. danie spoza puli posiłku (lub nieznane) albo powtórzone
        seen: set[str] = set()
        for s in list(comp.slots):
            if s.meal is MealType.SNACK:
                if s.dish_id not in pools.treat_snacks or s.dish_id in seen:
                    drop_snack(s, "przekąska spoza listy przekąsek z puli 20%" if s.dish_id not in pools.treat_snacks
                               else "powtórka przekąski")
                    changed = True
                    break
                seen.add(s.dish_id)
                continue
            if s.key in batch_keys:
                seen.add(s.dish_id)
                continue
            in_pool = s.dish_id in pools.by_meal.get(s.meal, [])
            if not in_pool or (a.week.no_repeats and s.dish_id in seen):
                want_treat, want_sweet = is_treat(s.dish_id), is_sweet_supper(s)
                why = "danie spoza listy na ten posiłek" if not in_pool else "powtórka dania"
                changed = replace(s, lambda d, t=want_treat, w=want_sweet, m=s.meal: is_treat(d) == t
                                  and (m is not MealType.SUPPER or dishes[d].is_sweet == w), why, force=True)
                if changed:
                    break
            seen.add(s.dish_id)
        if changed:
            continue
        # 2. nowe dania wymyślone na prośbę i dania z życzeń muszą trafić do tygodnia (danie z życzeń w swoim dniu)
        for d in sorted(keep - used(), key=lambda x: (x not in pins, x)):
            why = "danie z życzeń domowników" if d in pins else "nowe danie wymyślone na prośbę"
            if d in pools.treat_snacks:
                if place_snack(d, why):
                    changed = True
                    break
                continue
            batch = [s for s in comp.slots if s.key in batch_keys and s.dish_id not in keep
                     and d in pools.batch.get(batch_idx[s.key], [])]
            if batch:
                put(batch[0], d, why)
                changed = True
                break
            want_days = {int(x) for x in pins.get(d, [])}
            spots = [s for s in comp.slots if s.key not in batch_keys and s.dish_id not in keep
                     and d in pools.by_meal.get(s.meal, []) and eggs_ok(s, d)]
            on_day = [s for s in spots if int(s.days[0]) in want_days]
            spots = on_day or spots
            strict = [s for s in spots if same_kind(s, d) and cuisine_ok(s.dish_id, d)]
            spots = strict or spots
            if spots:
                best = max(spots, key=lambda s: (_similarity(pools, s.dish_id, d), -int(s.days[0])))
                put(best, d, why)
                changed = True
                break
            if with_eggs(d):
                # limit jajek jest wyczerpany przez inne dania — zwalniamy miejsce: dotychczasowe danie z jajkami
                # (nie nowe) zamieniamy na podobne bez jajek, a nowe danie wejdzie w następnej rundzie
                egg_spots = unprotected_first([s for s in comp.slots if with_eggs(s.dish_id) and s.dish_id not in keep])
                for s in egg_spots:
                    if replace(s, lambda x, s=s: not with_eggs(x) and same_kind(s, x),
                               f"miejsce na nowe danie z jajkami: {name(d)}"):
                        changed = True
                        break
                if changed:
                    break
        if changed:
            continue
        # 2b. danie z życzeń w innym dniu niż chcieli domownicy — zamiana z tym samym posiłkiem w wybranym dniu
        for d, want in sorted(pins.items()):
            cur = next((s for s in comp.slots if s.dish_id == d and s.key not in batch_keys), None)
            if cur is None or not want or int(cur.days[0]) in {int(x) for x in want}:
                continue
            if cur.meal is MealType.SNACK:
                day = int(want[0])
                if any(x.dish_id in keep for x in treats_on(day, cur)):
                    continue  # w tym dniu jest już inne danie z życzeń z puli 20% — zostaje, gdzie jest
                other = snack_on(day)
                notes.append(f"{label(cur)} → {DAY_LABELS_PL[Weekday(day)].lower()}: {name(d)} w dniu z życzeń domowników")
                if other is not None:
                    other.days = list(cur.days)
                cur.days = [Weekday(day)]
                changed = True
                break
            target = next((s for s in comp.slots if s.key not in batch_keys and s.meal is cur.meal
                           and int(s.days[0]) == int(want[0]) and s.dish_id not in pins), None)
            if target is None:
                continue
            other = target.dish_id
            notes.append(f"{label(cur)} ↔ {label(target)}: {name(d)} w dniu z życzeń domowników")
            target.dish_id, target.alternates = d, []
            cur.dish_id, cur.alternates = other, []
            changed = True
            break
        if changed:
            continue
        # 3. więcej niż jeden posiłek z puli 20% jednego dnia
        by_day: dict[int, list[SlotPlan]] = {}
        for s in comp.slots:
            if is_treat(s.dish_id):
                for d in s.days:
                    by_day.setdefault(int(d), []).append(s)
        for day_slots in by_day.values():
            if len(day_slots) < 2:
                continue
            snacks = [s for s in day_slots if s.meal is MealType.SNACK]
            loose = [s for s in snacks if s.dish_id not in keep]
            if loose:
                drop_snack(loose[0], "najwyżej jedna rzecz z puli 20% dziennie")
                changed = True
                break
            # przekąska z życzeń zostaje — wymieniamy posiłek z puli 20% tego dnia
            order = sorted((s for s in day_slots if s.meal is not MealType.SNACK),
                           key=lambda x: (x.dish_id in keep, x.meal is not MealType.SUPPER))
            for s in order:
                if replace(s, lambda d, m=s.meal: not is_treat(d) and (m is not MealType.SUPPER
                           or not dishes[d].is_sweet), "najwyżej jedna rzecz z puli 20% dziennie"):
                    changed = True
                    break
            if changed:
                break
            # posiłek z puli też jest z życzeń: przekąska przechodzi na dzień bez niczego z puli 20%
            free = next((x for x in TREAT_DAY_ORDER if Weekday(x) in days and not treats_on(x)), None)
            if snacks and free is not None and all(x.dish_id in keep for x in day_slots):
                s = snacks[0]
                notes.append(f"{label(s)} → {DAY_LABELS_PL[Weekday(free)].lower()}: {name(s.dish_id)} "
                             "(najwyżej jedna rzecz z puli 20% dziennie)")
                s.days = [Weekday(free)]
                changed = True
                break
        if changed:
            continue
        # 4. za mało / za dużo posiłków z puli 20% (pełny tydzień)
        treats = [s for s in comp.slots if is_treat(s.dish_id)]
        count = sum(len(s.days) for s in treats)
        band = a.week.treat_meals
        if len(days) == 7 and count < band.min:
            treat_days = {int(d) for s in treats for d in s.days}
            for day in TREAT_DAY_ORDER:
                if day in treat_days:
                    continue
                for meal in (MealType.LUNCH, MealType.SUPPER):
                    s = next((x for x in comp.slots if x.days == [Weekday(day)] and x.meal is meal), None)
                    if s is None or is_sweet_supper(s):
                        continue
                    if replace(s, lambda d, m=meal, day=day, s=s: is_treat(d)
                               and (m is not MealType.SUPPER or not dishes[d].is_sweet)
                               and not (with_eggs(d) and egg_day_taken(day, s)), "brakowało posiłku z puli 20%"):
                        changed = True
                        break
                if changed:
                    break
        elif count > band.max and any(s.meal is MealType.SNACK and s.dish_id not in keep for s in treats):
            drop_snack(next(s for s in reversed(treats) if s.meal is MealType.SNACK and s.dish_id not in keep),
                       "za dużo rzeczy z puli 20%")
            changed = True
        elif count > band.max:
            for s in unprotected_first(list(reversed(treats))):
                if replace(s, lambda d, m=s.meal: not is_treat(d) and (m is not MealType.SUPPER
                           or not dishes[d].is_sweet), "za dużo posiłków z puli 20%"):
                    changed = True
                    break
        if changed:
            continue
        # 5. za dużo słodkich kolacji
        sweets = [s for s in comp.slots if is_sweet_supper(s)]
        if len(sweets) > a.week.sweet_suppers_max:
            for s in unprotected_first(list(reversed(sweets))):
                if replace(s, lambda d: not dishes[d].is_sweet and not is_treat(d), "za dużo słodkich kolacji"):
                    changed = True
                    break
            if changed:
                continue
        # 6. jajka: najwyżej jeden posiłek dziennie i MAX_EGG_MEALS w tygodniu
        egg_slots = [s for s in comp.slots if with_eggs(s.dish_id)]
        egg_by_day: dict[int, list[SlotPlan]] = {}
        for s in egg_slots:
            for d in s.days:
                egg_by_day.setdefault(int(d), []).append(s)
        for _day, day_slots in sorted(egg_by_day.items()):
            if len(day_slots) < 2:
                continue
            # zostaje śniadanie (jajka rano są najnaturalniejsze) i nowe dania; wymieniamy resztę
            order = sorted(day_slots, key=lambda x: (x.dish_id in keep, x.meal is MealType.BREAKFAST))
            for s in order[:-1]:
                if replace(s, lambda d, s=s: not with_eggs(d) and same_kind(s, d),
                           "najwyżej jeden posiłek z jajkami dziennie"):
                    changed = True
                    break
            if changed:
                break
        if changed:
            continue
        if len(egg_slots) > MAX_EGG_MEALS:
            for s in unprotected_first(list(reversed(egg_slots))):
                if replace(s, lambda d, s=s: not with_eggs(d) and same_kind(s, d),
                           f"najwyżej {MAX_EGG_MEALS} posiłków z jajkami w tygodniu"):
                    changed = True
                    break
            if changed:
                continue
        # 7. dokładna liczba dań danej kuchni z życzeń
        for cuisine, want in sorted(counts.items()):
            have = [s for s in comp.slots if cuisine_of(s.dish_id) == cuisine]
            why = f"w życzeniach kuchnia {cuisine_label(cuisine)}: {want}"
            if len(have) > want:
                spots = unprotected_first(list(reversed(have)))
                keep_c = lambda d, c=cuisine: cuisine_of(d) != c  # noqa: E731
            elif len(have) < want:
                spots = sorted((s for s in comp.slots if cuisine_of(s.dish_id) != cuisine),
                               key=lambda x: (x.meal is MealType.BREAKFAST, int(x.days[0])))
                keep_c = lambda d, c=cuisine: cuisine_of(d) == c  # noqa: E731
            else:
                continue
            # najpierw zamiana „tego samego rodzaju” (status puli 20%/słodkie); gdy się nie da — luźniej, a zaburzoną
            # liczbę posiłków z puli 20% wyrównują reguły wyżej i przeszukiwanie lokalne na końcu
            for strict in (True, False):
                for s in spots:
                    ok = lambda d, s=s, st=strict, kc=keep_c: kc(d) and eggs_ok(s, d) and (same_kind(s, d) or not st)  # noqa: E731
                    if replace(s, ok, why) or replace_batch(s, ok, why):
                        changed = True
                        break
                if changed:
                    break
            if changed:
                break
        if not changed:
            break
    notes.extend(_polish(comp, pools, dishes, a, days, counts, keep, batch_idx, pins))
    return notes


# wagi naruszeń w przeszukiwaniu lokalnym: zasady tygodnia są ważniejsze od życzeń
HARD = 1000
RULE = 300
WISH = 100
MAX_POLISH_STEPS = 12


def violation_score(comp: Composition, pools: Pools, dishes: Mapping[str, DishTemplate], a: Assumptions,
                    days: list[Weekday], counts: Mapping[str, int], keep: set[str],
                    batch_idx: Mapping[str, int], pins: Mapping[str, list[Weekday]] | None = None) -> int:
    """Liczbowa miara naruszeń (0 = układ zgodny z zasadami i życzeniami) — spójna z `validate_composition`."""
    score = 0
    seen: set[str] = set()
    treat_days: dict[int, int] = {}
    egg_days: dict[int, int] = {}
    treats = sweets = eggs = 0
    for s in comp.slots:
        pool = (pools.treat_snacks if s.meal is MealType.SNACK else
                pools.batch.get(batch_idx[s.key], []) if s.key in batch_idx else pools.by_meal.get(s.meal, []))
        if s.dish_id not in pool:
            score += HARD
        if a.week.no_repeats and s.dish_id in seen:
            score += HARD
        seen.add(s.dish_id)
        dish = dishes.get(s.dish_id)
        if dish is None:
            continue
        if dish.is_treat:
            treats += len(s.days)
            for d in s.days:
                treat_days[int(d)] = treat_days.get(int(d), 0) + 1
        if s.meal is MealType.SUPPER and dish.is_sweet:
            sweets += 1
        prof = pools.profiles.get(s.dish_id)
        if prof is not None and prof.with_eggs:
            eggs += 1
            for d in s.days:
                egg_days[int(d)] = egg_days.get(int(d), 0) + 1
    band = a.week.treat_meals
    if len(days) == 7:
        score += RULE * int(max(0, band.min - treats) + max(0, treats - band.max))
    score += RULE * sum(n - 1 for n in treat_days.values() if n > 1)
    score += RULE * max(0, sweets - a.week.sweet_suppers_max)
    score += RULE * max(0, eggs - MAX_EGG_MEALS)
    score += RULE * sum(n - 1 for n in egg_days.values() if n > 1)
    for cuisine, want in counts.items():
        have = sum(1 for s in comp.slots if s.dish_id in dishes and dishes[s.dish_id].cuisine == cuisine)
        score += WISH * abs(have - want)
    score += WISH * len(keep - seen)
    for d, want in (pins or {}).items():
        slot = next((s for s in comp.slots if s.dish_id == d and s.key not in batch_idx), None)
        if slot is not None and want and int(slot.days[0]) != int(want[0]):
            score += WISH
    return score


def _polish(comp: Composition, pools: Pools, dishes: Mapping[str, DishTemplate], a: Assumptions, days: list[Weekday],
            counts: Mapping[str, int], keep: set[str], batch_idx: Mapping[str, int],
            pins: Mapping[str, list[Weekday]] | None = None) -> list[str]:
    """Przeszukiwanie lokalne po regułach: pojedyncze wymiany dań i zamiany dań między dniami, tylko gdy poprawiają wynik."""
    notes: list[str] = []

    def score() -> int:
        return violation_score(comp, pools, dishes, a, days, counts, keep, batch_idx, pins)

    def pool(s: SlotPlan) -> list[str]:
        if s.meal is MealType.SNACK:
            return pools.treat_snacks
        return pools.batch.get(batch_idx[s.key], []) if s.key in batch_idx else pools.by_meal.get(s.meal, [])

    def label(s: SlotPlan) -> str:
        return f"{', '.join(DAY_LABELS_PL[d] for d in s.days).lower()}, {MEAL_LABELS_PL[s.meal].lower()}"

    for _ in range(MAX_POLISH_STEPS):
        base = score()
        if base == 0:
            break
        used = {s.dish_id for s in comp.slots}
        best: tuple[int, float, tuple] | None = None
        for s in comp.slots:
            if s.dish_id in keep and s.dish_id in pool(s):
                continue  # nowe danie zostaje na swoim miejscu (może się tylko zamienić dniami — niżej)
            old = s.dish_id
            for d in pool(s):
                if d in used:
                    continue
                s.dish_id = d
                value = score()
                s.dish_id = old
                if value < base:
                    cand = (value, -_similarity(pools, old, d), ("wymiana", s, d))
                    if best is None or cand[:2] < best[:2]:
                        best = cand
        singles = [s for s in comp.slots if s.key not in batch_idx]
        for i, s1 in enumerate(singles):
            for s2 in singles[i + 1:]:
                if s1.meal is not s2.meal or s1.dish_id == s2.dish_id:
                    continue
                s1.dish_id, s2.dish_id = s2.dish_id, s1.dish_id
                value = score()
                s1.dish_id, s2.dish_id = s2.dish_id, s1.dish_id
                if value < base:
                    cand = (value, 0.0, ("zamiana", s1, s2))
                    if best is None or cand[:2] < best[:2]:
                        best = cand
        if best is None:
            break
        kind, x, y = best[2]
        if kind == "wymiana":
            notes.append(f"{label(x)}: {dishes[x.dish_id].name if x.dish_id in dishes else x.dish_id} → "
                         f"{dishes[y].name} (dopasowanie zasad i życzeń)")
            x.dish_id, x.alternates = y, []
        else:
            notes.append(f"{label(x)} ↔ {label(y)}: zamiana dań między dniami (dopasowanie zasad i życzeń)")
            x.dish_id, y.dish_id = y.dish_id, x.dish_id
            x.alternates, y.alternates = [], []
    return notes


MAX_BALANCE_SWAPS = 4  # najwyżej tyle wymian na tydzień — wybory modelu zostają w większości nietknięte
MIN_BALANCE_GAIN_PP = 5.0  # wymiana musi wyraźnie poprawić bilans dnia


def rebalance_days(comp: Composition, pools: Pools, dishes: Mapping[str, DishTemplate], a: Assumptions,
                   days: list[Weekday], cuisine_counts: Mapping[str, int] | None = None,
                   protected: set[str] | None = None, max_swaps: int = MAX_BALANCE_SWAPS) -> list[str]:
    """Bilans makro dnia (planner/balance.py): w dniach o rozkładzie energii dalekim od celu wymienia jedno danie
    na podobne, które wyraźnie poprawia bilans. Nie rusza dań na zapas, posiłków z puli 20%, dań chronionych
    (nowe dania, dania z życzeń) i nie łamie zasad tygodnia ani liczb kuchni z życzeń — każdą wymianę sprawdza
    `validate_composition` i `wish_errors`, a gdy coś psuje, wycofuje ją. Zwraca notatki po polsku."""
    from .balance import BALANCE_OK_PP, BALANCE_WARN_PP, day_deviation
    from .compose import validate_composition, wish_errors

    protected = set(protected or ())
    counts = dict(cuisine_counts or {})
    batch_keys = {"-".join(str(int(d)) for d in ds) + f"_{m.value}" for ds, m, idx in slot_layout(a, days)
                  if idx is not None}
    notes: list[str] = []
    tried: set[tuple[str, str]] = set()

    def typical(d: str) -> dict[str, float]:
        return pools.profiles[d].typical if d in pools.profiles else {}

    def day_dishes(day: int) -> list[SlotPlan]:
        return [s for s in comp.slots if day in {int(d) for d in s.days}]

    def deviation(day: int, replace: tuple[SlotPlan, str] | None = None) -> float:
        items = [typical(replace[1] if replace and s is replace[0] else s.dish_id) for s in day_dishes(day)]
        return day_deviation(items, pools.target)

    def wish_problems() -> set[str]:
        return set(wish_errors(comp, pools, dishes, a, days, counts, protected & set(pools.profiles)))

    if validate_composition(comp, pools, dishes, a, days):
        return notes  # bilans poprawiamy tylko w układzie zgodnym z zasadami
    # układ bywa zgodny z zasadami, ale nie ze wszystkimi życzeniami — wymiana nie może dołożyć nowego problemu
    wish_before = wish_problems()

    def valid() -> bool:
        return not validate_composition(comp, pools, dishes, a, days) and wish_problems() <= wish_before

    swaps = 0
    while swaps < max_swaps:
        devs = sorted(((deviation(int(d)), int(d)) for d in days), reverse=True)
        best = None
        for dev, day in devs:
            if dev <= BALANCE_WARN_PP:
                break
            used = {s.dish_id for s in comp.slots}
            eggs_used = sum(1 for s in comp.slots if pools.profiles.get(s.dish_id) and pools.profiles[s.dish_id].with_eggs)
            for slot in day_dishes(day):
                current = dishes.get(slot.dish_id)
                if slot.key in batch_keys or current is None or current.is_treat or slot.dish_id in protected:
                    continue
                cur_prof = pools.profiles.get(slot.dish_id)
                options = []
                for cand in pools.by_meal.get(slot.meal, []):
                    cd = dishes.get(cand)
                    prof = pools.profiles.get(cand)
                    if (cand in used or cd is None or prof is None or (slot.key, cand) in tried or cd.is_treat
                            or (slot.meal is MealType.SUPPER and cd.is_sweet != current.is_sweet)):
                        continue
                    if prof.with_eggs and not (cur_prof and cur_prof.with_eggs):
                        egg_day = any(pools.profiles.get(o.dish_id) and pools.profiles[o.dish_id].with_eggs
                                      for o in day_dishes(day) if o is not slot)
                        if eggs_used >= MAX_EGG_MEALS or egg_day:
                            continue
                    if counts and (cd.cuisine in counts or current.cuisine in counts) and cd.cuisine != current.cuisine:
                        continue  # liczba dań kuchni z życzeń zostaje bez zmian
                    new_dev = deviation(day, (slot, cand))
                    if new_dev <= dev - MIN_BALANCE_GAIN_PP:
                        options.append((new_dev, cand))
                if not options:
                    continue
                # wśród wymian dających dzień wystarczająco zrównoważony bierzemy najbardziej podobne danie (to samo
                # białko, kuchnia) — inaczej wymiany zbiegałyby się do kilku najchudszych dań; bez takich — najlepszy bilans
                enough = max(BALANCE_OK_PP, min(dev - 10.0, BALANCE_WARN_PP - 2.0))
                good = [o for o in options if o[0] <= enough]
                pick = (max(good, key=lambda o: (_similarity(pools, slot.dish_id, o[1]), -o[0], o[1])) if good
                        else min(options, key=lambda o: (o[0], -_similarity(pools, slot.dish_id, o[1]), o[1])))
                cand_score = (pick[0] if not good else pick[0] - 100, day)
                if best is None or cand_score < best[0]:
                    best = (cand_score, slot, pick[1], dev, pick[0], day)
            if best is not None:
                break  # najgorszy dzień, który da się poprawić
        if best is None:
            break
        _, slot, cand, old_dev, new_dev, day = best
        old = slot.dish_id
        slot.dish_id, old_alts = cand, slot.alternates
        slot.alternates = []
        if not valid():
            slot.dish_id, slot.alternates = old, old_alts
            tried.add((slot.key, cand))
            continue
        swaps += 1
        notes.append(f"{DAY_LABELS_PL[Weekday(day)].lower()}, {MEAL_LABELS_PL[slot.meal].lower()}: {dishes[old].name} → "
                     f"{dishes[cand].name} (bilans makro dnia: odchylenie {old_dev:.0f} → {new_dev:.0f} pp)")
    return notes
