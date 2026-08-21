"""Remote CIFS sync: input validation, password secrecy, and failure isolation.

Nothing here mounts anything for real -- every subprocess call is mocked. The
security properties under test are the ones the feature lives or dies by:

  * a malicious server string can never reach the sudo mount command
  * the password never appears in an API response, on disk, or in an argv
  * the credentials file is 0600 and is gone the moment mount returns
  * a broken share never breaks a running experiment
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import List, Optional

import pytest
from httpx import ASGITransport, AsyncClient

from rapidboxes import remote_sync as rs
from rapidboxes.config import AppConfig
from rapidboxes.main import create_app
from rapidboxes.models import (
    RemoteSyncSettings,
    RemoteSyncStatus,
    RemoteSyncUpdate,
    TropismConfig,
    validate_remote_server,
    validate_remote_username,
)

# A value distinctive enough that finding it anywhere is unambiguous.
SECRET = "correct-horse-battery-staple-9271"


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        simulation=True,
        storage_root=tmp_path / "experiments",
        settings_path=tmp_path / "settings.json",
        remote_sync_path=tmp_path / "remote_sync.json",
        spa_dir=None,
    )


@pytest.fixture
async def client(app_config: AppConfig):
    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac._app = app  # type: ignore[attr-defined]  - tests reach for app.state.app
            yield ac


def make_service(tmp_path: Path, **kwargs) -> rs.RemoteSyncService:
    settings = kwargs.pop(
        "settings",
        RemoteSyncSettings(
            enabled=True, server="//host.example.org/share/sub", username="LHR", researcher="alice"
        ),
    )
    service = rs.RemoteSyncService(
        settings,
        storage_root=kwargs.pop("storage_root", tmp_path / "experiments"),
        simulation=kwargs.pop("simulation", True),
        settings_path=kwargs.pop("settings_path", tmp_path / "remote_sync.json"),
        mount_point=kwargs.pop("mount_point", tmp_path / "mnt"),
    )
    service.set_password(kwargs.pop("password", SECRET))
    return service


# ---------------------------------------------------------------------------
# 1. Server/username validation -- the string that reaches a sudo command
# ---------------------------------------------------------------------------

MALICIOUS_SERVERS = [
    "-o",                                   # bare option
    "-oremount,suid",                       # smuggled option, no space needed
    "--bind",
    "//host/share,suid",                    # comma splits the option list
    "//host/share -o suid",                 # extra argv words
    "//host/share\t-o\tsuid",
    "//host/share\n//other/share",          # newline injection
    "//host/share;rm -rf /",                # shell metacharacters (defence in depth)
    "//host/share`id`",
    "//host/share$(id)",
    "//host/share|nc evil 1234",
    "//host/share&&reboot",
    "//host/../../etc/shadow",              # traversal
    "//host/share/../..",
    "/host/share",                          # single leading slash
    "///host/share",
    "//host",                               # no share component
    "//",
    "",
    "   ",
    "\\\\host\\share",                      # UNC backslash form is not accepted
    "//host/share/'",
    '//host/share/"',
    "//ho st/share",                        # embedded space
    "//host:2049/share",                    # colon is not in the allowlist
    "//host/share#frag",
    "//" + "a" * 300 + "/share",            # over the length cap
]

LEGITIMATE_SERVERS = [
    "//ds.asuch.cas.cz/ueb/lhr",            # the pre-filled default
    "//nas1/data",
    "//192.168.1.20/share/Pictures/Raps_pi",
    "//file-server_2/my.share/sub_folder-3",
]


@pytest.mark.parametrize("value", MALICIOUS_SERVERS)
def test_validate_remote_server_rejects_malicious_values(value: str):
    with pytest.raises(ValueError):
        validate_remote_server(value)


@pytest.mark.parametrize("value", LEGITIMATE_SERVERS)
def test_validate_remote_server_accepts_legitimate_values(value: str):
    assert validate_remote_server(value) == value


@pytest.mark.parametrize(
    "value",
    ["a b", "user\nname", "user,name", "-user", "user;id", "u" * 65, "", "user`id`"],
)
def test_validate_remote_username_rejects_malicious_values(value: str):
    with pytest.raises(ValueError):
        validate_remote_username(value)


@pytest.mark.parametrize("value", ["LHR", "domain\\user", "first.last", "user_1@example.org"])
def test_validate_remote_username_accepts_legitimate_values(value: str):
    assert validate_remote_username(value) == value


@pytest.mark.asyncio
@pytest.mark.parametrize("value", MALICIOUS_SERVERS[:12])
async def test_api_rejects_malicious_server_strings(client: AsyncClient, value: str):
    res = await client.put("/api/settings/remote-sync", json={"server": value})
    assert res.status_code == 400, f"{value!r} was accepted"
    # And the bad value was not retained.
    current = (await client.get("/api/settings/remote-sync")).json()
    assert current["server"] != value


@pytest.mark.asyncio
async def test_api_rejects_malicious_server_even_when_sync_is_being_enabled(client: AsyncClient):
    res = await client.put(
        "/api/settings/remote-sync",
        json={
            "server": "//host/share -o suid",
            "username": "LHR",
            "password": SECRET,
            "researcher": "alice",
            "enabled": True,
        },
    )
    assert res.status_code == 400
    status = (await client.get("/api/settings/remote-sync")).json()
    assert status["enabled"] is False


def test_settings_model_rejects_malicious_server_on_load():
    """A hand-edited remote_sync.json cannot smuggle a bad server in either."""
    with pytest.raises(Exception):
        RemoteSyncSettings(server="//host/share -o suid")


# ---------------------------------------------------------------------------
# 2. The password must never be readable back out
# ---------------------------------------------------------------------------

# Every GET the box exposes that could plausibly carry configuration.
GET_ENDPOINTS = [
    "/api/health",
    "/api/system",
    "/api/settings",
    "/api/settings/remote-sync",
    "/api/experiments/current",
    "/api/experiments/history",
    "/api/images",
    "/api/system/update/version",
    "/openapi.json",
]


@pytest.mark.asyncio
async def test_password_never_appears_in_any_get_response(client: AsyncClient):
    """This box has no auth and binds 0.0.0.0 -- anyone on the LAN can curl it."""
    res = await client.put(
        "/api/settings/remote-sync",
        json={
            "server": "//ds.asuch.cas.cz/ueb/lhr",
            "username": "LHR",
            "password": SECRET,
            "researcher": "alice",
            "enabled": True,
        },
    )
    assert res.status_code == 200
    # Not even the write that accepted it echoes it back.
    assert SECRET not in res.text
    assert res.json()["passwordSet"] is True
    assert "password" not in res.json()

    for endpoint in GET_ENDPOINTS:
        response = await client.get(endpoint)
        assert SECRET not in response.text, f"password leaked from GET {endpoint}"
        assert "correct-horse" not in response.text

    # And the same after a check-connection round trip, which handles the
    # password most directly.
    res = await client.post("/api/settings/remote-sync/check")
    assert SECRET not in res.text
    for endpoint in GET_ENDPOINTS:
        response = await client.get(endpoint)
        assert SECRET not in response.text, f"password leaked from GET {endpoint} after check"


def test_status_model_has_no_password_field():
    """Structural guarantee: there is no field for a password to be put in."""
    assert "password" not in RemoteSyncStatus.model_fields
    assert "password" not in RemoteSyncSettings.model_fields
    # ...and exactly one model accepts it, on the way in only.
    assert "password" in RemoteSyncUpdate.model_fields


@pytest.mark.asyncio
async def test_password_is_never_written_to_disk(client: AsyncClient, app_config: AppConfig, tmp_path: Path):
    await client.put(
        "/api/settings/remote-sync",
        json={
            "server": "//ds.asuch.cas.cz/ueb/lhr",
            "username": "LHR",
            "password": SECRET,
            "researcher": "alice",
            "enabled": True,
        },
    )
    await client.post("/api/settings/remote-sync/check")

    assert app_config.remote_sync_path.exists(), "the non-secret half should persist"
    persisted = json.loads(app_config.remote_sync_path.read_text())
    assert persisted["server"] == "//ds.asuch.cas.cz/ueb/lhr"
    assert persisted["username"] == "LHR"
    assert "password" not in persisted

    # Nothing anywhere under the data root may contain it.
    for path in tmp_path.rglob("*"):
        if path.is_file():
            try:
                content = path.read_text(errors="ignore")
            except OSError:
                continue
            assert SECRET not in content, f"password written to {path}"


@pytest.mark.asyncio
async def test_password_is_lost_on_restart_and_surfaces_as_credentials_required(
    app_config: AppConfig,
):
    """The session-only design's headline consequence, asserted end to end."""
    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.put(
                "/api/settings/remote-sync",
                json={
                    "server": "//host.example.org/share",
                    "username": "LHR",
                    "password": SECRET,
                    "researcher": "alice",
                    "enabled": True,
                },
            )
            status = (await ac.get("/api/settings/remote-sync")).json()
            assert status["enabled"] is True
            assert status["passwordSet"] is True
            assert status["credentialsRequired"] is False

    # A fresh process over the same files -- exactly what a reboot or the
    # monthly OTA restart produces.
    app2 = create_app(app_config)
    async with app2.router.lifespan_context(app2):
        transport = ASGITransport(app=app2)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            status = (await ac.get("/api/settings/remote-sync")).json()
            assert status["enabled"] is True, "the switch setting itself survives"
            assert status["passwordSet"] is False, "the password does not"
            assert status["credentialsRequired"] is True, (
                "sync must report itself inactive rather than appear on-but-silent"
            )
            assert status["mounted"] is False

            # And it refuses to pretend it can do anything in that state.
            res = await ac.post("/api/settings/remote-sync/sync-all", json={"researcher": "alice"})
            assert res.status_code == 400
            assert "restart" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. The mount invocation: no password in argv, credentials file cleaned up
# ---------------------------------------------------------------------------


class FakeRun:
    """Stands in for subprocess.run, recording what the mount was asked to do."""

    def __init__(self, returncode: int = 0, raises: Optional[Exception] = None):
        self.returncode = returncode
        self.raises = raises
        self.argv: List[str] = []
        self.kwargs: dict = {}
        self.credentials_path: Optional[str] = None
        self.credentials_content: Optional[str] = None
        self.credentials_mode: Optional[int] = None
        self.credentials_existed_during_call = False

    def __call__(self, argv, **kwargs):
        self.argv = list(argv)
        self.kwargs = kwargs
        for arg in self.argv:
            if arg.startswith("credentials=") or ",credentials=" in arg:
                for option in arg.split(","):
                    if option.startswith("credentials="):
                        path = option.split("=", 1)[1]
                        self.credentials_path = path
                        self.credentials_existed_during_call = os.path.exists(path)
                        if self.credentials_existed_during_call:
                            self.credentials_content = Path(path).read_text()
                            self.credentials_mode = os.stat(path).st_mode & 0o777
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(self.argv, self.returncode, stdout="", stderr="mount error text")


@pytest.mark.asyncio
async def test_mount_passes_password_via_0600_credentials_file_not_argv(
    tmp_path: Path, monkeypatch
):
    fake = FakeRun(returncode=0)
    monkeypatch.setattr(rs.subprocess, "run", fake)

    service = make_service(tmp_path, simulation=False)
    ok, message = await service.mount()
    assert ok, message

    # `ps aux` is world-readable: the password must not be anywhere in argv.
    joined = " ".join(fake.argv)
    assert SECRET not in joined
    assert "pass=" not in joined
    assert "password=" not in joined

    # It went through a credentials file instead...
    assert fake.credentials_existed_during_call
    assert fake.credentials_content == "username=LHR\npassword=%s\n" % SECRET
    assert fake.credentials_mode == 0o600, "credentials file must not be readable by others"

    # ...which is gone the moment mount returned.
    assert fake.credentials_path is not None
    assert not os.path.exists(fake.credentials_path), "credentials file outlived the mount call"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fake",
    [
        FakeRun(returncode=1),
        FakeRun(raises=subprocess.TimeoutExpired(cmd="mount", timeout=25)),
        FakeRun(raises=OSError("boom")),
    ],
    ids=["nonzero-exit", "timeout", "oserror"],
)
async def test_credentials_file_is_removed_even_when_mount_fails(
    tmp_path: Path, monkeypatch, fake: FakeRun
):
    monkeypatch.setattr(rs.subprocess, "run", fake)

    service = make_service(tmp_path, simulation=False)
    ok, message = await service.mount()
    assert ok is False
    assert message  # the real error text is surfaced, not a generic failure

    assert fake.credentials_path is not None
    assert not os.path.exists(fake.credentials_path), "credentials file leaked on the failure path"


@pytest.mark.asyncio
async def test_mount_argv_shape_is_safe(tmp_path: Path, monkeypatch):
    fake = FakeRun(returncode=0)
    monkeypatch.setattr(rs.subprocess, "run", fake)

    service = make_service(tmp_path, simulation=False)
    await service.mount()

    # A fixed argument list, never a shell string.
    assert isinstance(fake.argv, list)
    assert fake.kwargs.get("shell") in (None, False)
    assert fake.kwargs.get("timeout") == rs.MOUNT_TIMEOUT_S

    assert fake.argv[1] == "-n", "sudo must never prompt"
    assert fake.argv[3:5] == ["-t", "cifs"]
    assert fake.argv[5] == "//host.example.org/share/sub"
    assert fake.argv[6] == str(tmp_path / "mnt")
    assert fake.argv[7] == "-o"

    # The hardening options come LAST, because mount options are last-one-wins.
    options = fake.argv[8]
    assert options.startswith("credentials=")
    assert options.endswith(rs.fixed_mount_options())
    for hardening in ("nosuid", "nodev", "noexec", "uid=%d" % os.getuid()):
        assert hardening in options


@pytest.mark.asyncio
async def test_mount_refuses_without_a_password(tmp_path: Path, monkeypatch):
    fake = FakeRun(returncode=0)
    monkeypatch.setattr(rs.subprocess, "run", fake)

    service = make_service(tmp_path, simulation=False)
    service.clear_password()
    ok, message = await service.mount()
    assert ok is False
    assert "re-entered after a restart" in message
    assert fake.argv == [], "mount must not be invoked at all without credentials"


def test_no_shell_true_anywhere_in_the_backend():
    """A blunt guard: the server string flows into a *sudo* command, and with
    `shell=True` that would be local privilege escalation to root.

    Parsed rather than grepped, so the prose in the module docstrings that says
    "never shell=True" doesn't trip it -- only a real keyword argument does.
    """
    package_root = Path(rs.__file__).parent
    offenders = []
    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "shell" and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                ):
                    offenders.append("%s:%d" % (path.name, node.lineno))
    assert offenders == [], f"shell= passed to a subprocess call at {offenders}"


# ---------------------------------------------------------------------------
# 4. A broken share must never break an experiment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_failure_does_not_break_a_running_experiment(
    client: AsyncClient, app_config: AppConfig, monkeypatch
):
    """Data safety of the local run is paramount; the remote copy is best-effort."""

    def exploding_copy(src: Path, dest_dir: Path) -> None:
        raise OSError("Host is down")

    monkeypatch.setattr(rs.RemoteSyncService, "_copy_sync", staticmethod(exploding_copy))

    res = await client.put(
        "/api/settings/remote-sync",
        json={
            "server": "//host.example.org/share",
            "username": "LHR",
            "password": SECRET,
            "researcher": "alice",
            "enabled": True,
        },
    )
    assert res.status_code == 200

    config = TropismConfig(
        experimentName="sync-failure",
        username="alice",
        darkPhaseEnabled=True,
        darkPhaseHours=0.05,
        lateralIlluminationHours=0,
        intervalMinutes=1,
    )
    res = await client.post("/api/experiments", json=config.model_dump())
    assert res.status_code == 200
    assert res.json()["status"] == "started"
    experiment_id = res.json()["experimentId"]

    # Wait for the first capture (taken immediately at phase start).
    for _ in range(200):
        status = (await client.get("/api/experiments/current")).json()
        if status["imagesCaptured"] >= 1:
            break
        await asyncio.sleep(0.05)

    status = (await client.get("/api/experiments/current")).json()
    assert status["imagesCaptured"] >= 1
    assert status["state"] == "running", "a dead share must not stop the run"

    res = await client.post("/api/experiments/current/stop")
    final = res.json()
    assert final["state"] == "done"
    assert final["message"] == "stopped by user"

    # The image is safely on local disk regardless of the remote failure.
    local = app_config.storage_root / experiment_id
    assert list(local.glob("*.jpg")), "local images must survive a sync failure"

    # Let the background worker finish, then confirm the failure was recorded
    # as pending rather than raised.
    sync = client._app.state.app.sync  # type: ignore[attr-defined]
    await asyncio.wait_for(sync._queue.join(), timeout=10)
    remote = (await client.get("/api/settings/remote-sync")).json()
    assert remote["pendingCount"] >= 1
    assert remote["lastResult"] == "error"
    assert "Host is down" in (remote["lastError"] or "")


@pytest.mark.asyncio
async def test_enqueue_image_never_raises_on_the_capture_path(tmp_path: Path):
    """The one method the runner calls: synchronous, non-blocking, total."""
    service = make_service(tmp_path)
    # Nonsense inputs, a full queue, a missing file -- none may propagate.
    service.enqueue_image(tmp_path / "does-not-exist.jpg", "exp", "alice")
    service.enqueue_image(Path("/definitely/not/here.jpg"), "", "")
    service._queue = None  # type: ignore[assignment]  - simulate a broken queue
    service.enqueue_image(tmp_path / "x.jpg", "exp", "alice")  # must not raise


@pytest.mark.asyncio
async def test_disabled_sync_queues_nothing(tmp_path: Path):
    service = make_service(tmp_path, settings=RemoteSyncSettings(enabled=False, researcher="alice"))
    service.enqueue_image(tmp_path / "a.jpg", "exp", "alice")
    assert service._queue.qsize() == 0


# ---------------------------------------------------------------------------
# 5. Researcher changes, bulk sync, and the simulated share
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_stops_when_the_researcher_changes(tmp_path: Path):
    service = make_service(tmp_path)
    assert service.settings.enabled is True

    service.note_active_researcher("alice")  # same person: no change
    assert service.settings.enabled is True

    service.note_active_researcher("bob")
    assert service.settings.enabled is False
    assert "researcher changed" in (service.status().lastError or "")
    assert "alice" in service.status().lastError and "bob" in service.status().lastError
    await asyncio.sleep(0)  # let the best-effort unmount task run


@pytest.mark.asyncio
async def test_bulk_sync_copies_only_this_researchers_experiments(client: AsyncClient, app_config: AppConfig):
    root = app_config.storage_root
    root.mkdir(parents=True, exist_ok=True)
    for name, owner in [
        ("2026-01-01_alice_run-a", "alice"),
        ("2026-01-02_alice_run-b", "alice"),
        ("2026-01-03_bob_run-c", "bob"),
    ]:
        exp = root / name
        (exp / "thumbs").mkdir(parents=True)
        (exp / "dark_00000.jpg").write_bytes(b"jpeg-bytes")
        (exp / "thumbs" / "dark_00000.jpg").write_bytes(b"thumb")
        (exp / "metadata.json").write_text(json.dumps({"username": owner}))

    await client.put(
        "/api/settings/remote-sync",
        json={
            "server": "//host.example.org/share",
            "username": "LHR",
            "password": SECRET,
            "researcher": "alice",
            "enabled": True,
        },
    )
    res = await client.post("/api/settings/remote-sync/sync-all", json={"researcher": "alice"})
    assert res.status_code == 200

    sync = client._app.state.app.sync  # type: ignore[attr-defined]
    await asyncio.wait_for(sync._queue.join(), timeout=20)

    # In simulation the "share" is a local directory, so the layout is checkable:
    # <share>/<researcher>/<experiment>/...
    destination = sync.remote_path_for("alice")
    assert (destination / "2026-01-01_alice_run-a" / "dark_00000.jpg").exists()
    assert (destination / "2026-01-02_alice_run-b" / "metadata.json").exists()
    assert not (destination / "2026-01-03_bob_run-c").exists(), "another user's data must not be copied"
    # Locally-regenerable thumbnails are not shipped over the network.
    assert not (destination / "2026-01-01_alice_run-a" / "thumbs").exists()

    status = (await client.get("/api/settings/remote-sync")).json()
    assert status["lastResult"] == "ok"
    assert "Copied" in (status["bulkMessage"] or "")


@pytest.mark.asyncio
async def test_simulation_mode_degrades_gracefully_without_a_cifs_server(client: AsyncClient):
    """The whole stack must stay usable on a dev laptop with no share."""
    status = (await client.get("/api/settings/remote-sync")).json()
    assert status["simulation"] is True
    assert status["server"] == "//ds.asuch.cas.cz/ueb/lhr", "the default is pre-filled"
    assert status["enabled"] is False

    await client.put(
        "/api/settings/remote-sync",
        json={"username": "LHR", "password": SECRET, "researcher": "alice", "enabled": True},
    )
    res = await client.post("/api/settings/remote-sync/check")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "simulation" in body["message"].lower()
    assert body["status"]["mounted"] is True


@pytest.mark.asyncio
async def test_check_connection_requires_both_credentials(client: AsyncClient):
    res = await client.post("/api/settings/remote-sync/check")
    assert res.status_code == 400

    await client.put("/api/settings/remote-sync", json={"username": "LHR"})
    res = await client.post("/api/settings/remote-sync/check")
    assert res.status_code == 400, "username alone is not enough"


@pytest.mark.asyncio
async def test_enabling_sync_requires_credentials_and_a_researcher(client: AsyncClient):
    res = await client.put("/api/settings/remote-sync", json={"enabled": True})
    assert res.status_code == 400

    res = await client.put(
        "/api/settings/remote-sync",
        json={"username": "LHR", "password": SECRET, "enabled": True},
    )
    assert res.status_code == 400, "a destination researcher folder is required"


@pytest.mark.asyncio
async def test_turning_sync_off_drops_the_session_password(client: AsyncClient):
    await client.put(
        "/api/settings/remote-sync",
        json={"username": "LHR", "password": SECRET, "researcher": "alice", "enabled": True},
    )
    assert (await client.get("/api/settings/remote-sync")).json()["passwordSet"] is True

    res = await client.put("/api/settings/remote-sync", json={"enabled": False})
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is False
    assert body["passwordSet"] is False
    assert body["mounted"] is False
