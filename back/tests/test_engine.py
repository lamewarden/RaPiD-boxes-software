"""End-to-end engine test on simulated hardware with a virtual clock.

Drives a full two-phase experiment in milliseconds by injecting a fake time
source + sleep, so we can assert capture counts, file output, and cleanup.
"""
import asyncio
from pathlib import Path

import pytest

from rapidboxes.config import AppConfig
from rapidboxes.engine.runner import ExperimentRunner
from rapidboxes.hardware.base import BLACK, spectra_to_color, white
from rapidboxes.hardware.manager import build_hardware
from rapidboxes.models import (
    PHOTO_FLASH_INTENSITY,
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


def _runner(tmp_path: Path, ft: FakeTime, settings: DeviceSettings = None) -> ExperimentRunner:
    config = AppConfig(
        simulation=True,
        storage_root=tmp_path / "exp",
        settings_path=tmp_path / "settings.json",
    )
    config.ensure_dirs()
    hw = build_hardware(config, settings or DeviceSettings())
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
async def test_growth_protocol_baseline_day_night_rgbw_flash(tmp_path):
    ft = FakeTime()
    settings = DeviceSettings(photoIlluminationSource="rgbw")
    runner = _runner(tmp_path, ft, settings)

    # Spy on IR/LED calls so we can tell IR-lit vs RGBW-flash-lit captures apart.
    ir_on_calls = []
    led_calls = []
    orig_ir_on = runner._hw._ir.on
    orig_set_segment = runner._hw._leds.set_segment
    runner._hw._ir.on = lambda: (ir_on_calls.append(True), orig_ir_on())[1]
    runner._hw._leds.set_segment = lambda start, end, color, stride=1: (
        led_calls.append((start, end, color)),
        orig_set_segment(start, end, color, stride),
    )[1]

    config = GrowthConfig(
        experimentName="g",
        username="u",
        dayLengthHours=1,            # 1h day @ 240min interval -> 1 capture
        experimentLengthDays=1,
        spectra=["white"],
        dayIntensity=40,
        intervalMinutes=240,         # max allowed; 23h night -> 6 captures
    )

    resp = await runner.start(config)
    assert resp.status == "started"
    await runner._task  # run to completion

    assert runner.status.state == ExperimentState.done
    # 1 baseline + 1 day capture + 6 night captures (ceil(23h*3600 / 240min*60))
    assert runner.status.imagesPlanned == 8
    assert runner.status.imagesCaptured == 8
    assert runner.status.totalSeconds == 1 * 3600 + 23 * 3600
    assert runner.status.dayIndex == 1
    assert runner.status.totalDays == 1

    exp = runner.current_experiment
    files = sorted(p.name for p in Path(exp.path).glob("*.jpg"))
    assert sum(f.startswith("baseline_") for f in files) == 1
    assert sum(f.startswith("day_") for f in files) == 1
    assert sum(f.startswith("night_") for f in files) == 6

    # With photoIlluminationSource="rgbw", the setting applies uniformly to
    # every capture — baseline, day and night alike — so all 8 fire the RGBW
    # top flash and none use IR. The day capture is included: it must NOT be
    # taken under the phase's day lighting (that oversaturated the frame).
    assert len(ir_on_calls) == 0
    flash_color = white(PHOTO_FLASH_INTENSITY)
    flash_calls = [c for c in led_calls if c[2] == flash_color]
    assert len(flash_calls) == 8

    # Hardware left safe: all LEDs black, IR off.
    assert runner._hw._ir.state is False
    assert all(p == BLACK for p in runner._hw._leds.pixels)


@pytest.mark.asyncio
async def test_tropism_dark_phase_respects_rgbw_illumination_setting(tmp_path):
    """The photoIlluminationSource setting applies to Tropism's dark phase too,
    not just Growth night — that's the whole point of moving it to Settings."""
    ft = FakeTime()
    settings = DeviceSettings(photoIlluminationSource="rgbw")
    runner = _runner(tmp_path, ft, settings)

    ir_on_calls = []
    orig_ir_on = runner._hw._ir.on
    runner._hw._ir.on = lambda: (ir_on_calls.append(True), orig_ir_on())[1]

    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseEnabled=True,
        darkPhaseHours=120 / 3600,  # 120s @ 60s interval -> 2 captures
        lateralIlluminationHours=0,
        intervalMinutes=1.0,
    )

    resp = await runner.start(config)
    assert resp.status == "started"
    await runner._task

    assert runner.status.state == ExperimentState.done
    assert runner.status.imagesCaptured == 2
    assert len(ir_on_calls) == 0  # rgbw setting, not the dark-phase default of ir


@pytest.mark.asyncio
async def test_day_capture_is_not_taken_under_phase_lighting(tmp_path):
    """Regression: the Growth day capture used to fire with the day LEDs still
    on, oversaturating the frame. Every capture must happen with the phase's
    between-image lighting off, lit only by the Settings photo illumination —
    and the phase lighting must be restored afterwards for the interval."""
    ft = FakeTime()
    runner = _runner(tmp_path, ft, DeviceSettings(photoIlluminationSource="ir"))

    # Record the light state at the exact instant each frame is captured.
    at_capture = []
    cam = runner._hw._camera
    orig_capture_file = cam.capture_file

    def spy(path):
        at_capture.append(
            {
                "name": Path(path).name,
                "ir": runner._hw._ir.state,
                "any_led_lit": any(p != BLACK for p in runner._hw._leds.pixels),
            }
        )
        return orig_capture_file(path)

    cam.capture_file = spy  # type: ignore[method-assign]

    led_calls = []
    orig_set_segment = runner._hw._leds.set_segment
    runner._hw._leds.set_segment = lambda start, end, color, stride=1: (
        led_calls.append(color),
        orig_set_segment(start, end, color, stride),
    )[1]

    config = GrowthConfig(
        experimentName="g",
        username="u",
        dayLengthHours=1,           # 1h day @ 240min interval -> 1 day capture
        experimentLengthDays=1,
        spectra=["white"],
        dayIntensity=100,           # max intensity: the worst case for saturation
        intervalMinutes=240,
    )

    await runner.start(config)
    await runner._task

    day_shots = [c for c in at_capture if c["name"].startswith("day_")]
    assert day_shots, "expected at least one day-phase capture"
    for shot in day_shots:
        assert shot["ir"] is True, "day frame must be lit by the photo illumination"
        assert shot["any_led_lit"] is False, "day frame must not be lit by phase LEDs"

    # Day lighting is applied twice: once entering the phase, once restored
    # after the capture — so the plants aren't left dark for the interval.
    day_color = spectra_to_color(["white"], 100)
    assert led_calls.count(day_color) == 2
