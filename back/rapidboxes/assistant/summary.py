"""Generates a short AI summary at the end of an experiment: did it run
smoothly, any system errors, any confirmed mold. Deliberately NOT built on
AssistantService's chat/tool machinery -- that's for interactive chat with a
persistent session; this is a one-shot batch call fired from
ExperimentRunner's on_experiment_finished hook (engine/runner.py), which
must never block or fail that path. See vision.py for the shared one-shot
LLM call helpers and the mold->=3-frames confirmation rule.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import httpx

from ..config import AppConfig
from ..models import ExperimentStatus
from ..storage import ExperimentDir
from . import vision

log = logging.getLogger("rapidboxes.assistant.summary")

_SUMMARY_FILENAME = "ai_summary.json"


async def generate_and_store(config: AppConfig, exp: ExperimentDir, status: ExperimentStatus) -> None:
    """Best-effort: never raises (caller is ExperimentRunner's shutdown
    path). Writes ai_summary.json into the experiment folder on success;
    leaves no partial/stale file on failure."""
    try:
        summary = await _generate(config, exp, status)
    except Exception:
        log.exception("could not generate AI summary for %s", exp.experiment_id)
        return
    try:
        (exp.path / _SUMMARY_FILENAME).write_text(json.dumps(summary, indent=2))
    except OSError:
        log.exception("could not write AI summary for %s", exp.experiment_id)


def read_stored(exp: ExperimentDir) -> Optional[dict]:
    f = exp.path / _SUMMARY_FILENAME
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


async def _generate(config: AppConfig, exp: ExperimentDir, status: ExperimentStatus) -> dict:
    paths = vision.sample_image_paths(exp)
    anomaly = await vision.check_frames_for_anomalies(config, paths) if paths else None

    events = exp.read_events()
    protocol = status.config.protocol if status.config else "unknown"
    prompt = (
        "You are summarizing a finished plant-imaging experiment for the "
        "researcher who ran it. Facts:\n"
        f"- Protocol: {protocol}\n"
        f"- Final state: {status.state}, message: {status.message}\n"
        f"- Images captured: {status.imagesCaptured} of {status.imagesPlanned} planned\n"
        f"- Event log:\n{events or '(no events logged)'}\n\n"
        "In 2-3 sentences, tell the researcher whether the run went "
        "smoothly, and plainly call out anything concerning (errors, gaps, "
        "capture failures) visible in the event log above. Do not mention "
        "mold or image contents -- that is reported separately from a "
        "direct image check."
    )

    try:
        text_summary = await vision.call_llm(config, config.assistant_model, prompt)
    except httpx.HTTPError as exc:
        log.warning("AI summary text call failed for %s: %s", exp.experiment_id, exc)
        text_summary = "(summary unavailable -- the assistant model could not be reached)"

    ran_smoothly = status.state == "done" and "failed" not in (status.message or "").lower()

    return {
        "generatedAt": datetime.now().isoformat(),
        "ranSmoothly": ran_smoothly,
        "textSummary": text_summary.strip(),
        "moldDetected": anomaly.mold_confirmed if anomaly else False,
        "moldFrameCount": anomaly.mold_frame_count if anomaly else 0,
        "imageCheckSummary": anomaly.summary if anomaly else None,
        "framesChecked": anomaly.frames_checked if anomaly else 0,
    }
