"""Teksty od modelu językowego nie mogą zawierać liczb — liczby (kcal, gramy, porcje) podaje wyłącznie program.

Wspólna kontrola dla komentarza tygodnia, czatu, uzasadnień zamian, uwag kucharza i notatnika. Łapie cyfry każdego
zapisu (także „½”, „Ⅻ”) i liczebniki słowne („pół kilo”, „ponad sto gramów”, „tysiąc osiemset”) — audyt 09.10.2026, S12.
"""

from __future__ import annotations

import re

WORD = re.compile(r"[a-ząćęłńóśźż]+")
# liczebniki, które w zdaniu prawie zawsze oznaczają ilość (pozostałe listy dokładają konteksty, np. „raz” w tygodniu)
QUANTITY_WORDS = {
    "pół", "półtora", "półtorej", "ćwierć", "dwa", "dwie", "dwóch", "dwoma", "dwukrotnie", "trzy", "trzech", "trzema",
    "trzykrotnie", "cztery", "czterech", "pięć", "pięciu", "sześć", "sześciu", "siedem", "siedmiu", "osiem", "ośmiu",
    "dziewięć", "dziewięciu", "dziesięć", "dziesięciu", "jedenaście", "dwanaście", "piętnaście", "dwadzieścia",
    "trzydzieści", "czterdzieści", "pięćdziesiąt", "sto", "stu", "dwieście", "trzysta", "czterysta", "pięćset",
    "sześćset", "siedemset", "osiemset", "dziewięćset", "tysiąc", "tysiące", "tysięcy", "procent", "procentów",
}


def has_digits(text: str) -> bool:
    """Cyfry w dowolnym zapisie: 0–9, ułamki („½”), cyfry rzymskie Unicode („Ⅻ”), cyfry innych alfabetów."""
    return any(ch.isdigit() or ch.isnumeric() for ch in text)


def number_words(text: str, extra: set[str] | frozenset[str] = frozenset()) -> set[str]:
    return set(WORD.findall(text.lower())) & (QUANTITY_WORDS | set(extra))


def has_numbers(text: str, extra: set[str] | frozenset[str] = frozenset()) -> bool:
    return has_digits(text) or bool(number_words(text, extra))


def without_numbers(text: str, extra: set[str] | frozenset[str] = frozenset()) -> str:
    """Zdania z liczbami wypadają (reszta zostaje) — jedno zdanie z liczbą nie odrzuca całej odpowiedzi modelu."""
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    return " ".join(s for s in sentences if not has_numbers(s, extra))
