"""Czat z asystentem: model językowy rozpoznaje intencję, a program wykonuje akcję i podaje wszystkie liczby.

Model NIE podaje żadnych liczb (walidacja odrzuca cyfry i liczebniki). Pytania o wartości odżywcze obsługuje
program na podstawie wyników weryfikatora, zamiany dań sprawdza silnik, zmiany założeń wymagają potwierdzenia.
Uwagi o proporcjach dania („za mało mleczka, za dużo tofu”) model zamienia tylko na posiłek, składnik i kierunek —
zakres składnika zawęża program, a gramy liczy silnik; „zweryfikuj gramatury” to wnioski programu o talerzach.
"""

from __future__ import annotations

import contextlib
import copy
import re
import uuid
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from rapidfuzz import fuzz

from ..checker.verify import nutrition_of
from ..domain.assumptions import DAY_KEYS_PL, DAY_LABELS_PL, Weekday, parse_day
from ..domain.culinary import SAUCE_LIQUIDS
from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType, Role
from ..domain.product import Product
from ..food.matcher import MatchStatus, ProductMatcher, key_of, lemma_of, words
from ..llm.client import LLMError
from ..llm.scope import current as current_scope
from ..llm.scope import task_scope
from ..measures import household
from ..planner.portion_review import DEVIATION, grams_in_slot, plate_findings, slot_label
from ..storage.yamlio import load_yaml, to_plain
from ..textfmt import fmt_num, plural_pl
from ..textnorm import normalize
from ..textnumbers import has_numbers, without_numbers
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


class PortionRequest(BaseModel):
    """Jedna uwaga o proporcjach dania z planu. Model wskazuje tylko miejsce, składnik i kierunek — gramy liczy silnik."""
    dzien: str | None = Field(default=None, description="poniedzialek…niedziela, dzis albo jutro; brak = program poszuka")
    posilek: str | None = Field(default=None, description="sniadanie | obiad | kolacja")
    danie: str | None = Field(default=None, description="nazwa dania z planu, jeśli użytkownik ją podał")
    produkt: str = Field(description="składnik słowami użytkownika, w mianowniku: tofu, mleczko kokosowe, ryż, jajko; "
                                     "„danie jest suche” = sos")
    kierunek: Literal["za_duzo", "za_malo"]
    osoba: str | None = Field(default=None, description="id osoby, gdy uwaga dotyczy tylko jej talerza")


class ChatAction(BaseModel):
    typ: Literal["odpowiedz", "pokaz", "generuj", "wymysl_dania", "zamien", "proporcje", "sprawdz_porcje",
                 "zmien_zalozenia", "zapamietaj", "propozycje_gustu"]
    tekst: str = Field(description="krótka odpowiedź po polsku, BEZ cyfr i liczebników — liczby dopisze program")
    fakty: list[FactRequest] = Field(default_factory=list, max_length=6)
    porcje: list[PortionRequest] = Field(default_factory=list, max_length=8,
                                         description="dla proporcje i sprawdz_porcje: które składniki w których daniach")
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
        if has_numbers(v, CHAT_NUMBER_WORDS):
            raise ValueError("tekst nie może zawierać cyfr ani liczebników — wszystkie liczby podaje program "
                             "(użyj pola fakty albo zmiany)")
        return v


class DishPick(BaseModel):
    danie: str | None = Field(description="id dania z listy albo null, gdy nic nie pasuje")
    uzasadnienie: str

    @field_validator("uzasadnienie")
    @classmethod
    def _plain(cls, v: str) -> str:
        return without_numbers(v, CHAT_NUMBER_WORDS)  # zdania z liczbami wypadają — liczby podaje program


INVENT_REQUEST = re.compile(r"\b(wymysl\w*|stworz\w* (przepis|dani\w*)|dodaj\w* (przepis|dani\w*)|nowe dani\w*)\b")
# „nie wymyślaj…”, „jak wymyślacie…?”, „czy macie nowe dania?” — to nie są prośby o płatne wymyślanie (audyt W4)
NOT_A_REQUEST = re.compile(r"\b(nie|bez|zadn\w*|jak|czy|dlaczego|po co|kiedy|ile)\b")
PAID_ACTION_NOTE = ("To uruchomi płatne wywołania modelu językowego na Twoim koncie DeepSeek "
                    "(zwykle kilka centów; wymyślanie dań kosztuje więcej).")
# „zweryfikuj ten plan pod kątem gramatur” — model odpowiadał średnimi z tygodnia; program dokłada wnioski kulinarne
VERIFY_REQUEST = re.compile(r"\b(z?weryfik\w*|sprawdz\w*|przejrz\w*|ocen\w*)\b.*"
                            r"\b(gramatur\w*|porcj\w*|proporcj\w*|talerz\w*|ilosc\w*)")
# model zamieniał danie na prawie identyczne, gdy użytkownik prosił o inne proporcje tego samego dania
SWAP_WORDS = re.compile(r"\b(zamien\w*|wymien\w*|zamiast|inne\w* dani\w*|cos innego|na inne|nie chce\w* tego dania)\b")
# tylko wyraźne uwagi o ilości — „coś mniej tłustego” to dalej prośba o inne danie
PROPORTION_WORDS = re.compile(r"\b(za (malo|duzo)|such\w*|sucho|dodaj\w*|ujmij\w*|proporcj\w*)\b")
SAUCE_WORDS = {"sos", "sosu", "sosem", "sosie", "sosik", "sosiku", "sosow", "suche", "sucho", "suchy"}
QUANTITY = re.compile(r"\d+(?:[.,]\d+)?\s*(?:g|gram\w*|kg|ml|szt\w*|kost\w*)?\b")
PORTION_SLOTS_MAX = 3  # bez wskazanego dnia: najwyżej tyle posiłków z tym składnikiem naraz
REPORT_ROWS_MAX = 12
FREE_ACTION_NOTE = "Liczy tylko silnik — bez kosztów AI."
OUTDATED_PLAN_NOTE = ("Ten plan liczono według wcześniejszych przepisów lub celów, więc poprawiam go na tych samych "
                      "danych. Przycisk Przelicz w zakładce Tydzień zastosuje nowe proporcje przepisów.")

SYSTEM = """Jesteś asystentem w polskiej aplikacji do planowania tygodniowego jadłospisu dla domowników (osoby podaje kontekst).
Liczby (kalorie, makroskładniki, gramatury) liczy deterministyczny program — Ty ich NIGDY nie podajesz ani nie liczysz.
Twoje zadanie: zrozumieć wiadomość i wybrać jedną akcję:
- "pokaz" — pytanie o wartości, sumy, gramatury, gdzie jest produkt: wypełnij "fakty", a program dopisze liczby.
- "proporcje" — danie w planie ZOSTAJE, ale któregoś składnika ma być więcej albo mniej: „za mało mleczka w curry”,
  „za dużo tofu i ciecierzycy”, „cztery jajka na śniadanie to za dużo”, „dla żony za mało kurczaka”, „danie jest suche”
  (= za mało sosu). W "porcje" podaj każdy składnik osobno: dzien i posilek (albo danie — nazwa z planu), produkt
  w mianowniku, kierunek za_duzo / za_malo i osoba, gdy chodzi o talerz jednej osoby. Nie wybieraj wtedy "zamien".
- "sprawdz_porcje" — prośba o sprawdzenie lub weryfikację gramatur, porcji czy proporcji planu: program porówna każdy
  talerz z przepisem. Jeśli użytkownik przy okazji narzeka na konkretne składniki, wypełnij też "porcje" — program
  zaproponuje poprawki do zatwierdzenia.
- "zamien" — prośba o INNE danie w konkretnym posiłku: podaj dzien, posilek i opis_dania (czego szuka użytkownik).
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
- "zapamietaj" — trwała preferencja albo uwaga do przyszłych planów, bez liczbowych zmian celów: „zawsze dawaj mniej
  ryżu na obiad, wolę więcej mięsa”, „nie dawaj białek jaj”, „nie lubimy tego dania”, „lista zakupów jest niewygodna”.
  Program zapisze ją w notatniku preferencji; w "zyczenia" przepisz całą uwagę słowami użytkownika. Uwaga o konkretnym
  daniu z aktualnego planu to "proporcje".
- "propozycje_gustu" — prośba, żeby program sam zaproponował dania w guście domowników: „zaproponuj coś, co nam
  zasmakuje”, „co byś nam polecił?”, „czego się o nas nauczyłeś?”, „podsuń coś nowego w naszym stylu”. Program przejrzy
  oceny, zamiany i prośby, wybierze dania z biblioteki i pomysły na nowe (domownicy zdecydują, co trafi do planu).
  Gdy podają konkretne danie do wymyślenia, to "wymysl_dania".
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


def ingredient_ids(text: str, dish: DishTemplate, products: Mapping[str, Product]) -> list[str]:
    """Składnik z prośby (słowa użytkownika albo id) → produkty z przepisu dania; „sos”/„suche” to wszystkie
    składniki sosu. Szukamy tylko wśród składników tego dania, więc odmiana („mleczka”, „ciecierzycy”) wystarcza."""
    cands = {i.product_id: products[i.product_id] for i in dish.ingredients if i.product_id in products
             and i.role is not Role.SPICE and not products[i.product_id].negligible and i.typical_g > 0}
    raw = QUANTITY.sub(" ", (text or "").strip()).strip()
    if raw in cands:
        return [raw]
    ws = words(raw)
    if not ws:
        return []
    if normalize(ws[0]) in SAUCE_WORDS:
        sauce = [i.product_id for i in dish.ingredients if i.product_id in cands
                 and (i.role is Role.SAUCE or i.product_id in SAUCE_LIQUIDS)]
        if sauce:
            return sauce
    match = ProductMatcher(cands).match(raw)
    if match.product_id:
        return [match.product_id]
    query = set(lemma_of(ws).split()) | set(key_of(ws).split())
    stems = {w[:5] for w in query if len(w) >= 5}
    hits = []
    for pid, p in cands.items():
        tokens = set()
        for name in (p.name, *p.aliases):
            tokens |= set(lemma_of(words(name)).split()) | set(key_of(words(name)).split())
        if query & tokens or any(t.startswith(s) for t in tokens for s in stems):
            hits.append(pid)
    return hits if len(hits) == 1 else []


def dish_matches(text: str, dish: DishTemplate) -> bool:
    q, name = normalize(text), normalize(dish.name)
    return bool(q) and (q in name or fuzz.token_set_ratio(q, name) >= 85)


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
        """Jedna wiadomość = jedno polecenie z własnym limitem kosztu AI (jak zadanie w tle)."""
        if current_scope() is not None:
            return self._handle(text, plan_id)
        with task_scope():
            return self._handle(text, plan_id)

    def _handle(self, text: str, plan_id: str | None = None) -> dict:
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
        norm = normalize(text)
        if action.typ == "odpowiedz" and INVENT_REQUEST.search(norm) and not NOT_A_REQUEST.search(norm):
            # model czasem opowiada przepis zamiast go wymyślić do biblioteki — nie zgadujemy: pytamy o zgodę
            action = action.model_copy(update={"typ": "wymysl_dania", "zyczenia": text, "ile_dan": action.ile_dan or 2})
        if (action.typ == "pokaz" and action.porcje) or (VERIFY_REQUEST.search(norm) and (action.typ == "odpowiedz" or (
                action.typ == "pokaz" and all(f.rodzaj == "tydzien" for f in action.fakty)))):
            # same średnie z tygodnia nie odpowiadają na „zweryfikuj gramatury” — program dokłada wnioski kulinarne
            action = action.model_copy(update={"typ": "sprawdz_porcje"})
        if action.typ == "zamien" and (action.porcje or (PROPORTION_WORDS.search(norm) and not SWAP_WORDS.search(norm))):
            # „za mało mleczka, za dużo tofu” to nie prośba o inne danie — zamiana dawała prawie to samo danie
            action = action.model_copy(update={"typ": "proporcje"})
        try:
            if action.typ == "pokaz":
                return self._show(action, rec)
            if action.typ in ("generuj", "wymysl_dania", "propozycje_gustu"):
                return self._propose_action(action, text, rec)
            if action.typ == "zamien":
                return self._swap(action, rec, text)
            if action.typ == "proporcje":
                return self._portions(action, rec)
            if action.typ == "sprawdz_porcje":
                return self._check_portions(action, rec)
            if action.typ == "zmien_zalozenia":
                return self._propose(action, rec)
            if action.typ == "zapamietaj":
                return self._remember(action.zyczenia or text, rec)
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
                 (". Użytkownik to mężczyzna, żona to kobieta." if {p.id for p in a.persons} == {"kobieta", "mezczyzna"}
                  else ".")]
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
        blocks = self._fact_blocks(action, rec)
        text = action.tekst.strip()
        if blocks:
            text = (text + "\n\n" if text else "") + "\n\n".join(blocks) + "\n\n_Liczby: weryfikator planu._"
        return self._reply(text, rec)

    def _fact_blocks(self, action: ChatAction, rec: PlanRecord) -> list[str]:
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
        return blocks

    # ------------------------------------------------------------------ proporcje dania (bez zamiany dania)
    def _portions(self, action: ChatAction, rec: PlanRecord | None) -> dict:
        """„Za mało mleczka, za dużo tofu” — danie zostaje, silnik przelicza gramatury z zawężonym składnikiem.
        Bez dodatkowych wywołań modelu (koszt tylko rozpoznania wiadomości), więc działa od razu — jak zamiana."""
        if rec is None:
            raise UserError("Nie ma jeszcze planu — najpierw go wygeneruj.")
        fixes, notes = self._portion_fixes(action, rec)
        if not fixes:
            raise UserError(" ".join(notes) or "Napisz, w którym daniu i którego składnika ma być więcej, a którego "
                            "mniej — danie zostanie, zmienię tylko gramatury.")
        changes = self.s.portion_changes(rec, fixes)
        if not changes.applied:
            raise UserError("Nie zmienię tego: " + "; ".join(notes + changes.skipped) + ".")
        job = self.s.adjust_portions(rec.id, fixes)
        text = (action.tekst.strip() + "\n\n" if action.tekst.strip() else "") + \
            "Poprawiam proporcje — danie zostaje, silnik przeliczy te dni:\n" + self._fix_lines(changes, rec)
        if notes or changes.skipped:
            text += "\n\nUwagi: " + "; ".join(notes + changes.skipped) + "."
        if not changes.current:
            text += "\n\n" + OUTDATED_PLAN_NOTE
        text += f"\n\n{FREE_ACTION_NOTE} Nowa wersja planu powstanie tylko wtedy, gdy dni zmieszczą się w celach."
        return self._reply(text, rec, {"job_id": job.id, "kind": "proporcje"})

    def _check_portions(self, action: ChatAction, rec: PlanRecord | None) -> dict:
        """„Zweryfikuj gramatury” — wnioski kulinarne z policzonego planu; poprawki z uwag użytkownika czekają na
        zatwierdzenie (użytkownik prosił o sprawdzenie, nie o zmianę)."""
        if rec is None:
            raise UserError("Nie ma jeszcze planu — najpierw go wygeneruj.")
        parts = [action.tekst.strip()] if action.tekst.strip() else []
        parts.append(self._portion_report(rec))
        with contextlib.suppress(UserError):  # niejasny fakt (np. dzień) nie może zabrać wniosków z weryfikacji
            parts += [b for b in self._fact_blocks(action, rec) if not b.startswith("**Średnio")]
        data = None
        if action.porcje:
            try:
                fixes, notes = self._portion_fixes(action, rec)
            except UserError as exc:
                fixes, notes = [], [str(exc).rstrip(".")]
            changes = self.s.portion_changes(rec, fixes) if fixes else None
            notes += changes.skipped if changes else []
            if changes and changes.applied:
                pid = self._pending({"akcja": "proporcje", "plan_id": rec.id,
                                     "poprawki": [{k: f[k] for k in ("slot", "produkt", "kierunek", "osoba")}
                                                  for f in changes.applied],
                                     "wersje": sorted(self.s.store.children(rec.id))})
                text = "**Mogę poprawić** (danie zostaje, silnik przeliczy te dni):\n" + self._fix_lines(changes, rec)
                if notes:
                    text += "\n\nUwagi: " + "; ".join(notes) + "."
                if not changes.current:
                    text += "\n\n" + OUTDATED_PLAN_NOTE
                parts.append(text + f"\n\n{FREE_ACTION_NOTE} Poprawić?")
                data = {"proposal_id": pid, "kind": "potwierdz_akcje"}
            elif notes:
                parts.append("Uwagi: " + "; ".join(notes) + ".")
        return self._reply("\n\n".join(parts), rec, data)

    def _portion_report(self, rec: PlanRecord) -> str:
        repo = self.s.record_repo(rec)
        products = repo.products
        deviations, dry = plate_findings(rec.plan, repo.dishes, products, repo.assumptions,
                                         self.s.settings.reference_kcal)
        names = {p.id: p.name for p in repo.assumptions.persons}

        def where(key: str, dish_id: str) -> str:
            dish = repo.dishes.get(dish_id)
            return f"{slot_label(key)}, {dish.name if dish else dish_id}"

        def grouped(findings, fmt) -> list[str]:
            rows: dict[str, list[str]] = {}
            for f in findings:
                rows.setdefault(where(f.slot, f.dish_id), []).append(f"{names.get(f.person_id, f.person_id)}: {fmt(f)}")
            lines = [f"- {k}: " + "; ".join(v) for k, v in rows.items()]
            if len(lines) > REPORT_ROWS_MAX:
                lines = [*lines[:REPORT_ROWS_MAX], f"- …i jeszcze {len(lines) - REPORT_ROWS_MAX}"]
            return lines

        def pct(ratio: float) -> str:
            return ("+" if ratio >= 1 else "−") + fmt_num(abs(ratio - 1) * 100, 0) + "%"

        out = ["**Proporcje talerzy względem przepisów** (porcja przepisu przeliczona na kalorie osoby)"]
        if deviations:
            out.append(f"Składniki, które odbiegają od przepisu o ponad {fmt_num(DEVIATION * 100, 0)}%:")
            out += grouped(deviations, lambda f: f"{products[f.product_ids[0]].name.lower()} {fmt_num(f.grams, 0)} g "
                                                 f"(przepis ≈ {fmt_num(f.recipe_g, 0)} g, {pct(f.ratio)})")
        else:
            out.append(f"Żaden składnik nie odbiega od przepisu o ponad {fmt_num(DEVIATION * 100, 0)}%.")
        if dry:
            out.append("Talerze, które mogą wyjść suche (sosu mniej względem ryżu, makaronu czy białka niż w przepisie):")
            out += grouped(dry, lambda f: f"sos ({', '.join(products[p].name.lower() for p in f.product_ids)}) "
                                          f"{fmt_num(f.grams, 0)} g, a przepis do tych dodatków daje "
                                          f"≈ {fmt_num(f.recipe_g, 0)} g")
        else:
            out.append("Dania z sosem mają go w proporcji podobnej do przepisu.")
        warnings = [i.message for i in (rec.report.issues if rec.report else [])
                    if i.code in ("duza_porcja", "uwaga_kucharza", "proporcje_przepisu")]
        if warnings:
            out.append("Uwagi weryfikatora:")
            out += [f"- {self.s.humanize(m)}" for m in warnings[:REPORT_ROWS_MAX]]
        out.append("Napisz, czego ma być więcej, a czego mniej (np. „w środowym obiedzie mniej ryżu”) — danie zostanie, "
                   "a silnik przeliczy gramatury.\n\n_Liczby: weryfikator planu._")
        return "\n".join(out)

    def _fix_lines(self, changes, rec: PlanRecord) -> str:
        repo = changes.base
        names = {p.id: p.name for p in repo.assumptions.persons}
        rows = []
        for f in changes.applied:
            dish = repo.dishes.get(f["danie"])
            now = grams_in_slot(rec.plan, f["slot"], f["produkt"])
            persons = [f["osoba"]] if f.get("osoba") in now else list(now)
            grams = ", ".join(f"{names.get(p, p)} {fmt_num(now[p], 0)} g" for p in persons)
            rows.append(f"- {slot_label(f['slot'])}, {dish.name if dish else f['danie']}: "
                        f"{'mniej' if f['kierunek'] == 'za_duzo' else 'więcej'} — "
                        f"{repo.products[f['produkt']].name.lower()} (teraz: {grams})")
        return "\n".join(rows)

    def _portion_fixes(self, action: ChatAction, rec: PlanRecord) -> tuple[list[dict], list[str]]:
        """Uwagi modelu → poprawki {slot, produkt, kierunek, osoba}. Składnik szukamy w przepisie dania z planu
        (słowa użytkownika → id produktu); bez dnia — w posiłkach z tym składnikiem, które najbardziej odbiegają od
        przepisu we wskazaną stronę."""
        repo = self.s.record_repo(rec)
        comp = composition_from_plan(rec.plan)
        fixes: list[dict] = []
        notes: list[str] = []
        for req in action.porcje:
            day = self._day(req.dzien or (action.dzien if not req.danie else None), rec)
            meal_text = req.posilek or (action.posilek if not req.danie else None)
            meal = None
            if meal_text:
                try:
                    meal = MealType(normalize(meal_text))
                except ValueError:
                    raise UserError(f"Nie rozpoznałem posiłku „{meal_text}”.") from None
            slots = [s for s in comp.slots if (day is None or day in s.days) and (meal is None or s.meal == meal)]
            if req.danie:
                named = [s for s in slots if s.dish_id in repo.dishes and dish_matches(req.danie, repo.dishes[s.dish_id])]
                slots = named or slots
            found = [(s, ids) for s in slots if s.dish_id in repo.dishes
                     and (ids := ingredient_ids(req.produkt, repo.dishes[s.dish_id], repo.products))]
            if not found:
                place = f" w daniu „{req.danie}”" if req.danie else ""
                notes.append(f"nie znalazłem składnika „{without_numbers(req.produkt) or req.produkt}”{place} "
                             "w tym planie")
                continue
            if len(found) > 1:
                found = self._most_off(found, req.kierunek, rec, repo)
            osoba = _person_ids(self.s).get(normalize(req.osoba)) if req.osoba else None
            for s, ids in found:
                for pid in ids:
                    fix = {"slot": s.key, "produkt": pid, "kierunek": req.kierunek, "osoba": osoba}
                    if not any(f["slot"] == fix["slot"] and f["produkt"] == pid for f in fixes):
                        fixes.append(fix)
        return fixes, notes

    def _most_off(self, found: list, direction: str, rec: PlanRecord, repo) -> list:
        """Bez dokładnego miejsca: posiłek, w którym składnik najbardziej odbiega od przepisu we wskazaną stronę, oraz
        inne wyraźnie odbiegające (najwyżej PORTION_SLOTS_MAX)."""
        ref = self.s.settings.reference_kcal
        scored = []
        for s, ids in found:
            dish = repo.dishes[s.dish_id]
            ratios = []
            for person in repo.assumptions.persons:
                plate = sum(grams_in_slot(rec.plan, s.key, pid).get(person.id, 0.0) for pid in ids)
                recipe = sum(i.typical_g * (person.energy_center / ref if i.scales else 1.0)
                             for i in dish.ingredients if i.product_id in ids)
                if recipe > 0:
                    ratios.append(plate / recipe)
            if ratios:
                scored.append((max(ratios) if direction == "za_duzo" else min(ratios), s, ids))
        scored.sort(key=lambda x: -x[0] if direction == "za_duzo" else x[0])
        if not scored:
            return found[:1]
        off = [x for x in scored[1:] if (x[0] > 1.25 if direction == "za_duzo" else x[0] < 0.8)]
        return [(s, ids) for _, s, ids in [scored[0], *off][:PORTION_SLOTS_MAX]]

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
    def _propose_action(self, action: ChatAction, text: str, rec: PlanRecord | None) -> dict:
        """Płatne i trwałe akcje (nowy plan, wymyślanie dań do biblioteki) czekają na „Tak, uruchom” — jak zmiany
        założeń. Model mógł źle zrozumieć wiadomość, a wymyślone dania trafiają do biblioteki."""
        wishes = (action.zyczenia or text).strip()[:1000]
        if action.typ == "propozycje_gustu":
            what = ("Przejrzę Wasze oceny dań, zamiany w planach, uwagi i prośby z czatu, a potem zaproponuję dania "
                    "z biblioteki, których jeszcze nie jedliście, i pomysły na nowe. Wybierzecie, co ma trafić do "
                    "następnego planu")
            params = {"akcja": "propozycje_gustu"}
        elif action.typ == "generuj":
            what = "Ułożę nowy plan tygodnia" + (f" z życzeniami: „{wishes}”" if wishes else "")
            if action.wymysl_nowe:
                what += ", najpierw wymyślając nowe dania (kilka minut)"
            params = {"akcja": "generuj", "zyczenia": wishes, "wymysl_nowe": action.wymysl_nowe}
        else:
            count = action.ile_dan or 6
            what = (f"Wymyślę {count} {plural_pl(count, 'danie', 'dania', 'dań')} do biblioteki: „{wishes}”. "
                    "Nowe dania będą oznaczone jako nowe — możesz je odrzucić")
            params = {"akcja": "wymysl_dania", "zyczenia": wishes, "ile_dan": count}
        pid = self._pending(params)
        intro = (action.tekst.strip() + "\n\n") if action.tekst else ""
        return self._reply(f"{intro}{what}.\n\n{PAID_ACTION_NOTE} Uruchomić?", rec,
                           {"proposal_id": pid, "kind": "potwierdz_akcje"})

    def _pending(self, params: dict) -> str:
        """Propozycja czekająca na „Tak, uruchom” / „Zatwierdź” (najstarsze wygasają)."""
        s = self.s
        pid = uuid.uuid4().hex[:10]
        s.pending_changes[pid] = params
        while len(s.pending_changes) > MAX_PENDING_PROPOSALS:
            s.pending_changes.pop(next(iter(s.pending_changes)))
        return pid

    def _run_action(self, prop: dict, rec: PlanRecord | None) -> dict:
        s = self.s
        if prop["akcja"] == "proporcje":
            # plan mógł w międzyczasie dostać nowszą wersję (np. zamiana w oknie) — wtedy odmowa, nie siostrzana wersja
            job = s.adjust_portions(prop["plan_id"], prop["poprawki"], children_before=set(prop.get("wersje", [])))
            return self._reply("Poprawiam proporcje — silnik przeliczy te dni.", rec,
                               {"job_id": job.id, "kind": "proporcje"})
        if prop["akcja"] == "propozycje_gustu":
            from .taste import Taste

            job = Taste(s).refresh()
            return self._reply("Szukam dań w Waszym guście — propozycje pojawią się też w Notatniku preferencji.", rec,
                               {"job_id": job.id, "kind": "gust"})
        if prop["akcja"] == "generuj":
            job = s.generate(wishes=prop.get("zyczenia") or None, channel=self.channel, invent=bool(prop.get("wymysl_nowe")))
            return self._reply("Układam nowy plan tygodnia.", rec, {"job_id": job.id, "kind": "generowanie"})
        job = s.invent(prop.get("zyczenia") or "", int(prop.get("ile_dan") or 6), channel=self.channel)
        return self._reply("Wymyślam nowe dania — silnik i kucharz je sprawdzą.", rec, {"job_id": job.id, "kind": "wymyslanie"})

    def _remember(self, text: str, rec: PlanRecord | None) -> dict:
        """Uwaga trafia do notatnika preferencji; odpowiedź składa program z wyniku analizy (opisy zasad z liczbami)."""
        from .notebook import Notebook

        out = Notebook(self.s).add_comment(text, plan_id=rec.id if rec else None, channel=self.channel)
        a = out.get("analysis") or {}
        parts = ["Zapisałem w notatniku preferencji."]
        if a.get("summary"):
            parts.append(a["summary"])
        if a.get("rules_added"):
            parts.append("Nowe zasady:\n" + "\n".join(f"- {r['description']}" for r in a["rules_added"]))
        if a.get("rules_kept"):
            parts.append("Bez zmian (już zapisane):\n" + "\n".join(f"- {r}" for r in a["rules_kept"]))
        if a.get("rules_removed"):
            parts.append("Usunięte lub zastąpione:\n" + "\n".join(f"- {r}" for r in a["rules_removed"]))
        if a.get("ratings"):
            parts.append("Oceny dań: " + ", ".join(("👍 " if r["rating"] > 0 else "👎 ") + r["name"] for r in a["ratings"]))
        if a.get("program_notes"):
            parts.append("Do poprawy w programie:\n" + "\n".join(f"- {x}" for x in a["program_notes"]))
        if a.get("problems"):
            parts.append("Uwagi: " + "; ".join(a["problems"]))
        if a.get("rules_added"):
            parts.append("Zasady działają w nowych planach — aktualny plan przelicz w zakładce Plan tygodnia.")
        return self._reply("\n\n".join(parts), rec, {"kind": "notatnik", "note_id": out.get("note_id")})

    def _propose(self, action: ChatAction, rec: PlanRecord | None) -> dict:
        if not action.zmiany:
            raise UserError("Nie zrozumiałem, co zmienić w założeniach.")
        _, diff, warnings = self._apply_changes(action.zmiany)
        # zapisujemy operacje, nie zrzut założeń — przy potwierdzeniu stosujemy je na aktualnym pliku
        pid = self._pending({"zmiany": [c.model_dump(mode="json") for c in action.zmiany], "diff": diff})
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
        prop = s.pending_changes.get(proposal_id)
        rec = s.active_plan()
        if prop is None:
            return self._reply("Ta propozycja zmian wygasła — napisz jeszcze raz, co zmienić.", rec)
        if "akcja" in prop:
            try:
                reply = self._run_action(prop, rec)
            except UserError as exc:
                return self._reply(f"Nie uruchomiono: {exc}", rec)
            s.pending_changes.pop(proposal_id, None)
            return reply
        try:
            # zmiany nakładamy na aktualne założenia (mogły się zmienić od propozycji — np. w zakładce Założenia),
            # zamiast nadpisywać plik zrzutem sprzed chwili
            new, diff, _ = self._apply_changes([AssumptionChange.model_validate(c) for c in prop["zmiany"]])
        except UserError as exc:
            return self._reply(f"Nie zapisano zmian: {exc}", rec)
        # użytkownik potwierdził propozycję, w której widział uwagi (także o niskich kaloriach)
        result = s.save_assumptions(new, confirm_low_kcal=True)
        if not result["ok"]:
            return self._reply("Nie zapisano zmian: " + "; ".join(result["errors"]), rec)
        s.pending_changes.pop(proposal_id, None)  # dopiero po zapisie — odmowa (np. trwa zadanie) jej nie gubi
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
