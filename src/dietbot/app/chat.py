"""Czat z asystentem: model językowy rozpoznaje intencję, a program wykonuje akcję i podaje wszystkie liczby.

Model NIE podaje żadnych liczb (walidacja odrzuca cyfry i liczebniki). Pytania o wartości odżywcze obsługuje
program na podstawie wyników weryfikatora, zamiany dań sprawdza silnik, zmiany założeń wymagają potwierdzenia.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..checker.verify import nutrition_of
from ..domain.assumptions import DAY_KEYS_PL, DAY_LABELS_PL, Weekday, parse_day
from ..domain.dish import MEAL_LABELS_PL, MealType
from ..food.matcher import MatchStatus, ProductMatcher
from ..llm.client import LLMError
from ..measures import household
from ..storage.yamlio import load_yaml, to_plain
from ..textfmt import fmt_num
from ..textnorm import normalize
from .jobs import UserError
from .service import DietService, composition_from_plan
from .store import PlanRecord

CHAT_NUMBER_WORDS = {"dwa", "dwie", "dwóch", "dwoma", "dwukrotnie", "trzy", "trzech", "trzema", "trzykrotnie", "cztery",
                     "czterech", "pięć", "pięciu", "sześć", "sześciu", "siedem", "siedmiu", "osiem", "dziewięć",
                     "dziesięć", "sto", "tysiąc", "procent"}
WEEKDAY_WORDS = {"dzis": "dzis", "dzisiaj": "dzis", "jutro": "jutro"}
MAX_PENDING_PROPOSALS = 20  # propozycje zmian założeń czekające na potwierdzenie (najstarsze wygasają)
BOOL_PATH_SUFFIXES = ("bez_powtorek",)  # ścieżki logiczne, także gdy klucza jeszcze nie ma w pliku

ChangeOp = Literal["ustaw", "zwieksz", "zmniejsz", "dodaj", "usun"]


class AssumptionChange(BaseModel):
    sciezka: str = Field(description="ścieżka z listy DOZWOLONE ZMIANY, np. osoby.kobieta.cele_dzienne.kcal.max")
    operacja: ChangeOp = Field(description="ustaw | zwieksz | zmniejsz (liczby) | dodaj | usun (listy)")
    wartosc: float | str | bool | None = Field(default=None, description="liczba, tak/nie albo nazwa produktu/kuchni")


class FactRequest(BaseModel):
    rodzaj: Literal["dzien", "tydzien", "posilek", "produkt"]
    dzien: str | None = Field(default=None, description="poniedzialek…niedziela, dzis albo jutro")
    posilek: str | None = Field(default=None, description="sniadanie | obiad | kolacja")
    osoba: str | None = Field(default=None, description="id osoby albo brak = obie")
    produkt: str | None = Field(default=None, description="nazwa produktu (dla rodzaju produkt)")


class ChatAction(BaseModel):
    typ: Literal["odpowiedz", "pokaz", "generuj", "wymysl_dania", "zamien", "zmien_zalozenia"]
    tekst: str = Field(description="krótka odpowiedź po polsku, BEZ cyfr i liczebników — liczby dopisze program")
    fakty: list[FactRequest] = Field(default_factory=list, max_length=6)
    zyczenia: str | None = Field(default=None, description="dla generuj i wymysl_dania: życzenia/prośba słowami użytkownika")
    wymysl_nowe: bool = Field(default=False, description="dla generuj: true, gdy użytkownik chce NOWYCH, wymyślonych dań")
    ile_dan: int | None = Field(default=None, ge=1, le=12, description="dla wymysl_dania: ile dań wymyślić")
    dzien: str | None = Field(default=None, description="dla zamien: dzień")
    posilek: str | None = Field(default=None, description="dla zamien: sniadanie | obiad | kolacja")
    opis_dania: str | None = Field(default=None, description="dla zamien: na co zamienić, słowami użytkownika")
    zmiany: list[AssumptionChange] = Field(default_factory=list, max_length=8)

    @field_validator("tekst")
    @classmethod
    def _no_numbers(cls, v: str) -> str:
        words = set(re.findall(r"[a-ząćęłńóśźż]+", v.lower()))
        if any(ch.isdigit() for ch in v) or words & CHAT_NUMBER_WORDS:
            raise ValueError("tekst nie może zawierać cyfr ani liczebników — wszystkie liczby podaje program "
                             "(użyj pola fakty albo zmiany)")
        return v


class DishPick(BaseModel):
    danie: str | None = Field(description="id dania z listy albo null, gdy nic nie pasuje")
    uzasadnienie: str


INVENT_REQUEST = re.compile(r"\b(wymysl\w*|stworz\w* (przepis|dani\w*)|dodaj\w* (przepis|dani\w*)|nowe dani\w*)\b")

SYSTEM = """Jesteś asystentem w polskiej aplikacji do planowania tygodniowego jadłospisu dla dwóch osób.
Liczby (kalorie, makroskładniki, gramatury) liczy deterministyczny program — Ty ich NIGDY nie podajesz ani nie liczysz.
Twoje zadanie: zrozumieć wiadomość i wybrać jedną akcję:
- "pokaz" — pytanie o wartości, sumy, gramatury, gdzie jest produkt: wypełnij "fakty", a program dopisze liczby.
- "zamien" — prośba o zmianę dania w konkretnym posiłku: podaj dzien, posilek i opis_dania (czego szuka użytkownik).
- "generuj" — prośba o nowy plan tygodnia: w "zyczenia" przepisz życzenia (np. więcej ryb, bez jajek w piątek).
  Jeśli użytkownik chce, żeby dania zostały WYMYŚLONE (np. „resztę wymyśl”, „coś nowego”), ustaw wymysl_nowe: true.
- "wymysl_dania" — prośba o NOWE danie albo dania do biblioteki, bez planu: „wymyśl trzy kolacje z krewetkami”,
  „wymyśl mi naleśniki z serem”, „stwórz przepis na…”, „dodaj danie…”. Nawet gdy użytkownik opisuje szczegóły
  („ciasto jak u babci”), NIE opisuj przepisu w tekście — wybierz "wymysl_dania", a w "zyczenia" przepisz całą prośbę
  ze szczegółami i posiłkiem. "ile_dan": liczba dań z prośby; jedno konkretne danie → jeden (domyślnie sześć).
- "zmien_zalozenia" — trwała zmiana założeń (cele, widełki, zakazy, liczba słodkich kolacji…): tylko ścieżki z listy
  DOZWOLONE ZMIANY; przy liczbach wybierz "ustaw" (nowa wartość) albo "zwieksz"/"zmniejsz" (o ile). Prośba typu
  „zmniejsz kalorie o sto” dotyczy CAŁYCH widełek (ścieżka bez .min/.cel/.max). Program pokaże różnice i poprosi
  użytkownika o potwierdzenie.
- "odpowiedz" — rozmowa, porady kulinarne i wyjaśnienia działania aplikacji, gdy użytkownik o nic nie prosi
  (nie wymyśla dań, nie zmienia planu ani założeń).
Pole "tekst": krótko i życzliwie po polsku, bez cyfr i liczebników. Nie obiecuj, że coś już zrobiłeś — program wykona akcję."""


def _person_ids(service: DietService) -> dict[str, str]:
    """Nazwy i synonimy osób -> id (np. żona -> kobieta)."""
    out = {}
    for p in service.repo.assumptions.persons:
        out[normalize(p.id)] = p.id
        out[normalize(p.name)] = p.id
    if "kobieta" in out.values():
        for w in ("zona", "zony", "zonie", "ona", "zone"):
            out.setdefault(w, "kobieta")
    if "mezczyzna" in out.values():
        for w in ("maz", "meza", "mezowi", "ja", "mnie", "on"):
            out.setdefault(w, "mezczyzna")
    return out


def allowed_paths(service: DietService) -> list[tuple[str, str]]:
    a = service.repo.assumptions
    rows: list[tuple[str, str]] = []
    for p in a.persons:
        for key, label in (("kcal", "energia"), ("bialko", "białko"), ("tluszcz", "tłuszcz"), ("weglowodany", "węglowodany")):
            rows.append((f"osoby.{p.id}.cele_dzienne.{key}", f"{p.name}: {label} — całe widełki (min, cel i max razem)"))
            for bound in ("min", "cel", "max"):
                rows.append((f"osoby.{p.id}.cele_dzienne.{key}.{bound}", f"{p.name}: {label} — {bound}"))
        rows.append((f"osoby.{p.id}.kotwica_bialka_na_posilek.min", f"{p.name}: minimum białka na posiłek"))
        rows.append((f"osoby.{p.id}.kotwica_bialka_na_posilek.max", f"{p.name}: maksimum białka na posiłek"))
        rows.append((f"osoby.{p.id}.wyklucza_produkty", f"{p.name}: produkty, których nie je (lista)"))
    rows += [
        ("tydzien.posilki_20_procent.min", "minimum posiłków z puli 20% w tygodniu"),
        ("tydzien.posilki_20_procent.max", "maksimum posiłków z puli 20% w tygodniu"),
        ("tydzien.slodkie_kolacje_max", "maksimum słodkich kolacji w tygodniu"),
        ("tydzien.bez_powtorek", "czy dania mają się nie powtarzać (tak/nie)"),
        ("dzien.przekaski.max_na_osobe_dziennie", "maksimum przekąsek bilansujących dziennie na osobę"),
        ("zakazy.produkty", "produkty zakazane dla obu osób (lista)"),
        ("kuchnie", "kuchnie brane pod uwagę (lista)"),
    ]
    return rows


class ChatAssistant:
    def __init__(self, service: DietService, channel: str = "aplikacja"):
        self.s = service
        self.channel = channel

    # ------------------------------------------------------------------ wejście
    def handle(self, text: str, plan_id: str | None = None) -> dict:
        text = text.strip()
        rec = (self.s.store.get(plan_id) if plan_id else None) or self.s.store.active()
        if rec is not None:
            rec = self.s.checked_record(rec)
        self.s.store.add_message("uzytkownik", text, plan_id=rec.id if rec else None, channel=self.channel)
        llm = self.s.llm()
        if llm is None:
            return self._reply("Czat potrzebuje modelu językowego, a nie jest on dostępny "
                               f"({self.s._llm_error}). Plan, zamiany i założenia działają w zakładkach aplikacji.", rec)
        prompt = self._context(rec) + "\n\nWIADOMOŚĆ UŻYTKOWNIKA:\n" + text
        try:
            res = llm.complete_json("czat", SYSTEM, prompt, ChatAction, temperature=0.2, max_tokens=1200)
        except LLMError as exc:
            return self._reply(f"Nie udało się porozmawiać z modelem językowym: {exc}", rec)
        action = res.value
        if action.typ == "odpowiedz" and INVENT_REQUEST.search(normalize(text)):
            # model czasem opowiada przepis zamiast go wymyślić do biblioteki — prośba „wymyśl/stwórz/dodaj danie” wygrywa
            action = action.model_copy(update={"typ": "wymysl_dania", "zyczenia": text, "ile_dan": action.ile_dan or 2,
                                               "tekst": "Wymyślam to danie z produktów z bazy — silnik i kucharz je sprawdzą."})
        try:
            if action.typ == "pokaz":
                return self._show(action, rec)
            if action.typ == "generuj":
                job = self.s.generate(wishes=action.zyczenia or text, channel=self.channel, invent=action.wymysl_nowe)
                note = " Najpierw wymyślę nowe dania, potem ułożę tydzień — to potrwa kilka minut." if action.wymysl_nowe else ""
                return self._reply((action.tekst or "Układam nowy plan tygodnia.") + note, rec,
                                   {"job_id": job.id, "kind": "generowanie"})
            if action.typ == "wymysl_dania":
                job = self.s.invent(action.zyczenia or text, action.ile_dan or 6, channel=self.channel)
                return self._reply(action.tekst or "Wymyślam nowe dania.", rec, {"job_id": job.id, "kind": "wymyslanie"})
            if action.typ == "zamien":
                return self._swap(action, rec, text)
            if action.typ == "zmien_zalozenia":
                return self._propose(action, rec)
        except UserError as exc:
            return self._reply(f"{action.tekst}\n\n⚠️ {exc}" if action.tekst else f"⚠️ {exc}", rec)
        except KeyError as exc:  # plan liczony dla osób lub dań, których już nie ma (zmienione założenia/biblioteka)
            return self._reply(f"⚠️ Ten plan nie pasuje do obecnych założeń lub biblioteki dań ({exc}) — "
                               "przelicz go w zakładce Tydzień albo ułóż nowy.", rec)
        return self._reply(action.tekst, rec)

    def _reply(self, text: str, rec: PlanRecord | None, data: dict | None = None) -> dict:
        mid = self.s.store.add_message("asystent", text, plan_id=rec.id if rec else None, channel=self.channel,
                                       data=data)
        return {"id": mid, "role": "asystent", "text": text, "data": data}

    def _context(self, rec: PlanRecord | None) -> str:
        s = self.s
        repo = s.record_repo(rec) if rec else s.repo
        a = repo.assumptions
        today = date.today()
        lines = [f"Dziś: {DAY_LABELS_PL[Weekday(today.weekday())].lower()}, {today:%d.%m.%Y}.",
                 "Osoby: " + ", ".join(f"{p.name} (id: {p.id})" for p in a.persons) +
                 ". Użytkownik to mężczyzna, żona to kobieta."]
        if rec is None:
            lines.append("Nie ma jeszcze żadnego planu tygodnia.")
        else:
            idx = s.today_index(rec)
            lines.append(f"Aktualny plan: {rec.title}" + (f" (dziś to {DAY_LABELS_PL[Weekday(idx)].lower()} planu)"
                                                          if idx is not None else ""))
            dishes = repo.dishes
            for day in rec.plan.days:
                meals = [m for m in rec.plan.meals if m.day == day]
                parts = []
                for m in sorted(meals, key=lambda m: a.day.meals.index(m.meal) if m.meal in a.day.meals else 9):
                    d = dishes.get(m.dish_id)
                    flags = [f for f, on in (("pula 20%", d and d.is_treat), ("słodkie", d and d.is_sweet)) if on]
                    parts.append(f"{MEAL_LABELS_PL[m.meal].lower()}: {d.name if d else m.dish_id}"
                                 + (f" [{', '.join(flags)}]" if flags else ""))
                lines.append(f"- {DAY_LABELS_PL[day]}: " + "; ".join(parts))
        lines.append("\nDOZWOLONE ZMIANY ZAŁOŻEŃ (ścieżka — znaczenie):")
        lines += [f"- {p} — {d}" for p, d in allowed_paths(s)]
        history = s.store.messages(self.channel, limit=7)[:-1]
        if history:
            lines.append("\nOSTATNIE WIADOMOŚCI:")
            lines += [f"{m['role']}: {m['text'][:300]}" for m in history[-6:]]
        return "\n".join(lines)

    # ------------------------------------------------------------------ fakty (liczby z weryfikatora)
    def _day(self, value: str | None, rec: PlanRecord) -> Weekday | None:
        if not value:
            return None
        key = normalize(value)
        if key in WEEKDAY_WORDS:
            shift = 1 if WEEKDAY_WORDS[key] == "jutro" else 0
            idx = self.s.today_index(rec, date.today() + timedelta(days=shift))
            if idx is None:
                raise UserError("Aktualny plan nie obejmuje tego dnia.")
            return Weekday(idx)
        try:
            return parse_day(key if key in DAY_KEYS_PL else value)
        except ValueError:
            raise UserError(f"Nie rozpoznałem dnia „{value}”.") from None

    def _day_or_today(self, value: str | None, rec: PlanRecord) -> Weekday:
        """Dzień z wiadomości, a bez niego — dzisiejszy dzień planu (zamiast cichego poniedziałku)."""
        day = self._day(value, rec)
        if day is not None:
            return day
        idx = self.s.today_index(rec)
        if idx is None:
            raise UserError("Napisz, o który dzień chodzi — aktualny plan nie obejmuje dzisiejszej daty.")
        return Weekday(idx)

    def _persons(self, value: str | None, rec: PlanRecord | None = None) -> list:
        a = self.s.record_repo(rec).assumptions if rec else self.s.repo.assumptions
        if not value:
            return list(a.persons)
        pid = _person_ids(self.s).get(normalize(value))
        if pid is None:
            raise UserError(f"Nie rozpoznałem osoby „{value}”.")
        return [a.person(pid)]

    def _show(self, action: ChatAction, rec: PlanRecord | None) -> dict:
        if rec is None:
            raise UserError("Nie ma jeszcze planu — najpierw go wygeneruj.")
        s = self.s
        repo = s.record_repo(rec)
        a = repo.assumptions
        products, dishes = repo.products, repo.dishes
        totals = {(int(t.day), t.person_id): t for t in (rec.report.totals if rec.report else [])}
        blocks: list[str] = []
        for fact in action.fakty or []:
            persons = self._persons(fact.osoba, rec)
            if fact.rodzaj == "dzien":
                day = self._day_or_today(fact.dzien, rec)
                rows = [f"**{DAY_LABELS_PL[day]}**"]
                for p in persons:
                    t = totals.get((int(day), p.id))
                    if t is None:
                        continue
                    tg = p.targets
                    rows.append(f"- {p.name}: {fmt_num(t.kcal, 0)} kcal (widełki {fmt_num(tg.kcal.min)}–{fmt_num(tg.kcal.max)}), "
                                f"białko {fmt_num(t.protein, 0)} g, tłuszcz {fmt_num(t.fat, 0)} g, "
                                f"węglowodany {fmt_num(t.carbs, 0)} g, błonnik {fmt_num(t.fiber, 0)} g")
                blocks.append("\n".join(rows))
            elif fact.rodzaj == "tydzien":
                rows = ["**Średnio na dzień w tym planie**"]
                for p in persons:
                    ts = [t for (d, pid), t in totals.items() if pid == p.id]
                    if not ts:
                        continue
                    n = len(ts)
                    rows.append(f"- {p.name}: {fmt_num(sum(t.kcal for t in ts) / n, 0)} kcal, "
                                f"białko {fmt_num(sum(t.protein for t in ts) / n, 0)} g, "
                                f"tłuszcz {fmt_num(sum(t.fat for t in ts) / n, 0)} g, "
                                f"węglowodany {fmt_num(sum(t.carbs for t in ts) / n, 0)} g ({n} dni)")
                blocks.append("\n".join(rows))
            elif fact.rodzaj == "posilek":
                day = self._day_or_today(fact.dzien, rec)
                try:
                    meal = MealType(normalize(fact.posilek or ""))
                except ValueError:
                    raise UserError(f"Nie rozpoznałem posiłku „{fact.posilek}”.") from None
                m = rec.plan.meal(day, meal)
                if m is None:
                    raise UserError("W planie nie ma takiego posiłku.")
                d = dishes.get(m.dish_id)
                rows = [f"**{DAY_LABELS_PL[day]}, {MEAL_LABELS_PL[meal].lower()}: {d.name if d else m.dish_id}**"]
                for p in persons:
                    portion = next((x for x in m.portions if x.person_id == p.id), None)
                    if portion is None:  # plan liczony dla innych osób niż obecne założenia
                        continue
                    grams = {i.product_id: i.grams for i in portion.items}
                    n = nutrition_of({k: v for k, v in grams.items() if k in products}, products, a.energy.method)
                    items = ", ".join(
                        f"{products[i.product_id].name.lower()} {fmt_num(i.grams, 0)} g"
                        + (f" (≈ {h})" if (h := household(products[i.product_id], i.grams)) else "")
                        for i in portion.items if i.product_id in products and not products[i.product_id].negligible)
                    rows.append(f"- {p.name}: {items} → {fmt_num(n['kcal'], 0)} kcal, białko {fmt_num(n['protein'], 0)} g")
                blocks.append("\n".join(rows))
            elif fact.rodzaj == "produkt":
                match = ProductMatcher(products).match(fact.produkt or "")
                if match.status is not MatchStatus.SURE or match.product_id is None:
                    names = ", ".join(c.name for c in match.candidates[:3])
                    raise UserError(f"Nie jestem pewien, o który produkt chodzi („{fact.produkt}”)."
                                    + (f" Może: {names}?" if names else ""))
                pid = match.product_id
                uses = []
                total = 0.0
                for m in rec.plan.meals:
                    g = sum(i.grams for portion in m.portions for i in portion.items if i.product_id == pid)
                    if g > 0:
                        total += g
                        uses.append(f"{DAY_LABELS_PL[m.day].lower()} ({MEAL_LABELS_PL[m.meal].lower()})")
                name = products[pid].name
                if not uses:
                    blocks.append(f"**{name}** nie występuje w posiłkach tego planu.")
                else:
                    blocks.append(f"**{name}**: {fmt_num(total, 0)} g w tygodniu (obie osoby) — " + ", ".join(uses))
        text = action.tekst.strip()
        if blocks:
            text = (text + "\n\n" if text else "") + "\n\n".join(blocks) + "\n\n_Liczby: weryfikator planu._"
        return self._reply(text, rec)

    # ------------------------------------------------------------------ zamiana dania
    def _swap(self, action: ChatAction, rec: PlanRecord | None, original: str) -> dict:
        if rec is None:
            raise UserError("Nie ma jeszcze planu — najpierw go wygeneruj.")
        day = self._day(action.dzien, rec)
        if day is None:
            raise UserError("Napisz, w który dzień zmienić posiłek.")
        try:
            meal = MealType(normalize(action.posilek or ""))
        except ValueError:
            raise UserError("Napisz, który posiłek zmienić (śniadanie, obiad czy kolacja).") from None
        comp = composition_from_plan(rec.plan)
        slot = comp.slot(day, meal)
        if slot is None:
            raise UserError("W planie nie ma takiego posiłku.")
        options = self.s.swap_options(rec.id, slot.key)["options"]
        if not options:
            raise UserError("Nie mam dań, którymi można zastąpić ten posiłek.")
        listing = "\n".join(f"- {o['id']} | {o['name']} | kuchnia: {o['cuisine']} | {o['description'] or ''}"
                            + (f" | uwagi: {', '.join(o['notes'])}" if o["notes"] else "") for o in options)
        llm = self.s.llm()
        assert llm is not None
        current = self.s.repo.dishes.get(slot.dish_id)
        prompt = (f"Użytkownik chce zmienić: {DAY_LABELS_PL[day].lower()}, {MEAL_LABELS_PL[meal].lower()} "
                  f"(teraz: {current.name if current else slot.dish_id}).\nJego słowa: {original}\n"
                  f"Czego szuka: {action.opis_dania or 'dowolnej zmiany'}\n\nDania, które pasują do tego posiłku:\n{listing}\n\n"
                  "Wybierz jedno danie najlepiej pasujące do prośby. Unikaj dań z uwagami, chyba że użytkownik o to prosi.")
        pick = llm.complete_json("czat_zamiana", "Wybierasz danie z listy. Podajesz tylko id z listy.", prompt, DishPick,
                                 temperature=0.1, max_tokens=300).value
        valid = {o["id"] for o in options}
        if not pick.danie or pick.danie not in valid:
            raise UserError("Nie znalazłem w bibliotece dania pasującego do prośby. Możesz wybrać je ręcznie w planie.")
        job = self.s.swap(rec.id, slot.key, pick.danie)
        name = self.s.repo.dishes[pick.danie].name
        text = (action.tekst.strip() + "\n\n" if action.tekst else "") + \
            f"Zamieniam na: **{name}** — {pick.uzasadnienie.strip().rstrip('.')}. Silnik przeliczy gramatury."
        return self._reply(text, rec, {"job_id": job.id, "kind": "zamiana", "dish_id": pick.danie})

    # ------------------------------------------------------------------ zmiany założeń (z potwierdzeniem)
    def _propose(self, action: ChatAction, rec: PlanRecord | None) -> dict:
        s = self.s
        if not action.zmiany:
            raise UserError("Nie zrozumiałem, co zmienić w założeniach.")
        _, diff, warnings = self._apply_changes(action.zmiany)
        pid = uuid.uuid4().hex[:10]
        # zapisujemy operacje, nie zrzut założeń — przy potwierdzeniu stosujemy je na aktualnym pliku
        s.pending_changes[pid] = {"zmiany": [c.model_dump(mode="json") for c in action.zmiany], "diff": diff}
        while len(s.pending_changes) > MAX_PENDING_PROPOSALS:
            s.pending_changes.pop(next(iter(s.pending_changes)))
        text = (action.tekst.strip() + "\n\n" if action.tekst else "") + "Proponowane zmiany:\n" + \
            "\n".join(f"- {d}" for d in diff)
        if warnings:
            text += "\n\nUwagi: " + "; ".join(warnings)
        text += "\n\nZatwierdź, żeby zapisać."
        return self._reply(text, rec, {"proposal_id": pid, "kind": "zmiana_zalozen", "diff": diff})

    @staticmethod
    def _number(value, label: str) -> float:
        if isinstance(value, bool):  # bool jest podklasą int — „tak” nie może stać się jedynką
            raise UserError(f"Zmiana „{label}” wymaga liczby.")
        try:
            return float(value)
        except (TypeError, ValueError):
            raise UserError(f"Zmiana „{label}” wymaga liczby.") from None

    def _apply_changes(self, changes: list[AssumptionChange]) -> tuple[dict, list[str], list[str]]:
        """Stosuje zmiany z czatu na AKTUALNYM pliku założeń. Zwraca (nowe dane, opis różnic, uwagi)."""
        s = self.s
        data = to_plain(load_yaml(s.paths.assumptions))
        new = copy.deepcopy(data)
        allowed = {p for p, _ in allowed_paths(s)}
        labels = dict(allowed_paths(s))
        diff: list[str] = []
        matcher = ProductMatcher(s.repo.products)
        for ch in changes:
            path = ch.sciezka.strip()
            if path not in allowed:
                raise UserError(f"Tej rzeczy nie da się zmienić z czatu ({path}). Użyj zakładki Założenia.")
            parent, key, old = self._locate(new, path)
            if isinstance(old, dict) and "min" in old and "max" in old:  # całe widełki: przesuwamy min, cel i max
                number = self._number(ch.wartosc, labels[path])
                center = float(old.get("cel", (old["min"] + old["max"]) / 2))
                delta = {"zwieksz": number, "zmniejsz": -number, "ustaw": number - center}.get(ch.operacja)
                if delta is None:
                    raise UserError("Widełki można przesunąć albo ustawić nowy cel.")
                before = dict(old)
                for bound in ("min", "cel", "max"):
                    if bound in old:
                        v = float(old[bound]) + delta
                        old[bound] = int(v) if v.is_integer() else round(v, 1)
                parent[key] = old
                diff.append(f"{labels[path].split(' — ')[0]}: {self._band(before)} → {self._band(old)}")
                continue
            if isinstance(old, list):
                value = self._list_value(path, ch, matcher)
                if ch.operacja == "dodaj":
                    if value not in old:
                        parent[key] = [*old, value]
                elif ch.operacja == "usun":
                    parent[key] = [x for x in old if x != value]
                else:
                    raise UserError("Listę można tylko uzupełnić albo z niej usunąć.")
            elif isinstance(old, bool) or path.endswith(BOOL_PATH_SUFFIXES):
                parent[key] = bool(ch.wartosc) if not isinstance(ch.wartosc, str) else normalize(ch.wartosc) in ("tak", "true")
            else:
                number = self._number(ch.wartosc, labels[path])
                base = float(old) if old is not None else 0.0
                result = {"ustaw": number, "zwieksz": base + number, "zmniejsz": base - number}.get(ch.operacja)
                if result is None:
                    raise UserError("Liczbę można ustawić, zwiększyć albo zmniejszyć.")
                parent[key] = int(result) if float(result).is_integer() else round(result, 1)
            diff.append(f"{labels[path]}: {self._fmt(old)} → {self._fmt(parent[key])}")
        _, errors, warnings = s.validate_assumptions(new)
        if errors:
            raise UserError("Po tej zmianie założenia byłyby niespójne: " + "; ".join(errors))
        return new, diff, warnings

    def confirm(self, proposal_id: str) -> dict:
        s = self.s
        prop = s.pending_changes.pop(proposal_id, None)
        rec = s.active_plan()
        if prop is None:
            return self._reply("Ta propozycja zmian wygasła — napisz jeszcze raz, co zmienić.", rec)
        try:
            # zmiany nakładamy na aktualne założenia (mogły się zmienić od propozycji — np. w zakładce Założenia),
            # zamiast nadpisywać plik zrzutem sprzed chwili
            new, diff, _ = self._apply_changes([AssumptionChange.model_validate(c) for c in prop["zmiany"]])
        except UserError as exc:
            return self._reply(f"Nie zapisano zmian: {exc}", rec)
        result = s.save_assumptions(new)
        if not result["ok"]:
            return self._reply("Nie zapisano zmian: " + "; ".join(result["errors"]), rec)
        text = "Zapisałem nowe założenia:\n" + "\n".join(f"- {d}" for d in diff)
        data = {"kind": "zalozenia_zapisane"}
        if rec is not None:
            text += "\n\nAktualny plan był liczony według starych założeń — możesz go przeliczyć."
            data["plan_id"] = rec.id
        return self._reply(text, rec, data)

    def _locate(self, data: dict, path: str):
        parts = path.split(".")
        node = data
        for part in parts[:-1]:
            if isinstance(node, list):  # osoby.<id>
                node = next((x for x in node if isinstance(x, dict) and x.get("id") == part), None)
                if node is None:
                    raise UserError(f"Nie ma osoby „{part}”.")
            elif isinstance(node, dict) and part in node:
                node = node[part]
            else:
                raise UserError(f"W założeniach brakuje sekcji „{part}” — użyj zakładki Założenia.")
        key = parts[-1]
        if key not in node:
            node[key] = [] if key in ("wyklucza_produkty", "produkty", "kuchnie") else None
        return node, key, node[key]

    def _list_value(self, path: str, ch: AssumptionChange, matcher: ProductMatcher) -> str:
        raw = str(ch.wartosc or "").strip()
        if not raw:
            raise UserError("Podaj, co dodać lub usunąć z listy.")
        if path.endswith("kuchnie"):
            return normalize(raw).replace(" ", "_")
        if raw in self.s.repo.products:
            return raw
        m = matcher.match(raw)
        if m.status is not MatchStatus.SURE or m.product_id is None:
            names = ", ".join(c.name for c in m.candidates[:3])
            raise UserError(f"Nie rozpoznałem produktu „{raw}”." + (f" Może: {names}?" if names else ""))
        return m.product_id

    @staticmethod
    def _band(band: dict) -> str:
        cel = f", cel {fmt_num(float(band['cel']))}" if "cel" in band else ""
        return f"{fmt_num(float(band['min']))}–{fmt_num(float(band['max']))}{cel}"

    def _fmt(self, value) -> str:
        if isinstance(value, bool):
            return "tak" if value else "nie"
        if isinstance(value, list):
            names = [self.s.repo.products[x].name if x in self.s.repo.products else str(x) for x in value]
            return ", ".join(names) if names else "(pusto)"
        if isinstance(value, int | float):
            return fmt_num(float(value))
        return "(brak)" if value is None else str(value)
