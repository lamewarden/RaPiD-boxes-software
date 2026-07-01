"""ExperimentRunner: single-experiment state machine driving the hardware.

One asyncio task runs the phase sequence; pause/resume/stop come from API handlers
on other tasks. The pausable clock measures elapsed time, so captures are scheduled
in "elapsed seconds" and naturally freeze while paused. Cleanup (lights off, camera
released, metadata flushed) is guaranteed in a finally block.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, List, Optional, Set, Union

from .. import config_xml
from ..models import (
    CameraSettings,
    GROWTH_PHOTO_FLASH_INTENSITY,
    ExperimentPhase,
    ExperimentState,
    ExperimentStatus,
    GrowthConfig,
    SavedExperimentConfig,
    StartResponse,
    TropismConfig,
)
from ..logging import (
    clear_experiment_context,
    journal_step,
    log_error,
    reset_step_index,
    set_experiment_context,
    set_phase,
)
from ..storage import ExperimentDir, Storage
from .scheduler import advance_deadline, planned_captures

Config = Union[TropismConfig, GrowthConfig]

log = logging.getLogger("rapidboxes.engine")

_EPS = 1e-9
Listener = Callable[[dict], Awaitable[None]]


class PausableClock:
    """Elapsed-seconds clock that can be paused; uses an injectable time source."""

    def __init__(self, now: Callable[[], float]):
        self._now = now
        self._start = now()
        self._paused_at: Optional[float] = None
        self._accum = 0.0

    def elapsed(self) -> float:
        t = self._now()
        e = t - self._start - self._accum
        if self._paused_at is not None:
            e -= t - self._paused_at
        return e

    def pause(self) -> None:
        if self._paused_at is None:
            self._paused_at = self._now()

    def resume(self) -> None:
        if self._paused_at is not None:
            self._accum += self._now() - self._paused_at
            self._paused_at = None


@dataclass
class _Phase:
    name: ExperimentPhase
    duration_s: float
    capture: bool
    mode: Optional[str]  # "dark" | "bending" | "day" | "night" | None
    day_index: Optional[int] = None


def build_phases(config: Config) -> List[_Phase]:
    if isinstance(config, GrowthConfig):
        return _build_growth_phases(config)
    return _build_tropism_phases(config)


def _build_tropism_phases(config: TropismConfig) -> List[_Phase]:
    phases: List[_Phase] = []
    if config.darkPhaseEnabled and config.darkPhaseHours > 0:
        phases.append(_Phase(ExperimentPhase.dark, config.darkPhaseHours * 3600, True, "dark"))
    if config.lateralIlluminationHours > 0:
        phases.append(
            _Phase(ExperimentPhase.bending, config.lateralIlluminationHours * 3600, True, "bending")
        )
    return phases


def _build_growth_phases(config: GrowthConfig) -> List[_Phase]:
    phases: List[_Phase] = []
    night_hours = 24 - config.dayLengthHours
    for day in range(1, config.experimentLengthDays + 1):
        if config.dayLengthHours > 0:
            phases.append(
                _Phase(ExperimentPhase.day, config.dayLengthHours * 3600, True, "day", day_index=day)
            )
        if night_hours > 0:
            phases.append(
                _Phase(ExperimentPhase.night, night_hours * 3600, True, "night", day_index=day)
            )
    return phases


class ExperimentRunner:
    def __init__(
        self,
        hw,
        storage: Storage,
        *,
        now: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        tick_seconds: float = 1.0,
    ):
        self._hw = hw
        self._storage = storage
        self._now = now or (lambda: asyncio.get_event_loop().time())
        self._sleep = sleep or asyncio.sleep
        self._tick = tick_seconds

        self.status = ExperimentStatus()
        self._task: Optional[asyncio.Task] = None
        self._stop = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._clock: Optional[PausableClock] = None
        self._exp_dir: Optional[ExperimentDir] = None
        self._listeners: Set[Listener] = set()

    # --- subscriptions (WebSocket) --------------------------------------
    def subscribe(self, cb: Listener) -> None:
        self._listeners.add(cb)

    def unsubscribe(self, cb: Listener) -> None:
        self._listeners.discard(cb)

    async def _broadcast(self) -> None:
        payload = self.status.model_dump(mode="json")
        for cb in list(self._listeners):
            try:
                await cb(payload)
            except Exception:
                log.debug("listener failed; dropping", exc_info=True)
                self._listeners.discard(cb)

    # --- control --------------------------------------------------------
    @property
    def current_experiment(self) -> Optional[ExperimentDir]:
        return self._exp_dir

    async def start(self, config: Config, camera: Optional[CameraSettings] = None) -> StartResponse:
        if self.status.state in (ExperimentState.running, ExperimentState.paused, ExperimentState.finishing):
            journal_step("experiment.start", "skipped", detail="busy")
            return StartResponse(status="busy", experimentId=self.status.experimentId)
        if not self._hw.camera_available:
            journal_step("experiment.start", "crashed", error="no_camera")
            log_error("experiment", "start_failed", "camera not available", reason="no_camera")
            return StartResponse(status="no_camera")

        journal_step("experiment.start", "started", detail=f"protocol={config.protocol}")
        exp = self._storage.create_experiment(config.username, config.experimentName)
        self._exp_dir = exp
        reset_step_index()
        set_experiment_context(exp.experiment_id, protocol=config.protocol)
        journal_step("experiment.create_dir", "done", detail=str(exp.path))

        try:
            if isinstance(config, TropismConfig):
                saved = SavedExperimentConfig(
                    protocol="tropism",
                    darkPhaseEnabled=config.darkPhaseEnabled,
                    darkPhaseHours=config.darkPhaseHours,
                    lateralIlluminationHours=config.lateralIlluminationHours,
                    spectra=config.spectra,
                    intervalMinutes=config.intervalMinutes,
                    intensity=config.intensity,
                    camera=camera or CameraSettings(),
                )
                exp.write_config_xml(config_xml.serialize(saved), config.experimentName)
            else:
                saved = SavedExperimentConfig(
                    protocol="growth",
                    spectra=config.spectra,
                    intervalMinutes=config.intervalMinutes,
                    dayLengthHours=config.dayLengthHours,
                    experimentLengthDays=config.experimentLengthDays,
                    dayIntensity=config.dayIntensity,
                    photoIlluminationSource=config.photoIlluminationSource,
                    camera=camera or CameraSettings(),
                )
                exp.write_config_xml(config_xml.serialize(saved), config.experimentName)
            journal_step("experiment.write_xml", "done")
        except Exception as exc:
            journal_step("experiment.write_xml", "crashed", error=str(exc))
            log_error("experiment", "write_xml_failed", str(exc), exc=exc, experiment_id=exp.experiment_id)
            clear_experiment_context()
            raise

        self._stop = False
        self._pause_event.set()
        self._clock = PausableClock(self._now)
        self.status = ExperimentStatus(
            state=ExperimentState.running,
            experimentId=exp.experiment_id,
            experimentName=config.experimentName,
            username=config.username,
            startedAt=datetime.now(),
            config=config,
        )
        self._task = asyncio.create_task(self._run(config, exp))
        await self._broadcast()
        return StartResponse(status="started", experimentId=exp.experiment_id)

    async def pause(self) -> None:
        if self.status.state == ExperimentState.running and self._clock:
            self._clock.pause()
            self._pause_event.clear()
            self.status.state = ExperimentState.paused
            journal_step("experiment.pause", "done")
            await self._broadcast()

    async def resume(self) -> None:
        if self.status.state == ExperimentState.paused and self._clock:
            self._clock.resume()
            self._pause_event.set()
            self.status.state = ExperimentState.running
            journal_step("experiment.resume", "done")
            await self._broadcast()

    async def stop(self) -> None:
        if self.status.state in (ExperimentState.running, ExperimentState.paused):
            journal_step("experiment.stop", "started")
            self._stop = True
            self._pause_event.set()  # unblock if paused
            if self._task:
                try:
                    await self._task
                except Exception as exc:
                    log.exception("experiment task error during stop")
                    log_error("experiment", "stop_task_error", str(exc), exc=exc)

    async def shutdown(self) -> None:
        """Called on app shutdown: stop the run and release hardware."""
        await self.stop()
        await self._hw.shutdown()

    def recover(self) -> None:
        """On startup, report (don't resume) a previously interrupted run."""
        latest = self._storage.latest_experiment()
        meta = latest.read_metadata() if latest else None
        if meta and meta.get("state") in ("running", "paused"):
            msg = f"Previous experiment '{latest.experiment_id}' was interrupted and did not finish."
            journal_step(
                "experiment.recover",
                "crashed",
                error="interrupted_previous_run",
                detail=msg,
                experiment_id=latest.experiment_id,
            )
            log_error("experiment", "recover_interrupted", msg, experiment_id=latest.experiment_id)
            self.status = ExperimentStatus(
                state=ExperimentState.idle,
                message=msg,
            )

    # --- run loop -------------------------------------------------------
    async def _run(self, config: Config, exp: ExperimentDir) -> None:
        interval_s = config.intervalMinutes * 60.0
        phases = build_phases(config)
        is_growth = isinstance(config, GrowthConfig)
        self.status.totalSeconds = sum(p.duration_s for p in phases)
        self.status.imagesPlanned = sum(
            planned_captures(p.duration_s, interval_s) for p in phases if p.capture
        )
        if is_growth:
            self.status.imagesPlanned += 1  # one-off baseline photo
        try:
            journal_step("experiment.configure_camera", "started")
            await self._hw.configure_camera()
            journal_step("experiment.configure_camera", "done")
            if is_growth:
                self.status.phase = ExperimentPhase.baseline
                set_phase(ExperimentPhase.baseline.value)
                await self._capture(ExperimentPhase.baseline, "baseline", config, exp)
            for phase in phases:
                if self._stop:
                    break
                await self._run_phase(phase, interval_s, config, exp)
            self.status.message = "stopped by user" if self._stop else "completed"
            self.status.state = ExperimentState.done
            journal_step(
                "experiment.finalize",
                "stopped" if self._stop else "done",
                detail=self.status.message,
            )
        except asyncio.CancelledError:
            self.status.state = ExperimentState.error
            self.status.message = "cancelled"
            journal_step("experiment.finalize", "crashed", error="cancelled")
            raise
        except Exception as e:  # pragma: no cover - defensive
            log.exception("experiment failed")
            log_error("experiment", "run_failed", str(e), exc=e)
            self.status.state = ExperimentState.error
            self.status.message = str(e)
            journal_step("experiment.finalize", "crashed", error=str(e))
        finally:
            self.status.phase = None
            self.status.nextCaptureInSeconds = None
            set_phase(None)
            journal_step("experiment.cleanup", "started")
            try:
                await self._hw.all_off()
                journal_step("experiment.cleanup", "done")
            except Exception as exc:
                log.exception("all_off in finally failed")
                journal_step("experiment.cleanup", "crashed", error=str(exc))
                log_error("hardware", "cleanup_all_off_failed", str(exc), exc=exc)
            self._write_metadata(exp)
            await self._broadcast()
            clear_experiment_context()

    async def _run_phase(
        self, phase: _Phase, interval_s: float, config: Config, exp: ExperimentDir
    ) -> None:
        assert self._clock is not None
        phase_name = phase.name.value
        step_prefix = f"phase.{phase_name}"
        self.status.phase = phase.name
        set_phase(phase_name)
        self.status.phaseTotalSeconds = phase.duration_s
        self.status.dayIndex = phase.day_index
        self.status.totalDays = config.experimentLengthDays if isinstance(config, GrowthConfig) else None
        journal_step(
            step_prefix,
            "started",
            detail=f"duration_s={phase.duration_s}, day_index={phase.day_index}",
        )
        phase_start = self._clock.elapsed()
        try:
            await self._enter_phase_lights(phase, config)
        except Exception as exc:
            journal_step(step_prefix, "crashed", error=f"enter_lights: {exc}")
            log_error("hardware", "enter_phase_lights_failed", str(exc), exc=exc, phase=phase_name)
            raise
        self._write_metadata(exp)

        next_cap: Optional[float] = 0.0 if phase.capture else None
        while not self._stop:
            await self._pause_event.wait()
            if self._stop:
                break
            e = self._clock.elapsed() - phase_start
            self.status.phaseElapsedSeconds = min(e, phase.duration_s)
            self.status.elapsedSeconds = self._clock.elapsed()
            if e >= phase.duration_s:
                break
            if phase.capture and next_cap is not None and e + _EPS >= next_cap:
                await self._capture(phase.name, phase.mode, config, exp)
                e2 = self._clock.elapsed() - phase_start
                next_cap, skipped = advance_deadline(next_cap, interval_s, e2)
                if skipped:
                    log.warning(
                        "phase %s: capture overran interval, skipped %d slot(s)",
                        phase.name.value,
                        skipped,
                    )
                    log_error(
                        "experiment",
                        "capture_overrun",
                        f"skipped {skipped} capture slot(s)",
                        level="WARNING",
                        fsync=False,
                        phase=phase_name,
                        skipped_slots=skipped,
                    )
            if phase.capture and next_cap is not None:
                target = min(next_cap, phase.duration_s)
                self.status.nextCaptureInSeconds = max(0.0, next_cap - (self._clock.elapsed() - phase_start))
            else:
                target = phase.duration_s
                self.status.nextCaptureInSeconds = None
            await self._broadcast()
            remaining = target - (self._clock.elapsed() - phase_start)
            await self._sleep(max(0.0, min(remaining, self._tick)))

        try:
            await self._hw.all_off()
        except Exception as exc:
            log_error("hardware", "phase_all_off_failed", str(exc), exc=exc, phase=phase_name)
            raise
        journal_step(step_prefix, "done" if not self._stop else "stopped")

    async def _enter_phase_lights(self, phase: _Phase, config: Config) -> None:
        if phase.name == ExperimentPhase.dark:
            await self._hw.all_off()  # darkness; IR only fires during capture
        elif phase.name == ExperimentPhase.bending:
            await self._hw.lateral(config.spectra, config.intensity)
        elif phase.name == ExperimentPhase.day:
            await self._hw.top(config.spectra, config.dayIntensity)
        elif phase.name == ExperimentPhase.night:
            await self._hw.all_off()  # darkness; photo illumination fires only during capture

    async def _capture(
        self, phase_name: ExperimentPhase, mode: Optional[str], config: Config, exp: ExperimentDir
    ) -> None:
        idx = self.status.imagesCaptured
        step_id = f"phase.{phase_name.value}.capture.{idx}"
        path, image_id = exp.image_path(phase_name.value, idx)
        journal_step(step_id, "started", detail=f"mode={mode}, path={path.name}")
        try:
            if mode in ("dark", "baseline"):
                await self._hw.ir_on()
                try:
                    await self._hw.capture(str(path))
                finally:
                    await self._hw.ir_off()
            elif mode == "bending":
                await self._hw.leds_off()  # lights off during the exposure
                try:
                    await self._hw.capture(str(path))
                finally:
                    await self._hw.lateral(config.spectra, config.intensity)  # back on for the interval
            elif mode == "night":
                if config.photoIlluminationSource == "ir":
                    await self._hw.ir_on()
                    try:
                        await self._hw.capture(str(path))
                    finally:
                        await self._hw.ir_off()
                else:  # rgbw: fixed-intensity top-down white flash, off again after
                    await self._hw.top_white(GROWTH_PHOTO_FLASH_INTENSITY)
                    try:
                        await self._hw.capture(str(path))
                    finally:
                        await self._hw.all_off()
            else:
                await self._hw.capture(str(path))  # "day": ambient light, no flash
        except Exception as exc:
            journal_step(step_id, "crashed", error=str(exc))
            log_error(
                "hardware.camera",
                "capture_failed",
                str(exc),
                exc=exc,
                phase=phase_name.value,
                capture_index=idx,
                image_id=image_id,
            )
            raise
        self.status.imagesCaptured = idx + 1
        self.status.lastImageId = image_id
        journal_step(step_id, "done", detail=image_id)
        self._write_metadata(exp)
        await self._broadcast()

    def _write_metadata(self, exp: ExperimentDir) -> None:
        try:
            exp.write_metadata(self.status.model_dump(mode="json"))
        except Exception as exc:
            log.exception("metadata write failed")
            log_error("storage", "metadata_write_failed", str(exc), exc=exc, experiment_id=exp.experiment_id)
