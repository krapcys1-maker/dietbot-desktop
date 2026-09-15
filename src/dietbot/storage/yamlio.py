"""Odczyt i zapis YAML z zachowaniem komentarzy (pliki edytuje użytkownik)."""

from __future__ import annotations

import hashlib
import io
import os
import pickle
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

# wersja formatu pamięci podręcznej sparsowanych plików — zmiana unieważnia stare wpisy
_CACHE_VERSION = 1


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.allow_unicode = True
    y.preserve_quotes = True
    y.width = 120
    y.indent(mapping=2, sequence=2, offset=0)
    return y


def load_yaml(path: Path) -> Any:
    """Wczytuje plik YAML (toleruje BOM). Zwraca struktury ruamel (zachowują komentarze)."""
    text = path.read_text(encoding="utf-8-sig")
    return _yaml().load(text)


def dump_yaml(data: Any, path: Path) -> None:
    """Zapis atomowy: najpierw plik tymczasowy, potem podmiana — przerwany zapis nie psuje danych."""
    buf = io.StringIO()
    _yaml().dump(data, buf)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(buf.getvalue())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def to_plain(data: Any) -> Any:
    """Zamienia struktury ruamel na zwykłe dict/list (do walidacji Pydantic)."""
    if isinstance(data, dict):
        return {str(k): to_plain(v) for k, v in data.items()}
    if isinstance(data, list):
        return [to_plain(v) for v in data]
    return data


def load_plain(path: Path, cache_dir: Path | None = None) -> Any:
    """`to_plain(load_yaml(path))` z pamięcią podręczną na dysku.

    Parser zachowujący komentarze potrzebuje ok. 3,5 s na bibliotekę dań (kilkanaście tysięcy linii), a wynik zmienia
    się tylko po edycji pliku. Klucz wpisu: ścieżka, rozmiar i czas modyfikacji pliku. Bez `cache_dir` albo przy
    problemie z dyskiem — zwykłe wczytanie (pamięć podręczna nigdy nie jest warunkiem działania).
    """
    if cache_dir is None:
        return to_plain(load_yaml(path))
    cache_file = stamp = None
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        cache_file = cache_dir / (hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest() + ".pkl")
        if cache_file.exists():
            with cache_file.open("rb") as f:
                saved = pickle.load(f)
            if saved.get("version") == _CACHE_VERSION and saved.get("stamp") == stamp:
                return saved["data"]
    except (OSError, pickle.PickleError, EOFError, AttributeError, KeyError, TypeError):
        pass
    data = to_plain(load_yaml(path))
    if cache_file is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=cache_file.name, suffix=".tmp", dir=cache_dir)
            with os.fdopen(fd, "wb") as f:
                pickle.dump({"version": _CACHE_VERSION, "stamp": stamp, "data": data}, f,
                            protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, cache_file)
        except OSError:
            pass
    return data
