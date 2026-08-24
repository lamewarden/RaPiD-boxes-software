"""One-shot LLM helpers: text and vision calls that don't need a persistent
chat session. Shared by the interactive check_my_images tool
(assistant/service.py) and the end-of-experiment summary
(assistant/summary.py) -- both need "sample a few frames, ask about
anomalies, count per-frame mold verdicts" or a plain text summary, without
AssistantService's chat history/tool-calling machinery.

Mold specifically requires MOLD_CONFIRM_THRESHOLD frames to be individually
marked MOLD before being reported as confirmed -- never trust a single
convincing frame, and never trust the model's own holistic yes/no without
recomputing the count from its own per-frame answers.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import httpx

from ..config import AppConfig
from ..storage import ExperimentDir

log = logging.getLogger("rapidboxes.assistant.vision")

MOLD_CONFIRM_THRESHOLD = 3
MAX_SAMPLE_FRAMES = 5

_FRAME_RE = re.compile(r"frame\s+(\d+)\s*:\s*(MOLD|CLEAN)", re.IGNORECASE)
_SUMMARY_RE = re.compile(r"SUMMARY:\s*(.+)", re.IGNORECASE | re.DOTALL)


async def call_llm(
    config: AppConfig, model: str, prompt: str, image_paths: Optional[List[Path]] = None
) -> str:
    """One-shot call against the OpenAI-compatible gateway -- its own
    short-lived client, not AssistantService's persistent one, since this
    isn't an interactive chat session."""
    content: list = [{"type": "text", "text": prompt}]
    for p in image_paths or []:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        b64 = base64.b64encode(data).decode()
        ext = (p.suffix.lstrip(".") or "jpeg").lower()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}})

    async with httpx.AsyncClient(
        base_url=config.assistant_api_base_url,
        headers={"Authorization": f"Bearer {config.assistant_api_key}"},
        timeout=60.0,
    ) as client:
        res = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "stream": False,
            },
        )
        res.raise_for_status()
        return res.json()["choices"][0]["message"].get("content") or ""


@dataclass
class AnomalyCheckResult:
    mold_confirmed: bool
    mold_frame_count: int
    frames_checked: int
    summary: str


def sample_image_paths(exp: ExperimentDir, max_frames: int = MAX_SAMPLE_FRAMES) -> List[Path]:
    """Evenly-spaced sample of an experiment's own capture thumbnails (cheap
    320x240 JPEGs -- the size real vision testing was run against), always
    including the first and last frame."""
    images = exp.list_capture_images()
    if not images:
        return []
    if len(images) <= max_frames:
        chosen = images
    else:
        step = (len(images) - 1) / (max_frames - 1)
        indices = sorted({round(i * step) for i in range(max_frames)})
        chosen = [images[i] for i in indices]

    paths = []
    for img in chosen:
        thumb = exp.thumb_file(img["id"])
        if thumb is not None:
            paths.append(thumb)
    return paths


async def check_frames_for_anomalies(
    config: AppConfig, image_paths: List[Path]
) -> AnomalyCheckResult:
    """Sends every sampled frame in one call, asking for a per-frame mold
    verdict plus a general note (lighting, contamination, dislodged plate).
    Never raises -- a failed check reports itself as unconfirmed with an
    explanatory summary rather than propagating."""
    if not image_paths:
        return AnomalyCheckResult(False, 0, 0, "No images available to check.")

    prompt = (
        f"You are shown {len(image_paths)} photos from a plant-imaging chamber, "
        f"labeled frame 0 through frame {len(image_paths) - 1}, in that order. "
        "For EACH frame, on its own line, write exactly \"frame <i>: MOLD\" or "
        "\"frame <i>: CLEAN\" -- MOLD means you see mold or fungal growth on "
        "the plate/plant/surfaces in that specific frame, CLEAN means you "
        "don't. After listing every frame, add one final line starting with "
        "\"SUMMARY:\" giving a one-sentence overall note covering anything "
        "else unusual too (lighting problems, contamination, a dislodged "
        "plate) -- not just mold."
    )

    try:
        raw = await call_llm(config, config.assistant_vision_model, prompt, image_paths)
    except httpx.HTTPError as exc:
        log.warning("vision anomaly check failed: %s", exc)
        return AnomalyCheckResult(False, 0, len(image_paths), "Could not run the image check right now.")

    mold_count = sum(
        1 for m in _FRAME_RE.finditer(raw) if m.group(2).upper() == "MOLD"
    )
    summary_match = _SUMMARY_RE.search(raw)
    summary = summary_match.group(1).strip() if summary_match else raw.strip()

    return AnomalyCheckResult(
        mold_confirmed=mold_count >= MOLD_CONFIRM_THRESHOLD,
        mold_frame_count=mold_count,
        frames_checked=len(image_paths),
        summary=summary,
    )
