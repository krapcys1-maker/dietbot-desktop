"""Notatnik preferencji: domownicy piszą uwagi do planów, a Porcja uczy się ich gustu i nawyków.

Podział pracy jak w całej aplikacji:
- model językowy czyta uwagę i rozpoznaje, czego dotyczy (zakaz produktu, limit porcji, mniej węglowodanów, ocena dania,
  wskazówka do układania, uwaga do programu) — nie liczy i nie wymyśla liczb;
- kod dopasowuje produkty i dania do bazy, przyjmuje tylko liczby, które padły w uwadze (resztę liczy sam — np. limit
  porcji z ostatnich planów), zapisuje zasady i nakłada je na założenia przy każdym planie;
- uwagi, których nie da się załatwić zasadą diety, trafiają do listy „Do poprawy w programie” — dla twórcy aplikacji.
Program niczego w sobie nie przeprogramowuje: zapisuje preferencje, żeby kolejne plany były bliżej gustu domowników.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from rapidfuzz import fuzz

from ..domain.dish import MEAL_LABELS_PL, MealType
from ..domain.preferences import (
    MACRO_NOM_PL,
    RULE_KIND_PL,
    AppliedPreferences,
    MacroKey,
    PreferenceRule,
    RuleKind,
    apply_preferences,
    default_macro_grams,
    describe,
)
from ..domain.product import CATEGORY_LABELS_PL, Category
from ..food.matcher import MatchStatus, ProductMatcher
from ..llm.client import LLMError
from ..llm.scope import current as current_scope
from ..llm.scope import task_scope
from ..textfmt import fmt_num, plural_pl
from ..textnorm import normalize
from ..textnumbers import has_digits
from .jobs import NotFound, UserError
from .store import new_id

if TYPE_CHECKING:
    from ..storage.repository import Repository
    from .service import DietService

MAX_NOTE_CHARS = 2000
HABITS_KEY = "notatnik_nawyki"
DEFAULT_CAP_SHARE = 0.85  # „mniej X” bez liczby: o ok. 15% mniej niż największa porcja w ostatnich planach
RECENT_PLANS_FOR_CAP = 12
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
# słowa, które nie należą do nazwy produktu („same białka jaj”, „żadnych ryb”) — bez nich dopasowanie jest pewne
PRODUCT_FILLERS = {"sam", "sama", "samo", "same", "samych", "samego", "tylko", "zadne", "zadnych", "zadnego", "zadnej",
                   "wszystkie", "wszystkich", "duzo", "mniej", "wiecej", "kupne", "kupnych"}
SAME_NOTE_SCORE = 80  # podobieństwo, od którego uwaga do programu jest powtórką już zapisanej
FUZZY_SINGLE_MIN = 72.0  # jedyny kandydat z rozmytego dopasowania (np. dopełniacz nazwy)
SUMMARY_PREFIX = re.compile(r"^(zrozumiałem|rozumiem|odczytałem)\s*[:,.\-–—]\s*", re.IGNORECASE)

NOTE_SYSTEM = """Jesteś asystentem w polskiej aplikacji do planowania tygodniowego jadłospisu (Porcja). Domownik pisze
uwagę do planu: co mu nie pasuje, co lubi, czego nie je, co poprawić. Twoje zadanie: rozpoznać, co z tej uwagi wynika.
Liczby (gramatury, kalorie) liczy program — Ty niczego nie liczysz i nie wymyślasz liczb.

Rodzaje zasad (pole "zasady"), stosowane potem przy każdym planie:
- "zakaz_produktu" — domownik nie chce produktu albo całej kategorii (np. „nie dawaj białek jaj”, „bez ryb”).
  Podaj "produkt" (podstawowa nazwa produktu w mianowniku, bez słów typu „same”, „tylko”, np. „białka jaj”) ALBO
  "kategoria" (z listy), gdy chodzi o wszystkie produkty danego rodzaju. Gdy dotyczy jednej osoby — "osoby" z jej id.
- "limit_porcji" — za duża porcja konkretnego produktu (np. „za dużo ryżu, wolę mniej”). "kategoria" dla rodzaju
  (każdy ryż → kategoria ryz), "produkt" dla jednego produktu. "gramy" (id osoby → liczba) WYŁĄCZNIE z liczb, które
  użytkownik napisał jako nowy, pożądany limit — nie przepisuj liczb, które tylko opisują obecny stan. Para liczb bez
  osób dotyczy osób w kolejności podanej w kontekście. Bez liczb zostaw "gramy" puste — program sam policzy limit.
  "posilki", gdy uwaga dotyczy konkretnego posiłku (np. obiad).
- "zamiana_makro" — prośba o mniej jednego makroskładnika kosztem drugiego (np. „mniej węglowodanów, w zamian więcej
  białka”): "z" i "na" (weglowodany | bialko | tluszcz). Nie podawaj gramów — policzy je program.
- "wskazowka" — stała, miękka preferencja dla układającego tydzień, której nie da się zapisać powyżej (np. „mniej dań
  z jajkami na kolację”, „lubimy kuchnię azjatycką”): "tekst" krótko po polsku.
Każda zasada ma "cytat" — fragment uwagi, z którego wynika.

Pole "dania": oceny konkretnych dań z planu — tylko gdy domownik wprost pisze, że danie mu (nie) smakuje albo żeby go
nie dawać. Uwaga o ilościach w daniu (np. za dużo ryżu) to NIE jest niechęć do dania. Nazwa dokładnie jak w planie.

Pole "do_poprawy": uwagi do działania programu, których nie da się załatwić zasadą diety (np. wygląd eksportu, listy
zakupów, przycisk, błąd) — krótko, rzeczowo, po polsku, jako zadanie dla twórcy aplikacji.

Pole "cofnij": id istniejących zasad, które domownik wprost odwołuje (np. „jednak białka jaj mogą być”).
Pole "podsumowanie": krótko po polsku, co zrozumiałeś — bez cyfr i liczebników (liczby pokaże program).
Nie dopisuj zasad, o które domownik nie prosił."""

HABITS_SYSTEM = """Analizujesz nawyki domowników w polskiej aplikacji do planowania jadłospisu (Porcja). Program podaje
zebrane sygnały: oceny dań, wymiany dań w planach (co odrzucili, co wybrali), ich uwagi z notatnika i życzenia.
Napisz:
- "obserwacje": krótkie wnioski o gustach i nawykach (np. „często wymieniacie kolacje z jajkami na kanapki z mięsem”),
  bez cyfr i liczebników; tylko to, co wynika z sygnałów. Siła wniosku ma odpowiadać liczbie sygnałów: przy kilku
  sygnałach pisz ostrożnie („na razie widać…”, „bywa, że…”), słowa „zawsze”, „konsekwentnie”, „unikacie” tylko przy
  wielu powtórzeniach. Jedna zamiana to nie wzorzec;
- "propozycje": zasady, które warto zaproponować domownikom (zakaz_produktu, limit_porcji, zamiana_makro, wskazowka) —
  tylko przy wyraźnym, powtarzającym się wzorcu i bez dublowania istniejących zasad; bez gramów (policzy je program).
  Zakaz całej kategorii (np. ryby) proponuj tylko, gdy domownicy wprost tak napisali albo wielokrotnie odrzucają takie
  dania i niczego z nich nie wybierają; przy słabszym wzorcu wybierz łagodną „wskazowka” (np. „rzadziej dania z rybą”);
- "do_poprawy": uwagi do działania programu wynikające z sygnałów (jeśli są).
Domownicy sami zdecydują, czy przyjąć propozycje."""


def _no_digits(value: str) -> str:
    if has_digits(value):
        raise ValueError("bez cyfr — liczby podaje program")
    return value


class RuleDraft(BaseModel):
    typ: Literal["zakaz_produktu", "limit_porcji", "zamiana_makro", "wskazowka"]
    produkt: str | None = Field(default=None, max_length=120, description="nazwa produktu słowami użytkownika")
    kategoria: Category | None = Field(default=None, description="kategoria z listy, gdy chodzi o cały rodzaj")
    osoby: list[str] = Field(default_factory=list, description="id osób; puste = wszyscy")
    posilki: list[Literal["sniadanie", "obiad", "kolacja", "przekaska"]] = Field(default_factory=list)
    gramy: dict[str, float] = Field(default_factory=dict, description="id osoby -> liczba podana przez użytkownika")
    z: MacroKey | None = None
    na: MacroKey | None = None
    tekst: str | None = Field(default=None, max_length=300)
    cytat: str = Field(default="", max_length=400)


class DishOpinion(BaseModel):
    danie: str = Field(max_length=200)
    ocena: Literal["lubie", "nie_lubie"]


class NoteAnalysis(BaseModel):
    podsumowanie: str = Field(max_length=600)
    zasady: list[RuleDraft] = Field(default_factory=list, max_length=8)
    dania: list[DishOpinion] = Field(default_factory=list, max_length=8)
    do_poprawy: list[str] = Field(default_factory=list, max_length=6)
    cofnij: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("podsumowanie")
    @classmethod
    def _plain(cls, v: str) -> str:
        return _no_digits(v)


class HabitAnalysis(BaseModel):
    obserwacje: list[str] = Field(default_factory=list, max_length=8)
    propozycje: list[RuleDraft] = Field(default_factory=list, max_length=4)
    do_poprawy: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("obserwacje")
    @classmethod
    def _plain(cls, v: list[str]) -> list[str]:
        return [_no_digits(x) for x in v]


def numbers_in(text: str) -> set[float]:
    return {float(m.replace(",", ".")) for m in NUMBER_RE.findall(text)}


def load_rules(raw: list[dict]) -> list[PreferenceRule]:
    out: list[PreferenceRule] = []
    for data in raw:
        try:
            out.append(PreferenceRule.model_validate(data))
        except ValidationError:
            continue  # uszkodzony wpis nie może zablokować planowania; widok pokaże tylko poprawne zasady
    return out


def apply_to_repo(repo: Repository, rules: list[PreferenceRule]) -> AppliedPreferences:
    """Nakłada aktywne zasady na wczytane dane (założenia + wskazówki dla układającego tydzień)."""
    applied = apply_preferences(repo.assumptions, rules, repo.products)
    repo.assumptions = applied.assumptions
    repo.preference_hints = list(applied.hints)
    return applied


class Notebook:
    def __init__(self, service: DietService):
        self.s = service

    # ------------------------------------------------------------------ zasady
    def rules(self) -> list[PreferenceRule]:
        return load_rules(self.s.store.pref_rules())

    def _changed(self) -> None:
        """Nowe zasady obowiązują od razu: przeładowanie danych, pul dań i pamięci silnika."""
        self.s.reload()

    def _guard(self) -> None:
        if self.s.jobs.busy():
            raise UserError("Poczekaj na koniec układania lub zmiany planu, a potem zmień preferencje.")

    @staticmethod
    def _same(a: PreferenceRule, b: PreferenceRule) -> bool:
        """Ta sama treść zasady (bez id, daty i źródła) — np. powtórzona uwaga."""
        skip = {"id", "created_at", "source", "note_id", "active"}
        return a.model_dump(exclude=skip) == b.model_dump(exclude=skip)

    def existing_same(self, rule: PreferenceRule) -> PreferenceRule | None:
        return next((r for r in self.rules() if r.active and self._same(r, rule)), None)

    def save_rule(self, rule: PreferenceRule) -> list[PreferenceRule]:
        """Zapisuje zasadę; zasada o tym samym celu (np. poprzedni limit ryżu dla tych osób) zostaje zastąpiona."""
        replaced = []
        for old in self.rules():
            if old.id != rule.id and old.target_key() == rule.target_key() and (
                    not old.persons or not rule.persons or set(old.persons) & set(rule.persons)):
                if rule.kind is RuleKind.CAP:
                    # nowy limit przejmuje tylko osoby, dla których go podano; reszta starego limitu zostaje
                    rest = {pid: g for pid, g in old.grams.items() if pid not in rule.grams}
                    if rest:
                        self.s.store.save_pref_rule(old.id, old.model_copy(update={"grams": rest}).to_data())
                        continue
                self.s.store.delete_pref_rule(old.id)
                replaced.append(old)
        self.s.store.save_pref_rule(rule.id, rule.to_data())
        return replaced

    def set_active(self, rule_id: str, active: bool) -> dict:
        with self.s._lock:
            self._guard()
            rule = next((r for r in self.rules() if r.id == rule_id), None)
            if rule is None:
                raise NotFound("Nie ma takiej zasady.")
            self.s.store.save_pref_rule(rule.id, rule.model_copy(update={"active": active}).to_data())
            self._changed()
        return self.view()

    def delete_rule(self, rule_id: str) -> dict:
        with self.s._lock:
            self._guard()
            if not self.s.store.delete_pref_rule(rule_id):
                raise NotFound("Nie ma takiej zasady.")
            self._changed()
        return self.view()

    def manual_rule(self, payload: dict) -> dict:
        """Zasada dodana ręcznie w notatniku (bez modelu językowego). Brakujące liczby liczy kod."""
        a = self.s.repo.assumptions
        data = {k: v for k, v in payload.items() if v not in (None, "", [], {})}
        kind = data.get("typ")
        if kind == RuleKind.MACRO.value and not data.get("gramy"):
            data["gramy"] = default_macro_grams(a, data.get("z") or "weglowodany", data.get("osoby") or None)
        if kind == RuleKind.CAP.value:
            grams = {pid: float(g) for pid, g in (data.get("gramy") or {}).items() if g not in (None, "")}
            persons = data.get("osoby") or [p.id for p in a.persons]
            for pid in persons:
                if pid not in grams:
                    cap = self._default_cap(data.get("produkt"), data.get("kategoria"), pid,
                                            [MealType(m) for m in data.get("posilki") or []])
                    if cap is not None:
                        grams[pid] = cap
            data["gramy"] = grams
        try:
            rule = PreferenceRule.model_validate({**data, "id": new_id(), "zrodlo": "dodane ręcznie",
                                                  "utworzono": datetime.now(UTC).isoformat()})
        except ValidationError as exc:
            raise UserError("Nie udało się dodać zasady: " + "; ".join(e["msg"] for e in exc.errors())) from None
        self._check_rule(rule)
        with self.s._lock:
            self._guard()
            self.save_rule(rule)
            self._changed()
        return self.view()

    def _check_rule(self, rule: PreferenceRule) -> None:
        ids = {p.id for p in self.s.repo.assumptions.persons}
        if rule.product_id and rule.product_id not in self.s.repo.products:
            raise UserError("Nie ma takiego produktu w bazie.")
        unknown = [p for p in [*rule.persons, *rule.grams] if p not in ids]
        if unknown:
            raise UserError("Nieznana osoba: " + ", ".join(unknown))

    # ------------------------------------------------------------------ liczby liczone przez kod
    def _matches(self, product_id: str, target_product: str | None, category: str | None) -> bool:
        if target_product:
            return product_id == target_product
        p = self.s.repo.products.get(product_id)
        return bool(p and category and p.category.value == category)

    def _default_cap(self, product_id: str | None, category: str | None, person_id: str,
                     meals: list[MealType]) -> float | None:
        """Limit „mniej niż dotąd”: największa porcja osoby w ostatnich planach × 0,85, w dół do kroku produktu."""
        repo = self.s.repo
        best = 0.0
        for rec in self.s.store.list(limit=RECENT_PLANS_FOR_CAP):
            for m in rec.plan.meals:
                if meals and m.meal not in meals:
                    continue
                for portion in m.portions:
                    if portion.person_id != person_id:
                        continue
                    for it in portion.items:
                        if self._matches(it.product_id, product_id, category):
                            best = max(best, it.grams)
        products = [repo.products[product_id]] if product_id in repo.products else \
            [p for p in repo.products.values() if category and p.category.value == category]
        if not products:
            return None
        if best <= 0:  # brak historii: limit produktu przeliczony na zapotrzebowanie osoby
            person = repo.assumptions.person(person_id)
            caps = [p.max_portion_g for p in products if p.max_portion_g]
            if not caps:
                return None
            best = max(caps) * person.energy_center / self.s.settings.reference_kcal
        step = max(min(p.step_g for p in products), 1.0)
        return float(max(step, math.floor(best * DEFAULT_CAP_SHARE / step) * step))

    # ------------------------------------------------------------------ uwagi
    def _context(self, plan_id: str | None) -> str:
        s = self.s
        a, products = s.repo.assumptions, s.repo.products
        lines = ["Osoby (w tej kolejności są też kolumny tabel w planie): "
                 + ", ".join(f"{p.name} (id: {p.id})" for p in a.persons)
                 + (". Użytkownik aplikacji to zwykle mężczyzna, żona to kobieta."
                    if {p.id for p in a.persons} == {"kobieta", "mezczyzna"} else "."),
                 "Kategorie produktów: " + ", ".join(f"{c.value} ({CATEGORY_LABELS_PL[c].lower()})" for c in Category)]
        rules = self.rules()
        if rules:
            lines.append("ISTNIEJĄCE ZASADY (id — opis):")
            lines += [f"- {r.id} — {describe(r, a, products)}{'' if r.active else ' [wyłączona]'}" for r in rules]
        rec = (s.store.get(plan_id) if plan_id else None) or s.store.active()
        if rec is not None:
            dishes = s.record_repo(rec).dishes
            lines.append(f"AKTUALNY PLAN: {rec.title}")
            for m in rec.plan.meals:
                d = dishes.get(m.dish_id)
                lines.append(f"- {MEAL_LABELS_PL[m.meal].lower()}, dzień {int(m.day) + 1}: {d.name if d else m.dish_id}")
        notes = s.store.pref_notes(limit=6)
        if notes:
            lines.append("WCZEŚNIEJSZE UWAGI (najnowsze):")
            lines += [f"- {n['text'][:300]}" for n in notes]
        return "\n".join(lines)

    def _draft_rule(self, d: RuleDraft, text: str, note_id: str | None, source: str,
                    user_numbers: bool = True) -> tuple[PreferenceRule | None, list[str]]:
        """Szkic modelu → zasada sprawdzona przez kod. Zwraca (zasada albo None, uwagi dla domowników)."""
        s = self.s
        a = s.repo.assumptions
        ids = [p.id for p in a.persons]
        notes: list[str] = []
        persons = [p for p in d.osoby if p in ids]
        meals = [MealType(m) for m in d.posilki]
        base = {"id": new_id(), "typ": d.typ, "osoby": persons if len(persons) < len(ids) else [],
                "posilki": [m.value for m in meals], "zrodlo": source, "utworzono": datetime.now(UTC).isoformat(),
                "notatka": note_id}
        quote = f"„{d.cytat.strip()}”" if d.cytat.strip() else "zasada"
        if d.typ in (RuleKind.BAN.value, RuleKind.CAP.value):
            if d.kategoria is not None:
                base["kategoria"] = d.kategoria.value
            elif d.produkt:
                product_id, names = self._resolve_product(d.produkt)
                if product_id is None:
                    return None, [f"{quote}: nie rozpoznałem produktu „{d.produkt}”"
                                  + (f" (może: {names}?) — dodaj zasadę ręcznie" if names else "")]
                base["produkt"] = product_id
            else:
                return None, [f"{quote}: brak produktu albo kategorii"]
        if d.typ == RuleKind.CAP.value:
            allowed = numbers_in(text) if user_numbers else set()
            grams = {pid: float(g) for pid, g in d.gramy.items() if pid in ids and float(g) in allowed}
            if len(grams) < len([pid for pid in d.gramy if pid in ids]):
                notes.append(f"{quote}: pominąłem liczby, których nie było w uwadze — limit policzył program")
            for pid in (persons or ids):
                if pid not in grams:
                    cap = self._default_cap(base.get("produkt"), base.get("kategoria"), pid, meals)
                    if cap is not None:
                        grams[pid] = cap
            if not grams:
                return None, [f"{quote}: nie umiem policzyć limitu porcji — podaj gramy"]
            base["gramy"] = grams
        if d.typ == RuleKind.MACRO.value:
            if not d.z or not d.na or d.z == d.na:
                return None, [f"{quote}: nie wiem, który makroskładnik zmniejszyć, a który zwiększyć"]
            base.update({"z": d.z, "na": d.na, "gramy": default_macro_grams(a, d.z, persons or None)})
        if d.typ == RuleKind.HINT.value:
            if not (d.tekst or "").strip():
                return None, [f"{quote}: pusta wskazówka"]
            base["tekst"] = d.tekst.strip()
        try:
            return PreferenceRule.model_validate(base), notes
        except ValidationError as exc:
            return None, [f"{quote}: " + "; ".join(e["msg"] for e in exc.errors())]

    def _resolve_product(self, name: str) -> tuple[str | None, str]:
        """Produkt z bazy dla nazwy z uwagi — tylko pewne dopasowanie. Zwraca (id albo None, podpowiedzi)."""
        matcher = ProductMatcher(self.s.repo.products)
        stripped = " ".join(w for w in name.split() if normalize(w) not in PRODUCT_FILLERS)
        match = None
        for text in dict.fromkeys(t for t in (name, stripped) if t.strip()):
            match = matcher.match(text)
            if match.status is MatchStatus.SURE and match.product_id:
                return match.product_id, ""
            # odmiana („białek jaj”) daje dopasowanie rozmyte — przyjmujemy je tylko, gdy jest jedynym i wyraźnym
            if len(match.candidates) == 1 and match.candidates[0].score >= FUZZY_SINGLE_MIN:
                return match.candidates[0].product_id, ""
        return None, ", ".join(c.name for c in (match.candidates[:3] if match else []))

    def _match_dish(self, name: str, plan_dishes: list[str]) -> str | None:
        dishes = self.s.repo.dishes
        key = normalize(name)

        def best(ids: list[str], threshold: float) -> str | None:
            scored = sorted({(max(fuzz.token_set_ratio(key, normalize(dishes[i].name)),
                                  fuzz.ratio(key, normalize(dishes[i].description or ""))), i) for i in ids if i in dishes},
                            reverse=True)
            if not scored or scored[0][0] < threshold:
                return None
            if len(scored) > 1 and scored[0][0] - scored[1][0] < 3 and scored[1][1] != scored[0][1]:
                return None  # dwa podobne dania — nie zgadujemy
            return scored[0][1]

        return best(plan_dishes, 80) or best(sorted(self.s.repo.approved_dishes()), 92)

    def add_comment(self, text: str, plan_id: str | None = None, channel: str = "aplikacja") -> dict:
        if current_scope() is not None:
            return self._add_comment(text, plan_id, channel)
        with task_scope():  # jedna uwaga = jedno polecenie z limitem kosztu AI
            return self._add_comment(text, plan_id, channel)

    def _add_comment(self, text: str, plan_id: str | None = None, channel: str = "aplikacja") -> dict:
        text = (text or "").strip()
        if len(text) < 3:
            raise UserError("Napisz uwagę (np. „za dużo ryżu na obiad, wolę więcej mięsa”).")
        text = text[:MAX_NOTE_CHARS]
        s = self.s
        rec = (s.store.get(plan_id) if plan_id else None) or s.store.active()
        llm = s.llm()
        if llm is None:
            note_id = s.store.add_pref_note(text, plan_id=rec.id if rec else None, channel=channel,
                                            analysis={"summary": None, "problems": [
                                                f"Bez modelu językowego ({s._llm_error}) zapisałem samą notatkę — "
                                                "zasadę możesz dodać ręcznie poniżej."]})
            return {"note_id": note_id, **self.view()}
        prompt = self._context(rec.id if rec else None) + "\n\nUWAGA DOMOWNIKA:\n" + text
        try:
            res = llm.complete_json("notatnik_uwaga", NOTE_SYSTEM, prompt, NoteAnalysis, temperature=0.0,
                                    max_tokens=1500)
        except LLMError as exc:
            raise UserError(f"Nie udało się przeanalizować uwagi: {exc}") from None
        out = res.value
        with s._lock:
            self._guard()
            note_id = s.store.add_pref_note(text, plan_id=rec.id if rec else None, channel=channel)
            analysis = self._apply_analysis(out, text, note_id, rec)
            analysis["cost_usd"] = round(res.cost_usd, 5)
            s.store.set_pref_note_analysis(note_id, analysis)
            self._changed()
        return {"note_id": note_id, "analysis": analysis, **self.view()}

    def _apply_analysis(self, out: NoteAnalysis, text: str, note_id: str, rec) -> dict:
        s = self.s
        a, products = s.repo.assumptions, s.repo.products
        source = f"uwaga z {date.today():%d.%m.%Y}"
        added, removed, problems, ratings, kept = [], [], [], [], []
        existing = {r.id: r for r in self.rules()}
        for rid in out.cofnij:
            rule = existing.get(rid)
            if rule is not None and s.store.delete_pref_rule(rid):
                removed.append(describe(rule, a, products))
        for draft in out.zasady:
            rule, notes = self._draft_rule(draft, text, note_id, source)
            problems += notes
            if rule is None:
                continue
            if self.existing_same(rule) is not None:
                kept.append(describe(rule, a, products))
                continue
            for old in self.save_rule(rule):
                removed.append(describe(old, a, products) + " (zastąpiona)")
            added.append({"id": rule.id, "kind": rule.kind.value, "description": describe(rule, a, products)})
        plan_dishes = [m.dish_id for m in rec.plan.meals] if rec else []
        for op in out.dania:
            dish_id = self._match_dish(op.danie, plan_dishes)
            if dish_id is None:
                problems.append(f"nie rozpoznałem dania „{op.danie}” — oceń je kciukiem w planie")
                continue
            rating = 1 if op.ocena == "lubie" else -1
            s.store.rate_dish(dish_id, rating)
            ratings.append({"dish_id": dish_id, "name": s.repo.dishes[dish_id].name, "rating": rating})
        program = []
        open_notes = [n["text"] for n in s.store.program_notes() if n["status"] == "nowa"]
        for item in out.do_poprawy:
            item = item.strip()[:500]
            if not item:
                continue
            if any(fuzz.token_set_ratio(normalize(item), normalize(x)) >= SAME_NOTE_SCORE for x in open_notes):
                kept.append(f"uwaga do programu już jest na liście: {item}")
                continue
            s.store.add_program_note(item, note_id)
            open_notes.append(item)
            program.append(item)
        summary = SUMMARY_PREFIX.sub("", out.podsumowanie.strip())  # widok sam pisze „Zrozumiałem:”
        return {"summary": summary[:1].upper() + summary[1:], "rules_added": added, "rules_kept": kept,
                "rules_removed": removed,
                "ratings": ratings, "program_notes": program, "problems": problems}

    # ------------------------------------------------------------------ nawyki
    def _swaps(self) -> list[tuple[str, str, MealType]]:
        """(danie odrzucone, danie wybrane, posiłek) ze wszystkich ręcznych zamian w historii planów."""
        out = []
        store = self.s.store
        for rec in store.list(limit=-1):
            if rec.source != "zamiana" or not rec.parent_id:
                continue
            parent = store.get(rec.parent_id)
            if parent is None:
                continue
            before = {(int(m.day), m.meal): m.dish_id for m in parent.plan.meals}
            for m in rec.plan.meals:
                old = before.get((int(m.day), m.meal))
                if old and old != m.dish_id and (old, m.dish_id, m.meal) not in out:
                    out.append((old, m.dish_id, m.meal))
        return out

    def habit_signals(self) -> dict:
        """Sygnały zebrane przez kod: oceny, wymiany dań, uwagi i życzenia (z liczbami policzonymi tutaj)."""
        s = self.s
        repo, pools = s.repo, s.pools()
        dishes, profiles = repo.dishes, pools.profiles

        def label(dish_id: str, meal: MealType | None = None) -> str:
            d, prof = dishes.get(dish_id), profiles.get(dish_id)
            if d is None:
                return dish_id
            extra = []
            if meal is not None:
                extra.append(MEAL_LABELS_PL[meal].lower())
            if prof and prof.protein_sources:
                extra.append("białko: " + ", ".join(prof.protein_sources))
            if prof and prof.carb_sources:
                extra.append("węgle: " + ", ".join(prof.carb_sources))
            extra.append(f"kuchnia {d.cuisine}")
            return f"{d.name} ({'; '.join(extra)})"

        liked = [d for d, r in repo.dish_ratings.items() if r > 0 and d in dishes]
        disliked = [d for d, r in repo.dish_ratings.items() if r < 0 and d in dishes]
        swaps = self._swaps()
        out_src, in_src = Counter(), Counter()
        for old, new, _ in swaps:
            out_src.update(profiles[old].protein_sources if old in profiles else [])
            in_src.update(profiles[new].protein_sources if new in profiles else [])
        lines: list[str] = []
        if liked:
            lines.append("Polubione dania: " + "; ".join(label(d) for d in liked))
        if disliked:
            lines.append("Nielubiane dania: " + "; ".join(label(d) for d in disliked))
        if swaps:
            lines.append(f"Ręczne zamiany dań ({len(swaps)}):")
            lines += [f"- odrzucone: {label(o, m)} → wybrane: {label(n)}" for o, n, m in swaps[:25]]
            if out_src:
                lines.append("Źródła białka w odrzuconych daniach: " + ", ".join(f"{k} {v}" for k, v in out_src.most_common()))
            if in_src:
                lines.append("Źródła białka w wybranych daniach: " + ", ".join(f"{k} {v}" for k, v in in_src.most_common()))
        notes = s.store.pref_notes(limit=15)
        if notes:
            lines.append("Uwagi z notatnika:")
            lines += [f"- {n['text'][:300]}" for n in notes]
        wishes = []
        for rec in s.store.list(limit=40):
            if rec.wishes and rec.wishes not in wishes:
                wishes.append(rec.wishes)
        if wishes:
            lines.append("Życzenia do planów:")
            lines += [f"- {w[:200]}" for w in wishes[:10]]
        counts = {"liked": len(liked), "disliked": len(disliked), "swaps": len(swaps), "notes": len(notes),
                  "wishes": len(wishes)}
        return {"lines": lines, "counts": counts}

    def analyze_habits(self) -> dict:
        if current_scope() is not None:
            return self._analyze_habits()
        with task_scope():
            return self._analyze_habits()

    def _analyze_habits(self) -> dict:
        s = self.s
        signals = self.habit_signals()
        c = signals["counts"]
        stats = [f"{c['liked']} {plural_pl(c['liked'], 'polubione danie', 'polubione dania', 'polubionych dań')}",
                 f"{c['disliked']} {plural_pl(c['disliked'], 'nielubiane danie', 'nielubiane dania', 'nielubianych dań')}",
                 f"{c['swaps']} {plural_pl(c['swaps'], 'zamiana', 'zamiany', 'zamian')} dań",
                 f"{c['notes']} {plural_pl(c['notes'], 'uwaga', 'uwagi', 'uwag')} w notatniku"]
        result: dict = {"created_at": datetime.now(UTC).isoformat(), "stats": stats, "observations": [],
                        "proposals": [], "program_notes": [], "problems": []}
        if sum(c.values()) == 0:
            result["problems"].append("Za mało danych — oceniaj dania, wymieniaj je w planie i pisz uwagi, "
                                      "a analiza będzie miała z czego wyciągać wnioski.")
            s.store.set_setting(HABITS_KEY, json.dumps(result, ensure_ascii=False))
            return self.view()
        llm = s.llm()
        if llm is None:
            result["problems"].append(f"Wnioski pisze model językowy ({s._llm_error}) — poniżej same liczby.")
            s.store.set_setting(HABITS_KEY, json.dumps(result, ensure_ascii=False))
            return self.view()
        a, products = s.repo.assumptions, s.repo.products
        context = [self._context(None), "", "LICZBA SYGNAŁÓW: " + ", ".join(stats), "",
                   "SYGNAŁY ZEBRANE PRZEZ PROGRAM:", *signals["lines"]]
        try:
            res = llm.complete_json("notatnik_nawyki", HABITS_SYSTEM, "\n".join(context), HabitAnalysis,
                                    temperature=0.2, max_tokens=1500)
        except LLMError as exc:
            raise UserError(f"Nie udało się przeanalizować nawyków: {exc}") from None
        result["observations"] = [o.strip() for o in res.value.obserwacje if o.strip()]
        for draft in res.value.propozycje:
            # propozycje z analizy nie niosą liczb modelu — limity i zamiany liczy kod
            rule, notes = self._draft_rule(draft.model_copy(update={"gramy": {}}), "", None, "analiza nawyków",
                                           user_numbers=False)
            result["problems"] += notes
            if rule is not None:
                result["proposals"].append({"id": rule.id, "rule": rule.to_data(),
                                            "description": describe(rule, a, products), "applied": False})
        result["program_notes"] = [x.strip()[:500] for x in res.value.do_poprawy if x.strip()]
        result["cost_usd"] = round(res.cost_usd, 5)
        s.store.set_setting(HABITS_KEY, json.dumps(result, ensure_ascii=False))
        return self.view()

    def habits(self) -> dict | None:
        raw = self.s.store.get_setting(HABITS_KEY)
        try:
            return json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return None

    def apply_proposal(self, proposal_id: str) -> dict:
        habits = self.habits()
        prop = next((p for p in (habits or {}).get("proposals", []) if p["id"] == proposal_id), None)
        if prop is None:
            raise UserError("Ta propozycja już nie istnieje — przeanalizuj nawyki jeszcze raz.")
        if prop.get("applied"):
            return self.view()
        rule = PreferenceRule.model_validate({**prop["rule"], "utworzono": datetime.now(UTC).isoformat()})
        self._check_rule(rule)
        with self.s._lock:
            self._guard()
            self.save_rule(rule)
            prop["applied"] = True
            self.s.store.set_setting(HABITS_KEY, json.dumps(habits, ensure_ascii=False))
            self._changed()
        return self.view()

    def add_program_note_from_habits(self, index: int) -> dict:
        habits = self.habits() or {}
        items = habits.get("program_notes", [])
        if not 0 <= index < len(items):
            raise NotFound("Nie ma takiej uwagi.")
        self.s.store.add_program_note(items.pop(index))
        self.s.store.set_setting(HABITS_KEY, json.dumps(habits, ensure_ascii=False))
        return self.view()

    def set_program_note(self, note_id: str, status: str) -> dict:
        if status not in ("nowa", "zrobione", "usunieta"):
            raise UserError("Nieznany stan uwagi.")
        if not self.s.store.set_program_note_status(note_id, status):
            raise NotFound("Nie ma takiej uwagi.")
        return self.view()

    # ------------------------------------------------------------------ widok i eksport
    def _targets(self) -> list[dict]:
        """Widełki z założeń i po nałożeniu zasad (tylko te, które się różnią)."""
        raw = self.s.raw_assumptions
        eff = self.s.repo.assumptions
        out = []
        for p in eff.persons:
            try:
                before = raw.person(p.id).targets
            except KeyError:
                continue
            for key in ("protein", "fat", "carbs"):
                b, e = before.band(key), p.targets.band(key)
                if (b.min, b.max, b.center) != (e.min, e.max, e.center):
                    out.append({"person": p.name, "metric": {"protein": "Białko", "fat": "Tłuszcz",
                                                             "carbs": "Węglowodany"}[key],
                                "before": f"{fmt_num(b.min)}–{fmt_num(b.max)} g (cel {fmt_num(b.center)})",
                                "after": f"{fmt_num(e.min)}–{fmt_num(e.max)} g (cel {fmt_num(e.center)})"})
        return out

    def view(self) -> dict:
        s = self.s
        a, products = s.repo.assumptions, s.repo.products
        state = s.preferences
        rules = [{"id": r.id, "kind": r.kind.value, "kind_label": RULE_KIND_PL[r.kind],
                  "description": describe(r, a, products), "active": r.active, "source": r.source,
                  "created_at": r.created_at, "data": r.to_data()} for r in self.rules()]
        status = s.llm_ready()
        return {
            "rules": rules,
            "effects": state.effects if state else [],
            "problems": state.problems if state else [],
            "targets": self._targets(),
            "notes": s.store.pref_notes(limit=60),
            "program_notes": s.store.program_notes(),
            "habits": self.habits(),
            "llm_available": status,
            "persons": [{"id": p.id, "name": p.name} for p in a.persons],
            "categories": [{"id": c.value, "name": CATEGORY_LABELS_PL[c]} for c in Category],
            "meals": [{"id": m.value, "name": MEAL_LABELS_PL[m]} for m in (MealType.BREAKFAST, MealType.LUNCH,
                                                                             MealType.SUPPER)],
            "macros": [{"id": k, "name": v} for k, v in MACRO_NOM_PL.items()],
        }

    def export_text(self) -> str:
        v = self.view()
        lines = ["NOTATNIK PREFERENCJI — PORCJA", f"Zapisano: {datetime.now():%d.%m.%Y %H:%M}", ""]
        lines.append("ZASADY STOSOWANE W PLANACH")
        active = [r for r in v["rules"] if r["active"]]
        lines += [f"- {r['description']} ({r['source']})" for r in active] or ["- brak"]
        off = [r for r in v["rules"] if not r["active"]]
        if off:
            lines += ["", "WYŁĄCZONE ZASADY", *[f"- {r['description']}" for r in off]]
        if v["targets"]:
            lines += ["", "WIDEŁKI PO ZASADACH"]
            lines += [f"- {t['person']}, {t['metric'].lower()}: {t['before']} → {t['after']}" for t in v["targets"]]
        lines += ["", "DO POPRAWY W PROGRAMIE"]
        lines += [f"- [{n['status']}] {n['text']} ({n['created_at'][:10]})" for n in v["program_notes"]] or ["- brak"]
        habits = v["habits"]
        if habits and habits.get("observations"):
            lines += ["", f"NAWYKI (analiza z {habits['created_at'][:10]})", *[f"- {o}" for o in habits["observations"]]]
        lines += ["", "HISTORIA UWAG"]
        for n in v["notes"]:
            lines.append(f"{n['created_at'][:16].replace('T', ' ')} — {n['text']}")
            summary = (n.get("analysis") or {}).get("summary")
            if summary:
                lines.append(f"  Odczytano: {summary}")
        return "\n".join(lines).rstrip() + "\n"
