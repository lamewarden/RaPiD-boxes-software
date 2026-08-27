"""HardwareManager._run() timeout: a stuck device call must fail fast rather
than blocking forever (the original bug behind "hit White backlight in Live
and everything freezes").

A later, separate bug (DEBUG_HANDOUT.md #1.9): asyncio.wait_for giving up on
a timed-out call does NOT stop the underlying executor thread -- Python
threads can't be force-cancelled -- so it may still be running the blocking
driver call indefinitely. The original fix released the lock on timeout so a
*different* stuck call couldn't deadlock everything forever, but that also
let the *next* hardware call acquire the lock and run genuinely concurrently
with the still-stuck one -- exactly the race this class's own docstring
promises can never happen. The fix below keeps "fails fast" (this module's
original point) while removing "and then lets someone else drive the same
device concurrently" (the regression): once a timeout occurs, the whole
manager is presumed wedged and every further call fails fast too, without
even attempting the lock, until the service restarts."""
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
async def test_run_wedges_after_a_timeout_instead_of_letting_a_race_through():
    """A later call must fail FAST (not hang forever queued behind the
    leaked, still-running stuck one -- the original bug this module guards
    against) -- but it must also fail, not silently succeed, since the
    executor thread behind the first call may still genuinely be running.
    See DEBUG_HANDOUT.md #1.9."""
    hw = _manager()

    def stuck(*_args):
        time.sleep(1.0)

    with pytest.raises(HardwareTimeoutError):
        await hw._run(stuck, timeout=0.05)

    assert hw._wedged is True
    start = time.monotonic()
    with pytest.raises(HardwareTimeoutError, match="wedged"):
        await hw._run(lambda: 42, timeout=1.0)
    # Failed fast -- never even tried to wait on the lock/executor again.
    assert time.monotonic() - start < 0.1


@pytest.mark.asyncio
async def test_all_off_is_one_atomic_call_not_two_separately_interleavable_ones(
    monkeypatch,
):
    """Previously leds_off() then ir_off(), each its own _run()/lock
    acquisition -- another coroutine's call could interleave between them,
    so "all off" was never actually a state a caller could rely on having
    reached (relevant since _capture uses it as step 1 of every imaging
    sequence). Now one _run() call covers both devices. See
    DEBUG_HANDOUT.md #1.10."""
    hw = _manager()
    run_calls = []
    original_run = hw._run

    async def counting_run(fn, *args, **kw):
        run_calls.append(fn)
        return await original_run(fn, *args, **kw)

    monkeypatch.setattr(hw, "_run", counting_run)

    await hw.all_off()

    assert len(run_calls) == 1
    assert hw.light_desc == "off"
