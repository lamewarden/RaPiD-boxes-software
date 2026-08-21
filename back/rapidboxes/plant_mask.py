"""Plant shape from cumulative motion: roots/shoots paint their silhouette over time.

Full-resolution processing (no downsample). Writes:
  plant_mask.png       — binary bitmap (0/255)
  plant_overlay.jpg    — last frame with semitransparent mask
  plant_mask.meta.json — cache fingerprint
  plant_mask.progress.json — live progress for the UI
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from rapidboxes.growth_viz import (
    BG_ALPHA,
    BG_SEED_FRAMES,
    HIGHPASS_SIGMA,
    MAD_K,
    MAX_SIDE,
    STABLE_K,
    _cache_fingerprint,
    _dilate3,
    _erode3,
    _high_pass,
    _mad,
    _open3,
    _read_meta,
    growth_frame_paths,
)

# Morphological close iterations to fill thin gaps along root trajectories.
CLOSE_ITERS = 3
# Semitransparent overlay color (RGB) and opacity on plant pixels.
OVERLAY_RGB = (40, 220, 120)
OVERLAY_ALPHA = 0.45

PLANT_MASK_PNG = "plant_mask.png"
PLANT_OVERLAY_JPG = "plant_overlay.jpg"
PLANT_META = "plant_mask.meta.json"
PLANT_PROGRESS = "plant_mask.progress.json"

ProgressCb = Callable[[int, int, str], None]

_jobs_lock = threading.Lock()
_jobs: Dict[str, threading.Thread] = {}


def _close_n(mask: np.ndarray, iterations: int = CLOSE_ITERS) -> np.ndarray:
    """Morphological close (dilate then erode) to connect thin trajectories."""
    m = mask
    for _ in range(iterations):
        m = _dilate3(m)
    for _ in range(iterations):
        m = _erode3(m)
    return m


def _highpass_sigma(width: int, height: int) -> float:
    """Scale blur sigma with resolution (σ≈25 at 640px reference)."""
    ref = float(MAX_SIDE)
    return HIGHPASS_SIGMA * max(width, height) / ref


def _write_progress(exp_path: Path, payload: dict) -> None:
    path = exp_path / PLANT_PROGRESS
    tmp = exp_path / f"{PLANT_PROGRESS}.tmp"
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def read_plant_mask_progress(exp_path: Path) -> dict:
    data = _read_meta(exp_path / PLANT_PROGRESS)
    if data is None:
        return {"status": "idle", "current": 0, "total": 0, "message": ""}
    return data


def _native_size(path: Path) -> Tuple[int, int]:
    with Image.open(path) as im:
        return im.size  # (w, h)


def _load_gray_native(path: Path) -> np.ndarray:
    img = Image.open(path).convert("L")
    return np.asarray(img, dtype=np.float32)


def compute_plant_mask(
    frame_paths: Sequence[Path],
    progress: Optional[ProgressCb] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (binary mask HxW bool, last_frame HxW float32) at full resolution."""
    if len(frame_paths) < 2:
        raise ValueError("need at least 2 frames for plant mask")

    n = len(frame_paths)
    w, h = _native_size(frame_paths[0])
    sigma = _highpass_sigma(w, h)

    def report(i: int, msg: str) -> None:
        if progress:
            progress(i, n, msg)

    report(0, "Seeding background…")
    seed_n = min(BG_SEED_FRAMES, max(1, n - 1))
    seed_hp: List[np.ndarray] = []
    for i in range(seed_n):
        report(i + 1, f"Loading seed frame {i + 1}/{seed_n}")
        seed_hp.append(_high_pass(_load_gray_native(frame_paths[i]), sigma))

    bg = np.median(np.stack(seed_hp, axis=0), axis=0).astype(np.float32)
    del seed_hp

    cumulative = np.zeros((h, w), dtype=bool)
    last_frame = _load_gray_native(frame_paths[-1])

    for i in range(seed_n, n):
        report(i + 1, f"Frame {i + 1}/{n}")
        frame = _load_gray_native(frame_paths[i])
        hp = _high_pass(frame, sigma)
        residual = np.abs(hp - bg)
        med = float(np.median(residual))
        mad = _mad(residual)
        mask = _open3(residual > (med + MAD_K * mad))
        cumulative |= mask

        stable = residual < (med + STABLE_K * mad)
        alpha = np.where(stable, BG_ALPHA, 0.0).astype(np.float32)
        bg = (1.0 - alpha) * bg + alpha * hp

    report(n, "Closing mask…")
    cumulative = _close_n(cumulative, CLOSE_ITERS)
    return cumulative, last_frame


def _save_mask_and_overlay(exp_path: Path, mask: np.ndarray, last_frame: np.ndarray) -> None:
    mask_u8 = (mask.astype(np.uint8) * 255)
    mask_img = Image.fromarray(mask_u8, mode="L")
    tmp_mask = exp_path / f"{PLANT_MASK_PNG}.tmp"
    mask_img.save(tmp_mask, "PNG")
    os.replace(tmp_mask, exp_path / PLANT_MASK_PNG)

    base = last_frame
    if base.ndim == 2:
        base_rgb = np.stack([base, base, base], axis=-1)
    else:
        base_rgb = base.astype(np.float32)
    tint = np.array(OVERLAY_RGB, dtype=np.float32)
    alpha = np.where(mask, OVERLAY_ALPHA, 0.0)[..., None].astype(np.float32)
    out = base_rgb * (1.0 - alpha) + tint * alpha
    overlay = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), mode="RGB")
    tmp_ov = exp_path / f"{PLANT_OVERLAY_JPG}.tmp"
    overlay.save(tmp_ov, "JPEG", quality=92)
    os.replace(tmp_ov, exp_path / PLANT_OVERLAY_JPG)


def plant_mask_cache_valid(exp_path: Path, frame_paths: Sequence[Path]) -> bool:
    mask = exp_path / PLANT_MASK_PNG
    overlay = exp_path / PLANT_OVERLAY_JPG
    meta = _read_meta(exp_path / PLANT_META)
    if not (mask.is_file() and overlay.is_file() and meta is not None):
        return False
    fp = _cache_fingerprint(frame_paths)
    return (
        meta.get("imageCount") == fp["imageCount"]
        and abs(float(meta.get("latestMtime", -1)) - fp["latestMtime"]) < 1e-6
    )


def ensure_plant_mask(
    exp_path: Path,
    image_ids: Sequence[str],
    progress: Optional[ProgressCb] = None,
) -> Tuple[Path, Path]:
    """Compute (or reuse) plant_mask.png and plant_overlay.jpg. Returns their paths."""
    frames = growth_frame_paths(exp_path, image_ids)
    if len(frames) < 2:
        raise ValueError("need at least 2 frames for plant mask")

    if plant_mask_cache_valid(exp_path, frames):
        if progress:
            progress(len(frames), len(frames), "Cached")
        return exp_path / PLANT_MASK_PNG, exp_path / PLANT_OVERLAY_JPG

    mask, last = compute_plant_mask(frames, progress=progress)
    _save_mask_and_overlay(exp_path, mask, last)

    fp = _cache_fingerprint(frames)
    meta_tmp = exp_path / f"{PLANT_META}.tmp"
    meta_tmp.write_text(json.dumps(fp))
    os.replace(meta_tmp, exp_path / PLANT_META)
    return exp_path / PLANT_MASK_PNG, exp_path / PLANT_OVERLAY_JPG


def _job_worker(exp_path: Path, image_ids: List[str], exp_key: str) -> None:
    total = max(len(image_ids), 1)

    def on_progress(current: int, tot: int, message: str) -> None:
        _write_progress(
            exp_path,
            {
                "status": "running",
                "current": current,
                "total": tot,
                "message": message,
                "percent": int(100 * current / max(tot, 1)),
            },
        )

    try:
        _write_progress(
            exp_path,
            {"status": "running", "current": 0, "total": total, "message": "Starting…", "percent": 0},
        )
        ensure_plant_mask(exp_path, image_ids, progress=on_progress)
        _write_progress(
            exp_path,
            {
                "status": "done",
                "current": total,
                "total": total,
                "message": "Done",
                "percent": 100,
                "maskUrl": f"/api/images/{exp_path.name}/artifacts/plant_mask",
                "overlayUrl": f"/api/images/{exp_path.name}/artifacts/plant_overlay",
            },
        )
    except Exception as exc:
        _write_progress(
            exp_path,
            {
                "status": "error",
                "current": 0,
                "total": total,
                "message": str(exc),
                "percent": 0,
                "error": str(exc),
            },
        )
    finally:
        with _jobs_lock:
            _jobs.pop(exp_key, None)


def start_plant_mask_job(exp_path: Path, image_ids: Sequence[str]) -> dict:
    """Start background full-res segmentation if not already running / cached.

    Returns a progress-like dict immediately.
    """
    frames = growth_frame_paths(exp_path, image_ids)
    if len(frames) < 2:
        raise ValueError("need at least 2 frames for plant mask")

    exp_key = str(exp_path.resolve())

    if plant_mask_cache_valid(exp_path, frames):
        payload = {
            "status": "done",
            "current": len(frames),
            "total": len(frames),
            "message": "Cached",
            "percent": 100,
            "maskUrl": f"/api/images/{exp_path.name}/artifacts/plant_mask",
            "overlayUrl": f"/api/images/{exp_path.name}/artifacts/plant_overlay",
        }
        _write_progress(exp_path, payload)
        return payload

    with _jobs_lock:
        existing = _jobs.get(exp_key)
        if existing is not None and existing.is_alive():
            return read_plant_mask_progress(exp_path)

        _write_progress(
            exp_path,
            {
                "status": "running",
                "current": 0,
                "total": len(image_ids),
                "message": "Starting…",
                "percent": 0,
            },
        )
        t = threading.Thread(
            target=_job_worker,
            args=(exp_path, list(image_ids), exp_key),
            name=f"plant-mask-{exp_path.name}",
            daemon=True,
        )
        _jobs[exp_key] = t
        t.start()

    return read_plant_mask_progress(exp_path)
