"""Tests for the end-of-experiment AI summary (assistant/summary.py) and its
GET /api/experiments/{id}/summary endpoint. The LLM call itself is mocked
via monkeypatching vision.call_llm -- these tests are about the storage
round-trip and API wiring, not the gateway's behavior (covered separately
in test_assistant.py's check_my_images tests, which share the same
vision.check_frames_for_anomalies path)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from rapidboxes.assistant import summary, vision
from rapidboxes.config import AppConfig
from rapidboxes.main import create_app
from rapidboxes.models import ExperimentState, ExperimentStatus, TropismConfig
from rapidboxes.storage import Storage


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        simulation=True,
        storage_root=tmp_path / "experiments",
        settings_path=tmp_path / "settings.json",
        remote_sync_path=tmp_path / "remote_sync.json",
        user_defaults_path=tmp_path / "user_defaults.json",
        assistant_archive_dir=tmp_path / "assistant_archive",
        assistant_api_base_url="http://127.0.0.1:1",
        assistant_api_key="test-key",
        spa_dir=None,
    )


@pytest.fixture
async def client(app_config: AppConfig):
    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _mock_vision(monkeypatch, raw: str = "SUMMARY: all good.") -> None:
    async def fake_call_llm(config, model, prompt, image_paths=None):
        return raw

    monkeypatch.setattr(vision, "call_llm", fake_call_llm)


@pytest.mark.asyncio
async def test_generate_and_store_writes_and_reads_back(app_config: AppConfig, monkeypatch):
    _mock_vision(monkeypatch)
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.append_event("started protocol=tropism username=ivan")

    status = ExperimentStatus(
        state=ExperimentState.done,
        experimentId=exp.experiment_id,
        message="completed",
        imagesCaptured=5,
        imagesPlanned=5,
        config=TropismConfig(experimentName="run1", username="ivan"),
    )

    await summary.generate_and_store(app_config, exp, status)

    stored = summary.read_stored(exp)
    assert stored is not None
    assert stored["ranSmoothly"] is True
    assert "all good" in stored["textSummary"]
    assert stored["moldDetected"] is False  # no images -> no anomaly check run


@pytest.mark.asyncio
async def test_generate_and_store_never_raises_on_llm_failure(app_config: AppConfig, monkeypatch):
    async def fake_call_llm(config, model, prompt, image_paths=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(vision, "call_llm", fake_call_llm)
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    status = ExperimentStatus(state=ExperimentState.done, experimentId=exp.experiment_id, message="completed")

    await summary.generate_and_store(app_config, exp, status)  # must not raise

    stored = summary.read_stored(exp)
    assert stored is not None
    assert "unavailable" in stored["textSummary"]


def test_read_stored_returns_none_when_missing(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    assert summary.read_stored(exp) is None


@pytest.mark.asyncio
async def test_summary_endpoint_404_before_generated(client: AsyncClient, app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    storage.create_experiment("ivan", "run1")
    exp_id = storage.list_experiments()[0].name

    res = await client.get(f"/api/experiments/{exp_id}/summary")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_summary_endpoint_returns_stored_summary(
    client: AsyncClient, app_config: AppConfig, monkeypatch
):
    _mock_vision(monkeypatch)
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    status = ExperimentStatus(state=ExperimentState.done, experimentId=exp.experiment_id, message="completed")
    await summary.generate_and_store(app_config, exp, status)

    res = await client.get(f"/api/experiments/{exp.experiment_id}/summary")
    assert res.status_code == 200
    assert res.json()["ranSmoothly"] is True


@pytest.mark.asyncio
async def test_summary_endpoint_404_for_unknown_experiment(client: AsyncClient):
    res = await client.get("/api/experiments/does-not-exist/summary")
    assert res.status_code == 404
