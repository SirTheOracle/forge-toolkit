"""Fixed actions, their argv, and the operation lifecycle.

THE LOCK IS OWNED BY THE OPERATION, NOT THE REQUEST. Every mutation runs in a background
task that holds the per-session (or per-root) lock for the ENTIRE subprocess lifetime; the
HTTP handler returns a receipt promptly. A client disconnect or a request timeout releases
nothing — a hanging child keeps the lock and a second action stays blocked until it exits.
Nothing is killed, replayed, or released early.

AND THE LOCK IS NOT DEDUPLICATION (review R9). Serialising two identical submissions still
executes both. `handles.ReceiptStore` deduplicates; the lock orders. The two are combined
here in one specific order:

    reserve(request_id, body_sha)      # dedup, BEFORE anything else
      -> resolve handles              # refuse a stale selection early and cheaply
        -> acquire the operation lock
          -> REVALIDATE                <-- live identity AND object version, under the lock
            -> launch                  <-- immediately after, nothing in between

The revalidation step is deliberately INSIDE the lock and immediately before launch, because
anywhere earlier it is merely advisory: another operation holding the lock can change the
world while this one waits for it.

SESSION IDS ARE `$N`. `%N` is a PANE id.
"""

from __future__ import annotations

import asyncio
import datetime
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .ack import AckOutcome, AckReceipt, AckRunner, AckTarget, run_ack
from .config import Config, child_env, recheck_contract
from .handles import HandleTable, HandleError, ObjectRef, ReceiptStore, Reservation, canonical_body_sha
from .models import (
    OperationReceipt,
    OperationState,
    OutcomeComponents,
)
from .sampler import BoardSampler

#: Bounded receipt output. The child is still DRAINED to EOF past this — truncating the read
#: without draining would block a chatty child on a full pipe, which is the opposite of
#: "never kill a slow child".
OUTPUT_CAPTURE_MAX = 8 * 1024
DRAIN_MAX = 4 << 20
OPERATIONS_MAX = 256

PAUSE_TEXT = "forge-pause"
STOP_TEXT = "forge-stop"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ActionRefused(Exception):
    """Refused BEFORE any subprocess ran. `code` is a stable machine token."""

    def __init__(self, code: str, detail: str, current_object_key: str | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.current_object_key = current_object_key


@dataclass
class Plan:
    """A fully resolved, launchable operation. Nothing here came from a request body except
    validated text; every path, name, pane and executable was resolved server-side."""

    action: str
    argv: list[str]
    cwd: str
    lock_key: str
    lock_kind: str                                     # "session" | "root"
    session_name: str | None = None
    session_id: str | None = None
    session_created: str | None = None
    session_root: str | None = None
    object_ref: ObjectRef | None = None
    answers_ask_id: str | None = None
    #: Re-derives the object's CURRENT version under the lock. Returns (version, key).
    ack_visible: set[tuple[str, str]] = field(default_factory=set)
    revalidate_object: Callable[[], tuple[str, str]] | None = None


class CommandTable:
    """argv ARRAYS, never a shell string. cwd is always the selected physical root.
    force and billing overrides are never passed."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _expect(self, plan_root: str, sid: str, created: str) -> list[str]:
        return [
            "--expected-root", plan_root,
            "--expected-session-id", sid,          # "$N"
            "--expected-incarnation", created,
        ]

    def message(self, name: str, root: str, sid: str, created: str, text: str,
                answers: str | None) -> list[str]:
        argv = [str(self.cfg.exe.forge), "dispatch", "@%s" % name,
                "--sender", "seat", "--pointer"]
        argv += self._expect(root, sid, created)
        if answers:
            argv += ["--answers", answers]
        argv += ["--", text]
        return argv

    def control(self, name: str, root: str, sid: str, created: str, which: str) -> list[str]:
        """Pause/stop: the same dispatch with a FIXED literal phrase and NO --pointer."""
        argv = [str(self.cfg.exe.forge), "dispatch", "@%s" % name, "--sender", "seat"]
        argv += self._expect(root, sid, created)
        argv += ["--", PAUSE_TEXT if which == "pause" else STOP_TEXT]
        return argv

    def park(self, name: str, root: str, sid: str, created: str, *, slug: str, stage: str,
             reason: str, pending_ts: str, callback_id: str, callback_sha: str) -> list[str]:
        return [
            str(self.cfg.exe.forge_bridge),
            "--target-session", name, "--cross-session", "--target-root", root,
            "--expected-session-id", sid, "--expected-incarnation", created,
            "park", "--",
            "--slug", slug, "--stage", stage, "--reason", reason,
            "--expected-pending-ts", pending_ts,
            "--expected-callback-id", callback_id,
            "--expected-callback-sha256", callback_sha,
        ]

    def resolve(self, name: str, root: str, sid: str, created: str, *, slug: str, stage: str,
                note: str, pending_ts: str, callback_id: str, callback_sha: str) -> list[str]:
        """`none` is the documented sentinel for "the displayed object had no callback", and
        it is only legal on the --resolve path."""
        return [
            str(self.cfg.exe.forge_bridge),
            "--target-session", name, "--cross-session", "--target-root", root,
            "--expected-session-id", sid, "--expected-incarnation", created,
            "park", "--", "--resolve",
            "--slug", slug, "--stage", stage, "--note", note,
            "--expected-pending-ts", pending_ts,
            "--expected-callback-id", callback_id or "none",
            "--expected-callback-sha256", callback_sha or "none",
        ]

    def ack(self, target: str) -> list[str]:
        return [str(self.cfg.exe.forge_watch), "ack", target]

    def spawn(self, root: str, name: str | None) -> list[str]:
        argv = [str(self.cfg.exe.forge), "spawn", "--root", root]
        if name:
            argv += ["--name", name]
        return argv


# ── outcome parsing ──────────────────────────────────────────────────────────


def parse_dispatch(stdout: str, stderr: str, rc: int, answering: bool) -> OutcomeComponents:
    """Delivery and cleanup are INDEPENDENT components (plan §1).

    Step 18/20 make `bin/forge` print `cleanup=<consumed|noop|failed|not-applicable>` on its
    additive diagnostic line. Its absence is `unknown`, never `not-applicable`: an older
    binary that did not print it has told us nothing.
    """
    blob = "%s\n%s" % (stdout, stderr)
    delivered = "DISPATCHED @" in stdout
    cleanup: str | None = None
    for token in ("consumed", "noop", "failed", "not-applicable"):
        if "cleanup=%s" % token in blob:
            cleanup = token
            break
    if cleanup is None:
        cleanup = "not-applicable" if not answering else "unknown"
    return OutcomeComponents(
        recorded=True if (answering and ("archived" in blob or delivered)) else (delivered or None),
        delivered=delivered,
        cleanup=cleanup,                                  # type: ignore[arg-type]
        # ONLY a delivery failure offers plain redispatch. A cleanup failure preserves the
        # ask/stage identity and must never re-answer an archive.
        offers_redispatch=bool(answering and rc != 0 and not delivered),
    )


def parse_park(stdout: str, stderr: str) -> OutcomeComponents:
    blob = "%s\n%s" % (stdout, stderr)
    components = OutcomeComponents()
    if "PARK PARTIAL" in blob:
        components.park_result = "partial"
    elif "PARK NOOP" in blob:
        components.park_result = "noop"
    elif "PARK_RESOLVED" in blob or "PARK RESOLVED" in blob:
        components.park_result = "resolved"
    elif "PARKED " in blob:
        components.park_result = "parked"
    for line in blob.splitlines():
        if "lock_release_state=" in line:
            components.lock_release_state = line.split("lock_release_state=", 1)[1].split()[0]
            break
    if "FINALIZE_READY" in blob:
        components.finalize = "ready"
    elif "FINALIZE_DEFERRED" in blob:
        components.finalize = "deferred"
    return components


def parse_spawn(stdout: str, stderr: str) -> OutcomeComponents:
    blob = "%s\n%s" % (stdout, stderr)
    components = OutcomeComponents()
    if "SPAWNED @" in stdout:
        components.spawn_result = "spawned"
    elif "ENSURE @" in stdout:
        components.spawn_result = "ensured"
    elif stdout.startswith("PLAN ") or stdout.startswith("DRY-RUN "):
        components.spawn_result = "plan"
    if "on_spawn FAILED" in blob or "panes after populate" in blob or "stamp missing" in blob:
        components.spawn_result = "partial"
    return components


# ── the runner ───────────────────────────────────────────────────────────────


@dataclass
class _Live:
    task: asyncio.Task
    receipt: OperationReceipt


class SubprocessAckRunner:
    """Adapts `forge-watch ack` to the ack module's AckRunner protocol."""

    def __init__(self, runner: "ActionRunner", target_root: str) -> None:
        self._runner = runner
        self._root = target_root

    async def run(self, target: str) -> tuple[int, str, str, bool]:
        argv = self._runner.commands.ack(target)
        return await self._runner.execute(argv, self._root)


class ActionRunner:
    def __init__(
        self,
        cfg: Config,
        *,
        receipts: ReceiptStore,
        sampler: BoardSampler,
        live_identity: Callable[[str], Awaitable[dict[str, str] | None]],
        mutations_enabled: Callable[[], tuple[bool, str]],
    ) -> None:
        self.cfg = cfg
        self.commands = CommandTable(cfg)
        self.receipts = receipts
        self.sampler = sampler
        self._live_identity = live_identity
        self._mutations_enabled = mutations_enabled
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._root_locks: dict[str, asyncio.Lock] = {}
        self._operations: OrderedDict[str, OperationReceipt] = OrderedDict()
        self._live: dict[str, _Live] = {}
        self._contract = None

    # -- public API ----------------------------------------------------------

    def operation(self, operation_id: str) -> OperationReceipt:
        """Look up a receipt. A receipt from ANOTHER server instance is UNKNOWN, never a
        replay and never a 404-as-if-it-never-happened."""
        if not self.receipts.owns(operation_id):
            return OperationReceipt(
                operation_id=operation_id,
                server_instance=self.receipts.instance,
                request_id="00000000-0000-0000-0000-000000000000",
                action="unknown",
                state=OperationState.UNKNOWN,
                created_at=_now_iso(),
                summary="This operation belongs to a previous server process.",
                uncertainty=(
                    "The server restarted. Receipts are in memory only and are not replayed. "
                    "A child process from that run may have completed, may still be running, "
                    "or may never have started. Refresh the authoritative .dev/ state before "
                    "acting again."
                ),
            )
        receipt = self._operations.get(operation_id)
        if receipt is None:
            return OperationReceipt(
                operation_id=operation_id,
                server_instance=self.receipts.instance,
                request_id="00000000-0000-0000-0000-000000000000",
                action="unknown",
                state=OperationState.UNKNOWN,
                created_at=_now_iso(),
                summary="No such operation on this server.",
                uncertainty=self.receipts.eviction_note(),
            )
        return receipt

    async def submit(self, action: str, body: dict[str, Any], plan_factory: Callable[[], Plan],
                     session_key: str | None) -> OperationReceipt:
        """Reserve, refuse early, then launch in a background task and return promptly."""
        request_id = str(body["request_id"])
        body_sha = canonical_body_sha(body)
        kind, reservation = await self.receipts.reserve(request_id, body_sha, action, session_key)
        if kind in ("duplicate", "inflight"):
            if kind == "inflight":
                await reservation.ready.wait()
            existing = self._operations.get(reservation.operation_id or "")
            if existing is not None:
                clone = existing.model_copy(update={"duplicate": True})
                return clone
            raise ActionRefused(
                "duplicate_unresolved",
                "request_id %s was already accepted but its receipt is no longer retained; "
                "do not retry it blindly — refresh state first" % request_id,
            )

        try:
            enabled, detail = self._mutations_enabled()
            if not enabled:
                raise ActionRefused("mutations_disabled", detail)
            self._contract = await asyncio.to_thread(recheck_contract, self.cfg, self._contract)
            if not self._contract.ok:
                raise ActionRefused("contract_mismatch", self._contract.detail)
            plan = await asyncio.to_thread(plan_factory)
        except (ActionRefused, HandleError) as exc:
            await self.receipts.abandon(reservation)
            code = getattr(exc, "code", "refused")
            raise ActionRefused(code, getattr(exc, "detail", str(exc)),
                                getattr(exc, "current_object_key", None)) from exc
        except BaseException:
            # No process exists yet. Unexpected validation/cancellation must not
            # strand duplicate waiters behind an unpublished reservation.
            await self.receipts.abandon(reservation)
            raise

        operation_id = self.receipts.new_operation_id()
        receipt = OperationReceipt(
            operation_id=operation_id,
            server_instance=self.receipts.instance,
            request_id=request_id,
            session_key=session_key,
            action=action,
            state=OperationState.QUEUED,
            created_at=_now_iso(),
            summary="Queued; waiting for the %s lock." % plan.lock_kind,
            uncertainty=self.receipts.eviction_note(),
        )
        self._remember(operation_id, receipt)
        task = asyncio.create_task(self._owned_run(plan, receipt, reservation), name="forge-web-%s" % operation_id)
        self._live[operation_id] = _Live(task=task, receipt=receipt)
        await self.receipts.complete(reservation, operation_id)
        return receipt

    # -- internals -----------------------------------------------------------

    def _remember(self, operation_id: str, receipt: OperationReceipt) -> None:
        self._operations[operation_id] = receipt
        self._operations.move_to_end(operation_id)
        while len(self._operations) > OPERATIONS_MAX:
            victim = next((k for k, v in self._operations.items()
                           if v.state not in (OperationState.QUEUED, OperationState.RUNNING)), None)
            if victim is None:
                break
            self._operations.pop(victim)

    def _lock_for(self, plan: Plan) -> asyncio.Lock:
        table = self._session_locks if plan.lock_kind == "session" else self._root_locks
        return table.setdefault(plan.lock_key, asyncio.Lock())

    async def _owned_run(self, plan: Plan, receipt: OperationReceipt, reservation) -> None:
        # This owner outlives HTTP/caller cancellation. A child may already have mutated
        # .dev; neither cancellation nor a drain error is permission to release its lock.
        execution = asyncio.create_task(self._run(plan, receipt))
        try:
            while True:
                try:
                    await asyncio.shield(execution)
                    break
                except asyncio.CancelledError:
                    if execution.cancelled():
                        raise RuntimeError("mutation execution unexpectedly cancelled")
                    continue
        except OSError as exc:
            receipt.state = OperationState.REFUSED
            receipt.summary = "Child launch failed: %s" % exc
            receipt.components = OutcomeComponents(recorded=False, delivered=False)
        except Exception as exc:
            receipt.state = OperationState.UNKNOWN
            receipt.summary = "Execution/drain failed: %s" % exc
            receipt.uncertainty = "Outcome unconfirmed; do not replay. Refresh authoritative state."
        finally:
            receipt.finished_at = _now_iso()
            await self.receipts.finish(reservation)
            self._live.pop(receipt.operation_id, None)

    async def _run(self, plan: Plan, receipt: OperationReceipt) -> None:
        lock = self._lock_for(plan)
        async with lock:
            try:
                await self._revalidate(plan)
            except ActionRefused as exc:
                receipt.state = OperationState.REFUSED
                receipt.summary = "Refused before launch: %s" % exc.detail
                receipt.finished_at = _now_iso()
                receipt.components = OutcomeComponents(recorded=False, delivered=False)
                return
            receipt.state = OperationState.RUNNING
            receipt.summary = "Running %s" % plan.action
            if plan.action == "ack":
                await self._run_ack(plan, receipt)
                return
            rc, out, err, completed = await self.execute(plan.argv, plan.cwd)
            receipt.rc = rc
            receipt.stdout = out[:OUTPUT_CAPTURE_MAX]
            receipt.stderr = err[:OUTPUT_CAPTURE_MAX]
            receipt.truncated_output = (
                len(out) > OUTPUT_CAPTURE_MAX or len(err) > OUTPUT_CAPTURE_MAX
            )
            receipt.state = OperationState.EXITED if completed else OperationState.UNKNOWN
            receipt.finished_at = _now_iso()
            if plan.action in ("message", "answer", "pause", "stop"):
                receipt.components = parse_dispatch(out, err, rc, plan.action == "answer")
                receipt.summary = _dispatch_summary(plan.action, receipt.components, rc)
            elif plan.action in ("park", "resolve"):
                receipt.components = parse_park(out, err)
                receipt.summary = _park_summary(receipt.components, rc)
            elif plan.action == "spawn":
                receipt.components = parse_spawn(out, err)
                receipt.summary = _spawn_summary(receipt.components, rc)
            if not completed:
                receipt.uncertainty = (
                    "The child did not report completion; whether it applied anything is "
                    "unknown. Nothing was retried. Check .dev/ before acting again."
                )

    async def _revalidate(self, plan: Plan) -> None:
        """R9. Under the lock, immediately before launch.

        Two independent checks, because they fail for different reasons:
          1. LIVE IDENTITY — the session must still exist with the same name, tmux id,
             creation stamp and physical root. A session that died and was recreated in the
             same second has a different `$N`, which is why the id is part of this.
          2. OBJECT VERSION — the selected pending/ask must still have the version the
             operator was shown. A changed version yields the NEW handle so the UI can point
             at the object that now exists, rather than a bare 409.
        """
        self._contract = await asyncio.to_thread(recheck_contract, self.cfg, self._contract)
        if not self._contract.ok:
            raise ActionRefused("contract_mismatch", self._contract.detail)
        if plan.session_name is not None:
            live = await self._live_identity(plan.session_name)
            if live is None:
                raise ActionRefused("target_vanished",
                                    "session %s is no longer live" % plan.session_name)
            if (live.get("session_id") != plan.session_id
                    or live.get("created") != plan.session_created
                    or os.path.realpath(live.get("root", "")) != plan.session_root):
                raise ActionRefused(
                    "target_changed",
                    "session %s is live but its identity changed (id %s/%s, created %s/%s, "
                    "root %s/%s)" % (
                        plan.session_name, live.get("session_id"), plan.session_id,
                        live.get("created"), plan.session_created,
                        live.get("root"), plan.session_root,
                    ),
                )
        if plan.revalidate_object is not None and plan.object_ref is not None:
            version, key = await asyncio.to_thread(plan.revalidate_object)
            expected_key = HandleTable.compute_key(plan.object_ref.kind, plan.object_ref.session_key,
                                                    plan.object_ref.natural_id, plan.object_ref.version)
            if version != plan.object_ref.version or key != expected_key:
                raise ActionRefused(
                    "object_changed",
                    "the selected %s changed between display and launch; reselect it"
                    % plan.object_ref.kind,
                    current_object_key=key,
                )

    async def _run_ack(self, plan: Plan, receipt: OperationReceipt) -> None:
        target = AckTarget(value=plan.argv[-1], kind=plan.action, session_key=plan.lock_key)

        async def still_alive() -> bool:
            if plan.session_name is None:
                return True
            live = await self._live_identity(plan.session_name)
            return bool(live and live.get("session_id") == plan.session_id)

        result: AckReceipt = await run_ack(
            target,
            cli=SubprocessAckRunner(self, plan.cwd),
            sampler=self.sampler,
            target_alive=still_alive,
        )
        last = result.results[-1] if result.results else None
        receipt.rc = last.rc if last else None
        receipt.stdout = (last.raw_stdout if last else "")[:OUTPUT_CAPTURE_MAX]
        if last and last.conditions:
            visible = [condition for condition in last.conditions
                       if (condition.condition, condition.message) in plan.ack_visible]
            outside = len(last.conditions) - len(visible)
            receipt.stdout = "\n".join("  acked: %s  %s" % (condition.condition, condition.message)
                                       for condition in visible)
            if outside:
                receipt.stdout += "\n%d applied condition(s) outside the visible fleet (details aggregated)" % outside
            receipt.stdout += "\nApplied count: %d" % last.applied_count
        receipt.stderr = (last.raw_stderr if last else "")[:OUTPUT_CAPTURE_MAX]
        receipt.state = OperationState.EXITED
        receipt.finished_at = _now_iso()
        receipt.summary = result.summary
        receipt.components = OutcomeComponents(
            ack_outcome=result.outcome.value,
            ack_applied_count=result.applied_count if result.outcome is AckOutcome.APPLIED else None,
        )
        if result.outcome is AckOutcome.UNKNOWN:
            receipt.uncertainty = result.detail

    async def execute(self, argv: list[str], cwd: str) -> tuple[int, str, str, bool]:
        """Run one child to completion. NEVER killed, NEVER on a timeout.

        Output is DRAINED to EOF up to DRAIN_MAX so a chatty child cannot block on a full
        pipe; only the first OUTPUT_CAPTURE_MAX bytes are retained for the receipt.
        """
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=child_env(self.cfg),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def drain(stream) -> str:
            chunks: list[bytes] = []
            total = 0
            while True:
                block = await stream.read(65536)
                if not block:
                    break
                total += len(block)
                if total <= DRAIN_MAX:
                    chunks.append(block)
            return b"".join(chunks).decode("utf-8", "replace")

        results = await asyncio.gather(drain(proc.stdout), drain(proc.stderr), return_exceptions=True)
        # Always observe child exit before returning or raising to the lock owner. A
        # failed reader may leave a slow child alive; that operation remains serialized.
        rc = await proc.wait()
        errors = [value for value in results if isinstance(value, BaseException)]
        if errors:
            raise RuntimeError("child output capture failed after exit: %s" % errors[0])
        return rc, results[0], results[1], True


def _dispatch_summary(action: str, components: OutcomeComponents, rc: int) -> str:
    if action in ("pause", "stop"):
        verb = "Pause" if action == "pause" else "Stop"
        if components.delivered:
            return ("%s REQUESTED and delivered to pane 0. This does not kill tmux or any "
                    "running worker." % verb)
        return "%s was NOT delivered (rc %d)." % (verb, rc)
    if components.delivered and components.cleanup == "failed":
        return ("Answer delivered; callback cleanup FAILED. The ask and stage identity are "
                "preserved — reconcile the callback from pane 0. Do not re-answer the archive.")
    if components.delivered:
        return "Delivered to pane 0 (cleanup=%s)." % components.cleanup
    if components.offers_redispatch:
        return ("Answer recorded, delivery FAILED. Re-deliver with a plain message (not an "
                "answer): the ask is already archived.")
    return "Not delivered (rc %d)." % rc


def _park_summary(components: OutcomeComponents, rc: int) -> str:
    mapping = {
        "parked": "Parked. Lock release: %s." % (components.lock_release_state or "unknown"),
        "partial": ("Parked record stands, but the infra lock was NOT released "
                    "(lock_release_state=%s) — release it manually."
                    % (components.lock_release_state or "unknown")),
        "noop": "Nothing to park: no matching open callback for the selected entry.",
        "resolved": "Resolved.",
    }
    base = mapping.get(components.park_result or "", "Exited rc %d." % rc)
    if components.finalize == "deferred":
        base += " Finalize DEFERRED — the terminal cleanup write failed; retry finalize."
    elif components.finalize == "ready":
        base += " Finalize READY."
    return base


def _spawn_summary(components: OutcomeComponents, rc: int) -> str:
    mapping = {
        "spawned": "Spawned.",
        "ensured": "Already active at that root — no-op (ensure semantics).",
        "partial": ("Session is ALIVE but population was incomplete; it is recorded to the "
                    "board. No worktree was created implicitly."),
        "plan": "Dry-run plan only; nothing was created.",
    }
    return mapping.get(components.spawn_result or "", "Exited rc %d." % rc)