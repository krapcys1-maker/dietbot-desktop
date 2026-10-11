"""Klient LLM zgodny z API OpenAI (DeepSeek, OpenAI, OpenRouter...) z walidacją JSON i rejestrem kosztów.

Świadomie bez dodatkowej warstwy abstrakcji: potrzebujemy pełnej kontroli nad trybem JSON, wyłączeniem
"myślenia" w DeepSeek, ponawianiem z informacją o błędzie walidacji i dokładnym liczeniem kosztów.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..storage.paths import Paths
from ..storage.yamlio import load_yaml, to_plain
from .scope import BREAKER_COOLDOWN_S, breaker_reason, check_cancelled, current, open_breaker

# Ostrożny cennik, gdy w llm.yaml zmieniono model, a ceny dotyczą innego (nie zaniżamy kosztów nieznanego modelu).
FALLBACK_PRICES = {"wejscie": 5.0, "wejscie_z_cache": 5.0, "wyjscie": 20.0}
DEFAULT_TASK_LIMIT_USD = 0.5  # jedno polecenie (plan, wymyślanie dań, wiadomość) nie wyda więcej
RETRY_PAUSE_S = 2.0


class LLMError(RuntimeError):
    """Błąd wywołania modelu (sieć, pusta odpowiedź, JSON niezgodny ze schematem po wszystkich próbach)."""


class BudgetExceeded(LLMError):
    """Wywołanie przekroczyłoby ustalony budżet."""


class Prices(BaseModel):
    input: float = Field(alias="wejscie", ge=0)
    input_cached: float = Field(alias="wejscie_z_cache", ge=0)
    output: float = Field(alias="wyjscie", ge=0)


class LLMSettings(BaseModel):
    provider: str = Field(alias="dostawca")
    model: str
    base_url: str = Field(alias="adres_api")
    api_key_env: str = Field(alias="zmienna_klucza")
    temperature: float = Field(default=0.3, alias="temperatura")
    max_tokens: int = Field(default=6000, alias="max_tokenow_odpowiedzi")
    timeout_s: float = Field(default=180, alias="limit_czasu_s")
    attempts: int = Field(default=3, alias="proby")
    budget_usd: float = Field(alias="budzet_usd", ge=0)
    task_limit_usd: float = Field(default=DEFAULT_TASK_LIMIT_USD, alias="limit_na_polecenie_usd", gt=0)
    prices: Prices = Field(alias="ceny_usd_za_milion_tokenow")
    prices_model: str | None = Field(default=None, alias="ceny_dla_modelu")  # brak = ceny są dla `model`

    @classmethod
    def load(cls, paths: Paths | None = None) -> LLMSettings:
        paths = paths or Paths.discover()
        return cls.model_validate(to_plain(load_yaml(paths.config / "llm.yaml")))

    @property
    def prices_match(self) -> bool:
        return self.prices_model is None or self.prices_model == self.model

    def effective_prices(self) -> Prices:
        return self.prices if self.prices_match else Prices.model_validate(FALLBACK_PRICES)


def load_dotenv_file(paths: Paths) -> None:
    """Wczytuje .env bez nadpisywania zmiennych już ustawionych w systemie."""
    env = paths.root / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip()
        if v[:1] in ('"', "'") and v.endswith(v[0]) and len(v) >= 2:
            v = v[1:-1]
        elif " #" in v:  # komentarz na końcu linii (bez cudzysłowu)
            v = v.split(" #", 1)[0].rstrip()
        os.environ.setdefault(k, v)


def save_dotenv_value(paths: Paths, key: str, value: str | None) -> None:
    """Zapisuje albo usuwa pojedynczą wartość `.env`, zachowując pozostałe ustawienia."""
    env = paths.root / ".env"
    lines = env.read_text(encoding="utf-8-sig").splitlines() if env.exists() else []
    replacement = f"{key}={value}" if value is not None else None
    output: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip().removeprefix("export ").lstrip()
        # nazwa porównywana jak przy odczycie (spacje przy „=”) — inaczej „Usuń klucz” zostawiał ręcznie wpisaną linię
        # i stary klucz wracał po restarcie (audyt N3)
        if "=" in stripped and stripped.split("=", 1)[0].strip() == key:
            if replacement is not None and not replaced:
                output.append(replacement)
                replaced = True
            continue
        output.append(line)
    if replacement is not None and not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(replacement)
    text = "\n".join(output).rstrip() + ("\n" if output else "")
    env.parent.mkdir(parents=True, exist_ok=True)
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=env.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":
            os.chmod(tmp, 0o600)  # klucz tylko dla właściciela
        os.replace(tmp, env)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


_LEDGER_LOCKS: dict[str, threading.Lock] = {}
_LEDGER_RESERVED: dict[str, float] = {}
_LEDGER_GUARD = threading.Lock()


def _entry_cost(raw: str) -> float:
    """Koszt z linii rejestru; linia uszkodzona (NaN, null, tekst, ujemna liczba) nie wyłącza limitu."""
    try:
        value = float(json.loads(raw).get("koszt_usd", 0.0) or 0.0)
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError, RecursionError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


class CostLedger:
    """Rejestr kosztów w JSONL — przeżywa restart aplikacji, więc limit budżetu jest globalny.

    Przed każdym wywołaniem rezerwujemy szacowany koszt pod blokadą wspólną dla wszystkich wątków procesu, a po
    odpowiedzi zastępujemy go kosztem faktycznym — równoległe wywołania generatora nie przekroczą budżetu."""

    def __init__(self, path: Path, settings: LLMSettings):
        self.path = path
        self.settings = settings
        key = str(path.resolve())
        with _LEDGER_GUARD:
            self._lock = _LEDGER_LOCKS.setdefault(key, threading.Lock())
            _LEDGER_RESERVED.setdefault(key, 0.0)
        self._key = key

    def spent_usd(self) -> float:
        if not self.path.exists():
            return 0.0
        return sum(_entry_cost(line) for line in self.path.read_text(encoding="utf-8").splitlines())

    def cost(self, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
        p = self.settings.effective_prices()
        miss = max(prompt_tokens - cached_tokens, 0)
        return (miss * p.input + cached_tokens * p.input_cached + completion_tokens * p.output) / 1_000_000

    def estimate(self, prompt_chars: int, max_tokens: int) -> float:
        """Górny szacunek kosztu wywołania (ok. 3 znaki na token wejścia, pełna długość odpowiedzi)."""
        return self.cost(prompt_chars // 3 + 1, 0, max_tokens)

    def reserve(self, estimate: float) -> None:
        with self._lock:
            spent = self.spent_usd()
            reserved = _LEDGER_RESERVED[self._key]
            if spent + reserved + estimate > self.settings.budget_usd:
                raise BudgetExceeded(
                    f"Budżet AI {self.settings.budget_usd:.2f} $ zostałby przekroczony (wydano {spent:.4f} $, "
                    f"w toku {reserved:.4f} $, to wywołanie do {estimate:.4f} $). Zwiększ budżet w konfiguracji "
                    "albo korzystaj z aplikacji bez AI.")
            _LEDGER_RESERVED[self._key] = reserved + estimate

    def release(self, estimate: float) -> None:
        with self._lock:
            _LEDGER_RESERVED[self._key] = max(_LEDGER_RESERVED[self._key] - estimate, 0.0)

    def ensure_budget(self, prompt_chars: int, max_tokens: int) -> None:
        """Sprawdzenie bez rezerwacji (dla zgodności ze starszym kodem)."""
        estimate = self.estimate(prompt_chars, max_tokens)
        self.reserve(estimate)
        self.release(estimate)

    def record(self, **entry) -> None:
        entry = {"czas": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")


@dataclass
class LLMResult[T: BaseModel]:
    value: T
    attempts: int
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    seconds: float


def schema_hint(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(by_alias=True), ensure_ascii=False, separators=(",", ":"))


class LLMClient:
    def __init__(self, settings: LLMSettings | None = None, paths: Paths | None = None, ledger: CostLedger | None = None):
        self.paths = paths or Paths.discover()
        self.settings = settings or LLMSettings.load(self.paths)
        load_dotenv_file(self.paths)
        self.ledger = ledger or CostLedger(self.paths.out / "llm_koszty.jsonl", self.settings)
        key = os.environ.get(self.settings.api_key_env, "").strip()
        if not key:
            raise LLMError(f"Brak klucza API: ustaw {self.settings.api_key_env} w pliku .env")
        from openai import OpenAI

        # ponowienia robimy sami (z rozróżnieniem błędów) — SDK nie ponawia, żeby jedno wywołanie nie stało się
        # dziewięcioma żądaniami po kilka minut każde (audyt 09.10.2026, W5)
        self._client = OpenAI(api_key=key, base_url=self.settings.base_url, timeout=self.settings.timeout_s,
                              max_retries=0)

    def _guard(self, task: str, estimate: float) -> None:
        """Przed wywołaniem: przerwanie zadania, bezpiecznik awarii, limit na polecenie i rezerwacja budżetu."""
        check_cancelled()
        reason = breaker_reason()
        if reason:
            raise LLMError(f"Model językowy jest chwilowo niedostępny ({reason}) — pomijam AI w tym kroku.")
        scope = current()
        if scope is not None:
            limit = scope.limit_usd if scope.limit_usd is not None else self.settings.task_limit_usd
            if scope.spent_usd + estimate > limit:
                raise BudgetExceeded(
                    f"Limit kosztu jednego polecenia {limit:.2f} $ zostałby przekroczony "
                    f"(wydano w tym poleceniu {scope.spent_usd:.4f} $) — kończę bez dalszych wywołań AI.")
        self.ledger.reserve(estimate)

    def _charge(self, usd: float) -> None:
        scope = current()
        if scope is not None:
            scope.add_cost(usd)

    def complete_json[T: BaseModel](self, task: str, system: str, user: str, schema: type[T], *,
                                   temperature: float | None = None, max_tokens: int | None = None) -> LLMResult[T]:
        s = self.settings
        max_tokens = max_tokens or s.max_tokens
        system_full = (
            f"{system}\n\nZwracasz WYŁĄCZNIE poprawny obiekt JSON (bez markdown, bez komentarzy) zgodny z tym "
            f"schematem JSON:\n{schema_hint(schema)}"
        )
        messages: list[dict] = [{"role": "system", "content": system_full}, {"role": "user", "content": user}]
        extra_body = {"thinking": {"type": "disabled"}} if s.provider == "deepseek" else None
        total_cost = 0.0
        prompt_tok = completion_tok = 0
        t0 = time.perf_counter()
        last_error = ""
        transient_retry_used = False
        attempt = 0
        while attempt < s.attempts:
            attempt += 1
            prompt_chars = sum(len(m["content"]) for m in messages)
            estimate = self.ledger.estimate(prompt_chars, max_tokens)
            self._guard(task, estimate)
            try:
                resp = self._client.chat.completions.create(
                    model=s.model, messages=messages, response_format={"type": "json_object"},
                    temperature=s.temperature if temperature is None else temperature, max_tokens=max_tokens,
                    extra_body=extra_body,
                )
            except Exception as exc:  # noqa: BLE001 — każdy błąd wywołania kończy się zapisem w rejestrze
                self.ledger.release(estimate)
                kind, status = _classify(exc)
                last_error = f"{type(exc).__name__}: {exc}"
                # dostawca mógł naliczyć tokeny wejścia także przy przekroczonym czasie — liczymy je ostrożnie
                charged = self.ledger.cost(prompt_chars // 3 + 1, 0, 0) if kind == "czas" else 0.0
                total_cost += charged
                self._charge(charged)
                self.ledger.record(zadanie=task, model=s.model, proba=attempt, ok=False, blad=last_error[:300],
                                   koszt_usd=charged, szacunek=kind == "czas")
                if kind in ("czas", "polaczenie"):
                    open_breaker("przekroczony czas odpowiedzi" if kind == "czas" else "brak połączenia z API")
                    raise LLMError(f"Model językowy nie odpowiada ({last_error[:160]}). Przez "
                                   f"{BREAKER_COOLDOWN_S / 60:.0f} min aplikacja pracuje bez AI.") from None
                if kind == "chwilowy" and not transient_retry_used:
                    transient_retry_used = True  # 429 / 5xx: jedna ponowna próba po krótkiej przerwie
                    attempt -= 1
                    check_cancelled()
                    time.sleep(RETRY_PAUSE_S)
                    continue
                raise LLMError(_status_message(status, last_error)) from None
            self.ledger.release(estimate)
            usage = resp.usage
            p_tok = usage.prompt_tokens if usage else 0
            c_tok = usage.completion_tokens if usage else 0
            cached = 0
            if usage and usage.prompt_tokens_details and usage.prompt_tokens_details.cached_tokens:
                cached = usage.prompt_tokens_details.cached_tokens
            if usage is None:  # brak danych o zużyciu — liczymy górny szacunek zamiast zera
                p_tok, c_tok = prompt_chars // 3 + 1, max_tokens
            cost = self.ledger.cost(p_tok, cached, c_tok)
            total_cost += cost
            self._charge(cost)
            prompt_tok += p_tok
            completion_tok += c_tok
            content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
            finish = resp.choices[0].finish_reason if resp.choices else None
            try:
                if not content:
                    raise ValueError("pusta odpowiedź modelu")
                value = schema.model_validate(json.loads(content))
            except (json.JSONDecodeError, ValidationError, ValueError, RecursionError) as exc:
                last_error = str(exc)[:1500]
                self.ledger.record(zadanie=task, model=s.model, proba=attempt, ok=False, blad=last_error[:300],
                                   tokeny_wejscie=p_tok, tokeny_cache=cached, tokeny_wyjscie=c_tok, koszt_usd=cost,
                                   koniec=finish)
                # do ponowienia dołączamy tylko ostatnią (błędną) odpowiedź, nie wszystkie poprzednie — inaczej każda
                # kolejna próba jest droższa o całą historię
                messages = messages[:2]
                if content:
                    messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": (
                    "Twoja odpowiedź nie przeszła walidacji: " + last_error +
                    ("\nOdpowiedź została ucięta — skróć teksty." if finish == "length" else "") +
                    "\nPopraw ją i zwróć wyłącznie kompletny, poprawny JSON zgodny ze schematem.")})
                continue
            self.ledger.record(zadanie=task, model=s.model, proba=attempt, ok=True, tokeny_wejscie=p_tok,
                               tokeny_cache=cached, tokeny_wyjscie=c_tok, koszt_usd=cost, koniec=finish)
            return LLMResult(value=value, attempts=attempt, cost_usd=total_cost, prompt_tokens=prompt_tok,
                             completion_tokens=completion_tok, seconds=time.perf_counter() - t0)
        raise LLMError(f"Model nie zwrócił poprawnej odpowiedzi po {s.attempts} próbach ({task}): {last_error}")


def _classify(exc: Exception) -> tuple[str, int | None]:
    """Rodzaj błędu wywołania: czas | polaczenie | chwilowy (429, 5xx) | trwaly (pozostałe 4xx i reszta)."""
    try:
        import openai
    except ImportError:  # pragma: no cover
        return "trwaly", None
    if isinstance(exc, openai.APITimeoutError):
        return "czas", None
    if isinstance(exc, openai.APIConnectionError):
        return "polaczenie", None
    if isinstance(exc, openai.APIStatusError):
        code = exc.status_code
        return ("chwilowy" if code == 429 or code >= 500 else "trwaly"), code
    return "trwaly", None


def _status_message(status: int | None, detail: str) -> str:
    if status == 401:
        return "Klucz API został odrzucony (401) — sprawdź klucz w Ustawieniach."
    if status == 402:
        return "Brak środków na koncie dostawcy AI (402) — doładuj konto albo korzystaj bez AI."
    if status == 429:
        return "Dostawca AI ogranicza liczbę zapytań (429) — spróbuj za chwilę."
    if status is not None and status >= 500:
        return f"Usługa AI ma chwilowy problem ({status}) — spróbuj później."
    return f"Wywołanie modelu nie powiodło się: {detail[:300]}"
