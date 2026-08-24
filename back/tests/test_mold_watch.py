"""Tests for the opt-in mid-run mold watcher (assistant/mold_watch.py). The
vision call is mocked via monkeypatching vision.call_llm, same pattern as
test_assistant.py's check_my_images tests and test_summary.py -- these are
about the opt-in gating, the >=3-frame confirmation threshold, and the
one-shot flagging, not the gateway's own behavior."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from rapidboxes.assistant import mold_watch, vision
from rapidboxes.assistant.mold_watch import CHECK_EVERY_N_IMAGES, MoldWatchService
from rapidboxes.config import AppConfig
from rapidboxes.storage import Storage


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
        spa_dir=None,
    )


async def _feed_images(service: MoldWatchService, exp, *, count: int, report_enabled: bool, email):
    for i in range(count):
        path = exp.path / f"dark_{i:05d}.png"
        _write_fake_png(path)
        service.enqueue_image(path, exp.experiment_id, "ivan", report_enabled, email)
    # Let the queued job (if any) actually run.
    await service._queue.join()


@pytest.mark.asyncio
async def test_not_opted_in_never_checks(app_config, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    called = {"n": 0}

    async def fake_call_llm(*a, **kw):
        called["n"] += 1
        return "SUMMARY: fine"

    monkeypatch.setattr(vision, "call_llm", fake_call_llm)

    service = MoldWatchService(app_config, storage)
    runner = _FakeRunner()
    service.attach_runner(runner)
    service.start()

    await _feed_images(service, exp, count=CHECK_EVERY_N_IMAGES * 2, report_enabled=False, email=None)

    assert called["n"] == 0
    assert runner.calls == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_below_threshold_does_not_flag(app_config, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    _mock_vision(monkeypatch, raw="frame 0: MOLD\nframe 1: CLEAN\nSUMMARY: one odd frame.")

    service = MoldWatchService(app_config, storage)
    runner = _FakeRunner()
    service.attach_runner(runner)
    service.start()

    await _feed_images(
        service, exp, count=CHECK_EVERY_N_IMAGES, report_enabled=True, email="researcher@example.com"
    )

    assert runner.calls == []
    assert "issue detected" not in exp.read_events()
    await service.shutdown()


@pytest.mark.asyncio
async def test_at_threshold_flags_once_and_stops_rechecking(app_config, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    _mock_vision(
        monkeypatch,
        raw=(
            "frame 0: MOLD\nframe 1: MOLD\nframe 2: MOLD\nframe 3: CLEAN\nframe 4: CLEAN\n"
            "SUMMARY: Visible mold growth."
        ),
    )

    service = MoldWatchService(app_config, storage)
    runner = _FakeRunner()
    service.attach_runner(runner)
    service.start()

    await _feed_images(
        service, exp, count=CHECK_EVERY_N_IMAGES, report_enabled=True, email="researcher@example.com"
    )

    assert len(runner.calls) == 1
    experiment_id, detail = runner.calls[0]
    assert experiment_id == exp.experiment_id
    assert "3/5" in detail
    assert "issue detected" in exp.read_events()

    # Feed another full window of new captures -- already flagged, must not
    # re-check or fire a second alert.
    await _feed_images(
        service, exp, count=CHECK_EVERY_N_IMAGES, report_enabled=True, email="researcher@example.com"
    )
    assert len(runner.calls) == 1

    await service.shutdown()


def test_send_email_stub_does_not_raise():
    mold_watch.send_email("someone@example.com", "subject", "body")
