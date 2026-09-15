"""Wspólna baza modeli domenowych.

Pola w kodzie mają nazwy angielskie, a w plikach YAML/JSON — polskie (aliasy Pydantic),
bo pliki danych i konfiguracji edytuje użytkownik.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

SLUG_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class DomainModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", validate_assignment=True)

    def to_data(self) -> dict[str, Any]:
        """Słownik do zapisu w pliku: polskie klucze, bez pustych pól."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def check_slug(value: str, what: str = "identyfikator") -> str:
    if not SLUG_RE.match(value):
        raise ValueError(f"{what} '{value}' musi mieć postać slug: małe litery a-z, cyfry i podkreślenia")
    return value
