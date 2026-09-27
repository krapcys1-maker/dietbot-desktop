"""API aplikacji (FastAPI) — lokalny serwer dla okna aplikacji. Nasłuchuje tylko na 127.0.0.1."""

from __future__ import annotations

import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..domain.goal_calculator import GoalInput, calculate_goals
from .file_dialogs import save_text_as
from .jobs import UserError
from .service import DietService
from .validation import polish_error_list

log = logging.getLogger("dietbot.api")


class GenerateIn(BaseModel):
    wishes: str | None = Field(default=None, max_length=1000)
    days: list[int] | None = None
    avoid_recent: bool = True
    use_llm: bool = True
    week_start: date | None = None
    invent: bool = False  # model wymyśla nowe dania do tego tygodnia


class PlanPatchIn(BaseModel):
    title: str | None = None
    active: bool | None = None


class SwapIn(BaseModel):
    slot_key: str
    dish_id: str


class RecomputeIn(BaseModel):
    repair: bool = False


class AssumptionsIn(BaseModel):
    data: dict


class DishPatchIn(BaseModel):
    status: str


class RatingIn(BaseModel):
    rating: Literal[-1, 0, 1]


class SaveDietIn(BaseModel):
    title: str = Field(min_length=2, max_length=80)


class InventIn(BaseModel):
    request: str = Field(min_length=3, max_length=600)
    count: int = Field(default=6, ge=1, le=12)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    plan_id: str | None = None


class ConfirmIn(BaseModel):
    proposal_id: str


class ApiKeyIn(BaseModel):
    key: str = Field(min_length=20, max_length=256)


class SaveTextIn(BaseModel):
    kind: Literal["plan", "shopping"]


def web_dist_dir() -> Path | None:
    candidates = []
    if getattr(sys, "frozen", False):  # PyInstaller
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "web" / "dist")
    candidates.append(Path(__file__).resolve().parents[3] / "web" / "dist")
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return None


def create_app(service: DietService) -> FastAPI:
    app = FastAPI(title="DietBot", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.state.service = service

    @app.exception_handler(UserError)
    async def _user_error(_: Request, exc: UserError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # domyślnie FastAPI odpowiada po angielsku ("String should have at most 1000 characters")
        return JSONResponse(status_code=400, content={"detail": "; ".join(polish_error_list(list(exc.errors())))})

    @app.middleware("http")
    async def _same_origin_only(request: Request, call_next):
        # żądania zmieniające stan tylko z własnej strony: obca strona w przeglądarce nie uruchomi eksportu ani zadań
        origin = request.headers.get("origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            supplied, expected = urlsplit(origin), urlsplit(str(request.base_url))
            if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
                return JSONResponse(status_code=403, content={"detail": "Żądanie spoza aplikacji Porcja."})
        return await call_next(request)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Nieoczekiwany błąd API")
        return JSONResponse(status_code=500, content={"detail": f"Nieoczekiwany błąd: {type(exc).__name__}: {exc}"})

    s = service

    # ---------------------------------------------------------------- status i zadania
    @app.get("/api/status")
    def status() -> dict:
        return s.status()

    @app.get("/api/zadania")
    def jobs() -> list[dict]:
        return [j.to_dict() for j in s.jobs.list()]

    @app.get("/api/zadania/{job_id}")
    def job(job_id: str, od: int = 0) -> dict:
        j = s.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "Nie ma takiego zadania")
        return j.to_dict(since=max(0, od))

    # ---------------------------------------------------------------- plany
    @app.get("/api/diety")
    def library() -> list[dict]:
        return s.library()

    @app.put("/api/diety/{plan_id}")
    def save_diet(plan_id: str, body: SaveDietIn) -> dict:
        return s.save_diet(plan_id, body.title)

    @app.delete("/api/diety/{plan_id}")
    def unsave_diet(plan_id: str) -> dict:
        s.unsave_diet(plan_id)
        return {"ok": True}

    @app.get("/api/oceny")
    def ratings() -> dict:
        return s.store.ratings()

    @app.put("/api/oceny/{dish_id}")
    def rate_dish(dish_id: str, body: RatingIn) -> dict:
        return s.rate_dish(dish_id, body.rating)

    @app.post("/api/plany/generuj")
    def generate(body: GenerateIn) -> dict:
        if body.days is not None and (not body.days or any(d < 0 or d > 6 for d in body.days)):
            raise UserError("Wybierz przynajmniej jeden dzień tygodnia.")
        return s.generate(wishes=body.wishes, days=body.days, avoid_recent=body.avoid_recent, use_llm=body.use_llm,
                          week_start=body.week_start, invent=body.invent).to_dict()

    @app.get("/api/plany")
    def plans() -> list[dict]:
        return s.plans()

    @app.get("/api/plany/aktywny")
    def active_plan() -> dict:
        rec = s.active_plan()
        if rec is None:
            raise HTTPException(404, "Nie ma jeszcze żadnego planu")
        return s.plan(rec.id)

    @app.get("/api/plany/{plan_id}")
    def plan(plan_id: str) -> dict:
        return s.plan(plan_id)

    @app.patch("/api/plany/{plan_id}")
    def patch_plan(plan_id: str, body: PlanPatchIn) -> dict:
        if body.title is not None:
            s.rename(plan_id, body.title)
        if body.active:
            s.set_active(plan_id)
        return s.plan(plan_id)

    @app.delete("/api/plany/{plan_id}")
    def delete_plan(plan_id: str) -> dict:
        s.delete(plan_id)
        return {"ok": True}

    @app.get("/api/plany/{plan_id}/zamienniki")
    def swap_options(plan_id: str, slot: str) -> dict:
        return s.swap_options(plan_id, slot)

    @app.post("/api/plany/{plan_id}/zamien")
    def swap(plan_id: str, body: SwapIn) -> dict:
        return s.swap(plan_id, body.slot_key, body.dish_id).to_dict()

    @app.post("/api/plany/{plan_id}/przelicz")
    def recompute(plan_id: str, body: RecomputeIn) -> dict:
        return s.recompute(plan_id, repair=body.repair).to_dict()

    @app.get("/api/plany/{plan_id}/zakupy")
    def shopping(plan_id: str) -> dict:
        return s.shopping(plan_id)

    @app.post("/api/plany/{plan_id}/eksport")
    def export(plan_id: str) -> dict:
        return s.export(plan_id)

    @app.post("/api/plany/{plan_id}/zapisz-plik")
    def save_plan_file(plan_id: str, body: SaveTextIn) -> dict:
        exported = s.text_export(plan_id, body.kind)
        title = "Zapisz cały jadłospis" if body.kind == "plan" else "Zapisz listę zakupów"
        path = save_text_as(exported["filename"], title, exported["content"])
        return {
            "saved": path is not None,
            "path": str(path) if path else None,
            "filename": path.name if path else exported["filename"],
        }

    # ---------------------------------------------------------------- założenia
    @app.get("/api/zalozenia")
    def assumptions() -> dict:
        return s.assumptions()

    @app.post("/api/cele/oblicz")
    def calculate_targets(body: GoalInput) -> dict:
        try:
            return calculate_goals(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/zalozenia/sprawdz")
    def check_assumptions(body: AssumptionsIn) -> dict:
        _, errors, warnings = s.validate_assumptions(body.data)
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    @app.put("/api/zalozenia")
    def save_assumptions(body: AssumptionsIn) -> dict:
        return s.save_assumptions(body.data)

    # ---------------------------------------------------------------- dania i produkty
    @app.get("/api/dania")
    def dishes(posilek: str | None = None, q: str | None = None, status: str | None = None) -> list[dict]:
        return s.dishes(meal=posilek, q=q, status=status)

    @app.post("/api/dania/wymysl")
    def invent_dishes(body: InventIn) -> dict:
        return s.invent(body.request, body.count).to_dict()

    @app.get("/api/dania/{dish_id}")
    def dish(dish_id: str) -> dict:
        return s.dish(dish_id)

    @app.patch("/api/dania/{dish_id}")
    def patch_dish(dish_id: str, body: DishPatchIn) -> dict:
        return s.set_dish_status(dish_id, body.status)

    @app.get("/api/produkty")
    def products(q: str | None = None, kategoria: str | None = None) -> list[dict]:
        return s.products(q=q, category=kategoria)

    @app.get("/api/produkty/dopasuj")
    def match_product(tekst: str) -> dict:
        return s.match_product(tekst)

    @app.get("/api/produkty/{product_id}")
    def product(product_id: str) -> dict:
        return s.product(product_id)

    @app.post("/api/produkty")
    def add_product(body: dict) -> dict:
        return s.add_product(body)

    # ---------------------------------------------------------------- czat i koszty
    @app.get("/api/czat")
    def chat_history() -> list[dict]:
        return s.store.messages()

    @app.post("/api/czat")
    def chat(body: ChatIn) -> dict:
        from .chat import ChatAssistant

        return ChatAssistant(s).handle(body.text, plan_id=body.plan_id)

    @app.post("/api/czat/potwierdz")
    def chat_confirm(body: ConfirmIn) -> dict:
        from .chat import ChatAssistant

        return ChatAssistant(s).confirm(body.proposal_id)

    @app.post("/api/czat/odrzuc")
    def chat_reject(body: ConfirmIn) -> dict:
        s.pending_changes.pop(body.proposal_id, None)
        return {"ok": True}

    @app.get("/api/koszty")
    def costs() -> dict:
        return s.costs()

    @app.put("/api/ustawienia/deepseek")
    def save_deepseek_key(body: ApiKeyIn) -> dict:
        return s.save_api_key(body.key)

    @app.delete("/api/ustawienia/deepseek")
    def delete_deepseek_key() -> dict:
        return s.delete_api_key()

    @app.post("/api/system/otworz-wyniki")
    def open_results() -> dict:
        path = s.paths.out
        path.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(path)  # noqa: S606 — lokalna aplikacja otwiera własny folder wyników
            return {"ok": True, "path": str(path)}
        return {"ok": False, "path": str(path)}

    # ---------------------------------------------------------------- interfejs
    dist = web_dist_dir()
    if dist is not None:
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def no_ui() -> str:
            return ("<meta charset='utf-8'><h1>Porcja</h1><p>Interfejs nie jest zbudowany. "
                    "Uruchom <code>npm run build</code> w folderze <code>web</code>. API: <a href='/api/docs'>/api/docs</a></p>")

    return app
