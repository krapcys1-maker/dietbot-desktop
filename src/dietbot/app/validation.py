"""Komunikaty walidacji po polsku (Pydantic domyślnie mówi po angielsku)."""

from __future__ import annotations

from pydantic import ValidationError

FIELD_LABELS = {
    # pola żądań API (okno aplikacji)
    "wishes": "życzenia", "days": "dni", "week_start": "początek tygodnia", "text": "wiadomość", "request": "prośba",
    "count": "liczba dań", "slot": "posiłek", "slot_key": "posiłek", "dish_id": "danie", "title": "nazwa planu",
    "active": "aktualny", "repair": "naprawa", "proposal_id": "propozycja", "data": "dane", "status": "status",
    "invent": "wymyślanie", "use_llm": "model językowy", "avoid_recent": "bez powtórek", "od": "od", "posilek": "posiłek",
    "q": "szukaj", "tekst": "tekst",
    "osoby": "osoby", "cele_dzienne": "cele dzienne", "kcal": "kcal", "bialko": "białko", "tluszcz": "tłuszcz",
    "weglowodany": "węglowodany", "min": "min", "max": "max", "cel": "cel", "kotwica_bialka_na_posilek": "kotwica białka",
    "tolerancja_kotwicy_g": "tolerancja kotwicy", "dzien": "dzień", "posilki": "posiłki", "stale_pozycje": "stałe pozycje",
    "gramy": "gramy", "produkt": "produkt", "przekaski": "przekąski", "dozwolone": "dozwolone",
    "max_na_osobe_dziennie": "maks. przekąsek dziennie", "tydzien": "tydzień", "posilki_20_procent": "posiłki z puli 20%",
    "slodkie_kolacje_max": "maks. słodkich kolacji", "na_zapas": "gotowanie na zapas", "dni": "dni", "zakazy": "zakazy",
    "produkty": "produkty", "kuchnie": "kuchnie", "nazwa": "nazwa", "na_100g": "wartości na 100 g",
    "kategoria": "kategoria", "stan": "stan",
}


def _loc(loc: tuple, data: dict | None = None) -> str:
    parts = []
    for i, x in enumerate(loc):
        if i == 0 and x in ("body", "query", "path"):  # miejsce w żądaniu HTTP nic nie mówi użytkownikowi
            continue
        if isinstance(x, int):
            name = None
            if data is not None and i > 0 and loc[i - 1] == "osoby":
                persons = data.get("osoby") if isinstance(data, dict) else None
                if isinstance(persons, list) and x < len(persons) and isinstance(persons[x], dict):
                    name = persons[x].get("nazwa")
            parts.append(str(name) if name else f"#{x + 1}")
        else:
            parts.append(FIELD_LABELS.get(str(x), str(x)))
    return " → ".join(parts)


def polish_errors(exc: ValidationError, data: dict | None = None) -> list[str]:
    return polish_error_list(exc.errors(), data)


def polish_error_list(errors: list, data: dict | None = None) -> list[str]:
    """Lista błędów w formacie Pydantic/FastAPI (`type`, `loc`, `msg`, `ctx`) → komunikaty po polsku."""
    out = []
    for e in errors:
        t, ctx = e["type"], e.get("ctx") or {}
        if t == "greater_than_equal":
            msg = f"musi być co najmniej {ctx.get('ge')}"
        elif t == "greater_than":
            msg = f"musi być większe niż {ctx.get('gt')}"
        elif t == "less_than_equal":
            msg = f"może być najwyżej {ctx.get('le')}"
        elif t == "less_than":
            msg = f"musi być mniejsze niż {ctx.get('lt')}"
        elif t == "missing":
            msg = "pole jest wymagane"
        elif t in ("float_parsing", "float_type", "int_parsing", "int_type", "int_from_float"):
            msg = "musi być liczbą" + (" całkowitą" if t.startswith("int") else "")
        elif t in ("string_type", "string_too_short"):
            msg = "musi być tekstem" if t == "string_type" else f"za krótki tekst (min. {ctx.get('min_length')} znaki)"
        elif t == "string_too_long":
            msg = f"za długi tekst (maks. {ctx.get('max_length')} znaków)"
        elif t == "string_pattern_mismatch":
            msg = "niedozwolone znaki"
        elif t in ("too_short", "list_type"):
            msg = "za mało pozycji" if t == "too_short" else "musi być listą"
        elif t == "too_long":
            msg = f"za dużo pozycji (maks. {ctx.get('max_length')})"
        elif t in ("enum", "literal_error"):
            msg = f"niedozwolona wartość; dozwolone: {ctx.get('expected')}"
        elif t == "extra_forbidden":
            msg = "nieznane pole"
        elif t in ("bool_parsing", "bool_type"):
            msg = "musi być tak albo nie"
        elif t in ("date_parsing", "date_type", "date_from_datetime_parsing", "date_from_datetime_inexact"):
            msg = "musi być datą w formacie RRRR-MM-DD"
        elif t in ("dict_type", "model_type", "model_attributes_type"):
            msg = "zły format danych"
        elif t == "json_invalid":
            msg = "nieprawidłowy JSON"
        elif t == "value_error":
            msg = str(ctx.get("error", e["msg"])).removeprefix("Value error, ")
        else:
            msg = e["msg"]
        where = _loc(e["loc"], data)
        out.append(f"{where}: {msg}" if where else msg)
    return out
