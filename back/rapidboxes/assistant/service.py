"""AssistantService: the QA chat model, running against the institute's
OpenAI-compatible LLM gateway (e-INFRA CZ) via real tool/function calling,
plus the one piece of "tool use" it's allowed that can touch a real
experiment -- resolving a request like "start like Ivan's run from
yesterday" into a concrete ExperimentProposal built from that run's own
saved config. The model itself never invents parameter values and never
calls the start API; it only ever gets to point at a real past experiment,
which this service then reads verbatim from disk.

Chat is reachable whether an experiment is running or not -- an earlier
local-model version gated this to idle-only to avoid competing with a live
run for the Pi's RAM/CPU, but a remote API has no such contention, so
researchers can now ask about (or check on) a run while it's actually in
progress.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import httpx

from .. import config_xml, settings_store, user_defaults
from ..config import AppConfig
from ..engine.runner import ExperimentRunner
from ..models import (
    AssistantChatResponse,
    AssistantDownloadRef,
    AssistantImageRef,
    AssistantMessage,
    ExperimentProposal,
    SavedExperimentConfig,
)
from ..dsm_sharing import DsmSharingService
from ..remote_sync import RemoteSyncService
from ..storage import ExperimentDir, Storage, tally_by_user
from . import vision

log = logging.getLogger("rapidboxes.assistant")

_KNOWLEDGE_PATH = Path(__file__).parent / "knowledge.md"


class AssistantUnavailable(Exception):
    """The remote assistant API could not be reached or errored a request.

    Distinct from a normal chat reply so api/assistant.py can turn it into a
    503 instead of the app blowing up with a raw 500 whenever the gateway or
    the network is down.
    """


def _load_knowledge() -> str:
    try:
        return _KNOWLEDGE_PATH.read_text()
    except OSError:
        log.warning("assistant knowledge.md missing at %s", _KNOWLEDGE_PATH)
        return ""


_SYSTEM_PROMPT = _load_knowledge()


def _format_bytes(n: int) -> str:
    """Same thresholds/units as the frontend's formatBytes() (client/lib/
    format.ts) -- keeping these consistent matters: a researcher comparing
    what the assistant says to what the progress screen already shows for
    the same experiment should never see two different-looking numbers for
    the same quantity."""
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.1f} GB"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.0f} MB"
    return f"{round(n / 1024)} KB"


_FIRST_WORDS = {"first", "earliest", "oldest", "start", "beginning"}
_LAST_WORDS = {"last", "latest", "most recent", "newest", "final", "end"}


def _pick_image(images: List[dict], which: str) -> Optional[dict]:
    """`images` is ExperimentDir.list_capture_images()'s output, already
    sorted oldest-first. `which` is free text from the model: "first"/
    "last" (and common synonyms), an exact image id (e.g. "dark_00042"), or
    empty (defaults to the most recent, matching what someone means by
    just "show me the image" with no further qualifier)."""
    if not images:
        return None
    if not which or which in _LAST_WORDS:
        return images[-1]
    if which in _FIRST_WORDS:
        return images[0]
    for img in images:
        if img["id"].lower() == which:
            return img
    for img in images:
        if which in img["id"].lower():
            return img
    return None


# Real OpenAI-style tool definitions. Real testing across several candidate
# models on this gateway showed native tool_calls output is clean and
# reliable, unlike asking the model to emit JSON in message text (which
# needed code-fence-stripping workarounds and was still occasionally missed).
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "prefill_experiment",
            "description": (
                "Resolve a request to start, prepare, repeat, or reuse a "
                "*past* experiment (e.g. \"start experiment like yesterday\", "
                "\"same settings Ivan used last time\", \"run the tropism "
                "protocol Sabol did Monday\") into a real proposal built from "
                "that run's own saved config. Only call this for a clear "
                "start/repeat/reuse intent, never for a plain question about "
                "past experiments -- use list_experiments for that instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "name they mentioned, omit if unspecified",
                    },
                    "reference": {
                        "type": "string",
                        "description": "their own words for which run, e.g. 'yesterday', 'last tropism run'",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_experiments",
            "description": (
                "List what experiments exist, who ran what, or recent "
                "history -- for plain informational questions like \"what "
                "was my last experiment\" or \"what has Ivan run this "
                "week\", WITHOUT any intent to start or reuse one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": (
                            "whose experiments to list. For \"I\"/\"my\"/\"me\" "
                            "questions, use the real username you were told "
                            "at the start of this conversation -- never a "
                            "placeholder. For a named other person, use "
                            "their name. To see every user's experiments, "
                            "pass exactly 'all'. Omitting this defaults to "
                            "the current user, not everyone."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "how many to return, omit for a default",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_status",
            "description": (
                "Get the box's current live state right now: is an "
                "experiment running, how much storage is free, is the "
                "camera working. If an experiment is currently running, "
                "also reports its real captured-so-far storage use and the "
                "rough pre-flight estimate for its full planned size (NOT a "
                "final measured size -- use read_experiment_log for that, "
                "on a finished run)."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "my_settings",
            "description": (
                "Look up the CURRENT user's own device settings -- "
                "illumination source (IR/RGBW), LED wiring, IR pins, camera "
                "defaults, and whether they have a saved personal 'Mine' "
                "baseline. Use for questions like \"what's my illumination "
                "source\" or \"what are my camera settings\". Always scoped "
                "to whoever is chatting -- takes no arguments, never another "
                "user's settings."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "my_storage",
            "description": (
                "How much storage the CURRENT user is using in TOTAL, "
                "combined across every experiment they've ever run, plus "
                "the box's total free space. Use for questions like \"how "
                "much storage am I using overall\". For the exact size of "
                "ONE specific experiment, use read_experiment_log instead -- "
                "this tool's number is a combined total, not any single "
                "run's size. Always scoped to whoever is chatting -- takes "
                "no arguments, never another user's usage."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_experiment_log",
            "description": (
                "Read the event log AND exact on-disk size for one of the "
                "CURRENT user's own experiments -- what phases ran, capture "
                "failures, remote sync failures, crashes, and its real "
                "measured storage footprint. Use this to help explain a "
                "complaint like \"my last run had a weird gap\", \"what "
                "happened during yesterday's experiment\", or \"how big was "
                "that experiment\" (this is the only tool with an exact, "
                "measured size for one specific run). Always scoped to "
                "whoever is chatting -- can only read their own "
                "experiments, never another user's."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": {
                        "type": "string",
                        "description": "their own words for which run, e.g. 'yesterday', 'last tropism run', omit for their most recent",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_my_images",
            "description": (
                "Check a handful of images from one of the CURRENT user's "
                "own experiments for visible anomalies -- mold, "
                "contamination, lighting problems. Use for questions like "
                "\"check my images for mold\" or \"does my last run look "
                "ok\". This calls a vision model and can take several "
                "seconds. Always scoped to whoever is chatting -- can only "
                "check their own experiments."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": {
                        "type": "string",
                        "description": "their own words for which run, omit for their most recent",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_image",
            "description": (
                "Open one specific, real image from one of the CURRENT "
                "user's own experiments so they can actually see it -- use "
                "this whenever they ask to \"show\"/\"see\"/\"open\" an "
                "image, e.g. \"show me the first image from yesterday's "
                "run\" or \"show me dark_00042 from my last tropism "
                "experiment\". Always scoped to whoever is chatting -- can "
                "only show their own images, never another user's."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": {
                        "type": "string",
                        "description": "their own words for which run, e.g. 'yesterday', 'last tropism run', omit for their most recent",
                    },
                    "which": {
                        "type": "string",
                        "description": "which image: 'first', 'last', an exact image name like 'dark_00042', or omit for the most recent",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_image",
            "description": (
                "Actually look at one specific, real image from one of the "
                "CURRENT user's own experiments and describe what's in it. "
                "Use this whenever asked to \"describe\"/\"what's in\"/\"what "
                "does it look like\" for an image -- including a follow-up "
                "like \"describe it\" right after show_image was used, "
                "resolving the same image from context. show_image only "
                "opens a picture, it never looks at it -- this is the tool "
                "that actually does. Calls a vision model and can take "
                "several seconds. Always scoped to whoever is chatting -- "
                "can only describe their own images, never another user's."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": {
                        "type": "string",
                        "description": "their own words for which run, e.g. 'yesterday', 'last tropism run', omit for their most recent",
                    },
                    "which": {
                        "type": "string",
                        "description": "which image: 'first', 'last', an exact image name like 'dark_00042', or omit for the most recent",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_experiment",
            "description": (
                "Package one of the CURRENT user's own experiments as a zip "
                "for them to download or (over Telegram) receive directly. "
                "Use when asked to \"zip up\"/\"package\"/\"send me\"/"
                "\"download\" a whole experiment OR a specific range/count "
                "of its images (e.g. \"just the first three images\", "
                "\"images 5 through 10\", \"the last 5 photos\") -- for a "
                "single image use show_image instead. Omit every image-"
                "selection argument to package the whole experiment (every "
                "image plus its config), which is the default. Always "
                "scoped to whoever is chatting -- can only package their "
                "own experiments, never another user's."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": {
                        "type": "string",
                        "description": "their own words for which run, e.g. 'yesterday', 'last tropism run', omit for their most recent",
                    },
                    "firstN": {
                        "type": "integer",
                        "description": "only the first N captured images (chronological), e.g. 3 for 'the first three images'",
                    },
                    "lastN": {
                        "type": "integer",
                        "description": "only the last N captured images (chronological)",
                    },
                    "startIndex": {
                        "type": "integer",
                        "description": "1-based start of an explicit range, e.g. 'images 5 through 10' -> startIndex=5, endIndex=10. Ignored if firstN/lastN is set.",
                    },
                    "endIndex": {
                        "type": "integer",
                        "description": "1-based end of an explicit range (inclusive). Ignored if firstN/lastN is set.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upload_experiment_to_remote",
            "description": (
                "Copies one of the CURRENT user's own experiments to the "
                "connected network drive (CIFS/SMB, Settings -> General -> "
                "Remote Sync), into their own folder there, and reports "
                "back either a real clickable link (if Sharing Links is "
                "also set up) or the local network path once it's done. "
                "Only works if remote sync is currently switched on and "
                "connected -- if it isn't, say so rather than trying. Use "
                "when asked to "
                "\"upload\"/\"copy\"/\"put\" an experiment \"on the network "
                "drive\"/\"on the share\"/\"on the server\" -- for a zip to "
                "download or send via Telegram use download_experiment "
                "instead. Always scoped to whoever is chatting -- can only "
                "copy their own experiments, never another user's."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": {
                        "type": "string",
                        "description": "their own words for which run, e.g. 'yesterday', 'last tropism run', omit for their most recent",
                    },
                },
            },
        },
    },
]


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
    def __init__(
        self,
        config: AppConfig,
        storage: Storage,
        runner: Optional[ExperimentRunner] = None,
        remote_sync: Optional[RemoteSyncService] = None,
        dsm_sharing: Optional[DsmSharingService] = None,
    ):
        self._config = config
        self._storage = storage
        # Read-only-ish: the one tool that has a real, visible side effect
        # (upload_experiment_to_remote copies files to the CIFS share) goes
        # through this. Optional for the same reason `runner` is -- tests
        # that never exercise that tool don't need a real RemoteSyncService.
        self._remote_sync = remote_sync
        # A different NAS/account than remote_sync above -- see
        # dsm_sharing.py. Optional; when unset or not connected,
        # upload_experiment_to_remote still works, it just reports the local
        # network path instead of a real clickable URL.
        self._dsm_sharing = dsm_sharing
        # Read-only: current experiment status and camera availability
        # (system_status tool). Never used to start/stop/change anything --
        # see the module docstring; the assistant only ever reads the
        # runner's state, never controls it. Optional so tests that never
        # exercise system_status don't need to build a real runner.
        # Deliberately reads camera state via runner._hw at call time (below)
        # rather than storing its own hw reference -- AppState.rebuild_hardware()
        # swaps in a fresh HardwareManager on settings changes and updates
        # runner._hw, so a separately-held reference here would go stale.
        self._runner = runner
        # Owns creating its own archive dir, same as Storage/settings_store do
        # for theirs -- AppConfig.ensure_dirs() only runs on the get_config()
        # singleton path, not for an AppConfig built directly (e.g. tests).
        config.assistant_archive_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(
            base_url=config.assistant_api_base_url,
            headers={"Authorization": f"Bearer {config.assistant_api_key}"},
            timeout=60.0,
        )
        self._lock = asyncio.Lock()
        self._transcript: List[AssistantMessage] = []
        self._task: Optional[asyncio.Task] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- chat --------------------------------------------------------------
    async def chat(
        self, message: str, history: List[AssistantMessage], username: Optional[str]
    ) -> AssistantChatResponse:
        async with self._lock:
            self._transcript.append(AssistantMessage(role="user", content=message))
            # _SYSTEM_PROMPT is a module-level constant (loaded once), so the
            # requester's identity -- which varies per call -- has to be
            # appended here instead. Without this the model has no way to
            # resolve "I"/"my" to a real username at all; observed in
            # production guessing a literal placeholder like "current_user"
            # instead of leaving a tool argument to its real default.
            # Folded into the one system message, not a second one -- the
            # gateway rejects a request with more than one system-role
            # message (400 Bad Request), confirmed by testing.
            system_content = _SYSTEM_PROMPT
            if username:
                system_content += (
                    f"\n\nThe person chatting with you right now is '{username}'. "
                    "When they say \"I\", \"me\", or \"my\", they mean this exact "
                    "username -- pass it directly to a tool if it needs one, "
                    "never invent or guess a different value."
                )
            messages = [{"role": "system", "content": system_content}]
            messages += [{"role": m.role, "content": m.content} for m in history]
            messages.append({"role": "user", "content": message})

            # Tracked for the whole method (not just the initial LLM call, so
            # a slow check_my_images vision call is covered too) so an
            # explicit interrupt_and_archive() call -- currently unused, but
            # available for e.g. a future "clear conversation" action --
            # could still cancel whatever's in flight.
            self._task = asyncio.current_task()
            try:
                try:
                    result = await self._call_llm(messages)
                except asyncio.CancelledError:
                    raise
                except httpx.HTTPError as exc:
                    log.warning("assistant model call failed: %s", exc)
                    raise AssistantUnavailable(
                        "the assistant model isn't reachable right now"
                    ) from exc

                tool_calls = result.get("tool_calls") or []
                if tool_calls:
                    proposal, image, download, reply = await self._resolve_tool_call(
                        tool_calls[0], username
                    )
                else:
                    proposal, image, download, reply = None, None, None, (result.get("content") or "").strip()
            finally:
                self._task = None

            self._transcript.append(AssistantMessage(role="assistant", content=reply))
            return AssistantChatResponse(reply=reply, proposal=proposal, image=image, download=download)

    async def _call_llm(self, messages: list) -> dict:
        res = await self._client.post(
            "/chat/completions",
            json={
                "model": self._config.assistant_model,
                "messages": messages,
                "tools": _TOOLS,
                "tool_choice": "auto",
                "stream": False,
            },
        )
        res.raise_for_status()
        return res.json()["choices"][0]["message"]

    async def _resolve_tool_call(
        self, tool_call: dict, requesting_username: Optional[str]
    ) -> tuple[
        Optional[ExperimentProposal], Optional[AssistantImageRef], Optional[AssistantDownloadRef], str
    ]:
        """Dispatches one native tool_calls entry to its resolver. Only
        prefill_experiment ever carries a proposal, only show_image/
        describe_image carry an image, and only download_experiment carries
        a download -- every other tool answers with text alone. Most of
        those are read-only lookups; upload_experiment_to_remote is the one
        exception with a real side effect (it copies files to the CIFS
        share), reported back as plain text rather than a structured ref
        since there's no new local artifact to point at, unlike download's
        zip. my_settings/my_storage deliberately ignore any username
        the model might put in args (their schema takes none) and always use
        requesting_username -- unlike list_experiments/prefill_experiment,
        which may look up a named other user, these two never do."""
        name = tool_call.get("function", {}).get("name")
        try:
            args = json.loads(tool_call.get("function", {}).get("arguments") or "{}")
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}

        if name == "prefill_experiment":
            proposal, reply = self.resolve_prefill_experiment(args, requesting_username)
            return proposal, None, None, reply
        if name == "list_experiments":
            return None, None, None, self.resolve_list_experiments(args, requesting_username)
        if name == "system_status":
            return None, None, None, self.resolve_system_status()
        if name == "my_settings":
            return None, None, None, self._resolve_my_settings(requesting_username)
        if name == "my_storage":
            return None, None, None, self._resolve_my_storage(requesting_username)
        if name == "read_experiment_log":
            return None, None, None, self._resolve_read_experiment_log(args, requesting_username)
        if name == "check_my_images":
            return None, None, None, await self._resolve_check_my_images(args, requesting_username)
        if name == "show_image":
            image, reply = self._resolve_show_image(args, requesting_username)
            return None, image, None, reply
        if name == "describe_image":
            image, reply = await self._resolve_describe_image(args, requesting_username)
            return None, image, None, reply
        if name == "download_experiment":
            download, reply = self._resolve_download_experiment(args, requesting_username)
            return None, None, download, reply
        if name == "upload_experiment_to_remote":
            return None, None, None, await self._resolve_upload_to_remote(args, requesting_username)
        log.warning("model called unknown tool %r", name)
        return None, None, None, "Sorry, something went wrong handling that request."

    def resolve_prefill_experiment(
        self, args: dict, requesting_username: Optional[str]
    ) -> tuple[Optional[ExperimentProposal], str]:
        """Deterministic lookup, no model call -- public (unlike most
        _resolve_* methods) so telegram_link.py's /launch command can call
        it directly, the same "skill = a plain script, not a full LLM
        round-trip" precedent as /status and /experiments there."""
        target_user = (args.get("username") or requesting_username or "").strip().lower()
        reference = (args.get("reference") or "").strip().lower()
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

    def _find_experiment_dir(self, target_user: str, reference: str) -> Optional[ExperimentDir]:
        """Most-recent experiment matching an optional username + free-text
        reference ("yesterday", "last tropism run", ...). Shared by
        prefill_experiment (which additionally needs the saved config) and
        read_experiment_log (which only needs the folder, not a config)."""
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
        return candidates[0][0]

    def _find_experiment(
        self, target_user: str, reference: str
    ) -> Optional[tuple[ExperimentDir, SavedExperimentConfig]]:
        exp = self._find_experiment_dir(target_user, reference)
        if exp is None:
            return None

        data = exp.read_config_xml()
        if data is None:
            return None
        try:
            saved = config_xml.parse(data)
        except Exception:
            log.warning("could not parse saved config for %s", exp.experiment_id)
            return None
        return exp, saved

    def resolve_list_experiments(self, args: dict, requesting_username: Optional[str]) -> str:
        """Read-only listing, most-recently-modified first (same ordering
        Storage.list_experiments() already uses for Gallery/history). Public
        (unlike most _resolve_* methods) so telegram_link.py's /experiments
        command can call it directly -- no model round-trip needed for
        something this deterministic.

        Naming nobody defaults to the CURRENT user (matches what "which
        experiment did I conduct" actually expects), not everyone -- an
        earlier version defaulted to "every user" to mirror Gallery/Import's
        shared visibility, but real usage showed that reads as "wrong user"
        for the much more common first-person question. Passing exactly
        'all'/'everyone'/'everybody' still gets the full shared listing;
        naming someone else still works as before (this box is shared, so
        that's still allowed, unlike the strictly-self-only tools)."""
        raw = (args.get("username") or "").strip()
        if not raw:
            target_user = requesting_username.strip().lower() if requesting_username else None
        elif raw.lower() in ("all", "everyone", "everybody"):
            target_user = None
        else:
            target_user = raw.lower()
        limit = args.get("limit")
        if not isinstance(limit, int) or limit <= 0:
            limit = 5
        limit = min(limit, 20)  # keep replies short on this small kiosk screen

        rows = []
        for d in self._storage.list_experiments():
            exp = ExperimentDir(d)
            username = exp.username() or "unknown"
            if target_user and username.strip().lower() != target_user:
                continue
            meta = exp.read_metadata() or {}
            protocol = (meta.get("config") or {}).get("protocol", "?")
            started = exp.started_date()
            rows.append((exp.experiment_id, username, protocol, started))
            if len(rows) >= limit:
                break

        if not rows:
            who = f" for {target_user}" if target_user else ""
            return f"No experiments found{who}."

        who = f" for {target_user}" if target_user else " (all users)"
        lines = [f"Last {len(rows)} experiment(s){who}:"]
        for experiment_id, username, protocol, started in rows:
            date_str = started.isoformat() if started else "unknown date"
            lines.append(f"- {experiment_id} — {username} — {protocol} — {date_str}")
        return "\n".join(lines)

    def resolve_system_status(self) -> str:
        """Read-only snapshot of live device state -- current experiment (if
        any), free storage, camera. Never anything that changes state; see
        the module docstring for why that boundary is deliberate. Public
        (unlike most _resolve_* methods) so telegram_link.py's /status
        command can call it directly -- deliberately device-wide, not
        scoped to whoever's asking, unlike every other Telegram command."""
        if self._runner is None:
            return "System status isn't available right now."

        status = self._runner.status
        if status.state == "idle":
            running = "No experiment is currently running."
        else:
            # .value explicitly: str-mixin Enums changed their __str__/
            # __format__ behavior in Python 3.11+ (prints "ClassName.member"
            # instead of the plain value) -- invisible on a 3.9 dev venv,
            # live on the Pi's 3.13, caught by testing against the real
            # device.
            phase = status.phase.value if status.phase else "none"
            running = (
                f"Experiment '{status.experimentId}' ({status.username}) is {status.state.value}, "
                f"phase {phase}, {status.imagesCaptured}/{status.imagesPlanned} images captured."
            )
            # recoveryNotice is set once, right after a crash/power-loss/
            # reboot recovery, and stays on the live status until this
            # experiment finishes -- surface it here so "was it interrupted"
            # gets a real answer (outage length + images missed) instead of
            # silence, which is exactly the info a recovered run's own
            # events.log line ("recovered: ...") also carries for later
            # lookup via read_experiment_log.
            if status.recoveryNotice is not None:
                running += f"\n{status.recoveryNotice.message}"
            # bytesUsed is real, measured so far; estimatedTotalBytes is a
            # worst-case pre-flight guess for the *full planned* run, fixed
            # at start and never updated as captures happen or if the run
            # stops early -- labeled explicitly as an estimate so the model
            # never states it as a precise final size (a real gap that used
            # to leave the model guessing when asked "how big is this
            # experiment" about a still-running run).
            running += f"\nStorage used by this experiment so far: {_format_bytes(status.bytesUsed)}"
            if status.estimatedTotalBytes is not None:
                running += (
                    f" (rough pre-flight estimate for the full planned run: "
                    f"~{_format_bytes(status.estimatedTotalBytes)} -- not a measured final size, "
                    f"and won't shrink if the run ends early)."
                )

        usage = shutil.disk_usage(self._config.storage_root)
        camera = "available" if self._runner._hw.camera_available else "not detected"

        return (
            f"{running}\n"
            f"Device storage: {_format_bytes(usage.free)} free of {_format_bytes(usage.total)}.\n"
            f"Camera: {camera}."
        )

    def _resolve_my_settings(self, requesting_username: Optional[str]) -> str:
        """Read-only: the current persisted device settings plus the
        requester's own saved 'Mine' baseline, if any. Strictly scoped to
        requesting_username -- unlike list_experiments, this never takes a
        username argument from the model, by design (settings are more
        sensitive than a past experiment's config)."""
        if not requesting_username:
            return "I don't know who's chatting -- pick your username on the home screen first."

        settings = settings_store.load_device_settings(self._config.settings_path)
        lines = [
            "Current device settings (shared by whoever uses the box next):",
            f"Illumination source: {settings.photoIlluminationSource.upper()}",
            f"Camera zoom: {settings.camera.zoom:g}x, grayscale: {settings.camera.grayscale}",
            f"LED pixel count: {settings.leds.pixelCount}, order: {settings.leds.pixelOrder}",
            f"IR pins: {settings.ir.pins}",
            "(Camera settings reset to system defaults every restart; LEDs/IR/illumination "
            "source persist.)",
        ]

        mine = user_defaults.load_for(self._config.user_defaults_path, requesting_username)
        if mine:
            lines.append(f"\nYour saved 'Mine' baseline ({requesting_username}):")
            lines.append(f"Illumination source: {mine.photoIlluminationSource.upper()}")
            lines.append(f"Camera zoom: {mine.camera.zoom:g}x, grayscale: {mine.camera.grayscale}")
        else:
            lines.append(f"\nNo personal 'Mine' baseline saved yet for {requesting_username}.")

        return "\n".join(lines)

    def _resolve_my_storage(self, requesting_username: Optional[str]) -> str:
        """Read-only: the current user's own experiment count/bytes used,
        plus device-wide free space. Strictly scoped to requesting_username,
        same reasoning as _resolve_my_settings."""
        if not requesting_username:
            return "I don't know who's chatting -- pick your username on the home screen first."

        usage = shutil.disk_usage(self._config.storage_root)

        tallies = tally_by_user(self._storage, self._config.user_defaults_path)
        tally = tallies.get(requesting_username.strip().lower())
        if tally is None or tally.count == 0:
            usage_line = f"You have no stored experiments yet ({requesting_username})."
        else:
            usage_line = (
                f"{requesting_username} has {tally.count} experiment(s) using "
                f"{_format_bytes(tally.bytes_used)} total across all of them. "
                "Ask about one specific experiment (e.g. via read_experiment_log) "
                "for its own exact size, not this combined total."
            )

        return f"{usage_line}\nDevice free space: {_format_bytes(usage.free)} of {_format_bytes(usage.total)}."

    def _resolve_read_experiment_log(self, args: dict, requesting_username: Optional[str]) -> str:
        """Read-only: the tail of one of the requester's own experiments'
        events.log, plus its real on-disk size. Strictly scoped to
        requesting_username -- this is personal troubleshooting, not a
        shared lookup like list_experiments, so (unlike prefill_experiment)
        it never accepts a username and always resolves against the current
        user only.

        The size is a real `stat()` sum over that one experiment's own
        folder (ExperimentDir.size_bytes()) -- unlike my_storage's
        all-experiments total or a live run's estimatedTotalBytes (a
        pre-flight guess, not a measurement), this is exact and this is the
        only tool that answers "how big is *this specific* experiment"."""
        if not requesting_username:
            return "I don't know who's chatting -- pick your username on the home screen first."

        target_user = requesting_username.strip().lower()
        reference = (args.get("reference") or "").strip().lower()
        exp = self._find_experiment_dir(target_user, reference)
        if exp is None:
            return (
                f"I couldn't find one of your experiments matching \"{reference or 'that'}\". "
                "Try being more specific about which run."
            )

        size_line = f"{exp.experiment_id} is using {_format_bytes(exp.size_bytes())} on disk (exact, measured)."
        events = exp.read_events()
        if not events:
            return f"{size_line}\nNo logged events for {exp.experiment_id} (nothing unusual was recorded)."
        return f"{size_line}\nEvents for {exp.experiment_id}:\n{events}"

    async def _resolve_check_my_images(self, args: dict, requesting_username: Optional[str]) -> str:
        """Read-only vision check on the requester's own experiment images.
        Strictly scoped like read_experiment_log -- never another user's
        images. Mold is only ever reported confirmed if
        vision.MOLD_CONFIRM_THRESHOLD frames were individually flagged; see
        vision.check_frames_for_anomalies for why."""
        if not requesting_username:
            return "I don't know who's chatting -- pick your username on the home screen first."

        target_user = requesting_username.strip().lower()
        reference = (args.get("reference") or "").strip().lower()
        exp = self._find_experiment_dir(target_user, reference)
        if exp is None:
            return (
                f"I couldn't find one of your experiments matching \"{reference or 'that'}\". "
                "Try being more specific about which run."
            )

        paths = vision.sample_image_paths(exp)
        if not paths:
            return f"{exp.experiment_id} has no images to check yet."

        result = await vision.check_frames_for_anomalies(self._config, paths)
        headline = (
            f"Mold appears present in {result.mold_frame_count} of {result.frames_checked} "
            f"checked frames from {exp.experiment_id}."
            if result.mold_confirmed
            else f"Checked {result.frames_checked} frames from {exp.experiment_id}: no confirmed mold."
        )
        return f"{headline} {result.summary}"

    def _resolve_one_image(
        self, args: dict, requesting_username: Optional[str]
    ) -> tuple[Optional[ExperimentDir], Optional[dict], Optional[str]]:
        """Shared resolution for show_image/describe_image: finds the
        requester's own experiment, then one specific capture within it.
        Returns (exp, image, None) on success, or (None, None, error_reply)
        on failure -- strictly scoped like read_experiment_log/
        check_my_images, never another user's images."""
        if not requesting_username:
            return None, None, "I don't know who's chatting -- pick your username on the home screen first."

        target_user = requesting_username.strip().lower()
        reference = (args.get("reference") or "").strip().lower()
        exp = self._find_experiment_dir(target_user, reference)
        if exp is None:
            return None, None, (
                f"I couldn't find one of your experiments matching \"{reference or 'that'}\". "
                "Try being more specific about which run."
            )

        images = exp.list_capture_images()
        if not images:
            return None, None, f"{exp.experiment_id} has no captured images yet."

        which = (args.get("which") or "").strip().lower()
        image = _pick_image(images, which)
        if image is None:
            return None, None, (
                f"I couldn't find an image called \"{which}\" in {exp.experiment_id}. "
                f'Try "first", "last", or an exact name like "{images[-1]["id"]}".'
            )
        return exp, image, None

    def _resolve_show_image(
        self, args: dict, requesting_username: Optional[str]
    ) -> tuple[Optional[AssistantImageRef], str]:
        """Points at one real, already-captured image from one of the
        requester's own experiments -- never invented, always a file that
        actually exists (same principle as prefill_experiment)."""
        exp, image, error = self._resolve_one_image(args, requesting_username)
        if error:
            return None, error

        return (
            AssistantImageRef(
                experimentId=exp.experiment_id,
                imageId=image["id"],
                url=image["url"],
                thumbUrl=image["thumbUrl"],
                caption=f"{image['id']} — {exp.experiment_id}",
            ),
            f"Here's {image['id']} from {exp.experiment_id}.",
        )

    async def _resolve_describe_image(
        self, args: dict, requesting_username: Optional[str]
    ) -> tuple[Optional[AssistantImageRef], str]:
        """Actually looks at one specific image with the vision model and
        describes what's in it -- show_image only ever points at a file, it
        never sends pixels anywhere, so a follow-up "describe it" had
        nothing to work from before this existed. Same resolution as
        show_image (same reference/which args, same strict scoping), and
        also returns the image ref so a description on its own still
        displays the picture, not just text."""
        exp, image, error = self._resolve_one_image(args, requesting_username)
        if error:
            return None, error

        thumb = exp.thumb_file(image["id"])
        if thumb is None:
            return None, f"I couldn't read {image['id']} to describe it."

        prompt = (
            "Describe what's visible in this single photo from a plant-imaging "
            "chamber, in 2-3 plain-language sentences: the plant/plate, lighting "
            "conditions, and anything notable. Don't guess at things you can't "
            "actually see in the image."
        )
        try:
            description = await vision.call_llm(
                self._config, self._config.assistant_vision_model, prompt, [thumb]
            )
        except httpx.HTTPError as exc:
            log.warning("describe_image vision call failed: %s", exc)
            return None, "I couldn't run the image description right now -- try again shortly."

        ref = AssistantImageRef(
            experimentId=exp.experiment_id,
            imageId=image["id"],
            url=image["url"],
            thumbUrl=image["thumbUrl"],
            caption=f"{image['id']} — {exp.experiment_id}",
        )
        return ref, description.strip() or f"I couldn't get a description for {image['id']}."

    def _resolve_download_experiment(
        self, args: dict, requesting_username: Optional[str]
    ) -> tuple[Optional[AssistantDownloadRef], str]:
        """Points at one of the requester's own experiments -- or a specific
        range/count of its images -- packaged as a zip. Never invented,
        always real files that exist (same principle as show_image/
        prefill_experiment). Strictly scoped like read_experiment_log/
        check_my_images: never another user's data.

        Doesn't build the zip itself -- that's real disk I/O better done
        only where it's actually needed: the web UI just links to the
        existing download endpoint (a browser click builds it on demand),
        while Telegram delivery (telegram_link.py) builds and uploads it
        directly, since Telegram can't fetch a URL back from this
        not-internet-reachable device. The image subset (if any) is
        resolved once here and baked into `url` as a query string /
        `imageIds`, so both delivery paths zip the exact same files without
        re-parsing "first three" a second time."""
        if not requesting_username:
            return None, "I don't know who's chatting -- pick your username on the home screen first."

        target_user = requesting_username.strip().lower()
        reference = (args.get("reference") or "").strip().lower()
        exp = self._find_experiment_dir(target_user, reference)
        if exp is None:
            return None, (
                f"I couldn't find one of your experiments matching \"{reference or 'that'}\". "
                "Try being more specific about which run."
            )

        image_ids, error = self._resolve_download_image_range(exp, args)
        if error:
            return None, error

        if image_ids is None:
            size = exp.size_bytes()
            url = f"/api/experiments/{exp.experiment_id}/download"
            filename = f"{exp.experiment_id}.zip"
            desc = f"Packaging {exp.experiment_id} ({_format_bytes(size)}) as a zip."
        else:
            size = sum(
                f.stat().st_size for f in (exp.image_file(i) for i in image_ids) if f is not None
            )
            url = f"/api/experiments/{exp.experiment_id}/download?images={','.join(image_ids)}"
            filename = f"{exp.experiment_id}_{len(image_ids)}-images.zip"
            desc = (
                f"Packaging {len(image_ids)} image(s) from {exp.experiment_id} "
                f"({_format_bytes(size)}) as a zip."
            )

        ref = AssistantDownloadRef(
            experimentId=exp.experiment_id,
            url=url,
            filename=filename,
            sizeBytes=size,
            imageIds=image_ids,
        )
        return ref, desc

    def _resolve_download_image_range(
        self, exp: ExperimentDir, args: dict
    ) -> tuple[Optional[List[str]], Optional[str]]:
        """Turns firstN/lastN/startIndex/endIndex into a concrete list of
        real image ids (chronological order, same as the gallery), or
        (None, None) if none of those args were given -- meaning "the whole
        experiment", the original/default behavior. firstN/lastN win over
        an explicit start/end range if both are somehow given, since
        they're the more common, less error-prone ask."""
        images = exp.list_capture_images()
        first_n = args.get("firstN")
        last_n = args.get("lastN")
        start_index = args.get("startIndex")
        end_index = args.get("endIndex")
        if not any((first_n, last_n, start_index, end_index)):
            return None, None
        if not images:
            return None, f"{exp.experiment_id} has no captured images yet."

        if first_n:
            selected = images[: int(first_n)]
        elif last_n:
            selected = images[-int(last_n) :]
        else:
            start = max(int(start_index), 1) if start_index else 1
            end = int(end_index) if end_index else len(images)
            selected = images[start - 1 : end]

        if not selected:
            return None, f"That range doesn't match any of {exp.experiment_id}'s {len(images)} image(s)."
        return [img["id"] for img in selected], None

    async def _resolve_upload_to_remote(self, args: dict, requesting_username: Optional[str]) -> str:
        """Copies one of the requester's own experiments to the connected
        network share (CIFS/SMB) via RemoteSyncService.sync_experiment, into
        their own subfolder there -- never invents a path, always the real
        remote_sync.py layout (<mount>/<researcher>/<experiment>/...).
        Strictly scoped like download_experiment/read_experiment_log: only
        ever the requester's own experiments and their own subfolder,
        regardless of whoever else's device sync might currently be
        configured for.

        Doesn't attempt to turn sync on, connect, or ask for a password --
        same "degrades gracefully, no crash, tell them what to do instead"
        precedent as every other optional-infrastructure tool here
        (Telegram, remote sync's own credentialsRequired state in the UI).

        If DSM sharing (dsm_sharing.py) is *also* connected -- a separate
        NAS/account from the CIFS share, see its own module docstring for
        why -- this additionally asks it for a real, clickable, internet-
        reachable link and includes that in the reply instead of just the
        local network path. If DSM sharing isn't set up, the local path
        alone is still a complete, useful answer, so that failure is silent
        (no "couldn't get a link" noise for something never configured)."""
        if not requesting_username:
            return "I don't know who's chatting -- pick your username on the home screen first."
        if self._remote_sync is None or not self._remote_sync.settings.enabled or not self._remote_sync.password_set:
            return (
                "Remote sync isn't connected right now -- turn it on and connect "
                "in Settings -> General -> Remote Sync first."
            )

        target_user = requesting_username.strip().lower()
        reference = (args.get("reference") or "").strip().lower()
        exp = self._find_experiment_dir(target_user, reference)
        if exp is None:
            return (
                f"I couldn't find one of your experiments matching \"{reference or 'that'}\". "
                "Try being more specific about which run."
            )

        ok, message, _copied = await self._remote_sync.sync_experiment(target_user, exp.experiment_id)
        if not ok:
            return message

        if self._dsm_sharing is not None and self._dsm_sharing.settings.enabled and self._dsm_sharing.password_set:
            link_ok, link_result = await self._dsm_sharing.create_share_link(target_user, exp.experiment_id)
            if link_ok:
                return f"{message} Here's a link: {link_result}"
            log.info("DSM share-link creation failed for %s: %s", exp.experiment_id, link_result)

        remote_path = self._remote_sync.remote_path_for(target_user) / exp.experiment_id
        return f"{message} It's on the network drive at {remote_path}."

    # --- interrupt / archive -------------------------------------------------
    async def interrupt_and_archive(self, reason: str) -> None:
        """Cancels any in-flight generation and archives whatever was said so
        far so it isn't silently lost, then clears it for the next
        conversation. Not called automatically anywhere right now -- an
        earlier local-model version called this the moment an experiment
        started, to protect the Pi's RAM/CPU; a remote API has no such
        contention, so chat now stays available throughout a run. Kept as an
        available capability (e.g. for a future explicit "clear
        conversation" action) rather than removed outright."""
        if self._task is not None and not self._task.done():
            self._task.cancel()

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
