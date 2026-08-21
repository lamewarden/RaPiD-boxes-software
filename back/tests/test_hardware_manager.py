"""HardwareManager._run() timeout: a stuck device call must fail fast and
release the lock for everyone else, instead of blocking forever (the bug
behind "hit White backlight in Live and everything freezes")."""
from __future__ import annotations

import time

import pytest

from rapidboxes.hardware.base import HardwareTimeoutError
from rapidboxes.hardware.manager import HardwareManager
from rapidboxes.hardware.simulation import SimCamera, SimIr, SimLeds
from rapidboxes.models import DeviceSettings


def _manager() -> HardwareManager:
    return HardwareManager(SimCamera(), SimLeds(), SimIr(), DeviceSettings())


@pytest.mark.asyncio
async def test_run_times_out_on_a_stuck_call():
    hw = _manager()

    def stuck(*_args):
        time.sleep(1.0)

    with pytest.raises(HardwareTimeoutError):
        await hw._run(stuck, timeout=0.05)


@pytest.mark.asyncio
async def test_run_releases_lock_after_timeout_so_later_calls_still_work():
    hw = _manager()

    def stuck(*_args):
        time.sleep(1.0)

    with pytest.raises(HardwareTimeoutError):
        await hw._run(stuck, timeout=0.05)

    # The lock must be free again immediately -- a quick call right after
    # should not be stuck queued behind the (leaked, still-running) stuck one.
    result = await hw._run(lambda: 42, timeout=1.0)
    assert result == 42
