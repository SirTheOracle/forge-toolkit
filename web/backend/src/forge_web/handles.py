"""Request identity, bounded receipts, and the snapshot handle table.

THE PROBLEM THIS SOLVES (review R9). A per-session lock SERIALISES two submissions; it does
not DEDUPLICATE them. Two copies of the same message request — a double click, a retried
fetch after a dropped response — execute one after the other and the operator's pane
receives the instruction twice. The lock is not the wrong mechanism; it is answering a
different question.

FOUR DECISIONS, each with its failure mode named:

1. HANDLES ARE DETERMINISTIC AND VERSION-ENCODED, NOT PER-SNAPSHOT TOKENS.
   key = prefix + sha256(kind | session_key | natural_id | version)[:32]. An object that has
   not changed gets the SAME key on every 2 s poll, so a selection made at t=0 is still valid
   at t=120. An object that HAS changed gets a DIFFERENT key, so a stale key can never
   silently resolve to the newer object. This is the direct answer to "the two-second polling
   cycle may invalidate a user's selected object or silently resolve a stale handle
   differently": it can do neither.

2. THE TABLE'S LIFETIME IS INDEPENDENT OF THE POLL. An entry lives HANDLE_TTL_S (900 s) after
   the LAST issue or the LAST resolve, not until the next snapshot. Re-issuing the same key
   on the next poll refreshes it; an object disappearing from the fleet starts a 15-minute
   clock, during which the handle still resolves — to its RECORDED identity, which
   revalidation then rejects with a precise reason instead of a 404.

3. RECEIPTS ARE CONTENT-ADDRESSED AND RESERVED BEFORE LAUNCH. Same request_id + same body ->
   the SAME receipt, no second launch. Same request_id + DIFFERENT body -> 409, because that
   is a client bug and guessing which one the operator meant is worse than refusing. The
   reservation happens under the store lock BEFORE the operation lock is taken, so two
   concurrent POSTs cannot both reach launch.

4. RESTART IS UNCERTAIN AND SAYS SO. Operation ids are prefixed with a per-process instance
   id. A receipt from another instance is 410 server_restarted with outcome UNKNOWN. There
   is no durable queue and nothing is ever replayed. An id evicted by the bound is treated as
   NEW and its receipt carries an explicit "may re-execute" note — an honest bound beats a
   silent one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

HANDLE_TTL_S = 900.0
HANDLE_MAX = 4096
RECEIPT_TTL_S = 1800.0
RECEIPT_MAX = 512


class HandleError(Exception):
    """An unresolvable or expired handle."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ReceiptConflict(Exception):
    """Same request_id, different body."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def canonical_body_sha(body: dict[str, Any]) -> str:
    """SHA-256 over the request body with `request_id` removed.

    `revision` IS included: submitting the same text against a different displayed revision
    is a different intent, and reusing the first receipt would hide that the operator acted
    on a view they had already replaced.
    """
    payload = {k: v for k, v in body.items() if k != "request_id"}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ObjectRef:
    """What a handle resolves to. `version` is the object's content version; `payload`
    carries exactly the fields revalidation needs, and nothing a client could influence."""

    kind: str
    session_key: str | None
    natural_id: str
    version: str
    payload: dict[str, str]


@dataclass
class _Entry:
    ref: ObjectRef
    touched: float


class HandleTable:
    """Deterministic, version-encoding, poll-independent."""

    def __init__(self, *, ttl_s: float = HANDLE_TTL_S, maxsize: int = HANDLE_MAX,
                 clock=time.monotonic) -> None:
        self._ttl = ttl_s
        self._max = maxsize
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self.evicted = 0
        self.expired = 0

    @staticmethod
    def compute_key(kind: str, session_key: str | None, natural_id: str, version: str) -> str:
        prefix = "s" if kind == "session" else "o"
        blob = "\x00".join((kind, session_key or "", natural_id, version))
        return "%s-%s" % (prefix, hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32])

    def issue(self, kind: str, session_key: str | None, natural_id: str, version: str,
              payload: dict[str, str] | None = None) -> str:
        """Register (or refresh) a handle and return its key. Idempotent for an unchanged
        object: the same inputs always produce the same key."""
        key = self.compute_key(kind, session_key, natural_id, version)
        ref = ObjectRef(kind=kind, session_key=session_key, natural_id=natural_id,
                        version=version, payload=dict(payload or {}))
        now = self._clock()
        self._entries[key] = _Entry(ref=ref, touched=now)
        self._entries.move_to_end(key)
        self._sweep(now)
        return key

    def resolve(self, key: str, *, expect_kind: str | None = None) -> ObjectRef:
        now = self._clock()
        self._sweep(now)
        entry = self._entries.get(key)
        if entry is None:
            raise HandleError(
                "unknown_object",
                "handle %s is unknown to this server instance (it may have expired after "
                "%.0f minutes, or the server restarted); reselect the object" % (key, self._ttl / 60),
            )
        if expect_kind is not None and entry.ref.kind != expect_kind:
            raise HandleError(
                "wrong_object_kind",
                "handle %s refers to a %s, not a %s" % (key, entry.ref.kind, expect_kind),
            )
        entry.touched = now                      # a resolve refreshes, like an issue
        self._entries.move_to_end(key)
        return entry.ref

    def _sweep(self, now: float) -> None:
        cutoff = now - self._ttl
        while self._entries:
            key, entry = next(iter(self._entries.items()))
            if entry.touched >= cutoff:
                break
            self._entries.pop(key)
            self.expired += 1
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)
            self.evicted += 1

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class Reservation:
    """One in-flight or completed request_id."""

    request_id: str
    body_sha: str
    action: str
    session_key: str | None
    created_at: float
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    operation_id: str | None = None
    finished: bool = False
    #: Set when the reservation was created after an eviction of the same id.
    reused_after_eviction: bool = False


class ReceiptStore:
    """Bounded, TTL'd, instance-scoped request-id receipts.

    NOT DURABLE, DELIBERATELY. The plan forbids a durable web queue; a restart therefore
    loses receipts and may leave surviving children. `instance` makes that visible instead of
    letting a stale client id look valid.
    """

    def __init__(self, *, ttl_s: float = RECEIPT_TTL_S, maxsize: int = RECEIPT_MAX,
                 clock=time.monotonic, instance: str | None = None) -> None:
        self._ttl = ttl_s
        self._max = maxsize
        self._clock = clock
        self._lock = asyncio.Lock()
        self._slots: OrderedDict[str, Reservation] = OrderedDict()
        self._seq = 0
        self.instance = instance or uuid.uuid4().hex[:16]
        self.evicted = 0

    def new_operation_id(self) -> str:
        self._seq += 1
        return "%s:%06d" % (self.instance, self._seq)

    def owns(self, operation_id: str) -> bool:
        return operation_id.startswith(self.instance + ":")

    async def reserve(self, request_id: str, body_sha: str, action: str,
                      session_key: str | None) -> tuple[str, Reservation]:
        """Returns ("new" | "duplicate" | "inflight", reservation).

        - "new"      : caller owns it and must launch, then call complete().
        - "duplicate": an identical request already finished; return its receipt.
        - "inflight" : an identical request is running; await reservation.ready, then return
                       its receipt. This is what makes two simultaneous POSTs of one click
                       produce ONE dispatch.
        """
        async with self._lock:
            now = self._clock()
            self._sweep(now)
            existing = self._slots.get(request_id)
            if existing is not None:
                if (existing.body_sha, existing.action, existing.session_key) != (body_sha, action, session_key):
                    raise ReceiptConflict(
                        "request_id %s was already used for a different %s request; generate a "
                        "new request_id" % (request_id, existing.action)
                    )
                self._slots.move_to_end(request_id)
                return ("duplicate" if existing.ready.is_set() else "inflight", existing)
            reservation = Reservation(
                request_id=request_id,
                body_sha=body_sha,
                action=action,
                session_key=session_key,
                created_at=now,
                reused_after_eviction=self.evicted > 0,
            )
            self._slots[request_id] = reservation
            self._slots.move_to_end(request_id)
            return "new", reservation

    async def complete(self, reservation: Reservation, operation_id: str) -> None:
        async with self._lock:
            reservation.operation_id = operation_id
        reservation.ready.set()

    async def finish(self, reservation: Reservation) -> None:
        async with self._lock:
            reservation.finished = True
            reservation.created_at = self._clock()
            self._sweep(self._clock())

    async def abandon(self, reservation: Reservation) -> None:
        """Drop a reservation whose launch was REFUSED before any subprocess ran, so the
        operator can retry with the same request_id after fixing the precondition."""
        async with self._lock:
            if self._slots.get(reservation.request_id) is reservation:
                self._slots.pop(reservation.request_id, None)
        reservation.ready.set()

    def _sweep(self, now: float) -> None:
        cutoff = now - self._ttl
        for key in [k for k, v in self._slots.items() if v.finished and v.created_at < cutoff]:
            self._slots.pop(key, None)
        # Only FINISHED reservations may be evicted for size: dropping an in-flight one would
        # let a retry launch a second child while the first still runs.
        while len(self._slots) > self._max:
            victim = next((k for k, v in self._slots.items() if v.finished), None)
            if victim is None:
                break
            self._slots.pop(victim, None)
            self.evicted += 1

    def eviction_note(self) -> str:
        if not self.evicted:
            return ""
        return (
            "%d request receipts have aged out of this server's %d-entry bound; a retry of one "
            "of those ids would be treated as a new request and could re-execute."
            % (self.evicted, self._max)
        )

    def __len__(self) -> int:
        return len(self._slots)