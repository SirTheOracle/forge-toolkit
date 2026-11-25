"""The response return path, and the durability defect it must report honestly."""

from __future__ import annotations

import json

from forge_web.feed import response_for_dispatch, current_session_reply
from forge_web.models import ResponseMetadataState


def stop_record(session: str, root: str, dispatch_ids: list[str], *, truncated: bool,
                full_bytes: int) -> dict:
    """Exactly the shape bin/forge-cc-hook:452-460 writes, inside its envelope (:190-195)."""
    return {
        "schema": "cc-attention/1", "event": "stop", "variant": "snippet",
        "session": session, "root": root, "pane_index": "0", "role": "orchestrator",
        "tmux_pane": "%1", "emitted_at": "2026-09-14T10:00:00Z",
        "snippet": "done", "snippet_source": "last_assistant_message",
        "looks_like_question": False, "question_snippet": None,
        "response_paths": [".dev/attention/payloads/response.%s.txt" % d for d in dispatch_ids],
        "response_dispatch_ids": dispatch_ids,
        "truncated": truncated, "full_bytes": full_bytes,
    }


def write_payload(root, dispatch_id: str, text: str) -> None:
    path = root / ".dev" / "attention" / "payloads" / ("response.%s.txt" % dispatch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    (root / ".dev" / "attention" / ("dispatch-%s.json" % dispatch_id)).write_text(json.dumps({
        "schema": "cc-dispatch/1", "event": "dispatch", "root": str(root), "session": "forge-x", "dispatch_id": dispatch_id,
        "dispatched_at": "2026-09-14T10:00:00Z"}))


def write_stop(root, session: str, payload: dict) -> None:
    path = root / ".dev" / "attention" / ("stop.%s.json" % session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_matching_association_carries_the_metadata(project_root):
    write_payload(project_root, "cc-1", "the answer")
    write_stop(project_root, "forge-x",
               stop_record("forge-x", str(project_root), ["cc-1"], truncated=True, full_bytes=999))
    card = response_for_dispatch(str(project_root), "forge-x", "cc-1", 1700000000)
    assert card is not None
    assert card.metadata_state is ResponseMetadataState.MATCHED
    assert card.truncated is True and card.full_bytes == 999


def test_a_second_turn_overwrites_the_stop_record_and_the_older_payload_is_unknown(project_root):
    """THE DEFECT, reproduced. `atomic()` REPLACES stop.<session>.json every turn, so the
    first dispatch's payload outlives its only metadata."""
    write_payload(project_root, "cc-1", "first answer")
    write_stop(project_root, "forge-x",
               stop_record("forge-x", str(project_root), ["cc-1"], truncated=True, full_bytes=999))
    # Turn two.
    write_payload(project_root, "cc-2", "second answer")
    write_stop(project_root, "forge-x",
               stop_record("forge-x", str(project_root), ["cc-2"], truncated=False, full_bytes=12))

    older = response_for_dispatch(str(project_root), "forge-x", "cc-1", 1700000000)
    assert older is not None
    assert older.text == "first answer", "the payload itself survives"
    assert older.metadata_state is ResponseMetadataState.LOST_OVERWRITTEN
    assert older.truncated is None, "UNKNOWN — never the newer turn's False"
    assert older.full_bytes is None
    newer = response_for_dispatch(str(project_root), "forge-x", "cc-2", 1700000000)
    assert newer is not None and newer.truncated is False


def test_the_reader_limit_flag_is_independent_of_the_writers_metadata(project_root):
    from forge_web import feed

    write_payload(project_root, "cc-1", "x" * 400)
    write_stop(project_root, "forge-x",
               stop_record("forge-x", str(project_root), ["cc-1"], truncated=False, full_bytes=400))
    original = feed.RESPONSE_READ_MAX
    try:
        feed.RESPONSE_READ_MAX = 100
        card = response_for_dispatch(str(project_root), "forge-x", "cc-1", 1700000000)
    finally:
        feed.RESPONSE_READ_MAX = original
    assert card is not None
    assert card.truncated is False          # the WRITER did not truncate
    assert card.reader_limit_hit is True    # but WE stopped early — two different facts
    assert card.reader_limit_bytes == 100


def test_the_two_byte_bases_are_labelled_and_never_paired(project_root):
    """`full_bytes` counts the RAW body (bin/forge-cc-hook:455); `truncated` was decided on
    the REDACTED body (:430-433). Redaction changes length, so "N of M" would be a fiction."""
    write_payload(project_root, "cc-1", "«redacted» tail")
    write_stop(project_root, "forge-x",
               stop_record("forge-x", str(project_root), ["cc-1"], truncated=True, full_bytes=64))
    card = response_for_dispatch(str(project_root), "forge-x", "cc-1", 1700000000)
    assert card is not None
    assert card.full_bytes_basis == "raw_pre_redaction"
    assert card.truncated_basis == "redacted_post_redaction"


def test_an_absorbed_dispatch_shows_the_session_reply_attributed_to_the_recorded_ids(project_root):
    write_payload(project_root, "cc-2", "the turn's reply")
    write_stop(project_root, "forge-x",
               stop_record("forge-x", str(project_root), ["cc-2"], truncated=False, full_bytes=16))
    card = current_session_reply(str(project_root), "forge-x", 1700000000)
    assert card is not None
    assert card.absorbed is True
    assert card.dispatch_id is None, "never misattributed to the unanswered id"
    assert card.attributed_dispatch_ids == ["cc-2"]
    assert card.text == "the turn's reply"


def test_no_payload_and_no_stop_record_is_simply_absent(project_root):
    assert response_for_dispatch(str(project_root), "forge-x", "cc-nope", 1700000000) is None


def test_a_payload_with_no_stop_record_reports_no_stop_record(project_root):
    write_payload(project_root, "cc-1", "text")
    card = response_for_dispatch(str(project_root), "forge-x", "cc-1", 1700000000)
    assert card is not None
    assert card.metadata_state is ResponseMetadataState.NO_STOP_RECORD
    assert card.truncated is None


def test_a_symlinked_payload_escaping_the_payload_directory_is_refused(project_root, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("not yours")
    link = project_root / ".dev" / "attention" / "payloads" / "response.cc-esc.txt"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    assert response_for_dispatch(str(project_root), "forge-x", "cc-esc", 1700000000) is None