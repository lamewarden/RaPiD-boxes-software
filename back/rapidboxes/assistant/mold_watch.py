"""Opt-in mid-run mold/anomaly watcher (Phase 4 of the assistant agent-brain
work). Same asyncio.Queue + single-worker-task shape as RemoteSyncService,
fed from the same on_image_captured hook already wired into
ExperimentRunner -- see main.py's dispatch_image_captured, which fans a
capture out to both services.

Only ever active for a user who ticked "report on issue" and gave an email
address at experiment setup (TropismConfig.reportOnIssueEnabled/notifyEmail).
Every K captures for that run, it re-checks the most recent frames with the
same >= MOLD_CONFIRM_THRESHOLD-frame rule as the end-of-run summary (see
vision.py) -- never trusting one convincing frame. Once confirmed, it flags
the run exactly once (ExperimentRunner.mark_issue_detected -- live over the
WS, and durable via events.log) and would email the address on file, except
email sending has no provider configured yet: see send_email() below.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from ..config import AppConfig
from ..engine.runner import ExperimentRunner
from ..storage import Storage
from . import vision

log = logging.getLogger("rapidboxes.assistant.mold_watch")

# Re-check this often (in newly-captured images) once opted in. A real vision
# call costs real money/latency, so this trades detection lag (at most this
# many captures, typically minutes to tens of minutes apart) for not calling
# the model on every single frame.
CHECK_EVERY_N_IMAGES = 5

# How many of the most recent images to send to the model on each check --
# same frame count precedent as vision.MAX_SAMPLE_FRAMES.
CHECK_WINDOW = vision.MAX_SAMPLE_FRAMES


def send_email(to: str, subject: str, body: str) -> None:
    """Not implemented -- needs an SMTP relay or transactional-email provider
    and credentials before this can send anything (deferred, see the Phase 4
    plan). Detection, opt-in UI, and event-log/status flagging all work today
    without it; wiring a real provider in is a single-function change."""
    log.info("send_email is a stub (no provider configured); would have sent to %s: %s", to, subject)


@dataclass
class _Job:
    experiment_id: str
    email: str
    image_ids: List[str] = field(default_factory=list)


class MoldWatchService:
    """Owns the per-experiment capture counters and the background check
    queue. The only entry point used from the capture path is
    `enqueue_image`, which is synchronous, non-blocking and swallows
    everything -- a slow or failing check must never delay a capture."""

    def __init__(self, config: AppConfig, storage: Storage):
        self._config = config
        self._storage = storage
        # Set via attach_runner() once the runner exists -- main.py must wire
        # this service's own enqueue_image into the runner's on_image_captured
        # callback at *construction* time, before the runner object itself
        # exists to be passed in here, so the reference arrives one step
        # later instead of through the constructor.
        self._runner: Optional[ExperimentRunner] = None
        self._queue: "asyncio.Queue[_Job]" = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._counts: Dict[str, int] = {}
        self._recent: Dict[str, List[str]] = {}
        # Experiments already flagged -- a confirmed issue is reported once,
        # not re-alerted on every subsequent check window.
        self._flagged: Set[str] = set()

    def attach_runner(self, runner: ExperimentRunner) -> None:
        self._runner = runner

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())

    async def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None

    # --- the capture path (must never block or raise) ---------------------
    def enqueue_image(
        self, image_path: Path, experiment_id: str, username: str, report_enabled: bool, notify_email: Optional[str]
    ) -> None:
        try:
            if experiment_id in self._flagged:
                return
            if not report_enabled or not notify_email:
                # Not opted in for this run -- drop any stale counters so a
                # later differently-configured run with the same id (can't
                # normally happen, folder names are unique, but cheap to be
                # safe) starts clean.
                self._counts.pop(experiment_id, None)
                self._recent.pop(experiment_id, None)
                return

            recent = self._recent.setdefault(experiment_id, [])
            recent.append(image_path.stem)
            del recent[:-CHECK_WINDOW]

            count = self._counts.get(experiment_id, 0) + 1
            if count < CHECK_EVERY_N_IMAGES:
                self._counts[experiment_id] = count
                return
            self._counts[experiment_id] = 0
            self._queue.put_nowait(
                _Job(experiment_id=experiment_id, email=notify_email, image_ids=list(recent))
            )
        except Exception:  # pragma: no cover - defensive
            log.debug("could not queue image for mold watch", exc_info=True)

    # --- background worker -------------------------------------------------
    async def _run_worker(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._handle_job(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                log.exception("mold watch worker error")
            finally:
                self._queue.task_done()

    async def _handle_job(self, job: _Job) -> None:
        if job.experiment_id in self._flagged or self._runner is None:
            return
        exp = self._storage.get_experiment(job.experiment_id)
        if exp is None:
            return
        paths = [p for p in (exp.thumb_file(i) for i in job.image_ids) if p is not None]
        result = await vision.check_frames_for_anomalies(self._config, paths)
        if not result.mold_confirmed:
            return

        self._flagged.add(job.experiment_id)
        detail = (
            f"Possible mold: {result.mold_frame_count}/{result.frames_checked} "
            f"recent frames flagged. {result.summary}"
        )
        exp.append_event(f"issue detected: {detail}")
        await self._runner.mark_issue_detected(job.experiment_id, detail)
        send_email(
            job.email,
            subject=f"RaPiD-boxes: possible issue detected in {job.experiment_id}",
            body=detail,
        )
