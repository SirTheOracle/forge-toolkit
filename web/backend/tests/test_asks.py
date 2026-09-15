"""Ask projection: ownership, incarnation, and the three text states."""

from __future__ import annotations

import json

from forge_web.readers import ask_text_projection, open_asks

CREATED = 1_700_000_000


def write_ask(root, ask_id, **overrides):
    record = {
        "schema": "cc-attention/1", "event": "ask", "variant": "ask",
        "session": "forge-x", "root": str(root), "pane_index": "0", "role": "worker",
        "tmux_pane": "%3", "emitted_at": "2026-09-14T10:00:00Z", "ask_id": ask_id,
        "mode": "stage", "question_snippet": "Which option?",
        "question_sha256": "a" * 64, "slug": "slug", "stage": "coding", "worker": "codex-a",
    }
    record.update(overrides)
    path = root / ".dev" / "attention" / ("%s.json" % ask_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return record


def test_an_ask_for_this_session_is_actionable(project_root):
    write_ask(project_root, "ask-1")
    records = open_asks(str(project_root), "forge-x", CREATED)
    assert len(records) == 1 and records[0]["_actionable"] is True


def test_a_wrong_session_ask_is_evidence_not_an_action(project_root):
    write_ask(project_root, "ask-2", session="forge-y")
    record = open_asks(str(project_root), "forge-x", CREATED)[0]
    assert record["_actionable"] is False and "forge-y" in record["_ownership_note"]


def test_an_ask_without_a_session_is_unprovable_ownership(project_root):
    write_ask(project_root, "ask-3", session=None)
    record = open_asks(str(project_root), "forge-x", CREATED)[0]
    assert record["_actionable"] is False and "project evidence" in record["_ownership_note"]


def test_an_ask_older_than_the_session_belongs_to_a_previous_incarnation(project_root):
    write_ask(project_root, "ask-4", emitted_at="2020-01-01T00:00:00Z")
    record = open_asks(str(project_root), "forge-x", CREATED)[0]
    assert record["_actionable"] is False and "earlier incarnation" in record["_ownership_note"]


def test_an_archived_ask_is_not_an_open_ask(project_root):
    write_ask(project_root, "ask-5")
    src = project_root / ".dev" / "attention" / "ask-5.json"
    dst = project_root / ".dev" / "attention" / "archive" / "ask-5.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dst)
    assert open_asks(str(project_root), "forge-x", CREATED) == []


def test_full_truncated_and_legacy_text_states():
    full = ask_text_projection({"question_text": "the whole question"})
    assert full[1] == "full" and full[0] == "the whole question"

    clipped = ask_text_projection({
        "question_text": "clipped", "question_text_truncated": True,
        "question_text_bytes": 70000,
    })
    assert clipped[1] == "truncated"
    assert "7 of 70000" in clipped[2] and "truncated" in clipped[2].lower()

    legacy = ask_text_projection({"question_snippet": "only a snippet"})
    assert legacy[1] == "legacy_excerpt"
    assert legacy[2] == "Saved excerpt; full question was not retained"


def test_the_raw_hash_is_never_used_to_validate_the_redacted_text(project_root):
    """question_sha256 is over the RAW question; question_text is REDACTED. Checking one
    against the other would fail on every record containing a redactable token, so the
    projection must not reference the hash at all."""
    import inspect

    from forge_web import readers

    source = inspect.getsource(readers.ask_text_projection)
    assert "question_sha256" not in source.split('"""')[2], (
        "the hash appears in ask_text_projection's CODE, not just its docstring"
    )


def test_a_multibyte_boundary_is_preserved_by_the_reader(project_root):
    text = "héllo — ünicode ✓"
    write_ask(project_root, "ask-6", question_text=text,
              question_text_truncated=False, question_text_bytes=len(text.encode()))
    record = open_asks(str(project_root), "forge-x", CREATED)[0]
    assert ask_text_projection(record)[0] == text