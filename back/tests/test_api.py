"""FastAPI integration tests with simulated hardware."""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from rapidboxes.config import AppConfig
from rapidboxes.main import create_app
from rapidboxes.models import TropismConfig


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        simulation=True,
        storage_root=tmp_path / "experiments",
        settings_path=tmp_path / "settings.json",
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


@pytest.mark.asyncio
async def test_settings_round_trip(client: AsyncClient):
    res = await client.get("/api/settings")
    assert res.status_code == 200
    defaults = res.json()
    assert defaults["camera"]["width"] == 2304
    assert defaults["camera"]["autofocusEnabled"] is True

    updated = {
        **defaults,
        "camera": {
            **defaults["camera"],
            "autofocusEnabled": False,
            "focusDistance": 4.5,
        },
        "leds": {**defaults["leds"], "pixelCount": 80},
    }
    res = await client.put("/api/settings", json=updated)
    assert res.status_code == 200
    assert res.json()["leds"]["pixelCount"] == 80
    assert res.json()["camera"]["autofocusEnabled"] is False
    assert res.json()["camera"]["focusDistance"] == 4.5


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

    res = await client.post("/api/preview/backlight", json={"mode": "ir"})
    assert res.status_code == 200
    assert res.json()["mode"] == "ir"

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
async def test_camera_settings_test_photo_lights_by_grayscale():
    """Camera Settings test photo: IR when grayscale, RGBW (10,10,10,10) when colour."""
    from rapidboxes.hardware.manager import LIVE_WHITE_BACKLIGHT, HardwareManager
    from rapidboxes.hardware.simulation import SimCamera, SimIr, SimLeds
    from rapidboxes.models import CameraSettings, DeviceSettings

    ir = SimIr()
    leds = SimLeds(10)
    cam = SimCamera()
    seen: dict = {}

    def probe(settings: CameraSettings) -> bytes:
        seen["ir"] = ir.state
        seen["pixel"] = leds.pixels[0]
        return SimCamera.capture_test_jpeg(cam, settings)

    cam.capture_test_jpeg = probe  # type: ignore[method-assign]
    hw = HardwareManager(cam, leds, ir, DeviceSettings())

    await hw.capture_test_jpeg(CameraSettings(grayscale=True, settleSeconds=0))
    assert seen["ir"] is True
    assert ir.state is False

    await hw.capture_test_jpeg(CameraSettings(grayscale=False, settleSeconds=0))
    assert seen["ir"] is False
    assert seen["pixel"] == LIVE_WHITE_BACKLIGHT
    assert ir.state is False
    assert leds.pixels[0] == (0, 0, 0, 0)
