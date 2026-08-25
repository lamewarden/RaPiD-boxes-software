"""Opt-in issue-alert delivery over Telegram (see assistant/mold_watch.py),
replacing an earlier email-based design that was deferred for lack of any
mail infrastructure. Telegram was chosen specifically because it needs
neither a personal mailbox for the device nor any institutional mail relay
-- just a bot token (created once via @BotFather) and a per-researcher
linking step.

Linking flow (private DM per researcher, not one shared group -- so an
alert only ever reaches the person who opted in for their own experiment):

1. A researcher taps "Link" in Settings -> General -> Telegram Alerts. The
   backend hands back a short-lived one-time code (request_link_code) and
   the bot's @username, shown on screen.
2. They open Telegram, message the bot, and send that code as a plain
   message.
3. This service's background poll (Telegram's getUpdates, no webhook/public
   URL needed -- this device is not internet-reachable) picks up the
   message, matches the code, and persists chat_id under that username.
4. From then on, send_message(username, text) can DM them directly.

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
import secrets
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx

log = logging.getLogger("rapidboxes.telegram")

API_BASE = "https://api.telegram.org"
LINK_CODE_TTL_S = 600.0  # 10 minutes -- long enough to switch apps and type it, short enough that a stale code isn't a standing risk.
POLL_INTERVAL_S = 3.0


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


class TelegramLinkService:
    def __init__(self, bot_token: Optional[str], bot_username: Optional[str], links_path: Path):
        self._token = bot_token
        self.bot_username = bot_username
        self._links_path = links_path
        self._links: Dict[str, int] = _load_links(links_path)
        # code -> (username, expires_at). Only ever touched from the single
        # event loop thread (API handlers + the poll task), no lock needed.
        self._pending: Dict[str, Tuple[str, float]] = {}
        self._update_offset = 0
        self._worker: Optional[asyncio.Task] = None
        self._client = httpx.AsyncClient(base_url=API_BASE, timeout=15.0)

    @property
    def configured(self) -> bool:
        return bool(self._token and self.bot_username)

    def is_linked(self, username: str) -> bool:
        return _key(username) in self._links

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
        try:
            res = await self._client.post(
                f"/bot{self._token}/sendMessage", json={"chat_id": chat_id, "text": text}
            )
            res.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.warning("telegram sendMessage failed for %s: %s", username, exc)
            return False

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
        # plenty responsive for a "type a 6-digit code" flow.
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
            await self._try_complete_link(text, chat_id)

    async def _try_complete_link(self, text: str, chat_id: int) -> None:
        now = time.monotonic()
        self._pending = {c: (u, exp) for c, (u, exp) in self._pending.items() if exp > now}
        entry = self._pending.pop(text, None)
        if entry is None:
            return
        username, _ = entry
        self._links[username] = chat_id
        _save_links(self._links_path, self._links)
        log.info("telegram linked for %s", username)
        try:
            await self._client.post(
                f"/bot{self._token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": (
                        f'✅ Linked to RapiDBoxes as "{username}". You\'ll get a message here '
                        "if an issue is detected during an experiment you opted in on."
                    ),
                },
            )
        except httpx.HTTPError:
            pass  # best-effort confirmation only -- the link itself already succeeded
