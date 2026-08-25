"""Tests for kiosk_screenshot.py: real grim subprocess invocation is
mocked throughout (no real Wayland session in CI/dev), but the socket-
existence check and subprocess success/failure handling are exercised for
real."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rapidboxes.kiosk_screenshot import KioskScreenshotUnavailable, capture_kiosk_screenshot


@pytest.mark.asyncio
async def test_no_socket_reports_unavailable_without_touching_a_subprocess(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("os.getuid", lambda: 999999)  # a uid with no /run/user dir in CI

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("must not attempt a subprocess with no compositor socket")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_if_called)

    with pytest.raises(KioskScreenshotUnavailable, match="no kiosk display session"):
        await capture_kiosk_screenshot()


@pytest.mark.asyncio
async def test_missing_grim_binary_reports_unavailable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("rapidboxes.kiosk_screenshot.os.path.exists", lambda p: True)

    async def raise_not_found(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", raise_not_found)

    with pytest.raises(KioskScreenshotUnavailable, match="isn't installed"):
        await capture_kiosk_screenshot()


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int, hang: bool = False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(999)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_successful_capture_returns_the_real_bytes(monkeypatch):
    monkeypatch.setattr("rapidboxes.kiosk_screenshot.os.path.exists", lambda p: True)
    fake = _FakeProc(stdout=b"\x89PNG-real-bytes", stderr=b"", returncode=0)

    async def fake_exec(*args, **kwargs):
        assert args[0] == "grim"
        assert "-" in args
        assert kwargs["env"]["WAYLAND_DISPLAY"] == "wayland-0"
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await capture_kiosk_screenshot()
    assert result == b"\x89PNG-real-bytes"


@pytest.mark.asyncio
async def test_nonzero_exit_reports_the_real_stderr(monkeypatch):
    monkeypatch.setattr("rapidboxes.kiosk_screenshot.os.path.exists", lambda p: True)
    fake = _FakeProc(stdout=b"", stderr=b"compositor doesn't support wlr-screencopy", returncode=1)

    async def fake_exec(*args, **kwargs):
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(KioskScreenshotUnavailable, match="wlr-screencopy"):
        await capture_kiosk_screenshot()


@pytest.mark.asyncio
async def test_hung_subprocess_times_out_and_is_killed(monkeypatch):
    monkeypatch.setattr("rapidboxes.kiosk_screenshot.os.path.exists", lambda p: True)
    monkeypatch.setattr("rapidboxes.kiosk_screenshot._TIMEOUT_S", 0.05)
    fake = _FakeProc(stdout=b"", stderr=b"", returncode=0, hang=True)

    async def fake_exec(*args, **kwargs):
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(KioskScreenshotUnavailable, match="timed out"):
        await capture_kiosk_screenshot()
    assert fake.killed is True
