"""Formatowanie liczb i tekstów po polsku."""

from __future__ import annotations


def fmt_num(value: float, decimals: int = 1) -> str:
    """1234.5 -> '1234,5'; wartości całkowite bez części dziesiętnej."""
    if abs(value - round(value)) < 10 ** (-decimals) / 2:
        return str(int(round(value)))
    return f"{value:.{decimals}f}".replace(".", ",")


def fmt_grams(value: float) -> str:
    return f"{fmt_num(value, 0)} g"


def plural_pl(n: int, one: str, few: str, many: str) -> str:
    """plural_pl(2, 'posiłek', 'posiłki', 'posiłków') -> 'posiłki'."""
    n = abs(n)
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
