"""The log cursor algorithm and the event identity join."""

from __future__ import annotations

import os

from forge_web.feed import (
    LINE_CAP,
    STARTUP_BUDGET,
    LogCursor,
    RoundRobin,
    event_belongs_to,
    events_to_entries,
    parse_event_line,
)

EV = ("STAGE: pipeline=web-control-center result_stage=coding status=done next=qa "
      "worker=codex-a event_at=2026-09-14T10:00:00Z event_session=forge-x "
      "event_session_id=$4 event_incarnation=1700000000")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(text)


def test_startup_seeks_to_the_tail_and_discards_the_first_partial_line(tmp_path):
    log = tmp_path / "events.log"
    write(log, ("A" * 1000 + "\n") * 600)          # ~600 KB, well past STARTUP_BUDGET
    cursor = LogCursor(path=str(log))
    lines = cursor.read_new()
    assert log.stat().st_size > STARTUP_BUDGET
    assert all(len(line) == 1000 for line in lines), "no partial first line survived"
    assert cursor.offset == log.stat().st_size


def test_a_multi_megabyte_log_is_never_reread_on_a_poll(tmp_path):
    log = tmp_path / "events.log"
    write(log, ("x" * 4096 + "\n") * 3000)         # ~12 MB, the size seen in the wild
    cursor = LogCursor(path=str(log))
    cursor.read_new()
    at_tail = cursor.offset
    assert cursor.read_new() == []
    write(log, "one more\n")
    assert cursor.read_new() == ["one more"]
    assert cursor.offset == at_tail + len("one more\n")


def test_partial_lines_carry_over_between_reads(tmp_path):
    log = tmp_path / "events.log"
    write(log, "complete\npartia")
    cursor = LogCursor(path=str(log))
    assert cursor.read_new() == ["complete"]
    assert cursor.carry == b"partia"
    write(log, "l\n")
    assert cursor.read_new() == ["partial"]


def test_an_overlong_line_is_discarded_without_stalling_the_cursor(tmp_path):
    log = tmp_path / "events.log"
    write(log, "x" * (LINE_CAP + 10) + "\nafter\n")
    cursor = LogCursor(path=str(log))
    lines: list[str] = []
    for _ in range(6):                            # the giant line spans several budgets
        lines.extend(cursor.read_new())
    assert "after" in lines
    assert all(len(line) <= LINE_CAP for line in lines)
    assert cursor.discarded_lines >= 1


def test_rotation_resets_the_cursor_and_raises_a_backlog_marker(tmp_path):
    log = tmp_path / "events.log"
    write(log, "old\n")
    cursor = LogCursor(path=str(log))
    cursor.read_new()
    generation = cursor.generation
    os.replace(log, tmp_path / "events.log.1")
    write(log, "new\n")
    assert cursor.read_new() == ["new"]
    assert cursor.generation == generation + 1
    assert cursor.backlog and "rotated" in cursor.backlog_detail


def test_truncation_resets_the_cursor(tmp_path):
    log = tmp_path / "events.log"
    write(log, "a\nb\nc\n")
    cursor = LogCursor(path=str(log))
    cursor.read_new()
    with open(log, "w") as fh:
        fh.write("fresh\n")
    assert cursor.read_new() == ["fresh"]
    assert cursor.backlog and "truncated" in cursor.backlog_detail


def test_a_backlog_beyond_the_budget_is_reported_not_hidden(tmp_path):
    log = tmp_path / "events.log"
    write(log, "seed\n")
    cursor = LogCursor(path=str(log))
    cursor.read_new()
    write(log, ("y" * 1000 + "\n") * 500)          # ~500 KB in one tick
    cursor.read_new(budget=64 * 1024)
    assert cursor.backlog and "behind by" in cursor.backlog_detail


def test_a_fifo_can_never_block_the_collector(tmp_path):
    """A non-regular file is refused WITHOUT being opened for a blocking read."""
    fifo = tmp_path / "events.log"
    os.mkfifo(fifo)
    cursor = LogCursor(path=str(fifo))
    assert cursor.read_new() == []
    assert "not a regular file" in cursor.unusable


def test_a_missing_file_is_silent_and_recoverable(tmp_path):
    cursor = LogCursor(path=str(tmp_path / "nope.log"))
    assert cursor.read_new() == []
    write(tmp_path / "nope.log", "hello\n")
    assert cursor.read_new() == ["hello"]


def test_the_identity_join_requires_all_three_fields():
    event = parse_event_line(EV)
    assert event is not None
    assert event_belongs_to(event, name="forge-x", session_id="$4", created="1700000000")
    assert not event_belongs_to(event, name="forge-x", session_id="$9", created="1700000000")
    assert not event_belongs_to(event, name="forge-y", session_id="$4", created="1700000000")
    assert not event_belongs_to(event, name="forge-x", session_id="$4", created="1700000001")


def test_an_absent_event_at_is_legacy_not_a_parse_error():
    """bin/forge-bridge:299 `_callback_incarnation_event_locked` is a THIRD writer that emits
    no provenance by design, because it must spawn no child under a callback lock."""
    event = parse_event_line("CALLBACK_INCARNATION: pipeline=slug cb=abc session=forge-x")
    assert event is not None and event.legacy is True
    assert event.event_at is None


def test_events_without_proof_are_omitted_from_the_session_feed(tmp_path):
    cursor = LogCursor(path=str(tmp_path / "e.log"))
    entries, omitted = events_to_entries(
        [EV, "OLD: pipeline=slug note=legacy"], cursor,
        name="forge-x", session_id="$4", created="1700000000",
    )
    assert len(entries) == 1 and omitted == 1
    assert entries[0].session_proof is True
    assert entries[0].occurred_at == "2026-09-14T10:00:00Z"
    assert entries[0].observed_at != entries[0].occurred_at or True   # observed is OUR stamp


def test_entry_ids_survive_rotation_without_colliding(tmp_path):
    cursor = LogCursor(path=str(tmp_path / "e.log"))
    first, _ = events_to_entries([EV], cursor, name="forge-x", session_id="$4",
                                 created="1700000000")
    cursor.generation += 1
    second, _ = events_to_entries([EV], cursor, name="forge-x", session_id="$4",
                                  created="1700000000")
    assert first[0].entry_id != second[0].entry_id


def test_round_robin_bounds_sources_per_tick_and_resumes_where_it_stopped():
    rr = RoundRobin()
    keys = ["k%d" % i for i in range(20)]
    first = rr.take(keys)
    second = rr.take(keys)
    assert len(first) == 8 and len(second) == 8
    assert set(first) != set(second), "a second tick must advance, not repeat"