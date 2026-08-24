"""Pure capture-scheduling math (no I/O), so it can be unit-tested deterministically.

Fixes the legacy bug where `time.sleep(period - elapsed)` went negative when a
capture overran its interval, silently collapsing the cadence into a burst.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

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


def phase_at(durations: List[float], elapsed_s: float) -> Optional[Tuple[int, float]]:
    """Which phase index `elapsed_s` (seconds since the experiment started)
    falls into, and how far into that phase.

    Used on recovery to fast-forward a run by however long the box was off,
    the same way `advance_deadline` fast-forwards a single capture deadline.
    Returns None once `elapsed_s` reaches or exceeds the total of all
    durations -- the whole sequence has already elapsed.
    """
    cumulative = 0.0
    for i, d in enumerate(durations):
        if elapsed_s < cumulative + d - _EPS:
            return i, max(0.0, elapsed_s - cumulative)
        cumulative += d
    return None


def images_expected(
    phases: List[Tuple[float, bool]], index: int, phase_elapsed: float, interval_s: float
) -> int:
    """How many captures should have happened by `phase_elapsed` into phase
    `index`, given each phase's (duration_s, capture) pair -- i.e. what a
    fully-uninterrupted run would have taken by this point. Compared against
    the images actually captured, the gap is how many were missed to an
    outage."""
    expected = 0
    for i, (duration, capture) in enumerate(phases):
        if not capture:
            continue
        if i < index:
            expected += planned_captures(duration, interval_s)
        elif i == index and phase_elapsed > 0:
            expected += planned_captures(phase_elapsed, interval_s)
    return expected
