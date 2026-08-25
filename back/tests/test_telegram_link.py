"""Tests for TelegramLinkService (rapidboxes/telegram_link.py): the linking
flow, code expiry, persistence across instances, and send_message's
graceful degradation. The actual Telegram HTTP API is never called --
`_client.post` is monkeypatched with a fake before anything that would
reach it (completing a link sends a confirmation DM, so even the "does
linking work" tests need this, not just the send_message tests)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from rapidboxes import telegram_link as telegram_link_module
from rapidboxes.telegram_link import LINK_CODE_TTL_S, TelegramLinkService


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {}


def _mock_post(monkeypatch, service: TelegramLinkService) -> list:
    """Replaces service._client.post with a no-op fake and returns the list
    of (url, json_body) calls it records."""
    calls: list = []

    async def fake_post(url, json=None, **kw):
        calls.append((url, json))
        return _FakeResponse()

    monkeypatch.setattr(service._client, "post", fake_post)
    return calls


@pytest.mark.asyncio
async def test_configured_requires_both_token_and_username(tmp_path: Path):
    unset = TelegramLinkService(None, None, tmp_path / "links.json")
    assert unset.configured is False
    await unset.shutdown()

    token_only = TelegramLinkService("abc", None, tmp_path / "links.json")
    assert token_only.configured is False
    await token_only.shutdown()

    both = TelegramLinkService("abc", "MyBot", tmp_path / "links.json")
    assert both.configured is True
    await both.shutdown()


@pytest.mark.asyncio
async def test_request_link_code_is_six_digits_and_not_yet_linked(tmp_path: Path):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json")
    code = service.request_link_code("ivan")
    assert code.isdigit()
    assert len(code) == 6
    assert service.is_linked("ivan") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_completing_a_link_persists_and_is_case_insensitive(tmp_path: Path, monkeypatch):
    links_path = tmp_path / "links.json"
    service = TelegramLinkService("token", "MyBot", links_path)
    code = service.request_link_code("Ivan")
    _mock_post(monkeypatch, service)
    await service._try_complete_link(code, chat_id=555)

    assert service.is_linked("ivan") is True
    assert service.is_linked("IVAN") is True
    await service.shutdown()

    # A fresh instance pointed at the same file picks up the persisted link.
    reloaded = TelegramLinkService("token", "MyBot", links_path)
    assert reloaded.is_linked("ivan") is True
    await reloaded.shutdown()


@pytest.mark.asyncio
async def test_re_requesting_a_code_invalidates_the_previous_one(tmp_path: Path, monkeypatch):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json")
    old_code = service.request_link_code("ivan")
    new_code = service.request_link_code("ivan")
    assert old_code != new_code
    _mock_post(monkeypatch, service)

    await service._try_complete_link(old_code, chat_id=1)
    assert service.is_linked("ivan") is False

    await service._try_complete_link(new_code, chat_id=1)
    assert service.is_linked("ivan") is True
    await service.shutdown()


@pytest.mark.asyncio
async def test_expired_code_does_not_link(tmp_path: Path, monkeypatch):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json")
    code = service.request_link_code("ivan")

    future = telegram_link_module.time.monotonic() + LINK_CODE_TTL_S + 1
    monkeypatch.setattr(telegram_link_module.time, "monotonic", lambda: future)
    # Expired -- returns before ever reaching the network, no mock needed.
    await service._try_complete_link(code, chat_id=1)
    assert service.is_linked("ivan") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_unknown_code_is_ignored(tmp_path: Path):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json")
    # No pending code matches -- returns before ever reaching the network.
    await service._try_complete_link("999999", chat_id=1)
    assert service._links == {}
    await service.shutdown()


@pytest.mark.asyncio
async def test_send_message_when_not_configured_returns_false(tmp_path: Path):
    service = TelegramLinkService(None, None, tmp_path / "links.json")
    assert await service.send_message("ivan", "hi") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_send_message_when_not_linked_returns_false(tmp_path: Path):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json")
    assert await service.send_message("ivan", "hi") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_send_message_posts_to_the_linked_chat(tmp_path: Path, monkeypatch):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json")
    code = service.request_link_code("ivan")
    link_calls = _mock_post(monkeypatch, service)
    await service._try_complete_link(code, 42)
    link_calls.clear()  # only care about the send_message call below

    ok = await service.send_message("ivan", "possible issue detected")
    assert ok is True
    assert len(link_calls) == 1
    url, body = link_calls[0]
    assert url == "/bottoken/sendMessage"
    assert body == {"chat_id": 42, "text": "possible issue detected"}
    await service.shutdown()


@pytest.mark.asyncio
async def test_send_message_swallows_http_errors(tmp_path: Path, monkeypatch):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json")
    code = service.request_link_code("ivan")
    _mock_post(monkeypatch, service)
    await service._try_complete_link(code, 42)

    async def failing_post(url, json=None, **kw):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(service._client, "post", failing_post)

    ok = await service.send_message("ivan", "hi")
    assert ok is False
    await service.shutdown()
