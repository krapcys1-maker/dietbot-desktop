"""Preferencje domowników z notatnika: zasady, które program nakłada na założenia przy układaniu planów.

Zasady zapisuje aplikacja (zakładka „Notatnik preferencji”), a nie plik założeń — dzięki temu można je w każdej chwili
wyłączyć, a zapisane założenia domowników zostają bez zmian. Liczby w zasadach (limity porcji, przesunięcie makro)
pochodzą wprost z wypowiedzi użytkownika albo liczy je kod — nigdy model językowy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from ..textfmt import fmt_num
from .assumptions import Assumptions, Band, PortionCap
from .common import DomainModel
from .dish import MEAL_LABELS_PL, MealType
from .product import CATEGORY_LABELS_PL, Category, Product


class RuleKind(StrEnum):
    BAN = "zakaz_produktu"  # produkt albo kategoria nie trafia do planów (dania z nim jako obowiązkowym odpadają)
    CAP = "limit_porcji"  # największa porcja produktu/kategorii na posiłek, osobno dla osób
    MACRO = "zamiana_makro"  # część jednego makroskładnika zamieniona na inny (np. mniej węglowodanów, więcej białka)
    HINT = "wskazowka"  # miękka wskazówka dla układającego tydzień (np. „mniej dań z jajkami na kolację”)


RULE_KIND_PL: dict[RuleKind, str] = {
    RuleKind.BAN: "Zakaz produktu",
    RuleKind.CAP: "Limit porcji",
    RuleKind.MACRO: "Zamiana makroskładników",
    RuleKind.HINT: "Wskazówka do układania",
}

MacroKey = Literal["weglowodany", "bialko", "tluszcz"]
MACRO_FIELD: dict[str, str] = {"weglowodany": "carbs", "bialko": "protein", "tluszcz": "fat"}
MACRO_PL: dict[str, str] = {"weglowodany": "węglowodanów", "bialko": "białka", "tluszcz": "tłuszczu"}
MACRO_NOM_PL: dict[str, str] = {"weglowodany": "węglowodany", "bialko": "białko", "tluszcz": "tłuszcz"}
KCAL_PER_G: dict[str, float] = {"weglowodany": 4.0, "bialko": 4.0, "tluszcz": 9.0}
# domyślna zamiana makro: tyle energii dziennej osoby przechodzi z jednego makroskładnika na drugi
MACRO_SHIFT_SHARE = 0.04
MAX_MACRO_SHIFT_SHARE = 0.12  # więcej to już zmiana celów diety — do zrobienia w „Cele i preferencje”


class PreferenceRule(DomainModel):
    id: str
    kind: RuleKind = Field(alias="typ")
    active: bool = Field(default=True, alias="aktywna")
    persons: list[str] = Field(default_factory=list, alias="osoby")  # puste = wszyscy domownicy
    product_id: str | None = Field(default=None, alias="produkt")
    category: Category | None = Field(default=None, alias="kategoria")
    meals: list[MealType] = Field(default_factory=list, alias="posilki")  # puste = każdy posiłek
    # limit porcji: osoba → maks. g na posiłek; zamiana makro: osoba → g makroskładnika „z”, które oddajemy
    grams: dict[str, float] = Field(default_factory=dict, alias="gramy")
    shift_from: MacroKey | None = Field(default=None, alias="z")
    shift_to: MacroKey | None = Field(default=None, alias="na")
    text: str | None = Field(default=None, alias="tekst", max_length=300)
    source: str = Field(default="", alias="zrodlo", max_length=200)
    created_at: str = Field(default="", alias="utworzono")
    note_id: str | None = Field(default=None, alias="notatka")

    @model_validator(mode="after")
    def _shape(self) -> PreferenceRule:
        target = (self.product_id is not None) + (self.category is not None)
        if self.kind in (RuleKind.BAN, RuleKind.CAP) and target != 1:
            raise ValueError("zakaz i limit porcji dotyczą jednego produktu albo jednej kategorii")
        if self.kind is RuleKind.CAP and (not self.grams or any(not 0 < g <= 2000 for g in self.grams.values())):
            raise ValueError("limit porcji wymaga gramów dla osób (0–2000 g)")
        if self.kind is RuleKind.MACRO:
            if not self.shift_from or not self.shift_to or self.shift_from == self.shift_to:
                raise ValueError("zamiana makro wymaga dwóch różnych makroskładników")
            if not self.grams or any(not 0 < g <= 200 for g in self.grams.values()):
                raise ValueError("zamiana makro wymaga gramów dla osób (0–200 g)")
        if self.kind is RuleKind.HINT and not (self.text or "").strip():
            raise ValueError("wskazówka wymaga tekstu")
        return self

    def target_key(self) -> tuple:
        """Czego dotyczy zasada — nowa zasada o tym samym kluczu zastępuje starą (np. nowy limit ryżu)."""
        if self.kind is RuleKind.MACRO:
            return (self.kind, tuple(sorted({self.shift_from or "", self.shift_to or ""})))
        if self.kind is RuleKind.HINT:
            return (self.kind, (self.text or "").strip().lower())
        return (self.kind, self.product_id, self.category, tuple(sorted(m.value for m in self.meals)))


@dataclass
class AppliedPreferences:
    assumptions: Assumptions
    effects: list[str] = field(default_factory=list)  # co zmieniły zasady (opis dla domowników)
    hints: list[str] = field(default_factory=list)  # wskazówki dla układającego tydzień
    problems: list[str] = field(default_factory=list)  # zasady pominięte (np. produkt usunięty z bazy)


def target_label(rule: PreferenceRule, products: Mapping[str, Product]) -> str:
    if rule.product_id:
        p = products.get(rule.product_id)
        return p.name.lower() if p else rule.product_id
    if rule.category:
        return f"{CATEGORY_LABELS_PL[rule.category].lower()} (cała kategoria)"
    return ""


def _persons_label(rule: PreferenceRule, a: Assumptions) -> str:
    if not rule.persons or set(rule.persons) >= {p.id for p in a.persons}:
        return "wszyscy domownicy" if len(a.persons) > 2 else "obie osoby" if len(a.persons) == 2 else "domownik"
    names = {p.id: p.name for p in a.persons}
    return ", ".join(names.get(pid, pid) for pid in rule.persons)


def _meals_label(rule: PreferenceRule) -> str:
    return ", ".join(MEAL_LABELS_PL[m].lower() for m in rule.meals) if rule.meals else "każdy posiłek"


def macro_amounts(rule: PreferenceRule, person_id: str) -> tuple[float, float]:
    """(gramy oddawane z makroskładnika „z”, gramy dodawane do „na”) — ta sama energia."""
    taken = float(rule.grams.get(person_id, 0.0))
    if not taken or not rule.shift_from or not rule.shift_to:
        return 0.0, 0.0
    given = taken * KCAL_PER_G[rule.shift_from] / KCAL_PER_G[rule.shift_to]
    return taken, round(given, 1)


def describe(rule: PreferenceRule, a: Assumptions, products: Mapping[str, Product]) -> str:
    """Opis zasady po polsku; wszystkie liczby pochodzą z zasady (wypowiedź użytkownika albo obliczenie kodu)."""
    if rule.kind is RuleKind.BAN:
        return f"Bez: {target_label(rule, products)} — {_persons_label(rule, a)}"
    if rule.kind is RuleKind.CAP:
        names = {p.id: p.name for p in a.persons}
        parts = [f"{fmt_num(g)} g ({names.get(pid, pid)})" for pid, g in rule.grams.items()]
        return (f"{target_label(rule, products).capitalize()}: najwyżej {' i '.join(parts)} na posiłek — "
                f"{_meals_label(rule)}")
    if rule.kind is RuleKind.MACRO:
        names = {p.id: p.name for p in a.persons}
        parts = []
        for pid in rule.grams:
            taken, given = macro_amounts(rule, pid)
            parts.append(f"{names.get(pid, pid)}: −{fmt_num(taken)} g {MACRO_PL[rule.shift_from or '']}, "
                         f"+{fmt_num(given)} g {MACRO_PL[rule.shift_to or '']} dziennie")
        return f"Mniej {MACRO_PL[rule.shift_from or '']}, więcej {MACRO_PL[rule.shift_to or '']} — " + "; ".join(parts)
    return f"Wskazówka: {rule.text}"


def default_macro_grams(a: Assumptions, shift_from: str, persons: list[str] | None = None) -> dict[str, float]:
    """Domyślna zamiana makro liczona przez kod: ok. 4% energii dnia osoby, zaokrąglona do 5 g."""
    out: dict[str, float] = {}
    for p in a.persons:
        if persons and p.id not in persons:
            continue
        grams = p.energy_center * MACRO_SHIFT_SHARE / KCAL_PER_G[shift_from]
        out[p.id] = float(max(5, round(grams / 5) * 5))
    return out


def _shift_band(band: Band, delta: float, *, lower: bool) -> Band:
    """Przesuwa cel o całe `delta`: granica w tę stronę idzie o `delta`, druga o połowę (widełki szersze o połowę
    `delta`, nie sztywne). Wcześniej cel szedł o połowę, a druga granica stała — silnik trzymał się dolnego skraju
    strefy bez kary i z „+25 g białka dziennie” wychodziło ok. +5 g (test na profilu użytkownika, 20.10.2026)."""
    center = band.center
    if lower:
        new_min = max(0.0, band.min - delta)
        return Band(min=round(new_min, 1), max=round(band.max - delta / 2, 1), cel=round(max(new_min, center - delta), 1))
    new_max = band.max + delta
    return Band(min=round(band.min + delta / 2, 1), max=round(new_max, 1), cel=round(min(new_max, center + delta), 1))


def apply_preferences(a: Assumptions, rules: list[PreferenceRule], products: Mapping[str, Product]) -> AppliedPreferences:
    """Założenia z nałożonymi aktywnymi zasadami. Oryginalne założenia pozostają bez zmian."""
    out = a.model_copy(deep=True)
    result = AppliedPreferences(assumptions=out)
    person_ids = {p.id for p in out.persons}
    fixed = {f.product_id for f in out.day.fixed_items}
    for rule in rules:
        if not rule.active:
            continue
        unknown = [pid for pid in rule.persons if pid not in person_ids]
        if unknown:
            result.problems.append(f"{describe(rule, a, products)} — nieznana osoba: {', '.join(unknown)}")
            continue
        targets = [p for p in out.persons if not rule.persons or p.id in rule.persons]
        if rule.product_id and rule.product_id not in products:
            result.problems.append(f"Zasada dotyczy produktu, którego nie ma w bazie: {rule.product_id}")
            continue
        if rule.kind is RuleKind.BAN:
            ids = [rule.product_id] if rule.product_id else sorted(
                pid for pid, p in products.items() if p.category is rule.category and not p.negligible)
            ids = [pid for pid in ids if pid not in fixed]
            if rule.product_id and rule.product_id in fixed:
                result.problems.append(f"{products[rule.product_id].name} jest w stałych pozycjach dnia — zmień je "
                                       "w „Cele i preferencje”, zakaz nie obejmuje stałych pozycji")
            if not rule.persons or len(targets) == len(out.persons):
                out.prohibitions.products = sorted(set(out.prohibitions.products) | set(ids))
            else:
                for p in targets:
                    p.excluded_products = sorted(set(p.excluded_products) | set(ids))
            result.effects.append(describe(rule, a, products))
        elif rule.kind is RuleKind.CAP:
            for p in targets:
                grams = rule.grams.get(p.id)
                if grams is None:
                    continue
                p.portion_caps = [*p.portion_caps, PortionCap(produkt=rule.product_id, kategoria=rule.category,
                                                              posilki=list(rule.meals), maks_g=grams)]
            result.effects.append(describe(rule, a, products))
        elif rule.kind is RuleKind.MACRO:
            changed = []
            for p in targets:
                taken, given = macro_amounts(rule, p.id)
                if not taken:
                    continue
                share = taken * KCAL_PER_G[rule.shift_from or "weglowodany"] / max(p.energy_center, 1)
                if share > MAX_MACRO_SHIFT_SHARE:
                    result.problems.append(f"{p.name}: zamiana {fmt_num(taken)} g {MACRO_PL[rule.shift_from or '']} "
                                           "to za duża zmiana celów — ustaw ją w „Cele i preferencje”")
                    continue
                t = p.targets
                f_from, f_to = MACRO_FIELD[rule.shift_from or ""], MACRO_FIELD[rule.shift_to or ""]
                setattr(t, f_from, _shift_band(t.band(f_from), taken, lower=True))
                setattr(t, f_to, _shift_band(t.band(f_to), given, lower=False))
                b_from, b_to = t.band(f_from), t.band(f_to)
                changed.append(f"{p.name}: {MACRO_NOM_PL[rule.shift_from or '']} {fmt_num(b_from.min)}–"
                               f"{fmt_num(b_from.max)} g, {MACRO_NOM_PL[rule.shift_to or '']} {fmt_num(b_to.min)}–"
                               f"{fmt_num(b_to.max)} g")
            if changed:
                result.effects.append(describe(rule, a, products) + " (widełki: " + "; ".join(changed) + ")")
        elif rule.kind is RuleKind.HINT and rule.text:
            result.hints.append(rule.text.strip())
    result.assumptions = Assumptions.model_validate(out.model_dump(mode="json", by_alias=True))
    return result
