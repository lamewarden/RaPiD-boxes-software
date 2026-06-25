from rapidboxes.engine.scheduler import advance_deadline, planned_captures


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
