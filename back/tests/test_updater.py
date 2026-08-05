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
    target = path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
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
    # "b.txt" is at repo root, not under back/ or front/ -- nothing to rebuild.
    assert result.rebuildStatus == "skipped"

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


# ---------------------------------------------------------------------------
# Post-pull rebuild: pip/npm are never actually invoked in these tests --
# _rebuild_backend / _rebuild_frontend are swapped for fakes so we can assert
# on *which half* apply_update() decided to rebuild, and how a rebuild
# failure is surfaced, without needing a real venv/npm toolchain per test.
# ---------------------------------------------------------------------------


@pytest.fixture
def rebuild_spy(monkeypatch):
    calls: list[str] = []

    def fake_backend(repo):
        calls.append("backend")

    def fake_frontend(repo):
        calls.append("frontend")

    monkeypatch.setattr("rapidboxes.updater._rebuild_backend", fake_backend)
    monkeypatch.setattr("rapidboxes.updater._rebuild_frontend", fake_frontend)
    return calls


def test_apply_rebuilds_backend_only_when_only_backend_changed(remote_and_local, rebuild_spy):
    remote, local = remote_and_local
    _commit(remote, "back/rapidboxes/thing.py", "print(1)", "backend change")

    result = apply_update("main", repo_root=local)
    assert result.status == "updated"
    assert result.rebuildStatus == "ok"
    assert rebuild_spy == ["backend"]


def test_apply_rebuilds_frontend_only_when_only_frontend_changed(remote_and_local, rebuild_spy):
    remote, local = remote_and_local
    _commit(remote, "front/plant-imaging-controller-faa-main/client/x.tsx", "x", "frontend change")

    result = apply_update("main", repo_root=local)
    assert result.status == "updated"
    assert result.rebuildStatus == "ok"
    assert rebuild_spy == ["frontend"]


def test_apply_rebuilds_both_when_both_changed(remote_and_local, rebuild_spy):
    remote, local = remote_and_local
    _commit(remote, "back/rapidboxes/thing.py", "print(1)", "backend change")
    _commit(remote, "front/plant-imaging-controller-faa-main/client/x.tsx", "x", "frontend change")

    result = apply_update("main", repo_root=local)
    assert result.status == "updated"
    assert result.rebuildStatus == "ok"
    assert set(rebuild_spy) == {"backend", "frontend"}


def test_apply_skips_rebuild_when_neither_back_nor_front_changed(remote_and_local, rebuild_spy):
    remote, local = remote_and_local
    _commit(remote, "deploy/README.txt", "notes", "docs-only change")

    result = apply_update("main", repo_root=local)
    assert result.status == "updated"
    assert result.rebuildStatus == "skipped"
    assert rebuild_spy == []


def test_apply_reports_rebuild_failure_distinctly_without_reverting_the_pull(
    remote_and_local, monkeypatch
):
    from rapidboxes.updater import UpdaterError

    remote, local = remote_and_local
    remote_head = _commit(remote, "front/plant-imaging-controller-faa-main/client/x.tsx", "x", "fe change")

    def failing_frontend(repo):
        raise UpdaterError("npm run build exploded")

    monkeypatch.setattr("rapidboxes.updater._rebuild_frontend", failing_frontend)

    result = apply_update("main", repo_root=local)
    # The git pull itself still succeeded -- that's not undone by a rebuild
    # failure -- but the rebuild outcome is surfaced distinctly so callers
    # know not to treat this as a clean, restart-ready success.
    assert result.status == "updated"
    assert result.toCommit == remote_head[:8]
    assert result.rebuildStatus == "failed"
    assert "npm run build exploded" in result.rebuildMessage
    assert (local / "front/plant-imaging-controller-faa-main/client/x.tsx").exists()


# ---------------------------------------------------------------------------
# CLI: experiment-active gate + restart-on-rebuild-failure behaviour.
# apply_update() itself is faked here (already covered above / by the
# fast-forward tests) so these focus purely on main()'s control flow.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_config(tmp_path, monkeypatch):
    from rapidboxes.config import AppConfig

    config = AppConfig(
        simulation=True,
        storage_root=tmp_path / "experiments",
        settings_path=tmp_path / "settings.json",
        spa_dir=None,
    )
    monkeypatch.setattr("rapidboxes.config.get_config", lambda: config)
    return config


def test_cli_apply_refuses_when_experiment_active(monkeypatch, capsys, cli_config):
    from rapidboxes import updater

    monkeypatch.setattr(updater, "check_experiment_active_via_http", lambda port, timeout=5.0: True)
    called = {"n": 0}

    def fake_apply(branch, repo_root=None):
        called["n"] += 1
        return updater.UpdateApplyResult(status="updated", message="should not run")

    monkeypatch.setattr(updater, "apply_update", fake_apply)

    rc = updater.main(["apply"])
    assert rc == 1
    assert called["n"] == 0
    assert '"experiment_active"' in capsys.readouterr().out


def test_cli_apply_proceeds_when_experiment_not_active(monkeypatch, capsys, cli_config):
    from rapidboxes import updater

    monkeypatch.setattr(updater, "check_experiment_active_via_http", lambda port, timeout=5.0: False)

    def fake_apply(branch, repo_root=None):
        return updater.UpdateApplyResult(status="up_to_date", message="already current")

    monkeypatch.setattr(updater, "apply_update", fake_apply)

    rc = updater.main(["apply"])
    assert rc == 0
    assert '"up_to_date"' in capsys.readouterr().out


def test_cli_triggers_restart_after_successful_pull_and_rebuild(monkeypatch, cli_config):
    from rapidboxes import updater

    monkeypatch.setattr(updater, "check_experiment_active_via_http", lambda port, timeout=5.0: False)

    def fake_apply(branch, repo_root=None):
        return updater.UpdateApplyResult(
            status="updated", message="ok", fromCommit="a", toCommit="b",
            rebuildStatus="ok", rebuildMessage="rebuilt",
        )

    monkeypatch.setattr(updater, "apply_update", fake_apply)
    restarts = {"n": 0}
    monkeypatch.setattr(updater, "_trigger_restart", lambda port: restarts.__setitem__("n", restarts["n"] + 1))

    rc = updater.main(["apply", "--restart-on-success"])
    assert rc == 0
    assert restarts["n"] == 1


def test_cli_does_not_restart_when_rebuild_failed(monkeypatch, capsys, cli_config):
    from rapidboxes import updater

    monkeypatch.setattr(updater, "check_experiment_active_via_http", lambda port, timeout=5.0: False)

    def fake_apply(branch, repo_root=None):
        return updater.UpdateApplyResult(
            status="updated", message="ok", fromCommit="a", toCommit="b",
            rebuildStatus="failed", rebuildMessage="npm blew up",
        )

    monkeypatch.setattr(updater, "apply_update", fake_apply)
    restarts = {"n": 0}
    monkeypatch.setattr(updater, "_trigger_restart", lambda port: restarts.__setitem__("n", restarts["n"] + 1))

    rc = updater.main(["apply", "--restart-on-success"])
    assert rc == 2
    assert restarts["n"] == 0
    assert "NOT restarting" in capsys.readouterr().err


def test_check_experiment_active_via_http_reports_busy_states(monkeypatch):
    import json

    from rapidboxes import updater

    class FakeResponse:
        def __init__(self, body):
            self._body = json.dumps(body).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: FakeResponse({"state": "paused"})
    )
    assert updater.check_experiment_active_via_http(8000) is True

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: FakeResponse({"state": "idle"})
    )
    assert updater.check_experiment_active_via_http(8000) is False


def test_check_experiment_active_via_http_treats_unreachable_server_as_active(monkeypatch):
    from rapidboxes import updater

    def raise_connection_error(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", raise_connection_error)
    assert updater.check_experiment_active_via_http(8000) is True
