"""ExperimentRunner: single-experiment state machine driving the hardware.

One asyncio task runs the phase sequence; pause/resume/stop come from API handlers
on other tasks. The pausable clock measures elapsed time, so captures are scheduled
in "elapsed seconds" and naturally freeze while paused. Cleanup (lights off, camera
released, metadata flushed) is guaranteed in a finally block.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Set, Union

from .. import config_xml
from ..models import (
    CameraSettings,
    PHOTO_FLASH_INTENSITY,
    ExperimentPhase,
    ExperimentState,
    ExperimentStatus,
    GrowthConfig,
    PhaseInfo,
    RecoveryNotice,
    SavedExperimentConfig,
    StartResponse,
    StorageNotice,
    TropismConfig,
)
from ..retention import (
    cleanup_expired_experiments,
    estimate_experiment_bytes,
    experiments_near_expiration,
    suggest_deletions_for_space,
)
from ..storage import ExperimentDir, Storage
from .scheduler import advance_deadline, images_expected, phase_at, planned_captures

Config = Union[TropismConfig, GrowthConfig]

log = logging.getLogger("rapidboxes.engine")

_EPS = 1e-9
Listener = Callable[[dict], Awaitable[None]]

# How often metadata.json gets a heartbeat flush between captures, so a crash
# mid-interval (captures can be tens of minutes apart) still leaves an
# `updatedAt` recent enough for recover() to size the outage accurately --
# without wearing the SD card by writing on every 1s UI tick.
_METADATA_HEARTBEAT_S = 60.0


class PausableClock:
    """Elapsed-seconds clock that can be paused; uses an injectable time source."""

    def __init__(self, now: Callable[[], float], initial_elapsed: float = 0.0):
        self._now = now
        self._start = now() - initial_elapsed
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


def images_planned_for(config: Config) -> int:
    """Total capture count for `config`, without running anything -- used both
    to drive the live progress bar and to pre-flight a storage estimate
    before the experiment is created (see retention.estimate_experiment_bytes)."""
    interval_s = config.intervalMinutes * 60.0
    total = sum(planned_captures(p.duration_s, interval_s) for p in build_phases(config) if p.capture)
    if isinstance(config, GrowthConfig):
        total += 1  # one-off baseline photo
    return total


def phase_infos_for(config: Config) -> List[PhaseInfo]:
    """The API-facing view of build_phases(config) -- ExperimentStatus.phases,
    so the UI can show "previous/current/next phase" and a phase-by-phase
    breakdown without duplicating this scheduling logic client-side."""
    interval_s = config.intervalMinutes * 60.0
    return [
        PhaseInfo(
            name=p.name,
            durationSeconds=p.duration_s,
            capture=p.capture,
            dayIndex=p.day_index,
            imagesPlanned=planned_captures(p.duration_s, interval_s) if p.capture else 0,
        )
        for p in build_phases(config)
    ]


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
        on_image_captured: Optional[Callable[[Path, str, str], None]] = None,
        on_experiment_finished: Optional[Callable[[ExperimentDir, ExperimentStatus], Awaitable[None]]] = None,
    ):
        self._hw = hw
        self._storage = storage
        self._now = now or (lambda: asyncio.get_event_loop().time())
        self._sleep = sleep or asyncio.sleep
        self._tick = tick_seconds
        # Notified (path, experiment_id, username) right after each capture, so
        # remote sync can queue a copy. MUST be synchronous, non-blocking and
        # non-throwing -- see _capture, where its failure is swallowed: the
        # local experiment's schedule is paramount, the remote copy is
        # best-effort.
        self._on_image_captured = on_image_captured
        # Notified once a run reaches done/error (never for abort() -- that
        # path deletes the experiment and never goes through _run()'s finally
        # or recover()'s whole-schedule-elapsed branch, the only two places
        # this fires from). Unlike on_image_captured, this MAY be async and
        # slow (an LLM summary call) -- a few extra seconds at end-of-run is
        # fine, unlike mid-capture -- but must still never raise into caller.
        self._on_experiment_finished = on_experiment_finished

        self.status = ExperimentStatus()
        self._task: Optional[asyncio.Task] = None
        self._stop = False
        # abort() calls stop() internally, which lets _run() finish through
        # its *normal* done path (not a CancelledError) before the folder
        # gets deleted -- without this flag, on_experiment_finished would
        # fire (wasting a real LLM call) for data about to vanish. Set only
        # around abort()'s own stop() call.
        self._aborting = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._clock: Optional[PausableClock] = None
        self._last_heartbeat = 0.0
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
            return StartResponse(status="busy", experimentId=self.status.experimentId)
        if not self._hw.camera_available:
            return StartResponse(status="no_camera")

        cam_settings = camera or CameraSettings()
        estimated = estimate_experiment_bytes(config, cam_settings)
        available = shutil.disk_usage(self._storage.root).free
        if estimated > available:
            suggestion = suggest_deletions_for_space(self._storage, config.username, estimated, available)
            return StartResponse(
                status="low_space",
                estimatedBytes=estimated,
                availableBytes=available,
                suggestion=suggestion,
            )

        exp = self._storage.create_experiment(config.username, config.experimentName)
        self._exp_dir = exp

        cleanup_expired_experiments(self._storage, exclude_id=exp.experiment_id)
        storage_notice = self._build_storage_notice(config.username, exp.experiment_id)
        if isinstance(config, TropismConfig):
            saved = SavedExperimentConfig(
                protocol="tropism",
                darkPhaseEnabled=config.darkPhaseEnabled,
                darkPhaseHours=config.darkPhaseHours,
                lateralIlluminationHours=config.lateralIlluminationHours,
                spectra=config.spectra,
                intervalMinutes=config.intervalMinutes,
                intensity=config.intensity,
                photoIlluminationSource=self._hw.photo_illumination_source,
                leds=self._hw.led_settings,
                ir=self._hw.ir_settings,
                camera=camera or CameraSettings(),
                reportOnIssueEnabled=config.reportOnIssueEnabled,
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
                photoIlluminationSource=self._hw.photo_illumination_source,
                leds=self._hw.led_settings,
                ir=self._hw.ir_settings,
                camera=camera or CameraSettings(),
                reportOnIssueEnabled=config.reportOnIssueEnabled,
            )
            exp.write_config_xml(config_xml.serialize(saved), config.experimentName)
        exp.append_event(f"started protocol={config.protocol} username={config.username}")
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
            storageNotice=storage_notice,
            phases=phase_infos_for(config),
            estimatedTotalBytes=estimated,
        )
        self._write_metadata(exp)  # a valid snapshot exists even if start crashes right after
        self._task = asyncio.create_task(self._run(config, exp))
        await self._broadcast()
        return StartResponse(status="started", experimentId=exp.experiment_id)

    def _build_storage_notice(self, username: str, exclude_id: str) -> StorageNotice:
        """Advance warning if any of this user's own folders will be
        auto-deleted soon (see retention.RETENTION_DAYS), else a standing
        notice about the retention policy so backups aren't a surprise."""
        near = experiments_near_expiration(self._storage, username, exclude_id=exclude_id)
        if near:
            names = ", ".join(f"'{e['name'] or e['id']}' ({e['daysRemaining']}d)" for e in near)
            return StorageNotice(
                kind="expiring",
                message=f"Back up soon -- auto-deleted in ≤30 days: {names}",
                experiments=near,
            )
        return StorageNotice(
            kind="info",
            message="Device storage is automatically cleaned every 90 days -- back up your experiments in advance.",
        )

    async def mark_issue_detected(self, experiment_id: str, detail: str) -> None:
        """Called by MoldWatchService once a mid-run anomaly is confirmed.

        Ignored if `experiment_id` is no longer the active run (the check that
        found it can finish after the run itself has moved on) -- there is
        nothing live left to flag. Going through `self.status` (broadcast +
        the normal metadata heartbeat) rather than a direct file write avoids
        racing the runner's own periodic full-status metadata.json writes."""
        if self.status.experimentId != experiment_id:
            return
        self.status.issueDetected = True
        self.status.issueDetail = detail
        if self._exp_dir is not None:
            self._write_metadata(self._exp_dir)
        await self._broadcast()

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

    async def abort(self) -> None:
        """Stop the current run and delete its experiment folder (images included)."""
        experiment_id = self.status.experimentId or (
            self._exp_dir.experiment_id if self._exp_dir else None
        )
        self._aborting = True
        try:
            await self.stop()
        finally:
            self._aborting = False
        if experiment_id:
            try:
                self._storage.delete_experiment(experiment_id)
            except Exception:
                log.exception("failed to delete aborted experiment %s", experiment_id)
        self._exp_dir = None
        self._task = None
        self.status = ExperimentStatus(
            state=ExperimentState.idle,
            message="aborted — experiment deleted",
        )
        await self._broadcast()

    async def shutdown(self) -> None:
        """Called on app shutdown: stop the run and release hardware."""
        await self.stop()
        await self._hw.shutdown()

    async def recover(self) -> None:
        """On startup, resume a run interrupted by a crash, power loss, or reboot.

        The plant's clock didn't stop just because the box did, so this fast-
        forwards phases and capture deadlines by however long the box was off
        (using metadata.json's last-write time as a proxy for when it died)
        rather than quietly picking back up as if no time had passed. A
        `recoveryNotice` on the resumed status reports the outage length and
        how many captures could not be taken, so a gap in the sequence has an
        obvious explanation instead of looking like a bug.

        A run that was deliberately paused before the outage is restored
        paused, at the same point, with no time fast-forwarded -- pausing is a
        human decision that a reboot shouldn't override.
        """
        latest = self._storage.latest_experiment()
        if latest is None:
            return
        meta = latest.read_metadata()
        if not meta or meta.get("state") not in ("running", "paused"):
            return
        try:
            prev = ExperimentStatus.model_validate(meta)
        except Exception:
            log.exception("could not parse metadata for %s; not recoverable", latest.experiment_id)
            return
        if prev.config is None:
            log.warning("interrupted experiment %s has no saved config; not recoverable", latest.experiment_id)
            return

        was_running = prev.state == ExperimentState.running
        updated_at = prev.updatedAt or datetime.fromtimestamp(
            (latest.path / "metadata.json").stat().st_mtime
        )
        outage_s = max(0.0, (datetime.now() - updated_at).total_seconds()) if was_running else 0.0

        config = prev.config
        is_growth = isinstance(config, GrowthConfig)
        phases = build_phases(config)
        durations = [p.duration_s for p in phases]
        interval_s = config.intervalMinutes * 60.0
        elapsed_at_resume = prev.elapsedSeconds + outage_s
        located = phase_at(durations, elapsed_at_resume)

        self._exp_dir = latest
        self.status = prev
        # Always recompute rather than trust whatever was persisted: an
        # experiment already running when the app itself is upgraded has old
        # metadata with neither field, and both are cheap pure functions of
        # config anyway. Camera settings come from this experiment's own
        # saved XML (what it actually started with), not the live session
        # settings, which reset to the system default on every boot.
        self.status.phases = phase_infos_for(config)
        try:
            xml_bytes = latest.read_config_xml()
            saved_cfg = config_xml.parse(xml_bytes) if xml_bytes else None
        except Exception:
            log.warning("could not read saved config for %s; using system defaults", latest.experiment_id)
            saved_cfg = None
        saved_camera = saved_cfg.camera if saved_cfg else CameraSettings()
        self.status.estimatedTotalBytes = estimate_experiment_bytes(config, saved_camera)

        if located is None:
            # The whole remaining schedule elapsed while the box was off.
            expected = images_planned_for(config)
            self.status.state = ExperimentState.done
            self.status.phase = None
            self.status.currentPhaseIndex = None
            self.status.elapsedSeconds = self.status.totalSeconds
            self.status.phaseElapsedSeconds = 0.0
            self.status.nextCaptureInSeconds = None
            self.status.message = "completed (device was offline near the end)"
            self.status.recoveryNotice = self._build_recovery_notice(
                outage_s, max(0, expected - prev.imagesCaptured)
            )
            latest.append_event(
                f"finished state=done message={self.status.message} (recovered)"
            )
            self._write_metadata(latest)
            await self._hw.all_off()
            log.warning(
                "experiment %s finished its whole schedule while offline (%.0fs)",
                latest.experiment_id, outage_s,
            )
            await self._notify_finished(latest)
            return

        index, phase_elapsed = located
        expected = images_expected(list(zip(durations, [p.capture for p in phases])), index, phase_elapsed, interval_s)
        if is_growth:
            expected += 1  # baseline
        images_skipped = max(0, expected - prev.imagesCaptured)

        # The very next configure_camera() (unconditional, at the top of
        # _run()) would otherwise apply the fresh-session camera defaults --
        # silently swapping a resumed run's exposure/zoom/color mode and
        # illumination source mid-experiment. Restore what this specific
        # experiment actually started with instead.
        if saved_cfg is not None:
            self._hw.restore_experiment_settings(saved_cfg.camera, saved_cfg.photoIlluminationSource)
        else:
            log.warning(
                "%s has no saved config to restore camera settings from on resume",
                latest.experiment_id,
            )

        self._stop = False
        self._clock = PausableClock(self._now, initial_elapsed=elapsed_at_resume)
        self.status.currentPhaseIndex = index
        if was_running:
            self._pause_event.set()
            self.status.state = ExperimentState.running
            self.status.recoveryNotice = self._build_recovery_notice(outage_s, images_skipped)
        else:
            self._pause_event.clear()
            self.status.state = ExperimentState.paused

        skip_baseline = is_growth and prev.imagesCaptured >= 1
        cumulative_before = elapsed_at_resume - phase_elapsed
        self._task = asyncio.create_task(
            self._run(
                config,
                latest,
                start_phase_index=index,
                resume_phase_start=cumulative_before,
                skip_baseline=skip_baseline,
            )
        )
        log.info(
            "recovered experiment %s after %.0fs offline; resuming %s at phase %s (~%d capture(s) missed)",
            latest.experiment_id, outage_s, self.status.state.value, phases[index].name.value, images_skipped,
        )

    def _build_recovery_notice(self, outage_s: float, images_skipped: int) -> RecoveryNotice:
        minutes = outage_s / 60.0
        duration = f"{minutes / 60.0:.1f} h" if minutes >= 120 else f"{max(1, round(minutes))} min"
        images_text = (
            f"{images_skipped} image{'s' if images_skipped != 1 else ''} could not be captured"
            if images_skipped > 0
            else "no images were missed"
        )
        return RecoveryNotice(
            message=f"Resumed after ~{duration} offline (power loss or reboot) -- {images_text}.",
            offlineSeconds=outage_s,
            imagesSkipped=images_skipped,
        )

    # --- run loop -------------------------------------------------------
    async def _run(
        self,
        config: Config,
        exp: ExperimentDir,
        *,
        start_phase_index: int = 0,
        resume_phase_start: Optional[float] = None,
        skip_baseline: bool = False,
    ) -> None:
        interval_s = config.intervalMinutes * 60.0
        phases = build_phases(config)
        is_growth = isinstance(config, GrowthConfig)
        self.status.totalSeconds = sum(p.duration_s for p in phases)
        self.status.imagesPlanned = images_planned_for(config)
        cancelled = False
        try:
            await self._hw.configure_camera()
            if is_growth and not skip_baseline:
                self.status.phase = ExperimentPhase.baseline
                self.status.currentPhaseIndex = None
                await self._capture(ExperimentPhase.baseline, "baseline", config, exp)
            for i, phase in enumerate(phases):
                if i < start_phase_index:
                    continue
                if self._stop:
                    break
                phase_start = resume_phase_start if i == start_phase_index else None
                await self._run_phase(phase, interval_s, config, exp, phase_index=i, phase_start=phase_start)
            self.status.message = "stopped by user" if self._stop else "completed"
            self.status.state = ExperimentState.done
        except asyncio.CancelledError:
            cancelled = True
            self.status.state = ExperimentState.error
            self.status.message = "cancelled"
            exp.append_event("experiment cancelled")
            raise
        except Exception as e:  # pragma: no cover - defensive
            log.exception("experiment %s failed", exp.experiment_id)
            self.status.state = ExperimentState.error
            self.status.message = str(e)
            exp.append_event(f"experiment failed: {e}")
        finally:
            self.status.phase = None
            self.status.currentPhaseIndex = None
            self.status.nextCaptureInSeconds = None
            try:
                await self._hw.all_off()
            except Exception:
                log.exception("all_off in finally failed for %s", exp.experiment_id)
                exp.append_event("all_off failed during shutdown")
            exp.append_event(
                f"finished state={self.status.state.value} message={self.status.message}"
            )
            self._write_metadata(exp)
            await self._broadcast()
            if not cancelled and not self._aborting:
                await self._notify_finished(exp)

    async def _run_phase(
        self,
        phase: _Phase,
        interval_s: float,
        config: Config,
        exp: ExperimentDir,
        *,
        phase_index: int,
        phase_start: Optional[float] = None,
    ) -> None:
        assert self._clock is not None
        self.status.phase = phase.name
        self.status.currentPhaseIndex = phase_index
        self.status.phaseTotalSeconds = phase.duration_s
        self.status.dayIndex = phase.day_index
        self.status.totalDays = config.experimentLengthDays if isinstance(config, GrowthConfig) else None
        verb = "resumed" if phase_start is not None else "started"
        exp.append_event(f"phase {phase.name.value} {verb} (index {phase_index})")
        if phase_start is None:
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
                await self._capture(phase.name, phase.mode, config, exp)
                e2 = self._clock.elapsed() - phase_start
                next_cap, skipped = advance_deadline(next_cap, interval_s, e2)
                if skipped:
                    log.warning(
                        "phase %s: capture overran interval, skipped %d slot(s)",
                        phase.name.value,
                        skipped,
                    )
            elif self._clock.elapsed() - self._last_heartbeat >= _METADATA_HEARTBEAT_S:
                self._write_metadata(exp)
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

    async def _enter_phase_lights(self, phase: _Phase, config: Config) -> None:
        if phase.name == ExperimentPhase.dark:
            await self._hw.all_off()  # darkness; photo illumination fires only during capture
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
        path, image_id = exp.image_path(phase_name.value, idx)

        # The imaging sequence, identical for every phase of both protocols:
        #
        #   1. all light off (visible and IR)
        #   2. backlight on, as set in Settings > Illumination
        #   3. camera as set in Settings > Camera (applied at run start by
        #      configure_camera(); settings are locked while a run is active)
        #   4. take the image under that backlight
        #   5. backlight off
        #   6. protocol illumination back on for the interval, as configured
        #      in the Growth/Tropism program table
        #
        # The program table therefore only ever controls light *between*
        # images; Settings alone controls how an image is taken. Imaging under
        # the phase light (as the day phase used to) blew the frame out.
        await self._hw.all_off()                                    # 1
        if self._hw.photo_illumination_source == "ir":
            await self._hw.ir_on()                                  # 2
            try:
                await self._hw.capture(str(path))                   # 3, 4
            finally:
                await self._hw.ir_off()                             # 5
        else:  # rgbw: fixed-intensity top-down white flash
            await self._hw.top_white(PHOTO_FLASH_INTENSITY)         # 2
            try:
                await self._hw.capture(str(path))                   # 3, 4
            finally:
                await self._hw.all_off()                            # 5

        # 6. Dark/night/baseline phases are dark by protocol, so they have
        #    nothing to put back.
        if mode == "bending":
            await self._hw.lateral(config.spectra, config.intensity)
        elif mode == "day":
            await self._hw.top(config.spectra, config.dayIntensity)

        self.status.imagesCaptured = idx + 1
        self.status.lastImageId = image_id
        try:
            self.status.bytesUsed += path.stat().st_size
        except OSError:
            log.warning("could not stat just-captured %s for bytesUsed", path)
        self._write_metadata(exp)

        # Hand the new image to remote sync (if configured). This only drops a
        # job on an in-memory queue -- no I/O, no await, no exception escapes:
        # a hung or dead network share must never delay the next capture or
        # fail the run.
        if self._on_image_captured is not None:
            try:
                self._on_image_captured(path, exp.experiment_id, config.username)
            except Exception:
                log.warning(
                    "remote sync notification failed for %s; experiment continues",
                    exp.experiment_id,
                    exc_info=True,
                )
                exp.append_event(f"remote sync notification failed for {image_id}")

        await self._broadcast()

    def _write_metadata(self, exp: ExperimentDir) -> None:
        self.status.updatedAt = datetime.now()
        if self._clock is not None:
            self._last_heartbeat = self._clock.elapsed()
        try:
            exp.write_metadata(self.status.model_dump(mode="json"))
        except Exception:
            log.exception("metadata write failed for %s", exp.experiment_id)
            exp.append_event("metadata write failed")

    async def _notify_finished(self, exp: ExperimentDir) -> None:
        """Best-effort: a slow or failing summary call must never break
        shutdown/recovery. See on_experiment_finished's docstring above."""
        if self._on_experiment_finished is None:
            return
        try:
            await self._on_experiment_finished(exp, self.status)
        except Exception:
            log.exception("on_experiment_finished hook failed for %s", exp.experiment_id)
