"""Pure capture-scheduling math (no I/O), so it can be unit-tested deterministically.

Fixes the legacy bug where `time.sleep(period - elapsed)` went negative when a
capture overran its interval, silently collapsing the cadence into a burst.
"""
from __future__ import annotations

import math
from typing import Tuple

_EPS = 1e-9


def planned_captures(duration_s: float, interval_s: float) -> int:
    """Number of captures at t = 0, interval, 2*interval, ... while t < duration."""
    if duration_s <= 0 or interval_s <= 0:
        return 0
    return max(1, math.ceil(duration_s / interval_s - _EPS))


def advance_deadline(next_deadline: float, interval_s: float, now: float) -> Tuple[float, int]:
    """Advance the capture deadline by one interval.

    If the new deadline is still in the past (the previous capture overran),
    skip the whole missed intervals and realign to the future instead of firing
    a catch-up burst. Returns (new_deadline, skipped_count).
    """
    nxt = next_deadline + interval_s
    if nxt > now:
        return nxt, 0
    behind = now - nxt
    skip = int(behind // interval_s) + 1
    return nxt + skip * interval_s, skip
