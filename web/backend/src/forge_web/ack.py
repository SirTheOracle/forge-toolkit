"""Ack scope, the outcome classifier and the one bounded retry.

THE DEFECT THIS EXISTS FOR. `bin/forge-watch` takes flock(LOCK_EX|LOCK_NB) at MODULE SCOPE
(:317-320), BEFORE any mode dispatch. When the lock is held, `--board` exits 75 with a busy
payload (:331-352) but EVERY OTHER MODE — `ack` included — falls through to the bare
`sys.exit(0)` at :353 with EMPTY stdout and one stderr line. `MODE == 'ack'` dispatch is at
:2340, i.e. AFTER the lock. So rc 0 is not proof of application, and a UI that reads rc shows
a success toast for an ack that did nothing.

ONE RETURN SHAPE. `classify_ack` ALWAYS returns an `AckResult`; callers read `.outcome`. The
upstream documents disagreed with themselves about whether it returned an enum member or an
object, and the tests called `run_ack` with a signature the declaration did not have. Both
are fixed here and every test in this document calls exactly these signatures.

EXACTLY ONE BRANCH IS AUTO-RETRYABLE, because exactly one is POSITIVELY IDENTIFIED as
pre-mutation. In particular **rc 75 on the ack path is UNKNOWN, never busy**: 75 is the
BOARD's busy code (:352) and the ack path cannot produce it, so seeing it means something
unrecognised happened — and an unrecognised thing must not be retried against a mutating CLI.
"""

from __future__ import annotations

import asyncio
import enum
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from .models import AckScope, AckScopeRow
from .sampler import BoardSampler

SKIPPED_MARKER = "ack skipped — state.lock held"
NO_MATCH_MARKER = "no active conditions match"
SUMMARY_RE = re.compile(r"^Acknowledged (\d+) condition\(s\) for '.*'\.$", re.MULTILINE)
ACKED_RE = re.compile(r"^  acked: (\S+)  (.*)$", re.MULTILINE)

MAX_ATTEMPTS = 5
RETRY_START_DEADLINE_S = 15.0
RETRY_DELAYS_S = (0.5, 1.0, 2.0, 4.0, 4.0)
JITTER_LOW = 0.85
JITTER_HIGH = 1.15


class AckOutcome(str, enum.Enum):
    APPLIED = "applied"
    NO_MATCH = "no_match"
    NOT_APPLIED_BUSY = "not_applied_busy"          # the ONLY retryable outcome
    UNKNOWN = "unknown"
    CANCELLED_STALE_TARGET = "cancelled_stale_target"


@dataclass(frozen=True)
class AckCondition:
    condition: str
    message: str


@dataclass(frozen=True)
class AckResult:
    outcome: AckOutcome
    applied_count: int
    conditions: tuple[AckCondition, ...]
    raw_stdout: str
    raw_stderr: str
    rc: int
    completed: bool
    detail: str = ""


def classify_ack(rc: int, stdout: str, stderr: str, *, completed: bool) -> AckResult:
    """Determined by the COMPLETED CLI output, NEVER by rc alone.

    Retryable, and only this:
        completed and rc == 0 and stdout.strip() == "" and SKIPPED_MARKER in stderr
            -> NOT_APPLIED_BUSY
    Terminal for this attempt:
        rc == 0 and `  acked: ` lines AND the summary line     -> APPLIED
        rc == 0 and 'no active conditions match' in stdout     -> NO_MATCH
        not completed | acked lines without the summary |
        empty stdout WITHOUT the marker | rc 75 | anything else -> UNKNOWN
    UNKNOWN is never upgraded by a later inference.
    """
    stdout = stdout or ""
    stderr = stderr or ""
    conditions = tuple(AckCondition(c, m) for c, m in ACKED_RE.findall(stdout))
    summary = SUMMARY_RE.search(stdout)

    def build(outcome: AckOutcome, count: int, detail: str) -> AckResult:
        return AckResult(
            outcome=outcome, applied_count=count, conditions=conditions,
            raw_stdout=stdout, raw_stderr=stderr, rc=rc, completed=completed, detail=detail,
        )

    if not completed:
        return build(AckOutcome.UNKNOWN, 0,
                     "the ack process did not complete (timeout or interruption); whether it "
                     "applied anything is unknown and it will not be retried")
    if rc == 0 and stdout.strip() == "" and SKIPPED_MARKER in stderr:
        return build(AckOutcome.NOT_APPLIED_BUSY, 0,
                     "forge-watch refused before any mutation because state.lock was held "
                     "(bin/forge-watch:317-353); provably not applied")
    if rc != 0:
        # 75 lands here deliberately. It is the BOARD's busy code; on the ack path it is
        # unrecognised input, not a busy signal, and it is NOT retried.
        return build(AckOutcome.UNKNOWN, 0, "ack exited rc %d with unrecognised output" % rc)
    if conditions and summary:
        return build(AckOutcome.APPLIED, int(summary.group(1)), "")
    if conditions and not summary:
        return build(AckOutcome.UNKNOWN, 0,
                     "output was truncated: acked lines are present but the summary line is "
                     "missing, so the applied count cannot be trusted")
    if NO_MATCH_MARKER in stdout:
        return build(AckOutcome.NO_MATCH, 0, "")
    if stdout.strip() == "":
        return build(AckOutcome.UNKNOWN, 0,
                     "rc 0 with empty stdout and no skipped marker; not provably applied and "
                     "not provably refused")
    return build(AckOutcome.UNKNOWN, 0, "unrecognised ack output")


# ── scope ────────────────────────────────────────────────────────────────────


def _board_rows(board: dict[str, Any]) -> list[dict[str, Any]]:
    """The FULL unfiltered board: hot + active + parked + the COLLAPSED maintenance rows.

    The collapsed rows are the whole point of plan-review P2: they are hidden in the UI but
    `finding_matches` sees them, so an ack advertised as "this session" can quietly clear a
    maintenance condition the operator never saw.
    """
    rows: list[dict[str, Any]] = []
    for key in ("hot", "active", "parked"):
        rows.extend(r for r in (board.get(key) or []) if isinstance(r, dict))
    maintenance = board.get("maintenance") or {}
    if isinstance(maintenance, dict):
        rows.extend(r for r in (maintenance.get("rows") or []) if isinstance(r, dict))
    return rows


def finding_matches(row: dict[str, Any], target: str) -> bool:
    """The watcher's UNCHANGED OR matcher (bin/forge-watch:2335-2338). Do not widen it."""
    root = str(row.get("root") or "")
    fields = [row.get("session"), row.get("slug"), os.path.basename(root.rstrip("/")), root]
    return any(value and value == target for value in fields)


def ack_scope(board: dict[str, Any] | None, target: str, target_kind: str,
              fleet_roots: set[str]) -> AckScope:
    """What this ack would touch, computed from the full board.

    When the board source is UNAVAILABLE there is no scope to advertise, so ack is disabled:
    the operator clicks an advertised scope, and an unadvertised one is not a scope.
    """
    if not board or board.get("busy") or board.get("unavailable_reason"):
        return AckScope(
            target=target, target_kind=target_kind, available=False,
            unavailable_reason=str(
                (board or {}).get("message") or "the board source is unavailable"
            ),
        )
    visible: list[AckScopeRow] = []
    outside = 0
    for row in _board_rows(board):
        if not finding_matches(row, target):
            continue
        root = os.path.realpath(str(row.get("root") or ""))
        in_fleet = root in fleet_roots
        if not in_fleet:
            outside += 1
            continue
        visible.append(
            AckScopeRow(
                condition=str(row.get("condition") or ""),
                msg=str(row.get("msg") or ""),
                root=root,
                session=str(row.get("session") or "") or None,
                slug=str(row.get("slug") or "") or None,
                acked=bool(row.get("acked")),
                in_fleet=True,
            )
        )
    return AckScope(target=target, target_kind=target_kind, visible=visible,
                    outside_fleet_count=outside, available=True)


# ── the bounded retry ────────────────────────────────────────────────────────


class AckRunner(Protocol):
    async def run(self, target: str) -> tuple[int, str, str, bool]:
        """(rc, stdout, stderr, completed). `completed` is False on timeout or interruption."""


class Clock(Protocol):
    def now(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass(frozen=True)
class AckTarget:
    """Resolved SERVER-SIDE. The client picks a breadth, never a string."""

    value: str
    kind: str
    session_key: str | None = None


@dataclass
class AckReceipt:
    outcome: AckOutcome
    summary: str
    attempts: int
    applied_count: int = 0
    conditions: tuple[AckCondition, ...] = ()
    scope_retained: bool = True
    detail: str = ""
    results: list[AckResult] = field(default_factory=list)


async def run_ack(
    target: AckTarget,
    *,
    cli: AckRunner,
    sampler: BoardSampler,
    clock: Clock | None = None,
    target_alive: Callable[[], Awaitable[bool]] | None = None,
    rng: random.Random | None = None,
) -> AckReceipt:
    """Run one ack OPERATION — which may involve up to five attempts but is ONE operation.

    Rules, all from plan §7 and all asserted by tests:
      - only NOT_APPLIED_BUSY retries; every other outcome is terminal immediately;
      - at most MAX_ATTEMPTS total attempts;
      - a retry may only be STARTED within RETRY_START_DEADLINE_S of the first attempt. An
        already-running scan is NEVER interrupted to meet that deadline, so total wall time
        can exceed 15 s by design (a successful scan costs seconds). The deadline governs
        STARTS, not completion;
      - delays 0.5, 1, 2, 4, 4 s, each jittered inside a declared window;
      - sampler.ack_active is held for the WHOLE sequence, so board retries yield across all
        attempts, not per attempt;
      - the live target is revalidated before each attempt; a vanished target CANCELS as
        stale rather than retrying against something else;
      - operation identity, target and serialization ownership are kept ACROSS attempts.
    """
    clock = clock or RealClock()
    rng = rng or random.Random()
    sampler.ack_acquire()
    receipt = AckReceipt(outcome=AckOutcome.UNKNOWN, summary="", attempts=0)
    try:
        first_start: float | None = None
        for attempt in range(MAX_ATTEMPTS):
            if target_alive is not None and not await target_alive():
                receipt.outcome = AckOutcome.CANCELLED_STALE_TARGET
                receipt.summary = "Cancelled: the target is no longer live"
                receipt.detail = (
                    "The session this ack was scoped to vanished; acking a different target "
                    "would not be the operator's request."
                )
                return receipt
            start = clock.now()
            if first_start is None:
                first_start = start
            receipt.attempts = attempt + 1
            rc, out, err, completed = await cli.run(target.value)
            result = classify_ack(rc, out, err, completed=completed)
            receipt.results.append(result)
            if result.outcome is not AckOutcome.NOT_APPLIED_BUSY:
                receipt.outcome = result.outcome
                receipt.applied_count = result.applied_count
                receipt.conditions = result.conditions
                receipt.detail = result.detail
                receipt.summary = _summary_for(result)
                return receipt
            if attempt == MAX_ATTEMPTS - 1:
                break
            delay = rng.uniform(
                JITTER_LOW * RETRY_DELAYS_S[attempt], JITTER_HIGH * RETRY_DELAYS_S[attempt]
            )
            # The deadline is checked at the moment a retry would START. A scan that ran long
            # is not aborted, and its result is classified normally.
            if (clock.now() + delay) - first_start > RETRY_START_DEADLINE_S:
                break
            await clock.sleep(delay)
        receipt.outcome = AckOutcome.NOT_APPLIED_BUSY
        receipt.summary = "Not applied: watcher busy"
        receipt.detail = (
            "forge-watch refused before any mutation on every attempt. Nothing was "
            "acknowledged. The advertised scope is retained for an explicit retry."
        )
        receipt.scope_retained = True
        return receipt
    finally:
        sampler.ack_release()


def _summary_for(result: AckResult) -> str:
    if result.outcome is AckOutcome.APPLIED:
        return "Applied to %d condition(s)" % result.applied_count
    if result.outcome is AckOutcome.NO_MATCH:
        return "No active conditions matched"
    if result.outcome is AckOutcome.CANCELLED_STALE_TARGET:
        return "Cancelled: the target is no longer live"
    return "Not confirmed: the outcome could not be determined and was not retried"