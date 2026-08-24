"""FastAPI integration tests with simulated hardware."""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from rapidboxes.config import AppConfig
from rapidboxes.main import create_app
from rapidboxes.models import (
    ExperimentState,
    TropismConfig,
    UpdateApplyResult,
    UpdateCheckResult,
    UpdateHistoryEntry,
    VersionStatus,
)


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        simulation=True,
        storage_root=tmp_path / "experiments",
        settings_path=tmp_path / "settings.json",
        # Keep remote-sync state in the tmpdir too, so tests never read or
        # write the developer's real ~/rapidboxes/remote_sync.json.
        remote_sync_path=tmp_path / "remote_sync.json",
        user_defaults_path=tmp_path / "user_defaults.json",
        spa_dir=None,
    )


@pytest.fixture
async def client(app_config: AppConfig):
    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    res = await client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_system_info(client: AsyncClient):
    res = await client.get("/api/system")
    assert res.status_code == 200
    body = res.json()
    assert body["simulation"] is True
    assert "diskFreeBytes" in body
    assert body["cameraAvailable"] is True

    # SSH info card (Settings -> General): sshUser should resolve to the
    # real OS account running the tests on any POSIX box (dev laptop or CI),
    # via pwd.getpwuid -- never blank. sshEnabled's actual value is
    # environment-dependent (no systemd on most dev laptops), so only check
    # its shape, not a specific value.
    assert isinstance(body["sshUser"], str)
    assert body["sshUser"] != ""
    assert isinstance(body["sshEnabled"], bool)


@pytest.mark.asyncio
async def test_update_check_endpoint_delegates_to_updater(client: AsyncClient, monkeypatch):
    # Git plumbing itself is covered against real throwaway repos in
    # test_updater.py; here we only need to know the route is wired up and
    # returns whatever the updater reports, for the configured branch.
    seen = {}

    def fake_check(branch, repo_root=None):
        seen["branch"] = branch
        return UpdateCheckResult(
            branch=branch, updateAvailable=True, commitsBehind=2, commitLog=["abc123 fix"]
        )

    monkeypatch.setattr("rapidboxes.api.update.check_for_update", fake_check)

    res = await client.get("/api/system/update/check")
    assert res.status_code == 200
    body = res.json()
    assert body["updateAvailable"] is True
    assert body["commitsBehind"] == 2
    assert seen["branch"] == "main"  # AppConfig default


@pytest.mark.asyncio
async def test_update_apply_endpoint_delegates_to_updater(client: AsyncClient, monkeypatch):
    def fake_apply(branch, repo_root=None, **kwargs):
        return UpdateApplyResult(status="updated", message="ok", fromCommit="aaa", toCommit="bbb")

    monkeypatch.setattr("rapidboxes.api.update.apply_update", fake_apply)

    res = await client.post("/api/system/update/apply")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "updated"
    assert body["toCommit"] == "bbb"


@pytest.mark.parametrize("state_value", ["running", "paused", "finishing"])
@pytest.mark.asyncio
async def test_update_apply_refused_while_experiment_busy(
    app_config: AppConfig, monkeypatch, state_value: str
):
    # "finishing" isn't currently reachable through the real experiment API
    # (see engine/runner.py -- it's checked but never assigned yet), so this
    # sets runner.status.state directly rather than driving it via HTTP.
    called = {"n": 0}

    def fake_apply(branch, repo_root=None, **kwargs):
        called["n"] += 1
        return UpdateApplyResult(status="updated", message="should not have been called")

    monkeypatch.setattr("rapidboxes.api.update.apply_update", fake_apply)

    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        app.state.app.runner.status.state = ExperimentState(state_value)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/system/update/apply")
            assert res.status_code == 200
            body = res.json()
            assert body["status"] == "experiment_active"

    assert called["n"] == 0


@pytest.mark.parametrize("state_value", ["idle", "done", "error"])
@pytest.mark.asyncio
async def test_update_apply_allowed_while_experiment_not_active(
    app_config: AppConfig, monkeypatch, state_value: str
):
    def fake_apply(branch, repo_root=None, **kwargs):
        return UpdateApplyResult(status="up_to_date", message="already current")

    monkeypatch.setattr("rapidboxes.api.update.apply_update", fake_apply)

    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        app.state.app.runner.status.state = ExperimentState(state_value)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/system/update/apply")
            assert res.status_code == 200
            assert res.json()["status"] == "up_to_date"


@pytest.mark.asyncio
async def test_update_check_allowed_even_while_experiment_running(
    app_config: AppConfig, monkeypatch
):
    # Read-only -- never gated on experiment state.
    def fake_check(branch, repo_root=None):
        return UpdateCheckResult(branch=branch, updateAvailable=True, commitsBehind=1)

    monkeypatch.setattr("rapidboxes.api.update.check_for_update", fake_check)

    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        app.state.app.runner.status.state = ExperimentState.running
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/system/update/check")
            assert res.status_code == 200
            assert res.json()["updateAvailable"] is True


@pytest.mark.asyncio
async def test_update_version_endpoint_delegates_to_updater(client: AsyncClient, monkeypatch):
    entry = UpdateHistoryEntry(commit="abc12345", appliedAt=datetime.now(timezone.utc), trigger="manual")

    def fake_version(history_path):
        return VersionStatus(current=entry, previous=None)

    monkeypatch.setattr("rapidboxes.api.update.get_version_status", fake_version)

    res = await client.get("/api/system/update/version")
    assert res.status_code == 200
    body = res.json()
    assert body["current"]["commit"] == "abc12345"
    assert body["previous"] is None


@pytest.mark.asyncio
async def test_update_rollback_endpoint_delegates_to_updater(client: AsyncClient, monkeypatch):
    def fake_rollback(history_path):
        return UpdateApplyResult(status="rolled_back", message="ok", fromCommit="bbb", toCommit="aaa")

    monkeypatch.setattr("rapidboxes.api.update.rollback_update", fake_rollback)

    res = await client.post("/api/system/update/rollback")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "rolled_back"
    assert body["toCommit"] == "aaa"


@pytest.mark.parametrize("state_value", ["running", "paused", "finishing"])
@pytest.mark.asyncio
async def test_update_rollback_refused_while_experiment_busy(
    app_config: AppConfig, monkeypatch, state_value: str
):
    called = {"n": 0}

    def fake_rollback(history_path):
        called["n"] += 1
        return UpdateApplyResult(status="rolled_back", message="should not have been called")

    monkeypatch.setattr("rapidboxes.api.update.rollback_update", fake_rollback)

    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        app.state.app.runner.status.state = ExperimentState(state_value)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/system/update/rollback")
            assert res.status_code == 200
            assert res.json()["status"] == "experiment_active"

    assert called["n"] == 0


@pytest.mark.parametrize("state_value", ["idle", "done", "error"])
@pytest.mark.asyncio
async def test_update_rollback_allowed_while_experiment_not_active(
    app_config: AppConfig, monkeypatch, state_value: str
):
    def fake_rollback(history_path):
        return UpdateApplyResult(status="nothing_to_roll_back_to", message="No previous version recorded yet.")

    monkeypatch.setattr("rapidboxes.api.update.rollback_update", fake_rollback)

    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        app.state.app.runner.status.state = ExperimentState(state_value)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/system/update/rollback")
            assert res.status_code == 200
            assert res.json()["status"] == "nothing_to_roll_back_to"


@pytest.mark.asyncio
async def test_settings_round_trip(client: AsyncClient):
    res = await client.get("/api/settings")
    assert res.status_code == 200
    defaults = res.json()
    assert defaults["camera"]["width"] == 2304
    assert defaults["camera"]["autofocusEnabled"] is False
    assert defaults["camera"]["focusDistance"] == 10.0

    updated = {
        **defaults,
        "camera": {
            **defaults["camera"],
            "autofocusEnabled": True,
            "focusDistance": 4.5,
        },
        "leds": {**defaults["leds"], "pixelCount": 80},
    }
    res = await client.put("/api/settings", json=updated)
    assert res.status_code == 200
    assert res.json()["leds"]["pixelCount"] == 80
    assert res.json()["camera"]["autofocusEnabled"] is True
    assert res.json()["camera"]["focusDistance"] == 4.5


@pytest.mark.asyncio
async def test_my_defaults_round_trip_covers_camera_and_illumination(client: AsyncClient):
    res = await client.get("/api/settings/mine", params={"username": "lev"})
    assert res.status_code == 200
    assert res.json() is None  # nothing saved yet

    res = await client.get("/api/settings")
    settings = res.json()
    settings["camera"]["iso"] = 800
    settings["camera"]["focusDistance"] = 12.5
    settings["leds"]["pixelCount"] = 90
    settings["photoIlluminationSource"] = "rgbw"

    res = await client.put("/api/settings/mine", json={"username": "lev", "settings": settings})
    assert res.status_code == 200
    assert res.json()["camera"]["iso"] == 800
    assert res.json()["leds"]["pixelCount"] == 90
    assert res.json()["photoIlluminationSource"] == "rgbw"

    res = await client.get("/api/settings/mine", params={"username": "lev"})
    assert res.status_code == 200
    assert res.json()["camera"]["iso"] == 800
    assert res.json()["leds"]["pixelCount"] == 90

    # A different researcher's saved defaults are untouched.
    res = await client.get("/api/settings/mine", params={"username": "someone-else"})
    assert res.status_code == 200
    assert res.json() is None

    # Saving Mine does not touch the active session settings (that's the
    # separate PUT /api/settings save).
    res = await client.get("/api/settings")
    assert res.json()["camera"]["iso"] != 800


@pytest.mark.asyncio
async def test_list_users_combines_experiment_history_and_saved_defaults(
    client: AsyncClient, app_config: AppConfig
):
    (app_config.storage_root / "2026-01-01_Ivan_run-a").mkdir(parents=True)
    (app_config.storage_root / "2026-01-02_Ivan_run-b").mkdir(parents=True)
    (app_config.storage_root / "2026-01-03_bob_run-c").mkdir(parents=True)

    res = await client.get("/api/settings")
    settings = res.json()
    res = await client.put("/api/settings/mine", json={"username": "lev", "settings": settings})
    assert res.status_code == 200

    res = await client.get("/api/users")
    assert res.status_code == 200
    # case-insensitive alphabetical, with each user's experiment count
    assert res.json() == [
        {"username": "bob", "experimentCount": 1},
        {"username": "ivan", "experimentCount": 2},
        {"username": "lev", "experimentCount": 0},
    ]


@pytest.mark.asyncio
async def test_list_users_folds_case_variants_into_one_lowercased_entry(
    client: AsyncClient, app_config: AppConfig
):
    (app_config.storage_root / "2026-01-01_Ivan_run-a").mkdir(parents=True)
    (app_config.storage_root / "2026-01-02_IVAN_run-b").mkdir(parents=True)
    (app_config.storage_root / "2026-01-03_ivan_run-c").mkdir(parents=True)
    res = await client.get("/api/settings")
    await client.put("/api/settings/mine", json={"username": "IvAn", "settings": res.json()})

    res = await client.get("/api/users")
    assert res.json() == [{"username": "ivan", "experimentCount": 3}]  # one entry, not four


@pytest.mark.asyncio
async def test_start_experiment_busy_after_start(client: AsyncClient):
    config = TropismConfig(
        experimentName="api-test",
        darkPhaseEnabled=True,
        darkPhaseHours=0.01,
        lateralIlluminationHours=0,
        intervalMinutes=1,
    )
    res = await client.post("/api/experiments", json=config.model_dump())
    assert res.status_code == 200
    assert res.json()["status"] == "started"

    res = await client.post("/api/experiments", json=config.model_dump())
    assert res.status_code == 200
    assert res.json()["status"] == "busy"

    await client.post("/api/experiments/current/stop")


@pytest.mark.asyncio
async def test_free_space_only_deletes_requesting_users_own_folders(
    client: AsyncClient, app_config: AppConfig
):
    alice_dir = app_config.storage_root / "2026-01-01_alice_keepme"
    bob_dir = app_config.storage_root / "2026-01-01_bob_untouchable"
    alice_dir.mkdir(parents=True)
    bob_dir.mkdir(parents=True)

    res = await client.post(
        "/api/experiments/free-space",
        json={"username": "alice", "experimentIds": [alice_dir.name, bob_dir.name]},
    )
    assert res.status_code == 200
    body = res.json()

    assert body["deletedIds"] == [alice_dir.name]
    assert not alice_dir.exists()
    assert bob_dir.exists()  # never touched, even though its id was requested


@pytest.mark.asyncio
async def test_abort_stops_and_deletes_experiment(client: AsyncClient, app_config: AppConfig):
    config = TropismConfig(
        experimentName="abort-me",
        darkPhaseEnabled=True,
        darkPhaseHours=0.05,
        lateralIlluminationHours=0,
        intervalMinutes=1,
    )
    res = await client.post("/api/experiments", json=config.model_dump())
    assert res.status_code == 200
    experiment_id = res.json()["experimentId"]
    assert experiment_id
    exp_dir = app_config.storage_root / experiment_id
    assert exp_dir.is_dir()

    res = await client.post("/api/experiments/current/abort")
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "idle"
    assert not exp_dir.exists()
    assert (await client.get("/api/experiments/history")).json() == []


@pytest.mark.asyncio
async def test_download_experiment_zip_contains_all_files(app_config: AppConfig):
    """The download endpoint should zip everything under the experiment's
    folder (images, metadata.json, saved config xml) and offer it as a
    downloadable attachment, without loading the whole archive into memory
    at once (see the comment on download_experiment for why)."""
    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        storage = app.state.app.storage
        exp = storage.create_experiment("alice", "zip-test")
        (exp.path / "dark_00000.jpg").write_bytes(b"fake-jpeg-bytes-1")
        (exp.path / "bending_00000.jpg").write_bytes(b"fake-jpeg-bytes-2")
        exp.write_metadata({"experimentName": "zip-test", "username": "alice", "imagesCaptured": 2})
        exp.write_config_xml(b"<config></config>", "zip-test")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get(f"/api/experiments/{exp.experiment_id}/download")

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert f'filename="{exp.experiment_id}.zip"' in res.headers["content-disposition"]
    assert "attachment" in res.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        assert zf.testzip() is None  # no corrupt members
        names = set(zf.namelist())
        assert "dark_00000.jpg" in names
        assert "bending_00000.jpg" in names
        assert "metadata.json" in names
        assert "zip-test.xml" in names
        assert zf.read("dark_00000.jpg") == b"fake-jpeg-bytes-1"


@pytest.mark.asyncio
async def test_download_experiment_404_for_missing_experiment(client: AsyncClient):
    res = await client.get("/api/experiments/does-not-exist/download")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_cannot_change_settings_while_running(client: AsyncClient, app_config: AppConfig):
    config = TropismConfig(
        experimentName="lock-test",
        darkPhaseEnabled=True,
        darkPhaseHours=0.05,
        lateralIlluminationHours=0,
        intervalMinutes=1,
    )
    await client.post("/api/experiments", json=config.model_dump())

    settings = (await client.get("/api/settings")).json()
    settings["leds"]["pixelCount"] = 99
    res = await client.put("/api/settings", json=settings)
    assert res.status_code == 409

    await client.post("/api/experiments/current/stop")


@pytest.mark.asyncio
async def test_live_backlight_round_trip(client: AsyncClient):
    res = await client.post("/api/preview/backlight", json={"mode": "white"})
    assert res.status_code == 200
    assert res.json()["mode"] == "white"

    # IR assist removed from Live — focusing under IR is Settings → test photo.
    res = await client.post("/api/preview/backlight", json={"mode": "ir"})
    assert res.status_code == 422

    res = await client.post("/api/preview/backlight", json={"mode": "off"})
    assert res.status_code == 200
    assert res.json()["mode"] == "off"


@pytest.mark.asyncio
async def test_live_backlight_blocked_while_running(client: AsyncClient):
    config = TropismConfig(
        experimentName="backlight-lock",
        darkPhaseEnabled=True,
        darkPhaseHours=0.05,
        lateralIlluminationHours=0,
        intervalMinutes=1,
    )
    await client.post("/api/experiments", json=config.model_dump())

    res = await client.post("/api/preview/backlight", json={"mode": "white"})
    assert res.status_code == 409

    await client.post("/api/experiments/current/stop")


@pytest.mark.asyncio
async def test_camera_settings_test_photo_follows_illumination_setting():
    """Camera Settings test photo follows the persisted photoIlluminationSource
    setting (IR vs RGBW top flash) — independent of the camera's colour mode,
    so it previews exactly what a real dark/baseline/night capture would use."""
    from rapidboxes.hardware.manager import HardwareManager
    from rapidboxes.hardware.simulation import SimCamera, SimIr, SimLeds
    from rapidboxes.models import PHOTO_FLASH_INTENSITY, CameraSettings, DeviceSettings
    from rapidboxes.hardware.base import white

    ir = SimIr()
    leds = SimLeds(70)
    cam = SimCamera()
    seen: dict = {}

    def probe(settings: CameraSettings) -> bytes:
        seen["ir"] = ir.state
        seen["pixel"] = leds.pixels[22]  # inside the default top segment
        return SimCamera.capture_test_jpeg(cam, settings)

    cam.capture_test_jpeg = probe  # type: ignore[method-assign]

    # grayscale=False here on purpose: illumination must not be inferred from
    # colour mode, only from photoIlluminationSource.
    hw_ir = HardwareManager(cam, leds, ir, DeviceSettings(photoIlluminationSource="ir"))
    await hw_ir.capture_test_jpeg(CameraSettings(grayscale=False))
    assert seen["ir"] is True
    assert ir.state is False

    hw_rgbw = HardwareManager(cam, leds, ir, DeviceSettings(photoIlluminationSource="rgbw"))
    await hw_rgbw.capture_test_jpeg(CameraSettings(grayscale=True))
    assert seen["ir"] is False
    assert seen["pixel"] == white(PHOTO_FLASH_INTENSITY)
    assert ir.state is False
    assert leds.pixels[22] == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_zoom_crops_and_rescales_to_configured_size():
    """CameraSettings.zoom center-crops the frame and scales it back to
    width x height, so every saved image is the configured size regardless of
    framing -- and a real crop happens, not a no-op."""
    from rapidboxes.hardware.simulation import SimCamera
    from rapidboxes.models import CameraSettings

    cam = SimCamera()
    settings = CameraSettings(width=640, height=360, zoom=1.0)
    unzoomed = cam._zoomed_frame(settings)
    assert unzoomed.size == (640, 360)

    zoomed_settings = CameraSettings(width=640, height=360, zoom=2.0)
    zoomed = cam._zoomed_frame(zoomed_settings)
    # Same output size as the unzoomed frame...
    assert zoomed.size == (640, 360)
    # ...but a real crop happened: the two frames differ (the sim overlay
    # text sits near the top-left corner and falls outside a 2x center crop).
    assert list(zoomed.getdata()) != list(unzoomed.getdata())


def test_settle_seconds_scales_with_exposure_bounded():
    from rapidboxes.models import (
        SETTLE_SECONDS_MAX,
        SETTLE_SECONDS_MIN,
        settle_seconds_for,
    )

    assert settle_seconds_for(10_000) == SETTLE_SECONDS_MIN  # 10ms flash: floors
    assert settle_seconds_for(500_000) == 0.5  # within range: passes through
    assert settle_seconds_for(10_000_000) == SETTLE_SECONDS_MAX  # 10s IR: caps


@pytest.mark.asyncio
async def test_growth_heatmap_endpoint(client: AsyncClient, app_config: AppConfig):
    """Growth route returns JPEG for an experiment with >=2 frames."""
    from PIL import Image

    exp_dir = app_config.storage_root / "2026-01-01_test_growth"
    exp_dir.mkdir(parents=True)
    for i in range(3):
        arr = np.full((64, 64), 40 + i * 5, dtype=np.uint8)
        # Localized bright spot that moves
        arr[30:38, 10 + i * 8 : 18 + i * 8] = 220
        Image.fromarray(arr, mode="L").save(exp_dir / f"day_{i:05d}.png", "PNG")

    res = await client.get("/api/images/2026-01-01_test_growth/growth")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/jpeg")
    assert len(res.content) > 100

    # Cache hit still succeeds
    res2 = await client.get("/api/images/2026-01-01_test_growth/growth")
    assert res2.status_code == 200

    # Fewer than 2 frames → 400
    single = app_config.storage_root / "2026-01-01_test_one"
    single.mkdir()
    Image.fromarray(np.zeros((32, 32), dtype=np.uint8), mode="L").save(single / "day_00000.png")
    res_bad = await client.get("/api/images/2026-01-01_test_one/growth")
    assert res_bad.status_code == 400


@pytest.mark.asyncio
async def test_plant_mask_start_and_artifacts(client: AsyncClient, app_config: AppConfig):
    from PIL import Image

    exp_dir = app_config.storage_root / "2026-01-01_test_plant"
    exp_dir.mkdir(parents=True)
    for i in range(4):
        arr = np.full((48, 48), 40, dtype=np.uint8)
        arr[20:28, 8 + i * 6 : 14 + i * 6] = 220
        Image.fromarray(arr, mode="L").save(exp_dir / f"day_{i:05d}.png", "PNG")

    res = await client.post("/api/images/2026-01-01_test_plant/plant-mask/start")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in ("running", "done")

    import asyncio

    for _ in range(100):
        st = (await client.get("/api/images/2026-01-01_test_plant/plant-mask/status")).json()
        if st["status"] == "done":
            break
        if st["status"] == "error":
            pytest.fail(st.get("message", "error"))
        await asyncio.sleep(0.05)
    else:
        pytest.fail("plant mask did not finish")

    mask = await client.get("/api/images/2026-01-01_test_plant/artifacts/plant_mask")
    assert mask.status_code == 200
    assert mask.headers["content-type"].startswith("image/png")

    overlay = await client.get("/api/images/2026-01-01_test_plant/artifacts/plant_overlay")
    assert overlay.status_code == 200

    listing = (await client.get("/api/images/2026-01-01_test_plant")).json()
    ids = {img["id"] for img in listing["images"]}
    assert "plant_mask" in ids
    assert "plant_overlay" in ids

