"""Zadania w tle (generowanie, przeliczanie) z dziennikiem postępu do podglądu w aplikacji i na Telegramie."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from .store import new_id


class JobStatus(StrEnum):
    QUEUED = "w_kolejce"
    RUNNING = "w_toku"
    DONE = "gotowe"
    FAILED = "blad"


@dataclass
class Job:
    kind: str
    title: str
    id: str = field(default_factory=new_id)
    status: JobStatus = JobStatus.QUEUED
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def to_dict(self, since: int = 0) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status.value,
                "log": self.log[since:], "log_size": len(self.log), "result": self.result, "error": self.error,
                "created_at": self.created_at.isoformat(),
                "finished_at": self.finished_at.isoformat() if self.finished_at else None}


class UserError(Exception):
    """Błąd do pokazania użytkownikowi bez śladu stosu (np. brak planu, zła nazwa dania)."""


JobFn = Callable[[Callable[[str], None]], dict]


class JobManager:
    """Jedno zadanie liczące naraz (solver i tak używa rdzeni procesora), reszta czeka w kolejce."""

    def __init__(self, workers: int = 1, keep: int = 50):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dietbot-zadanie")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._keep = keep
        self._closed = False

    def submit(self, kind: str, title: str, fn: JobFn) -> Job:
        if self._closed:
            raise UserError("Aplikacja się zamyka — nie przyjmuje już nowych zadań.")
        job = Job(kind=kind, title=title)
        with self._lock:
            self._jobs[job.id] = job
            self._trim()

        def progress(msg: str) -> None:
            job.log.append(msg)

        def run() -> None:
            job.status = JobStatus.RUNNING
            try:
                job.result = fn(progress)
                job.status = JobStatus.DONE
            except UserError as exc:
                job.error = str(exc)
                job.status = JobStatus.FAILED
            except Exception as exc:  # noqa: BLE001 — błąd zadania pokazujemy w aplikacji, nie zabija serwera
                job.error = f"{type(exc).__name__}: {exc}"
                job.log.append(traceback.format_exc(limit=6))
                job.status = JobStatus.FAILED
            finally:
                job.finished_at = datetime.now(UTC)

        self._pool.submit(run)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def busy(self) -> bool:
        return any(j.status in (JobStatus.QUEUED, JobStatus.RUNNING) for j in self._jobs.values())

    def wait(self, job_id: str, timeout: float = 600) -> Job:
        """Czeka na koniec zadania (dla testów i Telegrama)."""
        import time

        deadline = time.monotonic() + timeout
        job = self._jobs[job_id]
        while job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            if time.monotonic() > deadline:
                raise TimeoutError(f"zadanie {job_id} nie skończyło się w {timeout:.0f} s")
            time.sleep(0.1)
        return job

    def _trim(self) -> None:
        finished = [j for j in self._jobs.values() if j.status in (JobStatus.DONE, JobStatus.FAILED)]
        for j in sorted(finished, key=lambda j: j.created_at)[: max(0, len(self._jobs) - self._keep)]:
            self._jobs.pop(j.id, None)

    def shutdown(self) -> None:
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
