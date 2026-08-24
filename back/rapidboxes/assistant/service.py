"""AssistantService: the local QA chat model (Ollama) plus the one piece of
"tool use" it's allowed -- resolving a natural-language request like "start
like Ivan's run from yesterday" into a concrete ExperimentProposal built from
that run's own saved config. The model itself never invents parameter values
and never calls the start API; it only ever gets to point at a real past
experiment, which this service then reads verbatim from disk.

Chat is only ever reachable while idle (see api/assistant.py, gated on
runner.status.state), and is immediately cut short -- generation cancelled,
model unloaded, transcript archived -- the moment an experiment starts (see
interrupt_and_archive(), called from api/experiments.py). That keeps the
model from ever competing with an active run for this Pi's limited RAM/CPU.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import httpx

from .. import config_xml
from ..config import AppConfig
from ..models import (
    AssistantChatResponse,
    AssistantMessage,
    ExperimentProposal,
    SavedExperimentConfig,
)
from ..storage import ExperimentDir, Storage

log = logging.getLogger("rapidboxes.assistant")

# How long Ollama keeps the model resident in RAM after the last request.
# Shared by normal chat calls and the wake-time warm-up call below so a
# freshly-woken model doesn't get evicted before the user finishes typing
# their first real message. interrupt_and_archive() overrides this to 0
# (immediate unload) the moment a real experiment starts, regardless.
_KEEP_ALIVE = "10m"

# Generous: covers systemd service start (~1-2s) plus cold model load into
# RAM (~19s measured for qwen2.5:1.5b on this Pi 5's CPU), with headroom.
_WAKE_TIMEOUT_S = 35.0
_WAKE_POLL_INTERVAL_S = 0.5


class AssistantUnavailable(Exception):
    """The local Ollama model could not be reached or errored a request.

    Distinct from a normal chat reply so api/assistant.py can turn it into a
    503 instead of the app blowing up with a raw 500 whenever Ollama is off
    (e.g. deliberately disabled per PROJECT_BRIEFING.md) or crashes.
    """

_KNOWLEDGE_PATH = Path(__file__).parent / "knowledge.md"

_ACTION_PROTOCOL = """
You can do exactly one thing beyond talking: if -- and only if -- the user is
clearly asking to start, prepare, repeat, or reuse a *past* experiment (e.g.
"start experiment like yesterday", "same settings Ivan used last time", "run
the tropism protocol Sabol did Monday"), respond with ONLY this JSON object
and nothing else, no code fence, no extra words:

{"action": "prefill_experiment", "username": "<name they mentioned, or null>", "reference": "<their own words for which run, e.g. \\"yesterday\\", \\"last tropism run\\", or null if unspecified>"}

For every other message -- questions, small talk, anything you're unsure
about -- reply normally in plain conversational text. Never mix the two:
either the raw JSON object alone, or plain text alone.
"""


def _load_knowledge() -> str:
    try:
        return _KNOWLEDGE_PATH.read_text()
    except OSError:
        log.warning("assistant knowledge.md missing at %s", _KNOWLEDGE_PATH)
        return ""


_SYSTEM_PROMPT = _load_knowledge() + "\n\n" + _ACTION_PROTOCOL


_KNOB_LABELS = [
    ("protocol", "Protocol", lambda c: c.protocol.capitalize()),
    ("darkPhaseEnabled", "Dark phase", lambda c: "enabled" if c.darkPhaseEnabled else "disabled"),
    ("darkPhaseHours", "Dark phase length", lambda c: f"{c.darkPhaseHours:g} h"),
    ("lateralIlluminationHours", "Bending (lateral light) length", lambda c: f"{c.lateralIlluminationHours:g} h"),
    ("dayLengthHours", "Day length", lambda c: f"{c.dayLengthHours:g} h"),
    ("experimentLengthDays", "Experiment length", lambda c: f"{c.experimentLengthDays:g} days"),
    ("spectra", "Spectra", lambda c: ", ".join(c.spectra) if c.spectra else "(none)"),
    ("intervalMinutes", "Capture interval", lambda c: f"every {c.intervalMinutes:g} min"),
    ("intensity", "Light intensity", lambda c: f"{c.intensity}%"),
    ("dayIntensity", "Day light intensity", lambda c: f"{c.dayIntensity}%"),
    ("photoIlluminationSource", "Photo light source", lambda c: c.photoIlluminationSource.upper()),
]
# Fields that only make sense for one protocol -- skipped for the other so the
# summary never shows an irrelevant knob.
_TROPISM_ONLY = {"darkPhaseEnabled", "darkPhaseHours", "lateralIlluminationHours"}
_GROWTH_ONLY = {"dayLengthHours", "experimentLengthDays", "dayIntensity"}


def format_config_knobs(config: SavedExperimentConfig) -> str:
    """Every configurable knob, one per line, always with its unit spelled
    out -- used both for the chat proposal's summary text and the SSH CLI's
    confirmation screen. Deliberately never abbreviates a unit: this is the
    text a human (or a much smaller model) makes a real go/no-go call from."""
    skip = _GROWTH_ONLY if config.protocol == "tropism" else _TROPISM_ONLY
    lines = []
    for field, label, fmt in _KNOB_LABELS:
        if field in skip:
            continue
        lines.append(f"{label}: {fmt(config)}")
    return "\n".join(lines)


class AssistantService:
    def __init__(self, config: AppConfig, storage: Storage):
        self._config = config
        self._storage = storage
        # Owns creating its own archive dir, same as Storage/settings_store do
        # for theirs -- AppConfig.ensure_dirs() only runs on the get_config()
        # singleton path, not for an AppConfig built directly (e.g. tests).
        config.assistant_archive_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(base_url=config.assistant_ollama_url, timeout=120.0)
        self._lock = asyncio.Lock()
        self._transcript: List[AssistantMessage] = []
        self._task: Optional[asyncio.Task] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- wake-on-demand ------------------------------------------------------
    async def ensure_awake(self) -> None:
        """Starts the local Ollama systemd service if it isn't already
        running, then pre-loads the configured model, so the chat window's
        loading screen (not the user's first message) absorbs the cold-start
        cost. Ollama is deliberately not autostarted at boot on this 4GB Pi
        -- see PROJECT_BRIEFING.md's reboot incident -- so this is the one
        path that ever starts it, and only in direct response to a human
        tapping the QA Assistant button (see api/assistant.py's /wake,
        gated the same idle-only way as chat)."""
        if not await self._ollama_reachable():
            await self._start_ollama_service()
            deadline = asyncio.get_event_loop().time() + _WAKE_TIMEOUT_S
            while not await self._ollama_reachable():
                if asyncio.get_event_loop().time() > deadline:
                    raise AssistantUnavailable(
                        "the assistant service didn't start in time -- try again"
                    )
                await asyncio.sleep(_WAKE_POLL_INTERVAL_S)
        await self._warm_model()

    async def _ollama_reachable(self) -> bool:
        try:
            res = await self._client.get("/api/tags", timeout=2.0)
            return res.status_code == 200
        except httpx.HTTPError:
            return False

    async def _start_ollama_service(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo",
                "systemctl",
                "start",
                "ollama",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except FileNotFoundError:
            # No systemd here (e.g. a dev laptop) -- fall through to the
            # poll loop, which succeeds if Ollama is already running some
            # other way, or times out with a clear message if not.
            log.warning("no systemctl on this host; assuming Ollama is started some other way")

    async def _warm_model(self) -> None:
        try:
            await self._client.post(
                "/api/generate",
                json={"model": self._config.assistant_model, "prompt": "", "stream": False, "keep_alive": _KEEP_ALIVE},
                timeout=_WAKE_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            log.warning("assistant warm-up call failed: %s", exc)
            raise AssistantUnavailable("the assistant model failed to load -- try again") from exc

    # --- chat --------------------------------------------------------------
    async def chat(
        self, message: str, history: List[AssistantMessage], username: Optional[str]
    ) -> AssistantChatResponse:
        async with self._lock:
            self._transcript.append(AssistantMessage(role="user", content=message))
            messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
            messages += [{"role": m.role, "content": m.content} for m in history]
            messages.append({"role": "user", "content": message})

            self._task = asyncio.current_task()
            try:
                raw = await self._call_ollama(messages)
            except asyncio.CancelledError:
                raise
            except httpx.HTTPError as exc:
                log.warning("assistant model call failed: %s", exc)
                raise AssistantUnavailable(
                    "the local assistant model isn't reachable right now"
                ) from exc
            finally:
                self._task = None

            proposal = self._try_resolve_action(raw, username)
            reply = proposal[1] if proposal else raw
            self._transcript.append(AssistantMessage(role="assistant", content=reply))
            return AssistantChatResponse(reply=reply, proposal=proposal[0] if proposal else None)

    async def _call_ollama(self, messages: list) -> str:
        res = await self._client.post(
            "/api/chat",
            json={
                "model": self._config.assistant_model,
                "messages": messages,
                "stream": False,
                # Keep it warm for a normal chat session's pacing, but
                # interrupt_and_archive() forces it out immediately the
                # moment an experiment starts, regardless of this.
                "keep_alive": _KEEP_ALIVE,
            },
        )
        res.raise_for_status()
        return res.json()["message"]["content"].strip()

    def _try_resolve_action(
        self, raw: str, requesting_username: Optional[str]
    ) -> Optional[tuple[Optional[ExperimentProposal], str]]:
        """Returns (proposal_or_None, reply_text) if `raw` was the
        prefill_experiment action JSON, else None (meaning: treat raw as an
        ordinary chat reply)."""
        stripped = raw.strip()
        if not stripped.startswith("{"):
            return None
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return None
        if not isinstance(parsed, dict) or parsed.get("action") != "prefill_experiment":
            return None

        target_user = (parsed.get("username") or requesting_username or "").strip().lower()
        reference = (parsed.get("reference") or "").strip().lower()
        match = self._find_experiment(target_user, reference)
        if match is None:
            who = f" for {target_user}" if target_user else ""
            return None, (
                f"I couldn't find a past experiment{who} matching \"{reference or 'that'}\". "
                "Try naming the user or being more specific about which run."
            )

        exp, saved = match
        summary = (
            f"Found {exp.experiment_id} ({exp.username() or 'unknown user'}). "
            f"Proposed settings:\n{format_config_knobs(saved)}\n\n"
            "Review it on the setup screen and press Start yourself when it looks right -- "
            "I never start experiments on my own."
        )
        proposal = ExperimentProposal(
            experimentId=exp.experiment_id,
            protocol=saved.protocol,
            sourceUsername=exp.username() or "",
            summary=summary,
            config=saved,
        )
        return proposal, summary

    def _find_experiment(
        self, target_user: str, reference: str
    ) -> Optional[tuple[ExperimentDir, SavedExperimentConfig]]:
        candidates = []
        for d in self._storage.list_experiments():
            exp = ExperimentDir(d)
            if target_user and (exp.username() or "").strip().lower() != target_user:
                continue
            meta = exp.read_metadata() or {}
            started = meta.get("startedAt")
            try:
                started_dt = datetime.fromisoformat(started) if started else None
            except ValueError:
                started_dt = None
            candidates.append((exp, started_dt))

        if "growth" in reference:
            candidates = [c for c in candidates if "growth" in c[0].experiment_id]
        elif "tropism" in reference:
            candidates = [c for c in candidates if "tropism" in c[0].experiment_id]

        if "yesterday" in reference:
            target_date = (datetime.now() - timedelta(days=1)).date()
            candidates = [c for c in candidates if c[1] and c[1].date() == target_date]
        elif "today" in reference:
            target_date = datetime.now().date()
            candidates = [c for c in candidates if c[1] and c[1].date() == target_date]

        candidates = [c for c in candidates if c[1] is not None]
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[1], reverse=True)
        exp = candidates[0][0]

        data = exp.read_config_xml()
        if data is None:
            return None
        try:
            saved = config_xml.parse(data)
        except Exception:
            log.warning("could not parse saved config for %s", exp.experiment_id)
            return None
        return exp, saved

    # --- interrupt / archive -------------------------------------------------
    async def interrupt_and_archive(self, reason: str) -> None:
        """Called the moment an experiment starts: cancel any in-flight
        generation, force Ollama to unload the model right away (rather than
        trusting its default idle keep_alive), and archive whatever was said
        so far so it isn't silently lost -- then clear it for the next
        conversation."""
        if self._task is not None and not self._task.done():
            self._task.cancel()

        try:
            await self._client.post(
                "/api/generate",
                json={"model": self._config.assistant_model, "keep_alive": 0},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            log.warning("could not unload assistant model: %s", exc)

        if self._transcript:
            self._archive(reason)
        self._transcript = []

    def _archive(self, reason: str) -> None:
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        path = self._config.assistant_archive_dir / f"{ts}_{reason}.json"
        try:
            path.write_text(
                json.dumps(
                    {
                        "reason": reason,
                        "archivedAt": datetime.now().isoformat(),
                        "messages": [m.model_dump() for m in self._transcript],
                    },
                    indent=2,
                )
            )
        except OSError:
            log.exception("could not archive assistant transcript")
