"""Tests for full-resolution plant-shape mask from cumulative motion."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rapidboxes.plant_mask import (
    compute_plant_mask,
    ensure_plant_mask,
    read_plant_mask_progress,
    start_plant_mask_job,
)


def _save_gray(path: Path, arr: np.ndarray) -> None:
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="L").save(path, "PNG")


def _growing_root_sequence(tmp: Path, n: int = 10, size: int = 96) -> list[Path]:
    """Bright disk advances — cumulative motion should cover the path."""
    rng = np.random.default_rng(2)
    bg = 40 + rng.normal(0, 2, size=(size, size))
    paths: list[Path] = []
    for i in range(n):
        frame = bg + i * 0.5
        cx = 15 + i * 6
        cy = size // 2
        yy, xx = np.ogrid[:size, :size]
        disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= 6**2
        frame = frame.copy()
        frame[disk] = 210
        p = tmp / f"day_{i:05d}.png"
        _save_gray(p, frame)
        paths.append(p)
    return paths


def test_cumulative_mask_covers_trajectory(tmp_path: Path):
    paths = _growing_root_sequence(tmp_path)
    mask, last = compute_plant_mask(paths)
    assert mask.shape == last.shape
    # Path spans roughly x=15..15+9*6 — expect substantial coverage in the mid band
    mid = mask[mask.shape[0] // 2 - 8 : mask.shape[0] // 2 + 8, 15:70]
    assert float(mid.mean()) > 0.15
    # Corners (static) mostly empty
    corner = mask[:12, :12]
    assert float(corner.mean()) < 0.2


def test_ensure_saves_mask_and_overlay(tmp_path: Path):
    paths = _growing_root_sequence(tmp_path, n=6, size=64)
    ids = [p.stem for p in paths]
    mask_path, overlay_path = ensure_plant_mask(tmp_path, ids)
    assert mask_path.is_file()
    assert overlay_path.is_file()
    mask = np.asarray(Image.open(mask_path))
    assert mask.ndim == 2
    assert set(np.unique(mask)).issubset({0, 255})


def test_start_job_completes(tmp_path: Path):
    paths = _growing_root_sequence(tmp_path, n=5, size=48)
    ids = [p.stem for p in paths]
    started = start_plant_mask_job(tmp_path, ids)
    assert started.get("status") in ("running", "done")
    deadline = time.time() + 30
    status = started
    while status.get("status") == "running" and time.time() < deadline:
        time.sleep(0.1)
        status = read_plant_mask_progress(tmp_path)
    assert status.get("status") == "done"
    assert (tmp_path / "plant_mask.png").is_file()
