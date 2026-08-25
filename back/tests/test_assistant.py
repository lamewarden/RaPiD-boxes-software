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
from rapidboxes.assistant import vision
from rapidboxes.assistant.cli import _build_start_payload
from rapidboxes.assistant.service import AssistantService
from rapidboxes.config import AppConfig
from rapidboxes.engine.runner import ExperimentRunner
from rapidboxes.hardware.manager import build_hardware
from rapidboxes.main import create_app
from rapidboxes.models import (
    CameraSettings,
    DeviceSettings,
    ExperimentPhase,
    ExperimentState,
    RecoveryNotice,
    RemoteSyncSettings,
    SavedExperimentConfig,
    TropismConfig,
)
from rapidboxes.remote_sync import RemoteSyncService
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
async def test_chat_still_works_while_experiment_running(client: AsyncClient, monkeypatch):
    """Chat runs against a remote API, not a local model -- no RAM/CPU
    contention to guard against, so (unlike an earlier local-model version)
    it stays available throughout a run, not just while idle."""
    _mock_llm(monkeypatch, content="hello there")
    res = await client.post("/api/experiments", json=_tropism_config().model_dump())
    assert res.status_code == 200

    res = await client.post("/api/assistant/chat", json={"message": "hi", "history": []})
    assert res.status_code == 200
    assert res.json()["reply"] == "hello there"

    await client.post("/api/experiments/current/abort")


@pytest.mark.asyncio
async def test_chat_resolves_a_real_tool_call(client: AsyncClient, monkeypatch):
    """End to end through the API: a mocked tool_calls response gets
    resolved to real data, not echoed back as raw text."""
    _mock_llm(monkeypatch, tool_name="system_status")
    res = await client.post("/api/assistant/chat", json={"message": "status?", "history": []})
    assert res.status_code == 200
    assert "No experiment is currently running" in res.json()["reply"]


@pytest.mark.asyncio
async def test_starting_an_experiment_does_not_interrupt_chat(
    client: AsyncClient, monkeypatch, app_config: AppConfig
):
    """Starting an experiment used to force-cancel and archive any in-flight
    chat (see interrupt_and_archive's docstring for why that no longer
    applies) -- confirms it's no longer wired up automatically."""
    _mock_llm(monkeypatch, content="hello there")
    res = await client.post("/api/assistant/chat", json={"message": "hi", "history": []})
    assert res.status_code == 200

    res = await client.post("/api/experiments", json=_tropism_config().model_dump())
    assert res.status_code == 200

    assert list(app_config.assistant_archive_dir.glob("*.json")) == []

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
        proposal, _image, _download, _summary = await service._resolve_tool_call(call, requesting_username=None)
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
        proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
        assert proposal is None
        assert "couldn't find" in reply
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_unknown_tool_name_degrades_gracefully(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("delete_everything")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "went wrong" in reply


@pytest.mark.asyncio
async def test_malformed_tool_arguments_degrade_gracefully(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    hw = build_hardware(app_config, DeviceSettings())
    runner = ExperimentRunner(hw, storage)
    service = AssistantService(app_config, storage, runner)
    call = {"function": {"name": "system_status", "arguments": "not valid json"}}
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "No experiment is currently running" in reply  # empty args -> still resolves


# --- list_experiments tool resolution ---------------------------------------


@pytest.mark.asyncio
async def test_list_experiments_no_username_defaults_to_requester(app_config: AppConfig):
    """The real bug this guards against: "which experiment did I conduct"
    used to default to showing every user's most recent experiment, not the
    asker's own -- confusing on a shared device. Omitting `username` now
    means "me", matching my_settings/my_storage/read_experiment_log."""
    storage = Storage(app_config.storage_root)
    for username, name in [("ivan", "run1"), ("sabol", "run2")]:
        exp = storage.create_experiment(username, name)
        exp.write_metadata({"username": username, "config": {"protocol": "tropism"}})

    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "for ivan" in reply
    assert " — ivan — " in reply
    assert "sabol" not in reply


@pytest.mark.asyncio
async def test_list_experiments_explicit_all_shows_everyone(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    for username, name in [("ivan", "run1"), ("sabol", "run2"), ("ivan", "run3")]:
        exp = storage.create_experiment(username, name)
        exp.write_metadata({"username": username, "config": {"protocol": "tropism"}})

    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments", username="all")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "all users" in reply
    assert reply.count(" — ivan — ") == 2
    assert reply.count(" — sabol — ") == 1


@pytest.mark.asyncio
async def test_list_experiments_no_username_no_requester_shows_everyone(app_config: AppConfig):
    """Degrade path: if we somehow don't know who's chatting either (should
    be rare now that the username is always injected into context), fall
    back to the old shared-visibility behavior rather than showing nothing."""
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "config": {"protocol": "tropism"}})

    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "all users" in reply


@pytest.mark.asyncio
async def test_list_experiments_filters_by_named_user(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    for username, name in [("ivan", "run1"), ("sabol", "run2")]:
        exp = storage.create_experiment(username, name)
        exp.write_metadata({"username": username, "config": {"protocol": "growth"}})

    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments", username="sabol")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "for sabol" in reply
    assert " — sabol — " in reply
    assert "ivan" not in reply.split("\n", 1)[1]  # ivan's own run excluded


@pytest.mark.asyncio
async def test_list_experiments_respects_limit(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    for i in range(3):
        exp = storage.create_experiment("ivan", f"run{i}")
        exp.write_metadata({"username": "ivan", "config": {"protocol": "tropism"}})

    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments", limit=2)
    _proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert "Last 2 experiment(s)" in reply


@pytest.mark.asyncio
async def test_list_experiments_no_match(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments", username="ghost")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "No experiments found" in reply


# --- system_status tool resolution -------------------------------------------


@pytest.mark.asyncio
async def test_system_status_reports_idle_storage_and_camera(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    hw = build_hardware(app_config, DeviceSettings())
    runner = ExperimentRunner(hw, storage)
    service = AssistantService(app_config, storage, runner)

    call = _tool_call("system_status")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "No experiment is currently running" in reply
    assert "Device storage:" in reply
    assert "Camera:" in reply


@pytest.mark.asyncio
async def test_system_status_reports_a_recovered_interruption(app_config: AppConfig):
    """A running experiment that survived a crash/power-loss recovery must
    have that surfaced here -- this is exactly what "was my experiment
    interrupted?" resolves to, and the answer needs the real outage length
    and skip count, not just silence."""
    storage = Storage(app_config.storage_root)
    hw = build_hardware(app_config, DeviceSettings())
    runner = ExperimentRunner(hw, storage)
    runner.status.state = ExperimentState.running
    runner.status.phase = ExperimentPhase.dark
    runner.status.experimentId = "2026-01-01_ivan_test"
    runner.status.username = "ivan"
    runner.status.imagesCaptured = 5
    runner.status.imagesPlanned = 100
    runner.status.recoveryNotice = RecoveryNotice(
        message="Resumed after ~30 min offline (power loss or reboot) -- 3 images could not be captured.",
        offlineSeconds=1800.0,
        imagesSkipped=3,
    )
    service = AssistantService(app_config, storage, runner)

    call = _tool_call("system_status")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "30 min offline" in reply
    assert "3 images could not be captured" in reply


@pytest.mark.asyncio
async def test_system_status_distinguishes_real_usage_from_the_preflight_estimate(app_config: AppConfig):
    """The user reported the assistant giving an "uncertain"/approximate
    answer about a running experiment's size -- root cause was that neither
    the real bytes captured so far nor the pre-flight estimate was ever
    exposed at all. Both must be present now, clearly distinguished (a
    measured "so far" figure vs. a labeled, non-final estimate)."""
    storage = Storage(app_config.storage_root)
    hw = build_hardware(app_config, DeviceSettings())
    runner = ExperimentRunner(hw, storage)
    runner.status.state = ExperimentState.running
    runner.status.phase = ExperimentPhase.dark
    runner.status.experimentId = "2026-01-01_ivan_test"
    runner.status.username = "ivan"
    runner.status.imagesCaptured = 163
    runner.status.imagesPlanned = 400
    runner.status.bytesUsed = 670_000_000
    runner.status.estimatedTotalBytes = 21_021_327_360
    service = AssistantService(app_config, storage, runner)

    call = _tool_call("system_status")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "so far: 639 MB" in reply
    assert "pre-flight estimate" in reply
    assert "19.6 GB" in reply
    assert "not a measured final size" in reply


@pytest.mark.asyncio
async def test_system_status_without_runner_degrades_gracefully(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)  # no runner passed
    call = _tool_call("system_status")
    _proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert "isn't available" in reply


# --- my_settings / my_storage: strictly scoped to the requester ------------


@pytest.mark.asyncio
async def test_my_settings_reports_persisted_settings_and_mine_baseline(app_config: AppConfig):
    from rapidboxes import settings_store, user_defaults

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
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "RGBW" in reply  # current persisted setting
    assert "Your saved 'Mine' baseline (ivan)" in reply
    assert "IR" in reply  # ivan's own saved baseline
    assert "2.5x" in reply


@pytest.mark.asyncio
async def test_my_settings_no_mine_baseline_says_so(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_settings")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "No personal 'Mine' baseline saved yet" in reply


@pytest.mark.asyncio
async def test_my_settings_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_settings")
    _proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_my_settings_ignores_username_argument_from_model(app_config: AppConfig):
    """my_settings' schema takes no arguments -- even if the model tries to
    slip one in, the resolver must still use requesting_username, never a
    model-supplied one. This is the strict-scoping guarantee."""
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = {"function": {"name": "my_settings", "arguments": json.dumps({"username": "someone-else"})}}
    _proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert "ivan" in reply.lower() or "No personal 'Mine' baseline saved yet for ivan" in reply


@pytest.mark.asyncio
async def test_my_storage_reports_own_usage_only(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    for username, name in [("ivan", "run1"), ("sabol", "run2")]:
        exp = storage.create_experiment(username, name)
        exp.write_metadata({"username": username})

    service = AssistantService(app_config, storage)
    call = _tool_call("my_storage")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "ivan has 1 experiment(s)" in reply
    assert "sabol" not in reply
    assert "Device free space:" in reply


@pytest.mark.asyncio
async def test_my_storage_no_experiments_yet(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_storage")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "no stored experiments yet" in reply


@pytest.mark.asyncio
async def test_my_storage_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_storage")
    _proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert "don't know who's chatting" in reply


# --- read_experiment_log: strictly scoped to the requester's own runs ------


@pytest.mark.asyncio
async def test_read_experiment_log_returns_own_events(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    exp.append_event("started protocol=tropism username=ivan")
    exp.append_event("phase dark started (index 0)")

    service = AssistantService(app_config, storage)
    call = _tool_call("read_experiment_log")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert exp.experiment_id in reply
    assert "started protocol=tropism username=ivan" in reply
    assert "phase dark started" in reply


@pytest.mark.asyncio
async def test_read_experiment_log_reports_exact_measured_size(app_config: AppConfig):
    """This is the only tool with an exact, disk-measured size for one
    specific experiment -- unlike my_storage (all-experiments total) or
    system_status's estimatedTotalBytes (a pre-flight guess for a live
    run's full planned schedule, not a measurement)."""
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    (exp.path / "dark_00000.png").write_bytes(b"x" * 2_000_000)

    service = AssistantService(app_config, storage)
    call = _tool_call("read_experiment_log")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "2 MB" in reply
    assert "exact, measured" in reply


@pytest.mark.asyncio
async def test_read_experiment_log_never_reads_another_users_run(app_config: AppConfig):
    """read_experiment_log's schema takes no username -- even if sabol has a
    run and ivan asks, only ivan's own experiments are ever candidates."""
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("sabol", "sabol-run")
    exp.write_metadata({"username": "sabol", "startedAt": datetime.now().isoformat()})
    exp.append_event("started protocol=tropism username=sabol")

    service = AssistantService(app_config, storage)
    call = _tool_call("read_experiment_log")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "couldn't find" in reply


@pytest.mark.asyncio
async def test_read_experiment_log_no_events_says_so(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    # No append_event calls -- events.log never created.

    service = AssistantService(app_config, storage)
    call = _tool_call("read_experiment_log")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "No logged events" in reply


@pytest.mark.asyncio
async def test_read_experiment_log_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("read_experiment_log")
    _proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert "don't know who's chatting" in reply


# --- check_my_images: strictly scoped, mold needs >=3 flagged frames -------


def _mock_vision(monkeypatch, raw: str = "", unreachable: bool = False) -> None:
    async def fake_call_llm(config, model, prompt, image_paths=None):
        if unreachable:
            raise httpx.ConnectError("connection refused")
        return raw

    monkeypatch.setattr(vision, "call_llm", fake_call_llm)


@pytest.mark.asyncio
async def test_check_my_images_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("check_my_images")
    _proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_check_my_images_no_experiment_found(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("check_my_images")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "couldn't find" in reply


@pytest.mark.asyncio
async def test_check_my_images_no_images_yet(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    # No image files written -- sample_image_paths returns [].

    service = AssistantService(app_config, storage)
    call = _tool_call("check_my_images")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "no images to check yet" in reply


@pytest.mark.asyncio
async def test_check_my_images_below_threshold_not_confirmed(app_config: AppConfig, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    _mock_vision(
        monkeypatch,
        raw="frame 0: MOLD\nSUMMARY: One frame looked unusual.",
    )

    service = AssistantService(app_config, storage)
    call = _tool_call("check_my_images")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "no confirmed mold" in reply


@pytest.mark.asyncio
async def test_check_my_images_at_threshold_confirmed(app_config: AppConfig, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    for i in range(5):
        _write_fake_png(exp.path / f"dark_{i:05d}.png")

    _mock_vision(
        monkeypatch,
        raw=(
            "frame 0: MOLD\nframe 1: MOLD\nframe 2: MOLD\nframe 3: CLEAN\nframe 4: CLEAN\n"
            "SUMMARY: Visible mold growth on several frames."
        ),
    )

    service = AssistantService(app_config, storage)
    call = _tool_call("check_my_images")
    proposal, _image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "Mold appears present in 3" in reply
    assert "Visible mold growth" in reply


def _write_fake_png(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (32, 32), color="white").save(path, "PNG")


# --- show_image: resolves a real capture, strictly scoped ------------------


@pytest.mark.asyncio
async def test_show_image_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("show_image")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert image is None
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_show_image_no_experiment_found(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("show_image")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "couldn't find" in reply


@pytest.mark.asyncio
async def test_show_image_no_images_yet(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})

    service = AssistantService(app_config, storage)
    call = _tool_call("show_image")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "no captured images yet" in reply


@pytest.mark.asyncio
async def test_show_image_defaults_to_the_last_capture(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    for i in range(3):
        _write_fake_png(exp.path / f"dark_{i:05d}.png")

    service = AssistantService(app_config, storage)
    call = _tool_call("show_image")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is not None
    assert image.imageId == "dark_00002"
    assert image.experimentId == exp.experiment_id
    assert "dark_00002" in reply


@pytest.mark.asyncio
async def test_show_image_first_and_last(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    for i in range(3):
        _write_fake_png(exp.path / f"dark_{i:05d}.png")

    service = AssistantService(app_config, storage)

    call = _tool_call("show_image", which="first")
    _proposal, image, _download, _reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image.imageId == "dark_00000"

    call = _tool_call("show_image", which="last")
    _proposal, image, _download, _reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image.imageId == "dark_00002"


@pytest.mark.asyncio
async def test_show_image_by_exact_name(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    for i in range(3):
        _write_fake_png(exp.path / f"dark_{i:05d}.png")

    service = AssistantService(app_config, storage)
    call = _tool_call("show_image", which="dark_00001")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is not None
    assert image.imageId == "dark_00001"
    assert image.url == f"/api/images/{exp.experiment_id}/dark_00001"


@pytest.mark.asyncio
async def test_show_image_unknown_name_reported_clearly(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    service = AssistantService(app_config, storage)
    call = _tool_call("show_image", which="bending_09999")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "couldn't find an image" in reply


@pytest.mark.asyncio
async def test_show_image_never_shows_another_users_experiment(app_config: AppConfig):
    """show_image's schema takes no username -- even if sabol has images and
    ivan asks, only ivan's own experiments are ever candidates."""
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("sabol", "sabol-run")
    exp.write_metadata({"username": "sabol", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    service = AssistantService(app_config, storage)
    call = _tool_call("show_image")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "couldn't find" in reply


# --- describe_image: actually looks at a real capture, strictly scoped -----


@pytest.mark.asyncio
async def test_describe_image_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("describe_image")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert image is None
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_describe_image_no_experiment_found(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("describe_image")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "couldn't find" in reply


@pytest.mark.asyncio
async def test_describe_image_no_images_yet(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})

    service = AssistantService(app_config, storage)
    call = _tool_call("describe_image")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "no captured images yet" in reply


@pytest.mark.asyncio
async def test_describe_image_calls_vision_and_returns_the_image(app_config: AppConfig, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    _mock_vision(monkeypatch, raw="A green seedling on a dark plate, evenly lit, nothing unusual.")

    service = AssistantService(app_config, storage)
    call = _tool_call("describe_image", which="dark_00000")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert reply == "A green seedling on a dark plate, evenly lit, nothing unusual."
    assert image is not None
    assert image.imageId == "dark_00000"
    assert image.experimentId == exp.experiment_id


@pytest.mark.asyncio
async def test_describe_image_reports_vision_failure_gracefully(app_config: AppConfig, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    _mock_vision(monkeypatch, unreachable=True)

    service = AssistantService(app_config, storage)
    call = _tool_call("describe_image")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "couldn't run the image description" in reply


@pytest.mark.asyncio
async def test_describe_image_never_describes_another_users_image(app_config: AppConfig, monkeypatch):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("sabol", "sabol-run")
    exp.write_metadata({"username": "sabol", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    called = {"n": 0}

    async def fake_call_llm(*a, **kw):
        called["n"] += 1
        return "should never run"

    monkeypatch.setattr(vision, "call_llm", fake_call_llm)

    service = AssistantService(app_config, storage)
    call = _tool_call("describe_image")
    _proposal, image, _download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "couldn't find" in reply
    assert called["n"] == 0


# --- download_experiment: resolves a real folder, strictly scoped ----------


@pytest.mark.asyncio
async def test_download_experiment_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert download is None
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_download_experiment_no_experiment_found(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "couldn't find" in reply


@pytest.mark.asyncio
async def test_download_experiment_resolves_real_data(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is not None
    assert download.experimentId == exp.experiment_id
    assert download.url == f"/api/experiments/{exp.experiment_id}/download"
    assert download.filename == f"{exp.experiment_id}.zip"
    assert download.sizeBytes == exp.size_bytes()
    assert exp.experiment_id in reply


@pytest.mark.asyncio
async def test_download_experiment_never_packages_another_users_run(app_config: AppConfig):
    """download_experiment's schema takes no username -- even if sabol has a
    run and ivan asks, only ivan's own experiments are ever candidates."""
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("sabol", "sabol-run")
    exp.write_metadata({"username": "sabol", "startedAt": datetime.now().isoformat()})

    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "couldn't find" in reply


# --- download_experiment: a specific range/count of images, not the whole --


def _five_image_experiment(storage: Storage):
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    for i in range(5):
        _write_fake_png(exp.path / f"dark_{i:05d}.png")
    return exp


@pytest.mark.asyncio
async def test_download_experiment_first_n_images(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = _five_image_experiment(storage)

    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment", firstN=3)
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is not None
    assert download.imageIds == ["dark_00000", "dark_00001", "dark_00002"]
    assert download.filename == f"{exp.experiment_id}_3-images.zip"
    assert download.url == f"/api/experiments/{exp.experiment_id}/download?images=dark_00000,dark_00001,dark_00002"
    expected_size = sum((exp.path / f"dark_{i:05d}.png").stat().st_size for i in range(3))
    assert download.sizeBytes == expected_size
    assert "3 image" in reply


@pytest.mark.asyncio
async def test_download_experiment_last_n_images(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    _five_image_experiment(storage)

    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment", lastN=2)
    _proposal, _image, download, _reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is not None
    assert download.imageIds == ["dark_00003", "dark_00004"]


@pytest.mark.asyncio
async def test_download_experiment_explicit_index_range(app_config: AppConfig):
    """'images 2 through 4' -- 1-based, inclusive, matching how a person
    would actually describe a range."""
    storage = Storage(app_config.storage_root)
    _five_image_experiment(storage)

    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment", startIndex=2, endIndex=4)
    _proposal, _image, download, _reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is not None
    assert download.imageIds == ["dark_00001", "dark_00002", "dark_00003"]


@pytest.mark.asyncio
async def test_download_experiment_no_range_args_packages_the_whole_thing(app_config: AppConfig):
    """No firstN/lastN/startIndex/endIndex -- unchanged default behavior."""
    storage = Storage(app_config.storage_root)
    exp = _five_image_experiment(storage)

    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment")
    _proposal, _image, download, _reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is not None
    assert download.imageIds is None
    assert download.url == f"/api/experiments/{exp.experiment_id}/download"
    assert download.sizeBytes == exp.size_bytes()


@pytest.mark.asyncio
async def test_download_experiment_range_on_empty_experiment(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})

    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment", firstN=3)
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "no captured images yet" in reply


@pytest.mark.asyncio
async def test_download_experiment_range_never_packages_another_users_run(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("sabol", "sabol-run")
    exp.write_metadata({"username": "sabol", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment", firstN=1)
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "couldn't find" in reply


# --- upload_experiment_to_remote: on-demand CIFS upload, strictly scoped ---


def _connected_remote_sync(app_config: AppConfig, tmp_path: Path) -> RemoteSyncService:
    service = RemoteSyncService(
        RemoteSyncSettings(enabled=True, server="//host.example.org/share", username="LHR", researcher="ivan"),
        storage_root=app_config.storage_root,
        simulation=True,
        mount_point=tmp_path / "mnt",
    )
    service.set_password("s3cret")
    return service


@pytest.mark.asyncio
async def test_upload_to_remote_requires_a_known_username(app_config: AppConfig, tmp_path: Path):
    storage = Storage(app_config.storage_root)
    remote_sync = _connected_remote_sync(app_config, tmp_path)
    service = AssistantService(app_config, storage, remote_sync=remote_sync)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert download is None
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_upload_to_remote_when_not_configured_at_all(app_config: AppConfig):
    """No RemoteSyncService attached at all (never wired up) -- same
    degrade-gracefully precedent as an unconfigured Telegram bot."""
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)  # remote_sync=None
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "isn't connected" in reply


@pytest.mark.asyncio
async def test_upload_to_remote_when_sync_is_switched_off(app_config: AppConfig, tmp_path: Path):
    storage = Storage(app_config.storage_root)
    remote_sync = _connected_remote_sync(app_config, tmp_path)
    remote_sync.settings.enabled = False
    service = AssistantService(app_config, storage, remote_sync=remote_sync)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "isn't connected" in reply


@pytest.mark.asyncio
async def test_upload_to_remote_when_password_missing_after_restart(app_config: AppConfig, tmp_path: Path):
    storage = Storage(app_config.storage_root)
    remote_sync = _connected_remote_sync(app_config, tmp_path)
    remote_sync.clear_password()
    service = AssistantService(app_config, storage, remote_sync=remote_sync)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "isn't connected" in reply


@pytest.mark.asyncio
async def test_upload_to_remote_no_experiment_found(app_config: AppConfig, tmp_path: Path):
    storage = Storage(app_config.storage_root)
    remote_sync = _connected_remote_sync(app_config, tmp_path)
    service = AssistantService(app_config, storage, remote_sync=remote_sync)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "couldn't find" in reply


@pytest.mark.asyncio
async def test_upload_to_remote_copies_real_files_and_reports_the_real_path(app_config: AppConfig, tmp_path: Path):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    remote_sync = _connected_remote_sync(app_config, tmp_path)
    service = AssistantService(app_config, storage, remote_sync=remote_sync)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")

    assert download is None  # text-only tool, no structured ref
    assert "Copied" in reply
    destination = remote_sync.remote_path_for("ivan") / exp.experiment_id
    assert str(destination) in reply
    assert (destination / "dark_00000.png").exists()


@pytest.mark.asyncio
async def test_upload_to_remote_never_packages_another_users_run(app_config: AppConfig, tmp_path: Path):
    """upload_experiment_to_remote's schema takes no username -- even if
    sabol has a run and ivan asks, only ivan's own experiments are ever
    candidates, and it would land in ivan's own remote folder, never
    sabol's."""
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("sabol", "sabol-run")
    exp.write_metadata({"username": "sabol", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    remote_sync = _connected_remote_sync(app_config, tmp_path)
    service = AssistantService(app_config, storage, remote_sync=remote_sync)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "couldn't find" in reply


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
