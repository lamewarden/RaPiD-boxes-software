"""Tests for the QA chat assistant.

Covers the app-level safety gates (idle-only, interrupt-and-archive-on-start,
graceful failure when the remote assistant API is unreachable) and the
deterministic tool resolution -- the property that matters most is that
resolved proposals always come from a real stored experiment, never from
values the model invented. The model call itself is mocked via
monkeypatching AssistantService._call_llm; these tests are about the
service's own contract, not the remote gateway's.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from rapidboxes import config_xml
from rapidboxes.assistant.cli import _build_start_payload
from rapidboxes.assistant.service import AssistantService
from rapidboxes.config import AppConfig
from rapidboxes.engine.runner import ExperimentRunner
from rapidboxes.hardware.manager import build_hardware
from rapidboxes.main import create_app
from rapidboxes.models import CameraSettings, DeviceSettings, SavedExperimentConfig, TropismConfig
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
        # Deliberately unroutable so any call that isn't mocked below fails
        # fast and deterministically, instead of depending on whether the
        # real gateway happens to be reachable from the machine running the
        # suite.
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


def _mock_llm(
    monkeypatch,
    content: str = "",
    tool_name: Optional[str] = None,
    tool_args: Optional[dict] = None,
    unreachable: bool = False,
) -> None:
    async def fake_call(self, messages):  # noqa: ANN001 - matches _call_llm's signature
        if unreachable:
            raise httpx.ConnectError("connection refused")
        message = {"role": "assistant", "content": content}
        if tool_name:
            message["tool_calls"] = [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(tool_args or {})},
                }
            ]
        return message

    monkeypatch.setattr(AssistantService, "_call_llm", fake_call)


def _tool_call(name: str, **args) -> dict:
    """Builds a tool_call dict matching the shape _resolve_tool_call expects,
    for tests that exercise resolution directly without going through a
    mocked LLM response."""
    return {"function": {"name": name, "arguments": json.dumps(args)}}


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
    _mock_llm(monkeypatch, content="hello there")
    res = await client.post("/api/assistant/chat", json={"message": "hi", "history": []})
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "hello there"
    assert body["proposal"] is None


@pytest.mark.asyncio
async def test_chat_503_when_llm_unreachable(client: AsyncClient, monkeypatch):
    _mock_llm(monkeypatch, unreachable=True)
    res = await client.post("/api/assistant/chat", json={"message": "hi", "history": []})
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_chat_409_while_experiment_running(client: AsyncClient, monkeypatch):
    _mock_llm(monkeypatch, content="hello there")
    res = await client.post("/api/experiments", json=_tropism_config().model_dump())
    assert res.status_code == 200

    res = await client.post("/api/assistant/chat", json={"message": "hi", "history": []})
    assert res.status_code == 409

    await client.post("/api/experiments/current/abort")


@pytest.mark.asyncio
async def test_chat_resolves_a_real_tool_call(client: AsyncClient, monkeypatch):
    """End to end through the API: a mocked tool_calls response gets
    resolved to real data, not echoed back as raw text."""
    _mock_llm(monkeypatch, tool_name="system_status")
    res = await client.post("/api/assistant/chat", json={"message": "status?", "history": []})
    assert res.status_code == 200
    assert "No experiment is currently running" in res.json()["reply"]


# --- interrupt-and-archive on experiment start ----------------------------


@pytest.mark.asyncio
async def test_start_experiment_archives_inflight_chat(
    client: AsyncClient, monkeypatch, app_config: AppConfig
):
    _mock_llm(monkeypatch, content="hello there")
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


# --- prefill_experiment tool resolution ------------------------------------


@pytest.mark.asyncio
async def test_prefill_resolves_real_past_experiment(app_config: AppConfig):
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
        call = _tool_call("prefill_experiment", username="ivan", reference="yesterday")
        proposal, _summary = service._resolve_tool_call(call, requesting_username=None)
        assert proposal is not None
        assert proposal.experimentId == exp.experiment_id
        assert proposal.sourceUsername == "ivan"
        # The values must come straight from the stored config, never from
        # anything the model itself might have put in the tool call.
        assert proposal.config.darkPhaseHours == 12.5
        assert proposal.config.lateralIlluminationHours == 3.0
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_prefill_no_match_returns_no_proposal(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    try:
        call = _tool_call("prefill_experiment", username="ghost", reference="yesterday")
        proposal, reply = service._resolve_tool_call(call, requesting_username=None)
        assert proposal is None
        assert "couldn't find" in reply
    finally:
        await service.aclose()


def test_unknown_tool_name_degrades_gracefully(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("delete_everything")
    proposal, reply = service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "went wrong" in reply


def test_malformed_tool_arguments_degrade_gracefully(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    hw = build_hardware(app_config, DeviceSettings())
    runner = ExperimentRunner(hw, storage)
    service = AssistantService(app_config, storage, runner)
    call = {"function": {"name": "system_status", "arguments": "not valid json"}}
    proposal, reply = service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "No experiment is currently running" in reply  # empty args -> still resolves


# --- list_experiments tool resolution ---------------------------------------


def test_list_experiments_all_users_shows_everyone(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    for username, name in [("ivan", "run1"), ("sabol", "run2"), ("ivan", "run3")]:
        exp = storage.create_experiment(username, name)
        exp.write_metadata({"username": username, "config": {"protocol": "tropism"}})

    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments")
    proposal, reply = service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "all users" in reply
    assert reply.count(" — ivan — ") == 2
    assert reply.count(" — sabol — ") == 1


def test_list_experiments_filters_by_named_user(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    for username, name in [("ivan", "run1"), ("sabol", "run2")]:
        exp = storage.create_experiment(username, name)
        exp.write_metadata({"username": username, "config": {"protocol": "growth"}})

    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments", username="sabol")
    proposal, reply = service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "for sabol" in reply
    assert " — sabol — " in reply
    assert "ivan" not in reply.split("\n", 1)[1]  # ivan's own run excluded


def test_list_experiments_respects_limit(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    for i in range(3):
        exp = storage.create_experiment("ivan", f"run{i}")
        exp.write_metadata({"username": "ivan", "config": {"protocol": "tropism"}})

    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments", limit=2)
    _proposal, reply = service._resolve_tool_call(call, requesting_username=None)
    assert "Last 2 experiment(s)" in reply


def test_list_experiments_no_match(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments", username="ghost")
    proposal, reply = service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "No experiments found" in reply


# --- system_status tool resolution -------------------------------------------


def test_system_status_reports_idle_storage_and_camera(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    hw = build_hardware(app_config, DeviceSettings())
    runner = ExperimentRunner(hw, storage)
    service = AssistantService(app_config, storage, runner)

    call = _tool_call("system_status")
    proposal, reply = service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "No experiment is currently running" in reply
    assert "Storage:" in reply
    assert "Camera:" in reply


def test_system_status_without_runner_degrades_gracefully(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)  # no runner passed
    call = _tool_call("system_status")
    _proposal, reply = service._resolve_tool_call(call, requesting_username=None)
    assert "isn't available" in reply


# --- my_settings / my_storage: strictly scoped to the requester ------------


def test_my_settings_reports_persisted_settings_and_mine_baseline(app_config: AppConfig):
    from rapidboxes import settings_store, user_defaults
    from rapidboxes.models import CameraSettings, DeviceSettings

    settings_store.save_device_settings(
        app_config.settings_path,
        DeviceSettings(photoIlluminationSource="rgbw"),
    )
    user_defaults.save_for(
        app_config.user_defaults_path,
        "ivan",
        DeviceSettings(camera=CameraSettings(zoom=2.5), photoIlluminationSource="ir"),
    )

    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_settings")
    proposal, reply = service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "RGBW" in reply  # current persisted setting
    assert "Your saved 'Mine' baseline (ivan)" in reply
    assert "IR" in reply  # ivan's own saved baseline
    assert "2.5x" in reply


def test_my_settings_no_mine_baseline_says_so(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_settings")
    proposal, reply = service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "No personal 'Mine' baseline saved yet" in reply


def test_my_settings_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_settings")
    _proposal, reply = service._resolve_tool_call(call, requesting_username=None)
    assert "don't know who's chatting" in reply


def test_my_settings_ignores_username_argument_from_model(app_config: AppConfig):
    """my_settings' schema takes no arguments -- even if the model tries to
    slip one in, the resolver must still use requesting_username, never a
    model-supplied one. This is the strict-scoping guarantee."""
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = {"function": {"name": "my_settings", "arguments": json.dumps({"username": "someone-else"})}}
    _proposal, reply = service._resolve_tool_call(call, requesting_username="ivan")
    assert "ivan" in reply.lower() or "No personal 'Mine' baseline saved yet for ivan" in reply


def test_my_storage_reports_own_usage_only(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    for username, name in [("ivan", "run1"), ("sabol", "run2")]:
        exp = storage.create_experiment(username, name)
        exp.write_metadata({"username": username})

    service = AssistantService(app_config, storage)
    call = _tool_call("my_storage")
    proposal, reply = service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "ivan has 1 experiment(s)" in reply
    assert "sabol" not in reply
    assert "Device free space:" in reply


def test_my_storage_no_experiments_yet(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_storage")
    proposal, reply = service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "no stored experiments yet" in reply


def test_my_storage_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_storage")
    _proposal, reply = service._resolve_tool_call(call, requesting_username=None)
    assert "don't know who's chatting" in reply


# --- read_experiment_log: strictly scoped to the requester's own runs ------


def test_read_experiment_log_returns_own_events(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    exp.append_event("started protocol=tropism username=ivan")
    exp.append_event("phase dark started (index 0)")

    service = AssistantService(app_config, storage)
    call = _tool_call("read_experiment_log")
    proposal, reply = service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert exp.experiment_id in reply
    assert "started protocol=tropism username=ivan" in reply
    assert "phase dark started" in reply


def test_read_experiment_log_never_reads_another_users_run(app_config: AppConfig):
    """read_experiment_log's schema takes no username -- even if sabol has a
    run and ivan asks, only ivan's own experiments are ever candidates."""
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("sabol", "sabol-run")
    exp.write_metadata({"username": "sabol", "startedAt": datetime.now().isoformat()})
    exp.append_event("started protocol=tropism username=sabol")

    service = AssistantService(app_config, storage)
    call = _tool_call("read_experiment_log")
    proposal, reply = service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "couldn't find" in reply


def test_read_experiment_log_no_events_says_so(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    # No append_event calls -- events.log never created.

    service = AssistantService(app_config, storage)
    call = _tool_call("read_experiment_log")
    proposal, reply = service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "no logged events" in reply


def test_read_experiment_log_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("read_experiment_log")
    _proposal, reply = service._resolve_tool_call(call, requesting_username=None)
    assert "don't know who's chatting" in reply


# --- CLI: SavedExperimentConfig -> start-experiment payload mapping -------


def test_build_start_payload_tropism():
    config = SavedExperimentConfig(
        protocol="tropism",
        darkPhaseEnabled=True,
        darkPhaseHours=12.5,
        lateralIlluminationHours=3.0,
        spectra=["white", "red"],
        intervalMinutes=15.0,
        intensity=40,
        photoIlluminationSource="rgbw",
        camera=CameraSettings(grayscale=False, zoom=2.0),
    ).model_dump()

    payload = _build_start_payload(config, username="ivan", experiment_name="replay-1")

    assert payload == {
        "protocol": "tropism",
        "experimentName": "replay-1",
        "username": "ivan",
        "darkPhaseEnabled": True,
        "darkPhaseHours": 12.5,
        "lateralIlluminationHours": 3.0,
        "spectra": ["white", "red"],
        "intervalMinutes": 15.0,
        "intensity": 40,
    }


def test_build_start_payload_growth():
    config = SavedExperimentConfig(
        protocol="growth",
        dayLengthHours=16,
        experimentLengthDays=10,
        spectra=["white"],
        dayIntensity=30,
        intervalMinutes=20.0,
        photoIlluminationSource="rgbw",
        camera=CameraSettings(grayscale=False, zoom=1.0),
    ).model_dump()

    payload = _build_start_payload(config, username="ivan", experiment_name="replay-2")

    assert payload == {
        "protocol": "growth",
        "experimentName": "replay-2",
        "username": "ivan",
        "dayLengthHours": 16,
        "experimentLengthDays": 10,
        "spectra": ["white"],
        "dayIntensity": 30,
        "intervalMinutes": 20.0,
    }
