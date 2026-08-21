"""Remote CIFS/SMB sync: mount an institutional share and copy images to it.

Replaces the legacy hardcoded

    sudo mount -t cifs //ds.asuch.cas.cz/ueb/lhr /mnt/Shared -o user=...,pass=...

with something configurable from the UI and, crucially, safe on a box that has
no API authentication and binds 0.0.0.0.

Remote layout (mirrors the legacy convention):

    <mount point>/<researcher>/<experiment folder>/<images + metadata>

SECURITY NOTES (all four are load-bearing, please keep them):

1. **The password is session-only.** It lives in `_password` on this object and
   nowhere else: not in remote_sync.json, not in settings.json, not in a
   credentials file that outlives the mount call, not in a log line. A restart
   (including the monthly OTA one) loses it by design, which the API surfaces
   as `credentialsRequired` so the UI can say so loudly.

2. **The password never reaches a process argument list.** `ps aux` is
   world-readable, so `-o pass=...` is not an option. It is written to a
   0600 file created with `tempfile.mkstemp` and passed as `-o credentials=`;
   the file is unlinked in a `finally` the moment `mount` returns, success or
   failure. (The stdin alternative was rejected: `mount.cifs` only prompts
   under conditions we would have to guess at, and a mis-guess hangs the
   mount; the credentials file is the documented, deterministic route.)

3. **No `shell=True`, ever.** Every subprocess is a fixed argument list. The
   server string is additionally validated against a strict allowlist
   (`validate_remote_server` in models.py) before it can reach the command, so
   a value like `//h/s -o suid` or `-oremount` cannot smuggle mount options.

4. **The sudo grant is narrow.** deploy/install.sh writes an /etc/sudoers.d
   entry allowing exactly this mount (fixed mount point, fixed trailing option
   string) and the matching umount -- never a blanket ALL. The hardening
   options are deliberately the *last* thing on the option string: mount
   options are last-one-wins, so even if something unexpected were injected
   earlier, `nosuid,nodev,noexec` and the unprivileged uid/gid still win.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .models import (
    RemoteSyncSettings,
    RemoteSyncStatus,
    validate_remote_server,
    validate_remote_username,
)

log = logging.getLogger("rapidboxes.remote_sync")

# Where the share is mounted on the Pi. Fixed (not user-settable) precisely so
# the sudoers rule can pin it literally.
MOUNT_POINT = Path("/mnt/rapidboxes-remote")

# Credentials files are created here when it exists: the systemd unit declares
# `RuntimeDirectory=rapidboxes-cifs` (mode 0700, owned by the service user), and
# the sudoers rule pins this directory in the allowed `credentials=` path.
# /run is tmpfs, so nothing survives a reboot even if a cleanup were missed.
CREDENTIALS_DIR = Path("/run/rapidboxes-cifs")
CREDENTIALS_PREFIX = "cred-"

# Absolute paths, preferred in order: sudoers matches the literal command we
# invoke, so this must agree with what deploy/install.sh writes.
_SUDO_CANDIDATES = ("/usr/bin/sudo", "/bin/sudo")
_MOUNT_CANDIDATES = ("/usr/bin/mount", "/bin/mount")
_UMOUNT_CANDIDATES = ("/usr/bin/umount", "/bin/umount")

MOUNT_TIMEOUT_S = 25.0
UMOUNT_TIMEOUT_S = 15.0
COPY_TIMEOUT_S = 120.0

# A failed item is retried on the next capture (see _flush_failed); this caps
# how many attempts one file gets before it is dropped from the retry set, so a
# permanently unreadable file cannot wedge the queue forever.
MAX_ATTEMPTS = 5
# Bound on the backlog we keep in memory, so a week-long run against a dead
# share cannot grow without limit.
MAX_PENDING = 2000

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str) -> str:
    return _SAFE.sub("-", (text or "").strip()) or "x"


def _first_existing(candidates: Sequence[str], name: str) -> Optional[str]:
    for path in candidates:
        if os.path.exists(path):
            return path
    found = shutil.which(name)
    return found


def sudo_binary() -> Optional[str]:
    return _first_existing(_SUDO_CANDIDATES, "sudo")


def mount_binary() -> Optional[str]:
    return _first_existing(_MOUNT_CANDIDATES, "mount")


def umount_binary() -> Optional[str]:
    return _first_existing(_UMOUNT_CANDIDATES, "umount")


def fixed_mount_options() -> str:
    """The literal, non-negotiable tail of the mount option string.

    Kept last on purpose (mount options are last-one-wins) and mirrored
    verbatim by the sudoers rule deploy/install.sh installs.
    """
    return (
        "nosuid,nodev,noexec,"
        f"uid={os.getuid()},gid={os.getgid()},"
        "file_mode=0664,dir_mode=0775"
    )


def build_mount_argv(server: str, mount_point: Path, credentials_path: str) -> List[str]:
    """The exact argument list handed to subprocess.run (never a shell string).

    `server` must already have passed validate_remote_server().
    """
    sudo = sudo_binary()
    mount = mount_binary()
    if sudo is None or mount is None:
        raise FileNotFoundError("sudo/mount not available on this system")
    return [
        sudo,
        "-n",  # never prompt: the sudoers grant is NOPASSWD or we fail loudly
        mount,
        "-t",
        "cifs",
        server,
        str(mount_point),
        "-o",
        f"credentials={credentials_path},{fixed_mount_options()}",
    ]


def build_umount_argv(mount_point: Path) -> List[str]:
    sudo = sudo_binary()
    umount = umount_binary()
    if sudo is None or umount is None:
        raise FileNotFoundError("sudo/umount not available on this system")
    return [sudo, "-n", umount, str(mount_point)]


def _write_credentials_file(username: str, password: str) -> str:
    """Create a 0600 credentials file and return its path.

    The caller MUST unlink it in a `finally` as soon as mount returns.
    """
    directory = CREDENTIALS_DIR if os.path.isdir(CREDENTIALS_DIR) else None
    fd, path = tempfile.mkstemp(prefix=CREDENTIALS_PREFIX, dir=str(directory) if directory else None)
    try:
        os.fchmod(fd, 0o600)  # mkstemp is already 0600; make it explicit and robust
        with os.fdopen(fd, "w") as f:
            f.write("username=%s\npassword=%s\n" % (username, password))
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def is_mount_point(path: Path) -> bool:
    try:
        return path.is_mount()  # py3.7+: Path.is_mount
    except Exception:
        return False


@dataclass
class _Job:
    kind: str  # "image" | "bulk"
    researcher: str
    experiment_id: str = ""
    files: List[Path] = field(default_factory=list)


@dataclass
class _Pending:
    src: Path
    researcher: str
    experiment_id: str
    attempts: int = 0


class RemoteSyncService:
    """Owns the mount, the session password, and the background copy queue.

    Every public coroutine is safe to call from an API handler. The only entry
    point used from the capture path is `enqueue_image`, which is synchronous,
    non-blocking and swallows everything: a dead share must never delay or fail
    an experiment.
    """

    def __init__(
        self,
        settings: RemoteSyncSettings,
        *,
        storage_root: Path,
        simulation: bool = False,
        settings_path: Optional[Path] = None,
        mount_point: Path = MOUNT_POINT,
    ):
        self.settings = settings
        self._storage_root = storage_root
        self._settings_path = settings_path
        self._mount_point = mount_point
        # Simulation covers both "dev laptop" and "no mount binary here".
        self.simulation = simulation or mount_binary() is None
        self._sim_root = storage_root.parent / "remote-sim"

        # Session-only. Never persisted, never serialised, never logged.
        self._password: Optional[str] = None

        self._mounted = False
        self._queue: "asyncio.Queue[_Job]" = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._pending: "OrderedDict[str, _Pending]" = OrderedDict()
        self._last_sync_at: Optional[datetime] = None
        self._last_result: Optional[str] = None
        self._last_error: Optional[str] = None
        self._bulk_in_progress = False
        self._bulk_message: Optional[str] = None
        self._mount_lock = asyncio.Lock()
        self._unmount_task: Optional[asyncio.Future] = None

    # --- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())

    async def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - best effort
                log.debug("remote sync worker raised on shutdown", exc_info=True)
            self._worker = None
        # The password dies with the process, so a mount left behind could
        # never be re-established anyway -- take it down cleanly.
        await self.unmount()
        self._password = None

    # --- configuration ---------------------------------------------------
    @property
    def password_set(self) -> bool:
        return bool(self._password)

    @property
    def credentials_required(self) -> bool:
        """Switched on, but with no password in memory: the post-restart state."""
        return self.settings.enabled and not self._password

    def set_password(self, password: str) -> None:
        self._password = password or None

    def clear_password(self) -> None:
        self._password = None

    def note_active_researcher(self, researcher: str) -> None:
        """Called when an experiment starts.

        Per spec, sync runs "until synchro is off, or until the user changes":
        a different researcher means the destination folder would change under
        someone's feet, so sync switches itself off and says why.
        """
        name = (researcher or "").strip()
        if not name or not self.settings.enabled:
            return
        if self.settings.researcher and name != self.settings.researcher:
            log.warning(
                "remote sync stopped: researcher changed from %r to %r",
                self.settings.researcher,
                name,
            )
            previous = self.settings.researcher
            self.settings.enabled = False
            self._last_result = "error"
            self._last_error = (
                "Sync stopped: the researcher changed from '%s' to '%s'. "
                "Switch sync back on to sync as '%s'." % (previous, name, name)
            )
            self.persist()
            # Fire-and-forget, but keep a reference: a bare ensure_future can
            # be garbage-collected mid-flight. Never awaited here, because this
            # runs on the experiment-start path.
            try:
                self._unmount_task = asyncio.ensure_future(self.unmount())
            except RuntimeError:  # no running loop (unit tests / sync callers)
                pass

    def persist(self) -> None:
        """Write the non-secret settings. There is no password to leak here:
        RemoteSyncSettings has no such field at all."""
        if self._settings_path is None:
            return
        try:
            save_remote_sync_settings(self._settings_path, self.settings)
        except Exception:
            log.exception("failed to persist remote sync settings")

    # --- mounting --------------------------------------------------------
    @property
    def mounted(self) -> bool:
        """The cached flag, deliberately NOT a fresh stat().

        Statting a hung CIFS mount blocks -- uninterruptibly, for however long
        the kernel takes to give up. This property is read by GET
        /api/settings/remote-sync, which the settings panel polls every 5s, so
        touching the filesystem here would let a dead share freeze the whole
        (single-process) API. Every real filesystem check happens on a worker
        thread inside mount/unmount and refreshes this flag.
        """
        return self._mounted

    def remote_root(self) -> Path:
        """Where the share is (or would be) reachable in the filesystem."""
        if self.simulation:
            return self._sim_root / _slug(self.settings.server)
        return self._mount_point

    def remote_path_for(self, researcher: str) -> Path:
        return self.remote_root() / _slug(researcher or self.settings.researcher)

    async def mount(self) -> Tuple[bool, str]:
        """Mount the share (idempotent). Returns (ok, message)."""
        async with self._mount_lock:
            return await self._mount_locked()

    async def _mount_locked(self) -> Tuple[bool, str]:
        try:
            server = validate_remote_server(self.settings.server)
            username = validate_remote_username(self.settings.username)
        except ValueError as e:
            return False, str(e)
        if not self._password:
            return False, "Password required — it is not stored and must be re-entered after a restart."

        if self.simulation:
            # No CIFS server on a dev laptop: emulate the share with a local
            # directory so the whole sync path stays exercisable.
            try:
                self.remote_root().mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return False, "Simulated share unavailable: %s" % e
            self._mounted = True
            return True, "Connected (simulation — no real CIFS mount was made)."

        # Everything below touches the filesystem, so it all happens on a
        # worker thread with a timeout: a hung share must not block the loop.
        try:
            ok, message = await asyncio.wait_for(
                asyncio.to_thread(self._mount_sync, server, username, self._password),
                timeout=MOUNT_TIMEOUT_S + 10,
            )
        except asyncio.TimeoutError:
            return False, "Mount did not complete in time — is the share reachable?"
        self._mounted = ok
        return ok, message

    def _mount_sync(self, server: str, username: str, password: str) -> Tuple[bool, str]:
        """Blocking mount, run in a worker thread.

        The credentials file exists only for the duration of the mount call.
        """
        if is_mount_point(self._mount_point):
            return True, "Already mounted at %s." % self._mount_point

        try:
            self._mount_point.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            return False, (
                "Mount point %s does not exist and could not be created — "
                "re-run deploy/install.sh." % self._mount_point
            )
        except Exception as e:
            return False, "Could not prepare %s: %s" % (self._mount_point, e)

        try:
            credentials_path = _write_credentials_file(username, password)
        except Exception as e:
            return False, "Could not create the credentials file: %s" % e
        try:
            argv = build_mount_argv(server, self._mount_point, credentials_path)
        except FileNotFoundError as e:
            self._unlink_quietly(credentials_path)
            return False, str(e)

        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, never shell=True
                argv,
                capture_output=True,
                text=True,
                timeout=MOUNT_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "Mount timed out after %ds — is the share reachable?" % int(MOUNT_TIMEOUT_S)
        except Exception as e:
            return False, "Mount failed to run: %s" % e
        finally:
            # Unconditionally, immediately: the password must not survive on
            # disk past this call under any outcome.
            self._unlink_quietly(credentials_path)

        if result.returncode == 0:
            return True, "Connected — share mounted at %s." % self._mount_point
        detail = (result.stderr or result.stdout or "").strip() or (
            "mount exited with status %d" % result.returncode
        )
        if "sudo:" in detail and "password" in detail.lower():
            detail += (
                " (the sudoers entry is missing — re-run deploy/install.sh on this box)"
            )
        return False, detail

    @staticmethod
    def _unlink_quietly(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

    async def unmount(self) -> Tuple[bool, str]:
        if self.simulation:
            self._mounted = False
            return True, "Disconnected (simulation)."
        try:
            ok, message = await asyncio.wait_for(
                asyncio.to_thread(self._umount_sync), timeout=UMOUNT_TIMEOUT_S + 10
            )
        except asyncio.TimeoutError:
            return False, "umount did not complete in time."
        self._mounted = not ok
        return ok, message

    def _umount_sync(self) -> Tuple[bool, str]:
        if not is_mount_point(self._mount_point):
            return True, "Not mounted."
        try:
            argv = build_umount_argv(self._mount_point)
        except FileNotFoundError as e:
            return False, str(e)
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, never shell=True
                argv, capture_output=True, text=True, timeout=UMOUNT_TIMEOUT_S, check=False
            )
        except Exception as e:
            return False, "umount failed to run: %s" % e
        if result.returncode == 0:
            return True, "Disconnected."
        return False, (result.stderr or result.stdout or "umount failed").strip()

    async def check_connection(self) -> Tuple[bool, str]:
        """Settings -> "Check connection": mount if needed and verify writability."""
        ok, message = await self.mount()
        if not ok:
            self._last_result = "error"
            self._last_error = message
            return False, message
        # A mount that is read-only (or points somewhere unexpected) would fail
        # every later copy silently, so prove we can actually write now.
        target = self.remote_path_for(self.settings.researcher)
        try:
            await asyncio.wait_for(asyncio.to_thread(self._probe_writable, target), timeout=30.0)
        except asyncio.TimeoutError:
            self._last_result = "error"
            self._last_error = "The share did not respond within 30s."
            return False, self._last_error
        except Exception as e:
            self._last_result = "error"
            self._last_error = "Mounted, but the destination folder is not writable: %s" % e
            return False, self._last_error
        self._last_result = "ok"
        self._last_error = None
        return True, message

    @staticmethod
    def _probe_writable(target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".rapidboxes-write-test"
        probe.write_text("ok")
        probe.unlink()

    # --- the capture path (must never block or raise) --------------------
    def enqueue_image(self, image_path: Path, experiment_id: str, researcher: str) -> None:
        """Queue one just-captured image for copying. Fire-and-forget.

        Called from the experiment runner immediately after a capture. It does
        no I/O, never awaits and never raises: a slow, hung or dead share must
        not delay the deadline scheduler by even a millisecond.
        """
        try:
            if not self.settings.enabled or not self._password:
                return
            files = [image_path]
            metadata = image_path.parent / "metadata.json"
            if metadata.exists():
                files.append(metadata)
            self._queue.put_nowait(
                _Job(kind="image", researcher=researcher, experiment_id=experiment_id, files=files)
            )
        except Exception:  # pragma: no cover - defensive
            log.debug("could not queue image for remote sync", exc_info=True)

    def enqueue_bulk(self, researcher: str) -> None:
        self._bulk_in_progress = True
        self._bulk_message = "Queued…"
        self._queue.put_nowait(_Job(kind="bulk", researcher=researcher))

    # --- background worker ----------------------------------------------
    async def _run_worker(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job.kind == "image":
                    await self._handle_image_job(job)
                elif job.kind == "bulk":
                    await self._handle_bulk_job(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                log.exception("remote sync worker error")
            finally:
                self._queue.task_done()

    async def _handle_image_job(self, job: _Job) -> None:
        if not self.settings.enabled or not self._password:
            self._remember_pending(job)
            return
        # Retry whatever failed earlier before adding more, so the remote
        # catches up in capture order rather than leaving holes.
        await self._flush_failed()
        for src in job.files:
            await self._copy_or_defer(src, job.researcher, job.experiment_id)

    async def _flush_failed(self) -> None:
        if not self._pending:
            return
        for key, item in list(self._pending.items()):
            if not self.settings.enabled or not self._password:
                return
            ok = await self._copy_one(item.src, item.researcher, item.experiment_id)
            if ok:
                self._pending.pop(key, None)
            else:
                item.attempts += 1
                if item.attempts >= MAX_ATTEMPTS:
                    log.warning("giving up on remote copy of %s after %d attempts", item.src, item.attempts)
                    self._pending.pop(key, None)
                # One failure means the share is unhappy; don't hammer the rest
                # of the backlog on this pass.
                return

    async def _copy_or_defer(self, src: Path, researcher: str, experiment_id: str) -> None:
        ok = await self._copy_one(src, researcher, experiment_id)
        if not ok:
            self._defer(src, researcher, experiment_id)

    def _defer(self, src: Path, researcher: str, experiment_id: str) -> None:
        key = str(src)
        if key in self._pending:
            return
        if len(self._pending) >= MAX_PENDING:
            self._pending.popitem(last=False)
        self._pending[key] = _Pending(src=src, researcher=researcher, experiment_id=experiment_id)

    def _remember_pending(self, job: _Job) -> None:
        for src in job.files:
            self._defer(src, job.researcher, job.experiment_id)

    async def _copy_one(self, src: Path, researcher: str, experiment_id: str) -> bool:
        """Copy one file to the share. Returns success; never raises."""
        try:
            if not self.mounted:
                ok, message = await self.mount()
                if not ok:
                    self._last_result = "error"
                    self._last_error = message
                    return False
            dest_dir = self.remote_path_for(researcher) / _slug(experiment_id)
            await asyncio.wait_for(
                asyncio.to_thread(self._copy_sync, src, dest_dir),
                timeout=COPY_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._last_result = "error"
            self._last_error = "Copy of %s timed out after %ds." % (src.name, int(COPY_TIMEOUT_S))
            log.warning("remote sync: %s", self._last_error)
            return False
        except Exception as e:
            self._last_result = "error"
            self._last_error = "Copy of %s failed: %s" % (src.name, e)
            log.warning("remote sync: %s", self._last_error)
            return False
        self._last_result = "ok"
        self._last_error = None
        self._last_sync_at = datetime.now()
        return True

    @staticmethod
    def _copy_sync(src: Path, dest_dir: Path) -> None:
        """Blocking copy, via a .part file so a half-written image is never
        visible to whoever is watching the share."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        final = dest_dir / src.name
        tmp = dest_dir / (src.name + ".part")
        shutil.copyfile(src, tmp)
        os.replace(tmp, final)

    # --- bulk "sync entire folder now" -----------------------------------
    async def _handle_bulk_job(self, job: _Job) -> None:
        self._bulk_in_progress = True
        try:
            if not self.settings.enabled or not self._password:
                self._bulk_message = "Cannot sync: credentials are needed after a restart."
                return
            ok, message = await self.mount()
            if not ok:
                self._bulk_message = "Could not connect: %s" % message
                self._last_result = "error"
                self._last_error = message
                return
            experiments = self._experiments_for(job.researcher)
            if not experiments:
                self._bulk_message = "No local experiments found for '%s'." % job.researcher
                return
            copied = 0
            failed = 0
            for index, exp_dir in enumerate(experiments, start=1):
                self._bulk_message = "Copying %d/%d: %s" % (index, len(experiments), exp_dir.name)
                for src in self._files_to_sync(exp_dir):
                    if await self._copy_one(src, job.researcher, exp_dir.name):
                        copied += 1
                    else:
                        failed += 1
                        self._defer(src, job.researcher, exp_dir.name)
            self._bulk_message = "Copied %d file%s from %d experiment%s%s." % (
                copied,
                "" if copied == 1 else "s",
                len(experiments),
                "" if len(experiments) == 1 else "s",
                "" if not failed else " (%d failed — will retry)" % failed,
            )
        finally:
            self._bulk_in_progress = False

    def _experiments_for(self, researcher: str) -> List[Path]:
        """This researcher's local experiment folders.

        Folders are named `<date>_<username>_<name>`, but metadata.json carries
        the authoritative username, so prefer that and fall back to the name.
        """
        if not self._storage_root.exists():
            return []
        wanted = _slug(researcher)
        out: List[Path] = []
        for path in sorted(p for p in self._storage_root.iterdir() if p.is_dir()):
            owner = self._experiment_owner(path)
            if owner is not None and _slug(owner) == wanted:
                out.append(path)
        return out

    @staticmethod
    def _experiment_owner(path: Path) -> Optional[str]:
        meta = path / "metadata.json"
        if meta.exists():
            try:
                import json

                data = json.loads(meta.read_text())
                username = data.get("username")
                if username:
                    return str(username)
            except Exception:
                pass
        parts = path.name.split("_")
        return parts[1] if len(parts) >= 3 else None

    @staticmethod
    def _files_to_sync(exp_dir: Path) -> List[Path]:
        """Everything worth having on the share: images, metadata, config XML.
        Locally-regenerable thumbnails are skipped."""
        files = sorted(p for p in exp_dir.iterdir() if p.is_file() and not p.name.endswith(".part"))
        return files

    # --- status ----------------------------------------------------------
    def status(self) -> RemoteSyncStatus:
        researcher = self.settings.researcher
        return RemoteSyncStatus(
            enabled=self.settings.enabled,
            server=self.settings.server,
            username=self.settings.username,
            passwordSet=self.password_set,
            mounted=self.mounted,
            credentialsRequired=self.credentials_required,
            researcher=researcher,
            remotePath=str(self.remote_path_for(researcher)) if researcher else None,
            pendingCount=len(self._pending) + self._queue.qsize(),
            lastSyncAt=self._last_sync_at,
            lastResult=self._last_result,
            lastError=self._last_error,
            bulkInProgress=self._bulk_in_progress,
            bulkMessage=self._bulk_message,
            simulation=self.simulation,
        )


# ---------------------------------------------------------------------------
# Persistence of the non-secret settings
# ---------------------------------------------------------------------------


def load_remote_sync_settings(path: Path) -> RemoteSyncSettings:
    if path.exists():
        try:
            return RemoteSyncSettings.model_validate_json(path.read_text())
        except Exception:
            log.exception("invalid remote sync settings %s; using defaults", path)
    return RemoteSyncSettings()


def save_remote_sync_settings(path: Path, settings: RemoteSyncSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(settings.model_dump_json(indent=2))
    os.replace(tmp, path)
