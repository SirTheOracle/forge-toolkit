"""The shared adaptive board sampler — a COMPLETE deterministic state machine.

Review R14 found the previous specification left four decisions as prose. All four are
resolved here, in code, with the reason attached:

  INITIAL EWMA. `ewma is None` until the first SUCCESS. While it is None the cooldown is the
  floor (5 s), because guessing a scan cost we have not measured is how you get either a
  needlessly idle sampler or one that hammers the lock on startup. The first success SEEDS
  the EWMA with its own duration (no blending against a fabricated prior); every later
  success blends with EWMA_ALPHA.

  WHEN THE 20-ATTEMPT BUSY THRESHOLD STARTS APPLYING. Only once the window is FULL
  (len(window) == BUSY_WINDOW). With three attempts recorded, "two of three are busy" is 67 %
  and means nothing. Before the window fills, `busy_rate` is still reported — it is a real
  observation — but it cannot latch busy mode.

  INVALID-OUTPUT AND TIMEOUT RETRY SCHEDULING. These are NOT busy. rc 75 is a positively
  identified contention signal with a documented payload; unparseable stdout or a killed scan
  is an unexplained failure and must not consume the two short retry slots reserved for
  contention. They go straight to the backoff ladder, indexed by their own consecutive-failure
  count, and they are recorded in the busy window as NOT-busy so they cannot fake contention.

  WHAT "RECOVERING GRADUALLY" MEANS. Busy mode latches when a FULL window is >50 % busy. It
  clears only after RECOVERY_SCANS consecutive successful scans each observed with a window
  busy-rate at or below RECOVERY_BUSY_RATE. Any busy attempt during recovery resets the
  credit counter to full. So recovery is three clean scans, not one lucky one.

AND THE HONEST LIMIT. `cooldown = min(CAP, max(FLOOR, DUTY_FACTOR * ewma))` targets ~10 %
occupancy of the watcher's lock ONLY while DUTY_FACTOR * ewma <= CAP, i.e. while a scan costs
at most CAP/DUTY_FACTOR = 6.67 s. Beyond that the cap binds and duty RISES: at a 20 s scan the
steady-state duty is 20/(20+60) = 25 %, not 10 %. The 10 % figure is a target, not a bound,
and `duty_projection()` reports the real number so the UI and the experiment can show it.
MAX_SCAN_S bounds a single scan so an indefinitely wedged one cannot occupy the lock forever.

The review's 1.94 s / 67 % sample belongs to `plan-review.md`. Nothing here re-presents it as
a measurement; `tests/forge-web/sampler-live.sh` is where real numbers come from.
"""

from __future__ import annotations

import enum
import random
from collections import deque
from dataclasses import dataclass, field

COOLDOWN_FLOOR_S = 5.0
DUTY_FACTOR = 9.0
COOLDOWN_CAP_S = 60.0
EWMA_ALPHA = 0.3
MAX_SCAN_S = 30.0

RETRY_WINDOWS_S = ((0.25, 0.75), (0.75, 1.5))
BACKOFF_LADDER_S = (5.0, 10.0, 20.0, 40.0, 60.0)
JITTER_LOW = 0.85
JITTER_HIGH = 1.15

BUSY_WINDOW = 20
BUSY_DISABLE_RETRY_AT = 0.50
RECOVERY_SCANS = 3
RECOVERY_BUSY_RATE = 0.25
BUSY_COOLDOWN_MULTIPLIER = 2.0

CLIENT_LEASE_S = 15.0


class BoardOutcome(str, enum.Enum):
    SUCCESS = "success"
    BUSY = "busy"                 # rc 75 with the cc-board/1 busy payload
    INVALID = "invalid"           # rc 0 but the body is not parseable cc-board/1
    TIMEOUT = "timeout"           # the scan exceeded MAX_SCAN_S and was killed
    ERROR = "error"               # any other nonzero rc


@dataclass
class SamplerState:
    """Every field a test needs to assert. Nothing is hidden behind a closure."""

    ewma: float | None = None
    window: deque[bool] = field(default_factory=lambda: deque(maxlen=BUSY_WINDOW))
    busy_mode: bool = False
    recovery_credits: int = 0
    retry_slot: int = 0
    busy_backoff_index: int = 0
    failure_streak: int = 0
    last_success_at: float | None = None
    last_attempt_at: float | None = None
    next_attempt_at: float = 0.0
    total_scan_seconds: float = 0.0
    total_success_scans: int = 0
    first_attempt_at: float | None = None
    suspended: bool = False

    @property
    def busy_rate(self) -> float:
        if not self.window:
            return 0.0
        return sum(1 for busy in self.window if busy) / len(self.window)

    @property
    def busy_threshold_armed(self) -> bool:
        """The >50 % rule applies only over a FULL window."""
        return len(self.window) == BUSY_WINDOW


class BoardSampler:
    """Pure scheduling. It never performs I/O; the collector drives it.

    Splitting the state machine from the subprocess is what makes R14's "deterministic clock"
    assertions real rather than a simulation of a simulation: the SAME object that schedules
    production is the object under test.
    """

    def __init__(self, *, rng: random.Random | None = None, clock=None) -> None:
        self.state = SamplerState()
        self._rng = rng or random.Random()
        self._clock = clock
        self._ack_holders = 0
        self._last_client_read: float | None = None
        self._coalesced = 0

    # ── client lease and ack priority ────────────────────────────────────────

    def note_client_read(self, now: float) -> None:
        """A browser read is a lease. 15 s without one suspends scanning; the next read
        resumes through THIS scheduler, so a refresh cannot bypass the cooldown."""
        self._last_client_read = now
        if self.state.suspended:
            self.state.suspended = False
            self.state.next_attempt_at = max(self.state.next_attempt_at, now)

    def request_refresh(self, now: float) -> None:
        """Coalesce an explicit refresh. It does NOT move next_attempt_at earlier — the
        budget is the point — but it does count, so the UI can say a refresh is pending."""
        self._coalesced += 1
        self.note_client_read(now)

    @property
    def coalesced_requests(self) -> int:
        return self._coalesced

    def ack_acquire(self) -> None:
        """Held for a WHOLE ack sequence, across every retry — not per attempt."""
        self._ack_holders += 1

    def ack_release(self) -> None:
        self._ack_holders = max(0, self._ack_holders - 1)

    @property
    def ack_active(self) -> bool:
        return self._ack_holders > 0

    # ── the decision ─────────────────────────────────────────────────────────

    def should_start(self, now: float) -> tuple[bool, str]:
        """May a scan START now? A scan already running is never interrupted by this."""
        if self.ack_active:
            return False, "an operator ack is waiting or executing; board retries yield to it"
        if self._last_client_read is None:
            return False, "no client has read yet"
        if now - self._last_client_read > CLIENT_LEASE_S:
            self.state.suspended = True
            return False, "no client read for %.0fs; board scans suspended" % CLIENT_LEASE_S
        if now < self.state.next_attempt_at:
            return False, "cooling down for another %.2fs" % (self.state.next_attempt_at - now)
        return True, ""

    def started(self, now: float) -> None:
        self.state.last_attempt_at = now
        if self.state.first_attempt_at is None:
            self.state.first_attempt_at = now
        self._coalesced = 0

    def record(self, outcome: BoardOutcome, duration_s: float, now: float) -> float:
        """Record a COMPLETED attempt and return the delay until the next one.

        Also sets state.next_attempt_at, so the driver never has to add the two itself.
        """
        state = self.state
        state.window.append(outcome is BoardOutcome.BUSY)
        state.total_scan_seconds += max(0.0, duration_s)

        if outcome is BoardOutcome.SUCCESS:
            state.total_success_scans += 1
            state.last_success_at = now
            state.failure_streak = 0
            state.retry_slot = 0
            state.busy_backoff_index = 0
            state.ewma = duration_s if state.ewma is None else (
                EWMA_ALPHA * duration_s + (1.0 - EWMA_ALPHA) * state.ewma
            )
            self._update_busy_mode_on_success()
            base = COOLDOWN_FLOOR_S if state.ewma is None else max(
                COOLDOWN_FLOOR_S, DUTY_FACTOR * state.ewma
            )
            if state.busy_mode:
                base *= BUSY_COOLDOWN_MULTIPLIER
            delay = min(COOLDOWN_CAP_S, base)
        elif outcome is BoardOutcome.BUSY:
            self._update_busy_mode_on_busy()
            if not state.busy_mode and state.retry_slot < len(RETRY_WINDOWS_S):
                low, high = RETRY_WINDOWS_S[state.retry_slot]
                state.retry_slot += 1
                delay = self._rng.uniform(low, high)
            else:
                index = min(state.busy_backoff_index, len(BACKOFF_LADDER_S) - 1)
                delay = self._jitter(BACKOFF_LADDER_S[index])
                state.busy_backoff_index += 1
                state.retry_slot = len(RETRY_WINDOWS_S)
        else:
            # INVALID / TIMEOUT / ERROR. NOT contention: they never consume a short retry
            # slot and they were recorded in the window as not-busy above, so they cannot
            # fabricate a busy rate.
            state.failure_streak += 1
            state.retry_slot = 0
            index = min(state.failure_streak - 1, len(BACKOFF_LADDER_S) - 1)
            delay = self._jitter(BACKOFF_LADDER_S[index])

        state.next_attempt_at = now + delay
        return delay

    def _jitter(self, value: float) -> float:
        return self._rng.uniform(JITTER_LOW * value, JITTER_HIGH * value)

    def _update_busy_mode_on_busy(self) -> None:
        state = self.state
        if state.busy_threshold_armed and state.busy_rate > BUSY_DISABLE_RETRY_AT:
            state.busy_mode = True
            state.recovery_credits = RECOVERY_SCANS
        elif state.busy_mode:
            state.recovery_credits = RECOVERY_SCANS     # any busy attempt resets recovery

    def _update_busy_mode_on_success(self) -> None:
        state = self.state
        if not state.busy_mode:
            return
        if state.busy_rate <= RECOVERY_BUSY_RATE:
            state.recovery_credits -= 1
            if state.recovery_credits <= 0:
                state.busy_mode = False
                state.recovery_credits = 0
        else:
            state.recovery_credits = RECOVERY_SCANS

    # ── reporting ────────────────────────────────────────────────────────────

    def cooldown_now(self) -> float:
        state = self.state
        base = COOLDOWN_FLOOR_S if state.ewma is None else max(
            COOLDOWN_FLOOR_S, DUTY_FACTOR * state.ewma
        )
        if state.busy_mode:
            base *= BUSY_COOLDOWN_MULTIPLIER
        return min(COOLDOWN_CAP_S, base)

    def duty_projection(self) -> float:
        """Steady-state fraction of wall time this sampler expects to hold the watcher lock,
        from the CURRENT EWMA and cooldown. Reported, never asserted away: once the 60 s cap
        binds, this exceeds 0.10 by design and the UI should show it."""
        state = self.state
        if state.ewma is None:
            return 0.0
        return state.ewma / (state.ewma + self.cooldown_now())

    def measured_duty(self, now: float) -> float:
        """Observed occupancy: successful+failed scan seconds over elapsed wall time."""
        if self.state.first_attempt_at is None:
            return 0.0
        elapsed = max(1e-9, now - self.state.first_attempt_at)
        return self.state.total_scan_seconds / elapsed