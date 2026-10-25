"""Bot Telegram bez sieci: udawana sesja HTTP zapisuje wysyłane wiadomości, a aktualizacje podajemy ręcznie."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, SendMessage
from aiogram.types import Chat, Message, Update

from dietbot.app.service import DietService
from dietbot.app.store import PlanRecord
from dietbot.app.telegram_bot import build_bot
from dietbot.checker import verify_plan
from dietbot.storage.paths import Paths

ROOT = Path(__file__).resolve().parents[1]
ALLOWED, STRANGER = 111, 999
TOKEN = "123456:TEST_TOKEN_abcdefghijklmnopqrstuvwxyz"


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list = []

    async def make_request(self, bot, method, timeout=None):  # noqa: ARG002
        self.calls.append(method)
        if isinstance(method, SendMessage | EditMessageText):
            return Message(message_id=len(self.calls), date=datetime.now(UTC),
                           chat=Chat(id=int(method.chat_id or ALLOWED), type="private"), text=method.text)
        if isinstance(method, AnswerCallbackQuery | EditMessageReplyMarkup):
            return True
        return True

    async def close(self):
        return None

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    def texts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, SendMessage | EditMessageText)]


@pytest.fixture(scope="module")
def service(tmp_path_factory, repo, week_result):
    home = tmp_path_factory.mktemp("dietbot_tg")
    for part in ("dane/produkty", "dane/dania", "dane/sklepy", "konfiguracja"):
        shutil.copytree(ROOT / part, home / part)
    (home / "wyniki").mkdir()
    s = DietService(Paths(home), use_llm=False)
    plan = week_result.plan
    report = verify_plan(plan, s.repo.assumptions, s.repo.products, s.repo.dishes)
    monday = date.today() - timedelta(days=date.today().weekday())
    rec = PlanRecord(plan=plan, title="Tydzień testowy", source="algorytm", ok=report.ok, report=report,
                     week_start=monday)
    s.store.save(rec)
    return s


def _update(bot, uid: int, text: str | None = None, callback: str | None = None, n: int = 1) -> Update:
    chat = {"id": uid, "type": "private"}
    user = {"id": uid, "is_bot": False, "first_name": "Test"}
    if callback is None:
        data = {"update_id": n, "message": {"message_id": n, "date": int(datetime.now(UTC).timestamp()), "chat": chat,
                                            "from": user, "text": text,
                                            "entities": [{"type": "bot_command", "offset": 0,
                                                          "length": len(text.split()[0])}] if text and text.startswith("/") else None}}
    else:
        data = {"update_id": n, "callback_query": {"id": str(n), "from": user, "chat_instance": "x", "data": callback,
                                                   "message": {"message_id": n, "date": int(datetime.now(UTC).timestamp()),
                                                               "chat": chat, "text": "propozycja"}}}
    return Update.model_validate(data, context={"bot": bot})


def run(service, uid, text=None, callback=None):
    session = FakeSession()
    bot, dp = build_bot(service, TOKEN, {ALLOWED}, session=session, poll_seconds=0.05)
    asyncio.run(dp.feed_update(bot, _update(bot, uid, text, callback)))
    return session


def test_stranger_gets_only_his_id(service):
    s = run(service, STRANGER, "/start")
    assert len(s.texts()) == 1 and str(STRANGER) in s.texts()[0] and "TELEGRAM_ALLOWED_USERS" in s.texts()[0]
    assert run(service, STRANGER, "/tydzien").texts() == []
    assert run(service, STRANGER, "ile mam białka?").texts() == []


def test_help_week_today_and_shopping(service):
    assert "/nowyplan" in run(service, ALLOWED, "/pomoc").texts()[0]
    week = run(service, ALLOWED, "/tydzien").texts()
    assert week and week[0].startswith("<b>Tydzień testowy</b>")
    today = run(service, ALLOWED, "/dzis").texts()
    assert today and "kcal" in today[0] and "Śniadanie" in today[0]
    shop = run(service, ALLOWED, "/zakupy").texts()
    assert shop and shop[0].startswith("<b>Lista zakupów — ")
    assert all(len(t) <= 4096 for t in shop)


def test_chat_without_llm_and_rejected_proposal(service):
    reply = run(service, ALLOWED, "ile białka ma żona w środę?").texts()
    assert reply and "modelu językowego" in reply[0]
    service.pending_changes["abc"] = {"data": {}, "diff": []}
    s = run(service, ALLOWED, callback="no:abc")
    assert any(isinstance(c, AnswerCallbackQuery) for c in s.calls)
    assert "abc" not in service.pending_changes


def test_expired_proposal_is_not_reported_as_saved(service):
    s = run(service, ALLOWED, callback="ok:nie_ma_takiej")
    answers = [c for c in s.calls if isinstance(c, AnswerCallbackQuery)]
    assert answers and answers[0].text == "Nie zapisano"
    assert any("wygasła" in t for t in s.texts())
