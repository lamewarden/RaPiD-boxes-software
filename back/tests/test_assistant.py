"""Tests for the QA chat assistant.

Covers the app-level safety gates (idle-only, interrupt-and-archive-on-start,
graceful failure when Ollama is unreachable) and the deterministic
prefill_experiment action resolution -- the property that matters most is
that resolved proposals always come from a real stored experiment, never
from values the model invented. The model call itself is mocked via
monkeypatching AssistantService._call_ollama; these tests are about the
service's own contract, not Ollama's.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from rapidboxes import config_xml
from rapidboxes.assistant.service import AssistantService
from rapidboxes.config import AppConfig
from rapidboxes.main import create_app
from rapidboxes.models import CameraSettings, SavedExperimentConfig, TropismConfig
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
        # Deliberately unroutable so interrupt_and_archive()'s unload call
        # (never mocked below) fails fast and deterministically in tests,
        # instead of depending on whether a real Ollama happens to be
        # running on the machine running the suite.
        assistant_ollama_url="http://127.0.0.1:1",
        spa_dir=None,
    )


@pytest.fixture
async def client(app_config: AppConfig):
    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _mock_ollama(monkeypatch, reply: str = "", unreachable: bool = False) -> None:
    async def fake_call(self, messages):  # noqa: ANN001 - matches _call_ollama's signature
        if unreachable:
            raise httpx.ConnectError("connection refused")
        return reply

    monkeypatch.setattr(AssistantService, "_call_ollama", fake_call)


def _tropism_config(**overrides) -> TropismConfig:
    return TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseHours=0.02,
        lateralIlluminationHours=0,
        intervalMinutes=1,
        **overrides,
    )


# --- chat gating ---------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_ok_while_idle(client: AsyncClient, monkeypatch):
    _mock_ollama(monkeypatch, reply="hello there")
    res = await client.post("/api/assistant/chat", json={"message": "hi", "history": []})
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "hello there"
    assert body["proposal"] is None


@pytest.mark.asyncio
async def test_chat_503_when_ollama_unreachable(client: AsyncClient, monkeypatch):
    _mock_ollama(monkeypatch, unreachable=True)
    res = await client.post("/api/assistant/chat", json={"message": "hi", "history": []})
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_chat_409_while_experiment_running(client: AsyncClient, monkeypatch):
    _mock_ollama(monkeypatch, reply="hello there")
    res = await client.post("/api/experiments", json=_tropism_config().model_dump())
    assert res.status_code == 200

    res = await client.post("/api/assistant/chat", json={"message": "hi", "history": []})
    assert res.status_code == 409

    await client.post("/api/experiments/current/abort")


# --- interrupt-and-archive on experiment start ----------------------------


@pytest.mark.asyncio
async def test_start_experiment_archives_inflight_chat(
    client: AsyncClient, monkeypatch, app_config: AppConfig
):
    _mock_ollama(monkeypatch, reply="hello there")
    res = await client.post("/api/assistant/chat", json={"message": "hi", "history": []})
    assert res.status_code == 200

    res = await client.post("/api/experiments", json=_tropism_config().model_dump())
    assert res.status_code == 200

    archives = list(app_config.assistant_archive_dir.glob("*.json"))
    assert len(archives) == 1
    data = json.loads(archives[0].read_text())
    assert data["reason"] == "experiment_started"
    assert [m["content"] for m in data["messages"]] == ["hi", "hello there"]

    await client.post("/api/experiments/current/abort")


# --- prefill_experiment action resolution ---------------------------------


@pytest.mark.asyncio
async def test_prefill_action_resolves_real_past_experiment(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "yesterday-run")
    yesterday = datetime.now() - timedelta(days=1)
    exp.write_metadata({"username": "ivan", "startedAt": yesterday.isoformat()})
    saved = SavedExperimentConfig(
        protocol="tropism",
        darkPhaseHours=12.5,
        lateralIlluminationHours=3.0,
        intervalMinutes=15.0,
        photoIlluminationSource="rgbw",
        camera=CameraSettings(grayscale=False, zoom=2.0),
    )
    exp.write_config_xml(config_xml.serialize(saved), "yesterday-run")

    service = AssistantService(app_config, storage)
    try:
        raw = json.dumps(
            {"action": "prefill_experiment", "username": "ivan", "reference": "yesterday"}
        )
        result = service._try_resolve_action(raw, requesting_username=None)
        assert result is not None
        proposal, _summary = result
        assert proposal is not None
        assert proposal.experimentId == exp.experiment_id
        assert proposal.sourceUsername == "ivan"
        # The values must come straight from the stored config, never from
        # anything the model itself might have put in the JSON.
        assert proposal.config.darkPhaseHours == 12.5
        assert proposal.config.lateralIlluminationHours == 3.0
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_prefill_action_no_match_returns_no_proposal(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    try:
        raw = json.dumps(
            {"action": "prefill_experiment", "username": "ghost", "reference": "yesterday"}
        )
        result = service._try_resolve_action(raw, requesting_username=None)
        assert result is not None
        proposal, reply = result
        assert proposal is None
        assert "couldn't find" in reply
    finally:
        await service.aclose()


def test_ordinary_reply_is_not_treated_as_action(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    assert service._try_resolve_action("just a normal conversational reply", None) is None
