"""Experiment lifecycle + history."""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse

from .. import config_xml
from ..models import (
    ExperimentConfig,
    ExperimentStatus,
    FreeSpaceRequest,
    FreeSpaceResponse,
    SavedExperimentConfig,
    StartResponse,
)
from ..storage import ExperimentDir
from .deps import AppState, get_state

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


@router.post("", response_model=StartResponse)
async def start_experiment(config: ExperimentConfig, state: AppState = Depends(get_state)):
    # This is how the backend learns who the active researcher is: the name is
    # client-side state (localStorage, see client/lib/session.ts) that arrives
    # with every experiment config. Remote sync uses it as the destination
    # subfolder, and switches itself off if it changes mid-stream.
    state.sync.note_active_researcher(config.username)
    return await state.runner.start(config, state.settings.camera)


@router.post("/free-space", response_model=FreeSpaceResponse)
async def free_space(body: FreeSpaceRequest, state: AppState = Depends(get_state)):
    """Delete experiment folders to make room for a "low_space" start retry.

    Ownership is re-checked server-side against each folder's own username
    (never trusts the client's id list), and the active run -- if any -- is
    never a candidate, so this can only ever remove the requesting user's own,
    not-currently-running experiments."""
    requested = set(body.experimentIds)
    active_id = (
        state.runner.status.experimentId
        if state.runner.status.state in ("running", "paused", "finishing")
        else None
    )
    want_user = body.username.strip().lower()

    deleted: List[str] = []
    freed = 0
    for d in state.storage.list_experiments():
        exp = ExperimentDir(d)
        if exp.experiment_id not in requested or exp.experiment_id == active_id:
            continue
        owner = exp.username()
        if not owner or owner.strip().lower() != want_user:
            continue
        size = exp.size_bytes()
        if state.storage.delete_experiment(exp.experiment_id):
            deleted.append(exp.experiment_id)
            freed += size

    available = shutil.disk_usage(state.storage.root).free
    return FreeSpaceResponse(deletedIds=deleted, freedBytes=freed, availableBytes=available)


@router.get("/current", response_model=ExperimentStatus)
async def current(state: AppState = Depends(get_state)):
    return state.runner.status


@router.post("/current/pause", response_model=ExperimentStatus)
async def pause(state: AppState = Depends(get_state)):
    await state.runner.pause()
    return state.runner.status


@router.post("/current/resume", response_model=ExperimentStatus)
async def resume(state: AppState = Depends(get_state)):
    await state.runner.resume()
    return state.runner.status


@router.post("/current/stop", response_model=ExperimentStatus)
async def stop(state: AppState = Depends(get_state)):
    await state.runner.stop()
    return state.runner.status


@router.post("/current/abort", response_model=ExperimentStatus)
async def abort(state: AppState = Depends(get_state)):
    """Stop the running experiment and delete its folder/images."""
    await state.runner.abort()
    return state.runner.status


@router.get("/history")
async def history(state: AppState = Depends(get_state)) -> List[dict]:
    out = []
    for d in state.storage.list_experiments():
        exp = ExperimentDir(d)
        meta = exp.read_metadata() or {}
        out.append(
            {
                "id": exp.experiment_id,
                "name": meta.get("experimentName"),
                "username": exp.username(),
                "startedAt": meta.get("startedAt"),
                "state": meta.get("state"),
                "imagesCaptured": meta.get(
                    "imagesCaptured", len(exp.list_capture_images())
                ),
            }
        )
    return out


@router.get("/{experiment_id}/config", response_model=SavedExperimentConfig)
async def get_config(experiment_id: str, state: AppState = Depends(get_state)):
    exp = state.storage.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    data = exp.read_config_xml()
    if data is None:
        raise HTTPException(404, "no saved config for this experiment")
    try:
        return config_xml.parse(data)
    except Exception:
        raise HTTPException(500, "could not parse saved config")


@router.get("/{experiment_id}/download")
async def download_experiment(
    experiment_id: str, background_tasks: BackgroundTasks, state: AppState = Depends(get_state)
):
    """Zip an experiment's whole folder (images + metadata.json + saved config
    XML) and hand it back as a downloadable attachment.

    An experiment can accumulate hundreds of JPEGs over a multi-day run, and
    this runs on a Pi with as little as 2GB of RAM. Building the archive in an
    `io.BytesIO()` would hold the *entire* zip in memory at once (easily
    hundreds of MB), which risks real memory pressure -- possibly enough to
    OOM a box that's unattended and mid-protocol on another experiment. There
    is plenty of disk under storage_root by comparison, so instead we stream
    the archive to a temp file on disk (bounded, constant memory regardless of
    experiment size) and hand that off to FileResponse, which streams it to
    the client in chunks. The temp file is removed by a background task once
    the response has been sent.
    """
    exp = state.storage.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")

    fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix=f"{exp.experiment_id}-")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(exp.path.rglob("*")):
                if f.is_file():
                    zf.write(f, arcname=f.relative_to(exp.path))
    except Exception:
        os.remove(tmp_path)
        raise

    background_tasks.add_task(os.remove, tmp_path)
    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"{exp.experiment_id}.zip",
        background=background_tasks,
    )
