"""Unit tests for growth-dynamics heatmap (synthetic frames)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rapidboxes.growth_viz import compute_growth_heatmap, ensure_growth_image


def _save_gray(path: Path, arr: np.ndarray) -> None:
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="L").save(path, "PNG")


def _growing_blob_sequence(tmp: Path, n: int = 12, size: int = 128) -> list[Path]:
    """Static textured background + a bright disk that advances rightward."""
    rng = np.random.default_rng(0)
    bg = 40 + rng.normal(0, 3, size=(size, size))
    paths: list[Path] = []
    for i in range(n):
        frame = bg.copy()
        # Slow agar-like global brightening (should be suppressed)
        frame += i * 0.8
        cx = 20 + i * 6
        cy = size // 2
        yy, xx = np.ogrid[:size, :size]
        disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= 8**2
        frame[disk] = 200
        p = tmp / f"day_{i:05d}.png"
        _save_gray(p, frame)
        paths.append(p)
    return paths


def _drift_only_sequence(tmp: Path, n: int = 12, size: int = 128) -> list[Path]:
    """Only slow global ramp + noise — no localized growth."""
    rng = np.random.default_rng(1)
    bg = 50 + rng.normal(0, 2, size=(size, size))
    paths: list[Path] = []
    for i in range(n):
        frame = bg + i * 1.5 + rng.normal(0, 0.5, size=bg.shape)
        p = tmp / f"day_{i:05d}.png"
        _save_gray(p, frame)
        paths.append(p)
    return paths


def test_growing_blob_marks_recent_hot_pixels(tmp_path: Path):
    paths = _growing_blob_sequence(tmp_path)
    img = compute_growth_heatmap(paths)
    arr = np.asarray(img, dtype=np.float32)
    # Hot colormap ends reddish; look at right half where late disks sit.
    right = arr[:, arr.shape[1] // 2 :, :]
    left = arr[:, : arr.shape[1] // 2, :]
    # Red channel elevated on the right (recent growth) vs left
    assert float(right[:, :, 0].mean()) > float(left[:, :, 0].mean())
    # Some pixels should be clearly colored (not pure gray base)
    chroma = np.max(arr, axis=2) - np.min(arr, axis=2)
    assert float(chroma.max()) > 30


def _noisy_static_sequence(tmp: Path, n: int = 10, size: int = 128) -> list[Path]:
    """Static scene + strong per-frame shot noise (1-min flicker stand-in)."""
    rng = np.random.default_rng(3)
    bg = 60 + rng.normal(0, 2, size=(size, size))
    paths: list[Path] = []
    for i in range(n):
        frame = bg + rng.normal(0, 6, size=bg.shape)
        p = tmp / f"day_{i:05d}.png"
        _save_gray(p, frame)
        paths.append(p)
    return paths


def test_frame_noise_does_not_light_up_heatmap(tmp_path: Path):
    paths = _noisy_static_sequence(tmp_path)
    img = compute_growth_heatmap(paths)
    arr = np.asarray(img, dtype=np.float32)
    chroma = np.max(arr, axis=2) - np.min(arr, axis=2)
    # Speckle must not paint a red-dot field across the plate
    assert float(np.percentile(chroma, 99)) < 35
    assert float((chroma > 25).mean()) < 0.02


def test_slow_global_drift_mostly_ignored(tmp_path: Path):
    paths = _drift_only_sequence(tmp_path)
    img = compute_growth_heatmap(paths)
    arr = np.asarray(img, dtype=np.float32)
    chroma = np.max(arr, axis=2) - np.min(arr, axis=2)
    # Near-grayscale composite: little false "growth" from agar/light ramp
    assert float(np.percentile(chroma, 99)) < 40


def test_needs_two_frames(tmp_path: Path):
    p = tmp_path / "day_00000.png"
    _save_gray(p, np.full((64, 64), 50, dtype=np.float32))
    with pytest.raises(ValueError, match="at least 2"):
        compute_growth_heatmap([p])


def test_ensure_growth_image_caches(tmp_path: Path):
    paths = _growing_blob_sequence(tmp_path, n=6, size=96)
    ids = [p.stem for p in paths]
    out1 = ensure_growth_image(tmp_path, ids)
    assert out1.name == "growth.jpg"
    assert out1.is_file()
    mtime1 = out1.stat().st_mtime
    out2 = ensure_growth_image(tmp_path, ids)
    assert out2 == out1
    assert out2.stat().st_mtime == mtime1
