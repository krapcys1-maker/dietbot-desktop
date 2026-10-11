"""Kontrola zbudowanej paczki: dane startowe w dist\\DietBot to dokładnie pliki z gita (dane/, konfiguracja/).

Nic spoza repozytorium (surowe bazy BLS/CIQUAL, własne produkty, kopie *.bak, klucze .env, baza planów) nie może
trafić do instalatora (audyt 09.10.2026, proces). Uruchamia go packaging\\zbuduj_instalator.ps1 po zbudowaniu EXE.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "dist" / "DietBot" / "_internal" / "defaults"
FORBIDDEN_NAMES = (".env", "dietbot.sqlite3", "wlasne.yaml")


def tracked(folder: str) -> set[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--", folder], cwd=ROOT, capture_output=True, check=True)
    files = {Path(p).as_posix() for p in out.stdout.decode("utf-8").split("\0") if p}
    return {f for f in files if not f.startswith("dane/raw/")}


def main() -> int:
    if not DEFAULTS.is_dir():
        print(f"Brak katalogu {DEFAULTS} — najpierw zbuduj paczkę.", file=sys.stderr)
        return 1
    packed = {p.relative_to(DEFAULTS).as_posix() for p in DEFAULTS.rglob("*") if p.is_file()}
    expected = tracked("dane") | tracked("konfiguracja")
    extra = sorted(packed - expected)
    missing = sorted(expected - packed)
    forbidden = sorted(p for p in packed if Path(p).name in FORBIDDEN_NAMES or p.endswith(".bak"))
    for title, items in (("Pliki spoza gita w paczce", extra), ("Brakujące pliki danych", missing),
                         ("Zakazane pliki", forbidden)):
        if items:
            print(f"{title} ({len(items)}):", *items[:20], sep="\n  ", file=sys.stderr)
    if extra or missing or forbidden:
        return 1
    print(f"Paczka OK: {len(packed)} plików danych startowych, wszystkie z repozytorium.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
