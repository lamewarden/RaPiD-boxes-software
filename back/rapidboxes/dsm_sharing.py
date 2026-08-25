"""Synology DSM sharing links: turn an experiment already on the NAS into a
real, clickable, internet-reachable URL (https://<host>:5001/sharing/<id>) --
the same kind of link a person gets from DSM's own File Station "Share"
button, generated instead via DSM's Web API
(SYNO.API.Auth + SYNO.FileStation.Sharing), so PidiBot can hand one out in
chat without a human opening File Station themselves.

**A different server than Remote Sync's CIFS share.** ds.asuch.cas.cz (the
CIFS mount in remote_sync.py) and ds-ueb-if.asuch.cas.cz (this module) turned
out to resolve to two different IPs -- genuinely different NAS boxes, found
by checking DNS before assuming anything. So this is deliberately its own
service with its own settings/credentials, not an extension of
RemoteSyncService: the CIFS username/password authenticate an SMB mount on
one box, and have no reason to also be valid DSM logins on a different one.

**File Station Sharing needs a DSM-internal path, not the CIFS UNC path.**
Synology's own volume numbering (e.g. "/volume1/ueb-if") can't be derived
from a share name, so `shareRoot` is entered by whoever set this up (same
precedent as Remote Sync's `server` field) -- this module only ever appends
<username>/<experiment_id> under it, mirroring RemoteSyncService.remote_path_for's
own <researcher>/<experiment> layout.

**Same password handling as Remote Sync.** Session-only: held in `_password`
here and nowhere else, never persisted to disk, never returned by any API
response, lost on every restart (surfaced as `credentialsRequired`, same
loud state as Remote Sync's own).

**This module never uploads anything.** It only asks DSM for a sharing link
to a path that (per the "Replace it" decision -- Remote Sync's CIFS
destination now points at this same NAS) already has files on it, copied
there by the existing Remote Sync flow. If nothing is at that path yet, DSM
itself reports the failure (e.g. "no such file or directory") -- there is no
separate "does the file exist" check here.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional, Tuple

import httpx

from .models import DsmSharingSettings, DsmSharingStatus

log = logging.getLogger("rapidboxes.dsm_sharing")

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str) -> str:
    return _SAFE.sub("-", (text or "").strip()) or "x"


# DSM API error codes worth a specific, actionable message. Anything else
# falls back to a generic "(code N)" -- still real information, just not
# hand-translated. See Synology's Web API guide for the full list; these are
# the ones someone entering credentials wrong would actually hit.
_AUTH_ERROR_MESSAGES = {
    400: "no such account or incorrect password",
    401: "this account is disabled",
    402: "permission denied for this account",
    403: "two-factor authentication is required for this account",
    404: "two-factor authentication code was rejected",
    406: "this account must enable two-factor authentication",
}


class DsmSharingService:
    """Owns the session-only DSM password and the (stateless, log-in-per-call)
    sharing-link creation. Unlike RemoteSyncService there is no persistent
    mount or background worker: link generation is rare enough (an on-demand
    chat request, not a per-capture event) that a fresh login for each call
    is simpler and avoids stale-session bugs, at the cost of one extra round
    trip per request -- an acceptable trade for how infrequently this runs.
    """

    def __init__(
        self,
        settings: DsmSharingSettings,
        *,
        settings_path: Optional[Path] = None,
        timeout: float = 15.0,
    ):
        self.settings = settings
        self._settings_path = settings_path
        # Session-only. Never persisted, never serialised, never logged --
        # same precedent as RemoteSyncService._password.
        self._password: Optional[str] = None
        self._last_result: Optional[str] = None
        self._last_error: Optional[str] = None
        self._client = httpx.AsyncClient(timeout=timeout)

    async def shutdown(self) -> None:
        self._password = None
        await self._client.aclose()

    # --- configuration -----------------------------------------------------
    @property
    def password_set(self) -> bool:
        return bool(self._password)

    @property
    def credentials_required(self) -> bool:
        return self.settings.enabled and not self._password

    def set_password(self, password: str) -> None:
        self._password = password or None

    def clear_password(self) -> None:
        self._password = None

    def persist(self) -> None:
        """Write the non-secret settings. No password field exists on
        DsmSharingSettings, so there is nothing here that could leak one."""
        if self._settings_path is None:
            return
        try:
            save_dsm_sharing_settings(self._settings_path, self.settings)
        except Exception:
            log.exception("failed to persist DSM sharing settings")

    def remote_path_for(self, username: str) -> str:
        """The DSM-internal path an experiment folder lands under, mirroring
        RemoteSyncService.remote_path_for's <researcher>/<experiment> layout
        (just DSM-path- rather than filesystem-Path-flavored, since this
        never touches the local disk)."""
        root = self.settings.shareRoot.rstrip("/")
        return f"{root}/{_slug(username)}"

    def status(self) -> DsmSharingStatus:
        return DsmSharingStatus(
            enabled=self.settings.enabled,
            host=self.settings.host,
            port=self.settings.port,
            username=self.settings.username,
            shareRoot=self.settings.shareRoot,
            passwordSet=self.password_set,
            credentialsRequired=self.credentials_required,
            lastResult=self._last_result,
            lastError=self._last_error,
        )

    # --- DSM API -------------------------------------------------------------
    def _base_url(self) -> str:
        return f"https://{self.settings.host}:{self.settings.port}"

    async def _login(self) -> Tuple[Optional[str], str]:
        """Returns (sid, message). sid is None on any failure -- wrong
        password, unreachable host, disabled account are all reported with
        the real reason, same "tell them what actually broke" precedent as
        RemoteSyncService.check_connection."""
        try:
            res = await self._client.get(
                f"{self._base_url()}/webapi/entry.cgi",
                params={
                    "api": "SYNO.API.Auth",
                    "version": "6",
                    "method": "login",
                    "account": self.settings.username,
                    "passwd": self._password,
                    "session": "FileStation",
                    "format": "sid",
                },
            )
            res.raise_for_status()
            body = res.json()
        except httpx.HTTPError as exc:
            return None, f"could not reach {self.settings.host}: {exc}"
        except ValueError:
            return None, f"{self.settings.host} did not return a valid response"

        if not body.get("success"):
            code = (body.get("error") or {}).get("code")
            reason = _AUTH_ERROR_MESSAGES.get(code, f"login failed (code {code})")
            return None, reason
        sid = (body.get("data") or {}).get("sid")
        if not sid:
            return None, "login succeeded but no session was returned"
        return sid, "ok"

    async def _logout(self, sid: str) -> None:
        """Best-effort -- a failed logout leaves a session that DSM expires
        on its own; it must never turn into a user-visible error."""
        try:
            await self._client.get(
                f"{self._base_url()}/webapi/entry.cgi",
                params={
                    "api": "SYNO.API.Auth",
                    "version": "6",
                    "method": "logout",
                    "session": "FileStation",
                    "_sid": sid,
                },
            )
        except httpx.HTTPError:
            pass

    async def check_connection(self) -> Tuple[bool, str]:
        """Logs in and straight back out -- proves the host/username/password
        actually work without creating a share link for anything."""
        if not self.settings.host or not self.settings.username or not self._password:
            self._last_result, self._last_error = "error", "host, username and password are required"
            return False, self._last_error
        sid, message = await self._login()
        if sid is None:
            self._last_result, self._last_error = "error", message
            return False, message
        await self._logout(sid)
        self._last_result, self._last_error = "ok", None
        return True, f"Connected to {self.settings.host} as {self.settings.username}."

    async def create_share_link(self, username: str, experiment_id: str) -> Tuple[bool, str]:
        """Logs in, asks DSM for a sharing link to
        <shareRoot>/<username>/<experiment_id>, logs back out. Returns
        (ok, url_or_error_message). Never raises -- same never-break-the-
        caller precedent as RemoteSyncService's public methods."""
        if not self.settings.enabled or not self._password:
            self._last_result, self._last_error = "error", "DSM sharing isn't connected right now."
            return False, self._last_error
        if not self.settings.shareRoot:
            self._last_result, self._last_error = "error", "no DSM share root is configured."
            return False, self._last_error

        sid, message = await self._login()
        if sid is None:
            self._last_result, self._last_error = "error", message
            return False, f"Could not connect to {self.settings.host}: {message}"

        path = f"{self.remote_path_for(username)}/{_slug(experiment_id)}"
        try:
            res = await self._client.get(
                f"{self._base_url()}/webapi/entry.cgi",
                params={
                    "api": "SYNO.FileStation.Sharing",
                    "version": "3",
                    "method": "create",
                    "path": f'["{path}"]',
                    "_sid": sid,
                },
            )
            res.raise_for_status()
            body = res.json()
        except httpx.HTTPError as exc:
            self._last_result, self._last_error = "error", str(exc)
            return False, f"Could not reach {self.settings.host} to create the link: {exc}"
        except ValueError:
            self._last_result, self._last_error = "error", "invalid response"
            return False, f"{self.settings.host} did not return a valid response."
        finally:
            await self._logout(sid)

        if not body.get("success"):
            code = (body.get("error") or {}).get("code")
            self._last_result, self._last_error = "error", f"code {code}"
            return False, (
                f"DSM couldn't share {path} (code {code}) -- it may not exist there yet, "
                "or this account may lack Sharing permission."
            )
        links = (body.get("data") or {}).get("links") or []
        if not links or not links[0].get("url"):
            self._last_result, self._last_error = "error", "no link returned"
            return False, "DSM didn't return a sharing link."

        self._last_result, self._last_error = "ok", None
        return True, links[0]["url"]


# ---------------------------------------------------------------------------
# Persistence of the non-secret settings (same pattern as remote_sync.py's).
# ---------------------------------------------------------------------------


def load_dsm_sharing_settings(path: Path) -> DsmSharingSettings:
    if path.exists():
        try:
            return DsmSharingSettings.model_validate_json(path.read_text())
        except Exception:
            log.exception("invalid DSM sharing settings %s; using defaults", path)
    return DsmSharingSettings()


def save_dsm_sharing_settings(path: Path, settings: DsmSharingSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(settings.model_dump_json(indent=2))
    os.replace(tmp, path)
