"""Klient LLM zgodny z API OpenAI (DeepSeek, OpenAI, OpenRouter...) z walidacją JSON i rejestrem kosztów.

Świadomie bez dodatkowej warstwy abstrakcji: potrzebujemy pełnej kontroli nad trybem JSON, wyłączeniem
"myślenia" w DeepSeek, ponawianiem z informacją o błędzie walidacji i dokładnym liczeniem kosztów.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..storage.paths import Paths
from ..storage.yamlio import load_yaml, to_plain


class LLMError(RuntimeError):
    """Błąd wywołania modelu (sieć, pusta odpowiedź, JSON niezgodny ze schematem po wszystkich próbach)."""


class BudgetExceeded(LLMError):
    """Wywołanie przekroczyłoby ustalony budżet."""


class Prices(BaseModel):
    input: float = Field(alias="wejscie")
    input_cached: float = Field(alias="wejscie_z_cache")
    output: float = Field(alias="wyjscie")


class LLMSettings(BaseModel):
    provider: str = Field(alias="dostawca")
    model: str
    base_url: str = Field(alias="adres_api")
    api_key_env: str = Field(alias="zmienna_klucza")
    temperature: float = Field(default=0.3, alias="temperatura")
    max_tokens: int = Field(default=6000, alias="max_tokenow_odpowiedzi")
    timeout_s: float = Field(default=180, alias="limit_czasu_s")
    attempts: int = Field(default=3, alias="proby")
    budget_usd: float = Field(alias="budzet_usd")
    prices: Prices = Field(alias="ceny_usd_za_milion_tokenow")

    @classmethod
    def load(cls, paths: Paths | None = None) -> LLMSettings:
        paths = paths or Paths.discover()
        return cls.model_validate(to_plain(load_yaml(paths.config / "llm.yaml")))


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
    prefix = key + "="
    replacement = f"{key}={value}" if value is not None else None
    output: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip().removeprefix("export ").lstrip()
        if stripped.startswith(prefix):
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
    temporary = env.with_name(".env.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(env)


class CostLedger:
    """Rejestr kosztów w JSONL — przeżywa restart aplikacji, więc limit budżetu jest globalny."""

    def __init__(self, path: Path, settings: LLMSettings):
        self.path = path
        self.settings = settings
        self._lock = threading.Lock()

    def spent_usd(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                total += float(json.loads(line).get("koszt_usd", 0.0))
            except (ValueError, json.JSONDecodeError):
                continue
        return total

    def cost(self, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
        p = self.settings.prices
        miss = max(prompt_tokens - cached_tokens, 0)
        return (miss * p.input + cached_tokens * p.input_cached + completion_tokens * p.output) / 1_000_000

    def ensure_budget(self, prompt_chars: int, max_tokens: int) -> None:
        estimate = self.cost(prompt_chars // 3 + 1, 0, max_tokens)
        spent = self.spent_usd()
        if spent + estimate > self.settings.budget_usd:
            raise BudgetExceeded(
                f"Budżet LLM {self.settings.budget_usd:.2f} $ zostałby przekroczony "
                f"(wydano {spent:.4f} $, to wywołanie do {estimate:.4f} $)."
            )

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

        self._client = OpenAI(api_key=key, base_url=self.settings.base_url, timeout=self.settings.timeout_s,
                              max_retries=2)

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
        for attempt in range(1, s.attempts + 1):
            self.ledger.ensure_budget(sum(len(m["content"]) for m in messages), max_tokens)
            try:
                resp = self._client.chat.completions.create(
                    model=s.model, messages=messages, response_format={"type": "json_object"},
                    temperature=s.temperature if temperature is None else temperature, max_tokens=max_tokens,
                    extra_body=extra_body,
                )
            except Exception as exc:  # sieć, 429, 5xx — SDK już ponawiał; zapisujemy i próbujemy dalej
                last_error = f"{type(exc).__name__}: {exc}"
                self.ledger.record(zadanie=task, model=s.model, proba=attempt, ok=False, blad=last_error[:300],
                                   koszt_usd=0.0)
                continue
            usage = resp.usage
            p_tok = usage.prompt_tokens if usage else 0
            c_tok = usage.completion_tokens if usage else 0
            cached = 0
            if usage and usage.prompt_tokens_details and usage.prompt_tokens_details.cached_tokens:
                cached = usage.prompt_tokens_details.cached_tokens
            cost = self.ledger.cost(p_tok, cached, c_tok)
            total_cost += cost
            prompt_tok += p_tok
            completion_tok += c_tok
            content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
            finish = resp.choices[0].finish_reason if resp.choices else None
            try:
                if not content:
                    raise ValueError("pusta odpowiedź modelu")
                value = schema.model_validate(json.loads(content))
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
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
