"""Storage lifecycle policy: pre-flight size checks and automatic cleanup.

Two independent policies:
  - Retention: any experiment folder older than RETENTION_DAYS is deleted
    automatically (any user), checked at app startup and whenever a new
    experiment starts. `experiments_near_expiration` is what powers the
    advance warning shown on the progress screen.
  - Space check: before starting a new experiment, estimate its total size
    and compare to free disk space. If it doesn't fit, suggest the oldest
    folders *belonging to the user about to start* whose combined size would
    free enough room -- never another user's data on a shared device.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional, Union

from .models import CameraSettings, GrowthConfig, StorageSuggestion, TropismConfig
from .storage import ExperimentDir, Storage

log = logging.getLogger("rapidboxes.retention")

Config = Union[TropismConfig, GrowthConfig]

RETENTION_DAYS = 90
EXPIRATION_WARNING_DAYS = 30

# Estimating a lossless PNG's size from resolution alone is inherently rough
# (it depends on image content/noise), so we lean conservative on the
# per-pixel estimate and then apply SIZE_ESTIMATE_MULTIPLIER on top as a
# deliberate safety margin.
#
# GRAYSCALE_BYTES_PER_PIXEL was 1.2 (2.4 effective after the multiplier) --
# reported live as wildly overestimating: a real tropism run at 12% complete
# showed 58MB used against a 2.2GB prediction (~4.4x off), with a real
# average of 1.485MB/image at 2304x1296. That's not a one-off: three real
# grayscale experiments sampled directly off the device (2026-08-31 --
# checked while investigating this report) gave 0.20-0.81 bytes/pixel
# depending on content, at 2304x1296 and 4608x2592. 0.7 keeps the effective
# (post-multiplier) value at 1.4 bytes/pixel -- comfortably above the worst
# of those three real samples (0.81) for genuine margin, while being close
# to reality instead of ~3x over it. No real color (non-grayscale) capture
# exists anywhere on the device to ground COLOR_BYTES_PER_PIXEL the same
# way (every color-configured experiment on it either errored before
# capturing or was aborted) -- left unchanged rather than guessed.
GRAYSCALE_BYTES_PER_PIXEL = 0.7
COLOR_BYTES_PER_PIXEL = 2.2
SIZE_ESTIMATE_MULTIPLIER = 2.0


def estimate_experiment_bytes(config: Config, camera: CameraSettings) -> int:
    """Rough worst-case storage footprint for a not-yet-started experiment."""
    from .engine.runner import images_planned_for  # local: avoids a runner<->retention import cycle

    per_pixel = GRAYSCALE_BYTES_PER_PIXEL if camera.grayscale else COLOR_BYTES_PER_PIXEL
    per_image = camera.width * camera.height * per_pixel
    images = images_planned_for(config)
    return int(images * per_image * SIZE_ESTIMATE_MULTIPLIER)


def _owned_experiments(
    storage: Storage, username: str, exclude_id: Optional[str] = None
) -> List[ExperimentDir]:
    want = username.strip().lower()
    out = []
    for d in storage.list_experiments():
        exp = ExperimentDir(d)
        if exclude_id and exp.experiment_id == exclude_id:
            continue
        owner = exp.username()
        if owner and owner.strip().lower() == want:
            out.append(exp)
    return out


def suggest_deletions_for_space(
    storage: Storage,
    username: str,
    needed_bytes: int,
    available_bytes: int,
    exclude_id: Optional[str] = None,
) -> Optional[StorageSuggestion]:
    """Oldest-first folders of `username` whose combined size covers the
    shortfall. None if the estimate already fits, there's nothing of this
    user's own to delete, or deleting everything still wouldn't be enough."""
    shortfall = needed_bytes - available_bytes
    if shortfall <= 0:
        return None

    owned = _owned_experiments(storage, username, exclude_id)
    owned.sort(key=lambda e: (e.started_date() or date.min, e.experiment_id))

    ids: List[str] = []
    freed = 0
    for exp in owned:
        if freed >= shortfall:
            break
        ids.append(exp.experiment_id)
        freed += exp.size_bytes()

    if not ids or freed < shortfall:
        return None
    return StorageSuggestion(experimentIds=ids, count=len(ids), freedBytes=freed)


def experiments_near_expiration(
    storage: Storage,
    username: str,
    exclude_id: Optional[str] = None,
    today: Optional[date] = None,
) -> List[dict]:
    """This user's own folders due for automatic deletion within
    EXPIRATION_WARNING_DAYS, soonest-to-expire first."""
    today = today or date.today()
    out = []
    for exp in _owned_experiments(storage, username, exclude_id):
        started = exp.started_date()
        if started is None:
            continue
        remaining = RETENTION_DAYS - (today - started).days
        if 0 <= remaining <= EXPIRATION_WARNING_DAYS:
            meta = exp.read_metadata() or {}
            out.append(
                {
                    "id": exp.experiment_id,
                    "name": meta.get("experimentName"),
                    "daysRemaining": remaining,
                }
            )
    out.sort(key=lambda e: e["daysRemaining"])
    return out


def cleanup_expired_experiments(
    storage: Storage, exclude_id: Optional[str] = None, today: Optional[date] = None
) -> List[str]:
    """Delete every experiment folder (any user) older than RETENTION_DAYS.

    Returns the ids actually deleted. Best-effort: a folder that fails to
    delete is logged and left in place rather than aborting the sweep.
    """
    today = today or date.today()
    deleted: List[str] = []
    for d in storage.list_experiments():
        exp = ExperimentDir(d)
        if exclude_id and exp.experiment_id == exclude_id:
            continue
        started = exp.started_date()
        if started is None:
            continue
        age_days = (today - started).days
        if age_days < RETENTION_DAYS:
            continue
        try:
            if storage.delete_experiment(exp.experiment_id):
                deleted.append(exp.experiment_id)
                log.info(
                    "retention: deleted expired experiment %s (age %dd)", exp.experiment_id, age_days
                )
        except Exception:
            log.exception("retention: failed to delete expired experiment %s", exp.experiment_id)
    return deleted
