"""Czytelny tekst bez znaczników Markdown do zapisu przez użytkownika."""

from __future__ import annotations

import re

TABLE_DIVIDER = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")


def markdown_to_text(source: str) -> str:
    lines: list[str] = []
    for original in source.splitlines():
        line = original.rstrip()
        if TABLE_DIVIDER.match(line):
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            line = "    ".join(cells)
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^>\s?", "", line)
        line = line.replace("*", "").replace("__", "").replace("_", "").replace("`", "")
        line = line.replace("  ", " ").rstrip()
        lines.append(line)
    return "\n".join(lines).strip() + "\n"
