"""Request identity, receipts, handle lifetime and post-lock revalidation."""

from __future__ import annotations

import asyncio

import pytest

from forge_web.actions import ActionRefused, ActionRunner, Plan
from forge_web.handles import (
    HandleError,
    HandleTable,
    ReceiptConflict,
    ReceiptStore,
    canonical_body_sha,
)
from forge_web.models import OperationState
from forge_web.sampler import BoardSampler


def body(text="hi", request_id="11111111-1111-4111-a111-111111111111", revision=7):
    return {"request_id": request_id, "revision": revision, "text": text}


# ── receipts ─────────────────────────────────────────────────────────────────


def test_request_id_is_excluded_from_the_body_hash_but_revision_is_not():
    assert canonical_body_sha(body()) == canonical_body_sha(
        body(request_id="22222222-2222-4222-a222-222222222222")
    )
    assert canonical_body_sha(body()) != canonical_body_sha(body(revision=8))


async def test_same_id_same_body_reuses_the_receipt_and_launches_nothing_twice():
    store = ReceiptStore()
    kind, first = await store.reserve("rid", "sha", "message", "s-11111111")
    assert kind == "new"
    await store.complete(first, store.new_operation_id())
    kind, again = await store.reserve("rid", "sha", "message", "s-11111111")
    assert kind == "duplicate" and again is first


async def test_same_id_different_body_is_a_conflict():
    store = ReceiptStore()
    _, reservation = await store.reserve("rid", "sha-a", "message", "s-11111111")
    await store.complete(reservation, store.new_operation_id())
    with pytest.raises(ReceiptConflict):
        await store.reserve("rid", "sha-b", "message", "s-11111111")


async def test_two_concurrent_posts_of_one_click_produce_one_launch():
    """The per-session lock would SERIALISE these two; only the receipt store DEDUPLICATES
    them. This is the exact case review R9 raised."""
    store = ReceiptStore()
    results = await asyncio.gather(
        store.reserve("rid", "sha", "message", "s-11111111"),
        store.reserve("rid", "sha", "message", "s-11111111"),
    )
    kinds = sorted(kind for kind, _ in results)
    assert kinds == ["inflight", "new"]


async def test_an_in_flight_reservation_is_never_evicted_for_size():
    store = ReceiptStore(maxsize=2)
    _, held = await store.reserve("in-flight", "sha", "message", None)
    for index in range(6):
        _, done = await store.reserve("done-%d" % index, "sha", "message", None)
        await store.complete(done, store.new_operation_id())
        await store.finish(done)
    kind, again = await store.reserve("in-flight", "sha", "message", None)
    assert kind == "inflight" and again is held
    assert store.eviction_note(), "eviction must be disclosed, not silent"


async def test_a_refused_launch_frees_the_id_so_the_same_intent_can_be_retried():
    store = ReceiptStore()
    _, reservation = await store.reserve("rid", "sha", "message", None)
    await store.abandon(reservation)
    kind, _ = await store.reserve("rid", "sha", "message", None)
    assert kind == "new"


def test_an_operation_from_another_server_instance_is_unknown_not_replayed(checkout):
    runner = ActionRunner(
        checkout.cfg(), receipts=ReceiptStore(), sampler=BoardSampler(),
        live_identity=_never, mutations_enabled=lambda: (True, ""),
    )
    receipt = runner.operation("some-other-instance:000001")
    assert receipt.state is OperationState.UNKNOWN
    assert "restarted" in receipt.uncertainty


async def _never(_name):
    return None


# ── handles ──────────────────────────────────────────────────────────────────


def test_an_unchanged_object_keeps_its_key_across_polls():
    table = HandleTable()
    keys = {table.issue("pending", "s-11111111", "slug/ts", "v1") for _ in range(60)}
    assert len(keys) == 1, "a 2 s poll must not invalidate a selected object"


def test_a_changed_object_gets_a_different_key_and_the_old_one_still_resolves():
    table = HandleTable()
    old = table.issue("pending", "s-11111111", "slug/ts", "v1")
    new = table.issue("pending", "s-11111111", "slug/ts", "v2")
    assert old != new
    # The stale handle still resolves — to its RECORDED version — so revalidation can give a
    # precise "it changed" instead of an indistinguishable 404.
    assert table.resolve(old).version == "v1"


def test_handle_lifetime_is_independent_of_the_poll_cycle():
    ticks = {"t": 0.0}
    table = HandleTable(ttl_s=900.0, clock=lambda: ticks["t"])
    key = table.issue("ask", "s-11111111", "ask-1", "v1")
    ticks["t"] = 899.0
    assert table.resolve(key).natural_id == "ask-1"       # survives 15 minutes of polling
    ticks["t"] = 899.0 + 901.0                            # 901 s after the last resolve
    with pytest.raises(HandleError) as excinfo:
        table.resolve(key)
    assert excinfo.value.code == "unknown_object"


def test_resolving_refreshes_the_entry_so_a_selected_object_does_not_age_out_mid_use():
    ticks = {"t": 0.0}
    table = HandleTable(ttl_s=100.0, clock=lambda: ticks["t"])
    key = table.issue("ask", "s-11111111", "ask-1", "v1")
    for _ in range(10):
        ticks["t"] += 90.0
        assert table.resolve(key) is not None


def test_the_table_is_bounded_and_discloses_eviction():
    table = HandleTable(maxsize=4)
    keys = [table.issue("pending", "s-11111111", "e-%d" % i, "v") for i in range(10)]
    assert len(table) == 4 and table.evicted == 6
    with pytest.raises(HandleError):
        table.resolve(keys[0])


def test_kind_confusion_is_refused():
    table = HandleTable()
    key = table.issue("ask", "s-11111111", "ask-1", "v1")
    with pytest.raises(HandleError) as excinfo:
        table.resolve(key, expect_kind="pending")
    assert excinfo.value.code == "wrong_object_kind"


# ── revalidation under the lock ──────────────────────────────────────────────


async def test_revalidation_runs_after_the_lock_and_refuses_a_changed_object(checkout):
    runner = ActionRunner(
        checkout.cfg(), receipts=ReceiptStore(), sampler=BoardSampler(),
        live_identity=lambda name: asyncio.sleep(0, result={"name": name, "root": str(checkout.root), "session_id": "$1", "created": "1700000000"}), mutations_enabled=lambda: (True, ""),
    )
    table = HandleTable()
    key = table.issue("pending", "s-11111111", "slug/ts", "v1", payload={"slug": "s"})
    ref = table.resolve(key)
    plan = Plan(
        action="park", argv=["/bin/true"], cwd=str(checkout.root), lock_key="s-11111111",
        lock_kind="session", session_name="forge-x", session_id="$1",
        session_created="1700000000", session_root=str(checkout.root), object_ref=ref,
        revalidate_object=lambda: ("v2", "o-newkey"),
    )
    with pytest.raises(ActionRefused) as excinfo:
        await runner._revalidate(plan)
    assert excinfo.value.code == "object_changed"
    assert excinfo.value.current_object_key == "o-newkey"


async def test_revalidation_refuses_same_second_name_reuse_by_tmux_id(checkout):
    async def reused(_name):
        return {"name": "forge-x", "root": str(checkout.root),
                "session_id": "$9", "created": "1700000000"}

    runner = ActionRunner(
        checkout.cfg(), receipts=ReceiptStore(), sampler=BoardSampler(),
        live_identity=reused, mutations_enabled=lambda: (True, ""),
    )
    plan = Plan(
        action="message", argv=["/bin/true"], cwd=str(checkout.root), lock_key="s-11111111",
        lock_kind="session", session_name="forge-x", session_id="$1",
        session_created="1700000000", session_root=str(checkout.root),
    )
    with pytest.raises(ActionRefused) as excinfo:
        await runner._revalidate(plan)
    # created is IDENTICAL; only the tmux object id differs. Second resolution alone could not
    # have caught this.
    assert excinfo.value.code == "target_changed"


async def _alive(_name):
    return None