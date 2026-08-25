"""Tests for DsmSharingService (rapidboxes/dsm_sharing.py): DSM login,
sharing-link creation, and the settings API. The real DSM host is never
called -- `_client.get` is monkeypatched with a fake that inspects the
`api`/`method` query params and returns the JSON shape the real endpoint
would, matching the mocking style already used for Telegram/remote sync.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from rapidboxes import dsm_sharing as ds
from rapidboxes.config import AppConfig
from rapidboxes.main import create_app
from rapidboxes.models import DsmSharingSettings


def make_service(tmp_path: Path, **kwargs) -> ds.DsmSharingService:
    settings = kwargs.pop(
        "settings",
        DsmSharingSettings(
            enabled=True, host="ds-ueb-if.example.org", port=5001, username="ivan", shareRoot="/volume1/ueb-if"
        ),
    )
    service = ds.DsmSharingService(settings, settings_path=kwargs.pop("settings_path", tmp_path / "dsm.json"))
    service.set_password(kwargs.pop("password", "s3cret"))
    return service


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._body


def _dispatcher(monkeypatch, service: ds.DsmSharingService, responses: dict) -> list:
    """responses maps a SYNO API name (e.g. "SYNO.API.Auth") to the JSON
    body its call should return. Records every (params) call made."""
    calls: list = []

    async def fake_get(url, params=None, **kw):
        calls.append(params)
        api = (params or {}).get("api")
        return _FakeResponse(responses.get(api, {"success": False, "error": {"code": 999}}))

    monkeypatch.setattr(service._client, "get", fake_get)
    return calls


# --- password / configuration state ----------------------------------------


def test_credentials_required_only_when_enabled_and_no_password(tmp_path: Path):
    service = make_service(tmp_path, settings=DsmSharingSettings(enabled=False))
    assert service.credentials_required is False  # off entirely -- not "needs creds"

    service = make_service(tmp_path, settings=DsmSharingSettings(enabled=True), password="")
    service.clear_password()
    assert service.credentials_required is True

    service = make_service(tmp_path, settings=DsmSharingSettings(enabled=True))
    assert service.credentials_required is False
    assert service.password_set is True


def test_remote_path_for_slugifies_the_username(tmp_path: Path):
    service = make_service(tmp_path)
    assert service.remote_path_for("Ivan Kashkan") == "/volume1/ueb-if/Ivan-Kashkan"


# --- login / check_connection ------------------------------------------------


@pytest.mark.asyncio
async def test_check_connection_requires_host_username_password(tmp_path: Path):
    service = make_service(tmp_path, settings=DsmSharingSettings(enabled=True, host="", username="", shareRoot=""))
    service.clear_password()
    ok, message = await service.check_connection()
    assert ok is False
    assert "required" in message


@pytest.mark.asyncio
async def test_check_connection_success(tmp_path: Path, monkeypatch):
    service = make_service(tmp_path)
    calls = _dispatcher(
        monkeypatch,
        service,
        {"SYNO.API.Auth": {"success": True, "data": {"sid": "abc123"}}},
    )
    ok, message = await service.check_connection()
    assert ok is True
    assert "ds-ueb-if.example.org" in message
    # login then logout -- both hit SYNO.API.Auth.
    assert [c["method"] for c in calls] == ["login", "logout"]
    assert calls[1]["_sid"] == "abc123"


@pytest.mark.asyncio
async def test_check_connection_reports_wrong_password(tmp_path: Path, monkeypatch):
    service = make_service(tmp_path)
    _dispatcher(
        monkeypatch,
        service,
        {"SYNO.API.Auth": {"success": False, "error": {"code": 400}}},
    )
    ok, message = await service.check_connection()
    assert ok is False
    assert "incorrect password" in message


@pytest.mark.asyncio
async def test_login_network_error_is_reported_not_raised(tmp_path: Path, monkeypatch):
    service = make_service(tmp_path)

    async def failing_get(url, params=None, **kw):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(service._client, "get", failing_get)
    ok, message = await service.check_connection()
    assert ok is False
    assert "could not reach" in message.lower()


# --- create_share_link -------------------------------------------------------


@pytest.mark.asyncio
async def test_create_share_link_success(tmp_path: Path, monkeypatch):
    service = make_service(tmp_path)
    calls = _dispatcher(
        monkeypatch,
        service,
        {
            "SYNO.API.Auth": {"success": True, "data": {"sid": "abc123"}},
            "SYNO.FileStation.Sharing": {
                "success": True,
                "data": {"links": [{"id": "rsZdI8dEq", "url": "https://ds-ueb-if.example.org:5001/sharing/rsZdI8dEq"}]},
            },
        },
    )
    ok, result = await service.create_share_link("ivan", "2026-01-01_ivan_run1")
    assert ok is True
    assert result == "https://ds-ueb-if.example.org:5001/sharing/rsZdI8dEq"

    sharing_call = next(c for c in calls if c["api"] == "SYNO.FileStation.Sharing")
    assert sharing_call["path"] == '["/volume1/ueb-if/ivan/2026-01-01_ivan_run1"]'
    assert sharing_call["_sid"] == "abc123"
    # login, create, logout -- always logs out even on success.
    assert [c["method"] for c in calls] == ["login", "create", "logout"]


@pytest.mark.asyncio
async def test_create_share_link_when_not_connected(tmp_path: Path):
    service = make_service(tmp_path, settings=DsmSharingSettings(enabled=False))
    ok, message = await service.create_share_link("ivan", "run1")
    assert ok is False
    assert "isn't connected" in message


@pytest.mark.asyncio
async def test_create_share_link_when_share_root_missing(tmp_path: Path):
    service = make_service(
        tmp_path,
        settings=DsmSharingSettings(enabled=True, host="h", username="u", shareRoot=""),
    )
    ok, message = await service.create_share_link("ivan", "run1")
    assert ok is False
    assert "share root" in message


@pytest.mark.asyncio
async def test_create_share_link_login_failure_never_calls_sharing_api(tmp_path: Path, monkeypatch):
    service = make_service(tmp_path)
    calls = _dispatcher(
        monkeypatch,
        service,
        {"SYNO.API.Auth": {"success": False, "error": {"code": 400}}},
    )
    ok, message = await service.create_share_link("ivan", "run1")
    assert ok is False
    assert "incorrect password" in message
    assert all(c["api"] == "SYNO.API.Auth" for c in calls)


@pytest.mark.asyncio
async def test_create_share_link_dsm_error_response(tmp_path: Path, monkeypatch):
    service = make_service(tmp_path)
    _dispatcher(
        monkeypatch,
        service,
        {
            "SYNO.API.Auth": {"success": True, "data": {"sid": "abc123"}},
            "SYNO.FileStation.Sharing": {"success": False, "error": {"code": 408}},
        },
    )
    ok, message = await service.create_share_link("ivan", "does-not-exist")
    assert ok is False
    assert "408" in message


@pytest.mark.asyncio
async def test_create_share_link_no_links_in_response(tmp_path: Path, monkeypatch):
    service = make_service(tmp_path)
    _dispatcher(
        monkeypatch,
        service,
        {
            "SYNO.API.Auth": {"success": True, "data": {"sid": "abc123"}},
            "SYNO.FileStation.Sharing": {"success": True, "data": {"links": []}},
        },
    )
    ok, message = await service.create_share_link("ivan", "run1")
    assert ok is False
    assert "didn't return a sharing link" in message


# --- persistence --------------------------------------------------------------


def test_settings_persist_without_the_password(tmp_path: Path):
    path = tmp_path / "dsm.json"
    settings = DsmSharingSettings(
        enabled=True, host="ds-ueb-if.example.org", port=5001, username="ivan", shareRoot="/volume1/ueb-if"
    )
    ds.save_dsm_sharing_settings(path, settings)
    assert "password" not in path.read_text()

    reloaded = ds.load_dsm_sharing_settings(path)
    assert reloaded.host == "ds-ueb-if.example.org"
    assert reloaded.shareRoot == "/volume1/ueb-if"


def test_load_missing_settings_file_returns_defaults(tmp_path: Path):
    reloaded = ds.load_dsm_sharing_settings(tmp_path / "does-not-exist.json")
    assert reloaded.enabled is False
    assert reloaded.port == 5001


# --- API: /api/settings/dsm-sharing ------------------------------------------


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        simulation=True,
        storage_root=tmp_path / "experiments",
        settings_path=tmp_path / "settings.json",
        dsm_sharing_path=tmp_path / "dsm_sharing.json",
        spa_dir=None,
    )


@pytest.fixture
async def client(app_config: AppConfig):
    app = create_app(app_config)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac._app = app  # type: ignore[attr-defined]
            yield ac


@pytest.mark.asyncio
async def test_get_dsm_sharing_never_returns_a_password_field(client: AsyncClient):
    res = await client.get("/api/settings/dsm-sharing")
    assert res.status_code == 200
    assert "password" not in res.json()


@pytest.mark.asyncio
async def test_put_rejects_a_malformed_host(client: AsyncClient):
    res = await client.put("/api/settings/dsm-sharing", json={"host": "not a host!"})
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_put_rejects_a_malformed_share_root(client: AsyncClient):
    res = await client.put("/api/settings/dsm-sharing", json={"shareRoot": "not/absolute"})
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_put_rejects_enabling_without_credentials(client: AsyncClient):
    res = await client.put("/api/settings/dsm-sharing", json={"enabled": True})
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_put_accepts_a_full_config_and_enables(client: AsyncClient):
    res = await client.put(
        "/api/settings/dsm-sharing",
        json={
            "host": "ds-ueb-if.asuch.cas.cz",
            "port": 5001,
            "username": "ivan",
            "password": "s3cret",
            "shareRoot": "/volume1/ueb-if",
            "enabled": True,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True
    assert body["passwordSet"] is True
    assert "password" not in body


@pytest.mark.asyncio
async def test_check_endpoint_requires_credentials_first(client: AsyncClient):
    res = await client.post("/api/settings/dsm-sharing/check")
    assert res.status_code == 400
