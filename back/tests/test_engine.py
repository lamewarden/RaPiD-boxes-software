"""End-to-end engine test on simulated hardware with a virtual clock.

Drives a full two-phase experiment in milliseconds by injecting a fake time
source + sleep, so we can assert capture counts, file output, and cleanup.
"""
import asyncio
from pathlib import Path

import pytest

from rapidboxes.config import AppConfig
from rapidboxes.engine.runner import ExperimentRunner
from rapidboxes.hardware.base import BLACK, white
from rapidboxes.hardware.manager import build_hardware
from rapidboxes.models import (
    GROWTH_PHOTO_FLASH_INTENSITY,
    DeviceSettings,
    ExperimentState,
    GrowthConfig,
    TropismConfig,
)
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

    # Saved config XML written at start, named after the experiment.
    xmls = list(Path(exp.path).glob("*.xml"))
    assert len(xmls) == 1
    assert xmls[0].name == "t.xml"

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


@pytest.mark.asyncio
async def test_growth_protocol_baseline_pre_illumination_day_night_rgbw_flash(tmp_path):
    ft = FakeTime()
    runner = _runner(tmp_path, ft)

    # Spy on IR/LED calls so we can tell IR-lit vs RGBW-flash-lit captures apart.
    ir_on_calls = []
    led_calls = []
    orig_ir_on = runner._hw._ir.on
    orig_set_segment = runner._hw._leds.set_segment
    runner._hw._ir.on = lambda: (ir_on_calls.append(True), orig_ir_on())[1]
    runner._hw._leds.set_segment = lambda start, end, color: (
        led_calls.append((start, end, color)),
        orig_set_segment(start, end, color),
    )[1]

    config = GrowthConfig(
        experimentName="g",
        username="u",
        preIlluminationEnabled=True,
        dayLengthHours=1,            # 1h day @ 240min interval -> 1 capture
        experimentLengthDays=1,
        spectra=["white"],
        dayIntensity=40,
        intervalMinutes=240,         # max allowed; 23h night -> 6 captures
        photoIlluminationSource="rgbw",
    )

    resp = await runner.start(config)
    assert resp.status == "started"
    await runner._task  # run to completion

    assert runner.status.state == ExperimentState.done
    # 1 baseline + 1 day capture + 6 night captures (ceil(23h*3600 / 240min*60))
    assert runner.status.imagesPlanned == 8
    assert runner.status.imagesCaptured == 8
    # totalSeconds includes the fixed 6h pre-illumination soak, which itself takes 0 captures.
    assert runner.status.totalSeconds == 6 * 3600 + 1 * 3600 + 23 * 3600
    assert runner.status.dayIndex == 1
    assert runner.status.totalDays == 1

    exp = runner.current_experiment
    files = sorted(p.name for p in Path(exp.path).glob("*.jpg"))
    assert sum(f.startswith("baseline_") for f in files) == 1
    assert sum(f.startswith("day_") for f in files) == 1
    assert sum(f.startswith("night_") for f in files) == 6
    assert not any(f.startswith("pre_illumination_") for f in files)

    # Only the baseline capture (taken before any light is on) used IR; night
    # captures used the fixed-intensity RGBW top flash instead.
    assert len(ir_on_calls) == 1
    flash_color = white(GROWTH_PHOTO_FLASH_INTENSITY)
    flash_calls = [c for c in led_calls if c[2] == flash_color]
    assert len(flash_calls) == 6

    # Hardware left safe: all LEDs black, IR off.
    assert runner._hw._ir.state is False
    assert all(p == BLACK for p in runner._hw._leds.pixels)
