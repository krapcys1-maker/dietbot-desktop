"""Przeliczenie przykładowych jadłospisów (tekst z czatu) i porównanie z sumami, które podał czat.

Pokazuje na danych użytkownika, dlaczego liczby w DietBocie liczy kod: czat podaje sumy „na oko”.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..checker.verify import nutrition_of
from ..domain.nutrition import EnergyMethod
from ..domain.product import Product
from .matcher import MatchStatus, ProductMatcher

DAY_RE = re.compile(r"DZIE[ŃN]\s+(\d+)", re.IGNORECASE)
PERSON_RE = re.compile(r"^\s*(Kobieta|Mężczyzna)\s*:\s*(.+?)\s*$")
ONLY_RE = re.compile(r"\(\s*tylko\s+(Kobieta|Mężczyzna)\s*\)\s*:\s*(.+?)\s*$", re.IGNORECASE)
SUMMARY_RE = re.compile(r"(\d[\d  ]*)\s*kcal\s*\|\s*Białko:\s*(\d+(?:[,.]\d+)?)\s*g\s*\|\s*Tłuszcze?:\s*(\d+(?:[,.]\d+)?)\s*g"
                        r"\s*\|\s*Węglowodany:\s*(\d+(?:[,.]\d+)?)\s*g", re.IGNORECASE)
PERSON_IDS = {"kobieta": "kobieta", "mężczyzna": "mezczyzna"}
# słowa, które w przykładach opisują sposób podania, a nie produkt
NOISE = re.compile(r"\b(dodatkowo|na deser|obok|do mizerii|do panierki|do smażenia|na talerz|ze słoika)\b", re.IGNORECASE)


@dataclass
class ExampleItem:
    text: str
    grams: float | None
    product_id: str | None
    status: str  # pewne / niepewne / brak / bez_gramatury / llm


@dataclass
class ExampleDay:
    number: int
    items: dict[str, list[ExampleItem]] = field(default_factory=dict)
    stated: dict[str, dict[str, float]] = field(default_factory=dict)


def split_items(text: str) -> list[str]:
    """Dzieli listę składników po przecinkach, ale nie wewnątrz nawiasów."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return [x.rstrip(".").strip() for x in out if x.rstrip(".").strip()]


def _num(s: str) -> float:
    return float(s.replace(" ", "").replace(" ", "").replace(",", "."))


def parse_example(text: str, matcher: ProductMatcher) -> list[ExampleDay]:
    days: list[ExampleDay] = []
    current: ExampleDay | None = None
    in_summary = False
    for line in text.splitlines():
        m = DAY_RE.search(line)
        if m and ("📅" in line or line.strip().upper().startswith("DZIE")):
            current = ExampleDay(number=int(m.group(1)))
            days.append(current)
            in_summary = False
            continue
        if current is None:
            continue
        if "podsumowanie" in line.lower():
            in_summary = True
            continue
        who, rest = None, None
        pm = PERSON_RE.match(line)
        if pm:
            who, rest = pm.group(1).lower(), pm.group(2)
        else:
            om = ONLY_RE.search(line)
            if om:
                who, rest = om.group(1).lower(), om.group(2)
        if who is None or rest is None:
            continue
        pid = PERSON_IDS[who]
        sm = SUMMARY_RE.search(rest)
        if sm:
            current.stated[pid] = {"kcal": _num(sm.group(1)), "protein": _num(sm.group(2)),
                                   "fat": _num(sm.group(3)), "carbs": _num(sm.group(4))}
            continue
        if in_summary:
            continue
        for raw in split_items(rest):
            cleaned = NOISE.sub(" ", raw)
            r = matcher.match(cleaned)
            if r.grams is None:
                current.items.setdefault(pid, []).append(ExampleItem(raw, None, None, "bez_gramatury"))
                continue
            status = r.status.value
            current.items.setdefault(pid, []).append(
                ExampleItem(raw, r.grams, r.product_id if r.status is MatchStatus.SURE else None, status))
    return days


def resolve_uncertain(days: list[ExampleDay], matcher: ProductMatcher, products: Mapping[str, Product], llm) -> list[str]:
    """Niepewne nazwy rozstrzyga LLM wyłącznie spośród kandydatów kodu; „rozdziel” dzieli gramy po równo."""
    from ..llm.resolve_products import DecisionKind, resolve_with_llm

    pending: dict[str, list[ExampleItem]] = {}
    for d in days:
        for items in d.items.values():
            for it in items:
                if it.product_id is None and it.grams is not None:
                    pending.setdefault(NOISE.sub(" ", it.text), []).append(it)
    if not pending or llm is None:
        return []
    names = list(pending)
    results = [matcher.match(n) for n in names]
    decisions, notes = resolve_with_llm(llm, products, results, matcher)
    for name, dec in zip(names, decisions, strict=True):
        for it in pending[name]:
            if dec.decyzja is DecisionKind.PRODUCT and dec.produkt_id:
                it.product_id, it.status = dec.produkt_id, "llm"
            elif dec.decyzja is DecisionKind.SPLIT and dec.skladniki:
                parts = [matcher.match(s) for s in dec.skladniki]
                sure = [p.product_id for p in parts if p.status is MatchStatus.SURE and p.product_id]
                if len(sure) == len(parts):
                    it.product_id, it.status = "+".join(sure), "llm"
    return notes


def day_totals(day: ExampleDay, products: Mapping[str, Product], fixed: Mapping[str, float] | None = None,
               method: EnergyMethod = EnergyMethod.MACROS_4_9_4) -> dict[str, dict[str, float]]:
    out = {}
    for pid, items in day.items.items():
        grams: dict[str, float] = dict(fixed or {})
        for it in items:
            if it.product_id is None or it.grams is None:
                continue
            ids = it.product_id.split("+")
            for p in ids:
                if p in products:
                    grams[p] = grams.get(p, 0.0) + it.grams / len(ids)
        out[pid] = nutrition_of(grams, products, method)
    return out


def render_report(files: list[tuple[str, list[ExampleDay]]], products: Mapping[str, Product],
                  fixed: Mapping[str, float], notes: list[str]) -> str:
    lines = ["# Przeliczenie przykładowych jadłospisów", "",
             "_Sumy podane przez czat porównane z przeliczeniem DietBota (te same gramatury, wartości z tabel BLS 4.0 "
             "i etykiet, energia 4·B + 9·T + 4·W). „Ze stałymi” = z mlekiem do kawy i siemieniem z założeń._", ""]
    errors: list[float] = []
    unmatched: dict[str, int] = {}
    for name, days in files:
        lines += [f"## {name}", ""]
        rows = [d for d in days if d.stated]
        if not rows:
            lines += ["_Plik nie podaje sum dziennych — pomijam porównanie._", ""]
            continue
        lines += ["| Dzień | Osoba | Czat: kcal / B / T / W | DietBot: kcal / B / T / W | Ze stałymi: kcal | Różnica kcal |",
                  "|---|---|---|---|---:|---:|"]
        for d in rows:
            calc = day_totals(d, products)
            calc_fixed = day_totals(d, products, fixed)
            for pid, st in d.stated.items():
                c = calc.get(pid)
                if c is None:
                    continue
                cf = calc_fixed[pid]
                best = min((c["kcal"], cf["kcal"]), key=lambda v: abs(v - st["kcal"]))
                diff = st["kcal"] - best
                errors.append(abs(diff))
                lines.append(f"| {d.number} | {pid} | {st['kcal']:.0f} / {st['protein']:.0f} / {st['fat']:.0f} / {st['carbs']:.0f} "
                             f"| {c['kcal']:.0f} / {c['protein']:.0f} / {c['fat']:.0f} / {c['carbs']:.0f} "
                             f"| {cf['kcal']:.0f} | {diff:+.0f} |")
            for items in d.items.values():
                for it in items:
                    if it.product_id is None and it.grams is not None:
                        unmatched[it.text] = unmatched.get(it.text, 0) + 1
        lines.append("")
    if errors:
        lines += ["## Wnioski", "",
                  f"- Porównanych dni (osoba × dzień): {len(errors)}",
                  f"- Średnia różnica energii (czat vs przeliczenie): {sum(errors) / len(errors):.0f} kcal",
                  f"- Największa różnica: {max(errors):.0f} kcal", ""]
    if unmatched:
        lines += ["## Pozycje bez pewnego produktu (nie wliczone)", ""]
        lines += [f"- {t} ({n}×)" for t, n in sorted(unmatched.items())]
        lines.append("")
    if notes:
        lines += ["## Uwagi dopasowania", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(lines)


def run(paths_dir: Path, products: Mapping[str, Product], fixed: Mapping[str, float], llm=None) -> str:
    matcher = ProductMatcher(products)
    files = []
    notes: list[str] = []
    for f in sorted(paths_dir.glob("*.txt")):
        days = parse_example(f.read_text(encoding="utf-8-sig"), matcher)
        notes += resolve_uncertain(days, matcher, products, llm)
        files.append((f.name, days))
    return render_report(files, products, fixed, notes)
