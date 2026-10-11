"""Zapisuje requirements-lock.txt: zależności projektu (z pyproject.toml, z dodatkami dev, app, build) i ich zależności
przechodnie w dokładnie tych wersjach, które są zainstalowane w bieżącym środowisku.

Uruchom po zmianie zależności:  .venv\\Scripts\\python.exe packaging\\zablokuj_zaleznosci.py
CI i budowanie instalatora instalują pakiety z tego pliku — każdy build ma te same wersje (audyt 09.10.2026, proces).
"""

from __future__ import annotations

import importlib.metadata as md
import re
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = ("dev", "app", "build")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    queue = [Requirement(r) for r in project["dependencies"]]
    for extra in EXTRAS:
        queue += [Requirement(r) for r in project.get("optional-dependencies", {}).get(extra, [])]
    pinned: dict[str, str] = {}
    missing: list[str] = []
    while queue:
        req = queue.pop()
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        key = canonical(req.name)
        if key in pinned:
            continue
        try:
            dist = md.distribution(req.name)
        except md.PackageNotFoundError:
            missing.append(req.name)
            continue
        pinned[key] = f"{dist.metadata['Name']}=={dist.version}"
        for raw in dist.requires or []:
            sub = Requirement(raw)
            wanted = {e for e in req.extras}
            if sub.marker is not None:
                ok = any(sub.marker.evaluate({"extra": e}) for e in wanted) if "extra" in str(sub.marker) \
                    else sub.marker.evaluate({"extra": ""})
                if not ok:
                    continue
            queue.append(sub)
    if missing:
        print("Brak zainstalowanych pakietów: " + ", ".join(sorted(set(missing))), file=sys.stderr)
        return 1
    header = ("# Wygenerowane przez packaging/zablokuj_zaleznosci.py — nie edytuj ręcznie.\n"
              "# Dokładne wersje zależności projektu (z dodatkami dev, app, build) dla Windows i Python 3.12.\n")
    lines = sorted(pinned.values(), key=str.lower)
    (ROOT / "requirements-lock.txt").write_text(header + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Zapisano {len(lines)} pakietów do requirements-lock.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
