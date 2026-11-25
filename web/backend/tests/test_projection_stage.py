"""Owner-bound stage projection. Every case here is one of plan required-case 2's."""

from __future__ import annotations

from forge_web.discovery import LiveSession
from forge_web.handles import HandleTable
from forge_web.models import BoardItem
from forge_web.projection import stage_facts

SESSION = LiveSession(name="forge-x", root="/p/a", session_id="$4",
                      created="1700000000", has_dev=True)
OTHER = "forge-y"


def entry(stage, ts, *, response=None, session="forge-x", incarnation="1700000000", **extra):
    record = {"timestamp": ts, "stage": stage, "response": response,
              "session": session, "incarnation": incarnation, "to": "codex-a"}
    record.update(extra)
    return record


def facts(logs, board=(), context=None):
    return stage_facts(
        session=SESSION, session_key="s-11111111", handles=HandleTable(), logs=logs,
        context=context or {}, board_rows=list(board), status_excerpt="excerpt",
    )


def test_board_lacks_a_stage_but_an_owned_pending_exists():
    result = facts({"slug": ([entry("coding", "2026-09-14T10:00:00Z")], True)})
    assert result.session_pending_stage == "coding"
    assert "no owner binding" in result.session_pending_source
    assert result.running is False, "no board evidence means pending, not running"


def test_a_sibling_sessions_completion_never_suppresses_our_pending():
    logs = {"slug": ([
        entry("coding", "2026-09-14T10:00:00Z"),
        entry("coding", "2026-09-14T11:00:00Z", response="done", session=OTHER),
    ], True)}
    assert facts(logs).session_pending_stage == "coding"


def test_our_own_strictly_newer_closed_entry_supersedes_our_orphan():
    logs = {"slug": ([
        entry("coding", "2026-09-14T10:00:00Z"),
        entry("coding", "2026-09-14T11:00:00Z", response="done"),
        entry("qa", "2026-09-14T12:00:00Z"),
    ], True)}
    result = facts(logs)
    assert result.session_pending_stage == "qa"
    assert result.concurrent_pending == []


def test_a_previous_incarnation_of_the_same_name_is_not_ours():
    logs = {"slug": ([entry("coding", "2026-09-14T10:00:00Z", incarnation="1600000000")], True)}
    assert facts(logs).session_pending_stage is None


def test_parked_coding_plus_active_qa_are_separate_facts():
    logs = {"slug": ([
        entry("coding", "2026-09-14T10:00:00Z", parked_at="2026-09-14T10:30:00Z",
              parked_reason="waiting"),
        entry("qa", "2026-09-14T11:00:00Z"),
    ], True)}
    result = facts(logs)
    assert [p.stage for p in result.parked] == ["coding"]
    assert result.current_pending is not None and result.current_pending.stage == "qa"
    assert result.parked[0].parked_reason == "waiting"


def test_multiple_live_pendings_give_current_plus_concurrent():
    logs = {
        "a": ([entry("coding", "2026-09-14T10:00:00Z")], True),
        "b": ([entry("qa", "2026-09-14T12:00:00Z")], True),
    }
    result = facts(logs)
    assert result.current_pending is not None and result.current_pending.slug == "b"
    assert [p.slug for p in result.concurrent_pending] == ["a"]


def test_ambiguous_legacy_never_confidently_says_running():
    logs = {"slug": ([entry("coding", "2026-09-14T10:00:00Z", session=None)], True)}
    result = facts(logs)
    assert result.ambiguous is True
    assert result.session_pending_stage is None
    assert result.running is False
    assert result.status_excerpt == "excerpt"


def test_a_non_authoritative_parse_is_reported_as_ambiguity():
    result = facts({"slug": ([entry("coding", "2026-09-14T10:00:00Z")], False)})
    assert result.ambiguous and "regex fallback" in result.ambiguity_detail


def test_running_requires_board_evidence_and_names_it():
    logs = {"slug": ([entry("coding", "2026-09-14T10:00:00Z")], True)}
    row = BoardItem(condition="SESSION-WORKING", row_class="active", state="working",
                    msg="working", slug="slug", stage="coding", acked=False,
                    session="forge-x", root="/p/a", label="a")
    result = facts(logs, board=[row])
    assert result.running is True and "SESSION-WORKING" in result.running_evidence


def test_next_stage_intent_is_never_labelled_running():
    result = facts({}, context={"active_pipeline": "slug", "next_stage": "qa",
                                "last_stage_completed": "coding", "last_stage_status": "done"})
    assert result.next_stage_intent == "qa"
    assert result.session_pending_stage is None
    assert result.running is False
    assert "an intent, not a running stage" in result.running_evidence


def test_the_pending_handle_encodes_the_version_so_a_park_cannot_hit_a_changed_entry():
    table = HandleTable()
    common = dict(session=SESSION, session_key="s-11111111", handles=table, context={},
                  board_rows=[], status_excerpt=None)
    first = stage_facts(logs={"slug": ([entry("coding", "T1")], True)}, **common)
    second = stage_facts(
        logs={"slug": ([entry("coding", "T1", parked_at="2026-09-14T10:30:00Z")], True)},
        **common,
    )
    assert first.current_pending is not None
    assert second.current_pending is None and len(second.parked) == 1
    assert first.current_pending.object_key != second.parked[0].object_key