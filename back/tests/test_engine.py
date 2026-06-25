"""End-to-end engine test on simulated hardware with a virtual clock.

Drives a full two-phase experiment in milliseconds by injecting a fake time
source + sleep, so we can assert capture counts, file output, and cleanup.
"""
import asyncio
from pathlib import Path

import pytest

from rapidboxes.config import AppConfig
from rapidboxes.engine.runner import ExperimentRunner
from rapidboxes.hardware.base import BLACK
from rapidboxes.hardware.manager import build_hardware
from rapidboxes.models import DeviceSettings, ExperimentState, TropismConfig
from rapidboxes.storage import Storage


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.t += seconds
        await asyncio.sleep(0)  # yield so listeners/captures run


def _runner(tmp_path: Path, ft: FakeTime) -> ExperimentRunner:
    config = AppConfig(
        simulation=True,
        storage_root=tmp_path / "exp",
        settings_path=tmp_path / "settings.json",
    )
    config.ensure_dirs()
    hw = build_hardware(config, DeviceSettings())
    storage = Storage(config.storage_root)
    return ExperimentRunner(hw, storage, now=ft.now, sleep=ft.sleep, tick_seconds=10_000)


@pytest.mark.asyncio
async def test_full_run_captures_planned_images_and_cleans_up(tmp_path):
    ft = FakeTime()
    runner = _runner(tmp_path, ft)
    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseEnabled=True,
        darkPhaseHours=180 / 3600,        # 180s @ 60s interval -> 3 captures
        lateralIlluminationHours=120 / 3600,  # 120s @ 60s interval -> 2 captures
        spectra=["red"],
        intervalMinutes=1.0,
        intensity=50,
    )

    resp = await runner.start(config)
    assert resp.status == "started"
    await runner._task  # run to completion

    assert runner.status.state == ExperimentState.done
    assert runner.status.imagesPlanned == 5
    assert runner.status.imagesCaptured == 5

    # Files actually written.
    exp = runner.current_experiment
    jpgs = list(Path(exp.path).glob("*.jpg"))
    assert len(jpgs) == 5

    # Hardware left safe: all LEDs black, IR off.
    assert runner._hw._ir.state is False
    assert all(p == BLACK for p in runner._hw._leds.pixels)


@pytest.mark.asyncio
async def test_pause_resume_and_stop(tmp_path):
    ft = FakeTime()
    runner = _runner(tmp_path, ft)
    config = TropismConfig(
        darkPhaseEnabled=True,
        darkPhaseHours=10,  # long; we'll stop it early
        lateralIlluminationHours=0,
        intervalMinutes=5.0,
    )
    await runner.start(config)
    await asyncio.sleep(0)  # let it begin

    await runner.pause()
    assert runner.status.state == ExperimentState.paused
    await runner.resume()
    assert runner.status.state == ExperimentState.running

    await runner.stop()
    assert runner.status.state == ExperimentState.done
    assert runner.status.message == "stopped by user"
    assert runner._hw._ir.state is False
    assert all(p == BLACK for p in runner._hw._leds.pixels)
