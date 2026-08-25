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
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

import httpx

from . import config_xml
from .assistant import summary as assistant_summary
from .assistant.service import AssistantUnavailable, format_config_knobs
from .models import AssistantDownloadRef, AssistantImageRef, AssistantMessage, ExperimentStatus
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


def _key(username: str) -> str:
    return username.strip().lower()


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
        self._update_offset = 0
        self._worker: Optional[asyncio.Task] = None
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
            res = await self._client.post(
                f"/bot{self._token}/sendPhoto",
                data={"chat_id": str(chat_id), "caption": caption},
                files={"photo": (path.name, data, "image/jpeg")},
            )
            res.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
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
        tmp_path = exp.zip_to_temp_file()
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

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if not self.configured:
            log.info("telegram bot not configured -- issue alerts stay unavailable until set up")
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._poll_loop())

    async def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None
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
            if text.split("@")[0].strip().lower() == "/monitor":
                await self._handle_monitor_command(chat_id)
                continue
            await self._handle_chat_message(chat_id, text)

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
