"""Położenie katalogów projektu (dane, konfiguracja, wyniki)."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "DietBot"


def user_data_root() -> Path:
    """Zapisywalny katalog bieżącego użytkownika dla wersji instalowanej."""
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local).resolve() / APP_DIR_NAME
    return Path.home().resolve() / ".dietbot"


def prepare_user_root(defaults: Path, target: Path) -> Path:
    """Przy pierwszym uruchomieniu kopiuje brakujące dane domyślne do katalogu użytkownika.

    Istniejących plików nigdy nie nadpisujemy: mogły zostać zmienione w aplikacji.
    """
    for folder in ("dane", "konfiguracja"):
        source = defaults / folder
        if not source.is_dir():
            raise FileNotFoundError(f"Paczka aplikacji nie zawiera folderu '{folder}'.")
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            destination = target / folder / relative
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)
    (target / "wyniki").mkdir(parents=True, exist_ok=True)
    return target


def frozen_defaults_root() -> Path | None:
    """Dane tylko do odczytu dołączone przez PyInstaller."""
    if not getattr(sys, "frozen", False):
        return None
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "defaults",
        Path(sys.executable).resolve().parent / "defaults",
    ]
    return next((p for p in candidates if (p / "dane").is_dir() and (p / "konfiguracja").is_dir()), None)


def find_root(start: Path | None = None) -> Path:
    """Katalog główny: zmienna DIETBOT_HOME albo pierwszy katalog w górę z plikiem pyproject.toml i folderem dane."""
    env = os.environ.get("DIETBOT_HOME")
    if env:
        root = Path(env).resolve()
        if not ((root / "dane").is_dir() and (root / "konfiguracja").is_dir()):
            raise FileNotFoundError(f"DIETBOT_HOME={env} nie zawiera folderów 'dane' i 'konfiguracja'.")
        return root
    if getattr(sys, "frozen", False):
        defaults = frozen_defaults_root()
        if defaults is not None:
            return prepare_user_root(defaults, user_data_root())
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
