"""Growth-dynamics heatmap: temporal change relative to a slow-adapting background.

Designed to highlight plant growth while suppressing:
  - global LED / exposure flicker (spatial high-pass)
  - soft lighting gradients (same)
  - slow agar / medium drift (low learning-rate background)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageFilter

# Processing resolution (max side). Keeps Pi compute bounded.
MAX_SIDE = 640
# Spatial high-pass blur sigma (pixels at processing resolution).
HIGHPASS_SIGMA = 25.0
# Frames used to seed the background median.
BG_SEED_FRAMES = 5
# Background learning rate on "stable" pixels (higher → noise absorbed faster).
BG_ALPHA = 0.05
# Residual below this fraction of the robust scale is treated as stable (for B update).
STABLE_K = 2.0
# Change mask: keep residuals above median + MAD_K * MAD (raised vs v1 to cut salt noise).
MAD_K = 7.5
# Absolute residual floor after high-pass (gray-level units); ignores tiny flicker.
MIN_RESIDUAL = 12.0
# Mild blur on residual before threshold — suppresses single-pixel shot noise.
RESIDUAL_SMOOTH_SIGMA = 1.5
# Morphological open passes after threshold (each pass removes ~1px speckles).
OPEN_ITERS = 2
# Soft blend strength of heatmap over the base frame.
HEAT_ALPHA = 0.72
# Bump when the algorithm changes so disk caches recompute.
ALGO_VERSION = 2

GROWTH_JPG = "growth.jpg"
GROWTH_META = "growth.meta.json"

# Cool (old) → hot (recent): dark blue → cyan → yellow → red.
_COLORMAP = np.array(
    [
        [0.05, 0.05, 0.45],
        [0.00, 0.65, 0.95],
        [0.95, 0.90, 0.10],
        [1.00, 0.15, 0.05],
    ],
    dtype=np.float32,
)


def _blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur for grayscale float arrays in roughly 0..255 range."""
    if sigma <= 0:
        return img.astype(np.float32, copy=True)
    # Pillow radius ≈ sigma for our purposes; clamp for tiny images.
    radius = max(1, int(round(sigma)))
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), mode="L")
    return np.asarray(pil.filter(ImageFilter.GaussianBlur(radius=radius)), dtype=np.float32)


def _high_pass(img: np.ndarray, sigma: float = HIGHPASS_SIGMA) -> np.ndarray:
    return img.astype(np.float32) - _blur(img, sigma)


def _dilate3(mask: np.ndarray) -> np.ndarray:
    """3×3 binary dilation via max-filter (no OpenCV)."""
    m = mask.astype(np.uint8)
    padded = np.pad(m, 1, mode="constant", constant_values=0)
    h, w = m.shape
    out = np.zeros_like(m)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            y0, x0 = 1 + dy, 1 + dx
            out = np.maximum(out, padded[y0 : y0 + h, x0 : x0 + w])
    return out.astype(bool)


def _erode3(mask: np.ndarray) -> np.ndarray:
    """3×3 binary erosion via min-filter."""
    m = mask.astype(np.uint8)
    padded = np.pad(m, 1, mode="constant", constant_values=1)
    h, w = m.shape
    out = np.ones_like(m)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            y0, x0 = 1 + dy, 1 + dx
            out = np.minimum(out, padded[y0 : y0 + h, x0 : x0 + w])
    return out.astype(bool)


def _open3(mask: np.ndarray) -> np.ndarray:
    return _dilate3(_erode3(mask))


def _clean_mask(mask: np.ndarray, opens: int = OPEN_ITERS) -> np.ndarray:
    """Remove salt-and-pepper speckles; slight dilate keeps thin plant tips."""
    m = mask
    for _ in range(max(1, opens)):
        m = _open3(m)
    return _dilate3(m)


def _mad(x: np.ndarray) -> float:
    med = float(np.median(x))
    return float(np.median(np.abs(x - med))) + 1e-6


def _change_mask(residual: np.ndarray) -> np.ndarray:
    """Threshold a residual map with floor + robust scale, then morph-clean."""
    # Smooth first so isolated noisy pixels rarely survive the cut.
    smoothed = _blur(np.clip(residual, 0, 255), RESIDUAL_SMOOTH_SIGMA)
    med = float(np.median(smoothed))
    mad = _mad(smoothed)
    thresh = max(med + MAD_K * mad, MIN_RESIDUAL)
    return _clean_mask(smoothed > thresh)


def _colormap(t: np.ndarray) -> np.ndarray:
    """Map t in [0,1] → RGB float in [0,1], shape (..., 3)."""
    t = np.clip(t, 0.0, 1.0).astype(np.float32)
    n = len(_COLORMAP) - 1
    x = t * n
    i0 = np.floor(x).astype(np.int32)
    i1 = np.minimum(i0 + 1, n)
    f = (x - i0)[..., None]
    c0 = _COLORMAP[i0]
    c1 = _COLORMAP[i1]
    return c0 * (1.0 - f) + c1 * f


def _load_gray(path: Path, size: Tuple[int, int]) -> np.ndarray:
    """Load image as float32 grayscale at the given (width, height)."""
    img = Image.open(path).convert("L")
    if img.size != size:
        img = img.resize(size, Image.Resampling.BILINEAR)
    return np.asarray(img, dtype=np.float32)


def _processing_size(first: Path) -> Tuple[int, int]:
    with Image.open(first) as im:
        w, h = im.size
    scale = min(1.0, MAX_SIDE / float(max(w, h)))
    return max(1, int(round(w * scale))), max(1, int(round(h * scale)))


def compute_growth_heatmap(frame_paths: Sequence[Path]) -> Image.Image:
    """Build a composite growth heatmap Image from ordered PNG paths.

    Requires at least 2 frames. Returns an RGB PIL Image.
    """
    if len(frame_paths) < 2:
        raise ValueError("need at least 2 frames for growth visualization")

    size = _processing_size(frame_paths[0])  # (w, h)
    wh = (size[0], size[1])

    frames: List[np.ndarray] = [_load_gray(p, wh) for p in frame_paths]
    hp = [_high_pass(f) for f in frames]

    # Leave at least one frame after the seed so short sequences still produce a mask.
    seed_n = min(BG_SEED_FRAMES, max(1, len(hp) - 1))
    bg = np.median(np.stack(hp[:seed_n], axis=0), axis=0).astype(np.float32)

    # last_change: -1 = never; else frame index of most recent significant change
    last_change = np.full(bg.shape, -1, dtype=np.int32)

    for i in range(seed_n, len(hp)):
        residual = np.abs(hp[i] - bg)
        mask = _change_mask(residual)
        last_change = np.where(mask, i, last_change)

        # Absorb low-level flicker into the background so it does not re-fire next frame.
        med = float(np.median(residual))
        mad = _mad(residual)
        stable = residual < (med + STABLE_K * mad)
        alpha = np.where(stable, BG_ALPHA, 0.0).astype(np.float32)
        bg = (1.0 - alpha) * bg + alpha * hp[i]

    n = len(frames)
    denom = max(n - 1, 1)
    age = np.where(last_change >= 0, last_change.astype(np.float32) / denom, 0.0)
    heat_rgb = _colormap(age)  # H,W,3 in 0..1
    changed = last_change >= 0

    base = frames[-1]
    base_rgb = np.stack([base, base, base], axis=-1) / 255.0
    alpha = np.where(changed, HEAT_ALPHA, 0.0)[..., None].astype(np.float32)
    out = base_rgb * (1.0 - alpha) + heat_rgb * alpha
    out_u8 = np.clip(out * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(out_u8, mode="RGB")


def _cache_fingerprint(frame_paths: Sequence[Path]) -> dict:
    latest_mtime = max((p.stat().st_mtime for p in frame_paths), default=0.0)
    return {
        "imageCount": len(frame_paths),
        "latestMtime": latest_mtime,
        "algoVersion": ALGO_VERSION,
    }


def _read_meta(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def growth_frame_paths(exp_path: Path, image_ids: Sequence[str]) -> List[Path]:
    """Resolve ordered capture PNG paths (excludes cache artifacts)."""
    out: List[Path] = []
    for image_id in image_ids:
        p = exp_path / f"{image_id}.png"
        if p.is_file():
            out.append(p)
    return out


def ensure_growth_image(exp_path: Path, image_ids: Sequence[str]) -> Path:
    """Return path to growth.jpg, recomputing when the frame set changes.

    Raises ValueError if fewer than 2 frames are available.
    """
    frames = growth_frame_paths(exp_path, image_ids)
    if len(frames) < 2:
        raise ValueError("need at least 2 frames for growth visualization")

    jpg = exp_path / GROWTH_JPG
    meta_path = exp_path / GROWTH_META
    fingerprint = _cache_fingerprint(frames)
    cached = _read_meta(meta_path)
    if (
        jpg.is_file()
        and cached is not None
        and cached.get("imageCount") == fingerprint["imageCount"]
        and abs(float(cached.get("latestMtime", -1)) - fingerprint["latestMtime"]) < 1e-6
        and cached.get("algoVersion") == ALGO_VERSION
    ):
        return jpg

    img = compute_growth_heatmap(frames)
    tmp = exp_path / f"{GROWTH_JPG}.tmp"
    img.save(tmp, "JPEG", quality=90)
    os.replace(tmp, jpg)

    meta_tmp = exp_path / f"{GROWTH_META}.tmp"
    meta_tmp.write_text(json.dumps(fingerprint))
    os.replace(meta_tmp, meta_path)
    return jpg
