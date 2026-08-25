"""Tests for the opt-in mid-run mold watcher (assistant/mold_watch.py). The
vision call is mocked via monkeypatching vision.call_llm, same pattern as
test_assistant.py's check_my_images tests and test_summary.py -- these are
about the opt-in gating, the >=3-frame confirmation threshold, and the
one-shot flagging, not the gateway's own behavior. Telegram delivery is
verified by monkeypatching TelegramLinkService.send_message directly --
see test_telegram_link.py for that service's own behavior."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from rapidboxes.assistant import vision
from rapidboxes.assistant.mold_watch import CHECK_EVERY_N_IMAGES, MoldWatchService
from rapidboxes.config import AppConfig
from rapidboxes.storage import Storage
from rapidboxes.telegram_link import TelegramLinkService


def _write_fake_png(path: Path) -> None:
    Image.new("RGB", (32, 32), color="white").save(path, "PNG")


def _mock_vision(monkeypatch, raw: str) -> None:
    async def fake_call_llm(config, model, prompt, image_paths=None):
        return raw

    monkeypatch.setattr(vision, "call_llm", fake_call_llm)


class _FakeRunner:
    def __init__(self):
        self.calls = []

    async def mark_issue_detected(self, experiment_id: str, detail: str) -> None:
        self.calls.append((experiment_id, detail))


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        simulation=True,
        storage_root=tmp_path / "experiments",
        settings_path=tmp_path / "settings.json",
        assistant_api_base_url="http://127.0.0.1:1",
        assistant_api_key="test-key",
        telegram_links_path=tmp_path / "telegram_links.json",
        spa_dir=None,
    )


@pytest.fixture
async def telegram(app_config: AppConfig, monkeypatch):
    """Not `configured` (no token) so no real background polling runs --
    send_message is replaced with a recording stub so mold_watch's own
    behavior (does it call send, with what) can be asserted."""
    service = TelegramLinkService(None, None, app_config.telegram_links_path)
    sent = []

    async def fake_send(username, text):
        sent.append((username, text))
        return True

    monkeypatch.setattr(service, "send_message", fake_send)
    service.sent = sent
    yield service
    await service.shutdown()


async def _feed_images(service: MoldWatchService, exp, *, count: int, report_enabled: bool):
    for i in range(count):
        path = exp.path / f"dark_{i:05d}.png"
        _write_fake_png(path)
        service.enqueue_image(path, exp.experiment_id, "ivan", report_enabled)
    # Let the queued job (if any) actually run.
    await service._queue.join()


@pytest.mark.asyncio
async def test_not_opted_in_never_checks(app_config, telegram, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    called = {"n": 0}

    async def fake_call_llm(*a, **kw):
        called["n"] += 1
        return "SUMMARY: fine"

    monkeypatch.setattr(vision, "call_llm", fake_call_llm)

    service = MoldWatchService(app_config, storage, telegram)
    runner = _FakeRunner()
    service.attach_runner(runner)
    service.start()

    await _feed_images(service, exp, count=CHECK_EVERY_N_IMAGES * 2, report_enabled=False)

    assert called["n"] == 0
    assert runner.calls == []
    assert telegram.sent == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_below_threshold_does_not_flag(app_config, telegram, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    _mock_vision(monkeypatch, raw="frame 0: MOLD\nframe 1: CLEAN\nSUMMARY: one odd frame.")

    service = MoldWatchService(app_config, storage, telegram)
    runner = _FakeRunner()
    service.attach_runner(runner)
    service.start()

    await _feed_images(service, exp, count=CHECK_EVERY_N_IMAGES, report_enabled=True)

    assert runner.calls == []
    assert telegram.sent == []
    assert "issue detected" not in exp.read_events()
    await service.shutdown()


@pytest.mark.asyncio
async def test_at_threshold_flags_once_and_stops_rechecking(app_config, telegram, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    _mock_vision(
        monkeypatch,
        raw=(
            "frame 0: MOLD\nframe 1: MOLD\nframe 2: MOLD\nframe 3: CLEAN\nframe 4: CLEAN\n"
            "SUMMARY: Visible mold growth."
        ),
    )

    service = MoldWatchService(app_config, storage, telegram)
    runner = _FakeRunner()
    service.attach_runner(runner)
    service.start()

    await _feed_images(service, exp, count=CHECK_EVERY_N_IMAGES, report_enabled=True)

    assert len(runner.calls) == 1
    experiment_id, detail = runner.calls[0]
    assert experiment_id == exp.experiment_id
    assert "3/5" in detail
    assert "issue detected" in exp.read_events()
    assert len(telegram.sent) == 1
    assert telegram.sent[0][0] == "ivan"
    assert exp.experiment_id in telegram.sent[0][1]

    # Feed another full window of new captures -- already flagged, must not
    # re-check or fire a second alert.
    await _feed_images(service, exp, count=CHECK_EVERY_N_IMAGES, report_enabled=True)
    assert len(runner.calls) == 1
    assert len(telegram.sent) == 1

    await service.shutdown()
