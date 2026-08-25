"""Telegram integration: opt-in issue-alert delivery (see
assistant/mold_watch.py) AND a full chat interface to PidiBot, both riding
the same linked account. Telegram was chosen for alerts specifically
because it needs neither a personal mailbox for the device nor any
institutional mail relay -- just a bot token (created once via @BotFather)
and a per-researcher linking step; chat came after, once linking already
existed, by routing any message that isn't a link code to the same
AssistantService the web UI uses.

Linking flow (private DM per researcher, not one shared group -- so an
alert, and a chat session, only ever reaches the person who opted in):

1. A researcher taps "Link" in Settings -> General -> Telegram Alerts. The
   backend hands back a short-lived one-time code (request_link_code) and
   the bot's @username, shown on screen.
2. They open Telegram, message the bot, and send that code as a plain
   message.
3. This service's background poll (Telegram's getUpdates, no webhook/public
   URL needed -- this device is not internet-reachable) picks up the
   message, matches the code, and persists chat_id under that username.
4. From then on, send_message(username, text) can DM them directly, and any
   other message from that chat is treated as a chat turn with PidiBot (see
   _handle_chat_message) -- same assistant, same tools, same strict
   per-user scoping, just a different transport than the kiosk screen.

Deliberately optional infrastructure: unset bot_token/bot_username means
"not configured yet", not an error -- every public method degrades
gracefully (no crash, no-op / False) so the rest of the app works fine with
this feature simply unavailable until an admin sets it up. Same precedent
as Remote Sync's credentials_required state.

Slash commands (/help, /status, /experiments, /unlink, /launch, /monitor)
are deliberately plain deterministic Python, not a model round-trip: each
one is a fixed "skill" backed by a "script" -- either a small handler right
here, or (for /status, /experiments, /launch's initial seed) a public
resolve_*() method on AssistantService that already existed as a tool for
the *general* chat path and needed no LLM involvement in the first place,
just an explicit username/args tuple instead of a model-produced tool_call.
Anything that isn't a recognized /command, and isn't an answer to an
in-progress /launch wizard (see below), goes to _handle_chat_message, the
one place an actual model call happens. See _poll_once for the dispatch.

/launch is the one multi-turn command: a small deterministic state machine
(_LaunchWizardState, _handle_launch_answer/_handle_launch_confirmation),
one field at a time, each answer parsed and range-checked before being
accepted -- a bad answer re-asks the same question rather than guessing or
silently skipping it. Confirming the final summary actually starts the
experiment (AssistantService.start_experiment_from_launch) -- the one real
exception to every other tool here never touching hardware, gated on every
field having been explicitly confirmed by a human first, never the model
deciding to. If it can't actually start right now (busy/no_camera/
low_space), the resolved config is staged for the matching setup screen to
load instead (AssistantService.stage_pending_launch), so the trip to the
device isn't wasted.

/monitor additionally pins a live-updating progress-bar message to the
chat (see _send_and_pin/_edit_message/_build_progress_text): edited in
place roughly every PROGRESS_UPDATE_INTERVAL_S rather than resent, both to
respect Telegram's per-chat edit rate limits and because a fresh message
every few minutes for a run lasting hours/days would drown out everything
else in the chat.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set, Tuple

import httpx

from . import config_xml
from .assistant import summary as assistant_summary
from .assistant.service import AssistantUnavailable, format_config_knobs
from .kiosk_screenshot import KioskScreenshotUnavailable, capture_kiosk_screenshot
from .models import (
    EXPOSURE_PROFILES,
    VALID_SPECTRA,
    AssistantDownloadRef,
    AssistantImageRef,
    AssistantMessage,
    ExperimentStatus,
    SavedExperimentConfig,
)
from .storage import ExperimentDir, Storage

if TYPE_CHECKING:
    from .assistant.service import AssistantService
    from .engine.runner import ExperimentRunner

log = logging.getLogger("rapidboxes.telegram")

API_BASE = "https://api.telegram.org"
LINK_CODE_TTL_S = 600.0  # 10 minutes -- long enough to switch apps and type it, short enough that a stale code isn't a standing risk.
POLL_INTERVAL_S = 3.0
# Turns (user+assistant messages) kept per Telegram chat -- same "don't let
# this grow forever" reasoning as the web UI's own localStorage history,
# just server-side since there's no client to hold it for this channel.
MAX_CHAT_HISTORY = 20

# The Bot API's own cap on a file uploaded directly (not fetched from a
# URL) is 50MB; stay comfortably under it rather than find out mid-upload
# that zip overhead pushed a borderline experiment over the edge.
TELEGRAM_MAX_UPLOAD_BYTES = 45 * 1024 * 1024

# How often the progress-bar background loop wakes up to check whether any
# pinned message is due for a refresh -- NOT how often a given message is
# actually edited (see PROGRESS_UPDATE_INTERVAL_S). Short enough that a
# freshly-pinned message and a completed/unsubscribed run are noticed
# promptly, without editing anything that often.
PROGRESS_TICK_S = 60.0
# How often a pinned progress message is actually edited, in real time
# regardless of how often the tick above runs. 15-20 minutes is plenty for
# a run lasting hours to days, and stays well clear of Telegram's per-chat
# edit rate limits.
PROGRESS_UPDATE_INTERVAL_S = 15 * 60.0
PROGRESS_BAR_SEGMENTS = 10

# How long a /launch wizard stays alive with no reply before it's treated as
# abandoned and silently dropped on the next message from that chat (a fresh
# /launch always starts clean regardless). Generous -- someone may set the
# device down mid-conversation -- but bounded so a months-old half-answered
# wizard can never resurface and confuse a completely unrelated later chat.
LAUNCH_WIZARD_TIMEOUT_S = 3600.0


# ---------------------------------------------------------------------------
# /launch: a guided, one-field-at-a-time wizard for a new experiment's
# config. Deterministic (a plain state machine, no model call) -- see the
# module docstring. Never starts anything itself: confirming at the end
# stages the resolved config for the matching setup screen to pick up (see
# AssistantService.stage_pending_launch), the same "a human still presses
# Start" boundary every other tool here respects.
# ---------------------------------------------------------------------------


def _parse_yes_no(text: str) -> bool:
    t = text.strip().lower()
    if t in ("yes", "y", "true", "on", "enable", "enabled"):
        return True
    if t in ("no", "n", "false", "off", "disable", "disabled"):
        return False
    raise ValueError("please answer yes or no")


def _bounded_float(lo: float, hi: float, unit: str) -> Callable[[str], float]:
    def parse(text: str) -> float:
        try:
            value = float(text.strip())
        except ValueError:
            raise ValueError(f"please send a number, {lo:g}-{hi:g} {unit}") from None
        if not (lo <= value <= hi):
            raise ValueError(f"must be between {lo:g} and {hi:g} {unit}")
        return value

    return parse


def _bounded_int(lo: int, hi: int, unit: str) -> Callable[[str], int]:
    def parse(text: str) -> int:
        try:
            value = int(text.strip())
        except ValueError:
            raise ValueError(f"please send a whole number, {lo}-{hi} {unit}") from None
        if not (lo <= value <= hi):
            raise ValueError(f"must be between {lo} and {hi} {unit}")
        return value

    return parse


def _parse_spectra(text: str) -> List[str]:
    if text.strip().lower() in ("all", "all of them", "everything"):
        return list(VALID_SPECTRA)
    parts = [p.strip().lower() for p in text.replace(",", " ").split() if p.strip()]
    if not parts:
        raise ValueError(f"please list at least one of: {', '.join(VALID_SPECTRA)}")
    bad = [p for p in parts if p not in VALID_SPECTRA]
    if bad:
        raise ValueError(f"unknown colour(s) {', '.join(bad)} -- choose from: {', '.join(VALID_SPECTRA)}")
    seen: List[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return seen


def _parse_light_source(text: str) -> str:
    t = text.strip().lower()
    if t in ("ir", "infrared"):
        return "ir"
    if t in ("rgbw", "rgb", "color", "colour"):
        return "rgbw"
    raise ValueError('please answer "ir" or "rgbw"')


def _parse_protocol(text: str) -> str:
    t = text.strip().lower()
    if t in ("tropism", "t"):
        return "tropism"
    if t in ("growth", "g"):
        return "growth"
    raise ValueError('please answer "tropism" or "growth"')


def _parse_name(text: str) -> str:
    t = text.strip()
    if not (1 <= len(t) <= 80):
        raise ValueError("please send a name, 1-80 characters")
    return t


def _parse_color_mode(text: str) -> bool:
    t = text.strip().lower()
    if t in ("bw", "b&w", "black and white", "black-and-white", "grayscale", "greyscale", "gray", "grey", "mono", "monochrome"):
        return True
    if t in ("color", "colour", "rgb"):
        return False
    raise ValueError('please answer "color" or "bw"')


@dataclass
class _LaunchField:
    name: str
    label: str  # short, for the opening overview -- "Dark phase"
    prompt: str  # a full question -- "Enable the dark phase? (yes/no)"
    parse: Callable[[str], object]
    format: Callable[[object], str]


_LAUNCH_PROTOCOL_FIELD = _LaunchField(
    "protocol", "Measurement", 'Which measurement -- "tropism" or "growth"?', _parse_protocol, str
)
# Handled specially (see _handle_launch_answer): SavedExperimentConfig has
# no experimentName of its own -- this is asked and stored separately on
# _LaunchWizardState, needed because start_experiment_from_launch actually
# starts a run now and TropismConfig/GrowthConfig require a real name.
_LAUNCH_NAME_FIELD = _LaunchField(
    "experimentName", "Name", "What should I call this experiment?", _parse_name, str
)
# Also handled specially: grayscale is nested under SavedExperimentConfig.
# camera, not a top-level field, so it can't go through the generic
# getattr(base, f.name)/model_copy(update=state.values) path the way
# protocol-level knobs do -- see _handle_launch_answer/_ask_current_launch_field.
_LAUNCH_COLOR_FIELD = _LaunchField(
    "grayscale",
    "Image color",
    'Colour or black-and-white images? ("color" or "bw")',
    _parse_color_mode,
    lambda v: "black-and-white" if v else "color",
)
# A control-flow-only gate, never itself part of the resolved config (see
# _handle_launch_answer) -- exposure is normally auto-paired with
# photoIlluminationSource (EXPOSURE_PROFILES/_couple_exposure_to_source in
# models.py); asking before letting anyone touch it directly is the whole
# point of this question, per an explicit request to warn people here.
_LAUNCH_EXPOSURE_OVERRIDE_FIELD = _LaunchField(
    "exposureOverride",
    "Exposure override",
    "Exposure is normally set automatically to match your light source (IR needs 0.2-10s, "
    "RGBW/white needs 10-500ms) -- do you want to manually override it anyway? Only say yes if "
    "you're sure: a mismatched value can leave every image black or blown out. (yes/no)",
    _parse_yes_no,
    lambda v: "yes" if v else "no",
)


def _build_exposure_field(source: str) -> _LaunchField:
    """Built fresh once the light source is actually known (see
    _handle_launch_answer, only when someone opts into overriding exposure)
    -- the valid range genuinely depends on it (EXPOSURE_PROFILES in
    models.py), so a static field can't validate this correctly ahead of
    time."""
    profile = EXPOSURE_PROFILES.get(source, EXPOSURE_PROFILES["ir"])
    lo_s = profile["min"] / 1_000_000
    hi_s = profile["max"] / 1_000_000

    def parse(text: str) -> int:
        try:
            seconds = float(text.strip())
        except ValueError:
            raise ValueError(
                f"please send a number of seconds ({lo_s:g}-{hi_s:g} for {source.upper()})"
            ) from None
        microseconds = round(seconds * 1_000_000)
        if not (profile["min"] <= microseconds <= profile["max"]):
            raise ValueError(f"must be between {lo_s:g} and {hi_s:g} seconds for {source.upper()} lighting")
        return microseconds

    return _LaunchField(
        "exposureMicroseconds",
        "Exposure",
        f"Exposure, in seconds ({lo_s:g}-{hi_s:g} for {source.upper()} lighting)?",
        parse,
        lambda v: f"{v / 1_000_000:g}s",
    )


_LAUNCH_ISSUE_ALERT_FIELD = _LaunchField(
    "reportOnIssueEnabled",
    "Telegram issue alerts",
    "Message you here on Telegram if an issue is detected mid-run? (yes/no)",
    _parse_yes_no,
    lambda v: "yes" if v else "no",
)

_LAUNCH_TROPISM_FIELDS = [
    _LaunchField(
        "darkPhaseEnabled",
        "Dark phase",
        'Enable the dark "apical hook" phase? (yes/no)',
        _parse_yes_no,
        lambda v: "enabled" if v else "disabled",
    ),
    _LaunchField(
        "darkPhaseHours",
        "Dark phase length",
        "Dark phase length, in hours (0-350)?",
        _bounded_float(0, 350, "hours"),
        lambda v: f"{v:g}h",
    ),
    _LaunchField(
        "lateralIlluminationHours",
        "Bending (lateral light) length",
        "Bending (lateral light) phase length, in hours (0-168)?",
        _bounded_float(0, 168, "hours"),
        lambda v: f"{v:g}h",
    ),
    _LaunchField(
        "spectra",
        "Spectra",
        f"Which light colour(s) for the bending phase -- {', '.join(VALID_SPECTRA)}? "
        "You can pick more than one, e.g. \"red, blue\", or send \"all\" for every colour.",
        _parse_spectra,
        lambda v: ", ".join(v) if v else "(none)",
    ),
    _LaunchField(
        "intervalMinutes",
        "Capture interval",
        "Capture interval, in minutes (1-240)?",
        _bounded_float(1, 240, "min"),
        lambda v: f"every {v:g} min",
    ),
    _LaunchField(
        "intensity",
        "Light intensity",
        "Light intensity, in percent (0-100)?",
        _bounded_int(0, 100, "%"),
        lambda v: f"{v}%",
    ),
    _LaunchField(
        "photoIlluminationSource",
        "Photo light source",
        'Photo light source -- "ir" or "rgbw"?',
        _parse_light_source,
        lambda v: v.upper(),
    ),
]

_LAUNCH_GROWTH_FIELDS = [
    _LaunchField(
        "dayLengthHours",
        "Day length",
        "Day length, in hours (0-24)?",
        _bounded_int(0, 24, "hours"),
        lambda v: f"{v}h",
    ),
    _LaunchField(
        "experimentLengthDays",
        "Experiment length",
        "Experiment length, in days (1-30)?",
        _bounded_int(1, 30, "days"),
        lambda v: f"{v} days",
    ),
    _LaunchField(
        "spectra",
        "Spectra",
        f"Which light colour(s) -- {', '.join(VALID_SPECTRA)}? "
        "You can pick more than one, e.g. \"red, blue\", or send \"all\" for every colour.",
        _parse_spectra,
        lambda v: ", ".join(v) if v else "(none)",
    ),
    _LaunchField(
        "dayIntensity",
        "Day light intensity",
        "Day light intensity, in percent (0-100)?",
        _bounded_int(0, 100, "%"),
        lambda v: f"{v}%",
    ),
    _LaunchField(
        "intervalMinutes",
        "Capture interval",
        "Capture interval, in minutes (1-240)?",
        _bounded_float(1, 240, "min"),
        lambda v: f"every {v:g} min",
    ),
    _LaunchField(
        "photoIlluminationSource",
        "Photo light source",
        'Photo light source -- "ir" or "rgbw"?',
        _parse_light_source,
        lambda v: v.upper(),
    ),
]


@dataclass
class _LaunchWizardState:
    username: str
    base: SavedExperimentConfig
    fields: List[_LaunchField] = dataclass_field(default_factory=list)
    index: int = 0
    values: Dict[str, object] = dataclass_field(default_factory=dict)
    # Held separately from `values` -- SavedExperimentConfig has no
    # experimentName field, so this never goes into state.base.model_copy().
    experiment_name: Optional[str] = None
    # Also held separately -- grayscale/exposureMicroseconds are nested
    # under SavedExperimentConfig.camera, not top-level, so they can't go
    # through model_copy(update=values) either. Only ever contains keys the
    # human actually answered (grayscale always, once asked; exposure only
    # if they opted into overriding it) -- passed on to
    # AssistantService.start_experiment_from_launch so it knows exactly
    # what to push to live DeviceSettings versus leave untouched.
    camera_overrides: Dict[str, object] = dataclass_field(default_factory=dict)
    awaiting_confirmation: bool = False
    final_config: Optional[SavedExperimentConfig] = None
    last_activity: float = dataclass_field(default_factory=time.monotonic)


# Same ambiguity the web UI's markdownLite.ts fixes (a bullet's leading
# "* " misread as the start of an *italic* span), ported to Python since
# Telegram gets plain text, not React elements, from PidiBot's replies.
_BULLET_RE = re.compile(r"^(\s*)\*\s+", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*([^\n*]+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^\n*]+?)\*(?!\*)")


def _strip_markdown_lite(text: str) -> str:
    """Telegram's own Markdown parse modes use a different, stricter syntax
    (and MarkdownV2 requires escaping most punctuation) -- rather than risk
    a 400 from an unescaped character in arbitrary LLM output, send plain
    text with the light markdown PidiBot actually uses stripped out instead
    of rendered."""
    text = _BULLET_RE.sub(r"\1• ", text)
    text = _BOLD_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    return text


def _format_duration(seconds: float) -> str:
    """"3d 18h 0m" -- mirrors the web UI's formatDurationLong (client/lib/
    progress.ts) exactly, so a duration quoted here and one quoted on the
    Progress screen for the same run never disagree in shape."""
    s = max(0, round(seconds))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if days > 0 or hours > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _key(username: str) -> str:
    return username.strip().lower()


def _parse_command(text: str) -> Optional[Tuple[str, str]]:
    """Splits a Telegram command message into (command, rest), e.g.
    "/launch like my run from tuesday" -> ("/launch", "like my run from
    tuesday"), "/status@IEB_pidibot" -> ("/status", ""). Returns None for
    anything that isn't a /command at all, so it falls through to normal
    chat instead."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head, _, rest = stripped.partition(" ")
    command = head.split("@")[0].lower()
    return command, rest.strip()


def _load_links(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {}
    try:
        return {k: int(v) for k, v in json.loads(path.read_text()).items()}
    except Exception:
        return {}


def _save_links(path: Path, links: Dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(links, indent=2))
    os.replace(tmp, path)


def _load_monitors(path: Path) -> Dict[str, Set[int]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        return {k: {int(c) for c in v} for k, v in raw.items()}
    except Exception:
        return {}


def _save_monitors(path: Path, monitors: Dict[str, Set[int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: sorted(v) for k, v in monitors.items()}, indent=2))
    os.replace(tmp, path)


class TelegramLinkService:
    def __init__(
        self,
        bot_token: Optional[str],
        bot_username: Optional[str],
        links_path: Path,
        storage: Storage,
        monitors_path: Path,
    ):
        self._token = bot_token
        self.bot_username = bot_username
        self._links_path = links_path
        self._links: Dict[str, int] = _load_links(links_path)
        self._storage = storage
        # Set via attach_assistant()/attach_runner() once those exist --
        # both are constructed after this service (same deferred-wiring
        # reason as MoldWatchService.attach_runner).
        self._assistant: Optional["AssistantService"] = None
        self._runner: Optional["ExperimentRunner"] = None
        # code -> (username, expires_at). Only ever touched from the single
        # event loop thread (API handlers + the poll task), no lock needed.
        self._pending: Dict[str, Tuple[str, float]] = {}
        # chat_id -> turns, for the chat-over-Telegram feature. Same
        # single-event-loop-thread reasoning, no lock needed.
        self._chat_history: Dict[int, List[AssistantMessage]] = {}
        # /monitor subscriptions: experiment_id -> chat_ids watching it.
        # Persisted (see the field's own docstring in config.py) so a
        # subscription survives the exact kind of restart it exists to
        # report on.
        self._monitors_path = monitors_path
        self._monitors: Dict[str, Set[int]] = _load_monitors(monitors_path)
        # Pinned progress-bar messages: (experiment_id, chat_id) -> the
        # sendMessage result's message_id, so later ticks know what to edit
        # rather than sending a new message. Deliberately NOT persisted like
        # _monitors -- unlike the blackout notice, nothing here needs to
        # survive a restart: if lost, the next /monitor for a still-running
        # experiment just starts a fresh pinned message.
        self._progress_messages: Dict[Tuple[str, int], int] = {}
        # Same key -- last successful edit, in monotonic time, so the tick
        # loop can skip a pin that was refreshed recently without needing a
        # per-subscription scheduler.
        self._progress_last_edit: Dict[Tuple[str, int], float] = {}
        # /launch wizards in progress, keyed by chat_id -- in-memory only,
        # same reasoning as _progress_messages: nothing here needs to
        # survive a restart, a fresh /launch just starts clean.
        self._launch_wizards: Dict[int, _LaunchWizardState] = {}
        self._update_offset = 0
        self._worker: Optional[asyncio.Task] = None
        self._progress_worker: Optional[asyncio.Task] = None
        self._client = httpx.AsyncClient(base_url=API_BASE, timeout=15.0)

    @property
    def configured(self) -> bool:
        return bool(self._token and self.bot_username)

    def is_linked(self, username: str) -> bool:
        return _key(username) in self._links

    def attach_assistant(self, assistant: "AssistantService") -> None:
        self._assistant = assistant

    def attach_runner(self, runner: "ExperimentRunner") -> None:
        self._runner = runner

    def is_monitored(self, experiment_id: str) -> bool:
        return bool(self._monitors.get(experiment_id))

    def request_link_code(self, username: str) -> str:
        """Fresh one-time code for `username`, replacing any still-pending
        code for the same user so re-requesting doesn't leave stale extra
        codes redeemable."""
        key = _key(username)
        for stale in [c for c, (u, _) in self._pending.items() if u == key]:
            del self._pending[stale]
        code = f"{secrets.randbelow(900_000) + 100_000}"
        self._pending[code] = (key, time.monotonic() + LINK_CODE_TTL_S)
        return code

    async def send_message(self, username: str, text: str) -> bool:
        """Best-effort DM to `username`'s linked chat. Never raises -- an
        alert failing to send must not break the mold-watch pipeline it's
        called from."""
        if not self.configured:
            log.info("telegram not configured; would have sent to %s: %s", username, text)
            return False
        chat_id = self._links.get(_key(username))
        if chat_id is None:
            log.info("telegram not linked for %s; cannot send: %s", username, text)
            return False
        return await self._send_raw(chat_id, text)

    async def _send_raw(self, chat_id: int, text: str) -> bool:
        try:
            res = await self._client.post(
                f"/bot{self._token}/sendMessage", json={"chat_id": chat_id, "text": text}
            )
            res.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.warning("telegram sendMessage failed for chat %s: %s", chat_id, exc)
            return False

    async def _send_and_pin(self, chat_id: int, text: str) -> Optional[int]:
        """Sends `text` and pins it -- the progress-bar message /monitor
        starts. Returns the message_id (for later edits) or None if even
        the send failed; a pin failure alone (e.g. the bot somehow lacking
        pin rights) is logged but not fatal -- the message still exists and
        can still be edited in place, just without staying pinned."""
        try:
            res = await self._client.post(
                f"/bot{self._token}/sendMessage", json={"chat_id": chat_id, "text": text}
            )
            res.raise_for_status()
            message_id = (res.json().get("result") or {}).get("message_id")
        except httpx.HTTPError as exc:
            log.warning("telegram sendMessage (progress bar) failed for chat %s: %s", chat_id, exc)
            return None
        if message_id is None:
            return None
        try:
            pin_res = await self._client.post(
                f"/bot{self._token}/pinChatMessage",
                json={"chat_id": chat_id, "message_id": message_id, "disable_notification": True},
            )
            pin_res.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("telegram pinChatMessage failed for chat %s: %s", chat_id, exc)
        return message_id

    async def _edit_message(self, chat_id: int, message_id: int, text: str) -> bool:
        try:
            res = await self._client.post(
                f"/bot{self._token}/editMessageText",
                json={"chat_id": chat_id, "message_id": message_id, "text": text},
            )
            res.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            # Telegram 400s this when the new text is byte-identical to what's
            # already there -- a genuinely unchanged progress bar between two
            # ticks, not a real failure, so it must not spam a warning every
            # PROGRESS_UPDATE_INTERVAL_S.
            if exc.response.status_code == 400 and "not modified" in exc.response.text.lower():
                return True
            log.warning("telegram editMessageText failed for chat %s: %s", chat_id, exc)
            return False
        except httpx.HTTPError as exc:
            log.warning("telegram editMessageText failed for chat %s: %s", chat_id, exc)
            return False

    async def _unpin_message(self, chat_id: int, message_id: int) -> None:
        """Best-effort, called once a monitored run finishes -- a failure
        here just leaves a stale pin behind, never worth surfacing."""
        try:
            res = await self._client.post(
                f"/bot{self._token}/unpinChatMessage",
                json={"chat_id": chat_id, "message_id": message_id},
            )
            res.raise_for_status()
        except httpx.HTTPError:
            pass

    async def _send_photo(self, chat_id: int, image: AssistantImageRef) -> None:
        """Sends the same thumbnail the web chat shows inline -- Telegram's
        `photo` param would need a URL it can fetch itself, but this device
        isn't internet-reachable (same reason polling is used over a
        webhook), so the file is uploaded directly instead."""
        exp = self._storage.get_experiment(image.experimentId)
        if exp is None:
            return
        path = exp.thumb_file(image.imageId)
        if path is None:
            return
        await self._send_photo_file(chat_id, path, image.caption)

    async def _send_photo_file(self, chat_id: int, path: Path, caption: str) -> None:
        try:
            data = path.read_bytes()
        except OSError as exc:
            log.warning("telegram sendPhoto failed to read %s: %s", path, exc)
            return
        await self._send_photo_bytes(chat_id, path.name, data, caption)

    async def _send_photo_bytes(self, chat_id: int, filename: str, data: bytes, caption: str) -> None:
        """Same as _send_photo_file but for an image already in memory (a
        fresh camera capture, a screen grab) -- no need to round-trip it
        through a temp file just to read it straight back."""
        try:
            res = await self._client.post(
                f"/bot{self._token}/sendPhoto",
                data={"chat_id": str(chat_id), "caption": caption},
                files={"photo": (filename, data, "image/jpeg")},
            )
            res.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("telegram sendPhoto failed for chat %s: %s", chat_id, exc)

    async def _send_download(self, chat_id: int, download: AssistantDownloadRef) -> None:
        """Builds the zip (real disk I/O, only done here, on demand -- see
        AssistantService._resolve_download_experiment's docstring) and
        uploads it directly, for the same reason _send_photo does: this
        device can't hand Telegram a URL to fetch back out. Over the Bot
        API's upload limit, tells the researcher instead of trying and
        failing partway through."""
        if download.sizeBytes > TELEGRAM_MAX_UPLOAD_BYTES:
            await self._send_raw(
                chat_id,
                f"{download.experimentId} is too large to send here via Telegram "
                f"({download.sizeBytes / (1024 * 1024):.0f} MB, limit ~45 MB). "
                "Download it from the device itself: Gallery → Folders.",
            )
            return
        exp = self._storage.get_experiment(download.experimentId)
        if exp is None:
            return
        tmp_path = exp.zip_to_temp_file(download.imageIds)
        try:
            data = tmp_path.read_bytes()
            res = await self._client.post(
                f"/bot{self._token}/sendDocument",
                data={"chat_id": str(chat_id)},
                files={"document": (download.filename, data, "application/zip")},
            )
            res.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            log.warning("telegram sendDocument failed for chat %s: %s", chat_id, exc)
        finally:
            os.remove(tmp_path)

    # --- push notifications (mold_watch.py, recover(), on_experiment_finished) -----

    async def notify_issue(self, experiment_id: str, username: str, text: str) -> None:
        """Called by MoldWatchService once a mid-run anomaly is confirmed.
        Reaches whichever is relevant: the config-opted-in researcher (if
        reportOnIssueEnabled was set for this run) and/or anyone who sent
        /monitor for this experiment -- usually the same person, but never
        double-sent twice to the one chat if both happen to apply."""
        chat_ids = set(self._monitors.get(experiment_id, set()))
        opted_in_chat_id = self._links.get(_key(username))
        if opted_in_chat_id is not None:
            chat_ids.add(opted_in_chat_id)
        for chat_id in chat_ids:
            await self._send_raw(chat_id, text)

    async def notify_blackout(self, experiment_id: str, message: str) -> None:
        """Called once, right after ExperimentRunner.recover(), if the
        experiment that just resumed has active /monitor subscribers and a
        fresh RecoveryNotice. This is the one event /monitor can report that
        genuinely cannot be caught any other way: by the time a *new*
        /monitor request could be sent after a reboot, recover() has already
        run and this specific outage has already been detected -- so this
        push is what makes "tell me about blackouts" actually work, not
        just a description of what would happen if timing were kinder."""
        for chat_id in self._monitors.get(experiment_id, set()):
            await self._send_raw(chat_id, f"⚡ {message}")

    async def notify_completion(self, exp: ExperimentDir, status: ExperimentStatus) -> None:
        """Called from on_experiment_finished (main.py), after the AI
        summary has already been generated and stored -- sends whoever
        /monitor'd this run its first/last image, the summary, a plain
        settings recap, and a nudge to ask for the zip (download_experiment
        is a real tool call away, not sent automatically -- an unasked-for
        multi-MB upload is the wrong default). One-shot: the subscription is
        consumed here, a finished run has nothing further to report."""
        chat_ids = self._monitors.pop(exp.experiment_id, None)
        if not chat_ids:
            return
        _save_monitors(self._monitors_path, self._monitors)

        lines = [f"🏁 {exp.experiment_id} finished."]
        if status.message:
            lines.append(status.message)

        summary = assistant_summary.read_stored(exp)
        if summary is not None:
            lines.append("")
            lines.append(summary.get("textSummary") or "(no summary text)")
            if summary.get("moldDetected"):
                lines.append(
                    f"⚠️ Possible mold detected ({summary.get('moldFrameCount')}/"
                    f"{summary.get('framesChecked')} frames)."
                )

        try:
            xml_bytes = exp.read_config_xml()
            saved_cfg = config_xml.parse(xml_bytes) if xml_bytes else None
        except Exception:
            saved_cfg = None
        if saved_cfg is not None:
            lines.append("")
            lines.append(format_config_knobs(saved_cfg))

        lines.append("")
        lines.append('Want the images? Ask me to "download this experiment" and I\'ll zip it up.')
        text = _strip_markdown_lite("\n".join(lines))

        images = exp.list_capture_images()
        first_path = exp.thumb_file(images[0]["id"]) if images else None
        last_path = exp.thumb_file(images[-1]["id"]) if len(images) > 1 else None

        for chat_id in chat_ids:
            await self._send_raw(chat_id, text)
            if first_path is not None:
                await self._send_photo_file(chat_id, first_path, f"First: {images[0]['id']}")
            if last_path is not None:
                await self._send_photo_file(chat_id, last_path, f"Last: {images[-1]['id']}")
            # The progress bar has nothing further to report once finished --
            # unpin it and drop the tracking (best-effort; a failed unpin
            # just leaves a stale pin, not a real problem).
            key = (exp.experiment_id, chat_id)
            message_id = self._progress_messages.pop(key, None)
            self._progress_last_edit.pop(key, None)
            if message_id is not None:
                await self._unpin_message(chat_id, message_id)

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if not self.configured:
            log.info("telegram bot not configured -- issue alerts stay unavailable until set up")
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._poll_loop())
        if self._progress_worker is None or self._progress_worker.done():
            self._progress_worker = asyncio.create_task(self._progress_loop())

    async def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None
        if self._progress_worker is not None:
            self._progress_worker.cancel()
            try:
                await self._progress_worker
            except (asyncio.CancelledError, Exception):
                pass
            self._progress_worker = None
        await self._client.aclose()

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telegram poll failed")
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _progress_loop(self) -> None:
        while True:
            try:
                await self._progress_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telegram progress-bar tick failed")
            await asyncio.sleep(PROGRESS_TICK_S)

    async def _progress_tick(self) -> None:
        """Refreshes the pinned progress message for whoever's monitoring
        whatever is currently running -- at most one experiment at a time,
        since this device only ever runs one. A no-op the rest of the time
        (idle, nobody monitoring, or every pin refreshed too recently)."""
        if self._runner is None:
            return
        status = self._runner.status
        if status.experimentId is None or status.state not in ("running", "paused"):
            return
        chat_ids = self._monitors.get(status.experimentId)
        if not chat_ids:
            return
        exp = self._storage.get_experiment(status.experimentId)
        if exp is None:
            return

        text = self._build_progress_text(status, exp)
        now = time.monotonic()
        for chat_id in chat_ids:
            key = (status.experimentId, chat_id)
            message_id = self._progress_messages.get(key)
            if message_id is None:
                continue
            if now - self._progress_last_edit.get(key, 0.0) < PROGRESS_UPDATE_INTERVAL_S:
                continue
            if await self._edit_message(chat_id, message_id, text):
                self._progress_last_edit[key] = now

    def _build_progress_text(self, status: ExperimentStatus, exp: ExperimentDir) -> str:
        """Real, already-measured numbers only -- elapsedSeconds/
        totalSeconds/imagesCaptured are the runner's own live status (same
        source the Progress screen itself reads), and the last-capture
        timestamp comes from the actual newest image file, not an
        estimate."""
        pct = 0.0
        if status.totalSeconds > 0:
            pct = max(0.0, min(100.0, (status.elapsedSeconds / status.totalSeconds) * 100))
        filled = round(pct / 100 * PROGRESS_BAR_SEGMENTS)
        bar = "▓" * filled + "░" * (PROGRESS_BAR_SEGMENTS - filled)
        remaining = max(0.0, status.totalSeconds - status.elapsedSeconds)

        lines = [
            f"🔬 {status.experimentId}",
            f"[{bar}] {pct:.0f}%",
            f"{_format_duration(status.elapsedSeconds)} elapsed · {_format_duration(remaining)} left",
        ]
        captured = (
            f"{status.imagesCaptured}/{status.imagesPlanned} images"
            if status.imagesPlanned
            else f"{status.imagesCaptured} images"
        )
        images = exp.list_capture_images()
        if images:
            age = (datetime.now() - images[-1]["timestamp"]).total_seconds()
            captured += f" · last capture {_format_duration(age)} ago"
        lines.append(captured)
        lines.append("⚠️ possible issue detected" if status.issueDetected else "no anomalies detected")
        return "\n".join(lines)

    async def _poll_once(self) -> None:
        # Long-polling (timeout>0) would tie up this connection for the
        # whole poll window; a short interval with timeout=0 is simpler and
        # plenty responsive for both the linking code and chat.
        res = await self._client.get(
            f"/bot{self._token}/getUpdates", params={"offset": self._update_offset, "timeout": 0}
        )
        res.raise_for_status()
        for update in res.json().get("result", []):
            self._update_offset = max(self._update_offset, update["update_id"] + 1)
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            chat_id = (message.get("chat") or {}).get("id")
            if chat_id is None or not text:
                continue
            if await self._try_complete_link(text, chat_id):
                continue
            if await self._maybe_continue_launch_wizard(chat_id, text):
                continue
            parsed = _parse_command(text)
            if parsed is not None:
                await self._dispatch_command(chat_id, parsed[0], parsed[1])
                continue
            await self._handle_chat_message(chat_id, text)

    async def _maybe_continue_launch_wizard(self, chat_id: int, text: str) -> bool:
        """Returns True if `text` was consumed by an in-progress /launch
        wizard for this chat -- an answer, /cancel, or a fresh /launch
        restarting it. A wizard sitting untouched past
        LAUNCH_WIZARD_TIMEOUT_S is treated as abandoned and dropped instead,
        so it can never resurface to confuse an unrelated later chat."""
        state = self._launch_wizards.get(chat_id)
        if state is None:
            return False
        if time.monotonic() - state.last_activity > LAUNCH_WIZARD_TIMEOUT_S:
            del self._launch_wizards[chat_id]
            return False

        parsed = _parse_command(text)
        if parsed is not None and parsed[0] == "/cancel":
            del self._launch_wizards[chat_id]
            await self._send_raw(chat_id, "Cancelled -- nothing was changed.")
            return True
        if parsed is not None and parsed[0] == "/launch":
            await self._handle_launch_command(chat_id, parsed[1])
            return True

        state.last_activity = time.monotonic()
        if state.awaiting_confirmation:
            await self._handle_launch_confirmation(chat_id, state, text)
        else:
            await self._handle_launch_answer(chat_id, state, text)
        return True

    async def _dispatch_command(self, chat_id: int, command: str, arg: str) -> None:
        """Every recognized /command is a fixed, deterministic handler (a
        "script") -- never a model call, see the module docstring. An
        unrecognized /whatever gets a direct answer here rather than being
        forwarded to the LLM as a chat turn, which would just as likely
        produce a confused non-answer to something that was never meant as
        a real question."""
        if command == "/help":
            await self._handle_help_command(chat_id)
        elif command == "/status":
            await self._handle_status_command(chat_id)
        elif command == "/experiments":
            await self._handle_experiments_command(chat_id)
        elif command == "/unlink":
            await self._handle_unlink_command(chat_id)
        elif command == "/launch":
            await self._handle_launch_command(chat_id, arg)
        elif command == "/cancel":
            await self._send_raw(chat_id, "Nothing active to cancel.")
        elif command == "/monitor":
            await self._handle_monitor_command(chat_id)
        elif command == "/snapshot":
            await self._handle_snapshot_command(chat_id)
        elif command == "/screenshot":
            await self._handle_screenshot_command(chat_id)
        else:
            await self._send_raw(chat_id, f"I don't know {command} -- send /help to see what I can do.")

    async def _try_complete_link(self, text: str, chat_id: int) -> bool:
        """Returns True if `text` was a valid pending code (and has now been
        consumed) -- callers use this to decide whether to also treat the
        message as a chat turn."""
        now = time.monotonic()
        self._pending = {c: (u, exp) for c, (u, exp) in self._pending.items() if exp > now}
        entry = self._pending.pop(text, None)
        if entry is None:
            return False
        username, _ = entry
        self._links[username] = chat_id
        _save_links(self._links_path, self._links)
        log.info("telegram linked for %s", username)
        await self._send_raw(
            chat_id,
            f'✅ Linked to RapiDBoxes as "{username}". You can chat with PidiBot right here, '
            "and you'll get a message if an issue is detected during an experiment you opted "
            "in on.",
        )
        return True

    def _username_for_chat(self, chat_id: int) -> Optional[str]:
        for username, linked_chat_id in self._links.items():
            if linked_chat_id == chat_id:
                return username
        return None

    async def _handle_help_command(self, chat_id: int) -> None:
        """Works even from an unlinked chat -- someone's first message is
        plausibly "what can you do", and the answer needs to say "link
        first" rather than silently refusing like every other command."""
        lines = [
            "/status — is anything running right now, plus storage and camera (whole device, not just yours).",
            "/experiments — your own most recent experiments.",
            "/monitor — subscribe to your currently running experiment: anomalies, a blackout notice, and a "
            "completion summary with photos. Pins a live-updating progress bar here too.",
            "/launch [what you want] — walks through setting up a new experiment, one question at a time, "
            "starting from a past run's settings (or defaults). Ends with a summary to confirm -- \"yes\" "
            "actually starts it, real camera and lighting, right then. If it can't (busy/no camera/low "
            "space), it loads the settings on the setup screen instead. /cancel stops it anytime.",
            "/snapshot — a real photo with your current camera and light settings (not the Live preview's "
            "fudged fast exposure). If your own experiment is running, sends its most recent capture instead.",
            "/screenshot — whatever's actually on the kiosk's touchscreen right now.",
            "/unlink — disconnect this Telegram account from RapidBoxes.",
            "/help — this list.",
            "",
            "Anything else you send me is a normal question — ask about your settings, storage, past runs, "
            "or images.",
        ]
        if self._username_for_chat(chat_id) is None:
            lines.insert(0, "This chat isn't linked yet -- link it first: Settings → General → Telegram Alerts.\n")
        await self._send_raw(chat_id, "\n".join(lines))

    async def _handle_status_command(self, chat_id: int) -> None:
        """Deliberately device-wide, not scoped to the asker -- see
        AssistantService.resolve_system_status's own docstring for why
        that's the one exception to every other command here being
        strictly personal."""
        if self._username_for_chat(chat_id) is None:
            await self._send_raw(
                chat_id,
                "I don't recognize this Telegram account yet -- link it first on the device: "
                "Settings → General → Telegram Alerts.",
            )
            return
        if self._assistant is None:
            await self._send_raw(chat_id, "System status isn't available right now -- try again shortly.")
            return
        await self._send_raw(chat_id, self._assistant.resolve_system_status())

    async def _handle_experiments_command(self, chat_id: int) -> None:
        username = self._username_for_chat(chat_id)
        if username is None:
            await self._send_raw(
                chat_id,
                "I don't recognize this Telegram account yet -- link it first on the device: "
                "Settings → General → Telegram Alerts.",
            )
            return
        if self._assistant is None:
            await self._send_raw(chat_id, "That isn't available right now -- try again shortly.")
            return
        # No "username" arg -- resolve_list_experiments then defaults to the
        # requester's own experiments only, never another user's.
        await self._send_raw(chat_id, self._assistant.resolve_list_experiments({}, username))

    async def _handle_unlink_command(self, chat_id: int) -> None:
        username = self._username_for_chat(chat_id)
        if username is None:
            await self._send_raw(chat_id, "This Telegram account isn't linked to anything, so there's nothing to unlink.")
            return
        del self._links[_key(username)]
        _save_links(self._links_path, self._links)
        await self._send_raw(
            chat_id,
            f'Unlinked. "{username}" on RapidBoxes is no longer connected to this chat -- link again anytime '
            "from Settings → General → Telegram Alerts.",
        )

    async def _handle_launch_command(self, chat_id: int, arg: str) -> None:
        """Starts (or restarts) a guided, one-field-at-a-time wizard for a
        new experiment's config. Seeds "currently set" defaults from a past
        experiment the same way resolve_prefill_experiment already resolves
        one (most recent, or matching `arg`'s free text) -- deterministic,
        no model call. Confirming the final summary actually starts the
        experiment (AssistantService.start_experiment_from_launch) -- the
        one real exception to every other tool here never touching
        hardware, gated on every field having been answered, range-checked,
        and shown back for explicit human confirmation first."""
        username = self._username_for_chat(chat_id)
        if username is None:
            await self._send_raw(
                chat_id,
                "I don't recognize this Telegram account yet -- link it first on the device: "
                "Settings → General → Telegram Alerts.",
            )
            return
        if self._assistant is None:
            await self._send_raw(chat_id, "That isn't available right now -- try again shortly.")
            return

        # No "username" arg -- always the requester's own past experiments,
        # even if their free text names someone else.
        proposal, _reply = self._assistant.resolve_prefill_experiment({"reference": arg}, username)
        base = proposal.config if proposal is not None else SavedExperimentConfig()

        state = _LaunchWizardState(
            username=username, base=base, fields=[_LAUNCH_PROTOCOL_FIELD, _LAUNCH_NAME_FIELD]
        )
        self._launch_wizards[chat_id] = state

        await self._send_raw(chat_id, self._build_launch_overview(base, resolved=proposal is not None))
        await self._ask_current_launch_field(chat_id, state)

    def _build_launch_overview(self, base: SavedExperimentConfig, resolved: bool) -> str:
        intro = (
            "Let's set up a new experiment. Here's what's currently set, from your last run:"
            if resolved
            else "Let's set up a new experiment. No past run found, so here are the defaults:"
        )
        protocol_fields = _LAUNCH_TROPISM_FIELDS if base.protocol == "tropism" else _LAUNCH_GROWTH_FIELDS
        lines = [f"{_LAUNCH_PROTOCOL_FIELD.label} ({base.protocol})"]
        for f in protocol_fields:
            lines.append(f"{f.label} ({f.format(getattr(base, f.name))})")
        # Nested under base.camera, not a top-level field -- can't go
        # through the generic getattr(base, f.name) loop above.
        lines.append(f"{_LAUNCH_COLOR_FIELD.label} ({_LAUNCH_COLOR_FIELD.format(base.camera.grayscale)})")
        lines.append(f"{_LAUNCH_ISSUE_ALERT_FIELD.label} ({_LAUNCH_ISSUE_ALERT_FIELD.format(base.reportOnIssueEnabled)})")
        body = "\n".join(lines)
        return (
            f"{intro}\n\n{body}\n\n"
            "I'll ask about each one, starting with the measurement type -- reply with a new value, "
            "or resend the current one to keep it. Send /cancel anytime to stop."
        )

    @staticmethod
    def _current_launch_field_value(state: _LaunchWizardState, f: _LaunchField) -> object:
        """Nested camera fields (grayscale/exposureMicroseconds) live under
        state.base.camera / state.camera_overrides, not the flat
        state.values/state.base the generic fields use -- see
        _LaunchWizardState's own docstring comments for why."""
        if f.name == "grayscale":
            return state.camera_overrides.get("grayscale", state.base.camera.grayscale)
        if f.name == "exposureMicroseconds":
            return state.camera_overrides.get("exposureMicroseconds", state.base.camera.exposureMicroseconds)
        if f.name == "exposureOverride":
            return None  # a one-off gate, no meaningful "current" value to show
        return state.values.get(f.name, getattr(state.base, f.name, None))

    async def _ask_current_launch_field(self, chat_id: int, state: _LaunchWizardState) -> None:
        f = state.fields[state.index]
        current = self._current_launch_field_value(state, f)
        current_text = f.format(current) if current is not None else "not set"
        await self._send_raw(chat_id, f"{f.prompt} (currently: {current_text})")

    async def _handle_launch_answer(self, chat_id: int, state: _LaunchWizardState, text: str) -> None:
        f = state.fields[state.index]
        try:
            value = f.parse(text)
        except ValueError as exc:
            current = self._current_launch_field_value(state, f)
            current_text = f.format(current) if current is not None else "not set"
            await self._send_raw(chat_id, f"⚠️ {exc}. {f.prompt} (currently: {current_text})")
            return  # re-ask the same field -- index does not advance

        if f.name == "experimentName":
            state.experiment_name = value
        elif f.name in ("grayscale", "exposureMicroseconds"):
            state.camera_overrides[f.name] = value
        elif f.name == "exposureOverride":
            pass  # control-flow only -- never part of the resolved config
        else:
            state.values[f.name] = value

        if f.name == "protocol":
            extra = _LAUNCH_TROPISM_FIELDS if value == "tropism" else _LAUNCH_GROWTH_FIELDS
            state.fields.extend(extra)
            state.fields.append(_LAUNCH_COLOR_FIELD)
            state.fields.append(_LAUNCH_EXPOSURE_OVERRIDE_FIELD)
            state.fields.append(_LAUNCH_ISSUE_ALERT_FIELD)
        elif f.name == "exposureOverride" and value:
            source = state.values.get("photoIlluminationSource", state.base.photoIlluminationSource)
            state.fields.insert(state.index + 1, _build_exposure_field(source))

        state.index += 1

        # A disabled dark phase has no meaningful length to ask about.
        if (
            state.index < len(state.fields)
            and state.fields[state.index].name == "darkPhaseHours"
            and state.values.get("darkPhaseEnabled") is False
        ):
            state.values["darkPhaseHours"] = 0.0
            state.index += 1

        if state.index >= len(state.fields):
            await self._finish_launch_wizard(chat_id, state)
            return
        await self._ask_current_launch_field(chat_id, state)

    async def _finish_launch_wizard(self, chat_id: int, state: _LaunchWizardState) -> None:
        state.awaiting_confirmation = True
        final_config = state.base.model_copy(update=state.values)
        if state.camera_overrides:
            final_config.camera = final_config.camera.model_copy(update=state.camera_overrides)
        state.final_config = final_config

        lines = [format_config_knobs(final_config)]
        lines.append(f"Image color: {'black-and-white' if final_config.camera.grayscale else 'color'}")
        if "exposureMicroseconds" in state.camera_overrides:
            lines.append(f"Exposure (manual override): {final_config.camera.exposureMicroseconds / 1_000_000:g}s")
        summary = "\n".join(lines)

        await self._send_raw(
            chat_id,
            f'Ready to review:\n\nName: {state.experiment_name}\n{summary}\n\n'
            'Look right? Reply "yes" to start it, or "no" to cancel.',
        )

    async def _handle_launch_confirmation(self, chat_id: int, state: _LaunchWizardState, text: str) -> None:
        answer = text.strip().lower()
        if answer in ("yes", "y", "confirm", "confirmed", "start"):
            del self._launch_wizards[chat_id]
            await self._confirm_and_start_launch(chat_id, state)
            return
        if answer in ("no", "n", "cancel"):
            del self._launch_wizards[chat_id]
            await self._send_raw(chat_id, "Cancelled -- nothing was changed. Send /launch to start over.")
            return
        await self._send_raw(chat_id, 'Please reply "yes" to start it or "no" to cancel.')

    async def _confirm_and_start_launch(self, chat_id: int, state: _LaunchWizardState) -> None:
        """Only ever reached after every field was answered, range-checked,
        and shown back for explicit human confirmation -- see
        AssistantService.start_experiment_from_launch for what actually
        touches hardware. If it can't actually start right now (something's
        already running, no camera, not enough space, or the service isn't
        available), the resolved config is staged instead so the trip to
        the device at least starts from a filled-in setup screen rather
        than nothing."""
        if self._assistant is None or state.final_config is None or state.experiment_name is None:
            await self._send_raw(chat_id, "Something went wrong -- send /launch to try again.")
            return

        response, message = await self._assistant.start_experiment_from_launch(
            state.final_config, state.experiment_name, state.username, state.camera_overrides
        )
        if response is not None and response.status == "started":
            await self._send_raw(chat_id, f"🚀 {message}")
            return

        self._assistant.stage_pending_launch(state.username, state.final_config)
        await self._send_raw(
            chat_id,
            f"{message} I've loaded the settings on the setup screen instead -- once that's sorted, "
            "you can press Start there yourself.",
        )

    async def _handle_snapshot_command(self, chat_id: int) -> None:
        """A real camera capture with the device's actual current settings
        -- see AssistantService.capture_snapshot for the full behavior
        (including its own-experiment-running fallback). Deterministic
        dispatch here, the actual hardware call lives on AssistantService
        since it already holds the runner/app-state wiring this needs."""
        username = self._username_for_chat(chat_id)
        if username is None:
            await self._send_raw(
                chat_id,
                "I don't recognize this Telegram account yet -- link it first on the device: "
                "Settings → General → Telegram Alerts.",
            )
            return
        if self._assistant is None:
            await self._send_raw(chat_id, "That isn't available right now -- try again shortly.")
            return

        frame, message = await self._assistant.capture_snapshot(username)
        if frame is None:
            await self._send_raw(chat_id, message)
            return
        await self._send_photo_bytes(chat_id, "snapshot.jpg", frame, message)

    async def _handle_screenshot_command(self, chat_id: int) -> None:
        """Whatever is actually on the kiosk's own touchscreen right now --
        see kiosk_screenshot.py. Unlike every other command, this is
        deliberately device-wide, not scoped to the asker: the kiosk is one
        shared screen, so a screenshot of it can show whatever anyone is
        currently doing there, the same "device-wide" precedent /status
        already set. Never touches the camera/experiment hardware at all,
        so it works regardless of whether an experiment is running."""
        if self._username_for_chat(chat_id) is None:
            await self._send_raw(
                chat_id,
                "I don't recognize this Telegram account yet -- link it first on the device: "
                "Settings → General → Telegram Alerts.",
            )
            return

        try:
            png = await capture_kiosk_screenshot()
        except KioskScreenshotUnavailable as exc:
            await self._send_raw(chat_id, f"Couldn't capture the screen: {exc}")
            return
        await self._send_photo_bytes(chat_id, "screenshot.png", png, "Current kiosk screen.")

    async def _handle_monitor_command(self, chat_id: int) -> None:
        """/monitor subscribes the sender to their own currently-running
        experiment: anomalies (via MoldWatchService, whether or not
        reportOnIssueEnabled was ticked at setup), a blackout/recovery
        notice if the device restarts mid-run, and a completion summary
        with first/last image when it finishes. Strictly scoped like every
        other tool here -- only your own running experiment, resolved from
        the link store, never a name you could type."""
        username = self._username_for_chat(chat_id)
        if username is None:
            await self._send_raw(
                chat_id,
                "I don't recognize this Telegram account yet -- link it first on the device: "
                "Settings → General → Telegram Alerts.",
            )
            return
        if self._runner is None:
            await self._send_raw(chat_id, "Monitoring isn't available right now -- try again shortly.")
            return

        status = self._runner.status
        if (
            status.experimentId is None
            or status.username is None
            or _key(status.username) != _key(username)
            or status.state not in ("running", "paused")
        ):
            await self._send_raw(chat_id, "You don't have an experiment running right now.")
            return

        self._monitors.setdefault(status.experimentId, set()).add(chat_id)
        _save_monitors(self._monitors_path, self._monitors)
        await self._send_raw(
            chat_id,
            f"🔎 Now monitoring {status.experimentId}. I'll message you here about anomalies, "
            "a power/connectivity blackout, and when it finishes.",
        )

        # Pin a live progress bar too -- unless this chat already has one for
        # this experiment (re-sending /monitor must not spawn a second pin).
        key = (status.experimentId, chat_id)
        if key not in self._progress_messages:
            exp = self._storage.get_experiment(status.experimentId)
            if exp is not None:
                message_id = await self._send_and_pin(chat_id, self._build_progress_text(status, exp))
                if message_id is not None:
                    self._progress_messages[key] = message_id
                    self._progress_last_edit[key] = time.monotonic()

    async def _handle_chat_message(self, chat_id: int, text: str) -> None:
        """Routes a non-code message to the same AssistantService the web
        chat uses -- same tools, same strict per-user scoping (the
        `username` passed here is never taken from anything the sender
        typed, only from the link store), just a different transport."""
        username = self._username_for_chat(chat_id)
        if username is None:
            await self._send_raw(
                chat_id,
                "I don't recognize this Telegram account yet -- link it first on the device: "
                "Settings → General → Telegram Alerts.",
            )
            return
        if self._assistant is None:
            await self._send_raw(chat_id, "Chat isn't available right now -- try again shortly.")
            return

        history = self._chat_history.setdefault(chat_id, [])
        try:
            response = await self._assistant.chat(text, history, username)
        except AssistantUnavailable as exc:
            await self._send_raw(chat_id, f"The assistant isn't reachable right now: {exc}")
            return
        except Exception:
            log.exception("telegram chat dispatch failed for %s", username)
            await self._send_raw(chat_id, "Something went wrong handling that -- try again.")
            return

        history.append(AssistantMessage(role="user", content=text))
        history.append(AssistantMessage(role="assistant", content=response.reply))
        del history[:-MAX_CHAT_HISTORY]

        await self._send_raw(chat_id, _strip_markdown_lite(response.reply))
        if response.image is not None:
            await self._send_photo(chat_id, response.image)
        if response.download is not None:
            await self._send_download(chat_id, response.download)
