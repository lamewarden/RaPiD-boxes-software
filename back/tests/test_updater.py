"""Exercises the OTA updater against real throwaway git repos (no mocking of
git itself -- we want to know the actual fetch/ff-only-merge behaviour is
correct, including the refusal paths)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rapidboxes.updater import apply_update, check_for_update


def _git(args, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return result.stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "Test"], path)


def _commit(path: Path, filename: str, content: str, message: str) -> str:
    (path / filename).write_text(content)
    _git(["add", filename], path)
    _git(["commit", "-m", message], path)
    return _git(["rev-parse", "HEAD"], path).strip()


@pytest.fixture
def remote_and_local(tmp_path: Path):
    remote = tmp_path / "remote"
    _init_repo(remote)
    _commit(remote, "a.txt", "1", "initial")

    local = tmp_path / "local"
    _git(["clone", str(remote), str(local)], tmp_path)
    _git(["config", "user.email", "test@example.com"], local)
    _git(["config", "user.name", "Test"], local)

    return remote, local


def test_check_reports_up_to_date_right_after_clone(remote_and_local):
    _remote, local = remote_and_local
    result = check_for_update("main", repo_root=local)
    assert result.error is None
    assert result.updateAvailable is False
    assert result.commitsBehind == 0


def test_check_reports_available_update_with_commit_log(remote_and_local):
    remote, local = remote_and_local
    _commit(remote, "b.txt", "2", "second commit")
    _commit(remote, "c.txt", "3", "third commit")

    result = check_for_update("main", repo_root=local)
    assert result.error is None
    assert result.updateAvailable is True
    assert result.commitsBehind == 2
    assert len(result.commitLog) == 2
    assert "third commit" in result.commitLog[0]


def test_apply_fast_forwards_local_to_remote(remote_and_local):
    remote, local = remote_and_local
    remote_head = _commit(remote, "b.txt", "2", "second commit")

    result = apply_update("main", repo_root=local)
    assert result.status == "updated"
    assert result.toCommit == remote_head[:8]
    assert (local / "b.txt").exists()

    # Idempotent: applying again with nothing new is a no-op, not an error.
    again = apply_update("main", repo_root=local)
    assert again.status == "up_to_date"


def test_apply_refuses_when_working_tree_is_dirty(remote_and_local):
    remote, local = remote_and_local
    _commit(remote, "b.txt", "2", "second commit")

    (local / "a.txt").write_text("locally modified, uncommitted")

    result = apply_update("main", repo_root=local)
    assert result.status == "error"
    assert "local changes" in result.message

    # Nothing was pulled -- HEAD is unchanged and the dirty file is untouched.
    head = _git(["rev-parse", "HEAD"], local).strip()
    check = check_for_update("main", repo_root=local)
    assert head != check.remoteCommit
    assert (local / "a.txt").read_text() == "locally modified, uncommitted"


def test_apply_refuses_when_history_has_diverged(remote_and_local):
    remote, local = remote_and_local
    _commit(remote, "b.txt", "2", "remote-only commit")
    _commit(local, "c.txt", "3", "local-only commit")

    result = apply_update("main", repo_root=local)
    assert result.status == "error"
    assert "diverged" in result.message.lower()

    # The local-only commit is still there -- no reset/rebase was attempted.
    assert (local / "c.txt").exists()


def test_check_reports_error_for_unreachable_remote(tmp_path: Path):
    # A repo with no "origin" remote at all -- `git fetch origin` fails cleanly.
    local = tmp_path / "solo"
    _init_repo(local)
    _commit(local, "a.txt", "1", "initial")

    result = check_for_update("main", repo_root=local)
    assert result.error is not None
    assert result.updateAvailable is False
