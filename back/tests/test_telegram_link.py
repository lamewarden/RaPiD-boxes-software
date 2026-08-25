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

from rapidboxes import config_xml
from rapidboxes import telegram_link as telegram_link_module
from rapidboxes.assistant import summary as assistant_summary
from rapidboxes.assistant.service import AssistantService
from rapidboxes.config import AppConfig
from rapidboxes.engine.runner import ExperimentRunner
from rapidboxes.hardware.manager import build_hardware
from rapidboxes.models import (
    CameraSettings,
    DeviceSettings,
    ExperimentState,
    ExperimentStatus,
    SavedExperimentConfig,
)
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


class _FakeResponseWithBody:
    """Like _FakeResponse but with a real JSON body and an optional HTTP
    error status -- needed for the progress-bar tests, which read back a
    real message_id from sendMessage's response, and for the "message is
    not modified" 400 case editMessageText must swallow, not warn about."""

    def __init__(self, body: dict, status_code: int = 200, text: str = ""):
        self._body = body
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://test")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> dict:
        return self._body


def _mock_post_with_message_ids(monkeypatch, service: TelegramLinkService) -> list:
    """Records every (url, json_body) call and hands back an
    incrementing, real-looking message_id for every sendMessage, so
    _send_and_pin has something real to track."""
    calls: list = []
    counter = {"n": 1000}

    async def fake_post(url, json=None, **kw):
        calls.append((url, json))
        if url.endswith("/sendMessage"):
            counter["n"] += 1
            return _FakeResponseWithBody({"ok": True, "result": {"message_id": counter["n"]}})
        return _FakeResponseWithBody({"ok": True, "result": {}})

    monkeypatch.setattr(service._client, "post", fake_post)
    return calls


@pytest.mark.asyncio
async def test_configured_requires_both_token_and_username(tmp_path: Path):
    unset = TelegramLinkService(None, None, tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
    assert unset.configured is False
    await unset.shutdown()

    token_only = TelegramLinkService("abc", None, tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
    assert token_only.configured is False
    await token_only.shutdown()

    both = TelegramLinkService("abc", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
    assert both.configured is True
    await both.shutdown()


@pytest.mark.asyncio
async def test_request_link_code_is_six_digits_and_not_yet_linked(tmp_path: Path):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
    code = service.request_link_code("ivan")
    assert code.isdigit()
    assert len(code) == 6
    assert service.is_linked("ivan") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_completing_a_link_persists_and_is_case_insensitive(tmp_path: Path, monkeypatch):
    links_path = tmp_path / "links.json"
    service = TelegramLinkService("token", "MyBot", links_path, Storage(tmp_path / "storage"), tmp_path / "monitors.json")
    code = service.request_link_code("Ivan")
    _mock_post(monkeypatch, service)
    await service._try_complete_link(code, chat_id=555)

    assert service.is_linked("ivan") is True
    assert service.is_linked("IVAN") is True
    await service.shutdown()

    # A fresh instance pointed at the same file picks up the persisted link.
    reloaded = TelegramLinkService("token", "MyBot", links_path, Storage(tmp_path / "storage"), tmp_path / "monitors.json")
    assert reloaded.is_linked("ivan") is True
    await reloaded.shutdown()


@pytest.mark.asyncio
async def test_re_requesting_a_code_invalidates_the_previous_one(tmp_path: Path, monkeypatch):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
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
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
    code = service.request_link_code("ivan")

    future = telegram_link_module.time.monotonic() + LINK_CODE_TTL_S + 1
    monkeypatch.setattr(telegram_link_module.time, "monotonic", lambda: future)
    # Expired -- returns before ever reaching the network, no mock needed.
    await service._try_complete_link(code, chat_id=1)
    assert service.is_linked("ivan") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_unknown_code_is_ignored(tmp_path: Path):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
    # No pending code matches -- returns before ever reaching the network.
    await service._try_complete_link("999999", chat_id=1)
    assert service._links == {}
    await service.shutdown()


@pytest.mark.asyncio
async def test_send_message_when_not_configured_returns_false(tmp_path: Path):
    service = TelegramLinkService(None, None, tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
    assert await service.send_message("ivan", "hi") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_send_message_when_not_linked_returns_false(tmp_path: Path):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
    assert await service.send_message("ivan", "hi") is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_send_message_posts_to_the_linked_chat(tmp_path: Path, monkeypatch):
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
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
    service = TelegramLinkService("token", "MyBot", tmp_path / "links.json", Storage(tmp_path / "storage"), tmp_path / "monitors.json")
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
    service = TelegramLinkService("token", "MyBot", chat_config.telegram_links_path, storage, chat_config.telegram_links_path.parent / "monitors.json")
    code = service.request_link_code(username)
    _mock_post(monkeypatch, service)
    await service._try_complete_link(code, chat_id)
    return service


@pytest.mark.asyncio
async def test_chat_message_from_unlinked_chat_is_rejected(chat_config: AppConfig, monkeypatch):
    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService("token", "MyBot", chat_config.telegram_links_path, storage, chat_config.telegram_links_path.parent / "monitors.json")
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
async def test_chat_message_requesting_an_image_range_sends_only_that_subset(
    chat_config: AppConfig, monkeypatch
):
    """'send me the first two images' -- the zip Telegram actually receives
    must contain only those images, not the whole experiment folder."""
    import zipfile
    from io import BytesIO

    from PIL import Image

    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": "2026-01-01T00:00:00"})
    for i in range(5):
        Image.new("RGB", (4, 4), color="white").save(exp.path / f"dark_{i:05d}.png", "PNG")

    assistant = AssistantService(chat_config, storage)

    async def fake_call(self, messages):
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "download_experiment", "arguments": '{"firstN": 2}'},
                }
            ],
        }

    monkeypatch.setattr(AssistantService, "_call_llm", fake_call)
    service.attach_assistant(assistant)

    document_calls = []

    async def fake_post(url, json=None, data=None, files=None, **kw):
        if url.endswith("/sendDocument"):
            document_calls.append((url, data, files))
        return _FakeResponse()

    monkeypatch.setattr(service._client, "post", fake_post)

    await service._handle_chat_message(42, "send me the first two images")

    assert len(document_calls) == 1
    _, _data, files = document_calls[0]
    filename, content, _ctype = files["document"]
    assert filename == f"{exp.experiment_id}_2-images.zip"
    with zipfile.ZipFile(BytesIO(content)) as zf:
        assert set(zf.namelist()) == {"dark_00000.png", "dark_00001.png"}
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


# --- /monitor: on-demand subscription to the sender's own running run ------


class _FakeRunner:
    def __init__(self, status: ExperimentStatus):
        self.status = status


@pytest.mark.asyncio
async def test_monitor_from_unlinked_chat_is_rejected(chat_config: AppConfig, monkeypatch):
    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService(
        "token", "MyBot", chat_config.telegram_links_path, storage, chat_config.telegram_links_path.parent / "monitors.json"
    )
    calls = _mock_post(monkeypatch, service)

    await service._handle_monitor_command(999)

    assert len(calls) == 1
    assert "don't recognize this Telegram account" in calls[0][1]["text"]
    assert service._monitors == {}
    await service.shutdown()


@pytest.mark.asyncio
async def test_monitor_without_runner_attached_reports_unavailable(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    calls = _mock_post(monkeypatch, service)

    await service._handle_monitor_command(42)

    assert len(calls) == 1
    assert "isn't available right now" in calls[0][1]["text"]
    assert service._monitors == {}
    await service.shutdown()


@pytest.mark.asyncio
async def test_monitor_with_no_running_experiment_declines(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    service.attach_runner(_FakeRunner(ExperimentStatus(state=ExperimentState.idle)))
    calls = _mock_post(monkeypatch, service)

    await service._handle_monitor_command(42)

    assert len(calls) == 1
    assert "don't have an experiment running" in calls[0][1]["text"]
    assert service._monitors == {}
    await service.shutdown()


@pytest.mark.asyncio
async def test_monitor_declines_another_users_running_experiment(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    service.attach_runner(
        _FakeRunner(
            ExperimentStatus(state=ExperimentState.running, experimentId="exp1", username="someone-else")
        )
    )
    calls = _mock_post(monkeypatch, service)

    await service._handle_monitor_command(42)

    assert len(calls) == 1
    assert "don't have an experiment running" in calls[0][1]["text"]
    assert service._monitors == {}
    await service.shutdown()


@pytest.mark.asyncio
async def test_monitor_subscribes_to_own_running_experiment_and_persists(
    chat_config: AppConfig, monkeypatch
):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    service.attach_runner(
        _FakeRunner(ExperimentStatus(state=ExperimentState.running, experimentId="exp1", username="Ivan"))
    )
    calls = _mock_post(monkeypatch, service)

    await service._handle_monitor_command(42)

    assert len(calls) == 1
    assert "Now monitoring exp1" in calls[0][1]["text"]
    assert service._monitors == {"exp1": {42}}
    assert service.is_monitored("exp1") is True

    # Persisted -- a fresh instance pointed at the same file picks it up,
    # which is what makes notify_blackout able to report a restart at all.
    reloaded = TelegramLinkService(
        "token", "MyBot", service._links_path, service._storage, service._monitors_path
    )
    assert reloaded._monitors == {"exp1": {42}}
    await reloaded.shutdown()
    await service.shutdown()


@pytest.mark.asyncio
async def test_monitor_also_accepts_a_paused_experiment(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    service.attach_runner(
        _FakeRunner(ExperimentStatus(state=ExperimentState.paused, experimentId="exp1", username="ivan"))
    )
    calls = _mock_post(monkeypatch, service)

    await service._handle_monitor_command(42)

    assert "Now monitoring exp1" in calls[0][1]["text"]
    await service.shutdown()


# --- notify_issue: dual delivery to config-linked + /monitor subscribers ---


@pytest.mark.asyncio
async def test_notify_issue_reaches_both_the_linked_researcher_and_monitor_chat(
    chat_config: AppConfig, monkeypatch
):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    service._monitors["exp1"] = {99}
    calls = _mock_post(monkeypatch, service)

    await service.notify_issue("exp1", "ivan", "possible issue")

    recipients = {body["chat_id"] for _, body in calls}
    assert recipients == {42, 99}
    await service.shutdown()


@pytest.mark.asyncio
async def test_notify_issue_does_not_double_send_when_linked_chat_is_also_monitoring(
    chat_config: AppConfig, monkeypatch
):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    service._monitors["exp1"] = {42}
    calls = _mock_post(monkeypatch, service)

    await service.notify_issue("exp1", "ivan", "possible issue")

    assert len(calls) == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_notify_issue_with_no_subscribers_sends_nothing(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    service._links.clear()  # not opted in via Settings, either
    calls = _mock_post(monkeypatch, service)

    await service.notify_issue("exp1", "ivan", "possible issue")

    assert calls == []
    await service.shutdown()


# --- notify_blackout: only ever reaches /monitor subscribers ---------------


@pytest.mark.asyncio
async def test_notify_blackout_reaches_monitor_subscribers_only(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    service._links.clear()
    service._monitors["exp1"] = {99}
    calls = _mock_post(monkeypatch, service)

    await service.notify_blackout("exp1", "power was out for 4 minutes")

    assert len(calls) == 1
    assert calls[0][1]["chat_id"] == 99
    assert calls[0][1]["text"] == "⚡ power was out for 4 minutes"
    await service.shutdown()


@pytest.mark.asyncio
async def test_notify_blackout_with_no_subscribers_sends_nothing(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    calls = _mock_post(monkeypatch, service)

    await service.notify_blackout("exp1", "power was out")

    assert calls == []
    await service.shutdown()


# --- notify_completion: first/last image, AI summary, settings recap, -----
# --- download nudge, one-shot subscription cleanup -------------------------


@pytest.mark.asyncio
async def test_notify_completion_with_no_subscribers_is_a_noop(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    calls = _mock_post(monkeypatch, service)

    await service.notify_completion(exp, ExperimentStatus(state=ExperimentState.done, message="done"))

    assert calls == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_notify_completion_sends_summary_images_and_settings_then_clears_subscription(
    chat_config: AppConfig, monkeypatch
):
    from PIL import Image

    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    Image.new("RGB", (32, 32), color="white").save(exp.path / "dark_00000.png", "PNG")
    Image.new("RGB", (32, 32), color="black").save(exp.path / "dark_00001.png", "PNG")

    saved = SavedExperimentConfig(
        protocol="tropism",
        darkPhaseHours=12.5,
        lateralIlluminationHours=3.0,
        intervalMinutes=15.0,
        photoIlluminationSource="rgbw",
        camera=CameraSettings(grayscale=False, zoom=2.0),
    )
    exp.write_config_xml(config_xml.serialize(saved), "run1")
    (exp.path / "ai_summary.json").write_text(
        '{"textSummary": "Ran smoothly, no issues.", "moldDetected": true, '
        '"moldFrameCount": 3, "framesChecked": 5}'
    )

    service._monitors[exp.experiment_id] = {99}
    text_calls = []
    photo_calls = []

    async def fake_post(url, json=None, data=None, files=None, **kw):
        if url.endswith("/sendPhoto"):
            photo_calls.append((url, data, files))
        else:
            text_calls.append((url, json))
        return _FakeResponse()

    monkeypatch.setattr(service._client, "post", fake_post)

    await service.notify_completion(exp, ExperimentStatus(state=ExperimentState.done, message="Completed on schedule."))

    assert len(text_calls) == 1
    text = text_calls[0][1]["text"]
    assert text_calls[0][1]["chat_id"] == 99
    assert exp.experiment_id in text
    assert "Completed on schedule." in text
    assert "Ran smoothly, no issues." in text
    assert "Possible mold detected (3/5 frames)" in text
    assert "download this experiment" in text
    assert "*" not in text  # markdown-lite stripped, same as chat replies

    assert len(photo_calls) == 2  # first + last
    assert photo_calls[0][1]["chat_id"] == "99"

    # One-shot: the subscription is consumed, and persisted as gone.
    assert service.is_monitored(exp.experiment_id) is False
    reloaded_monitors = telegram_link_module._load_monitors(service._monitors_path)
    assert exp.experiment_id not in reloaded_monitors

    await service.shutdown()


@pytest.mark.asyncio
async def test_notify_completion_with_a_single_image_sends_only_one_photo(
    chat_config: AppConfig, monkeypatch
):
    from PIL import Image

    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    Image.new("RGB", (32, 32), color="white").save(exp.path / "dark_00000.png", "PNG")
    service._monitors[exp.experiment_id] = {99}

    photo_calls = []

    async def fake_post(url, json=None, data=None, files=None, **kw):
        if url.endswith("/sendPhoto"):
            photo_calls.append((url, data, files))
        return _FakeResponse()

    monkeypatch.setattr(service._client, "post", fake_post)

    await service.notify_completion(exp, ExperimentStatus(state=ExperimentState.done, message="done"))

    assert len(photo_calls) == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_notify_completion_without_a_stored_ai_summary_still_sends_a_recap(
    chat_config: AppConfig, monkeypatch
):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    service._monitors[exp.experiment_id] = {99}
    assert assistant_summary.read_stored(exp) is None

    calls = _mock_post(monkeypatch, service)

    await service.notify_completion(exp, ExperimentStatus(state=ExperimentState.error, message="capture failed"))

    assert len(calls) == 1
    text = calls[0][1]["text"]
    assert "capture failed" in text
    assert "download this experiment" in text
    await service.shutdown()


# --- _parse_command ----------------------------------------------------------


def test_parse_command_splits_off_the_bot_username_and_argument_text():
    from rapidboxes.telegram_link import _parse_command

    assert _parse_command("/launch like my run from tuesday") == ("/launch", "like my run from tuesday")
    assert _parse_command("/status@IEB_pidibot") == ("/status", "")
    assert _parse_command("/help") == ("/help", "")
    assert _parse_command("  /monitor  ") == ("/monitor", "")


def test_parse_command_returns_none_for_ordinary_text():
    from rapidboxes.telegram_link import _parse_command

    assert _parse_command("how much storage do I have left") is None
    assert _parse_command("") is None


# --- /help -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_command_works_even_when_unlinked(chat_config: AppConfig, monkeypatch):
    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService(
        "token", "MyBot", chat_config.telegram_links_path, storage, chat_config.telegram_links_path.parent / "monitors.json"
    )
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/help", "")

    assert len(calls) == 1
    text = calls[0][1]["text"]
    assert "isn't linked yet" in text
    for command in ("/status", "/experiments", "/monitor", "/launch", "/unlink", "/help"):
        assert command in text
    await service.shutdown()


@pytest.mark.asyncio
async def test_help_command_when_linked_has_no_unlinked_note(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/help", "")

    assert "isn't linked yet" not in calls[0][1]["text"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_unknown_command_gets_a_direct_reply_not_forwarded_to_chat(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)

    async def fail_if_called(self, messages):
        raise AssertionError("unknown /command must not reach the model")

    monkeypatch.setattr(AssistantService, "_call_llm", fail_if_called)
    assistant = AssistantService(chat_config, service._storage)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/frobnicate", "")

    assert len(calls) == 1
    assert "/frobnicate" in calls[0][1]["text"]
    assert "/help" in calls[0][1]["text"]
    await assistant.aclose()
    await service.shutdown()


# --- /status -------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_command_requires_linking(chat_config: AppConfig, monkeypatch):
    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService(
        "token", "MyBot", chat_config.telegram_links_path, storage, chat_config.telegram_links_path.parent / "monitors.json"
    )
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/status", "")

    assert "don't recognize this Telegram account" in calls[0][1]["text"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_status_command_without_assistant_reports_unavailable(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/status", "")

    assert "isn't available right now" in calls[0][1]["text"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_status_command_is_device_wide_not_scoped_to_the_asker(chat_config: AppConfig, monkeypatch):
    """The one deliberate exception to strict per-user scoping -- confirms
    it reports on someone ELSE's running experiment, not just the asker's
    own (or nothing). Uses a real ExperimentRunner (not the plain
    _FakeRunner the /monitor tests use) because resolve_system_status also
    reads real hardware state via runner._hw -- same pattern
    test_assistant.py's own system_status tests use."""
    storage = Storage(chat_config.storage_root)
    hw = build_hardware(chat_config, DeviceSettings())
    runner = ExperimentRunner(hw, storage)
    runner.status.state = ExperimentState.running
    runner.status.experimentId = "exp1"
    runner.status.username = "sabol"
    runner.status.imagesCaptured = 3
    runner.status.imagesPlanned = 10

    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assistant = AssistantService(chat_config, storage, runner=runner)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/status", "")

    assert "sabol" in calls[0][1]["text"]
    await assistant.aclose()
    assert "exp1" in calls[0][1]["text"]
    await assistant.aclose()
    await service.shutdown()


# --- /experiments --------------------------------------------------------


@pytest.mark.asyncio
async def test_experiments_command_requires_linking(chat_config: AppConfig, monkeypatch):
    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService(
        "token", "MyBot", chat_config.telegram_links_path, storage, chat_config.telegram_links_path.parent / "monitors.json"
    )
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/experiments", "")

    assert "don't recognize this Telegram account" in calls[0][1]["text"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_experiments_command_lists_only_the_askers_own(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    ivan_exp = storage.create_experiment("ivan", "run1")
    ivan_exp.write_metadata({"username": "ivan"})
    sabol_exp = storage.create_experiment("sabol", "sabol-run")
    sabol_exp.write_metadata({"username": "sabol"})

    assistant = AssistantService(chat_config, storage)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/experiments", "")

    text = calls[0][1]["text"]
    assert ivan_exp.experiment_id in text
    assert sabol_exp.experiment_id not in text
    await assistant.aclose()
    await service.shutdown()


# --- /unlink ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlink_command_removes_and_persists(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assert service.is_linked("ivan") is True
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/unlink", "")

    assert service.is_linked("ivan") is False
    assert "Unlinked" in calls[0][1]["text"]

    reloaded = TelegramLinkService(
        "token", "MyBot", service._links_path, service._storage, service._monitors_path
    )
    assert reloaded.is_linked("ivan") is False
    await reloaded.shutdown()
    await service.shutdown()


@pytest.mark.asyncio
async def test_unlink_command_when_not_linked_says_so(chat_config: AppConfig, monkeypatch):
    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService(
        "token", "MyBot", chat_config.telegram_links_path, storage, chat_config.telegram_links_path.parent / "monitors.json"
    )
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(999, "/unlink", "")

    assert "nothing to unlink" in calls[0][1]["text"]
    await service.shutdown()


# --- /launch -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_command_requires_linking(chat_config: AppConfig, monkeypatch):
    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService(
        "token", "MyBot", chat_config.telegram_links_path, storage, chat_config.telegram_links_path.parent / "monitors.json"
    )
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "like my last run")

    assert "don't recognize this Telegram account" in calls[0][1]["text"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_launch_command_seeds_the_overview_from_a_real_past_experiment(
    chat_config: AppConfig, monkeypatch
):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "tropism-run")
    exp.write_metadata({"username": "ivan", "startedAt": "2026-01-01T00:00:00"})
    saved = SavedExperimentConfig(
        protocol="tropism",
        darkPhaseHours=12.5,
        lateralIlluminationHours=3.0,
        intervalMinutes=15.0,
        photoIlluminationSource="rgbw",
        camera=CameraSettings(grayscale=False, zoom=2.0),
    )
    exp.write_config_xml(config_xml.serialize(saved), "tropism-run")

    assistant = AssistantService(chat_config, storage)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "")

    # Two messages: the overview (seeded from the real past run), then the
    # first question (measurement type).
    assert len(calls) == 2
    overview = calls[0][1]["text"]
    assert "from your last run" in overview
    assert "12.5h" in overview  # dark phase length carried over verbatim
    assert "RGBW" in overview
    assert "measurement" in calls[1][1]["text"].lower()
    assert 42 in service._launch_wizards
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_launch_command_falls_back_to_defaults_when_nothing_found(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    sabol_exp = storage.create_experiment("sabol", "sabol-run")
    sabol_exp.write_metadata({"username": "sabol", "startedAt": "2026-01-01T00:00:00"})

    assistant = AssistantService(chat_config, storage)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    # Naming sabol in the free text does nothing -- /launch never takes a
    # username argument, only "reference", so this can never resolve to
    # sabol's run; with no run of ivan's own either, it starts from bare
    # defaults rather than refusing outright.
    await service._dispatch_command(42, "/launch", "like sabol's run")

    assert "No past run found" in calls[0][1]["text"]
    assert 42 in service._launch_wizards
    await assistant.aclose()
    await service.shutdown()


# --- /launch wizard: the full guided flow ------------------------------------


@pytest.mark.asyncio
async def test_launch_wizard_full_tropism_flow_stages_a_pending_launch(chat_config: AppConfig, monkeypatch):
    """End-to-end: every question answered validly, confirmed, and the
    exact resolved config lands in AssistantService.take_pending_launch --
    never anything invented, always what was actually typed."""
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assistant = AssistantService(chat_config, service._storage)

    async def fail_if_called(self, messages):
        raise AssertionError("the /launch wizard must never call the model")

    monkeypatch.setattr(AssistantService, "_call_llm", fail_if_called)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "")  # overview + first question (measurement)
    await service._maybe_continue_launch_wizard(42, "tropism")
    await service._maybe_continue_launch_wizard(42, "yes")  # dark phase enabled
    await service._maybe_continue_launch_wizard(42, "48")  # dark phase hours
    await service._maybe_continue_launch_wizard(42, "10")  # bending hours
    await service._maybe_continue_launch_wizard(42, "red, blue")  # spectra
    await service._maybe_continue_launch_wizard(42, "12")  # interval
    await service._maybe_continue_launch_wizard(42, "60")  # intensity
    await service._maybe_continue_launch_wizard(42, "rgbw")  # light source
    await service._maybe_continue_launch_wizard(42, "no")  # issue alerts

    confirmation_text = calls[-1][1]["text"]
    assert "Ready to review" in confirmation_text
    assert "48 h" in confirmation_text  # format_config_knobs's own unit spacing
    assert "RGBW" in confirmation_text
    assert 42 in service._launch_wizards  # still open, awaiting yes/no

    await service._maybe_continue_launch_wizard(42, "yes")

    assert 42 not in service._launch_wizards
    final_text = calls[-1][1]["text"]
    assert "loaded these into the setup screen" in final_text
    assert "I don't start experiments myself" in final_text

    staged = assistant.take_pending_launch("ivan")
    assert staged is not None
    assert staged.protocol == "tropism"
    assert staged.darkPhaseEnabled is True
    assert staged.darkPhaseHours == 48.0
    assert staged.lateralIlluminationHours == 10.0
    assert staged.spectra == ["red", "blue"]
    assert staged.intervalMinutes == 12.0
    assert staged.intensity == 60
    assert staged.photoIlluminationSource == "rgbw"
    assert staged.reportOnIssueEnabled is False
    # One-shot -- a second take must come back empty.
    assert assistant.take_pending_launch("ivan") is None
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_launch_wizard_full_growth_flow_stages_a_pending_launch(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assistant = AssistantService(chat_config, service._storage)
    service.attach_assistant(assistant)
    _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "")
    await service._maybe_continue_launch_wizard(42, "growth")
    await service._maybe_continue_launch_wizard(42, "18")  # day length hours
    await service._maybe_continue_launch_wizard(42, "21")  # experiment length days
    await service._maybe_continue_launch_wizard(42, "white")  # spectra
    await service._maybe_continue_launch_wizard(42, "40")  # day intensity
    await service._maybe_continue_launch_wizard(42, "25")  # interval
    await service._maybe_continue_launch_wizard(42, "ir")  # light source
    await service._maybe_continue_launch_wizard(42, "yes")  # issue alerts
    await service._maybe_continue_launch_wizard(42, "yes")  # confirm

    staged = assistant.take_pending_launch("ivan")
    assert staged is not None
    assert staged.protocol == "growth"
    assert staged.dayLengthHours == 18
    assert staged.experimentLengthDays == 21
    assert staged.spectra == ["white"]
    assert staged.dayIntensity == 40
    assert staged.intervalMinutes == 25.0
    assert staged.photoIlluminationSource == "ir"
    assert staged.reportOnIssueEnabled is True
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_launch_wizard_dark_phase_hours_skipped_when_disabled(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assistant = AssistantService(chat_config, service._storage)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "")
    await service._maybe_continue_launch_wizard(42, "tropism")
    await service._maybe_continue_launch_wizard(42, "no")  # dark phase disabled

    # Next question must be the bending phase, not dark phase length.
    next_question = calls[-1][1]["text"]
    assert "Bending" in next_question or "bending" in next_question
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_launch_wizard_invalid_answer_warns_and_repeats_the_same_question(
    chat_config: AppConfig, monkeypatch
):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assistant = AssistantService(chat_config, service._storage)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "")
    state = service._launch_wizards[42]
    assert state.index == 0  # still on the measurement question

    await service._maybe_continue_launch_wizard(42, "banana")  # not tropism/growth

    warning = calls[-1][1]["text"]
    assert "⚠️" in warning
    assert state.index == 0  # did not advance

    await service._maybe_continue_launch_wizard(42, "500")  # dark phase hours out of range (0-350)
    # First get past the protocol + darkPhaseEnabled questions validly.
    await service._maybe_continue_launch_wizard(42, "tropism")
    await service._maybe_continue_launch_wizard(42, "yes")
    before = state.index
    await service._maybe_continue_launch_wizard(42, "not a number")
    assert state.index == before
    assert "⚠️" in calls[-1][1]["text"]
    assert "please send a number" in calls[-1][1]["text"]

    await service._maybe_continue_launch_wizard(42, "500")  # out of bounds
    assert state.index == before
    assert "must be between" in calls[-1][1]["text"]
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_launch_wizard_cancel_mid_flow(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assistant = AssistantService(chat_config, service._storage)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "")
    await service._maybe_continue_launch_wizard(42, "/cancel")

    assert 42 not in service._launch_wizards
    assert "Cancelled" in calls[-1][1]["text"]
    assert assistant.take_pending_launch("ivan") is None
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_launch_wizard_no_at_confirmation_cancels_without_staging(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assistant = AssistantService(chat_config, service._storage)
    service.attach_assistant(assistant)
    _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "")
    await service._maybe_continue_launch_wizard(42, "tropism")
    await service._maybe_continue_launch_wizard(42, "no")
    await service._maybe_continue_launch_wizard(42, "10")
    await service._maybe_continue_launch_wizard(42, "white")
    await service._maybe_continue_launch_wizard(42, "20")
    await service._maybe_continue_launch_wizard(42, "25")
    await service._maybe_continue_launch_wizard(42, "ir")
    await service._maybe_continue_launch_wizard(42, "no")
    assert 42 in service._launch_wizards  # awaiting confirmation

    await service._maybe_continue_launch_wizard(42, "no")

    assert 42 not in service._launch_wizards
    assert assistant.take_pending_launch("ivan") is None
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_launch_wizard_garbage_at_confirmation_reprompts(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assistant = AssistantService(chat_config, service._storage)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "")
    await service._maybe_continue_launch_wizard(42, "tropism")
    await service._maybe_continue_launch_wizard(42, "no")
    await service._maybe_continue_launch_wizard(42, "10")
    await service._maybe_continue_launch_wizard(42, "white")
    await service._maybe_continue_launch_wizard(42, "20")
    await service._maybe_continue_launch_wizard(42, "25")
    await service._maybe_continue_launch_wizard(42, "ir")
    await service._maybe_continue_launch_wizard(42, "no")

    await service._maybe_continue_launch_wizard(42, "maybe")

    assert 42 in service._launch_wizards
    assert 'confirm' in calls[-1][1]["text"].lower()
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_launch_wizard_restarts_cleanly_on_a_fresh_launch(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assistant = AssistantService(chat_config, service._storage)
    service.attach_assistant(assistant)
    _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "")
    await service._maybe_continue_launch_wizard(42, "tropism")
    assert service._launch_wizards[42].index == 1

    await service._maybe_continue_launch_wizard(42, "/launch")

    fresh = service._launch_wizards[42]
    assert fresh.index == 0
    assert fresh.fields == [telegram_link_module._LAUNCH_PROTOCOL_FIELD]
    await assistant.aclose()
    await service.shutdown()


@pytest.mark.asyncio
async def test_launch_wizard_abandoned_past_timeout_is_dropped(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    assistant = AssistantService(chat_config, service._storage)

    async def fake_call(self, messages):
        return {"role": "assistant", "content": "ok, that's a normal chat reply"}

    monkeypatch.setattr(AssistantService, "_call_llm", fake_call)
    service.attach_assistant(assistant)
    calls = _mock_post(monkeypatch, service)

    await service._dispatch_command(42, "/launch", "")
    state = service._launch_wizards[42]
    state.last_activity = (
        telegram_link_module.time.monotonic() - telegram_link_module.LAUNCH_WIZARD_TIMEOUT_S - 1
    )

    consumed = await service._maybe_continue_launch_wizard(42, "hello")
    assert consumed is False
    assert 42 not in service._launch_wizards
    await assistant.aclose()
    await service.shutdown()


# --- /monitor's pinned progress bar -----------------------------------------


@pytest.mark.asyncio
async def test_monitor_pins_a_progress_message_for_a_real_experiment(chat_config: AppConfig, monkeypatch):
    from PIL import Image

    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": "2026-01-01T00:00:00"})
    Image.new("RGB", (4, 4), color="white").save(exp.path / "dark_00000.png", "PNG")
    service.attach_runner(
        _FakeRunner(
            ExperimentStatus(
                state=ExperimentState.running,
                experimentId=exp.experiment_id,
                username="ivan",
                elapsedSeconds=3600,
                totalSeconds=7200,
                imagesCaptured=1,
                imagesPlanned=2,
            )
        )
    )
    calls = _mock_post_with_message_ids(monkeypatch, service)

    await service._handle_monitor_command(42)

    send_calls = [c for c in calls if c[0].endswith("/sendMessage")]
    pin_calls = [c for c in calls if c[0].endswith("/pinChatMessage")]
    assert len(send_calls) == 2  # "Now monitoring..." + the progress bar
    assert len(pin_calls) == 1
    progress_text = send_calls[1][1]["text"]
    assert "50%" in progress_text
    assert exp.experiment_id in progress_text
    key = (exp.experiment_id, 42)
    assert key in service._progress_messages
    assert pin_calls[0][1]["message_id"] == service._progress_messages[key]
    await service.shutdown()


@pytest.mark.asyncio
async def test_monitor_does_not_pin_without_a_real_experiment_folder(chat_config: AppConfig, monkeypatch):
    """Matches the other /monitor tests' fake "exp1" id -- no real folder
    on disk, so there is nothing real to point a progress bar at."""
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    service.attach_runner(
        _FakeRunner(ExperimentStatus(state=ExperimentState.running, experimentId="exp1", username="ivan"))
    )
    calls = _mock_post_with_message_ids(monkeypatch, service)

    await service._handle_monitor_command(42)

    assert not any(c[0].endswith("/pinChatMessage") for c in calls)
    assert service._progress_messages == {}
    await service.shutdown()


@pytest.mark.asyncio
async def test_monitor_twice_does_not_create_a_second_pin(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": "2026-01-01T00:00:00"})
    service.attach_runner(
        _FakeRunner(
            ExperimentStatus(state=ExperimentState.running, experimentId=exp.experiment_id, username="ivan")
        )
    )
    calls = _mock_post_with_message_ids(monkeypatch, service)

    await service._handle_monitor_command(42)
    await service._handle_monitor_command(42)

    assert len([c for c in calls if c[0].endswith("/pinChatMessage")]) == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_progress_tick_edits_the_pinned_message_after_the_interval(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": "2026-01-01T00:00:00"})
    service.attach_runner(
        _FakeRunner(
            ExperimentStatus(
                state=ExperimentState.running,
                experimentId=exp.experiment_id,
                username="ivan",
                elapsedSeconds=1800,
                totalSeconds=3600,
                imagesCaptured=5,
                imagesPlanned=10,
            )
        )
    )
    service._monitors[exp.experiment_id] = {42}
    service._progress_messages[(exp.experiment_id, 42)] = 555
    # time.monotonic() isn't wall-clock -- 0.0 isn't "long ago" relative to
    # its own epoch, only a value computed relative to the real current
    # reading is (same pattern test_expired_code_does_not_link uses).
    from rapidboxes.telegram_link import PROGRESS_UPDATE_INTERVAL_S

    service._progress_last_edit[(exp.experiment_id, 42)] = (
        telegram_link_module.time.monotonic() - PROGRESS_UPDATE_INTERVAL_S - 1
    )

    calls = _mock_post_with_message_ids(monkeypatch, service)
    await service._progress_tick()

    edit_calls = [c for c in calls if c[0].endswith("/editMessageText")]
    assert len(edit_calls) == 1
    assert edit_calls[0][1]["message_id"] == 555
    assert "50%" in edit_calls[0][1]["text"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_progress_tick_skips_a_recently_edited_pin(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    service.attach_runner(
        _FakeRunner(ExperimentStatus(state=ExperimentState.running, experimentId=exp.experiment_id, username="ivan"))
    )
    service._monitors[exp.experiment_id] = {42}
    service._progress_messages[(exp.experiment_id, 42)] = 555
    service._progress_last_edit[(exp.experiment_id, 42)] = telegram_link_module.time.monotonic()

    calls = _mock_post_with_message_ids(monkeypatch, service)
    await service._progress_tick()

    assert not any(c[0].endswith("/editMessageText") for c in calls)
    await service.shutdown()


@pytest.mark.asyncio
async def test_progress_tick_is_a_noop_when_idle(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    service.attach_runner(_FakeRunner(ExperimentStatus(state=ExperimentState.idle)))
    calls = _mock_post_with_message_ids(monkeypatch, service)

    await service._progress_tick()

    assert calls == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_edit_message_treats_not_modified_400_as_success(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)

    async def fake_post(url, json=None, **kw):
        return _FakeResponseWithBody(
            {"ok": False, "description": "Bad Request: message is not modified"},
            status_code=400,
            text="Bad Request: message is not modified",
        )

    monkeypatch.setattr(service._client, "post", fake_post)
    ok = await service._edit_message(42, 555, "same text")

    assert ok is True
    await service.shutdown()


@pytest.mark.asyncio
async def test_edit_message_reports_a_real_failure_as_false(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)

    async def fake_post(url, json=None, **kw):
        return _FakeResponseWithBody({"ok": False}, status_code=403, text="Forbidden: bot was blocked by the user")

    monkeypatch.setattr(service._client, "post", fake_post)
    ok = await service._edit_message(42, 555, "new text")

    assert ok is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_notify_completion_unpins_and_forgets_the_progress_message(chat_config: AppConfig, monkeypatch):
    service = await _linked_service(chat_config, monkeypatch, "ivan", 42)
    storage = service._storage
    exp = storage.create_experiment("ivan", "run1")
    service._monitors[exp.experiment_id] = {42}
    service._progress_messages[(exp.experiment_id, 42)] = 555
    service._progress_last_edit[(exp.experiment_id, 42)] = 123.0

    calls = _mock_post_with_message_ids(monkeypatch, service)
    await service.notify_completion(exp, ExperimentStatus(state=ExperimentState.done, message="done"))

    unpin_calls = [c for c in calls if c[0].endswith("/unpinChatMessage")]
    assert len(unpin_calls) == 1
    assert unpin_calls[0][1]["message_id"] == 555
    assert (exp.experiment_id, 42) not in service._progress_messages
    assert (exp.experiment_id, 42) not in service._progress_last_edit
    await service.shutdown()


# --- _build_progress_text: format correctness, no HTTP involved ------------


def test_build_progress_text_percentage_bar_and_duration_format(chat_config: AppConfig):
    from PIL import Image

    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService(
        "token", "MyBot", chat_config.telegram_links_path, storage, chat_config.telegram_links_path.parent / "monitors.json"
    )
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": "2026-01-01T00:00:00"})
    Image.new("RGB", (4, 4), color="white").save(exp.path / "dark_00000.png", "PNG")

    status = ExperimentStatus(
        state=ExperimentState.running,
        experimentId=exp.experiment_id,
        username="ivan",
        elapsedSeconds=3600 * 14 + 60 * 20,  # 14h 20m
        totalSeconds=3600 * 23,  # 23h total -> 8h40m left
        imagesCaptured=142,
        imagesPlanned=230,
    )
    text = service._build_progress_text(status, exp)

    assert exp.experiment_id in text
    assert "62%" in text
    assert "14h 20m elapsed" in text
    assert "8h 40m left" in text
    assert "142/230 images" in text
    assert "no anomalies detected" in text


def test_build_progress_text_flags_a_detected_issue(chat_config: AppConfig):
    storage = Storage(chat_config.storage_root)
    service = TelegramLinkService(
        "token", "MyBot", chat_config.telegram_links_path, storage, chat_config.telegram_links_path.parent / "monitors.json"
    )
    exp = storage.create_experiment("ivan", "run1")
    status = ExperimentStatus(
        state=ExperimentState.running, experimentId=exp.experiment_id, username="ivan", issueDetected=True
    )
    text = service._build_progress_text(status, exp)
    assert "possible issue detected" in text
