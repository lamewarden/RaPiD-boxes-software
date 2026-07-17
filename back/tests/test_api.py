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
