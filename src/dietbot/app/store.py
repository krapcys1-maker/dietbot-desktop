"""Magazyn planów i rozmów (SQLite w folderze wyniki): każda zmiana planu to nowa wersja, starych nie nadpisujemy."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from ..checker.verify import VerificationReport
from ..domain.plan import WeekPlan

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    family_id TEXT NOT NULL,
    parent_id TEXT,
    version INTEGER NOT NULL,
    source TEXT NOT NULL,
    change_note TEXT,
    wishes TEXT,
    week_start TEXT,
    ok INTEGER NOT NULL,
    llm_cost_usd REAL NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    assumptions_hash TEXT,
    plan_json TEXT NOT NULL,
    report_json TEXT,
    composition_json TEXT,
    log_json TEXT,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS plans_created ON plans(created_at);
CREATE INDEX IF NOT EXISTS plans_family ON plans(family_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'aplikacja',
    plan_id TEXT,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    data_json TEXT
);
"""


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class PlanRecord:
    plan: WeekPlan
    title: str
    source: str  # llm / algorytm / zamiana / przeliczenie / naprawa
    ok: bool
    report: VerificationReport | None = None
    composition: dict | None = None
    log: list[str] = field(default_factory=list)
    wishes: str | None = None
    week_start: date | None = None
    change_note: str | None = None
    llm_cost_usd: float = 0.0
    seconds: float = 0.0
    assumptions_hash: str | None = None
    id: str = field(default_factory=new_id)
    family_id: str | None = None
    parent_id: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def summary(self) -> dict:
        return {"id": self.id, "title": self.title, "created_at": self.created_at.isoformat(), "source": self.source,
                "ok": self.ok, "version": self.version, "family_id": self.family_id, "parent_id": self.parent_id,
                "change_note": self.change_note, "wishes": self.wishes,
                "week_start": self.week_start.isoformat() if self.week_start else None,
                "llm_cost_usd": round(self.llm_cost_usd, 5), "seconds": round(self.seconds, 1)}


class PlanStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ plany
    def save(self, rec: PlanRecord) -> PlanRecord:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")  # numer wersji i zapis w jednej transakcji — dwa zapisy naraz nie dostaną tej samej
            if rec.parent_id:
                parent = c.execute("SELECT family_id FROM plans WHERE id = ? AND deleted = 0", (rec.parent_id,)).fetchone()
                if parent is None:
                    raise KeyError(f"brak planu {rec.parent_id}")
                rec.family_id = parent["family_id"]
                row = c.execute("SELECT MAX(version) FROM plans WHERE family_id = ?", (rec.family_id,)).fetchone()
                rec.version = int(row[0] or 0) + 1
            else:
                rec.family_id = rec.family_id or rec.id
            c.execute(
                """INSERT INTO plans (id, created_at, title, family_id, parent_id, version, source, change_note, wishes,
                   week_start, ok, llm_cost_usd, seconds, assumptions_hash, plan_json, report_json, composition_json,
                   log_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rec.id, rec.created_at.isoformat(), rec.title, rec.family_id, rec.parent_id, rec.version, rec.source,
                 rec.change_note, rec.wishes, rec.week_start.isoformat() if rec.week_start else None, int(rec.ok),
                 rec.llm_cost_usd, rec.seconds, rec.assumptions_hash, rec.plan.model_dump_json(by_alias=True),
                 rec.report.model_dump_json() if rec.report else None,
                 json.dumps(rec.composition, ensure_ascii=False) if rec.composition else None,
                 json.dumps(rec.log, ensure_ascii=False)),
            )
        return rec

    @staticmethod
    def _record(row: sqlite3.Row) -> PlanRecord:
        return PlanRecord(
            id=row["id"], created_at=datetime.fromisoformat(row["created_at"]), title=row["title"],
            family_id=row["family_id"], parent_id=row["parent_id"], version=row["version"], source=row["source"],
            change_note=row["change_note"], wishes=row["wishes"],
            week_start=date.fromisoformat(row["week_start"]) if row["week_start"] else None, ok=bool(row["ok"]),
            llm_cost_usd=row["llm_cost_usd"], seconds=row["seconds"], assumptions_hash=row["assumptions_hash"],
            plan=WeekPlan.model_validate_json(row["plan_json"]),
            report=VerificationReport.model_validate_json(row["report_json"]) if row["report_json"] else None,
            composition=json.loads(row["composition_json"]) if row["composition_json"] else None,
            log=json.loads(row["log_json"]) if row["log_json"] else [],
        )

    def get(self, plan_id: str) -> PlanRecord | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM plans WHERE id = ? AND deleted = 0", (plan_id,)).fetchone()
        return self._record(row) if row else None

    def list(self, limit: int = 100) -> list[PlanRecord]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM plans WHERE deleted = 0 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._record(r) for r in rows]

    def family(self, family_id: str) -> list[PlanRecord]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM plans WHERE family_id = ? AND deleted = 0 ORDER BY version",
                             (family_id,)).fetchall()
        return [self._record(r) for r in rows]

    def latest(self, ok_only: bool = False) -> PlanRecord | None:
        query = "SELECT * FROM plans WHERE deleted = 0" + (" AND ok = 1" if ok_only else "")
        with self._conn() as c:
            row = c.execute(query + " ORDER BY created_at DESC LIMIT 1").fetchone()
        return self._record(row) if row else None

    def rename(self, plan_id: str, title: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE plans SET title = ? WHERE id = ?", (title, plan_id))

    def delete(self, plan_id: str) -> None:
        """Miękkie usunięcie (plan znika z list, ale zostaje w bazie)."""
        with self._conn() as c:
            c.execute("UPDATE plans SET deleted = 1 WHERE id = ?", (plan_id,))
        if self.get_setting("active_plan") == plan_id:
            self.set_setting("active_plan", "")

    # ------------------------------------------------------------------ ustawienia
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row and row[0] != "" else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, value))

    def active(self) -> PlanRecord | None:
        """Plan oznaczony jako aktualny, a jeśli brak — najnowszy poprawny."""
        pid = self.get_setting("active_plan")
        rec = self.get(pid) if pid else None
        return rec or self.latest(ok_only=True) or self.latest()

    # ------------------------------------------------------------------ czat
    def add_message(self, role: str, text: str, *, plan_id: str | None = None, channel: str = "aplikacja",
                    data: dict | None = None) -> int:
        with self._conn() as c:
            cur = c.execute("INSERT INTO chat (created_at, channel, plan_id, role, text, data_json) VALUES (?, ?, ?, ?, ?, ?)",
                            (datetime.now(UTC).isoformat(), channel, plan_id, role, text,
                             json.dumps(data, ensure_ascii=False) if data else None))
            return int(cur.lastrowid)

    def messages(self, channel: str = "aplikacja", limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM chat WHERE channel = ? ORDER BY id DESC LIMIT ?", (channel, limit)).fetchall()
        return [{"id": r["id"], "created_at": r["created_at"], "plan_id": r["plan_id"], "role": r["role"],
                 "text": r["text"], "data": json.loads(r["data_json"]) if r["data_json"] else None}
                for r in reversed(rows)]
