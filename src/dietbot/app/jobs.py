"""Zadania w tle (generowanie, przeliczanie) z dziennikiem postępu do podglądu w aplikacji i na Telegramie."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ..llm.scope import Cancelled, task_scope
from .store import new_id


class JobStatus(StrEnum):
    QUEUED = "w_kolejce"
    RUNNING = "w_toku"
    DONE = "gotowe"
    FAILED = "blad"
    CANCELLED = "przerwane"


FINISHED = (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


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
    llm_cost_usd: float = 0.0  # wszystkie wywołania AI w zadaniu, także nieudane
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self, since: int = 0) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status.value,
                "log": self.log[since:], "log_size": len(self.log), "result": self.result, "error": self.error,
                "created_at": self.created_at.isoformat(), "llm_cost_usd": round(self.llm_cost_usd, 5),
                "finished_at": self.finished_at.isoformat() if self.finished_at else None}


class UserError(Exception):
    """Błąd do pokazania użytkownikowi bez śladu stosu (np. brak planu, zła nazwa dania)."""


class NotFound(UserError):  # noqa: N818
    """Nie ma takiego planu, dania, produktu, zasady — API odpowiada 404 (wcześniej raz 400, raz 404 — audyt N8)."""


JobFn = Callable[[Callable[[str], None]], dict]


class JobManager:
    """Jedno zadanie liczące naraz (solver i tak używa rdzeni procesora), reszta czeka w kolejce."""

    def __init__(self, workers: int = 1, keep: int = 50):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dietbot-zadanie")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._keep = keep
        self._closed = False

    def submit(self, kind: str, title: str, fn: JobFn, cost_limit_usd: float | None = None) -> Job:
        """`cost_limit_usd` — limit kosztu AI tego zadania (None = z ustawień modelu)."""
        if self._closed:
            raise UserError("Aplikacja się zamyka — nie przyjmuje już nowych zadań.")
        job = Job(kind=kind, title=title)
        with self._lock:
            self._jobs[job.id] = job
            self._trim()

        def progress(msg: str) -> None:
            job.log.append(msg)
            if job.cancel.is_set():  # każdy krok planowania sprawdza, czy użytkownik nie przerwał zadania
                raise Cancelled("Zadanie przerwane.")

        def run() -> None:
            if job.cancel.is_set():
                job.status, job.error = JobStatus.CANCELLED, "Przerwano przed rozpoczęciem."
                job.finished_at = datetime.now(UTC)
                return
            job.status = JobStatus.RUNNING
            scope = None
            try:
                with task_scope(cost_limit_usd, job.cancel) as scope:
                    job.result = fn(progress)
                job.status = JobStatus.DONE
            except Cancelled:
                job.error = "Przerwano — nic nie zostało zapisane."
                job.log.append("Przerwano na prośbę użytkownika.")
                job.status = JobStatus.CANCELLED
            except UserError as exc:
                job.error = str(exc)
                job.status = JobStatus.FAILED
            except Exception as exc:  # noqa: BLE001 — błąd zadania pokazujemy w aplikacji, nie zabija serwera
                job.error = f"{type(exc).__name__}: {exc}"
                job.log.append(traceback.format_exc(limit=6))
                job.status = JobStatus.FAILED
            finally:
                if scope is not None:
                    job.llm_cost_usd = scope.spent_usd
                job.finished_at = datetime.now(UTC)

        self._pool.submit(run)
        return job

    def cancel(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if job is not None and job.status not in FINISHED:
            job.cancel.set()
        return job

    def cancel_all(self) -> int:
        active = [j for j in self._jobs.values() if j.status not in FINISHED]
        for job in active:
            job.cancel.set()
        return len(active)

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
        while job.status not in FINISHED:
            if time.monotonic() > deadline:
                raise TimeoutError(f"zadanie {job_id} nie skończyło się w {timeout:.0f} s")
            time.sleep(0.1)
        return job

    def _trim(self) -> None:
        finished = [j for j in self._jobs.values() if j.status in FINISHED]
        for j in sorted(finished, key=lambda j: j.created_at)[: max(0, len(self._jobs) - self._keep)]:
            self._jobs.pop(j.id, None)

    def shutdown(self, wait_s: float = 0.0) -> bool:
        """Przerywa zadania i (opcjonalnie) czeka, aż działające zauważy przerwanie. True = nic już nie działa.
        Wcześniej zamknięcie okna zostawiało ukryty proces, który dalej liczył, płacił za AI i zapisywał plan (W6)."""
        import time

        self._closed = True
        self.cancel_all()
        self._pool.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + wait_s
        while self.busy() and time.monotonic() < deadline:
            time.sleep(0.1)
        return not any(j.status == JobStatus.RUNNING for j in self._jobs.values())
