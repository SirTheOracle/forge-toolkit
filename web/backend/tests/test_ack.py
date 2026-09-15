"""The ack classifier and the one bounded retry. Byte-exact fixtures from bin/forge-watch."""

from __future__ import annotations

import pytest

from forge_web.ack import (
    AckOutcome,
    AckTarget,
    ack_scope,
    classify_ack,
    finding_matches,
    run_ack,
)
from forge_web.sampler import BoardSampler

# bin/forge-watch:335 — f"forge-watch: {MODE} skipped — state.lock held by another run{held}\n"
SKIPPED = "forge-watch: ack skipped — state.lock held by another run for ~1s\n"
# bin/forge-watch:2347,2353 — the two print() forms, byte for byte.
APPLIED = ("  acked: ITEM-BLOCKED  proj · codex-a — blocked\n"
           "  acked: NEEDS-ASK  proj · claude — asked\n"
           "Acknowledged 2 condition(s) for 'forge-x'.\n")
NO_MATCH = "(no active conditions match 'forge-x')\n"


def test_rc0_empty_stdout_with_the_marker_is_provably_not_applied():
    result = classify_ack(0, "", SKIPPED, completed=True)
    assert result.outcome is AckOutcome.NOT_APPLIED_BUSY
    assert result.applied_count == 0


def test_rc0_empty_stdout_without_the_marker_is_unknown_and_never_retried():
    assert classify_ack(0, "", "", completed=True).outcome is AckOutcome.UNKNOWN
    assert classify_ack(0, "", "other warning\n", completed=True).outcome is AckOutcome.UNKNOWN


def test_applied_reads_the_cli_output_not_the_preview():
    result = classify_ack(0, APPLIED, "", completed=True)
    assert result.outcome is AckOutcome.APPLIED
    assert result.applied_count == 2
    assert [c.condition for c in result.conditions] == ["ITEM-BLOCKED", "NEEDS-ASK"]


def test_no_match_is_distinct_from_skipped_and_from_applied():
    assert classify_ack(0, NO_MATCH, "", completed=True).outcome is AckOutcome.NO_MATCH


@pytest.mark.parametrize(
    "rc,out,err,completed",
    [
        (1, "", "", True),                                    # nonzero rc
        (0, "  acked: X  m\n", "", True),                     # truncated: no summary line
        (0, "", SKIPPED, False),                              # timed out / interrupted
        (0, "something unrecognised\n", "", True),
        (75, "", "", True),                                   # the BOARD's busy code, on ack
        (75, "", SKIPPED, True),                              # even WITH the marker: rc is wrong
    ],
)
def test_never_retried_cases_all_classify_unknown(rc, out, err, completed):
    assert classify_ack(rc, out, err, completed=completed).outcome is AckOutcome.UNKNOWN


def test_classify_always_returns_one_shape():
    for args in ((0, APPLIED, ""), (0, "", SKIPPED), (3, "", "boom")):
        result = classify_ack(*args, completed=True)
        assert hasattr(result, "outcome") and hasattr(result, "raw_stdout")


async def test_retry_only_the_proven_case_and_stop_at_five(fake_clock, recording_cli):
    recording_cli.queue([(0, "", SKIPPED)] * 8)
    sampler = BoardSampler()
    receipt = await run_ack(AckTarget("forge-x", "session"), cli=recording_cli,
                            sampler=sampler, clock=fake_clock)
    assert recording_cli.calls == 5
    assert receipt.outcome is AckOutcome.NOT_APPLIED_BUSY
    assert receipt.summary == "Not applied: watcher busy"
    assert receipt.scope_retained is True


async def test_an_unknown_outcome_terminates_immediately(fake_clock, recording_cli):
    recording_cli.queue([(75, "", ""), (0, APPLIED, "")])
    receipt = await run_ack(AckTarget("forge-x", "session"), cli=recording_cli,
                            sampler=BoardSampler(), clock=fake_clock)
    assert receipt.outcome is AckOutcome.UNKNOWN and recording_cli.calls == 1


async def test_the_deadline_gates_starts_and_never_interrupts_a_running_scan(fake_clock, recording_cli):
    """A scan already in flight runs to completion even if it finishes past t=15 s, and its
    result is classified normally. Total wall time exceeding the deadline is BY DESIGN."""
    recording_cli.queue([(0, "", SKIPPED), (0, APPLIED, "")], durations=[1.9, 40.0])
    start = fake_clock.now()
    receipt = await run_ack(AckTarget("forge-x", "session"), cli=recording_cli,
                            sampler=BoardSampler(), clock=fake_clock)
    assert receipt.outcome is AckOutcome.APPLIED
    assert fake_clock.now() - start > 15.0


async def test_a_vanished_target_cancels_rather_than_retargeting(fake_clock, recording_cli):
    async def dead():
        return False

    receipt = await run_ack(AckTarget("forge-gone", "session"), cli=recording_cli,
                            sampler=BoardSampler(), clock=fake_clock, target_alive=dead)
    assert receipt.outcome is AckOutcome.CANCELLED_STALE_TARGET
    assert recording_cli.calls == 0


async def test_board_retries_yield_for_the_whole_ack_sequence(fake_clock, recording_cli):
    """ack_active is held across ALL attempts, not per attempt."""
    seen: list[bool] = []
    sampler = BoardSampler()
    original = recording_cli.run

    async def run(target):
        seen.append(sampler.ack_active)
        return await original(target)

    recording_cli.run = run                                   # type: ignore[method-assign]
    recording_cli.queue([(0, "", SKIPPED)] * 5)
    await run_ack(AckTarget("forge-x", "session"), cli=recording_cli, sampler=sampler,
                  clock=fake_clock)
    assert seen == [True] * 5
    assert sampler.ack_active is False                        # released exactly once, at the end
    assert sampler.should_start(0.0)[0] is False              # (still gated: no client read)


# ── scope ────────────────────────────────────────────────────────────────────

BOARD = {
    "schema": "cc-board/1",
    "hot": [{"condition": "NEEDS-ASK", "class": "hot", "session": "forge-x",
             "root": "/p/a", "slug": "s1", "msg": "asked", "acked": False}],
    "active": [{"condition": "SESSION-WORKING", "class": "active", "session": "forge-x",
                "root": "/p/a", "slug": "s1", "msg": "working", "acked": False}],
    "parked": [],
    "maintenance": {"collapsed": True, "count": 2, "rows": [
        {"condition": "ZOMBIE-ACTIVE", "class": "maintenance", "session": "forge-x",
         "root": "/p/a", "msg": "old context", "acked": False},
        {"condition": "ZOMBIE-ACTIVE", "class": "maintenance", "session": "forge-x",
         "root": "/elsewhere", "msg": "outside the fleet", "acked": False},
    ]},
}


def test_the_matcher_is_the_watchers_unchanged_or():
    row = {"session": "forge-x", "slug": "s1", "root": "/p/a"}
    for target in ("forge-x", "s1", "a", "/p/a"):
        assert finding_matches(row, target)
    assert not finding_matches(row, "forge-y")


def test_scope_includes_hidden_maintenance_and_aggregates_outside_fleet():
    scope = ack_scope(BOARD, "forge-x", "session", {"/p/a"})
    conditions = [row.condition for row in scope.visible]
    assert "ZOMBIE-ACTIVE" in conditions, "the collapsed maintenance rows are in scope"
    assert scope.outside_fleet_count == 1
    assert "higher or lower" in scope.estimate_note        # never "up to N"


def test_ack_is_disabled_when_the_board_source_is_unavailable():
    busy = {"schema": "cc-board/1", "busy": True, "unavailable_reason": "state_lock_busy",
            "message": "state.lock held"}
    scope = ack_scope(busy, "forge-x", "session", {"/p/a"})
    assert scope.available is False and scope.visible == []
    assert ack_scope(None, "forge-x", "session", set()).available is False


def test_ack_scope_never_includes_asks_or_parks_as_resolvable():
    """Ack marks conditions acked (bin/forge-watch:2344-2348); it does not touch the ask
    record or the park lifecycle. The scope is board rows and nothing else."""
    scope = ack_scope(BOARD, "forge-x", "session", {"/p/a"})
    assert all(hasattr(row, "condition") for row in scope.visible)
    assert not hasattr(scope, "asks")