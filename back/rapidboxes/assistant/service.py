"""AssistantService: the QA chat model, running against the institute's
OpenAI-compatible LLM gateway (e-INFRA CZ) via real tool/function calling,
plus the one piece of "tool use" it's allowed that can touch a real
experiment -- resolving a request like "start like Ivan's run from
yesterday" into a concrete ExperimentProposal built from that run's own
saved config. The model itself never invents parameter values and never
calls the start API; it only ever gets to point at a real past experiment,
which this service then reads verbatim from disk.

Chat is only ever reachable while idle (see api/assistant.py, gated on
runner.status.state), and is immediately cut short -- generation cancelled,
transcript archived -- the moment an experiment starts (see
interrupt_and_archive(), called from api/experiments.py).
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
    AssistantMessage,
    ExperimentProposal,
    SavedExperimentConfig,
)
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
                        "description": "name to filter by, omit for every user",
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
                "camera working."
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
                "How much storage the CURRENT user is using -- their own "
                "experiment count and bytes used, plus the box's total free "
                "space. Use for questions like \"how much storage am I "
                "using\". Always scoped to whoever is chatting -- takes no "
                "arguments, never another user's usage."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_experiment_log",
            "description": (
                "Read the event log for one of the CURRENT user's own past "
                "experiments -- what phases ran, capture failures, remote "
                "sync failures, crashes. Use this to help explain a "
                "complaint like \"my last run had a weird gap\" or \"what "
                "happened during yesterday's experiment\". Always scoped to "
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
    ):
        self._config = config
        self._storage = storage
        # Read-only: current experiment status and camera availability
        # (system_status tool). Never used to start/stop/change anything --
        # see the module docstring and interrupt_and_archive() for the one
        # direction this relationship goes (experiments interrupt the
        # assistant, never the other way around). Optional so tests that
        # never exercise system_status don't need to build a real runner.
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
            messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
            messages += [{"role": m.role, "content": m.content} for m in history]
            messages.append({"role": "user", "content": message})

            # Tracked for the whole method, not just the initial LLM call --
            # check_my_images' vision call can itself take several seconds,
            # and interrupt_and_archive() must be able to cancel that too if
            # an experiment starts mid-check, not only during the first call.
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
                    proposal, reply = await self._resolve_tool_call(tool_calls[0], username)
                else:
                    proposal, reply = None, (result.get("content") or "").strip()
            finally:
                self._task = None

            self._transcript.append(AssistantMessage(role="assistant", content=reply))
            return AssistantChatResponse(reply=reply, proposal=proposal)

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
    ) -> tuple[Optional[ExperimentProposal], str]:
        """Dispatches one native tool_calls entry to its resolver. Only
        prefill_experiment ever carries a proposal -- every other tool is a
        read-only lookup, answered directly, never a setup-screen prefill.
        my_settings/my_storage deliberately ignore any username the model
        might put in args (their schema takes none) and always use
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
            return self._resolve_prefill_experiment(args, requesting_username)
        if name == "list_experiments":
            return None, self._resolve_list_experiments(args, requesting_username)
        if name == "system_status":
            return None, self._resolve_system_status()
        if name == "my_settings":
            return None, self._resolve_my_settings(requesting_username)
        if name == "my_storage":
            return None, self._resolve_my_storage(requesting_username)
        if name == "read_experiment_log":
            return None, self._resolve_read_experiment_log(args, requesting_username)
        if name == "check_my_images":
            return None, await self._resolve_check_my_images(args, requesting_username)
        log.warning("model called unknown tool %r", name)
        return None, "Sorry, something went wrong handling that request."

    def _resolve_prefill_experiment(
        self, args: dict, requesting_username: Optional[str]
    ) -> tuple[Optional[ExperimentProposal], str]:
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

    def _resolve_list_experiments(self, args: dict, requesting_username: Optional[str]) -> str:
        """Read-only listing, most-recently-modified first (same ordering
        Storage.list_experiments() already uses for Gallery/history) -- not
        scoped to the requester by default, same shared-device visibility
        rule as prefill_experiment: naming nobody means "every user", not
        "only me", because Gallery/Import already show everyone's runs to
        everyone on this box."""
        target_user = (args.get("username") or "").strip().lower() or None
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

    def _resolve_system_status(self) -> str:
        """Read-only snapshot of live device state -- current experiment (if
        any), free storage, camera. Never anything that changes state; see
        the module docstring for why that boundary is deliberate."""
        if self._runner is None:
            return "System status isn't available right now."

        status = self._runner.status
        if status.state == "idle":
            running = "No experiment is currently running."
        else:
            running = (
                f"Experiment '{status.experimentId}' ({status.username}) is {status.state}, "
                f"phase {status.phase}, {status.imagesCaptured}/{status.imagesPlanned} images captured."
            )

        usage = shutil.disk_usage(self._config.storage_root)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        camera = "available" if self._runner._hw.camera_available else "not detected"

        return (
            f"{running}\n"
            f"Storage: {free_gb:.1f} GB free of {total_gb:.1f} GB.\n"
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
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)

        tallies = tally_by_user(self._storage, self._config.user_defaults_path)
        tally = tallies.get(requesting_username.strip().lower())
        if tally is None or tally.count == 0:
            usage_line = f"You have no stored experiments yet ({requesting_username})."
        else:
            mb = tally.bytes_used / (1024**2)
            usage_line = (
                f"{requesting_username} has {tally.count} experiment(s) using {mb:.1f} MB."
            )

        return f"{usage_line}\nDevice free space: {free_gb:.1f} GB of {total_gb:.1f} GB."

    def _resolve_read_experiment_log(self, args: dict, requesting_username: Optional[str]) -> str:
        """Read-only: the tail of one of the requester's own experiments'
        events.log. Strictly scoped to requesting_username -- this is
        personal troubleshooting, not a shared lookup like list_experiments,
        so (unlike prefill_experiment) it never accepts a username and
        always resolves against the current user only."""
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

        events = exp.read_events()
        if not events:
            return f"{exp.experiment_id} has no logged events (nothing unusual was recorded)."
        return f"Events for {exp.experiment_id}:\n{events}"

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

    # --- interrupt / archive -------------------------------------------------
    async def interrupt_and_archive(self, reason: str) -> None:
        """Called the moment an experiment starts: cancel any in-flight
        generation and archive whatever was said so far so it isn't silently
        lost -- then clear it for the next conversation."""
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
