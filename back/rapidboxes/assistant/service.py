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
        Storage.list_experiments() already uses for Gallery/history).

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
