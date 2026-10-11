"""Odczyt i zapis YAML z zachowaniem komentarzy (pliki edytuje użytkownik)."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

# wersja formatu pamięci podręcznej sparsowanych plików — zmiana unieważnia stare wpisy
_CACHE_VERSION = 2


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


def _encode(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dt.datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, dt.date):
        return {"__date__": value.isoformat()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if len(value) == 1 and "__date__" in value:
            return dt.date.fromisoformat(value["__date__"])
        if len(value) == 1 and "__datetime__" in value:
            return dt.datetime.fromisoformat(value["__datetime__"])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def load_plain(path: Path, cache_dir: Path | None = None) -> Any:
    """`to_plain(load_yaml(path))` z pamięcią podręczną na dysku.

    Parser zachowujący komentarze potrzebuje ok. 3,5 s na bibliotekę dań (kilkanaście tysięcy linii), a wynik zmienia
    się tylko po edycji pliku. Klucz wpisu: skrót SHA-256 treści pliku (nie czas modyfikacji — ten bywa nieświeży po
    przywróceniu kopii albo na dyskach sieciowych). Zapis w JSON, nie pickle: podrzucony plik pamięci podręcznej nie
    może wykonać kodu (audyt N4). Bez `cache_dir` albo przy problemie z dyskiem — zwykłe wczytanie.
    """
    if cache_dir is None:
        return to_plain(load_yaml(path))
    cache_file = digest = None
    try:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        cache_file = cache_dir / (hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest() + ".json")
        if cache_file.exists():
            saved = json.loads(cache_file.read_text(encoding="utf-8"))
            if saved.get("version") == _CACHE_VERSION and saved.get("sha256") == digest:
                return _decode(saved["data"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        pass
    data = to_plain(load_yaml(path))
    if cache_file is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=cache_file.name, suffix=".tmp", dir=cache_dir)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": _CACHE_VERSION, "sha256": digest, "data": _encode(data)}, f, ensure_ascii=False)
            os.replace(tmp, cache_file)
        except (OSError, TypeError, ValueError):
            pass
    return data
