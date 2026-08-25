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

import asyncio
import base64
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
from rapidboxes.dsm_sharing import DsmSharingService
from rapidboxes.models import (
    EXPOSURE_PROFILES,
    CameraSettings,
    DeviceSettings,
    DsmSharingSettings,
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
            ac._app = app  # type: ignore[attr-defined]  - some tests reach for app.state.app
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
async def test_pending_launch_endpoint_is_one_shot(client: AsyncClient):
    assistant = client._app.state.app.assistant  # type: ignore[attr-defined]
    assistant.stage_pending_launch("ivan", SavedExperimentConfig(protocol="growth", dayLengthHours=18))

    res = await client.get("/api/assistant/pending-launch", params={"username": "ivan"})
    assert res.status_code == 200
    assert res.json()["config"]["dayLengthHours"] == 18

    # Consumed -- a second GET for the same user comes back empty.
    res = await client.get("/api/assistant/pending-launch", params={"username": "ivan"})
    assert res.json()["config"] is None


@pytest.mark.asyncio
async def test_pending_launch_endpoint_empty_for_unknown_user(client: AsyncClient):
    res = await client.get("/api/assistant/pending-launch", params={"username": "nobody"})
    assert res.status_code == 200
    assert res.json()["config"] is None


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
        proposal, _image, _download, _live_image, _chat_action, _summary = await service._resolve_tool_call(call, requesting_username=None)
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
        proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
        assert proposal is None
        assert "couldn't find" in reply
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_prefill_start_now_sets_chat_action_with_or_without_a_real_match(
    app_config: AppConfig,
):
    """chatAction is telegram_link.py's only signal to hand a plain-English
    "start it" off to the real /launch wizard. Fires on startNow=true
    whether or not a past-experiment match was found -- matching
    _handle_launch_command's own "starts from bare defaults if nothing is
    found" behavior for the literal /launch command, so "start it" (no
    match) behaves exactly like typing /launch with an unmatched reference,
    not like a dead end."""
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "prior-run")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    exp.write_config_xml(config_xml.serialize(SavedExperimentConfig()), "prior-run")
    service = AssistantService(app_config, storage)
    try:
        call = _tool_call("prefill_experiment", reference="my run", startNow=True)
        proposal, _image, _download, _live_image, chat_action, _reply = await service._resolve_tool_call(
            call, requesting_username="ivan"
        )
        assert proposal is not None
        assert chat_action == "start_launch"

        call = _tool_call("prefill_experiment", reference="nonexistent-run", startNow=True)
        proposal, _image, _download, _live_image, chat_action, _reply = await service._resolve_tool_call(
            call, requesting_username="nobody"
        )
        assert proposal is None
        assert chat_action == "start_launch"
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_prefill_without_start_now_never_sets_chat_action(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "prior-run")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    exp.write_config_xml(config_xml.serialize(SavedExperimentConfig()), "prior-run")
    service = AssistantService(app_config, storage)
    try:
        call = _tool_call("prefill_experiment", reference="my run")
        proposal, _image, _download, _live_image, chat_action, _reply = await service._resolve_tool_call(
            call, requesting_username="ivan"
        )
        assert proposal is not None
        assert chat_action is None
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_prefill_exact_repeat_sets_the_stronger_chat_action_only_with_a_real_match(
    app_config: AppConfig,
):
    """"Same as my last one" (exactRepeat) is stronger than plain startNow
    -- it needs a real past run to repeat, so with no match it must
    degrade to the ordinary start_launch wizard, never silently invent a
    "same as defaults" run."""
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "prior-run")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    exp.write_config_xml(config_xml.serialize(SavedExperimentConfig()), "prior-run")
    service = AssistantService(app_config, storage)
    try:
        call = _tool_call("prefill_experiment", reference="my run", startNow=True, exactRepeat=True)
        proposal, _image, _download, _live_image, chat_action, _reply = await service._resolve_tool_call(
            call, requesting_username="ivan"
        )
        assert proposal is not None
        assert chat_action == "start_launch_exact"

        call = _tool_call("prefill_experiment", reference="nonexistent-run", startNow=True, exactRepeat=True)
        proposal, _image, _download, _live_image, chat_action, _reply = await service._resolve_tool_call(
            call, requesting_username="nobody"
        )
        assert proposal is None
        assert chat_action == "start_launch"
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_stop_experiment_tool_sets_chat_action_and_a_safe_fallback_reply(app_config: AppConfig):
    """Tool-level check only -- telegram_link.py is what actually acts on
    chatAction=="stop"; here we just confirm the fallback reply (shown
    as-is anywhere that doesn't intercept chatAction, e.g. the web chat)
    never claims to have stopped anything itself."""
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("stop_experiment")
    proposal, _image, _download, _live_image, chat_action, reply = await service._resolve_tool_call(
        call, requesting_username="ivan"
    )
    assert proposal is None
    assert chat_action == "stop"
    assert "Stop button" in reply


@pytest.mark.asyncio
async def test_unknown_tool_name_degrades_gracefully(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("delete_everything")
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "went wrong" in reply


@pytest.mark.asyncio
async def test_malformed_tool_arguments_degrade_gracefully(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    hw = build_hardware(app_config, DeviceSettings())
    runner = ExperimentRunner(hw, storage)
    service = AssistantService(app_config, storage, runner)
    call = {"function": {"name": "system_status", "arguments": "not valid json"}}
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert "Last 2 experiment(s)" in reply


@pytest.mark.asyncio
async def test_list_experiments_no_match(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("list_experiments", username="ghost")
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert proposal is None
    assert "30 min offline" in reply
    assert "3 images could not be captured" in reply


@pytest.mark.asyncio
async def test_system_status_reports_elapsed_remaining_and_expected_finish(app_config: AppConfig):
    # Real wall-clock, offset just 2h in the future -- avoids needing a
    # datetime.now() mock (no time-freezing dependency in this repo); the
    # only flake risk is a test run landing within 2h of midnight, rare
    # enough to accept for a local suite.
    started_at = datetime.now() - timedelta(hours=2)
    storage = Storage(app_config.storage_root)
    hw = build_hardware(app_config, DeviceSettings())
    runner = ExperimentRunner(hw, storage)
    runner.status.state = ExperimentState.running
    runner.status.phase = ExperimentPhase.dark
    runner.status.experimentId = "2026-01-01_ivan_test"
    runner.status.username = "ivan"
    runner.status.imagesCaptured = 5
    runner.status.imagesPlanned = 100
    runner.status.startedAt = started_at
    runner.status.elapsedSeconds = 2 * 3600
    runner.status.totalSeconds = 4 * 3600  # 2h left, finishes ~now + 2h
    service = AssistantService(app_config, storage, runner)

    call = _tool_call("system_status")
    _proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)

    assert "2h 0m elapsed" in reply
    assert "2h 0m remaining" in reply
    expected_finish = (started_at + timedelta(hours=4)).strftime("%H:%M")
    assert f"expected to finish today at {expected_finish}" in reply


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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
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
    _proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "No personal 'Mine' baseline saved yet" in reply


@pytest.mark.asyncio
async def test_my_settings_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_settings")
    _proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_my_settings_ignores_username_argument_from_model(app_config: AppConfig):
    """my_settings' schema takes no arguments -- even if the model tries to
    slip one in, the resolver must still use requesting_username, never a
    model-supplied one. This is the strict-scoping guarantee."""
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = {"function": {"name": "my_settings", "arguments": json.dumps({"username": "someone-else"})}}
    _proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert "ivan" in reply.lower() or "No personal 'Mine' baseline saved yet for ivan" in reply


@pytest.mark.asyncio
async def test_my_storage_reports_own_usage_only(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    for username, name in [("ivan", "run1"), ("sabol", "run2")]:
        exp = storage.create_experiment(username, name)
        exp.write_metadata({"username": username})

    service = AssistantService(app_config, storage)
    call = _tool_call("my_storage")
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "ivan has 1 experiment(s)" in reply
    assert "sabol" not in reply
    assert "Device free space:" in reply


@pytest.mark.asyncio
async def test_my_storage_no_experiments_yet(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_storage")
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "no stored experiments yet" in reply


@pytest.mark.asyncio
async def test_my_storage_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("my_storage")
    _proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert proposal is None
    assert "No logged events" in reply


@pytest.mark.asyncio
async def test_read_experiment_log_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("read_experiment_log")
    _proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
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
    _proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_check_my_images_no_experiment_found(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("check_my_images")
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    proposal, _image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert image is None
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_show_image_no_experiment_found(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("show_image")
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "couldn't find" in reply


@pytest.mark.asyncio
async def test_show_image_no_images_yet(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})

    service = AssistantService(app_config, storage)
    call = _tool_call("show_image")
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, image, _download, _live_image, _chat_action, _reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image.imageId == "dark_00000"

    call = _tool_call("show_image", which="last")
    _proposal, image, _download, _live_image, _chat_action, _reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image.imageId == "dark_00002"


# --- take_snapshot / take_screenshot: natural-language routing to a real ---
# --- ephemeral capture (not download_experiment) -- these went through the -
# --- real app lifespan (the `client` fixture, real runner+app_state) since -
# --- both resolvers need self._runner/self._app_state, unlike show_image's -
# --- plain storage lookup. Reproduces the reported bug: a plain-English ----
# --- "send me a screenshot" request used to have no matching tool and fell -
# --- through to download_experiment instead. ------------------------------


@pytest.mark.asyncio
async def test_chat_take_snapshot_resolves_to_a_real_live_image(client: AsyncClient, monkeypatch):
    _mock_llm(monkeypatch, tool_name="take_snapshot")
    res = await client.post(
        "/api/assistant/chat",
        json={"message": "send me a snapshot of the plant right now", "history": [], "username": "ivan"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["download"] is None
    live_image = body["liveImage"]
    assert live_image is not None
    assert live_image["mimeType"] == "image/jpeg"
    decoded = base64.b64decode(live_image["base64Data"])
    assert len(decoded) > 0
    assert "current camera and light settings" in live_image["caption"]


@pytest.mark.asyncio
async def test_chat_take_snapshot_without_a_known_username_degrades(client: AsyncClient, monkeypatch):
    _mock_llm(monkeypatch, tool_name="take_snapshot")
    res = await client.post(
        "/api/assistant/chat",
        json={"message": "send me a snapshot", "history": []},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["liveImage"] is None
    assert "don't know who's chatting" in body["reply"]


@pytest.mark.asyncio
async def test_chat_take_screenshot_resolves_to_a_real_live_image(client: AsyncClient, monkeypatch):
    from rapidboxes.assistant import service as assistant_service_module

    async def fake_screenshot() -> bytes:
        return b"\x89PNG\r\n\x1a\nfake-screenshot-bytes"

    monkeypatch.setattr(assistant_service_module, "capture_kiosk_screenshot", fake_screenshot)
    _mock_llm(monkeypatch, tool_name="take_screenshot")

    res = await client.post(
        "/api/assistant/chat",
        json={"message": "take a screenshot of the current web ui screen and send it to me", "history": []},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["download"] is None
    live_image = body["liveImage"]
    assert live_image is not None
    assert live_image["mimeType"] == "image/png"
    assert base64.b64decode(live_image["base64Data"]) == b"\x89PNG\r\n\x1a\nfake-screenshot-bytes"
    assert live_image["caption"] == "Current kiosk screen."


@pytest.mark.asyncio
async def test_chat_take_screenshot_relays_the_unavailable_reason(client: AsyncClient, monkeypatch):
    from rapidboxes.assistant import service as assistant_service_module
    from rapidboxes.kiosk_screenshot import KioskScreenshotUnavailable

    async def fake_screenshot() -> bytes:
        raise KioskScreenshotUnavailable("no kiosk display session found")

    monkeypatch.setattr(assistant_service_module, "capture_kiosk_screenshot", fake_screenshot)
    _mock_llm(monkeypatch, tool_name="take_screenshot")

    res = await client.post(
        "/api/assistant/chat",
        json={"message": "screenshot please", "history": []},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["liveImage"] is None
    assert "no kiosk display session found" in body["reply"]


@pytest.mark.asyncio
async def test_show_image_by_exact_name(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    for i in range(3):
        _write_fake_png(exp.path / f"dark_{i:05d}.png")

    service = AssistantService(app_config, storage)
    call = _tool_call("show_image", which="dark_00001")
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "couldn't find" in reply


# --- describe_image: actually looks at a real capture, strictly scoped -----


@pytest.mark.asyncio
async def test_describe_image_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("describe_image")
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert image is None
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_describe_image_no_experiment_found(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("describe_image")
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "couldn't find" in reply


@pytest.mark.asyncio
async def test_describe_image_no_images_yet(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})

    service = AssistantService(app_config, storage)
    call = _tool_call("describe_image")
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, image, _download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert image is None
    assert "couldn't find" in reply
    assert called["n"] == 0


# --- download_experiment: resolves a real folder, strictly scoped ----------


@pytest.mark.asyncio
async def test_download_experiment_requires_a_known_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment")
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert download is None
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_download_experiment_no_experiment_found(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment")
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, _image, download, _live_image, _chat_action, _reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, _image, download, _live_image, _chat_action, _reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is not None
    assert download.imageIds == ["dark_00001", "dark_00002", "dark_00003"]


@pytest.mark.asyncio
async def test_download_experiment_no_range_args_packages_the_whole_thing(app_config: AppConfig):
    """No firstN/lastN/startIndex/endIndex -- unchanged default behavior."""
    storage = Storage(app_config.storage_root)
    exp = _five_image_experiment(storage)

    service = AssistantService(app_config, storage)
    call = _tool_call("download_experiment")
    _proposal, _image, download, _live_image, _chat_action, _reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username=None)
    assert download is None
    assert "don't know who's chatting" in reply


@pytest.mark.asyncio
async def test_upload_to_remote_when_not_configured_at_all(app_config: AppConfig):
    """No RemoteSyncService attached at all (never wired up) -- same
    degrade-gracefully precedent as an unconfigured Telegram bot."""
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)  # remote_sync=None
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "isn't connected" in reply


@pytest.mark.asyncio
async def test_upload_to_remote_when_sync_is_switched_off(app_config: AppConfig, tmp_path: Path):
    storage = Storage(app_config.storage_root)
    remote_sync = _connected_remote_sync(app_config, tmp_path)
    remote_sync.settings.enabled = False
    service = AssistantService(app_config, storage, remote_sync=remote_sync)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "isn't connected" in reply


@pytest.mark.asyncio
async def test_upload_to_remote_when_password_missing_after_restart(app_config: AppConfig, tmp_path: Path):
    storage = Storage(app_config.storage_root)
    remote_sync = _connected_remote_sync(app_config, tmp_path)
    remote_sync.clear_password()
    service = AssistantService(app_config, storage, remote_sync=remote_sync)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "isn't connected" in reply


@pytest.mark.asyncio
async def test_upload_to_remote_no_experiment_found(app_config: AppConfig, tmp_path: Path):
    storage = Storage(app_config.storage_root)
    remote_sync = _connected_remote_sync(app_config, tmp_path)
    service = AssistantService(app_config, storage, remote_sync=remote_sync)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
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
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")

    assert download is None  # text-only tool, no structured ref
    assert "Copied" in reply
    destination = remote_sync.remote_path_for("ivan") / exp.experiment_id
    assert str(destination) in reply
    assert (destination / "dark_00000.png").exists()


def _connected_dsm_sharing(tmp_path: Path) -> DsmSharingService:
    service = DsmSharingService(
        DsmSharingSettings(enabled=True, host="ds-ueb-if.example.org", username="ivan", shareRoot="/volume1/ueb-if"),
        settings_path=tmp_path / "dsm.json",
    )
    service.set_password("s3cret")
    return service


@pytest.mark.asyncio
async def test_upload_to_remote_includes_a_real_link_when_dsm_sharing_is_connected(
    app_config: AppConfig, tmp_path: Path, monkeypatch
):
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    remote_sync = _connected_remote_sync(app_config, tmp_path)
    dsm_sharing = _connected_dsm_sharing(tmp_path)

    async def fake_create_share_link(username, experiment_id):
        return True, "https://ds-ueb-if.example.org:5001/sharing/rsZdI8dEq"

    monkeypatch.setattr(dsm_sharing, "create_share_link", fake_create_share_link)

    service = AssistantService(app_config, storage, remote_sync=remote_sync, dsm_sharing=dsm_sharing)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")

    assert download is None
    assert "https://ds-ueb-if.example.org:5001/sharing/rsZdI8dEq" in reply


@pytest.mark.asyncio
async def test_upload_to_remote_falls_back_to_local_path_when_dsm_sharing_fails(
    app_config: AppConfig, tmp_path: Path, monkeypatch
):
    """DSM sharing being connected but unable to produce a link (wrong
    share root, permission issue, whatever) must not break the whole
    reply -- the local network path is still a complete, useful answer."""
    storage = Storage(app_config.storage_root)
    exp = storage.create_experiment("ivan", "run1")
    exp.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    _write_fake_png(exp.path / "dark_00000.png")

    remote_sync = _connected_remote_sync(app_config, tmp_path)
    dsm_sharing = _connected_dsm_sharing(tmp_path)

    async def failing_create_share_link(username, experiment_id):
        return False, "DSM couldn't share that path (code 408)."

    monkeypatch.setattr(dsm_sharing, "create_share_link", failing_create_share_link)

    service = AssistantService(app_config, storage, remote_sync=remote_sync, dsm_sharing=dsm_sharing)
    call = _tool_call("upload_experiment_to_remote")
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")

    assert download is None
    assert "Copied" in reply
    destination = remote_sync.remote_path_for("ivan") / exp.experiment_id
    assert str(destination) in reply


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
    _proposal, _image, download, _live_image, _chat_action, reply = await service._resolve_tool_call(call, requesting_username="ivan")
    assert download is None
    assert "couldn't find" in reply


# --- pending launch: staged by telegram_link.py's /launch wizard -----------


def test_pending_launch_round_trips_and_is_one_shot(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    config = SavedExperimentConfig(protocol="growth", dayLengthHours=18)

    service.stage_pending_launch("Ivan", config)
    taken = service.take_pending_launch("ivan")  # case-insensitive, matches Telegram/linking precedent

    assert taken is not None
    assert taken.dayLengthHours == 18
    assert service.take_pending_launch("ivan") is None  # consumed, not replayable


def test_pending_launch_protocol_mismatch_leaves_it_staged(app_config: AppConfig):
    """The Tropism and Growth setup screens both poll take_pending_launch
    on mount -- a staged growth config must not be silently discarded just
    because the Tropism screen happened to ask first."""
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    service.stage_pending_launch("ivan", SavedExperimentConfig(protocol="growth", dayLengthHours=18))

    assert service.take_pending_launch("ivan", protocol="tropism") is None  # wrong screen, not consumed

    taken = service.take_pending_launch("ivan", protocol="growth")  # right screen
    assert taken is not None
    assert taken.dayLengthHours == 18
    assert service.take_pending_launch("ivan") is None  # now actually consumed


def test_pending_launch_is_scoped_per_username(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    service.stage_pending_launch("ivan", SavedExperimentConfig(protocol="tropism"))

    assert service.take_pending_launch("sabol") is None


def test_pending_launch_expires_unclaimed(app_config: AppConfig, monkeypatch):
    from rapidboxes.assistant import service as assistant_service_module

    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    service.stage_pending_launch("ivan", SavedExperimentConfig(protocol="tropism"))

    future = assistant_service_module.time.monotonic() + assistant_service_module.PENDING_LAUNCH_TTL_S + 1
    monkeypatch.setattr(assistant_service_module.time, "monotonic", lambda: future)

    assert service.take_pending_launch("ivan") is None


# --- start_experiment_from_launch: the one path that actually touches -----
# --- hardware -- called only by telegram_link.py's /launch wizard, only ---
# --- after a human confirms every field. Uses the real app lifespan (the --
# --- `client` fixture) so attach_app_state's wiring is exercised too, not -
# --- just the method in isolation. ------------------------------------------


@pytest.mark.asyncio
async def test_start_experiment_from_launch_actually_starts(client: AsyncClient):
    app_state = client._app.state.app
    saved = SavedExperimentConfig(
        protocol="tropism",
        darkPhaseEnabled=True,
        darkPhaseHours=1,
        lateralIlluminationHours=0,
        spectra=["white"],
        intervalMinutes=1,
        intensity=25,
        photoIlluminationSource="ir",
        reportOnIssueEnabled=False,
    )

    response, message = await app_state.assistant.start_experiment_from_launch(saved, "wizard-run", "ivan")

    assert response is not None
    assert response.status == "started"
    assert "Started" in message
    assert response.experimentId in message
    # Available immediately, not just on a later /status query -- computed
    # directly from the config via build_phases(), not read off
    # ExperimentStatus.totalSeconds (which the background run task only
    # populates a moment later, after this call has already returned).
    assert "Expected to finish" in message
    assert "1h 0m total" in message  # darkPhaseHours=1, lateralIlluminationHours=0
    assert app_state.runner.status.state == ExperimentState.running
    assert app_state.runner.status.experimentName == "wizard-run"
    assert app_state.runner.status.username == "ivan"
    await app_state.runner.abort()


@pytest.mark.asyncio
async def test_start_experiment_from_launch_growth_protocol(client: AsyncClient):
    app_state = client._app.state.app
    saved = SavedExperimentConfig(
        protocol="growth",
        dayLengthHours=16,
        experimentLengthDays=1,  # kept short so the estimated footprint stays well under real free space
        spectra=["white"],
        dayIntensity=25,
        intervalMinutes=30,
        photoIlluminationSource="ir",
    )

    response, _message = await app_state.assistant.start_experiment_from_launch(saved, "growth-run", "ivan")

    assert response is not None
    assert response.status == "started"
    assert app_state.runner.status.config.protocol == "growth"
    await app_state.runner.abort()


@pytest.mark.asyncio
async def test_start_experiment_from_launch_returns_busy_when_already_running(client: AsyncClient):
    app_state = client._app.state.app
    saved = SavedExperimentConfig(protocol="tropism", darkPhaseHours=1, lateralIlluminationHours=0, intervalMinutes=1)

    first, _ = await app_state.assistant.start_experiment_from_launch(saved, "run-1", "ivan")
    assert first.status == "started"

    second, message = await app_state.assistant.start_experiment_from_launch(saved, "run-2", "ivan")
    assert second.status == "busy"
    assert second.experimentId == first.experimentId
    assert "already running" in message
    # The first run must still be the one actually running -- a "busy"
    # reply must never have touched hardware or started a second run.
    assert app_state.runner.status.experimentId == first.experimentId
    await app_state.runner.abort()


@pytest.mark.asyncio
async def test_start_experiment_from_launch_reports_no_camera(client: AsyncClient):
    app_state = client._app.state.app
    app_state.runner._hw.camera_available = False
    saved = SavedExperimentConfig(protocol="tropism", darkPhaseHours=1, lateralIlluminationHours=0, intervalMinutes=1)

    response, message = await app_state.assistant.start_experiment_from_launch(saved, "run", "ivan")

    assert response is not None
    assert response.status == "no_camera"
    assert "No camera" in message
    assert app_state.runner.status.state == ExperimentState.idle


@pytest.mark.asyncio
async def test_start_experiment_from_launch_applies_a_changed_photo_illumination_source(client: AsyncClient):
    app_state = client._app.state.app
    assert app_state.settings.photoIlluminationSource == "ir"
    saved = SavedExperimentConfig(
        protocol="tropism",
        darkPhaseHours=1,
        lateralIlluminationHours=0,
        intervalMinutes=1,
        photoIlluminationSource="rgbw",
    )

    response, _message = await app_state.assistant.start_experiment_from_launch(saved, "run", "ivan")

    assert response.status == "started"
    assert app_state.settings.photoIlluminationSource == "rgbw"  # actually applied, not just noted
    await app_state.runner.abort()


@pytest.mark.asyncio
async def test_start_experiment_from_launch_leaves_matching_source_untouched(client: AsyncClient, monkeypatch):
    app_state = client._app.state.app
    assert app_state.settings.photoIlluminationSource == "ir"

    async def fail_if_rebuilt(self, settings):
        raise AssertionError("must not rebuild hardware when the source didn't change")

    monkeypatch.setattr(type(app_state), "rebuild_hardware", fail_if_rebuilt)
    saved = SavedExperimentConfig(
        protocol="tropism", darkPhaseHours=1, lateralIlluminationHours=0, intervalMinutes=1, photoIlluminationSource="ir"
    )

    response, _message = await app_state.assistant.start_experiment_from_launch(saved, "run", "ivan")
    assert response.status == "started"
    await app_state.runner.abort()


@pytest.mark.asyncio
async def test_start_experiment_from_launch_auto_corrects_stale_exposure_on_source_switch(
    client: AsyncClient,
):
    """The real bug this session found and fixed, isolated: a naive
    DeviceSettings.model_copy(update=...) would silently skip
    _couple_exposure_to_source and leave IR's exposure applied under RGBW
    lighting. Seeds a live exposure that's only valid for IR, switches to
    RGBW with no camera_overrides at all (the "declined the override"
    path), and confirms the live exposure lands on RGBW's own default
    instead of staying stale."""
    from rapidboxes import settings_store

    app_state = client._app.state.app
    ir_default = EXPOSURE_PROFILES["ir"]["default"]
    settings_store.save_device_settings(
        app_state.config.settings_path,
        app_state.settings.model_copy(update={"camera": CameraSettings(exposureMicroseconds=ir_default)}),
    )
    await app_state.rebuild_hardware(
        app_state.settings.model_copy(update={"camera": CameraSettings(exposureMicroseconds=ir_default)})
    )
    assert app_state.settings.camera.exposureMicroseconds == ir_default

    saved = SavedExperimentConfig(
        protocol="tropism",
        darkPhaseHours=1,
        lateralIlluminationHours=0,
        intervalMinutes=1,
        photoIlluminationSource="rgbw",
    )
    response, _message = await app_state.assistant.start_experiment_from_launch(saved, "run", "ivan")

    assert response.status == "started"
    assert app_state.settings.photoIlluminationSource == "rgbw"
    assert app_state.settings.camera.exposureMicroseconds == EXPOSURE_PROFILES["rgbw"]["default"]
    await app_state.runner.abort()


@pytest.mark.asyncio
async def test_start_experiment_from_launch_applies_grayscale_and_exposure_overrides(client: AsyncClient):
    app_state = client._app.state.app
    saved = SavedExperimentConfig(
        protocol="tropism", darkPhaseHours=1, lateralIlluminationHours=0, intervalMinutes=1, photoIlluminationSource="ir"
    )

    response, _message = await app_state.assistant.start_experiment_from_launch(
        saved, "run", "ivan", {"grayscale": False, "exposureMicroseconds": 2_000_000}
    )

    assert response.status == "started"
    assert app_state.settings.camera.grayscale is False
    # Explicit, already-validated override -- the coupling validator must
    # leave a value it finds already in range untouched, not "fix" it away.
    assert app_state.settings.camera.exposureMicroseconds == 2_000_000
    await app_state.runner.abort()


@pytest.mark.asyncio
async def test_start_experiment_from_launch_without_runner_or_app_state_degrades(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)  # no runner, no attach_app_state
    saved = SavedExperimentConfig(protocol="tropism")

    response, message = await service.start_experiment_from_launch(saved, "run", "ivan")

    assert response is None
    assert "isn't available right now" in message


@pytest.mark.asyncio
async def test_capture_snapshot_while_idle_takes_a_real_photo(client: AsyncClient):
    app_state = client._app.state.app
    frame, message = await app_state.assistant.capture_snapshot("ivan")
    assert frame is not None
    assert isinstance(frame, (bytes, bytearray))
    assert len(frame) > 0
    assert "current camera and light settings" in message


@pytest.mark.asyncio
async def test_capture_snapshot_while_own_experiment_running_sends_last_capture(client: AsyncClient):
    app_state = client._app.state.app
    config = _tropism_config().model_copy(update={"experimentName": "run1", "username": "ivan"})
    res = await client.post("/api/experiments", json=config.model_dump())
    assert res.status_code == 200
    # Let at least one real capture land before asking for a snapshot.
    exp = app_state.storage.get_experiment(app_state.runner.status.experimentId)
    for _ in range(50):
        if exp.list_capture_images():
            break
        await asyncio.sleep(0.05)
    assert exp.list_capture_images(), "no capture landed in time for this test"

    frame, message = await app_state.assistant.capture_snapshot("ivan")

    assert frame is not None
    assert "experiment is running" in message
    assert "most recent real capture" in message
    await client.post("/api/experiments/current/abort")


@pytest.mark.asyncio
async def test_capture_snapshot_declines_while_another_users_experiment_runs(client: AsyncClient):
    app_state = client._app.state.app
    config = _tropism_config().model_copy(update={"experimentName": "run1", "username": "sabol"})
    res = await client.post("/api/experiments", json=config.model_dump())
    assert res.status_code == 200

    frame, message = await app_state.assistant.capture_snapshot("ivan")

    assert frame is None
    assert "can't take a test photo" in message
    await client.post("/api/experiments/current/abort")


@pytest.mark.asyncio
async def test_capture_snapshot_reports_no_camera(client: AsyncClient, monkeypatch):
    from rapidboxes.hardware.base import CameraUnavailableError

    app_state = client._app.state.app

    async def fail_unavailable(settings):
        raise CameraUnavailableError("camera not connected")

    monkeypatch.setattr(app_state.hw, "capture_test_jpeg", fail_unavailable)
    frame, message = await app_state.assistant.capture_snapshot("ivan")
    assert frame is None
    assert "No camera" in message


@pytest.mark.asyncio
async def test_capture_snapshot_without_runner_or_app_state_degrades(app_config: AppConfig):
    storage = Storage(app_config.storage_root)
    service = AssistantService(app_config, storage)
    frame, message = await service.capture_snapshot("ivan")
    assert frame is None
    assert "isn't available right now" in message


@pytest.mark.asyncio
async def test_launch_wizard_end_to_end_through_telegram_actually_starts_a_run(client: AsyncClient, monkeypatch):
    """The gap the two layers of unit tests above don't close: proves the
    real wire-up, not just the pieces in isolation -- a person answering
    /launch's questions over the real app's own TelegramLinkService, ending
    with a running experiment on the real (simulated) hardware, not just a
    staged config."""
    app_state = client._app.state.app  # type: ignore[attr-defined]
    telegram = app_state.telegram
    telegram._links["ivan"] = 42  # fake an existing link -- no need to replay the code exchange here

    sent = []

    async def fake_post(url, json=None, **kw):
        sent.append((url, json))

        class _Resp:
            def raise_for_status(self) -> None:
                pass

        return _Resp()

    monkeypatch.setattr(telegram._client, "post", fake_post)

    await telegram._dispatch_command(42, "/launch", "")
    await telegram._maybe_continue_launch_wizard(42, "tropism")
    await telegram._maybe_continue_launch_wizard(42, "telegram-launched-run")  # name
    await telegram._maybe_continue_launch_wizard(42, "no")  # dark phase disabled
    await telegram._maybe_continue_launch_wizard(42, "1")  # bending hours
    await telegram._maybe_continue_launch_wizard(42, "white")  # spectra
    await telegram._maybe_continue_launch_wizard(42, "1")  # interval
    await telegram._maybe_continue_launch_wizard(42, "25")  # intensity
    # Live settings default to "ir" -- switching to "rgbw" here, with the
    # exposure override declined, exercises the real bug this session found
    # and fixed: a model_copy-based settings update would leave IR's stale
    # 1s exposure applied under RGBW lighting (blown-out captures) instead
    # of auto-snapping to RGBW's own default.
    assert app_state.settings.photoIlluminationSource == "ir"
    await telegram._maybe_continue_launch_wizard(42, "rgbw")  # light source
    await telegram._maybe_continue_launch_wizard(42, "color")  # image color
    await telegram._maybe_continue_launch_wizard(42, "no")  # exposure override -- declined
    await telegram._maybe_continue_launch_wizard(42, "no")  # issue alerts
    await telegram._maybe_continue_launch_wizard(42, "yes")  # confirm -> real start

    assert app_state.runner.status.state == ExperimentState.running
    assert app_state.runner.status.experimentName == "telegram-launched-run"
    assert app_state.runner.status.username == "ivan"
    assert app_state.settings.photoIlluminationSource == "rgbw"
    # Auto-corrected, not left stale -- this is the actual bug fix, proven
    # end-to-end through the real DeviceSettings validator, not asserted in
    # isolation.
    assert app_state.settings.camera.exposureMicroseconds == EXPOSURE_PROFILES["rgbw"]["default"]
    final_text = sent[-1][1]["text"]
    assert "🚀" in final_text
    assert "Started" in final_text
    assert "Expected to finish" in final_text
    await app_state.runner.abort()


@pytest.mark.asyncio
async def test_launch_wizard_skip_shortcut_end_to_end_actually_starts_a_run(
    client: AsyncClient, monkeypatch
):
    """Reported problem: replying "I approve all settings - run it" to the
    wizard's very first question got a rigid "please answer 'tropism' or
    'growth'" instead of being recognized as "keep everything as shown and
    take me to the final review." Proves the shortcut works from the very
    first question (before protocol is even answered, so the
    protocol-specific fields don't exist in state.fields yet) all the way
    through a real start on the real (simulated) hardware -- not just that
    it stops erroring."""
    app_state = client._app.state.app  # type: ignore[attr-defined]
    telegram = app_state.telegram
    telegram._links["ivan"] = 42

    sent = []

    async def fake_post(url, json=None, **kw):
        sent.append((url, json))

        class _Resp:
            def raise_for_status(self) -> None:
                pass

        return _Resp()

    monkeypatch.setattr(telegram._client, "post", fake_post)

    await telegram._dispatch_command(42, "/launch", "")
    assert "Which measurement" in sent[-1][1]["text"]

    await telegram._maybe_continue_launch_wizard(42, "I aproove all settings - run it")

    # Jumped straight to the summary/confirmation, not another field prompt
    # or a warning -- no past run existed, so it used SavedExperimentConfig's
    # own bare defaults (protocol=tropism, darkPhaseHours=90, etc.).
    summary_text = sent[-1][1]["text"]
    assert "Ready to review" in summary_text
    assert 'Reply "yes"' in summary_text
    assert "⚠️" not in summary_text

    await telegram._maybe_continue_launch_wizard(42, "yes")

    assert app_state.runner.status.state == ExperimentState.running
    assert app_state.runner.status.username == "ivan"
    assert app_state.runner.status.experimentName.startswith("telegram-launch-")
    final_text = sent[-1][1]["text"]
    assert "Started" in final_text
    await app_state.runner.abort()


@pytest.mark.asyncio
async def test_launch_wizard_exact_repeat_skips_every_question_and_uses_the_real_past_values(
    client: AsyncClient, monkeypatch
):
    """Reported problem: "same as my last one" should never re-ask
    parameters one at a time -- it should show the past run's real
    settings and, on confirmation, start it. Proves no field questions are
    sent at all (a single message goes straight to the full summary) and
    that the values shown -- and the experiment actually started -- are
    the past run's own real, non-default settings, not
    SavedExperimentConfig's bare defaults."""
    app_state = client._app.state.app  # type: ignore[attr-defined]
    telegram = app_state.telegram
    storage = app_state.storage
    telegram._links["ivan"] = 42

    # A distinctive prior run -- darkPhaseHours=12.5 is nowhere near
    # SavedExperimentConfig's own default (90.0), so seeing it in the
    # summary proves this came from the real past run, not defaults.
    prior = storage.create_experiment("ivan", "prior-tropism-run")
    prior.write_metadata({"username": "ivan", "startedAt": datetime.now().isoformat()})
    prior_config = SavedExperimentConfig(
        protocol="tropism",
        darkPhaseEnabled=True,
        darkPhaseHours=12.5,
        lateralIlluminationHours=2.0,
        spectra=["red"],
        intervalMinutes=5.0,
        intensity=33,
        photoIlluminationSource="ir",
    )
    prior.write_config_xml(config_xml.serialize(prior_config), "prior-tropism-run")

    sent = []

    async def fake_post(url, json=None, **kw):
        sent.append((url, json))

        class _Resp:
            def raise_for_status(self) -> None:
                pass

        return _Resp()

    monkeypatch.setattr(telegram._client, "post", fake_post)

    await telegram._handle_launch_command(42, "same as my last one", exact_repeat=True)

    # Exactly one message -- straight to the summary, no overview, no
    # field-by-field prompts at all.
    assert len(sent) == 1
    summary_text = sent[0][1]["text"]
    assert "Ready to review" in summary_text
    assert 'Reply "yes"' in summary_text
    assert "12.5 h" in summary_text
    assert "red" in summary_text

    await telegram._maybe_continue_launch_wizard(42, "yes")

    assert app_state.runner.status.state == ExperimentState.running
    assert app_state.runner.status.username == "ivan"
    started_config = app_state.runner.status.config
    assert started_config.darkPhaseHours == 12.5
    assert started_config.spectra == ["red"]
    assert started_config.intensity == 33
    await app_state.runner.abort()


@pytest.mark.asyncio
async def test_stop_command_end_to_end_through_telegram_keeps_every_image(
    client: AsyncClient, monkeypatch
):
    """Proves the real chain, not just the pieces in isolation: a real
    running experiment, stopped through the real TelegramLinkService's
    /stop + "yes" confirmation, actually calls the real
    ExperimentRunner.stop() -- captured images stay on disk, nothing is
    deleted, unlike .abort()."""
    app_state = client._app.state.app
    telegram = app_state.telegram
    telegram._links["ivan"] = 42

    sent = []

    async def fake_post(url, json=None, **kw):
        sent.append((url, json))

        class _Resp:
            def raise_for_status(self) -> None:
                pass

        return _Resp()

    monkeypatch.setattr(telegram._client, "post", fake_post)

    config = _tropism_config().model_copy(update={"experimentName": "will-be-stopped", "username": "ivan"})
    res = await client.post("/api/experiments", json=config.model_dump())
    assert res.status_code == 200
    experiment_id = app_state.runner.status.experimentId
    exp = app_state.storage.get_experiment(experiment_id)
    for _ in range(100):
        if exp.list_capture_images():
            break
        await asyncio.sleep(0.05)
    captured_before = exp.list_capture_images()
    assert captured_before, "no capture landed in time for this test"

    await telegram._dispatch_command(42, "/stop", "")
    assert 42 in telegram._stop_confirmations
    await telegram._maybe_continue_stop_confirmation(42, "yes")

    assert app_state.runner.status.state != ExperimentState.running
    assert app_state.runner.status.message == "stopped by user"
    # The real proof: every image captured before stopping is still on disk.
    still_there = exp.list_capture_images()
    assert len(still_there) >= len(captured_before)
    assert {img["id"] for img in captured_before} <= {img["id"] for img in still_there}

    final_text = sent[-1][1]["text"]
    assert f"Stopped {experiment_id}" in final_text
    assert "kept" in final_text


def test_format_finish_time_today_tomorrow_and_further_out():
    from rapidboxes.assistant.service import format_finish_time

    now = datetime.now()
    today_start = now.replace(hour=1, minute=0, second=0, microsecond=0)
    assert format_finish_time(today_start, 3600).startswith("today at")

    # Cross into tomorrow deterministically: start right at midnight-minus-
    # a-bit isn't safe (could land "today" on a slow run), so start well
    # into today and add enough hours to guarantee tomorrow's date.
    tomorrow_result = format_finish_time(now, 36 * 3600)
    finish = now + timedelta(hours=36)
    if finish.date() == (now.date() + timedelta(days=1)):
        assert tomorrow_result.startswith("tomorrow at")
    else:
        # now was already late enough that +36h lands two days out --
        # still a real, correctly-labeled date, not "today"/"tomorrow".
        assert tomorrow_result.startswith("on ")

    far = format_finish_time(now, 30 * 86400)  # 30 days out
    assert far.startswith("on ")
    assert (now + timedelta(days=30)).strftime("%Y-%m-%d") in far


def test_format_duration_matches_telegram_links_own_copy():
    from rapidboxes.assistant.service import _format_duration as assistant_format_duration
    from rapidboxes.telegram_link import _format_duration as telegram_format_duration

    for seconds in (0, 59, 60, 3600, 3661, 86400, 86400 + 3600 * 2 + 60 * 5, 30 * 86400):
        assert assistant_format_duration(seconds) == telegram_format_duration(seconds)


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
