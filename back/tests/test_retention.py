"""Unit tests for storage lifecycle policy: size estimates and cleanup."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from rapidboxes.models import CameraSettings, GrowthConfig
from rapidboxes.retention import (
    RETENTION_DAYS,
    cleanup_expired_experiments,
    estimate_experiment_bytes,
    experiments_near_expiration,
    suggest_deletions_for_space,
)
from rapidboxes.storage import ExperimentDir, Storage


def _make_experiment(root: Path, days_old: int, username: str, name: str, *, payload_bytes: int = 0) -> str:
    """Create a fake experiment folder aged `days_old` (relative to 2026-07-24,
    this suite's fixed `today`), owned by `username` via the folder-name
    convention (no metadata.json needed)."""
    started = date(2026, 7, 24) - timedelta(days=days_old)
    exp_id = f"{started.isoformat()}_{username}_{name}"
    d = root / exp_id
    d.mkdir(parents=True)
    exp = ExperimentDir(d)  # also creates thumbs/
    if payload_bytes:
        (d / "dark_00000.png").write_bytes(b"\x00" * payload_bytes)
    return exp.experiment_id


TODAY = date(2026, 7, 24)


def test_estimate_scales_with_resolution_count_and_color():
    small_gray = CameraSettings(width=640, height=480, grayscale=True)
    big_gray = CameraSettings(width=2304, height=1296, grayscale=True)
    big_color = CameraSettings(width=2304, height=1296, grayscale=False)
    config = GrowthConfig(dayLengthHours=16, experimentLengthDays=2, intervalMinutes=240)

    small = estimate_experiment_bytes(config, small_gray)
    big = estimate_experiment_bytes(config, big_gray)
    color = estimate_experiment_bytes(config, big_color)

    assert small < big  # more pixels -> bigger estimate
    assert big < color  # color -> bigger estimate than grayscale
    assert estimate_experiment_bytes(config, big_gray) > 0

    longer = GrowthConfig(dayLengthHours=16, experimentLengthDays=10, intervalMinutes=240)
    assert estimate_experiment_bytes(longer, big_gray) > big  # more planned images -> bigger estimate


def test_experiments_near_expiration_window_and_ownership(tmp_path):
    storage = Storage(tmp_path)
    # Alice: one well within the window, one outside it on each side, one already-expired.
    soon_id = _make_experiment(tmp_path, RETENTION_DAYS - 10, "alice", "soon")  # 10d left
    fresh_id = _make_experiment(tmp_path, 5, "alice", "fresh")  # 85d left -> excluded
    expired_id = _make_experiment(tmp_path, RETENTION_DAYS + 5, "alice", "expired")  # already past -> excluded
    # Bob's folder is also near expiration, but must never show up for Alice.
    _make_experiment(tmp_path, RETENTION_DAYS - 2, "bob", "bobs")

    near = experiments_near_expiration(storage, "alice", today=TODAY)
    near_ids = {e["id"] for e in near}

    assert soon_id in near_ids
    assert fresh_id not in near_ids
    assert expired_id not in near_ids
    assert all("bob" not in i for i in near_ids)


def test_cleanup_expired_experiments_deletes_only_old_ones(tmp_path):
    storage = Storage(tmp_path)
    old_id = _make_experiment(tmp_path, RETENTION_DAYS + 1, "alice", "old")
    boundary_id = _make_experiment(tmp_path, RETENTION_DAYS, "alice", "boundary")
    young_id = _make_experiment(tmp_path, RETENTION_DAYS - 1, "alice", "young")
    # A folder whose name doesn't start with a parseable date must be left alone, not crash the sweep.
    garbage = tmp_path / "not-a-date-folder"
    garbage.mkdir()
    ExperimentDir(garbage)

    deleted = cleanup_expired_experiments(storage, today=TODAY)

    assert set(deleted) == {old_id, boundary_id}
    assert not (tmp_path / old_id).exists()
    assert not (tmp_path / boundary_id).exists()
    assert (tmp_path / young_id).exists()
    assert garbage.exists()


def test_cleanup_expired_experiments_skips_excluded_id(tmp_path):
    storage = Storage(tmp_path)
    old_id = _make_experiment(tmp_path, RETENTION_DAYS + 1, "alice", "old")

    deleted = cleanup_expired_experiments(storage, exclude_id=old_id, today=TODAY)

    assert deleted == []
    assert (tmp_path / old_id).exists()


def test_suggest_deletions_scoped_to_owner_oldest_first(tmp_path):
    storage = Storage(tmp_path)
    # Alice has three folders of known size, oldest first should be picked.
    oldest = _make_experiment(tmp_path, 30, "alice", "oldest", payload_bytes=1000)
    middle = _make_experiment(tmp_path, 20, "alice", "middle", payload_bytes=1000)
    _make_experiment(tmp_path, 10, "alice", "newest", payload_bytes=1000)
    # Bob's folder is bigger and older than all of Alice's, but must never be suggested.
    _make_experiment(tmp_path, 60, "bob", "huge", payload_bytes=100_000)

    # Shortfall covered by the single oldest folder alone.
    suggestion = suggest_deletions_for_space(
        storage, "alice", needed_bytes=1500, available_bytes=1000
    )
    assert suggestion is not None
    assert suggestion.experimentIds == [oldest]

    # Shortfall requires two folders.
    suggestion2 = suggest_deletions_for_space(
        storage, "alice", needed_bytes=2500, available_bytes=1000
    )
    assert suggestion2.experimentIds == [oldest, middle]
    assert all("bob" not in i for i in suggestion2.experimentIds)


def test_suggest_deletions_none_when_fits_or_insufficient(tmp_path):
    storage = Storage(tmp_path)
    _make_experiment(tmp_path, 30, "alice", "only", payload_bytes=1000)

    # Already fits.
    assert suggest_deletions_for_space(storage, "alice", needed_bytes=500, available_bytes=1000) is None

    # Deleting everything Alice owns still wouldn't be enough.
    assert (
        suggest_deletions_for_space(storage, "alice", needed_bytes=1_000_000, available_bytes=0)
        is None
    )

    # No experiments at all for this user.
    assert suggest_deletions_for_space(storage, "nobody", needed_bytes=100, available_bytes=0) is None
