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
from rapidboxes.models import CameraSettings, ExperimentState, ExperimentStatus, SavedExperimentConfig
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
