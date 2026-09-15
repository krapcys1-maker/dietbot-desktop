"""Położenie katalogów projektu (dane, konfiguracja, wyniki)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def find_root(start: Path | None = None) -> Path:
    """Katalog główny: zmienna DIETBOT_HOME albo pierwszy katalog w górę z plikiem pyproject.toml i folderem dane."""
    env = os.environ.get("DIETBOT_HOME")
    if env:
        root = Path(env).resolve()
        if not ((root / "dane").is_dir() and (root / "konfiguracja").is_dir()):
            raise FileNotFoundError(f"DIETBOT_HOME={env} nie zawiera folderów 'dane' i 'konfiguracja'.")
        return root
    starts = [(start or Path.cwd()).resolve()]
    if getattr(sys, "frozen", False):  # program spakowany (PyInstaller): dane obok pliku .exe albo wyżej
        starts.insert(0, Path(sys.executable).resolve().parent)
    for here in starts:
        for candidate in [here, *here.parents]:
            if (candidate / "dane").is_dir() and (candidate / "konfiguracja").is_dir():
                return candidate
    package_root = Path(__file__).resolve().parents[3]
    if (package_root / "dane").is_dir():
        return package_root
    raise FileNotFoundError("Nie znaleziono katalogu projektu (folderów 'dane' i 'konfiguracja'). Ustaw DIETBOT_HOME.")


@dataclass(frozen=True)
class Paths:
    root: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> Paths:
        return cls(find_root(start))

    @property
    def data(self) -> Path:
        return self.root / "dane"

    @property
    def products_dir(self) -> Path:
        return self.data / "produkty"

    @property
    def dishes_dir(self) -> Path:
        return self.data / "dania"

    @property
    def stores_dir(self) -> Path:
        return self.data / "sklepy"

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def bls_index(self) -> Path:
        return self.raw / "bls4" / "bls4_index.json"

    @property
    def bls_xlsx(self) -> Path:
        return self.raw / "bls4" / "BLS_4_0_2025_DE" / "BLS_4_0_Daten_2025_DE.xlsx"

    @property
    def config(self) -> Path:
        return self.root / "konfiguracja"

    @property
    def assumptions(self) -> Path:
        return self.config / "zalozenia.yaml"

    @property
    def out(self) -> Path:
        return self.root / "wyniki"

    @property
    def cache(self) -> Path:
        """Pamięć podręczna sparsowanych plików YAML (można bezpiecznie usunąć)."""
        return self.out / "cache"
