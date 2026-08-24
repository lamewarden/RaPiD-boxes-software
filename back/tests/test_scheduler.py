import pytest

from rapidboxes.engine.scheduler import advance_deadline, images_expected, phase_at, planned_captures


def test_planned_captures_counts_t0_and_excludes_endpoint():
    assert planned_captures(180, 60) == 3   # t = 0, 60, 120
    assert planned_captures(120, 60) == 2   # t = 0, 60
    assert planned_captures(5, 5) == 1      # t = 0
    assert planned_captures(4, 5) == 1      # t = 0
    assert planned_captures(0, 60) == 0
    assert planned_captures(60, 0) == 0


def test_advance_deadline_normal_case_no_skip():
    nxt, skipped = advance_deadline(0.0, 60.0, now=1.0)
    assert nxt == 60.0
    assert skipped == 0


def test_advance_deadline_realigns_after_overrun():
    # A capture finished at now=205s but the deadline was 60s: 2 whole intervals
    # (60, 120, 180) are in the past -> realign to the next future slot, no burst.
    nxt, skipped = advance_deadline(0.0, 60.0, now=205.0)
    assert skipped >= 1
    assert nxt > 205.0
    # The realigned deadline stays on the interval grid.
    assert abs((nxt % 60.0)) < 1e-9


def test_phase_at_locates_mid_phase():
    assert phase_at([100.0, 200.0, 50.0], 0.0) == (0, 0.0)
    assert phase_at([100.0, 200.0, 50.0], 50.0) == (0, 50.0)
    assert phase_at([100.0, 200.0, 50.0], 100.0) == (1, 0.0)
    assert phase_at([100.0, 200.0, 50.0], 250.0) == (1, 150.0)
    index, phase_elapsed = phase_at([100.0, 200.0, 50.0], 349.999)
    assert index == 2
    assert phase_elapsed == pytest.approx(49.999)


def test_phase_at_none_once_whole_schedule_elapsed():
    assert phase_at([100.0, 200.0, 50.0], 350.0) is None
    assert phase_at([100.0, 200.0, 50.0], 10_000.0) is None
    assert phase_at([], 0.0) is None


def test_images_expected_sums_full_phases_plus_partial_current():
    phases = [(180.0, True), (120.0, False), (60.0, True)]
    # Fully past phase 0 (180s @ 60s -> 3), skip the non-capturing phase 1,
    # 30s into capturing phase 2 (30s @ 60s -> 1, t=0 only).
    assert images_expected(phases, index=2, phase_elapsed=30.0, interval_s=60.0) == 4


def test_images_expected_zero_at_the_very_start_of_a_phase():
    phases = [(180.0, True)]
    assert images_expected(phases, index=0, phase_elapsed=0.0, interval_s=60.0) == 0
