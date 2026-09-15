"""The command table, the child environment at the call site, and the operation lifecycle."""

from __future__ import annotations

import asyncio

import pytest

from forge_web.actions import (
    ActionRefused,
    ActionRunner,
    CommandTable,
    OUTPUT_CAPTURE_MAX,
    PAUSE_TEXT,
    Plan,
    STOP_TEXT,
    parse_dispatch,
    parse_park,
    parse_spawn,
)
from forge_web.handles import ReceiptStore
from forge_web.models import MessageRequest, OperationState, SpawnRequest, TEXT_MAX_BYTES
from forge_web.sampler import BoardSampler

NAME, ROOT, SID, CREATED = "forge-x", "/p/a", "$4", "1700000000"


async def _alive(_name):
    return {"name": NAME, "root": ROOT, "session_id": SID, "created": CREATED}


def runner_for(checkout) -> ActionRunner:
    return ActionRunner(
        checkout.cfg(), receipts=ReceiptStore(), sampler=BoardSampler(),
        live_identity=_alive, mutations_enabled=lambda: (True, ""),
    )


# ── argv ─────────────────────────────────────────────────────────────────────


def test_message_argv_is_seat_pointer_expectations_then_the_literal_boundary(checkout):
    argv = CommandTable(checkout.cfg()).message(NAME, ROOT, SID, CREATED, "hello\nworld", None)
    assert argv[1:6] == ["dispatch", "@forge-x", "--sender", "seat", "--pointer"]
    assert argv[-2:] == ["--", "hello\nworld"]
    assert "--expected-session-id" in argv and argv[argv.index("--expected-session-id") + 1] == "$4"
    # `$N` is a SESSION id; `%N` is a PANE id. A `%` here would be the F2 outage.
    assert not any(a.startswith("%") for a in argv)
    assert "--force" not in argv and "--allow-api-billing" not in argv


def test_answer_argv_adds_answers_before_the_boundary(checkout):
    argv = CommandTable(checkout.cfg()).message(NAME, ROOT, SID, CREATED, "yes", "ask-1")
    assert argv[argv.index("--answers") + 1] == "ask-1"
    assert argv.index("--answers") < argv.index("--")


def test_pause_stop_argv_is_the_fixed_phrase_with_no_pointer(checkout):
    table = CommandTable(checkout.cfg())
    pause = table.control(NAME, ROOT, SID, CREATED, "pause")
    stop = table.control(NAME, ROOT, SID, CREATED, "stop")
    assert "--pointer" not in pause and "--pointer" not in stop
    assert pause[-2:] == ["--", PAUSE_TEXT]
    assert stop[-2:] == ["--", STOP_TEXT]


def test_park_argv_puts_routing_and_expectations_before_the_boundary(checkout):
    argv = CommandTable(checkout.cfg()).park(
        NAME, ROOT, SID, CREATED, slug="s", stage="coding", reason="--target-session evil",
        pending_ts="T1", callback_id="cb1", callback_sha="deadbeef",
    )
    boundary = argv.index("--")
    assert argv[boundary - 1] == "park"
    for flag in ("--target-session", "--cross-session", "--target-root",
                 "--expected-session-id", "--expected-incarnation"):
        assert argv.index(flag) < boundary
    # The reason travels AFTER the boundary, so a reserved-flag-shaped reason is opaque.
    assert argv[argv.index("--reason") + 1] == "--target-session evil"
    assert argv.index("--reason") > boundary


def test_resolve_uses_the_none_sentinels_when_the_displayed_object_had_no_callback(checkout):
    argv = CommandTable(checkout.cfg()).resolve(
        NAME, ROOT, SID, CREATED, slug="s", stage="coding", note="ok",
        pending_ts="T1", callback_id="", callback_sha="",
    )
    assert argv[argv.index("--expected-callback-id") + 1] == "none"
    assert argv[argv.index("--expected-callback-sha256") + 1] == "none"
    assert "--resolve" in argv


def test_spawn_argv_never_carries_force_or_billing(checkout):
    argv = CommandTable(checkout.cfg()).spawn("/p/a", "forge-new")
    assert argv[1:] == ["spawn", "--root", "/p/a", "--name", "forge-new"]


def test_no_worker_pane_is_ever_a_target(checkout):
    """Every browser message goes to pane 0 via `forge dispatch`, which targets the
    orchestrator by construction (bin/forge:537). Nothing in the table names a pane."""
    table = CommandTable(checkout.cfg())
    for argv in (
        table.message(NAME, ROOT, SID, CREATED, "t", None),
        table.control(NAME, ROOT, SID, CREATED, "pause"),
        table.park(NAME, ROOT, SID, CREATED, slug="s", stage="c", reason="r",
                   pending_ts="T", callback_id="i", callback_sha="h"),
    ):
        assert not any(a in ("--pane", "--worker", "codex-a", "codex-b") for a in argv)


# ── validation ───────────────────────────────────────────────────────────────


def test_the_text_limit_is_sixty_four_kibibytes_of_utf8():
    ok = "é" * (TEXT_MAX_BYTES // 2)
    MessageRequest(request_id="11111111-1111-4111-a111-111111111111", revision=1, text=ok)
    with pytest.raises(ValueError):
        MessageRequest(request_id="11111111-1111-4111-a111-111111111111", revision=1,
                       text=ok + "é")


def test_nul_bytes_are_refused():
    with pytest.raises(ValueError):
        MessageRequest(request_id="11111111-1111-4111-a111-111111111111", revision=1,
                       text="a\x00b")


@pytest.mark.parametrize("name", ["with space", "a/b", "x\x00", "", "z" * 129])
def test_spawn_names_reject_separators_nul_and_absurd_lengths(name):
    with pytest.raises(ValueError):
        SpawnRequest(request_id="11111111-1111-4111-a111-111111111111", revision=1,
                     root_key="o-" + "a" * 32, name=name)


def test_a_request_body_cannot_name_a_path_cwd_pane_or_executable():
    fields = set(MessageRequest.model_fields) | set(SpawnRequest.model_fields)
    for forbidden in ("path", "cwd", "pane", "executable", "argv", "root", "session", "command"):
        assert forbidden not in fields


# ── the child environment at the actual call site ────────────────────────────


async def test_the_child_receives_every_pinned_binary_and_no_routing_variable(checkout, monkeypatch):
    for key in ("TMUX", "TMUX_PANE", "TMUX_SESSION", "FORGE_ROLE", "FORGE_SENDER"):
        monkeypatch.setenv(key, "leaked")
    runner = runner_for(checkout)
    cfg = checkout.cfg()
    rc, _out, _err, _done = await runner.execute([str(cfg.exe.forge), "board", "--json"],
                                                 str(checkout.root))
    assert rc == 0, "the stub exits 97 unless FORGE_WEB_CHILD=1 reached it"
    line = checkout.calls()[-1]
    env = dict(part.split("=", 1) for part in line[line.index("ENV") + 1:] if "=" in part)
    assert env["FORGE_BROKER_BIN"].startswith(str(cfg.checkout))
    assert env["FORGE_CODEX_DOCTOR_BIN"].startswith(str(cfg.checkout))
    assert env["FORGE_IDENTITY_ENFORCE"] == "1"
    for key in ("TMUX", "TMUX_PANE", "TMUX_SESSION", "FORGE_ROLE", "FORGE_SENDER"):
        assert env[key] == ""


# ── lifecycle ────────────────────────────────────────────────────────────────


def make_slow(tmp_path):
    script = tmp_path / "slow"
    script.write_text(
        "#!/bin/bash\n"
        "while [ ! -f \"$1\" ]; do sleep 0.02; done\n"
        "echo 'DISPATCHED @forge-x pane=0 sender=seat'\n"
    )
    script.chmod(0o755)
    return script


def plan_for(argv, *, action="message", lock="s-11111111"):
    return Plan(action=action, argv=argv, cwd="/tmp", lock_key=lock, lock_kind="session")


async def test_a_hanging_child_keeps_the_lock_and_blocks_a_second_action(checkout, tmp_path):
    runner = runner_for(checkout)
    gate = tmp_path / "gate"
    slow = make_slow(tmp_path)

    first = await runner.submit(
        "message",
        {"request_id": "11111111-1111-4111-a111-111111111111", "revision": 1, "text": "a"},
        lambda: plan_for([str(slow), str(gate)]), "s-11111111",
    )
    second = await runner.submit(
        "message",
        {"request_id": "22222222-2222-4222-a222-222222222222", "revision": 1, "text": "b"},
        lambda: plan_for(["/bin/echo", "DISPATCHED @forge-x pane=0 sender=seat"]), "s-11111111",
    )
    await asyncio.sleep(0.2)
    assert runner.operation(second.operation_id).state is OperationState.QUEUED, (
        "the second action must wait for the first child to EXIT, not for its request"
    )
    gate.write_text("go")
    for _ in range(200):
        if runner.operation(second.operation_id).state is OperationState.EXITED:
            break
        await asyncio.sleep(0.05)
    assert runner.operation(first.operation_id).state is OperationState.EXITED
    assert runner.operation(second.operation_id).state is OperationState.EXITED


async def test_a_client_disconnect_releases_nothing(checkout, tmp_path):
    """The handler returning is the ONLY thing a disconnect can affect; the operation task
    owns the lock and is not cancelled with the request."""
    runner = runner_for(checkout)
    gate = tmp_path / "gate"
    receipt = await runner.submit(
        "message",
        {"request_id": "11111111-1111-4111-a111-111111111111", "revision": 1, "text": "a"},
        lambda: plan_for([str(make_slow(tmp_path)), str(gate)]), "s-11111111",
    )
    del receipt                                   # the "client" goes away
    await asyncio.sleep(0.2)
    assert runner._session_locks["s-11111111"].locked() is True
    gate.write_text("go")
    await asyncio.sleep(0.4)


async def test_read_collection_never_contends_for_the_mutation_lock(checkout, tmp_path):
    runner = runner_for(checkout)
    gate = tmp_path / "gate"
    await runner.submit(
        "message",
        {"request_id": "11111111-1111-4111-a111-111111111111", "revision": 1, "text": "a"},
        lambda: plan_for([str(make_slow(tmp_path)), str(gate)]), "s-11111111",
    )
    await asyncio.sleep(0.15)
    # A read is a pure dict lookup; it must complete while the mutation lock is held.
    assert runner.operation("nope").state is OperationState.UNKNOWN
    gate.write_text("go")
    await asyncio.sleep(0.3)


async def test_a_refused_precondition_launches_nothing(checkout):
    runner = ActionRunner(
        checkout.cfg(), receipts=ReceiptStore(), sampler=BoardSampler(),
        live_identity=_alive, mutations_enabled=lambda: (False, "read-only mode"),
    )
    before = len(checkout.calls())
    with pytest.raises(ActionRefused) as excinfo:
        await runner.submit(
            "message",
            {"request_id": "11111111-1111-4111-a111-111111111111", "revision": 1, "text": "a"},
            lambda: plan_for(["/bin/echo", "x"]), "s-11111111",
        )
    assert excinfo.value.code == "mutations_disabled"
    assert len(checkout.calls()) == before


async def test_output_is_bounded_for_the_receipt_but_the_child_is_fully_drained(checkout, tmp_path):
    chatty = tmp_path / "chatty"
    chatty.write_text("#!/bin/bash\nfor i in $(seq 1 3000); do echo \"line $i padding\"; done\n")
    chatty.chmod(0o755)
    runner = runner_for(checkout)
    receipt = await runner.submit(
        "message",
        {"request_id": "11111111-1111-4111-a111-111111111111", "revision": 1, "text": "a"},
        lambda: plan_for([str(chatty)]), "s-11111111",
    )
    for _ in range(200):
        if runner.operation(receipt.operation_id).state is OperationState.EXITED:
            break
        await asyncio.sleep(0.05)
    final = runner.operation(receipt.operation_id)
    assert final.rc == 0, "a chatty child must EXIT, not block on a full pipe"
    assert len(final.stdout) <= OUTPUT_CAPTURE_MAX and final.truncated_output


# ── outcome components ───────────────────────────────────────────────────────


def test_delivered_and_cleanup_are_independent_components():
    ok = parse_dispatch("DISPATCHED @forge-x pane=0 sender=seat answers=ask-1\n",
                        "forge: consume ask=ask-1 slug=s stage=coding cleanup=failed\n",
                        0, True)
    assert ok.delivered is True and ok.cleanup == "failed"
    assert ok.offers_redispatch is False, "cleanup failure must NOT offer a redispatch"

    lost = parse_dispatch("", "injection failed (forge-bridge send)\n", 1, True)
    assert lost.delivered is False and lost.offers_redispatch is True


def test_a_missing_cleanup_token_on_an_answer_is_unknown_not_not_applicable():
    result = parse_dispatch("DISPATCHED @forge-x pane=0 sender=seat\n", "", 0, True)
    assert result.cleanup == "unknown"


@pytest.mark.parametrize(
    "line,expected,lock",
    [
        ("PARKED slug=s stage=coding lock_release_state=released parked_at=T", "parked", "released"),
        ("PARK PARTIAL slug=s stage=coding lock_release_state=failed — parked record stands",
         "partial", "failed"),
        ("PARK NOOP slug=s stage=coding reason=no-blocked-callback", "noop", None),
        ("PARK RESOLVED slug=s stage=coding", "resolved", None),
    ],
)
def test_every_park_receipt_shape_is_recognised(line, expected, lock):
    components = parse_park(line + "\n", "")
    assert components.park_result == expected
    assert components.lock_release_state == lock


def test_finalize_ready_and_deferred_are_preserved():
    assert parse_park("PARK_RESOLVED FINALIZE_READY slug=s stage=verify\n", "").finalize == "ready"
    assert parse_park("", "PARK_RESOLVED FINALIZE_DEFERRED slug=s stage=verify (retry)\n"
                      ).finalize == "deferred"


@pytest.mark.parametrize(
    "out,err,expected",
    [
        ("SPAWNED @forge-new root=/p/a (stamps set; board via forge-watch)\n", "", "spawned"),
        ("ENSURE @forge-x (already active at /p/a, 5 panes) — no-op\n", "", "ensured"),
        ("SPAWNED @forge-new root=/p/a\n",
         "forge: spawn — WARNING: forge-new has 1 panes after populate (expected 5).\n",
         "partial"),
        ("PLAN ATTACH session=forge-x root=/p/a billing=OK\n", "", "plan"),
    ],
)
def test_every_spawn_receipt_shape_is_recognised(out, err, expected):
    assert parse_spawn(out, err).spawn_result == expected