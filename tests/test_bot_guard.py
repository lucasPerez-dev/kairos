"""Verrouillage du bot Telegram : seul(s) le(s) chat(s) autorisé(s) peut/peuvent le piloter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from src.alerts import Alerter
from src.bot import guard_chat, resolve_allowed_chats, track_chat
from src.config import Settings
from src.engine import Engine, Zones
from src.fetcher import QueueTimesFetcher
from src.park_hours import ParkHours
from src.service import KairosService
from src.store import Store


def make_service(tmp_path) -> KairosService:
    store = Store(tmp_path / "b.db")
    settings = Settings(db_path=str(tmp_path / "b.db"), telegram_chat_id="", mode="collect")
    hours = ParkHours({"default": {"open": "10:00", "close": "20:00"}}, "Europe/Madrid")
    engine = Engine(store, hours, Zones({"zones": {}}))
    alerter = Alerter(store, settings)
    return KairosService(
        settings, store, QueueTimesFetcher({"p": 1}), engine, hours, alerter, []
    )


def _ctx(service: KairosService, allowed: set[int] | None):
    bot_data = {"service": service, "allowed_chats": allowed}
    return SimpleNamespace(application=SimpleNamespace(bot_data=bot_data))


def _update(chat_id: int | None, user_id: int | None = None):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id) if chat_id is not None else None,
        effective_user=SimpleNamespace(id=user_id) if user_id is not None else None,
    )


def test_resolve_prefers_configured_chat(tmp_path) -> None:
    service = make_service(tmp_path)
    service.settings.telegram_chat_id = "-1234"
    assert resolve_allowed_chats(service, service.settings) == {-1234}


def test_resolve_falls_back_to_stored_then_none(tmp_path) -> None:
    service = make_service(tmp_path)
    assert resolve_allowed_chats(service, service.settings) is None  # rien de configuré
    service.store.set_state("telegram_chat_id", -42)
    assert resolve_allowed_chats(service, service.settings) == {-42}


async def test_guard_blocks_unknown_chat(tmp_path) -> None:
    service = make_service(tmp_path)
    with pytest.raises(ApplicationHandlerStop):
        await guard_chat(_update(999), _ctx(service, {-42}))


async def test_guard_allows_known_chat(tmp_path) -> None:
    service = make_service(tmp_path)
    await guard_chat(_update(-42), _ctx(service, {-42}))  # ne doit pas lever


async def test_guard_allows_everything_when_unconfigured(tmp_path) -> None:
    service = make_service(tmp_path)
    await guard_chat(_update(999), _ctx(service, None))  # mode amorçage


async def test_track_chat_never_stores_unknown_when_restricted(tmp_path) -> None:
    service = make_service(tmp_path)
    await track_chat(_update(999), _ctx(service, {-42}))
    assert service.store.get_state("telegram_chat_id") is None


async def test_track_chat_stores_allowed_chat(tmp_path) -> None:
    service = make_service(tmp_path)
    await track_chat(_update(-42), _ctx(service, {-42}))
    assert service.store.get_state("telegram_chat_id") == -42
