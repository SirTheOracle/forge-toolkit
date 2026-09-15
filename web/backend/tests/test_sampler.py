"""The sampler state machine, driven by a deterministic clock and a fixed-jitter Random.

SYNTHETIC ONLY, AND LABELLED AS SUCH. These assertions prove the SCHEDULE is what the plan
says. They are NOT measurements of the real watcher and this file deliberately contains no
`assert duty <= X` against a live system: `tests/forge-web/sampler-live.sh` (Step 64) produces
the real numbers, and the 1.94 s / 67 % figures belong to plan-review.md.
"""

from __future__ import annotations

from forge_web.sampler import (
    BACKOFF_LADDER_S,
    BUSY_WINDOW,
    COOLDOWN_CAP_S,
    COOLDOWN_FLOOR_S,
    DUTY_FACTOR,
    RECOVERY_SCANS,
    RETRY_WINDOWS_S,
    BoardOutcome,
    BoardSampler,
)
from conftest import SeqRandom


def sampler() -> BoardSampler:
    return BoardSampler(rng=SeqRandom())


def test_the_initial_ewma_is_absent_and_the_floor_applies_until_the_first_success():
    s = sampler()
    assert s.state.ewma is None
    assert s.cooldown_now() == COOLDOWN_FLOOR_S
    assert s.duty_projection() == 0.0, "duty is unknown, not zero-by-assumption, before data"


def test_the_first_success_seeds_the_ewma_rather_than_blending_a_fabricated_prior():
    s = sampler()
    s.record(BoardOutcome.SUCCESS, 2.0, now=0.0)
    assert s.state.ewma == 2.0
    s.record(BoardOutcome.SUCCESS, 4.0, now=100.0)
    assert abs(s.state.ewma - (0.3 * 4.0 + 0.7 * 2.0)) < 1e-9


def test_cooldown_is_nine_times_the_ewma_floored_at_five_and_capped_at_sixty():
    s = sampler()
    s.record(BoardOutcome.SUCCESS, 0.1, now=0.0)
    assert s.cooldown_now() == COOLDOWN_FLOOR_S                  # floor binds
    s.state.ewma = 1.94                                          # plan-review's SAMPLE value
    assert abs(s.cooldown_now() - DUTY_FACTOR * 1.94) < 1e-9     # ≈ 17.5 s, not 5 s
    s.state.ewma = 100.0
    assert s.cooldown_now() == COOLDOWN_CAP_S                    # cap binds


def test_the_ten_percent_duty_target_is_not_a_bound_once_the_cap_binds():
    """R14's honesty requirement, as an assertion rather than a comment."""
    s = sampler()
    s.state.ewma = 6.0
    assert s.duty_projection() <= 0.11
    s.state.ewma = 20.0
    assert s.duty_projection() > 0.20, "at a 20 s scan the 60 s cap makes duty ~25%"


def test_rc75_takes_two_short_jittered_retries_then_the_ladder():
    s = sampler()
    delays = [s.record(BoardOutcome.BUSY, 0.2, now=float(i)) for i in range(5)]
    assert delays[0] == RETRY_WINDOWS_S[0][0]
    assert delays[1] == RETRY_WINDOWS_S[1][0]
    assert abs(delays[2] - 0.85 * BACKOFF_LADDER_S[0]) < 1e-9
    assert abs(delays[3] - 0.85 * BACKOFF_LADDER_S[1]) < 1e-9
    assert abs(delays[4] - 0.85 * BACKOFF_LADDER_S[2]) < 1e-9


def test_jitter_stays_inside_the_declared_windows():
    import random

    s = BoardSampler(rng=random.Random(1234))
    for index in range(2):
        delay = s.record(BoardOutcome.BUSY, 0.1, now=float(index))
        low, high = RETRY_WINDOWS_S[index]
        assert low <= delay <= high
    for _ in range(6):
        delay = s.record(BoardOutcome.BUSY, 0.1, now=99.0)
        assert 0.85 * BACKOFF_LADDER_S[0] <= delay <= 1.15 * BACKOFF_LADDER_S[-1]


def test_the_busy_threshold_does_not_arm_until_the_window_is_full():
    s = sampler()
    for index in range(3):
        s.record(BoardOutcome.BUSY, 0.1, now=float(index))
    assert s.state.busy_rate == 1.0
    assert s.state.busy_threshold_armed is False
    assert s.state.busy_mode is False, "3 of 3 busy must not latch a 20-attempt rule"


def test_sustained_busy_over_a_full_window_disables_retry_bursts_and_doubles_the_cooldown():
    s = sampler()
    s.record(BoardOutcome.SUCCESS, 1.0, now=0.0)
    for index in range(BUSY_WINDOW):
        s.record(BoardOutcome.BUSY, 0.1, now=float(index))
    assert s.state.busy_threshold_armed and s.state.busy_mode
    before = s.state.busy_backoff_index
    s.record(BoardOutcome.BUSY, 0.1, now=100.0)
    assert s.state.busy_backoff_index == before + 1, "no short retry slot in busy mode"
    s.state.ewma = 1.0
    assert s.cooldown_now() == min(COOLDOWN_CAP_S, 2.0 * DUTY_FACTOR * 1.0)


def test_recovery_is_gradual_and_a_single_busy_attempt_resets_it():
    s = sampler()
    for index in range(BUSY_WINDOW):
        s.record(BoardOutcome.BUSY, 0.1, now=float(index))
    assert s.state.busy_mode
    # Clean scans fill the window back up; only once the window busy-rate is low do credits
    # start burning down.
    for index in range(BUSY_WINDOW):
        s.record(BoardOutcome.SUCCESS, 1.0, now=100.0 + index)
    assert s.state.busy_mode is False
    # And a busy attempt mid-recovery resets the credit counter to full.
    for index in range(BUSY_WINDOW):
        s.record(BoardOutcome.BUSY, 0.1, now=200.0 + index)
    for index in range(BUSY_WINDOW - RECOVERY_SCANS - 2):
        s.record(BoardOutcome.SUCCESS, 1.0, now=300.0 + index)
    s.record(BoardOutcome.BUSY, 0.1, now=400.0)
    assert s.state.recovery_credits == RECOVERY_SCANS


def test_invalid_output_and_timeout_are_not_contention():
    s = sampler()
    first = s.record(BoardOutcome.INVALID, 0.3, now=0.0)
    second = s.record(BoardOutcome.TIMEOUT, 30.0, now=10.0)
    assert abs(first - 0.85 * BACKOFF_LADDER_S[0]) < 1e-9
    assert abs(second - 0.85 * BACKOFF_LADDER_S[1]) < 1e-9
    assert s.state.retry_slot == 0, "they must not consume a contention retry slot"
    assert list(s.state.window) == [False, False], "and must not fake a busy rate"


def test_an_error_streak_resets_on_success():
    s = sampler()
    s.record(BoardOutcome.ERROR, 0.1, now=0.0)
    s.record(BoardOutcome.ERROR, 0.1, now=1.0)
    assert s.state.failure_streak == 2
    s.record(BoardOutcome.SUCCESS, 1.0, now=2.0)
    assert s.state.failure_streak == 0


def test_ack_pauses_board_starts_and_a_running_scan_is_not_interrupted():
    s = sampler()
    s.note_client_read(0.0)
    assert s.should_start(1.0)[0] is True
    s.ack_acquire()
    allowed, reason = s.should_start(1.0)
    assert allowed is False and "ack" in reason
    s.ack_release()
    assert s.should_start(1.0)[0] is True


def test_no_client_read_for_fifteen_seconds_suspends_scans_and_a_read_resumes_them():
    s = sampler()
    s.note_client_read(0.0)
    allowed, reason = s.should_start(16.0)
    assert allowed is False and "suspended" in reason and s.state.suspended
    s.note_client_read(16.0)
    assert s.should_start(16.0)[0] is True and s.state.suspended is False


def test_a_refresh_is_coalesced_and_cannot_bypass_the_cooldown():
    s = sampler()
    s.note_client_read(0.0)
    s.record(BoardOutcome.SUCCESS, 2.0, now=0.0)
    for _ in range(25):
        s.request_refresh(1.0)
    assert s.coalesced_requests == 25
    assert s.should_start(1.0)[0] is False, "25 tabs may not buy 25 scans"
    s.note_client_read(s.state.next_attempt_at)
    assert s.should_start(s.state.next_attempt_at + 0.01)[0] is True


def test_two_clients_share_one_schedule():
    s = sampler()
    s.note_client_read(0.0)
    s.record(BoardOutcome.SUCCESS, 2.0, now=0.0)
    first = s.state.next_attempt_at
    s.note_client_read(0.5)                       # a second tab reads
    assert s.state.next_attempt_at == first


def test_a_long_scan_does_not_stack_with_the_next_tick():
    s = sampler()
    s.note_client_read(0.0)
    s.started(0.0)
    delay = s.record(BoardOutcome.SUCCESS, 25.0, now=25.0)
    assert s.state.next_attempt_at == 25.0 + delay, "the cooldown starts when the scan ENDS"
    assert s.should_start(25.0 + delay - 0.01)[0] is False


def test_measured_duty_is_reported_not_assumed():
    s = sampler()
    s.note_client_read(0.0)
    s.started(0.0)
    s.record(BoardOutcome.SUCCESS, 2.0, now=2.0)
    assert abs(s.measured_duty(20.0) - 2.0 / 20.0) < 1e-9