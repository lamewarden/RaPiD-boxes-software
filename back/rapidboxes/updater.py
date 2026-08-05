"""OTA self-update: git-only, fast-forward-only, no destructive resets.

Backs both the manual "Update" button (Settings -> General, via
`api/update.py`) and the unattended monthly check (`deploy/rapidboxes-update
.timer` -> `deploy/rapidboxes-update.service`, which runs this module as a
CLI: `python -m rapidboxes.updater apply --restart-on-success`).

Safety:
- Every git invocation is a fixed argument list passed to `subprocess.run`
  (never `shell=True` / string interpolation), so there is no command
  injection surface even though the branch name is config-controlled.
- Only `git fetch` and `git merge --ff-only` are used to apply an update.
  A fast-forward is a strictly additive move of the local branch pointer; if
  the working tree is dirty or history has diverged, the merge is refused by
  git itself and we surface that as an error instead of falling back to
  anything destructive like `reset --hard`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

from .models import UpdateApplyResult, UpdateCheckResult

# Cap on how many "<hash> <subject>" lines we return from a check -- enough to
# skim, not a changelog dump.
_MAX_LOG_LINES = 10

_GIT_TIMEOUT_S = 30

_repo_root_cache: Optional[Path] = None


class UpdaterError(RuntimeError):
    """A git command failed, timed out, or git itself is unavailable."""


def _run_git(args: List[str], cwd: Path, timeout: int = _GIT_TIMEOUT_S) -> str:
    """Run `git <args>` as a fixed argument list (no shell) and return stdout."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise UpdaterError("git executable not found") from e
    except subprocess.TimeoutExpired as e:
        raise UpdaterError(f"git {' '.join(args)} timed out after {timeout}s") from e

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise UpdaterError(detail or f"git {' '.join(args)} failed (exit {result.returncode})")
    return result.stdout


def get_repo_root() -> Path:
    """Resolve the git repo root once (this file lives at <repo>/back/rapidboxes/)."""
    global _repo_root_cache
    if _repo_root_cache is not None:
        return _repo_root_cache
    here = Path(__file__).resolve().parent
    root = _run_git(["rev-parse", "--show-toplevel"], here).strip()
    _repo_root_cache = Path(root)
    return _repo_root_cache


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


def apply_update(branch: str, repo_root: Optional[Path] = None) -> UpdateApplyResult:
    """`git fetch origin <branch>` then `git merge --ff-only origin/<branch>`.

    Refuses (returns status="error", changes nothing) if the working tree has
    local modifications or history has diverged such that a fast-forward is
    impossible -- fixing that is a human/manual-deploy decision, never an
    automatic `reset --hard`.

    `repo_root` defaults to the real repo (autodetected via `git rev-parse
    --show-toplevel`); tests pass a throwaway repo instead.
    """
    try:
        repo = repo_root or get_repo_root()
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

        return UpdateApplyResult(
            status="updated",
            message=f"Updated {before[:8]} -> {after[:8]}.",
            fromCommit=before[:8],
            toCommit=after[:8],
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


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    from .config import get_config

    parser = argparse.ArgumentParser(
        description="RaPiD-boxes OTA updater (git fetch + fast-forward-only pull)."
    )
    parser.add_argument("action", choices=["check", "apply"])
    parser.add_argument(
        "--restart-on-success",
        action="store_true",
        help="After a successful 'apply' that pulled new commits, POST "
        "/api/system/restart-service to the locally running instance.",
    )
    args = parser.parse_args(argv)

    config = get_config()

    if args.action == "check":
        result = check_for_update(config.update_branch)
        print(result.model_dump_json())
        return 1 if result.error else 0

    result = apply_update(config.update_branch)
    print(result.model_dump_json())

    if result.status == "updated" and args.restart_on_success:
        try:
            _trigger_restart(config.port)
        except Exception as e:  # noqa: BLE001 - report and exit non-zero, don't crash the timer
            print(f"warning: pull succeeded but restart trigger failed: {e}", file=sys.stderr)
            return 2

    return 1 if result.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
