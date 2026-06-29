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
from typing import Awaitable, Callable, List, Optional, Set

from .. import config_xml
from ..models import (
    CameraSettings,
    ExperimentPhase,
    ExperimentState,
    ExperimentStatus,
    SavedExperimentConfig,
    StartResponse,
    TropismConfig,
)
from ..storage import ExperimentDir, Storage
from .scheduler import advance_deadline, planned_captures

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
    mode: Optional[str]  # "dark" | "bending" | None


def build_phases(config: TropismConfig) -> List[_Phase]:
    phases: List[_Phase] = []
    if config.preIlluminationEnabled and config.preIlluminationHours > 0:
        phases.append(
            _Phase(ExperimentPhase.pre_illumination, config.preIlluminationHours * 3600, False, None)
        )
    if config.darkPhaseEnabled and config.darkPhaseHours > 0:
        phases.append(_Phase(ExperimentPhase.dark, config.darkPhaseHours * 3600, True, "dark"))
    if config.lateralIlluminationHours > 0:
        phases.append(
            _Phase(ExperimentPhase.bending, config.lateralIlluminationHours * 3600, True, "bending")
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

    async def start(
        self, config: TropismConfig, camera: Optional[CameraSettings] = None
    ) -> StartResponse:
        if self.status.state in (ExperimentState.running, ExperimentState.paused, ExperimentState.finishing):
            return StartResponse(status="busy", experimentId=self.status.experimentId)
        if not self._hw.camera_available:
            return StartResponse(status="no_camera")
        exp = self._storage.create_experiment(config.username, config.experimentName)
        self._exp_dir = exp
        saved = SavedExperimentConfig(
            preIlluminationEnabled=config.preIlluminationEnabled,
            preIlluminationHours=config.preIlluminationHours,
            darkPhaseEnabled=config.darkPhaseEnabled,
            darkPhaseHours=config.darkPhaseHours,
            lateralIlluminationHours=config.lateralIlluminationHours,
            spectra=config.spectra,
            intervalMinutes=config.intervalMinutes,
            intensity=config.intensity,
            camera=camera or CameraSettings(),
        )
        exp.write_config_xml(config_xml.serialize(saved), config.experimentName)
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
            await self._broadcast()

    async def resume(self) -> None:
        if self.status.state == ExperimentState.paused and self._clock:
            self._clock.resume()
            self._pause_event.set()
            self.status.state = ExperimentState.running
            await self._broadcast()

    async def stop(self) -> None:
        if self.status.state in (ExperimentState.running, ExperimentState.paused):
            self._stop = True
            self._pause_event.set()  # unblock if paused
            if self._task:
                try:
                    await self._task
                except Exception:
                    log.exception("experiment task error during stop")

    async def shutdown(self) -> None:
        """Called on app shutdown: stop the run and release hardware."""
        await self.stop()
        await self._hw.shutdown()

    def recover(self) -> None:
        """On startup, report (don't resume) a previously interrupted run."""
        latest = self._storage.latest_experiment()
        meta = latest.read_metadata() if latest else None
        if meta and meta.get("state") in ("running", "paused"):
            self.status = ExperimentStatus(
                state=ExperimentState.idle,
                message=f"Previous experiment '{latest.experiment_id}' was interrupted and did not finish.",
            )

    # --- run loop -------------------------------------------------------
    async def _run(self, config: TropismConfig, exp: ExperimentDir) -> None:
        interval_s = config.intervalMinutes * 60.0
        phases = build_phases(config)
        self.status.totalSeconds = sum(p.duration_s for p in phases)
        self.status.imagesPlanned = sum(
            planned_captures(p.duration_s, interval_s) for p in phases if p.capture
        )
        try:
            await self._hw.configure_camera()
            for phase in phases:
                if self._stop:
                    break
                await self._run_phase(phase, interval_s, config, exp)
            self.status.message = "stopped by user" if self._stop else "completed"
            self.status.state = ExperimentState.done
        except asyncio.CancelledError:
            self.status.state = ExperimentState.error
            self.status.message = "cancelled"
            raise
        except Exception as e:  # pragma: no cover - defensive
            log.exception("experiment failed")
            self.status.state = ExperimentState.error
            self.status.message = str(e)
        finally:
            self.status.phase = None
            self.status.nextCaptureInSeconds = None
            try:
                await self._hw.all_off()
            except Exception:
                log.exception("all_off in finally failed")
            self._write_metadata(exp)
            await self._broadcast()

    async def _run_phase(
        self, phase: _Phase, interval_s: float, config: TropismConfig, exp: ExperimentDir
    ) -> None:
        assert self._clock is not None
        self.status.phase = phase.name
        self.status.phaseTotalSeconds = phase.duration_s
        phase_start = self._clock.elapsed()
        await self._enter_phase_lights(phase, config)
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
                await self._capture(phase, config, exp)
                e2 = self._clock.elapsed() - phase_start
                next_cap, skipped = advance_deadline(next_cap, interval_s, e2)
                if skipped:
                    log.warning(
                        "phase %s: capture overran interval, skipped %d slot(s)",
                        phase.name.value,
                        skipped,
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

        await self._hw.all_off()

    async def _enter_phase_lights(self, phase: _Phase, config: TropismConfig) -> None:
        if phase.name == ExperimentPhase.pre_illumination:
            await self._hw.top_white(config.intensity)
        elif phase.name == ExperimentPhase.dark:
            await self._hw.all_off()  # darkness; IR only fires during capture
        elif phase.name == ExperimentPhase.bending:
            await self._hw.lateral(config.spectra, config.intensity)

    async def _capture(self, phase: _Phase, config: TropismConfig, exp: ExperimentDir) -> None:
        idx = self.status.imagesCaptured
        path, image_id = exp.image_path(phase.name.value, idx)
        if phase.mode == "dark":
            await self._hw.ir_on()
            try:
                await self._hw.capture(str(path))
            finally:
                await self._hw.ir_off()
        elif phase.mode == "bending":
            await self._hw.leds_off()  # lights off during the exposure
            try:
                await self._hw.capture(str(path))
            finally:
                await self._hw.lateral(config.spectra, config.intensity)  # back on for the interval
        else:
            await self._hw.capture(str(path))
        self.status.imagesCaptured = idx + 1
        self.status.lastImageId = image_id
        self._write_metadata(exp)
        await self._broadcast()

    def _write_metadata(self, exp: ExperimentDir) -> None:
        try:
            exp.write_metadata(self.status.model_dump(mode="json"))
        except Exception:
            log.exception("metadata write failed")
