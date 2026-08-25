"""Tests for TelegramLinkService (rapidboxes/telegram_link.py): the linking
flow, code expiry, persistence across instances, send_message's graceful
degradation, and (bottom of the file) chat-over-Telegram routing to the
same AssistantService the web UI uses. The actual Telegram HTTP API is
never called -- `_client.post` is monkeypatched with a fake before anything
that would reach it (completing a link sends a confirmation DM, so even the
"does linking work" tests need this, not just the send_message tests)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from rapidboxes import telegram_link as telegram_link_module
from rapidboxes.assistant.service import AssistantService
from rapidboxes.config import AppConfig
from rapidboxes.storage import Storage
from rapidboxes.telegram_link import LINK_CODE_TTL_S, MAX_CHAT_HISTORY, TelegramLinkService, _strip_markdown_lite


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
    unset = TelegramLinkService(None, None, tmp_path / "links.json", Storage(tmp_path / "storage"))
    assert unset.configured is False
    await unset.shutdown()

    token_only = TelegramLinkService("abc", None, tmp_path / "links.json", Storage(tmp_path / "storage"))
    assert token_only.configured is False
    await token_only.shutdown()

    both = TelegramLinkService("abc", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"))
    assert both.configured is True
    await both.shutdown()


@pytest.mark.asyncio
async def test_request_link_code_is_six_digits_and_not_yet_linked(tmp_path: Path):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"))
    code = service.request_link_code("ivan")
    assert code.isdigit()
    assert len(code) == 6
    assert service.is_linked("ivan") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_completing_a_link_persists_and_is_case_insensitive(tmp_path: Path, monkeypatch):
    links_path = tmp_path / "links.json"
    service = TelegramLinkService("token", "MyBot", links_path, Storage(tmp_path / "storage"))
    code = service.request_link_code("Ivan")
    _mock_post(monkeypatch, service)
    await service._try_complete_link(code, chat_id=555)

    assert service.is_linked("ivan") is True
    assert service.is_linked("IVAN") is True
    await service.shutdown()

    # A fresh instance pointed at the same file picks up the persisted link.
    reloaded = TelegramLinkService("token", "MyBot", links_path, Storage(tmp_path / "storage"))
    assert reloaded.is_linked("ivan") is True
    await reloaded.shutdown()


@pytest.mark.asyncio
async def test_re_requesting_a_code_invalidates_the_previous_one(tmp_path: Path, monkeypatch):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"))
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
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"))
    code = service.request_link_code("ivan")

    future = telegram_link_module.time.monotonic() + LINK_CODE_TTL_S + 1
    monkeypatch.setattr(telegram_link_module.time, "monotonic", lambda: future)
    # Expired -- returns before ever reaching the network, no mock needed.
    await service._try_complete_link(code, chat_id=1)
    assert service.is_linked("ivan") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_unknown_code_is_ignored(tmp_path: Path):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"))
    # No pending code matches -- returns before ever reaching the network.
    await service._try_complete_link("999999", chat_id=1)
    assert service._links == {}
    await service.shutdown()


@pytest.mark.asyncio
async def test_send_message_when_not_configured_returns_false(tmp_path: Path):
    service = TelegramLinkService(None, None, tmp_path / "links.json", Storage(tmp_path / "storage"))
    assert await service.send_message("ivan", "hi") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_send_message_when_not_linked_returns_false(tmp_path: Path):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"))
    assert await service.send_message("ivan", "hi") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_send_message_posts_to_the_linked_chat(tmp_path: Path, monkeypatch):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"))
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
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"))
    code = service.request_link_code("ivan")
    _mock_post(monkeypatch, service)
    await service._try_complete_link(code, 42)

    async def failing_post(url, json=None, **kw):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(service._client, "post", failing_post)

    ok = await service.send_message("ivan", "hi")
    assert ok is False
    await service.shutdown()


# --- _strip_markdown_lite: same fix as the web UI's markdownLite.ts --------


def test_strip_markdown_lite_bold_and_italic():
    assert _strip_markdown_lite("I am **PidiBot**.") == "I am PidiBot."
    assert _strip_markdown_lite("tap *Folders*") == "tap Folders"


def test_strip_markdown_lite_bullets_dont_eat_a_following_bold_span():
    # The exact real-reply shape that broke the web UI's first attempt at
    # this: a bullet's leading "* " must not be read as an *italic* opener
    # that swallows into the following **bold**.
    text = "*   **Original prototype & Backend:** Ivan Kashkan\n*   **UI:** Judith Garcia Gonzalez"
    result = _strip_markdown_lite(text)
    assert "*" not in result
    assert "• Original prototype & Backend: Ivan Kashkan" in result
    assert "• UI: Judith Garcia Gonzalez" in result


# --- chat over Telegram: routes to the same AssistantService the web -------
# --- UI uses, strictly scoped to whoever the chat_id is linked to. ---------


def _mock_llm_reply(monkeypatch, content: str) -> None:
    async def fake_call(self, messages):  # noqa: ANN001 - matches _call_llm's signature
        return {"role": "assistant", "content": content}

    monkeypatch.setattr(AssistantService, "_call_llm", fake_call)


@pytest.fixture
def chat_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        simulation=True,
        storage_root=tmp_path / "experiments",
        settings_path=tmp_path / "settings.json",
        assistant_archive_dir=tmp_path / "assistant_archive",
        assistant_api_base_url="http://127.0.0.1:1",
        assistant_api_key="test-key",
        telegram_links_path=tmp_path / "links.json",
        spa_dir=None,
    )


async def _linked_service(chat_config: AppConfig, monkeypatch, username: str, chat_id: int) -> TelegramLinkService:
    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService("token", "MyBot", chat_config.telegram_links_path, storage)
    code = service.request_link_code(username)
    _mock_post(monkeypatch, service)
    await service._try_complete_link(code, chat_id)
    return service


@pytest.mark.asyncio
async def test_chat_message_from_unlinked_chat_is_rejected(chat_config: AppConfig, monkeypatch):
    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService("token", "MyBot", chat_config.telegram_links_path, storage)
    calls = _mock_post(monkeypatch, service)

    await service._handle_chat_message(999, "hello")

    assert len(calls) == 1
    _, body = calls[0]
    assert "don't recognize this Telegram account" in body["text"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_chat_message_without_assistant_attached_reports_unavailable(
    chat_config: AppConfig, monkeypatch
):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)

    calls = _mock_post(monkeypatch, service)
    await service._handle_chat_message(42, "hi")

    assert "isn't available right now" in calls[0][1]["text"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_chat_message_routes_to_assistant_and_strips_markdown(
    chat_config: AppConfig, monkeypatch
):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    assistant = AssistantService(chat_config, storage)
    _mock_llm_reply(monkeypatch, "I am **PidiBot**, here to help.")
    service.attach_assistant(assistant)

    calls = _mock_post(monkeypatch, service)
    await service._handle_chat_message(42, "who are you?")

    send_calls = [c for c in calls if c[0] == "/bottoken/sendMessage"]
    assert len(send_calls) == 1
    assert send_calls[0][1]["text"] == "I am PidiBot, here to help."
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_chat_history_accumulates_across_turns(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    assistant = AssistantService(chat_config, storage)
    seen_history_lengths = []

    async def fake_call(self, messages):
        # messages[0] is the system prompt; the rest mirror history + this turn's user message.
        seen_history_lengths.append(len(messages) - 1)
        return {"role": "assistant", "content": "ok"}

    monkeypatch.setattr(AssistantService, "_call_llm", fake_call)
    service.attach_assistant(assistant)
    _mock_post(monkeypatch, service)

    await service._handle_chat_message(42, "first")
    await service._handle_chat_message(42, "second")

    assert seen_history_lengths == [1, 3]
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_chat_history_is_capped(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    assistant = AssistantService(chat_config, storage)
    _mock_llm_reply(monkeypatch, "ok")
    service.attach_assistant(assistant)
    _mock_post(monkeypatch, service)

    for i in range(MAX_CHAT_HISTORY):
        await service._handle_chat_message(42, f"message {i}")

    assert len(service._chat_history[42]) == MAX_CHAT_HISTORY
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_chat_message_reports_assistant_unavailable(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    assistant = AssistantService(chat_config, storage)

    async def fake_call(self, messages):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(AssistantService, "_call_llm", fake_call)
    service.attach_assistant(assistant)

    calls = _mock_post(monkeypatch, service)
    await service._handle_chat_message(42, "hi")

    assert "isn't reachable right now" in calls[0][1]["text"]
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_chat_message_with_shown_image_sends_a_real_photo(chat_config: AppConfig, monkeypatch):
    from PIL import Image

    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": "2026-01-01T00:00:00"})
    Image.new("RGB", (32, 32), color="white").save(exp.path / "dark_00000.png", "PNG")

    assistant = AssistantService(chat_config, storage)

    async def fake_call(self, messages):
        return {
            "role": "assistant",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "show_image", "arguments": "{}"}}
            ],
        }

    monkeypatch.setattr(AssistantService, "_call_llm", fake_call)
    service.attach_assistant(assistant)

    text_calls = []
    photo_calls = []

    async def fake_post(url, json=None, data=None, files=None, **kw):
        if url.endswith("/sendPhoto"):
            photo_calls.append((url, data, files))
        else:
            text_calls.append((url, json))
        return _FakeResponse()

    monkeypatch.setattr(service._client, "post", fake_post)

    await service._handle_chat_message(42, "show me the first image")

    assert len(photo_calls) == 1
    _, data, files = photo_calls[0]
    assert data["chat_id"] == "42"
    assert "photo" in files
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_chat_message_requesting_download_sends_a_real_zip_document(
    chat_config: AppConfig, monkeypatch
):
    from PIL import Image

    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": "2026-01-01T00:00:00"})
    Image.new("RGB", (32, 32), color="white").save(exp.path / "dark_00000.png", "PNG")

    assistant = AssistantService(chat_config, storage)

    async def fake_call(self, messages):
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "download_experiment", "arguments": "{}"},
                }
            ],
        }

    monkeypatch.setattr(AssistantService, "_call_llm", fake_call)
    service.attach_assistant(assistant)

    text_calls = []
    document_calls = []

    async def fake_post(url, json=None, data=None, files=None, **kw):
        if url.endswith("/sendDocument"):
            document_calls.append((url, data, files))
        else:
            text_calls.append((url, json))
        return _FakeResponse()

    monkeypatch.setattr(service._client, "post", fake_post)

    await service._handle_chat_message(42, "zip up my experiment")

    assert len(document_calls) == 1
    _, data, files = document_calls[0]
    assert data["chat_id"] == "42"
    filename, content, _ctype = files["document"]
    assert filename == f"{exp.experiment_id}.zip"
    assert content  # real bytes, not empty
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_download_too_large_for_telegram_suggests_the_device_instead(
    chat_config: AppConfig, monkeypatch
):
    from rapidboxes.telegram_link import TELEGRAM_MAX_UPLOAD_BYTES

    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": "2026-01-01T00:00:00"})

    from rapidboxes.models import AssistantDownloadRef

    calls = _mock_post(monkeypatch, service)
    huge = AssistantDownloadRef(
        experimentId=exp.experiment_id,
        url=f"/api/experiments/{exp.experiment_id}/download",
        filename=f"{exp.experiment_id}.zip",
        sizeBytes=TELEGRAM_MAX_UPLOAD_BYTES + 1,
    )
    await service._send_download(42, huge)

    assert len(calls) == 1
    assert "too large" in calls[0][1]["text"]
    assert "Gallery" in calls[0][1]["text"]
    await service.shutdown()
