"""Generowanie nowych szablonów dań przez LLM z twardą walidacją w kodzie.

LLM proponuje przepis (produkty z bazy, role, typowe gramatury dla osoby 2000 kcal, kroki po polsku).
Kod odrzuca wszystko, co łamie zasady, jest nierealne albo dubluje istniejące danie.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError
from rapidfuzz import fuzz

from ..domain.assumptions import Assumptions
from ..domain.culinary import protect_recipe
from ..domain.dish import (
    DishIngredient,
    DishStatus,
    DishTag,
    DishTemplate,
    IngredientGroup,
    MealType,
    Portioning,
    Role,
)
from ..domain.product import CATEGORY_LABELS_PL, Category, Product
from ..engine.settings import EngineSettings
from ..food.matcher import MatchStatus, ProductMatcher
from ..planner.pools import build_pools
from ..textnorm import normalize
from .client import BudgetExceeded, LLMClient


class GenIngredient(BaseModel):
    produkt: str = Field(description="id produktu z listy")
    rola: Role
    porcjowanie: Portioning = Portioning.PER_PERSON
    typowo_g: float = Field(gt=0, le=900, description="gramy dla osoby 2000 kcal (surowe/suche jak w bazie)")
    opcjonalny: bool = False
    grupa: str | None = Field(default=None, description="nazwa masy, do której należy składnik (np. 'ciasto naleśnikowe', "
                              "'panierka', 'masa mielona', 'farsz', 'sos', 'marynata', 'surówka'); brak = składnik osobny")


class GenDish(BaseModel):
    id: str = Field(description="slug: małe litery, cyfry, podkreślenia, bez polskich znaków")
    nazwa: str
    opis: str
    posilki: list[MealType]
    kuchnia: str
    tagi: list[str] = Field(default_factory=list)
    skladniki: list[GenIngredient] = Field(min_length=2, max_length=14)
    przygotowanie: list[str] = Field(min_length=1, max_length=8)
    czas_min: int = Field(ge=1, le=240)
    na_ile_dni: int | None = None


class GenBatch(BaseModel):
    dania: list[GenDish]


GEN_SYSTEM = """Jesteś doświadczonym kucharzem i dietetykiem. Tworzysz przepisy do polskiej aplikacji z jadłospisami
dla domowników mieszkających w Rumunii (zakupy w zwykłych sklepach, np. Lidl). Gramatury dokładne policzy program —
Ty podajesz TYPOWE porcje składników dla jednej osoby jedzącej 2000 kcal dziennie, jak w dobrej książce kucharskiej.

Role składników:
- bialko: główne źródło białka (mięso, drób, ryby, jajka, twarde źródła nabiałowe, strączki),
- weglowodany: główne źródło węglowodanów (pieczywo, ryż, makaron, ziemniaki),
- tluszcz: tłuszcz dodany (oliwa, masło, olej, orzechy jako tłuszcz, majonez),
- warzywa, owoce, nabial (np. jogurt jako baza), sos (np. passata, mleczko kokosowe), dodatek (np. parmezan do posypania),
- przyprawa: sól, pieprz, zioła — małe ilości.
Porcjowanie: "osobno" (każdy talerz osobno — mięso w kawałkach, ryż, pieczywo), "z_garnka" (jednorodny sos/zupa
z jednego garnka — dzielony proporcjonalnie), "po_rowno" (tyle samo dla każdego).
Ilości: mięso/ryba surowe 120–200 g, ryż/makaron suche 60–100 g, ziemniaki surowe 200–350 g, pieczywo 60–120 g,
oliwa/olej 5–12 g, warzywa 80–250 g, przyprawy do 3 g.
BIAŁKO: każde danie (śniadanie, obiad i kolacja — także sałatki, kanapki i słodkie kolacje; wyjątek tylko pula_20)
przy typowych porcjach ma dać 35–45 g białka. Policz je z zawartości białka produktów podanej przy liście.
MASY I PROPORCJE: składnikom jednej masy (ciasto, panierka, masa mielona, farsz, sos, marynata, surówka) nadaj to samo
pole "grupa" i to samo porcjowanie. Program może zrobić więcej albo mniej masy, ale zachowa proporcje z Twoich gramatur,
więc typowe gramatury składników masy muszą oddawać prawdziwy przepis, np. ciasto naleśnikowe: mąka 60 g, mleko 150 g,
jajko 30 g, olej 5 g; panierka do kotleta 150 g: jajko 15 g, bułka tarta 20 g, mąka 8 g (mięso w tej samej grupie).
Przyprawy nie należą do grup. Ciasto, placki i panierka MUSZĄ mieć grupę obejmującą wszystkie składniki masy;
w panierce także mięso. Brak proporcji powoduje odrzucenie przepisu. Nie dobieraj niezależnie mąki, jajek i mleka
pod makro. Kanapki nie potrzebują grupy masy, ale pieczywo i główne nadzienie muszą mieć rozsądną proporcję:
program zachowa ją (±25%). Zamknięty tost: 2 kromki (50 g) i co najmniej 20 g sera, jeśli jest serowy.
Nazwy grup po polsku z polskimi znakami (np. "ciasto naleśnikowe", "surówka"). Każdy produkt tylko RAZ na liście
składników — gdy mąka jest w cieście i do podsypania, podaj ją raz, łącznie.
Śniadania i obiady są WYTRAWNE (bez cukru, dżemu, słodkich dodatków); na słodko wyłącznie kolacje z tagiem slodkie.
Nazwy, opisy i kroki WYŁĄCZNIE po polsku, poprawną polszczyzną, bez angielskich słów. Kroki krótkie i konkretne.
Nazwa i opis mogą wymieniać tylko składniki, które są na liście składników dania. Nazwy zgodne z klasyką
(np. margherita to tylko sos pomidorowy i mozzarella). W opisach nie używaj nazw technicznych (tagi, "pula 20",
"z_garnka") i nie podawaj kalorii."""


@dataclass
class GenerationResult:
    accepted: list[DishTemplate] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0


# "wrap", "bowl", "pasta" (np. pasta jajeczna), "tost" to słowa używane w polskich przepisach — nie są tu błędem
ENGLISH_WORDS = {"with", "and", "chicken", "salad", "sauce", "beef", "pork", "rice", "cheese", "egg", "eggs", "the",
                 "of", "fried", "baked", "grilled", "soup", "fish", "breakfast", "dinner", "lunch"}
# słowa nazywające TYP dania, a nie składnik (tortilla hiszpańska, tosty, pizza...)
DISH_TYPE_WORDS = {"tortilla", "tortille", "tortilli", "tosty", "tost", "tostow", "pizza", "pizzy", "burger", "burgera",
                   "kebab", "curry", "pasta", "pasty", "zapiekanka", "zapiekanki", "wrap", "bowl", "sushi", "risotto",
                   "burrito", "tacos", "taco", "sajgonki", "falafel", "falafele", "falafelem", "gyros", "lasagne",
                   "quesadilla", "quesadille", "fajitas", "enchiladas", "szawarma", "kofta", "nachos", "frytkami"}
MAIN_CARB_FIXABLE = {"bulka_tarta", "maka_pszenna", "maka_ziemniaczana"}
# od tylu gramów słodyczy (cukier, miód, dżem, czekolada) danie śniadaniowe/obiadowe uznajemy za słodkie
# (teriyaki czy sos słodko-kwaśny mają ok. 10 g miodu na porcję i zostają wytrawne)
SWEET_DISH_G = 15
# śniadanie i obiad (poza pulą 20%) muszą mieć prawdziwe źródło węglowodanów — dzienne cele węglowodanów domowników
# nie domkną się, gdy kolacja jest lekka, a śniadanie to sam omlet
MIN_MAIN_MEAL_CARBS_G = 25
MAX_DISH_FAT_G = 55  # typowa porcja (2000 kcal) — powyżej tego silnik nie zmieści tłuszczu w dniu
SPARE_PER_ORDER = 1  # dań zapasowych na zamówienie z limitem — na wypadek odrzucenia w teście silnika lub przez kucharza
# zupa na zapas to cały obiad: przy talerzu mężczyzny 820 g i kotwicy białka 45–55 g musi być gęsta. Test odporności
# zup (13.09.2026): 400–600 g bulionu na porcję osoby referencyjnej = zupa mieściła się w 0 z 8 dwudniowych bloków
SOUP_LIQUIDS = frozenset({"bulion_warzywny", "bulion_drobiowy", "woda"})
MAX_SOUP_LIQUID_G = 300
MAX_SOUP_PORTION_G = 950
# nazwy obcych dań przyjęte w polszczyźnie — nie są błędem
LOANWORDS = {"szakszuka", "tofu", "skyr", "halloumi", "wrap", "stir", "fry", "bruschetta", "quesadilla", "frittata",
             "nachos", "tapas", "margherita", "paella", "risotto", "curry", "teriyaki", "pho", "feta", "mozzarella",
             "parmezan", "gouda", "cheddar", "hummus", "tortilla", "pesto", "gnocchi", "lasagne", "carbonara",
             "bolognese", "tikka", "masala", "burrito", "tacos", "kebab", "gyros", "falafel", "tzatziki", "pita",
             "minestrone", "bowl", "caprese", "ricotta", "mascarpone", "grana", "padano", "chili", "con", "carne",
             "fajitas", "omlet", "tosty", "hot", "dog", "penne", "spaghetti", "tagliatelle", "fusilli", "dal", "korma"}
FOREIGN_FUNCTION_WORDS = {"y", "al", "de", "la", "el", "del", "alla", "e", "con", "los", "las", "di", "le"}


def foreign_name_words(name: str) -> list[str]:
    """Słowa nazwy spoza polskiego słownika (poza przyjętymi nazwami dań). >= 2 takie słowa lub obcy spójnik = błąd."""
    try:
        import simplemma
    except Exception:  # bez słownika nie oceniamy
        return []
    tokens = [t for t in re.findall(r"[^\W\d_]+", name.lower())]
    foreign = [t for t in tokens if normalize(t) in FOREIGN_FUNCTION_WORDS and t not in ("e",)]
    if "chili con carne" in name.lower():
        foreign = [t for t in foreign if t != "con"]
    unknown = [t for t in tokens if len(t) >= 4 and normalize(t) not in LOANWORDS
               and not simplemma.is_known(t, lang="pl")]
    return foreign + unknown if (foreign or len(unknown) >= 2) else []
_SLUG = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def products_block(products: Mapping[str, Product], a: Assumptions) -> str:
    """Lista produktów dla modelu: id (nazwa, białko w 100 g) — żeby model sam policzył białko dania."""
    banned = set(a.prohibitions.products)
    by_cat: dict[Category, list[str]] = {}
    for p in sorted(products.values(), key=lambda p: normalize(p.name)):
        if p.id in banned or p.id in ("woda", "kawa_napar"):
            continue
        label = p.name if p.category is Category.SPICES else f"{p.name}, {p.per_100g.protein:.0f} g"
        by_cat.setdefault(p.category, []).append(f"{p.id} ({label})")
    return ("Format: id (nazwa, gramy białka w 100 g)\n"
            + "\n".join(f"- {CATEGORY_LABELS_PL[c]}: " + "; ".join(items) for c, items in by_cat.items()))


def rules_block(a: Assumptions) -> str:
    carbs = ", ".join(a.prohibitions.allowed_carb_categories)
    forbidden = {m.value: t for m, t in a.prohibitions.dish_tags_by_meal.items()}
    anchors = "; ".join(f"{p.name.lower()} {p.protein_anchor.min:.0f}–{p.protein_anchor.max:.0f} g" for p in a.persons)
    return "\n".join([
        f"- Białko na posiłek u domowników: {anchors} (program zwiększa porcje osobie, która je więcej) — dlatego"
        " danie przy typowych porcjach (osoba 2000 kcal) potrzebuje 35–45 g białka.",
        f"- Główne źródło węglowodanów tylko z kategorii: {carbs} (bez kasz i płatków). Wyjątek: danie z tagiem pula_20.",
        f"- Zakazane produkty: {', '.join(a.prohibitions.products)}.",
        f"- Zakazane tagi w posiłkach: {forbidden} (np. brak słodkich śniadań i obiadów).",
        "- Kolacje lekkie: sałatki białkowe, kanapki, serki, omlety, szakszuka, wrapy — BEZ ryżu, ziemniaków i makaronu"
        " (do kolacji tylko pieczywo albo brak węglowodanów), nie typowe obiady. Wyjątek: słodka kolacja z tagiem"
        " slodkie może mieć ryż albo makaron (np. ryż na mleku z owocami).",
        "- Tag slodkie: tylko kolacje (np. skyr z owocami, placuszki białkowe).",
        "- Tag pula_20: celowo 'brudne' jedzenie (pizza, burger, lasagne, tosty z dużą ilością sera, fast food).",
        "- Zupa na zapas: tagi [zupa, na_zapas, na_cieplo], na_ile_dni: 2, posiłek obiad, sycąca, z wkładką białkową."
        f" To cały obiad, więc gęsta: najwyżej {MAX_SOUP_LIQUID_G} g bulionu na porcję, dużo wkładki (mięso, strączki,"
        f" ziemniaki albo makaron), podana z pieczywem; cała porcja najwyżej {MAX_SOUP_PORTION_G} g. Bulionu nie dawaj"
        " do grupy proporcji (ilość płynu to gęstość zupy, nie przepis).",
        "- Dozwolone tagi: slodkie, pula_20, na_zapas, zupa, wege, na_cieplo, szybkie.",
    ])


MEAL_WHAT_PL = {
    MealType.BREAKFAST: "śniadań (wytrawnych)",
    MealType.LUNCH: "obiadów",
    MealType.SUPPER: "kolacji (lekkich)",
}


def existing_block(existing_names: list[str]) -> str:
    return ("Te dania JUŻ SĄ w bibliotece — nie powtarzaj ich ani bardzo podobnych (inna nazwa tego samego dania też"
            " jest powtórką):\n- " + "\n- ".join(sorted(set(existing_names))))


def build_gen_prompt(meal: MealType, cuisine: str, count: int, special: str | None, products: Mapping[str, Product],
                     a: Assumptions, existing_names: list[str]) -> str:
    extra = f"\nSzczególnie: {special}" if special else ""
    return (
        f"Stwórz {count} różnych {MEAL_WHAT_PL[meal]} kuchni: {cuisine}.{extra}\n\n"
        f"{existing_block(existing_names)}\n\n"
        f"Zasady użytkowników:\n{rules_block(a)}\n\n"
        f"Używaj WYŁĄCZNIE tych produktów (pole 'produkt' = id):\n{products_block(products, a)}"
    )


def _polish_ok(text: str) -> bool:
    words = set(normalize(text).split())
    return not (words & ENGLISH_WORDS)


TECH_WORDS = re.compile(r"_|kcal", re.IGNORECASE)


def mentioned_missing_products(dish: DishTemplate, matcher: ProductMatcher, banned: set[str] | None = None) -> list[str]:
    """Produkty wymienione w nazwie/opisie (pojedyncze słowa będące aliasami), których nie ma w składnikach.

    Pomija: słowa po "bez", słowa z nazw/aliasów składników dania, słowa nazywające typ dania, produkty z tej samej
    kategorii co któryś składnik ("z pomidorami" przy pomidorach z puszki). Zakazany produkt w tekście — zawsze błąd.
    """
    from ..food.matcher import _lemma, words

    banned = banned or set()
    have = {i.product_id for i in dish.ingredients}
    have_categories = {matcher.products[h].category for h in have if h in matcher.products}
    have_tokens: set[str] = set()
    for pid in have:
        product = matcher.products[pid]
        for name in [product.name, *product.aliases]:
            have_tokens |= {_lemma(w) for w in words(name)}
    missing: list[str] = []
    ws = words(f"{dish.name} {dish.description or ''}")
    for i, w in enumerate(ws):
        if len(w) < 4 or (i > 0 and ws[i - 1] == "bez") or normalize(w) in DISH_TYPE_WORDS:
            continue
        lem = _lemma(w)
        pid = matcher.lemmas.get(lem)
        if not pid or pid in have:
            continue
        product = matcher.products[pid]
        if pid in banned:
            missing.append(f"{w} → {pid} (produkt zakazany)")
            continue
        if lem in have_tokens or product.category.value == "przyprawy" or product.category in have_categories:
            continue
        missing.append(f"{w} → {pid}")
    return missing


def build_groups(ingredients: list[DishIngredient], group_names: Mapping[str, str]) -> list[IngredientGroup]:
    """Grupy z pola 'grupa' składników: min. 2 składniki; różne porcjowanie w masie ujednolicamy (większość wygrywa),
    bo masa z jednej miski jest albo dzielona z garnka, albo porcjowana osobno — nigdy jedno i drugie."""
    from collections import Counter

    members: dict[str, list[DishIngredient]] = {}
    for ing in ingredients:
        name = group_names.get(ing.product_id)
        if name:
            members.setdefault(name, []).append(ing)
    groups: list[IngredientGroup] = []
    for name, items in members.items():
        if len(items) < 2:
            continue
        common = Counter(i.portioning for i in items).most_common(1)[0][0]
        for i in items:
            if i.portioning is not common:
                i.portioning = common
        # duża grupa to zwykle całe danie z garnka (gulasz, curry) — ciasto i panierkę trzymamy ściśle, a przy gulaszu
        # silnik może dołożyć trochę mięsa kosztem sosu, żeby trafić w białko
        tolerance = 0.15 if len(items) >= 7 else 0.10 if len(items) >= 5 else None
        groups.append(IngredientGroup(name=name, products=[i.product_id for i in items], tolerance=tolerance))
    return groups


def _cuisine(value: str, a: Assumptions) -> str:
    key = normalize(value).replace(" ", "_")
    for c in a.cuisines:
        if key.startswith(c[:5]):
            return c
    return "miedzynarodowa"


def validate_generated(gen: GenDish, products: Mapping[str, Product], matcher: ProductMatcher, a: Assumptions,
                       existing: Mapping[str, DishTemplate], settings: EngineSettings,
                       source: str) -> tuple[DishTemplate | None, list[str]]:
    reasons: list[str] = []
    if not _SLUG.match(gen.id):
        gen.id = re.sub(r"[^a-z0-9]+", "_", normalize(gen.nazwa)).strip("_")
    if gen.id in existing:
        if fuzz.ratio(normalize(existing[gen.id].name), normalize(gen.nazwa)) >= 88:
            reasons.append(f"to samo danie już istnieje: {existing[gen.id].name}")
        else:
            base, n = gen.id, 2
            while f"{base}_{n}" in existing:
                n += 1
            gen.id = f"{base}_{n}"
    ingredients: list[DishIngredient] = []
    group_names: dict[str, str] = {}  # produkt -> nazwa grupy
    for ing in gen.skladniki:
        pid = ing.produkt.strip()
        if pid not in products:
            m = matcher.match(pid)
            if m.status is MatchStatus.SURE:
                pid = m.product_id
            elif ing.rola is Role.SPICE:
                continue  # przyprawa spoza bazy nie zmienia bilansu — pomijamy ją zamiast odrzucać całe danie
            else:
                reasons.append(f"nieznany produkt '{ing.produkt}'")
                continue
        product = products[pid]
        n = product.per_100g
        role = ing.rola
        typical = ing.typowo_g
        if role is Role.PROTEIN and n.protein < 7 and product.category not in (Category.LEGUMES,):
            if product.category in (Category.DAIRY, Category.CHEESE):
                role = Role.DAIRY  # np. jogurt w roli białka — poprawiamy rolę zamiast odrzucać danie
            else:
                reasons.append(f"{pid} ma za mało białka na rolę 'bialko' ({n.protein:.1f} g/100 g)")
        if role is Role.CARBS and n.carbs < 12:
            reasons.append(f"{pid} ma za mało węglowodanów na rolę 'weglowodany'")
        if (role is Role.SPICE or product.negligible) and typical > 5:
            typical = 5
        if product.max_portion_g and typical > product.max_portion_g:
            typical = product.max_portion_g  # typowa porcja nie może przekraczać limitu produktu
        if product.category is Category.FATS and typical > 25:
            reasons.append(f"{pid}: za dużo tłuszczu ({typical} g)")
        grp_name = " ".join((ing.grupa or "").strip().lower().split())[:60]
        in_group = bool(grp_name) and role is not Role.SPICE and not product.negligible and not ing.opcjonalny
        previous = next((i for i in ingredients if i.product_id == pid), None)
        if previous is not None:
            # ten sam produkt drugi raz (mąka w cieście i do podsypania, olej w cieście i do smażenia): jedna pozycja
            # z sumą gramów; w masie zostaje tylko wtedy, gdy dodatek jest mały (≤ 25%) — inaczej zaburzyłby proporcje
            old_group = group_names.get(pid)
            keep_group = old_group or (grp_name if in_group else None)
            small = min(previous.typical_g, typical) <= 0.25 * max(previous.typical_g, typical)
            if (old_group and in_group and grp_name != old_group) or (bool(old_group) != in_group and not small):
                keep_group = None
            previous.typical_g = round(previous.typical_g + typical)
            if keep_group:
                group_names[pid] = keep_group
            else:
                group_names.pop(pid, None)
            continue
        if in_group:
            group_names[pid] = grp_name
        ingredients.append(DishIngredient(product_id=pid, role=role, portioning=ing.porcjowanie,
                                          typical_g=round(typical), optional=ing.opcjonalny,
                                          unit="szt" if pid == "jajko_kurze" and typical % 50 == 0 and not in_group
                                          else None))
    # bułka tarta/mąka jako "węglowodany" obok dozwolonego źródła (ziemniaki, ryż...) → to dodatek
    allowed_carbs = set(a.prohibitions.allowed_carb_categories)
    has_allowed_carb = any(i.role is Role.CARBS and products[i.product_id].category.value in allowed_carbs
                           for i in ingredients)
    if has_allowed_carb:
        for i in ingredients:
            if i.role is Role.CARBS and i.product_id in MAIN_CARB_FIXABLE:
                i.role = Role.ADDON
    if reasons:
        return None, reasons
    groups = build_groups(ingredients, group_names)
    tags = [t for t in dict.fromkeys(gen.tagi) if t in {x.value for x in DishTag}]
    if DishTag.BATCH.value in tags and DishTag.SOUP.value not in tags:
        tags.remove(DishTag.BATCH.value)  # na zapas planujemy tylko zupy — reszta jako zwykłe danie
    batch_days = (gen.na_ile_dni or 2) if DishTag.BATCH.value in tags else None
    try:
        dish = DishTemplate(
            id=gen.id, name=gen.nazwa.strip(), description=gen.opis.strip(), meal_types=list(dict.fromkeys(gen.posilki)),
            cuisine=_cuisine(gen.kuchnia, a), tags=tags, ingredients=ingredients,
            steps=[s.strip() for s in gen.przygotowanie if s.strip()], prep_minutes=gen.czas_min,
            batch_days=batch_days, source=source, groups=groups,
            status=DishStatus.APPROVED,
        )
    except ValidationError as exc:
        return None, [f"struktura: {e['msg']}" for e in exc.errors()]
    dish = protect_recipe(dish, products)
    reasons.extend(dish.culinary_errors)
    if MealType.SNACK in dish.meal_types:
        reasons.append("generator nie tworzy przekąsek")
    if dish.is_batch and (MealType.LUNCH not in dish.meal_types or DishTag.SOUP.value not in dish.tags):
        reasons.append("danie na zapas musi być zupą na obiad")
    if dish.is_sweet and any(m in (MealType.BREAKFAST, MealType.LUNCH) for m in dish.meal_types):
        reasons.append("słodkie danie na śniadanie/obiad jest zakazane")
    sweets_g = sum(i.typical_g for i in dish.ingredients if products[i.product_id].category is Category.SWEETS)
    if not dish.is_treat and sweets_g >= SWEET_DISH_G and any(m in (MealType.BREAKFAST, MealType.LUNCH)
                                                               for m in dish.meal_types):
        reasons.append(f"danie jest na słodko ({sweets_g:g} g cukru/miodu/dżemu) — śniadania i obiady mają być wytrawne")
    text = " ".join([dish.name, dish.description or "", *dish.steps])
    foreign = foreign_name_words(dish.name)
    if foreign:
        reasons.append(f"nazwa nie jest po polsku ({', '.join(foreign)}) — przetłumacz, np. 'kurczak z czosnkiem po hiszpańsku'")
    if not _polish_ok(text):
        reasons.append("tekst zawiera angielskie słowa")
    if TECH_WORDS.search(text):
        reasons.append("tekst zawiera nazwy techniczne (tagi/podkreślenia/kcal)")
    missing_mentions = mentioned_missing_products(dish, matcher, set(a.prohibitions.products))
    if missing_mentions:
        reasons.append("nazwa/opis wspomina produkty spoza składników: " + ", ".join(missing_mentions))
    for other in existing.values():
        if fuzz.token_sort_ratio(normalize(other.name), normalize(dish.name)) >= 88:
            reasons.append(f"zbyt podobne do istniejącego: {other.name}")
            break
        a_set = {i.product_id for i in other.ingredients if i.role is not Role.SPICE}
        b_set = {i.product_id for i in dish.ingredients if i.role is not Role.SPICE}
        if a_set and len(a_set & b_set) / len(a_set | b_set) >= 0.85 and set(other.meal_types) & set(dish.meal_types):
            reasons.append(f"prawie te same składniki co: {other.name}")
            break
    if reasons:
        return None, reasons
    # zasady i wykonalność kotwic białka — tą samą logiką co planer
    pools = build_pools({dish.id: dish}, products, a, settings)
    if dish.id in pools.excluded:
        return None, pools.excluded[dish.id]
    profile = pools.profiles[dish.id]
    in_any_pool = any(dish.id in ids for ids in pools.by_meal.values()) or any(dish.id in ids for ids in pools.batch.values())
    if not in_any_pool:
        return None, ["danie nie pasuje do żadnego posiłku po zastosowaniu zasad"]
    if not dish.is_treat and profile.protein_label == "niskie":
        return None, ["za mało białka przy typowych porcjach"]
    typical = profile.typical
    main_meal = any(m in (MealType.BREAKFAST, MealType.LUNCH) for m in dish.meal_types)
    if not dish.is_treat and main_meal and typical["carbs"] < MIN_MAIN_MEAL_CARBS_G:
        return None, [f"za mało węglowodanów ({typical['carbs']:.0f} g) — śniadanie i obiad potrzebują pieczywa, "
                      "ziemniaków, ryżu, makaronu albo mąki"]
    if not dish.is_treat and typical["fat"] > MAX_DISH_FAT_G:
        return None, [f"za tłuste ({typical['fat']:.0f} g tłuszczu w typowej porcji)"]
    if dish.is_batch and not dish.is_treat:
        soup = []
        liquid = sum(i.typical_g for i in dish.ingredients if i.product_id in SOUP_LIQUIDS)
        if liquid > MAX_SOUP_LIQUID_G:
            soup.append(f"za dużo płynu ({liquid:.0f} g bulionu na porcję, najwyżej {MAX_SOUP_LIQUID_G} g) — zupa na cały "
                        "obiad musi być gęsta: więcej wkładki (mięso, strączki, ziemniaki, makaron) i pieczywo")
        mass = sum(i.typical_g for i in dish.ingredients if not products[i.product_id].negligible)
        if mass > MAX_SOUP_PORTION_G:
            soup.append(f"za duża porcja zupy ({mass:.0f} g, najwyżej {MAX_SOUP_PORTION_G} g) — mniej płynu i wodnistych warzyw")
        grouped = sorted({p for g in dish.groups for p in g.products if p in SOUP_LIQUIDS})
        if grouped:
            soup.append(f"bulion ({', '.join(grouped)}) nie może być w grupie proporcji — ilość płynu to gęstość zupy")
        if soup:
            return None, soup
    return dish, []


def generate(client: LLMClient, meal: MealType, cuisine: str, count: int, special: str | None,
             products: Mapping[str, Product], a: Assumptions, existing: Mapping[str, DishTemplate],
             settings: EngineSettings | None = None) -> GenerationResult:
    settings = settings or EngineSettings()
    matcher = ProductMatcher(products)
    prompt = build_gen_prompt(meal, cuisine, count, special, products, a, [d.name for d in existing.values()])
    res = client.complete_json(f"generacja_dan_{meal.value}_{cuisine}", GEN_SYSTEM, prompt, GenBatch,
                               temperature=0.8, max_tokens=7000)
    out = GenerationResult(cost_usd=res.cost_usd)
    known = dict(existing)
    source = f"LLM {client.settings.model} + walidacja automatyczna DietBot"
    for gen in res.value.dania:
        dish, reasons = validate_generated(gen, products, matcher, a, known, settings, source)
        if dish is None:
            out.rejected.append({"nazwa": gen.nazwa, "id": gen.id, "powody": reasons})
        else:
            out.accepted.append(dish)
            known[dish.id] = dish
    return out


# ---------------------------------------------------------------------------------------------- plan generowania
TOP_UP_PLAN: list[tuple[MealType, str, int, str | None]] = [
    (MealType.SUPPER, "miedzynarodowa", 6, "słodkie kolacje wysokobiałkowe (tag slodkie): skyr, serek wiejski, twarożek z owocami — duże porcje nabiału białkowego"),
    (MealType.SUPPER, "polska", 6, "słodkie kolacje wysokobiałkowe (tag slodkie): placuszki, omlety na słodko, twarożek, skyr"),
    (MealType.BREAKFAST, "polska", 6, "śniadania BEZ jajek: serki, twarożek, wędliny, ryby wędzone z pieczywem"),
    (MealType.BREAKFAST, "miedzynarodowa", 6, "śniadania BEZ jajek: kanapki i tosty na ciepło z kurczakiem, tuńczykiem, serem"),
    (MealType.SUPPER, "polska", 6, "lekkie kolacje bez jajek: sałatki z mięsem lub rybą, kanapki, serki"),
    (MealType.SUPPER, "wege", 6, "wegetariańskie kolacje z dużą ilością białka (skyr, serek wiejski, tofu, strączki, halloumi)"),
    (MealType.LUNCH, "hiszpanska", 6, "obiady po hiszpańsku z ryżem lub ziemniakami"),
    (MealType.LUNCH, "indyjska", 6, "obiady po indyjsku z ryżem, z kurczakiem, indykiem lub wołowiną"),
    (MealType.LUNCH, "wege", 6, "wegetariańskie obiady z dużą ilością białka (tofu, halloumi, strączki, jajka, ser)"),
]

DEFAULT_PLAN: list[tuple[MealType, str, int, str | None]] = [
    (MealType.BREAKFAST, "polska", 6, "kanapki, pasty kanapkowe, twarożki i serki z pieczywem"),
    (MealType.BREAKFAST, "polska", 6, "jajka na różne sposoby z pieczywem i warzywami"),
    (MealType.BREAKFAST, "miedzynarodowa", 6, "tosty, wrapy i kanapki na ciepło, wytrawne"),
    (MealType.BREAKFAST, "hiszpanska", 6, "np. tortilla ziemniaczana, kanapki z pomidorem i szynką"),
    (MealType.BREAKFAST, "wloska", 6, "frittata, kanapki i bruschetty z białkiem"),
    (MealType.BREAKFAST, "wege", 6, "wegetariańskie, z jajkami, nabiałem lub strączkami"),
    (MealType.LUNCH, "polska", 6, "klasyczne polskie obiady w lżejszej wersji"),
    (MealType.LUNCH, "polska", 6, "obiady z ziemniakami, mięsem lub rybą i surówką"),
    (MealType.LUNCH, "wloska", 6, "makarony i risotta z mięsem, rybą lub owocami morza"),
    (MealType.LUNCH, "azjatycka", 6, "stir-fry i dania z ryżem"),
    (MealType.LUNCH, "azjatycka", 6, "curry, słodko-kwaśne, teriyaki z ryżem lub makaronem"),
    (MealType.LUNCH, "indyjska", 6, "curry i dania z ryżem, z kurczakiem, strączkami lub rybą"),
    (MealType.LUNCH, "hiszpanska", 6, "np. paella, gulasze, dania z ziemniakami"),
    (MealType.LUNCH, "miedzynarodowa", 6, "dania z grilla i piekarnika z dodatkami"),
    (MealType.LUNCH, "miedzynarodowa", 6, "bowle, zapiekanki i szybkie dania na co dzień"),
    (MealType.LUNCH, "wege", 6, "wegetariańskie obiady z dużą ilością białka"),
    (MealType.LUNCH, "polska", 4, "zupy na zapas na dwa dni (tagi zupa, na_zapas, na_cieplo; na_ile_dni 2)"),
    (MealType.LUNCH, "miedzynarodowa", 4, "zupy na zapas na dwa dni z różnych kuchni (tagi zupa, na_zapas, na_cieplo; na_ile_dni 2)"),
    (MealType.LUNCH, "miedzynarodowa", 6, "posiłki z puli 20% (tag pula_20): pizza, lasagne, burgery, fast food"),
    (MealType.SUPPER, "polska", 6, "lekkie kolacje: kanapki, twarożki, sałatki z białkiem"),
    (MealType.SUPPER, "wloska", 6, "lekkie kolacje: sałatki, kanapki, frittata"),
    (MealType.SUPPER, "miedzynarodowa", 6, "sałatki białkowe i wrapy"),
    (MealType.SUPPER, "miedzynarodowa", 6, "omlety, szakszuki i lekkie danie na ciepło"),
    (MealType.SUPPER, "azjatycka", 6, "lekkie kolacje w stylu azjatyckim"),
    (MealType.SUPPER, "hiszpanska", 6, "lekkie kolacje w stylu hiszpańskim"),
    (MealType.SUPPER, "wege", 6, "wegetariańskie lekkie kolacje z białkiem"),
    (MealType.SUPPER, "miedzynarodowa", 6, "słodkie kolacje wysokobiałkowe (tag slodkie) — skyr, twarożek, placuszki, owoce"),
    (MealType.SUPPER, "miedzynarodowa", 5, "kolacje z puli 20% (tag pula_20): zapiekanki, hot dogi, nachos z serem"),
]


UNFIXABLE_PREFIXES = ("to samo danie", "zbyt podobne", "prawie te same składniki")


@dataclass
class GenOrder:
    """Zamówienie dla generatora: `ask` propozycji, z których przyjmujemy najwyżej `keep` (None = wszystkie poprawne)."""

    meal: MealType
    cuisine: str
    ask: int
    special: str | None = None
    keep: int | None = None
    max_fat_share: float | None = None  # np. 0,32 — lekkie danie: najwyżej 32% energii z tłuszczu (typowe porcje)
    min_carb_share: float | None = None  # np. 0,40 — najmniej 40% energii z węglowodanów
    # kontrola nazwy (np. danie z życzeń musi być tym, o co proszono): nazwa -> powód odrzucenia albo None
    name_check: Callable[[str], str | None] | None = None

    def macro_rule(self) -> str | None:
        parts = []
        if self.max_fat_share is not None:
            parts.append(f"tłuszcz najwyżej {self.max_fat_share * 100:.0f}% energii dania")
        if self.min_carb_share is not None:
            parts.append(f"węglowodany co najmniej {self.min_carb_share * 100:.0f}% energii")
        if not parts:
            return None
        return ("Rozkład energii przy typowych porcjach (policz z tabeli: białko i węglowodany 4 kcal/g, tłuszcz 9 kcal/g): "
                + ", ".join(parts) + ". Wybieraj chude wersje (twaróg chudy, serek wiejski lekki, jogurt 1,5%, chude "
                "wędliny, białka jaj, mozzarella light), mało masła, oleju i tłustych serów, porządną porcję pieczywa.")

    def macro_problems(self, typical: Mapping[str, float]) -> list[str]:
        kcal = 4 * typical["protein"] + 9 * typical["fat"] + 4 * typical["carbs"]
        if kcal <= 0:
            return []
        out = []
        fat, carbs = 9 * typical["fat"] / kcal, 4 * typical["carbs"] / kcal
        if self.max_fat_share is not None and fat > self.max_fat_share + 1e-9:
            out.append(f"za tłuste jak na zamówione lekkie danie: tłuszcz {fat * 100:.0f}% energii "
                       f"(najwyżej {self.max_fat_share * 100:.0f}%) — zmniejsz tłuste składniki, dodaj pieczywo lub warzywa")
        if self.min_carb_share is not None and carbs < self.min_carb_share - 1e-9:
            out.append(f"za mało węglowodanów: {carbs * 100:.0f}% energii (co najmniej {self.min_carb_share * 100:.0f}%) "
                       "— dodaj pieczywo, ziemniaki, owoce albo tortillę")
        return out


def as_order(item) -> GenOrder:
    if isinstance(item, GenOrder):
        return item
    meal, cuisine, count, special, *rest = item
    return GenOrder(meal, cuisine, count, special, rest[0] if rest else None)


class RevisedBatch(BaseModel):
    dania: list[GenDish]


def _rejections(items: list[tuple[GenDish, list[str]]]) -> str:
    import json

    return "\n\n".join("DANIE:\n" + json.dumps(gen.model_dump(mode="json"), ensure_ascii=False)
                       + "\nPOWODY ODRZUCENIA:\n- " + "\n- ".join(reasons) for gen, reasons in items)


def revision_prompt(items: list[tuple[GenDish, list[str]]], products: Mapping[str, Product], a: Assumptions) -> str:
    return (
        "Poniższe dania zostały odrzucone przez program sprawdzający. Popraw każde tak, aby spełniało zasady, "
        "zachowując charakter dania (np. zwiększ porcję lub dodaj źródło białka, zamień produkt spoza listy na "
        "dostępny, popraw nazwę tak, by zgadzała się ze składnikami, usuń angielskie słowa). "
        "Danie, którego nie da się sensownie poprawić, pomiń.\n\n"
        f"Zasady użytkowników:\n{rules_block(a)}\n\n"
        f"Dostępne produkty (id):\n{products_block(products, a)}\n\n" + _rejections(items)
    )


def refill_prompt(order: GenOrder, missing: int, items: list[tuple[GenDish, list[str]]],
                  products: Mapping[str, Product], a: Assumptions, existing_names: list[str]) -> str:
    """Druga szansa dla zamówienia, które nie dostało dość dań: popraw odrzucone albo wymyśl inne."""
    special = "; ".join(x for x in (order.special, order.macro_rule()) if x)
    extra = f"\nSzczególnie: {special}" if special else ""
    rejected = f"ODRZUCONE PROPOZYCJE:\n{_rejections(items)}\n\n" if items else ""
    return (
        f"Potrzebujemy jeszcze {missing} {MEAL_WHAT_PL[order.meal]} kuchni: {order.cuisine}.{extra}\n"
        f"Podaj {missing + 1} propozycje: popraw odrzucone dania (zachowując ich charakter) albo wymyśl zamiast nich "
        "INNE dania. Danie odrzucone jako powtórka zastąp zupełnie innym pomysłem.\n\n"
        f"{rejected}{existing_block(existing_names)}\n\n"
        f"Zasady użytkowników:\n{rules_block(a)}\n\n"
        f"Używaj WYŁĄCZNIE tych produktów (pole 'produkt' = id):\n{products_block(products, a)}"
    )


def run_generation_plan(repo, client: LLMClient, plan=DEFAULT_PLAN, workers: int = 4, log=print,
                        source: str | None = None, revise: bool = True, review: bool = False,
                        engine_check: bool = False):
    """Generuje dania wg planu równolegle; akceptuje sekwencyjnie (deduplikacja między partiami).

    Zamówienie z limitem (`GenOrder.keep`) przyjmuje najwyżej tyle dań; gdy po pierwszej rundzie brakuje dań,
    model dostaje odrzucone propozycje z powodami i poprawia je albo wymyśla inne (druga runda, równolegle).
    Do testu silnika i kucharza idzie też jedno danie zapasowe na zamówienie (jeśli model dał dość poprawnych
    propozycji) — gdy któreś odpadnie, zapasowe zajmuje jego miejsce; na końcu każde zamówienie ma najwyżej `keep` dań.
    Zwraca (przyjęte, odrzucone propozycje, koszt).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .scope import in_context

    orders = [as_order(x) for x in plan]
    settings = EngineSettings()
    matcher = ProductMatcher(repo.products)
    known = dict(repo.dishes)
    accepted: list[DishTemplate] = []
    rejected: list[dict] = []
    taken = [0] * len(orders)
    order_of: dict[str, int] = {}  # id przyjętego dania -> zamówienie
    pending: list[list[tuple[GenDish, list[str]]]] = [[] for _ in orders]
    cost = 0.0
    source = source or f"LLM {client.settings.model} + walidacja automatyczna DietBot"

    def names_for(meal: MealType) -> list[str]:
        return [d.name for d in known.values() if meal in d.meal_types]

    def missing(i: int) -> int | None:
        keep = orders[i].keep
        return None if keep is None else max(0, keep - taken[i])

    def full(i: int) -> bool:
        keep = orders[i].keep
        return keep is not None and taken[i] >= keep + SPARE_PER_ORDER

    def take(i: int, gens: list[GenDish], suffix: str = "") -> int:
        ok = 0
        for gen in gens:
            if full(i):
                break  # nadmiarowe propozycje — zamówienie pełne razem z daniem zapasowym
            dish, reasons = validate_generated(gen, repo.products, matcher, repo.assumptions, known, settings,
                                               source + suffix)
            if dish is not None and (orders[i].max_fat_share is not None or orders[i].min_carb_share is not None):
                typical = build_pools({dish.id: dish}, repo.products, repo.assumptions, settings).profiles[dish.id].typical
                macro = orders[i].macro_problems(typical)
                if macro:
                    dish, reasons = None, macro
            if dish is not None and orders[i].name_check is not None:
                problem = orders[i].name_check(dish.name)
                if problem:
                    dish, reasons = None, [problem]
            if dish is None:
                order = orders[i]
                rejected.append({"posilek": order.meal.value, "kuchnia": order.cuisine, "nazwa": gen.nazwa,
                                 "powody": reasons, **({"po_poprawce": True} if suffix else {})})
                pending[i].append((gen, reasons))
            else:
                accepted.append(dish)
                known[dish.id] = dish
                order_of[dish.id] = i
                taken[i] += 1
                ok += 1
        return ok

    def parallel(jobs, handle) -> None:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futures = {ex.submit(in_context(fn)): key for key, fn in jobs}
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                except BudgetExceeded:
                    raise  # limit kosztu to nie „nieudana partia” — wynik nie może udawać, że dania odpadły w kontroli
                except Exception as exc:  # pojedyncza partia nie może zatrzymać całości
                    log(f"partia nieudana: {exc}")
                    continue
                handle(futures[fut], res)

    def first_round(i: int):
        o = orders[i]
        special = "; ".join(x for x in (o.special, o.macro_rule()) if x) or None
        prompt = build_gen_prompt(o.meal, o.cuisine, o.ask, special, repo.products, repo.assumptions,
                                  names_for(o.meal))
        return client.complete_json(f"generacja_dan_{o.meal.value}_{o.cuisine}", GEN_SYSTEM, prompt, GenBatch,
                                    temperature=0.8, max_tokens=7000)

    def handle_first(i: int, res) -> None:
        nonlocal cost
        cost += res.cost_usd
        o = orders[i]
        ok = take(i, res.value.dania)
        need = f" (potrzebne {o.keep})" if o.keep is not None else ""
        log(f"{o.meal.value}/{o.cuisine}: przyjęte {ok}/{len(res.value.dania)}{need}, koszt {res.cost_usd:.4f} $")

    parallel([(i, lambda i=i: first_round(i)) for i in range(len(orders))], handle_first)
    if not revise:
        return accepted, rejected, cost

    second: list[tuple[int, object]] = []
    for i, o in enumerate(orders):
        items, pending[i] = pending[i], []
        if o.keep is None:  # biblioteka: poprawiamy tylko to, co da się poprawić
            fixable = [(g, r) for g, r in items if not any(str(x).startswith(UNFIXABLE_PREFIXES) for x in r)]
            if fixable:
                prompt = revision_prompt(fixable, repo.products, repo.assumptions)
                second.append((i, lambda p=prompt, f=fixable: ("poprawa", f, client.complete_json(
                    "poprawa_dan", GEN_SYSTEM, p, RevisedBatch, temperature=0.4, max_tokens=8000))))
        elif missing(i):
            prompt = refill_prompt(o, missing(i), items, repo.products, repo.assumptions, names_for(o.meal))
            second.append((i, lambda p=prompt, f=items: ("uzupelnienie", f, client.complete_json(
                "poprawa_dan", GEN_SYSTEM, p, RevisedBatch, temperature=0.6, max_tokens=7000))))

    def handle_second(i: int, out) -> None:
        nonlocal cost
        kind, items, res = out
        cost += res.cost_usd
        o = orders[i]
        if kind == "poprawa":  # danie pominięte w poprawkach = model nie umiał go poprawić
            returned = {normalize(g.nazwa) for g in res.value.dania} | {g.id for g in res.value.dania}
            for gen, _ in items:
                if normalize(gen.nazwa) not in returned and gen.id not in returned:
                    rejected.append({"posilek": o.meal.value, "kuchnia": o.cuisine, "nazwa": gen.nazwa,
                                     "powody": ["model nie poprawił dania"], "bez_poprawki": True})
        ok = take(i, res.value.dania, " (po poprawce)")
        label = "poprawki" if kind == "poprawa" else "druga runda"
        log(f"{o.meal.value}/{o.cuisine} — {label}: przyjęte {ok}/{len(res.value.dania)}, koszt {res.cost_usd:.4f} $")

    if second:
        parallel(second, handle_second)
    if engine_check and accepted:
        from ..planner.dish_audit import audit_dishes

        passed, failed = audit_dishes(accepted, repo.approved_dishes(), repo.products, repo.assumptions)
        for dish, reasons in failed:
            rejected.append({"nazwa": dish.name, "id": dish.id, "powody": reasons, "test_silnika": True})
            log(f"    test silnika: {dish.name} — {reasons[0]}")
        log(f"test silnika: przeszło {len(passed)}/{len(accepted)}")
        accepted = passed
    if review and accepted:
        from .chef_review import review_dishes

        rv = review_dishes(client, accepted, repo.products, set(repo.assumptions.prohibitions.products),
                           {d.name for d in repo.dishes.values()}, workers=workers, log=log)
        cost += rv.cost_usd
        rejected.extend(rv.rejected)
        log(f"kontrola kucharza: dobre {len(rv.kept) - len(rv.corrected)}, poprawione {len(rv.corrected)}, "
            f"odrzucone {len(rv.rejected)}, koszt {rv.cost_usd:.4f} $")
        for r in rv.rejected:
            log(f"    odrzucone przez kucharza: {r['nazwa']} — {r['powody'][0]}")
        accepted = rv.kept
    # dania zapasowe, które nie były potrzebne (zamówienie ma już `keep` dań po teście silnika i kucharzu)
    kept_per_order = [0] * len(orders)
    final: list[DishTemplate] = []
    for dish in accepted:
        i = order_of.get(dish.id)
        keep = orders[i].keep if i is not None else None
        if keep is not None and kept_per_order[i] >= keep:
            log(f"    danie zapasowe niepotrzebne: {dish.name}")
            continue
        if i is not None:
            kept_per_order[i] += 1
        final.append(dish)
    return final, rejected, cost


def save_generated(dishes: list[DishTemplate], directory) -> dict[str, int]:
    """Dopisuje dania do plików gen_<posiłek>.yaml (składniki w stylu przepływowym dla czytelności)."""
    from pathlib import Path

    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    from ..storage.yamlio import dump_yaml, load_yaml

    directory = Path(directory)
    by_file: dict[str, list[DishTemplate]] = {}
    for d in dishes:
        main = d.meal_types[0].value
        by_file.setdefault(f"gen_{main}.yaml", []).append(d)
    counts: dict[str, int] = {}
    for name, items in by_file.items():
        path = directory / name
        data = load_yaml(path) if path.exists() else CommentedSeq()
        for d in items:
            raw = d.to_data()
            entry = CommentedMap()
            for k, v in raw.items():
                if k == "skladniki":
                    seq = CommentedSeq()
                    for ing in v:
                        m = CommentedMap(ing)
                        m.fa.set_flow_style()
                        seq.append(m)
                    entry[k] = seq
                elif isinstance(v, list) and k in ("posilki", "tagi"):
                    s = CommentedSeq(v)
                    s.fa.set_flow_style()
                    entry[k] = s
                else:
                    entry[k] = v
            data.append(entry)
        dump_yaml(data, path)
        counts[name] = len(items)
    return counts
