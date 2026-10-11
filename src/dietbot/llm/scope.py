"""Zakres jednego zadania (plan, wymyślanie dań, wiadomość czatu): limit kosztu AI, bezpiecznik awarii i anulowanie.

Zakres jest w zmiennej kontekstowej, więc widzi go każde wywołanie modelu w tym zadaniu — także w wątkach generatora
i kontroli kucharza (uruchamianych przez `in_context`). Jeden obiekt zakresu jest wspólny dla wszystkich wątków
zadania: suma kosztów, znacznik awarii API i żądanie przerwania obowiązują je wszystkie naraz.
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

# Po przekroczeniu czasu albo zerwanym połączeniu kolejne wywołania (naprawa, kucharz, następne zadanie) nie czekają
# znowu po kilka minut: przez ten czas model jest „niedostępny”, a planer od razu przechodzi na algorytm.
BREAKER_COOLDOWN_S = 300.0


class Cancelled(BaseException):  # noqa: N818 — nazwa jak w komunikatach; BaseException: nie łapie go `except Exception`
    """Zadanie przerwane przez użytkownika (np. zamknięcie okna) — przerywa także wywołania modelu."""


@dataclass
class TaskScope:
    limit_usd: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    spent_usd: float = 0.0
    calls: int = 0
    broken: str | None = None  # przyczyna awarii API w tym zadaniu
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_cost(self, usd: float) -> None:
        with self.lock:
            self.spent_usd += max(usd, 0.0)
            self.calls += 1


_current: contextvars.ContextVar[TaskScope | None] = contextvars.ContextVar("dietbot_task_scope", default=None)
_breaker = {"until": 0.0, "reason": ""}
_breaker_lock = threading.Lock()


def current() -> TaskScope | None:
    return _current.get()


@contextmanager
def task_scope(limit_usd: float | None = None, cancel: threading.Event | None = None) -> Iterator[TaskScope]:
    scope = TaskScope(limit_usd=limit_usd, cancel=cancel or threading.Event())
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


def check_cancelled() -> None:
    scope = current()
    if scope is not None and scope.cancel.is_set():
        raise Cancelled("Zadanie przerwane.")


def in_context[R](fn: Callable[..., R]) -> Callable[..., R]:
    """Funkcja do uruchomienia w innym wątku z bieżącym zakresem zadania (osobna kopia kontekstu na wywołanie)."""
    ctx = contextvars.copy_context()
    return lambda *args, **kwargs: ctx.run(fn, *args, **kwargs)


def open_breaker(reason: str) -> None:
    with _breaker_lock:
        _breaker["until"] = time.monotonic() + BREAKER_COOLDOWN_S
        _breaker["reason"] = reason
    scope = current()
    if scope is not None:
        scope.broken = reason


def breaker_reason() -> str | None:
    """Przyczyna, dla której model jest teraz uznany za niedostępny (None = można wołać)."""
    scope = current()
    if scope is not None and scope.broken:
        return scope.broken
    with _breaker_lock:
        if time.monotonic() < _breaker["until"]:
            return _breaker["reason"]
    return None


def reset_breaker() -> None:
    with _breaker_lock:
        _breaker["until"], _breaker["reason"] = 0.0, ""
