"""Bot Telegram (aiogram 3): plan na dziś/jutro/tydzień, lista zakupów, nowy plan i czat.

Dostęp tylko dla numerów ID z TELEGRAM_ALLOWED_USERS (plik .env). Bez tej listy bot nikomu nie pokazuje danych —
na /start odpowiada tylko numerem ID pytającego, żeby można go było dopisać.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import math
import os
import re
from datetime import date, timedelta

from .chat import ChatAssistant
from .jobs import JobStatus, UserError
from .service import DietService

log = logging.getLogger("dietbot.telegram")
LIMIT = 3900  # Telegram przyjmuje 4096 znaków w wiadomości
MEAL_ICONS = {"sniadanie": "🍳", "obiad": "🍲", "kolacja": "🥗", "przekaska": "🍿"}
DAY_SHORT = ["Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Nd"]

HELP = (
    "<b>DietBot</b> — wasz jadłospis na tydzień.\n\n"
    "/dzis — posiłki na dziś z gramaturami\n"
    "/jutro — posiłki na jutro\n"
    "/tydzien — dania na cały tydzień\n"
    "/zakupy — lista zakupów\n"
    "/nowyplan <i>życzenia</i> — ułóż nowy plan (np. /nowyplan więcej ryb)\n\n"
    "Możesz też pisać zwyczajnie: „ile białka mam dziś?”, „zamień jutrzejszą kolację na coś z kurczakiem”."
)


def allowed_users() -> set[int]:
    out = set()
    for part in re.split(r"[,; ]+", os.environ.get("TELEGRAM_ALLOWED_USERS", "")):
        if part.strip().isdigit():
            out.add(int(part.strip()))
    return out


def md_to_html(text: str) -> str:
    """Odpowiedzi asystenta mają proste **pogrubienia** i _kursywę_ — zamiana na bezpieczny HTML Telegrama."""
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    return re.sub(r"(?<![\w/])_(.+?)_(?![\w/])", r"<i>\1</i>", escaped)


def chunks(text: str, limit: int = LIMIT) -> list[str]:
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # pojedyncza bardzo długa linia
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit and current:
            parts.append(current.rstrip("\n"))
            current = ""
        current += line + "\n"
    if current.strip():
        parts.append(current.rstrip("\n"))
    return parts or [""]


def _n(value: float) -> int:
    """Zaokrąglenie „w górę od połowy”, jak w aplikacji — `round()` Pythona zaokrąglałby 152,5 do 152 i sumy dnia
    w Telegramie różniłyby się o 1 g od aplikacji i czatu."""
    return math.floor(value + 0.5)


def _items(items: list[dict]) -> str:
    out = []
    for it in items:
        if it["negligible"]:
            continue
        hh = f" (≈ {it['household']})" if it.get("household") else ""
        out.append(f"{html.escape(it['name'].lower())} {it['amount']}{hh}")
    return ", ".join(out)


def format_day(view: dict, day: int) -> str:
    d = next((x for x in view["days"] if x["day"] == day), None)
    if d is None:
        return "Ten plan nie obejmuje tego dnia."
    date_txt = ""
    if view.get("week_start"):
        start = date.fromisoformat(view["week_start"])
        date_txt = f" {start + timedelta(days=day):%d.%m}"
    persons = {p["id"]: html.escape(p["name"]) for p in view["persons"]}
    lines = [f"<b>{d['label']}{date_txt}</b> · {html.escape(view['title'])}", ""]
    for m in d["meals"]:
        flags = []
        if m["is_treat"]:
            flags.append("pula 20%")
        if m["is_sweet"]:
            flags.append("słodkie")
        if m["reheat"]:
            flags.append("odgrzewasz")
        flag_txt = f" <i>({', '.join(flags)})</i>" if flags else ""
        lines.append(f"{MEAL_ICONS.get(m['meal'], '•')} <b>{m['meal_label']}:</b> {html.escape(m['dish_name'])}{flag_txt}")
        for pid, portion in m["portions"].items():
            n = portion["nutrition"]
            lines.append(f"  • {persons.get(pid, pid)}: {_items(portion['items'])} — {_n(n['kcal'])} kcal, B {_n(n['protein'])} g")
        if m.get("pot"):
            pot = ", ".join(f"{html.escape(p['name'].lower())} {p['amount']}" for p in m["pot"])
            lines.append(f"  🥘 <i>Gotujesz raz na wszystkie porcje:</i> {pot}")
        lines.append("")
    for s in d["snacks"]:
        lines.append(f"🍎 <b>Przekąska</b> ({persons.get(s['person_id'], s['person_id'])}): {_items(s['items'])}")
    if d["snacks"]:
        lines.append("")
    for pid, t in d["totals"].items():
        v = t["values"]
        bad = [k for k, st in t["states"].items() if st != "ok"]
        icon = "✅" if not bad else "⚠️"
        lines.append(f"{icon} {persons.get(pid, pid)}: {_n(v['kcal'])} kcal · B {_n(v['protein'])} · T {_n(v['fat'])} · W {_n(v['carbs'])} g")
    return "\n".join(lines)


def format_week(view: dict) -> str:
    lines = [f"<b>{html.escape(view['title'])}</b>" + (" ✅" if view["ok"] else " ⚠️"), ""]
    if view.get("comment"):
        lines += [f"<i>{html.escape(view['comment'])}</i>", ""]
    for d in view["days"]:
        meals = " · ".join(html.escape(m["dish_name"]) + (" 🍕" if m["is_treat"] else "") for m in d["meals"])
        lines.append(f"<b>{DAY_SHORT[d['day']]}:</b> {meals}")
    facts = [f"{f['label']}: {f['value']}" for f in view.get("facts", [])]
    if facts:
        lines += ["", html.escape(" · ".join(facts))]
    persons = {p["id"]: html.escape(p["name"]) for p in view.get("persons", [])}
    summary = view.get("week_summary") or []
    if summary:
        lines += ["", "<b>Podsumowanie tygodnia</b> (średnio dziennie)"]
        for w in summary:
            avg = w["average"]
            ok = w["days_ok"] == w["days_count"] and all(s == "ok" for s in w["average_states"].values())
            lines.append(f"{'✅' if ok else '⚠️'} {persons.get(w['person_id'], w['person_id'])}: {_n(avg['kcal'])} kcal · "
                         f"B {_n(avg['protein'])} · T {_n(avg['fat'])} · W {_n(avg['carbs'])} g — dni w widełkach "
                         f"{w['days_ok']} z {w['days_count']}")
    return "\n".join(lines)


def format_shopping(data: dict) -> str:
    lines = [f"🛒 <b>Lista zakupów</b> — {html.escape(data['title'])}"]
    for g in data["groups"]:
        lines += ["", f"<b>{html.escape(g['label'])}</b>"]
        for it in g["items"]:
            amount = "" if it["pomijalny"] else f" — {it['ilosc']}"
            lines.append(f"▫️ {html.escape(it['nazwa'])}{amount}")
    return "\n".join(lines)


async def run_bot(service: DietService) -> None:
    from aiogram.types import BotCommand

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Brak TELEGRAM_BOT_TOKEN w pliku .env")
    allowed = allowed_users()
    if not allowed:
        log.warning("TELEGRAM_ALLOWED_USERS jest puste — bot odpowie tylko numerem ID na /start")
    bot, dp = build_bot(service, token, allowed)
    await bot.set_my_commands([BotCommand(command="dzis", description="Posiłki na dziś"),
                               BotCommand(command="jutro", description="Posiłki na jutro"),
                               BotCommand(command="tydzien", description="Dania na cały tydzień"),
                               BotCommand(command="zakupy", description="Lista zakupów"),
                               BotCommand(command="nowyplan", description="Ułóż nowy plan"),
                               BotCommand(command="pomoc", description="Pomoc")])
    await dp.start_polling(bot, handle_signals=False)


def build_bot(service: DietService, token: str, allowed: set[int], session=None, poll_seconds: float = 2.0):
    """Bot i dyspozytor z obsługą komend (bez uruchamiania — testy podają własną sesję HTTP)."""
    from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.filters import Command, CommandObject, CommandStart
    from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML), session=session)
    router = Router()

    class Access(BaseMiddleware):
        async def __call__(self, handler, event, data):
            user = getattr(event, "from_user", None)
            if user is not None and user.id in allowed:
                return await handler(event, data)
            if isinstance(event, Message) and (event.text or "").startswith("/start") and user is not None:
                await event.answer(f"Twój numer ID w Telegramie: <code>{user.id}</code>.\n"
                                   "Dopisz go do TELEGRAM_ALLOWED_USERS w pliku .env aplikacji i uruchom ją ponownie.")
            return None

    router.message.outer_middleware(Access())
    router.callback_query.outer_middleware(Access())

    async def send(message: Message, text: str, **kw) -> None:
        for part in chunks(text):
            await message.answer(part, **kw)

    async def active_view() -> dict | None:
        rec = await asyncio.to_thread(service.active_plan)
        return await asyncio.to_thread(service.plan, rec.id) if rec else None

    async def follow(message: Message, job_id: str) -> None:
        status_msg = await message.answer("⏳ Liczę…")
        last = ""
        while True:
            job = service.jobs.get(job_id)
            if job is None:
                return
            if job.status in (JobStatus.DONE, JobStatus.FAILED):
                break
            tail = job.log[-1] if job.log else ""
            if tail and tail != last:
                last = tail
                with contextlib.suppress(Exception):  # np. "message is not modified"
                    await status_msg.edit_text(f"⏳ {html.escape(tail[:300])}")
            await asyncio.sleep(poll_seconds)
        if job.status is JobStatus.FAILED:
            await status_msg.edit_text(f"❌ Nie udało się: {html.escape(job.error or 'nieznany błąd')}")
            return
        result = job.result or {}
        if "dishes" in result:  # samo wymyślanie dań — pokazujemy nowe dania, nie plan
            names = "\n".join(f"• {html.escape(d['name'])}" for d in result["dishes"])
            await status_msg.edit_text(f"✅ Nowe dania w bibliotece:\n{names}")
            return
        await status_msg.edit_text("✅ Gotowe" + ("" if result.get("ok") else " (plan ma uwagi)"))
        view = await active_view()
        if view:
            await send(message, format_week(view))

    @router.message(CommandStart())
    @router.message(Command("pomoc", "help"))
    async def cmd_start(message: Message) -> None:
        await message.answer(HELP)

    async def day_cmd(message: Message, shift: int) -> None:
        view = await active_view()
        if view is None:
            await message.answer("Nie ma jeszcze planu. Napisz /nowyplan.")
            return
        rec = service.store.get(view["id"])
        idx = service.today_index(rec, date.today() + timedelta(days=shift)) if rec else None
        if idx is None:
            await message.answer("Aktualny plan nie obejmuje tego dnia. Ułóż nowy: /nowyplan")
            return
        await send(message, format_day(view, idx))

    @router.message(Command("dzis", "dziś"))
    async def cmd_today(message: Message) -> None:
        await day_cmd(message, 0)

    @router.message(Command("jutro"))
    async def cmd_tomorrow(message: Message) -> None:
        await day_cmd(message, 1)

    @router.message(Command("tydzien", "tydzień"))
    async def cmd_week(message: Message) -> None:
        view = await active_view()
        await send(message, format_week(view) if view else "Nie ma jeszcze planu. Napisz /nowyplan.")

    @router.message(Command("zakupy"))
    async def cmd_shopping(message: Message) -> None:
        rec = await asyncio.to_thread(service.active_plan)
        if rec is None:
            await message.answer("Nie ma jeszcze planu. Napisz /nowyplan.")
            return
        await send(message, format_shopping(await asyncio.to_thread(service.shopping, rec.id)))

    @router.message(Command("nowyplan"))
    async def cmd_new(message: Message, command: CommandObject) -> None:
        try:
            job = await asyncio.to_thread(service.generate, wishes=command.args, channel="telegram")
        except UserError as exc:
            await message.answer(f"❌ {html.escape(str(exc))}")
            return
        await message.answer("Układam nowy plan" + (f" z życzeniami: <i>{html.escape(command.args)}</i>" if command.args else "") + "…")
        await follow(message, job.id)

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        assistant = ChatAssistant(service, channel="telegram")
        reply = await asyncio.to_thread(assistant.handle, message.text or "")
        data = reply.get("data") or {}
        markup = None
        if data.get("proposal_id"):
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Zatwierdź", callback_data=f"ok:{data['proposal_id']}"),
                InlineKeyboardButton(text="✖️ Odrzuć", callback_data=f"no:{data['proposal_id']}"),
            ]])
        parts = chunks(md_to_html(reply["text"]))
        for i, part in enumerate(parts):
            await message.answer(part, reply_markup=markup if i == len(parts) - 1 else None)
        if data.get("job_id"):
            await follow(message, data["job_id"])

    @router.callback_query(F.data.startswith("ok:") | F.data.startswith("no:"))
    async def on_confirm(callback: CallbackQuery) -> None:
        action, pid = (callback.data or ":").split(":", 1)
        if callback.message is not None:
            with contextlib.suppress(Exception):
                await callback.message.edit_reply_markup(reply_markup=None)
        if action == "no":
            service.pending_changes.pop(pid, None)
            await callback.answer("Odrzucono")
            return
        reply = await asyncio.to_thread(ChatAssistant(service, channel="telegram").confirm, pid)
        saved = (reply.get("data") or {}).get("kind") == "zalozenia_zapisane"
        await callback.answer("Zapisano" if saved else "Nie zapisano")
        if callback.message is not None:
            for part in chunks(md_to_html(reply["text"])):
                await callback.message.answer(part)

    dp = Dispatcher()
    dp.include_router(router)
    return bot, dp
