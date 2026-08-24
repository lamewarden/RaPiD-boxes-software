"""End-to-end engine test on simulated hardware with a virtual clock.

Drives a full two-phase experiment in milliseconds by injecting a fake time
source + sleep, so we can assert capture counts, file output, and cleanup.
"""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rapidboxes import config_xml
from rapidboxes.config import AppConfig
from rapidboxes.engine.runner import ExperimentRunner
from rapidboxes.hardware.base import BLACK, spectra_to_color, white
from rapidboxes.hardware.manager import build_hardware
from rapidboxes.models import (
    PHOTO_FLASH_INTENSITY,
    CameraSettings,
    DeviceSettings,
    ExperimentPhase,
    ExperimentState,
    ExperimentStatus,
    GrowthConfig,
    SavedExperimentConfig,
    TropismConfig,
)
from rapidboxes.storage import ExperimentDir, Storage


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


def _leave_crashed_experiment(
    tmp_path: Path,
    config,
    *,
    state: ExperimentState,
    phase: ExperimentPhase,
    elapsed_seconds: float,
    images_captured: int,
    offline_for: timedelta,
    day_index=None,
) -> Storage:
    """Write a metadata.json as if a previous process died mid-run, so the
    next ExperimentRunner's recover() has something realistic to find --
    without actually driving a live task through a real/fake-time interrupt."""
    storage = Storage(tmp_path / "exp")
    exp = storage.create_experiment(config.username, config.experimentName)
    status = ExperimentStatus(
        state=state,
        phase=phase,
        experimentId=exp.experiment_id,
        experimentName=config.experimentName,
        username=config.username,
        startedAt=datetime.now() - offline_for,
        elapsedSeconds=elapsed_seconds,
        phaseElapsedSeconds=elapsed_seconds,
        imagesCaptured=images_captured,
        imagesPlanned=1000,
        config=config,
        dayIndex=day_index,
        updatedAt=datetime.now() - offline_for,
    )
    exp.write_metadata(status.model_dump(mode="json"))
    return storage


@pytest.mark.asyncio
async def test_recover_resumes_mid_phase_after_outage_and_reports_skipped_images(tmp_path):
    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseEnabled=True,
        darkPhaseHours=7200 / 3600,          # 7200s dark phase
        lateralIlluminationHours=3600 / 3600,  # 3600s bending phase
        spectra=["red"],
        intervalMinutes=10.0,                # 600s interval
    )
    # Crashed 1000s into the dark phase (2 captures already taken, t=0 & 600),
    # offline for 1850s -> resumes at phase-elapsed 2850s.
    storage = _leave_crashed_experiment(
        tmp_path,
        config,
        state=ExperimentState.running,
        phase=ExperimentPhase.dark,
        elapsed_seconds=1000.0,
        images_captured=2,
        offline_for=timedelta(seconds=1850),
    )

    ft = FakeTime()
    runner = ExperimentRunner(
        build_hardware(
            AppConfig(simulation=True, storage_root=storage.root, settings_path=tmp_path / "settings.json"),
            DeviceSettings(),
        ),
        storage,
        now=ft.now,
        sleep=ft.sleep,
        tick_seconds=10_000,
    )

    await runner.recover()

    # Resumed immediately (synchronously, before the task even runs a tick).
    assert runner.status.state == ExperimentState.running
    assert runner.status.phase == ExperimentPhase.dark
    assert runner.status.currentPhaseIndex == 0
    assert runner._clock is not None
    assert abs(runner._clock.elapsed() - 2850.0) < 5.0

    # phases/estimatedTotalBytes are recomputed on recover(), not just
    # trusted from whatever was persisted -- the crafted metadata above (like
    # a real experiment that was already running when the app itself gets
    # upgraded to a version that added these fields) has neither.
    assert [p.name.value for p in runner.status.phases] == ["dark", "bending"]
    assert runner.status.estimatedTotalBytes is not None
    assert runner.status.estimatedTotalBytes > 0

    # 5 captures were due by phase-elapsed 2850s (t=0,600,1200,1800,2400); only
    # 2 had actually happened before the crash -> 3 were missed to the outage.
    assert runner.status.recoveryNotice is not None
    assert runner.status.recoveryNotice.imagesSkipped == 3
    assert runner.status.recoveryNotice.offlineSeconds == pytest.approx(1850.0, abs=5.0)

    await runner._task  # run the resumed experiment to completion

    assert runner.status.state == ExperimentState.done
    exp = runner.current_experiment
    files = sorted(p.name for p in Path(exp.path).glob("*.png"))
    # Numbering continues from imagesCaptured=2 -- no restart-from-zero, no
    # collision with the (pre-crash, not-actually-on-disk-in-this-test) 0 & 1.
    assert "dark_00000.png" not in files
    assert "dark_00001.png" not in files
    assert "dark_00002.png" in files
    # Both phases still ran end to end.
    assert any(f.startswith("bending_") for f in files)
    assert runner._hw._ir.state is False
    assert all(p == BLACK for p in runner._hw._leds.pixels)


@pytest.mark.asyncio
async def test_recover_restores_this_experiments_own_camera_settings(tmp_path):
    """Regression: the session's camera settings reset to the system default
    on every process start (by design, for a *fresh* start) -- but recover()
    used to leave that fresh-default hardware config in place for a *resumed*
    run too, so a restart mid-experiment silently swapped a real run's color
    mode/zoom/exposure to whatever the new session defaulted to. Caught live
    on a real device: a multi-day run went from color+zoomed to
    grayscale+wide-angle exactly at a restart."""
    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseEnabled=True,
        darkPhaseHours=7200 / 3600,
        lateralIlluminationHours=3600 / 3600,
        spectra=["red"],
        intervalMinutes=10.0,
    )
    storage = _leave_crashed_experiment(
        tmp_path,
        config,
        state=ExperimentState.running,
        phase=ExperimentPhase.dark,
        elapsed_seconds=1000.0,
        images_captured=2,
        offline_for=timedelta(seconds=1850),
    )
    exp = ExperimentDir(storage.list_experiments()[0])
    # This experiment actually started in color, zoomed in, on RGBW --
    # distinct from every field's own default (grayscale=True, zoom=1.0,
    # source="ir"), so a test that only checked "not None" couldn't pass by
    # accident.
    saved_camera = CameraSettings(grayscale=False, zoom=2.5, exposureMicroseconds=50_000)
    saved = SavedExperimentConfig(
        protocol="tropism",
        darkPhaseEnabled=config.darkPhaseEnabled,
        darkPhaseHours=config.darkPhaseHours,
        lateralIlluminationHours=config.lateralIlluminationHours,
        spectra=config.spectra,
        intervalMinutes=config.intervalMinutes,
        intensity=config.intensity,
        photoIlluminationSource="rgbw",
        camera=saved_camera,
    )
    exp.write_config_xml(config_xml.serialize(saved), config.experimentName)

    ft = FakeTime()
    # The live session's hardware is on the fresh system defaults, as it
    # would be after any real restart -- grayscale, no zoom, IR.
    runner = ExperimentRunner(
        build_hardware(
            AppConfig(simulation=True, storage_root=storage.root, settings_path=tmp_path / "settings.json"),
            DeviceSettings(),
        ),
        storage,
        now=ft.now,
        sleep=ft.sleep,
        tick_seconds=10_000,
    )

    await runner.recover()

    restored = runner._hw._settings.camera
    assert restored.grayscale is False
    assert restored.zoom == 2.5
    assert restored.exposureMicroseconds == 50_000
    assert runner._hw._settings.photoIlluminationSource == "rgbw"


@pytest.mark.asyncio
async def test_recover_marks_done_when_whole_schedule_elapsed_offline(tmp_path):
    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseEnabled=True,
        darkPhaseHours=600 / 3600,
        lateralIlluminationHours=0,
        spectra=["red"],
        intervalMinutes=1.0,
    )
    storage = _leave_crashed_experiment(
        tmp_path,
        config,
        state=ExperimentState.running,
        phase=ExperimentPhase.dark,
        elapsed_seconds=300.0,
        images_captured=1,
        offline_for=timedelta(hours=5),  # far longer than the whole 600s schedule
    )

    ft = FakeTime()
    runner = ExperimentRunner(
        build_hardware(
            AppConfig(simulation=True, storage_root=storage.root, settings_path=tmp_path / "settings.json"),
            DeviceSettings(),
        ),
        storage,
        now=ft.now,
        sleep=ft.sleep,
        tick_seconds=10_000,
    )

    await runner.recover()

    assert runner._task is None  # nothing left to run
    assert runner.status.state == ExperimentState.done
    assert runner.status.phase is None
    assert runner.status.recoveryNotice is not None
    assert runner.status.recoveryNotice.imagesSkipped > 0


@pytest.mark.asyncio
async def test_recover_restores_paused_state_without_fast_forwarding(tmp_path):
    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseEnabled=True,
        darkPhaseHours=0,
        lateralIlluminationHours=3600 / 3600,
        spectra=["red"],
        intervalMinutes=10.0,
    )
    storage = _leave_crashed_experiment(
        tmp_path,
        config,
        state=ExperimentState.paused,
        phase=ExperimentPhase.bending,
        elapsed_seconds=1234.0,
        images_captured=2,
        offline_for=timedelta(hours=6),  # irrelevant while paused
    )

    ft = FakeTime()
    runner = ExperimentRunner(
        build_hardware(
            AppConfig(simulation=True, storage_root=storage.root, settings_path=tmp_path / "settings.json"),
            DeviceSettings(),
        ),
        storage,
        now=ft.now,
        sleep=ft.sleep,
        tick_seconds=10_000,
    )

    await runner.recover()

    assert runner.status.state == ExperimentState.paused
    assert runner.status.recoveryNotice is None  # a deliberate pause isn't an "outage"
    assert runner._clock is not None
    assert abs(runner._clock.elapsed() - 1234.0) < 1.0  # no gap added

    # Task exists but is blocked on the pause event -- no progress until resumed.
    for _ in range(20):
        await asyncio.sleep(0)
    assert runner.status.imagesCaptured == 2

    await runner.resume()
    await runner._task
    assert runner.status.state == ExperimentState.done


@pytest.mark.asyncio
async def test_recover_skips_baseline_recapture_for_growth_protocol(tmp_path):
    config = GrowthConfig(
        experimentName="g",
        username="u",
        dayLengthHours=1,
        experimentLengthDays=1,
        spectra=["white"],
        dayIntensity=40,
        intervalMinutes=240,
    )
    # Baseline + one day capture already done; crashed 100s into night.
    storage = _leave_crashed_experiment(
        tmp_path,
        config,
        state=ExperimentState.running,
        phase=ExperimentPhase.night,
        elapsed_seconds=3600 + 100.0,
        images_captured=2,
        offline_for=timedelta(seconds=30),
        day_index=1,
    )

    ft = FakeTime()
    runner = ExperimentRunner(
        build_hardware(
            AppConfig(simulation=True, storage_root=storage.root, settings_path=tmp_path / "settings.json"),
            DeviceSettings(),
        ),
        storage,
        now=ft.now,
        sleep=ft.sleep,
        tick_seconds=10_000,
    )

    await runner.recover()
    await runner._task

    assert runner.status.state == ExperimentState.done
    exp = runner.current_experiment
    files = [p.name for p in Path(exp.path).glob("*.png")]
    assert not any(f.startswith("baseline_") for f in files), "baseline must not be re-captured on resume"


@pytest.mark.asyncio
async def test_recover_does_nothing_for_a_finished_experiment(tmp_path):
    config = TropismConfig(
        experimentName="t", username="u", darkPhaseHours=600 / 3600, lateralIlluminationHours=0, intervalMinutes=1.0
    )
    storage = _leave_crashed_experiment(
        tmp_path,
        config,
        state=ExperimentState.done,
        phase=None,
        elapsed_seconds=600.0,
        images_captured=10,
        offline_for=timedelta(hours=1),
    )

    ft = FakeTime()
    runner = ExperimentRunner(
        build_hardware(
            AppConfig(simulation=True, storage_root=storage.root, settings_path=tmp_path / "settings.json"),
            DeviceSettings(),
        ),
        storage,
        now=ft.now,
        sleep=ft.sleep,
        tick_seconds=10_000,
    )

    await runner.recover()

    assert runner._task is None
    assert runner.status.state == ExperimentState.idle  # untouched, fresh default


@pytest.mark.asyncio
async def test_start_reports_low_space_without_creating_experiment(tmp_path, monkeypatch):
    """When the estimated footprint doesn't fit free disk space, start()
    must report low_space and create nothing -- not even the folder."""
    import rapidboxes.engine.runner as runner_mod

    monkeypatch.setattr(runner_mod, "estimate_experiment_bytes", lambda config, camera: 10**18)

    ft = FakeTime()
    runner = _runner(tmp_path, ft)
    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseHours=180 / 3600,
        lateralIlluminationHours=0,
        spectra=["red"],
        intervalMinutes=1.0,
    )

    resp = await runner.start(config)

    assert resp.status == "low_space"
    assert resp.estimatedBytes == 10**18
    assert resp.experimentId is None
    assert runner.status.state == ExperimentState.idle
    assert list((tmp_path / "exp").glob("*")) == []


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
    pngs = list(Path(exp.path).glob("*.png"))
    assert len(pngs) == 5

    # Saved config XML written at start, named after the experiment.
    xmls = list(Path(exp.path).glob("*.xml"))
    assert len(xmls) == 1
    assert xmls[0].name == "t.xml"

    # Hardware left safe: all LEDs black, IR off.
    assert runner._hw._ir.state is False
    assert all(p == BLACK for p in runner._hw._leds.pixels)


@pytest.mark.asyncio
async def test_full_run_writes_events_log(tmp_path):
    """events.log (ExperimentDir.append_event) is the durable, guaranteed
    experiment_id-scoped log the assistant's read_experiment_log tool reads
    -- unlike the shared systemd journal, which has no per-run tagging."""
    ft = FakeTime()
    runner = _runner(tmp_path, ft)
    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseEnabled=True,
        darkPhaseHours=60 / 3600,
        lateralIlluminationHours=0,
        spectra=["red"],
        intervalMinutes=1.0,
        intensity=50,
    )

    await runner.start(config)
    await runner._task

    events = runner.current_experiment.read_events()
    assert "started protocol=tropism username=u" in events
    assert "phase dark started" in events
    assert "finished state=done message=completed" in events


@pytest.mark.asyncio
async def test_crash_writes_failure_event_to_experiment_log(tmp_path, monkeypatch):
    ft = FakeTime()
    runner = _runner(tmp_path, ft)
    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseEnabled=True,
        darkPhaseHours=60 / 3600,
        lateralIlluminationHours=0,
        spectra=["red"],
        intervalMinutes=1.0,
        intensity=50,
    )

    async def fail_capture(path):
        raise RuntimeError("simulated capture failure")

    monkeypatch.setattr(runner._hw, "capture", fail_capture)

    await runner.start(config)
    await runner._task

    assert runner.status.state == ExperimentState.error
    events = runner.current_experiment.read_events()
    assert "experiment failed: simulated capture failure" in events
    assert "finished state=error message=simulated capture failure" in events


@pytest.mark.asyncio
async def test_status_exposes_phase_plan_index_and_storage_tracking(tmp_path):
    ft = FakeTime()
    runner = _runner(tmp_path, ft)
    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseEnabled=True,
        darkPhaseHours=180 / 3600,
        lateralIlluminationHours=120 / 3600,
        spectra=["red"],
        intervalMinutes=1.0,
        intensity=50,
    )

    resp = await runner.start(config)
    assert resp.status == "started"

    # The full plan is known immediately at start, before any phase runs.
    assert [p.name.value for p in runner.status.phases] == ["dark", "bending"]
    assert runner.status.phases[0].durationSeconds == 180
    assert runner.status.phases[0].imagesPlanned == 3
    assert runner.status.phases[1].imagesPlanned == 2
    assert runner.status.estimatedTotalBytes is not None
    assert runner.status.estimatedTotalBytes > 0
    assert runner.status.bytesUsed == 0

    await runner._task  # run to completion

    assert runner.status.state == ExperimentState.done
    assert runner.status.currentPhaseIndex is None  # cleared once finished
    # Real bytes written, one stat() per capture -- must be nonzero and, for
    # a handful of small simulated frames, comfortably under the worst-case
    # pre-flight estimate (which assumes full-resolution uncompressed frames).
    assert runner.status.bytesUsed > 0
    assert runner.status.bytesUsed < runner.status.estimatedTotalBytes


@pytest.mark.asyncio
async def test_current_phase_index_advances_through_the_plan(tmp_path):
    ft = FakeTime()
    runner = _runner(tmp_path, ft)
    config = TropismConfig(
        experimentName="t",
        username="u",
        darkPhaseEnabled=True,
        darkPhaseHours=60 / 3600,
        lateralIlluminationHours=60 / 3600,
        spectra=["red"],
        intervalMinutes=1.0,
    )

    await runner.start(config)
    for _ in range(20):
        await asyncio.sleep(0)
    assert runner.status.currentPhaseIndex == 0
    assert runner.status.phase == ExperimentPhase.dark

    await runner._task
    assert runner.status.state == ExperimentState.done


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
    files = sorted(p.name for p in Path(exp.path).glob("*.png"))
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
