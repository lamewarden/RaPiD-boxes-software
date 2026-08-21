"""OTA self-update: git-only, fast-forward-only, no destructive resets.

Backs both the manual "Update" button (Settings -> General, via
`api/update.py`) and the unattended monthly check (`deploy/rapidboxes-update
.timer` -> `deploy/rapidboxes-update.service`, which runs this module as a
CLI: `python -m rapidboxes.updater apply --restart-on-success`).

Safety:
- Every git/pip/npm invocation is a fixed argument list passed to
  `subprocess.run` (never `shell=True` / string interpolation), so there is
  no command injection surface even though the branch name is
  config-controlled.
- Only `git fetch` and `git merge --ff-only` are used to apply an update.
  A fast-forward is a strictly additive move of the local branch pointer; if
  the working tree is dirty or history has diverged, the merge is refused by
  git itself and we surface that as an error instead of falling back to
  anything destructive like `reset --hard`.
- An update is refused outright (no fetch/merge attempted) while an
  experiment is running/paused/finishing -- see `BUSY_EXPERIMENT_STATES`,
  the in-process check in `api/update.py`, and `check_experiment_active_via_http`
  for the CLI's out-of-process equivalent. The same gate applies to
  `rollback_update()`.
- Rollback moves the working tree with `git checkout --detach <commit>`,
  never `git reset --hard` on the branch ref -- see `rollback_update` for
  why.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .config import get_config
from .models import ExperimentState, UpdateApplyResult, UpdateCheckResult, UpdateHistoryEntry, VersionStatus
from .update_history import append_entry, load_history

# Experiment states that make an update unsafe to apply -- mirrors the
# "busy" check in engine/runner.py's own start() guard.
BUSY_EXPERIMENT_STATES = frozenset(
    {ExperimentState.running.value, ExperimentState.paused.value, ExperimentState.finishing.value}
)

# Cap on how many "<hash> <subject>" lines we return from a check -- enough to
# skim, not a changelog dump.
_MAX_LOG_LINES = 10

_GIT_TIMEOUT_S = 30
# pip/npm can be slow, especially on a Pi -- generous but bounded so a hung
# network call can't wedge the monthly timer (or a request thread) forever.
_PIP_TIMEOUT_S = 600
_NPM_TIMEOUT_S = 600

_repo_root_cache: Optional[Path] = None


class UpdaterError(RuntimeError):
    """A git/pip/npm command failed, timed out, or the executable is missing."""


def _run_cmd(args: List[str], cwd: Path, timeout: int) -> str:
    """Run a fixed argument list (no shell) and return stdout, or raise."""
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise UpdaterError(f"{args[0]} not found") from e
    except subprocess.TimeoutExpired as e:
        raise UpdaterError(f"{' '.join(args)} timed out after {timeout}s") from e

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise UpdaterError(detail or f"{' '.join(args)} failed (exit {result.returncode})")
    return result.stdout


def _run_git(args: List[str], cwd: Path, timeout: int = _GIT_TIMEOUT_S) -> str:
    """Run `git <args>` as a fixed argument list (no shell) and return stdout."""
    return _run_cmd(["git", *args], cwd, timeout)


def get_repo_root() -> Path:
    """Resolve the git repo root once (this file lives at <repo>/back/rapidboxes/)."""
    global _repo_root_cache
    if _repo_root_cache is not None:
        return _repo_root_cache
    here = Path(__file__).resolve().parent
    root = _run_git(["rev-parse", "--show-toplevel"], here).strip()
    _repo_root_cache = Path(root)
    return _repo_root_cache


def _commit_datetime(commit: str, repo: Path) -> datetime:
    """Best-effort "when was this commit made", for seeding history entries.

    Used only when the history file has no entries yet (fresh install, or a
    box that predates this feature) -- we don't actually know when the
    current checkout was installed, so the commit's own commit-date is a much
    better proxy for "how long has this been running" than `now()`, which
    would make a freshly-seeded box always report ~0 uptime.
    """
    try:
        raw = _run_git(["show", "-s", "--format=%cI", commit], repo).strip()
        return datetime.fromisoformat(raw)
    except Exception:
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Post-pull rebuild: mirrors deploy/update.sh's "pip install ... && npm
# install && npm run build" exactly, so there is one source of truth for how
# this project is rebuilt after new code lands, whether that's a human
# running update.sh by hand or the OTA path pulling automatically. Only the
# half (backend/frontend) that actually changed is rebuilt.
# ---------------------------------------------------------------------------


def _changed_paths(before: str, after: str, repo: Path) -> List[str]:
    out = _run_git(["diff", "--name-only", f"{before}..{after}"], repo)
    return [line for line in out.splitlines() if line.strip()]


def _needs_backend_rebuild(paths: List[str]) -> bool:
    # deploy/install.sh and deploy/update.sh install the backend as a normal
    # (non-editable) package -- `pip install "$BACK_DIR[pi]"` -- so a running
    # process is on a *copy* of back/rapidboxes in site-packages. Any change
    # under back/ (not just pyproject.toml) needs a reinstall to take effect.
    return any(p == "back" or p.startswith("back/") for p in paths)


def _needs_frontend_rebuild(paths: List[str]) -> bool:
    return any(p == "front" or p.startswith("front/") for p in paths)


def _venv_pip() -> Path:
    """pip alongside the interpreter currently running this process.

    systemd's ExecStart (both rapidboxes.service and rapidboxes-update.service)
    points at `@VENV@/bin/python`, so `sys.executable` *is* the venv this app
    was installed into -- more robust than assuming a fixed `back/.venv` path.
    """
    return Path(sys.executable).resolve().parent / "pip"


def _find_front_dir(repo: Path) -> Optional[Path]:
    """Mirror deploy/update.sh's `find front -maxdepth 3 -name package.json`."""
    front_root = repo / "front"
    if not front_root.is_dir():
        return None
    matches = sorted(front_root.glob("package.json"))
    matches += sorted(front_root.glob("*/package.json"))
    matches += sorted(front_root.glob("*/*/package.json"))
    return matches[0].parent if matches else None


def _rebuild_backend(repo: Path) -> None:
    back_dir = repo / "back"
    pip = _venv_pip()
    # Same as deploy/update.sh: `pip install "$BACK_DIR[pi]"` (the Pi hardware
    # extras), not "[dev]" -- this is the production deploy path.
    _run_cmd([str(pip), "install", f"{back_dir}[pi]"], repo, timeout=_PIP_TIMEOUT_S)


def _rebuild_frontend(repo: Path) -> None:
    front_dir = _find_front_dir(repo)
    if front_dir is None:
        raise UpdaterError("could not locate the front-end project (no package.json under front/)")
    # Same as deploy/update.sh.
    _run_cmd(["npm", "install", "--no-audit", "--no-fund"], front_dir, timeout=_NPM_TIMEOUT_S)
    _run_cmd(["npm", "run", "build"], front_dir, timeout=_NPM_TIMEOUT_S)


def _rebuild_after_pull(before: str, after: str, repo: Path) -> Tuple[str, str]:
    """Returns (rebuildStatus, rebuildMessage) for UpdateApplyResult.

    Best-effort change detection: if `git diff --name-only` itself fails for
    some reason, fail safe by rebuilding both rather than silently skipping.
    """
    try:
        changed = _changed_paths(before, after, repo)
        needs_backend = _needs_backend_rebuild(changed)
        needs_frontend = _needs_frontend_rebuild(changed)
    except UpdaterError:
        changed = []
        needs_backend = True
        needs_frontend = True

    if not needs_backend and not needs_frontend:
        return "skipped", "No backend or frontend changes; nothing to rebuild."

    done: List[str] = []
    try:
        if needs_backend:
            _rebuild_backend(repo)
            done.append("backend deps")
        if needs_frontend:
            _rebuild_frontend(repo)
            done.append("frontend build")
        return "ok", f"Rebuilt: {', '.join(done)}."
    except UpdaterError as e:
        return "failed", f"Pulled new code but rebuild failed after {', '.join(done) or 'nothing'}: {e}"


def check_for_update(branch: str, repo_root: Optional[Path] = None) -> UpdateCheckResult:
    """`git fetch origin <branch>`, then compare local HEAD to origin/<branch>.

    Never mutates the working tree -- fetch only updates the local view of
    the remote (refs/remotes/origin/<branch>), not any checked-out files.

    `repo_root` defaults to the real repo (autodetected via `git rev-parse
    --show-toplevel`); tests pass a throwaway repo instead.
    """
    try:
        repo = repo_root or get_repo_root()
        _run_git(["fetch", "origin", branch], repo)
        local = _run_git(["rev-parse", "HEAD"], repo).strip()
        remote = _run_git(["rev-parse", f"origin/{branch}"], repo).strip()

        if local == remote:
            return UpdateCheckResult(
                branch=branch,
                updateAvailable=False,
                currentCommit=local[:8],
                remoteCommit=remote[:8],
                commitsBehind=0,
                commitLog=[],
            )

        behind_raw = _run_git(["rev-list", "--count", f"HEAD..origin/{branch}"], repo).strip()
        behind = int(behind_raw) if behind_raw.isdigit() else 0

        log_raw = _run_git(
            ["log", "--oneline", f"-n{_MAX_LOG_LINES}", f"HEAD..origin/{branch}"], repo
        )
        log_lines = [line for line in log_raw.splitlines() if line.strip()]

        return UpdateCheckResult(
            branch=branch,
            updateAvailable=behind > 0,
            currentCommit=local[:8],
            remoteCommit=remote[:8],
            commitsBehind=behind,
            commitLog=log_lines,
        )
    except UpdaterError as e:
        return UpdateCheckResult(branch=branch, updateAvailable=False, error=str(e))


def apply_update(
    branch: str,
    repo_root: Optional[Path] = None,
    history_path: Optional[Path] = None,
    trigger: str = "manual",
) -> UpdateApplyResult:
    """`git fetch origin <branch>` then `git merge --ff-only origin/<branch>`.

    Refuses (returns status="error", changes nothing) if the working tree has
    local modifications or history has diverged such that a fast-forward is
    impossible -- fixing that is a human/manual-deploy decision, never an
    automatic `reset --hard`.

    On a successful fast-forward, rebuilds whichever half (backend deps /
    frontend build) actually changed -- see `_rebuild_after_pull` -- and
    reports that separately via rebuildStatus/rebuildMessage, since a pull
    that succeeded but whose rebuild failed leaves the process on mismatched
    code and deps, a worse state than a clean refusal. Also appends a
    version-history entry (see update_history.py) recording the new commit,
    when, and `trigger` ("manual" from the Settings button, "monthly" from
    the CLI/timer) -- this is what powers the "running commit X for Y" /
    rollback UI.

    Does NOT check whether an experiment is active -- callers (the API
    endpoint / the CLI's `main()`) must gate on that themselves first, since
    only they know how to observe live state (in-process vs. over loopback
    HTTP). See `check_experiment_active_via_http` for the CLI's version.

    `repo_root` / `history_path` default to the real repo / configured
    history file; tests pass throwaway paths instead.
    """
    try:
        repo = repo_root or get_repo_root()
        hpath = history_path or get_config().update_history_path
        _run_git(["fetch", "origin", branch], repo)

        dirty = _run_git(["status", "--porcelain"], repo).strip()
        if dirty:
            return UpdateApplyResult(
                status="error",
                message=(
                    "Working tree has local changes; refusing to update. "
                    "Resolve manually (e.g. via deploy/update.sh) before retrying."
                ),
            )

        before = _run_git(["rev-parse", "HEAD"], repo).strip()

        try:
            _run_git(["merge", "--ff-only", f"origin/{branch}"], repo)
        except UpdaterError as e:
            return UpdateApplyResult(
                status="error",
                message=f"Fast-forward failed (local history has diverged): {e}",
                fromCommit=before[:8],
            )

        after = _run_git(["rev-parse", "HEAD"], repo).strip()

        if before == after:
            return UpdateApplyResult(
                status="up_to_date",
                message="Already up to date.",
                fromCommit=before[:8],
                toCommit=after[:8],
            )

        rebuild_status, rebuild_message = _rebuild_after_pull(before, after, repo)

        append_entry(
            hpath,
            UpdateHistoryEntry(commit=after[:8], appliedAt=datetime.now(timezone.utc), trigger=trigger),
        )

        return UpdateApplyResult(
            status="updated",
            message=f"Updated {before[:8]} -> {after[:8]}.",
            fromCommit=before[:8],
            toCommit=after[:8],
            rebuildStatus=rebuild_status,
            rebuildMessage=rebuild_message,
        )
    except UpdaterError as e:
        return UpdateApplyResult(status="error", message=str(e))


def get_version_status(history_path: Path, repo_root: Optional[Path] = None) -> VersionStatus:
    """What's currently running, and (if any) what it can roll back to.

    Lazily seeds `history_path` with a single "seed" entry for the current
    HEAD if it has no entries yet -- a box that's never run an OTA update (or
    is still on the very first install.sh checkout) has no prior entries, but
    "how long has the current version been running" should still have an
    answer. See `_commit_datetime` for why the seed's timestamp is the
    commit's own date rather than "now".

    Trusts the history file as the record of truth for what's "current" --
    it does not reconcile against live git HEAD beyond the initial seed, so
    an update applied outside this module (e.g. a manual `deploy/update.sh`
    pull) won't be reflected here until the next apply/rollback through it.
    """
    try:
        repo = repo_root or get_repo_root()
        history = load_history(history_path)
        if not history:
            head = _run_git(["rev-parse", "HEAD"], repo).strip()
            seeded = append_entry(
                history_path,
                UpdateHistoryEntry(commit=head[:8], appliedAt=_commit_datetime(head, repo), trigger="seed"),
            )
            history = [seeded]

        current = history[-1]
        previous = history[-2] if len(history) >= 2 else None
        return VersionStatus(current=current, previous=previous)
    except UpdaterError as e:
        return VersionStatus(error=str(e))


def rollback_update(history_path: Path, repo_root: Optional[Path] = None) -> UpdateApplyResult:
    """Move the working tree back to the previous recorded commit.

    Judgment call: this uses `git checkout --detach <commit>`, not
    `git reset --hard <commit>` on the tracked branch. Weighed both:

    - `reset --hard` would rewrite the branch ref (e.g. refs/heads/main)
      itself backward. That's a bigger hammer than needed here, and it has a
      nasty side effect: the *next* `apply_update()` (or the monthly timer)
      fetches, compares local HEAD to origin/<branch>, and sees it's simply
      "behind" by the exact commits we just rolled back through --
      `merge --ff-only` would then happily fast-forward right back onto the
      commit we were trying to get away from, silently undoing the rollback
      on the very next check.
    - `checkout --detach` instead moves only the *working tree* + HEAD to
      that commit, leaving HEAD detached (not pointing at refs/heads/<branch>
      at all). refs/heads/<branch> is untouched. A subsequent apply_update()
      still fetches fine, but `git merge --ff-only origin/<branch>` from a
      detached HEAD that isn't a fast-forward ancestor of origin/<branch>
      fails correctly instead of silently reapplying the rolled-back commit
      -- i.e. it fails the same safe way a diverged-history apply already
      does. (Still worth flagging to the user: see the "risk" note surfaced
      by the UI -- a human explicitly re-running "Update Now" after a
      rollback, or the next monthly run once history has "moved on" past the
      rolled-back commit, can still land back on it. This module doesn't try
      to prevent that; it only avoids doing it *silently* on the very next
      routine check.)

    Refuses (no-op) if the working tree is dirty, or if there's no previous
    entry recorded to roll back to. Does NOT check whether an experiment is
    active -- same split of responsibility as apply_update(): callers gate
    on that first.

    Unlike apply_update(), this needs no network access -- the rollback
    target is a commit this repo already has (it was HEAD before), so no
    `git fetch` is required.
    """
    try:
        repo = repo_root or get_repo_root()
        history = load_history(history_path)

        if not history:
            # Mirror get_version_status()'s lazy seed so a box that calls
            # rollback before ever calling check/apply/version still gets a
            # sensible history file going forward -- but there is still
            # nothing *previous* to offer.
            head = _run_git(["rev-parse", "HEAD"], repo).strip()
            append_entry(
                history_path,
                UpdateHistoryEntry(commit=head[:8], appliedAt=_commit_datetime(head, repo), trigger="seed"),
            )
            return UpdateApplyResult(
                status="nothing_to_roll_back_to", message="No previous version recorded yet."
            )

        if len(history) < 2:
            return UpdateApplyResult(
                status="nothing_to_roll_back_to", message="No previous version recorded yet."
            )

        target_entry = history[-2]

        dirty = _run_git(["status", "--porcelain"], repo).strip()
        if dirty:
            return UpdateApplyResult(
                status="error",
                message=(
                    "Working tree has local changes; refusing to roll back. "
                    "Resolve manually before retrying."
                ),
            )

        before = _run_git(["rev-parse", "HEAD"], repo).strip()

        try:
            target_full = _run_git(["rev-parse", target_entry.commit], repo).strip()
        except UpdaterError:
            return UpdateApplyResult(
                status="error",
                message=(
                    f"Recorded previous commit {target_entry.commit} no longer exists in "
                    "this repo (it may have been garbage-collected)."
                ),
            )

        try:
            _run_git(["checkout", "--detach", target_full], repo)
        except UpdaterError as e:
            return UpdateApplyResult(status="error", message=f"Rollback checkout failed: {e}")

        after = _run_git(["rev-parse", "HEAD"], repo).strip()

        rebuild_status, rebuild_message = _rebuild_after_pull(before, after, repo)

        append_entry(
            history_path,
            UpdateHistoryEntry(commit=after[:8], appliedAt=datetime.now(timezone.utc), trigger="rollback"),
        )

        return UpdateApplyResult(
            status="rolled_back",
            message=f"Rolled back {before[:8]} -> {after[:8]}.",
            fromCommit=before[:8],
            toCommit=after[:8],
            rebuildStatus=rebuild_status,
            rebuildMessage=rebuild_message,
        )
    except UpdaterError as e:
        return UpdateApplyResult(status="error", message=str(e))


# ---------------------------------------------------------------------------
# CLI entry point for the unattended monthly update
# (deploy/rapidboxes-update.service runs:
#   python -m rapidboxes.updater apply --restart-on-success).
# No user is present to click "Restart", so on a successful pull we ask the
# already-running service to restart itself over loopback HTTP -- the exact
# same POST /api/system/restart-service path the manual "Update" button (and
# the kiosk's own restart-after-settings-change flow) uses. That endpoint just
# SIGKILLs its own pid and relies on systemd's Restart=always, so this script
# needs no sudo/systemctl access of its own.
# ---------------------------------------------------------------------------


def _trigger_restart(port: int) -> None:
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/system/restart-service", method="POST"
    )
    with urllib.request.urlopen(req, timeout=5):
        pass


def check_experiment_active_via_http(port: int, timeout: float = 5.0) -> bool:
    """Best-effort loopback check of whether an experiment is running.

    The CLI (invoked by the systemd timer) is a *separate process* from the
    running rapidboxes.service and has no direct access to the live
    ExperimentRunner, so it asks the already-running server the same way the
    kiosk UI does: GET /api/experiments/current (see api/experiments.py),
    which returns the same ExperimentStatus.state the in-process API check
    uses (api/update.py).

    Any failure to reach it (server down, timeout, bad response) is treated
    as "active" -- refuse to update -- since skipping one monthly update is
    far cheaper than guessing wrong and interrupting a multi-day protocol.
    """
    import json
    import urllib.request

    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/experiments/current")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        return body.get("state") in BUSY_EXPERIMENT_STATES
    except Exception:
        return True


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="RaPiD-boxes OTA updater (git fetch + fast-forward-only pull)."
    )
    parser.add_argument("action", choices=["check", "apply"])
    parser.add_argument(
        "--restart-on-success",
        action="store_true",
        help="After a successful 'apply' that pulled new commits (and whose "
        "rebuild, if any, succeeded), POST /api/system/restart-service to "
        "the locally running instance.",
    )
    args = parser.parse_args(argv)

    config = get_config()

    if args.action == "check":
        result = check_for_update(config.update_branch)
        print(result.model_dump_json())
        return 1 if result.error else 0

    # apply: refuse outright if an experiment looks active -- same "clean
    # refusal, no partial state" pattern as the dirty-tree / diverged-history
    # cases inside apply_update() itself. Persistent=true on the timer (plus
    # next month's run) covers the case where this is skipped.
    if check_experiment_active_via_http(config.port):
        result = UpdateApplyResult(
            status="experiment_active",
            message="An experiment appears to be active (or its status could not "
            "be verified); refusing to update.",
        )
        print(result.model_dump_json())
        print(result.message, file=sys.stderr)
        return 1

    result = apply_update(
        config.update_branch, history_path=config.update_history_path, trigger="monthly"
    )
    print(result.model_dump_json())

    if result.status == "updated" and args.restart_on_success:
        if result.rebuildStatus == "failed":
            print(
                f"warning: pull succeeded but rebuild failed ({result.rebuildMessage}); "
                "NOT restarting -- code and installed deps are now mismatched, needs "
                "manual attention (e.g. deploy/update.sh).",
                file=sys.stderr,
            )
            return 2
        try:
            _trigger_restart(config.port)
        except Exception as e:  # noqa: BLE001 - report and exit non-zero, don't crash the timer
            print(f"warning: pull succeeded but restart trigger failed: {e}", file=sys.stderr)
            return 2

    return 1 if result.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
