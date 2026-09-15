"""Uzupełnianie i kontrola wartości produktów ze źródła BLS (bez ręcznego przepisywania liczb)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from ..domain.nutrition import Nutrients
from ..storage.yamlio import dump_yaml, load_yaml, to_plain
from .bls import BlsIndex

# pole BLS -> klucz w YAML
_FIELDS = [
    ("kcal", "kcal"),
    ("protein", "bialko"),
    ("fat", "tluszcz"),
    ("carbs", "weglowodany"),
    ("fiber", "blonnik"),
    ("sugars", "cukry"),
    ("saturated_fat", "kwasy_nasycone"),
    ("salt", "sol"),
    ("alcohol", "alkohol"),
    ("organic_acids", "kwasy_organiczne"),
    ("polyols", "poliole"),
]
_OPTIONAL_ZERO_SKIP = {"alkohol", "kwasy_organiczne", "poliole"}


def _round(value: float) -> float | int:
    r = round(value, 2)
    return int(r) if r == int(r) else r


def bls_nutrients(index: BlsIndex, code: str) -> dict[str, float | int]:
    food = index.get(code)
    out: dict[str, float | int] = {}
    for src, key in _FIELDS:
        v = food.values.get(src)
        if v is None:
            if key in ("kcal", "bialko", "tluszcz", "weglowodany"):
                raise ValueError(f"BLS {code}: brak wartości '{src}' — nie można użyć tego wpisu")
            continue
        if key in _OPTIONAL_ZERO_SKIP and v == 0:
            continue
        out[key] = _round(v)
    # drobne niespójności zaokrągleń w tabeli (np. cukry > węglowodany o 0,01 g)
    if "cukry" in out and out["cukry"] > out["weglowodany"]:
        out["cukry"] = out["weglowodany"]
    if "kwasy_nasycone" in out and out["kwasy_nasycone"] > out["tluszcz"]:
        out["kwasy_nasycone"] = out["tluszcz"]
    return out


@dataclass
class RefreshReport:
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    diffs: list[str] = field(default_factory=list)


def refresh_products_file(path: Path, index: BlsIndex, write: bool = True, today: date | None = None) -> RefreshReport:
    today = today or date.today()
    data = load_yaml(path)
    rep = RefreshReport()
    for entry in data:
        src = entry.get("zrodlo") or {}
        if str(src.get("baza")) != "BLS":
            continue
        pid, code = entry.get("id"), src.get("kod")
        try:
            new = bls_nutrients(index, str(code))
            Nutrients.model_validate(new)
        except (KeyError, ValueError, ValidationError) as exc:
            rep.errors.append(f"{pid}: {exc}")
            continue
        old = to_plain(entry.get("na_100g")) or {}
        food = index.get(str(code))
        original = f"{food.name_de} | {food.name_en}"
        if old == new and src.get("nazwa_oryginalna") == original and src.get("wersja") == index.version:
            rep.unchanged.append(pid)
            continue
        if old and old != new:
            changed = [f"{k}: {old.get(k)} → {new.get(k)}" for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]
            rep.diffs.append(f"{pid}: " + ", ".join(changed))
        entry["na_100g"] = new
        src["wersja"] = index.version
        src["nazwa_oryginalna"] = original
        src["pobrano"] = today.isoformat()
        entry["zrodlo"] = src
        rep.updated.append(pid)
    if write and rep.updated:
        dump_yaml(data, path)
    return rep
