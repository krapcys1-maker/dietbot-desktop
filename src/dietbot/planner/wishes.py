"""Życzenia do planu: model wyodrębnia to, co da się policzyć, a kod zamienia to na twarde reguły tygodnia.

- wykluczenia na cały tydzień („bez ryb”) → lista produktów; dania z nimi nie trafią do puli tego planu,
- liczba dań danej kuchni („kuchnia włoska 2 razy”) → dokładna liczba pilnowana przez walidację i autokorektę,
- konkretne dania („w 2 dni zupa pomidorowa”, „schabowy z piersi kurczaka z ziemniakami i surówką”) → kod wybiera
  kandydatów z biblioteki (słowa z nazwy i składników), model wskazuje to, które domownik rozpozna, a planer
  przypina je do tygodnia (w podanym dniu). Gdy biblioteka nie ma pasującego dania, usługa je wymyśla.
Pozostałe życzenia (np. „więcej kuchni włoskiej”, „coś szybkiego”) dostaje model układający tydzień.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from ..domain.assumptions import DAY_KEYS_PL, DAY_LABELS_PL, Weekday
from ..domain.dish import MEAL_LABELS_PL, DishTemplate, MealType, Role, cuisine_label
from ..domain.product import CATEGORY_LABELS_PL, Category, Product
from ..food.matcher import MatchStatus, ProductMatcher
from ..textfmt import plural_pl
from ..textnorm import normalize
from .pools import Pools

WISH_SYSTEM = """Analizujesz życzenia domowników do tygodniowego jadłospisu. Wypisz trzy rzeczy:
1. Czego ma NIE BYĆ w całym tygodniu (np. „bez ryb”, „nie chcę grzybów”, „żadnej wieprzowiny”). Nie wykluczaj niczego,
   czego użytkownik wprost nie wykluczył. Wykluczenia dotyczące jednego dnia („bez jajek w piątek”) pomiń.
2. Ile dań danej kuchni ma być w tygodniu — TYLKO gdy użytkownik podał liczbę: „kuchnia włoska 2 razy” → wloska 2,
   „raz coś indyjskiego” → indyjska 1, „bez kuchni azjatyckiej” → azjatycka 0. Bez liczby („więcej kuchni
   azjatyckiej”, „coś włoskiego”) nic nie wpisuj.
3. Konkretne dania, o które proszą („w piątek pizza”, „w 2 dni zupa pomidorowa”, „schabowy z kurczaka z ziemniakami
   i surówką”): opis dania pełnymi słowami, z poprawionymi literówkami i wszystkimi szczegółami z życzeń (z czego ma
   być, z jakimi dodatkami); posiłek, jeśli wynika z prośby albo z rodzaju dania (zupa i danie z ziemniakami to obiad;
   lody, chipsy, popcorn, batonik, żelki, kawałek ciasta jedzone poza posiłkami to przekaska);
   dni tygodnia tylko wtedy, gdy je podano; zupa_na_zapas dla zupy gotowanej na kilka dni (każda zupa na obiad).
   Każde danie osobno („lody albo popcorn” to jedno życzenie). Ogólne życzenia bez konkretnego dania („więcej ryb”,
   „coś szybkiego”, „coś słodkiego”, „resztę wymyśl”, „kuchnia włoska 2 razy”) tu pomiń."""


class CuisineCount(BaseModel):
    kuchnia: str = Field(description="kuchnia z listy")
    ile: int = Field(ge=0, le=21, description="liczba dań tej kuchni w całym tygodniu")


class WishDish(BaseModel):
    opis: str = Field(min_length=2, max_length=160,
                      description="danie pełnymi słowami, np. „kotlet schabowy z piersi kurczaka z ziemniakami i surówką”")
    posilek: Literal["sniadanie", "obiad", "kolacja", "przekaska"] | None = None
    dni: list[str] = Field(default_factory=list, max_length=7,
                           description="dni tygodnia (poniedzialek…niedziela) — tylko gdy podane w życzeniach")
    zupa_na_zapas: bool = False
    kuchnia: str | None = Field(default=None, description="kuchnia z listy, jeśli wynika z dania (np. schabowy → polska)")


class WishFilters(BaseModel):
    bez_kategorii: list[Category] = Field(default_factory=list, description="kategorie produktów wykluczone na cały tydzień")
    bez_produktow: list[str] = Field(default_factory=list, description="nazwy produktów wykluczonych na cały tydzień")
    kuchnie: list[CuisineCount] = Field(default_factory=list,
                                        description="liczba dań danej kuchni — tylko gdy użytkownik podał liczbę")
    dania: list[WishDish] = Field(default_factory=list, max_length=7, description="konkretne dania z życzeń")


@dataclass
class WishExclusions:
    product_ids: set[str] = field(default_factory=set)
    cuisine_counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    dishes: list[WishDish] = field(default_factory=list)
    requested: list[RequestedDish] = field(default_factory=list)  # `dishes` po dopasowaniu do biblioteki


@dataclass
class RequestedDish:
    """Danie z życzeń po dopasowaniu: `dish_id` None = biblioteka nie ma pasującego dania."""

    request: WishDish
    dish_id: str | None = None
    days: list[Weekday] = field(default_factory=list)
    meal: MealType | None = None
    candidates: list[str] = field(default_factory=list)
    invented: bool = False

    @property
    def batch(self) -> bool:
        return self.request.zupa_na_zapas and self.meal is not MealType.SNACK

    @property
    def snack(self) -> bool:
        """Przekąska z puli 20% (lody, chipsy…) — z biblioteki przekąsek; takich życzeń nie wymyślamy jako dań."""
        return self.meal is MealType.SNACK or is_snack_request(self.request)

    def where(self) -> str:
        parts = []
        if self.days:
            parts.append(", ".join(DAY_LABELS_PL[d].lower() for d in self.days))
        if self.meal is not None:
            parts.append(MEAL_LABELS_PL[self.meal].lower())
        return f" ({', '.join(parts)})" if parts else ""


def match_cuisine(value: str, cuisines: list[str]) -> str | None:
    key = normalize(value).replace(" ", "_")
    if not key:
        return None
    for c in cuisines:
        if key == c or key.startswith(c[:5]) or c.startswith(key[:5]):
            return c
    return None


def parse_days(values: list[str]) -> list[Weekday]:
    out: list[Weekday] = []
    for v in values:
        key = normalize(v).replace(" ", "")
        day = DAY_KEYS_PL.get(key) or next((d for k, d in DAY_KEYS_PL.items() if key[:3] and k.startswith(key[:3])), None)
        if day is not None and day not in out:
            out.append(day)
    return sorted(out)


def extract_exclusions(llm, wishes: str, products: Mapping[str, Product],
                       cuisines: list[str] | None = None) -> WishExclusions:
    categories = "\n".join(f"- {c.value}: {CATEGORY_LABELS_PL[c]}" for c in Category)
    kitchens = f"\n\nKuchnie: {', '.join(cuisines)}" if cuisines else ""
    user = f"Kategorie produktów:\n{categories}{kitchens}\n\nŻyczenia: {wishes}"
    res = llm.complete_json("zyczenia_wykluczenia", WISH_SYSTEM, user, WishFilters, temperature=0.0, max_tokens=900)
    out = WishExclusions(cost_usd=res.cost_usd, dishes=list(res.value.dania))
    cats = set(res.value.bez_kategorii)
    if cats:
        out.product_ids |= {pid for pid, p in products.items() if p.category in cats}
        out.notes.append("bez: " + ", ".join(CATEGORY_LABELS_PL[c].lower() for c in cats))
    matcher = ProductMatcher(products)
    for name in res.value.bez_produktow:
        m = matcher.match(name)
        if m.status is MatchStatus.SURE and m.product_id:
            out.product_ids.add(m.product_id)
            out.notes.append(f"bez: {products[m.product_id].name.lower()}")
        else:
            out.notes.append(f"nie rozpoznano produktu do wykluczenia: {name}")
    for item in res.value.kuchnie if cuisines else []:
        key = match_cuisine(item.kuchnia, cuisines)
        if key is None:
            out.notes.append(f"nie znam kuchni „{item.kuchnia}” — przekażę życzenie modelowi")
            continue
        out.cuisine_counts[key] = item.ile
        out.notes.append(f"kuchnia {cuisine_label(key)}: {item.ile} {plural_pl(item.ile, 'danie', 'dania', 'dań')}")
    return out


# ------------------------------------------------------------------------------------ konkretne dania z życzeń
SHORTLIST = 14
# słowa bez znaczenia dla dopasowania dania
DISH_STOPWORDS = {"ale", "albo", "danie", "dania", "jakis", "jakies", "jakas", "cos", "moze", "byc", "chce", "chcemy",
                  "prosze", "raz", "dwa", "dni", "dzien", "dzienna", "tydzien", "zrobiony", "zrobiona", "domowy", "domowa",
                  "domowe", "porcja", "talerz", "obiad", "kolacja", "sniadanie", "przekaska", "zapas", "zapasu", "dwoch",
                  "najlepiej", "dla", "oraz", "lub", "przy", "troche", "duzo", "bardzo", "dobra", "dobre", "dobry",
                  "prawdziwa", "prawdziwe", "prawdziwy", "normalna", "normalne", "normalny", "zwykla", "zwykle", "zwykly",
                  "klasyczna", "klasyczne", "klasyczny", "tradycyjna", "tradycyjne", "tradycyjny", "filmu", "film",
                  "kawalek", "kawalka", "kawalki"}
# nazwy potoczne → słowa, którymi dania nazywają to samo w bibliotece
DISH_SYNONYMS = {
    "schabowy": ["kotlet", "panier"], "schabowego": ["kotlet", "panier"], "schabowe": ["kotlet", "panier"],
    "mielony": ["kotlet", "mielon"], "mielone": ["kotlet", "mielon"], "mielonego": ["kotlet", "mielon"],
    "piersi": ["kurcz", "piers"], "pierś": ["kurcz", "piers"], "piers": ["kurcz", "piers"],
    "frytki": ["frytk"], "mizeria": ["mizer", "ogorek"], "buraczki": ["burak"], "pomidorowka": ["pomidorow"],
    "chipsow": ["chips"], "loda": ["lody"], "lodow": ["lody"], "batonik": ["baton"], "batonika": ["baton"],
    "ciasto": ["serni", "drozd"], "ciastko": ["serni", "drozd"], "ciasta": ["serni", "drozd"],
}
# pierwsze słowo życzenia, po którym poznajemy przekąskę z puli 20% (lody, chipsy…), a nie danie do wymyślenia
SNACK_HEADS = ("lod", "chips", "czips", "popcorn", "nachos", "paluszk", "baton", "czekolad", "zelk", "zelek", "sernik",
               "ciast", "drozdzowk", "chrupk", "precl", "orzeszk")
ADJECTIVE_ENDINGS = ("owa", "owe", "owy", "owych", "owej", "owym")


def _words(text: str) -> list[str]:
    """Słowa życzenia ważne dla dopasowania: bez słów pustych i bez tego, czego danie ma nie mieć („bez cebuli”)."""
    out: list[str] = []
    skip = False
    for w in normalize(text).split():
        if w == "bez":
            skip = True
            continue
        if skip:
            skip = False
            continue
        if len(w) >= 3 and w not in DISH_STOPWORDS:
            out.append(w)
    return out


def _stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word[:4]


def _stems(text: str) -> list[str]:
    out: list[str] = []
    for w in _words(text):
        for s in [_stem(w), *DISH_SYNONYMS.get(w, [])]:
            if s not in out:
                out.append(s)
    return out


def is_snack_request(req: WishDish) -> bool:
    if req.posilek == "przekaska":
        return True
    if req.posilek is not None or req.zupa_na_zapas:
        return False
    words = _words(req.opis)
    # „lody”, „chipsy”, „kawałek sernika” tak; „czekoladowe naleśniki” (przymiotnik) nie
    return bool(words) and words[0].startswith(SNACK_HEADS) and not words[0].endswith(ADJECTIVE_ENDINGS)


def request_match_problem(opis: str, name: str) -> str | None:
    """Czy nazwa (wymyślonego) dania odpowiada życzeniu: co najmniej ⅔ słów życzenia — rdzeniem albo synonimem —
    jest w nazwie. Chroni przed daniem „na temat obok” (życzenie „lody” → wymyślony kotlet schabowy)."""
    words = _words(opis)
    if not words:
        return None
    target = normalize(name)
    hits = sum(1 for w in words if any(s in target for s in (_stem(w), *DISH_SYNONYMS.get(w, []))))
    if hits * 3 >= len(words) * 2:
        return None
    return f"nazwa „{name}” nie odpowiada zamówionemu daniu „{opis}”"


def _candidate_ids(req: WishDish, pools: Pools, dishes: Mapping[str, DishTemplate]) -> list[str]:
    if req.posilek == "przekaska":
        return sorted(d for d in pools.treat_snacks if d in dishes)
    soup = req.zupa_na_zapas or "zup" in normalize(req.opis)
    if soup:
        return sorted({d for ids in pools.batch.values() for d in ids})
    if req.posilek:
        return sorted(d for d in pools.by_meal.get(MealType(req.posilek), []) if d in dishes)
    # posiłek nieznany: wszystkie posiłki i przekąski z puli 20% („w sobotę lody”)
    return sorted({d for ids in pools.by_meal.values() for d in ids if d in dishes}
                  | {d for d in pools.treat_snacks if d in dishes})


def shortlist_dishes(req: WishDish, pools: Pools, dishes: Mapping[str, DishTemplate], products: Mapping[str, Product],
                     k: int = SHORTLIST) -> list[tuple[str, float]]:
    """Kandydaci z biblioteki: słowa z życzenia w nazwie dania liczą się podwójnie, w składnikach pojedynczo; przy
    remisie wygrywa nazwa najbliższa życzeniu („zupa pomidorowa” → najkrótsza z zup pomidorowych)."""
    return [(did, score) for did, score, _ in _scored(req, pools, dishes, products)[:k]]


def _scored(req: WishDish, pools: Pools, dishes: Mapping[str, DishTemplate],
            products: Mapping[str, Product]) -> list[tuple[str, float, bool]]:
    """(id, wynik, czy wszystkie słowa życzenia są w nazwie dania) — posortowane od najlepszego."""
    stems = _stems(req.opis)
    want = normalize(req.opis)
    scored = []
    for did in _candidate_ids(req, pools, dishes):
        dish = dishes[did]
        name = normalize(dish.name)
        ingredients = " ".join(normalize(products[i.product_id].name) for i in dish.ingredients
                               if i.role is not Role.SPICE and i.product_id in products)
        in_name = sum(1 for s in stems if s in name)
        in_ing = sum(1 for s in stems if s not in name and s in ingredients)
        score = 2 * in_name + in_ing + fuzz.token_set_ratio(want, name) / 100
        whole = bool(stems) and all(any(s in w for w in name.split()) for s in _stems_plain(req.opis))
        scored.append((did, round(score, 3), whole, fuzz.ratio(want, name)))
    scored.sort(key=lambda x: (-x[1], -x[3], dishes[x[0]].name))
    return [(did, score, whole) for did, score, whole, _ in scored]


def _stems_plain(text: str) -> list[str]:
    """Rdzenie samych słów z życzenia (bez synonimów) — do sprawdzenia, czy nazwa dania mówi o tym samym."""
    return [_stem(w) for w in _words(text)]


class DishPick(BaseModel):
    nr: int = Field(ge=1, description="numer życzenia z listy")
    danie: str | None = Field(default=None, description="id wybranego dania albo null, gdy żadne nie pasuje")


class DishPicks(BaseModel):
    wybory: list[DishPick]


PICK_SYSTEM = """Dopasowujesz dania z życzeń domowników do dań z biblioteki aplikacji z jadłospisami.
Dla każdego życzenia wybierz z jego listy kandydatów danie, które domownik rozpozna jako to, o co prosił:
zgodne rodzajem dania i najważniejszymi składnikami (np. „schabowy z piersi kurczaka z ziemniakami i surówką” =
kotlet z kurczaka w panierce z ziemniakami i surówką; „zupa pomidorowa” = zupa pomidorowa, a nie krem z soczewicą;
„pizza” = pizza; „lody” = lody, a nie deser z lodami na kolację). Drobne różnice w dodatkach są w porządku.
Jeśli żaden kandydat nie pasuje — danie: null. Używaj wyłącznie identyfikatorów z list."""


def resolve_requested_dishes(llm, requests: list[WishDish], pools: Pools, dishes: Mapping[str, DishTemplate],
                             products: Mapping[str, Product]) -> tuple[list[RequestedDish], float]:
    """Dopasowanie dań z życzeń do biblioteki. Bez modelu: najlepszy kandydat, gdy nazwa wyraźnie pasuje."""
    out: list[RequestedDish] = []
    for req in requests:
        meal = (MealType.SNACK if req.posilek == "przekaska" else MealType.LUNCH if req.zupa_na_zapas
                else MealType(req.posilek) if req.posilek else None)
        top = shortlist_dishes(req, pools, dishes, products)
        out.append(RequestedDish(request=req, meal=meal, days=parse_days(req.dni), candidates=[d for d, _ in top]))
    cost = 0.0
    todo = [r for r in out if r.candidates]
    if not todo:
        return out, cost
    snacks = set(pools.treat_snacks)

    def settle() -> None:
        # przekąska z puli 20% wybrana dla życzenia bez posiłku („w sobotę lody”) zajmuje slot przekąski
        for r in todo:
            if r.dish_id in snacks:
                r.meal = MealType.SNACK

    if llm is not None:
        blocks = []
        for n, r in enumerate(todo, start=1):
            lines = [f"  - {d} | {dishes[d].name} | {dishes[d].description or ''}"[:220] for d in r.candidates]
            blocks.append(f"{n}. Życzenie: {r.request.opis}\n  Kandydaci:\n" + "\n".join(lines))
        res = llm.complete_json("zyczenia_dania", PICK_SYSTEM, "\n\n".join(blocks), DishPicks, temperature=0.0,
                                max_tokens=600)
        cost = res.cost_usd
        used: set[str] = set()
        for pick in res.value.wybory:
            if 1 <= pick.nr <= len(todo) and pick.danie in todo[pick.nr - 1].candidates and pick.danie not in used:
                todo[pick.nr - 1].dish_id = pick.danie
                used.add(pick.danie)
        settle()
        return out, cost
    used = set()
    for r in todo:
        # bez modelu przyjmujemy tylko wyraźne dopasowanie: wszystkie słowa życzenia w nazwie dania
        best = next((x for x in _scored(r.request, pools, dishes, products) if x[0] not in used), None)
        if best is not None and best[2]:
            r.dish_id = best[0]
            used.add(best[0])
    settle()
    return out, cost
