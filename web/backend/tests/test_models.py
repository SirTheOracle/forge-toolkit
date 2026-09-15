"""The wire contract's tri-states and its refusal to accept a client-named target."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge_web.models import (
    AckRequest,
    ActionEnvelope,
    ResponseCard,
    ResponseMetadataState,
    SessionDetailView,
    SourceState,
)

RID = "11111111-1111-4111-a111-111111111111"


def test_unknown_truncation_is_none_not_false():
    card = ResponseCard(dispatch_id="cc-1", text="x",
                        metadata_state=ResponseMetadataState.LOST_OVERWRITTEN,
                        observed_at="2026-09-14T10:00:00Z")
    assert card.truncated is None and card.full_bytes is None
    assert card.model_dump()["truncated"] is None, "the wire must carry null, not false"


def test_the_two_byte_bases_are_fixed_literals():
    card = ResponseCard(dispatch_id=None, text="", metadata_state=ResponseMetadataState.MATCHED,
                        observed_at="t")
    assert card.full_bytes_basis == "raw_pre_redaction"
    assert card.truncated_basis == "redacted_post_redaction"


def test_extra_fields_are_forbidden_everywhere():
    with pytest.raises(ValidationError):
        ActionEnvelope(request_id=RID, revision=1, pane="0")


def test_the_request_id_must_be_a_uuid_shape():
    with pytest.raises(ValidationError):
        ActionEnvelope(request_id="12345", revision=1)


def test_ack_scope_is_a_breadth_not_a_string():
    with pytest.raises(ValidationError):
        AckRequest(request_id=RID, revision=1, scope="/etc/passwd")
    assert AckRequest(request_id=RID, revision=1, scope="session").scope == "session"


def test_a_handle_cannot_be_a_path_fragment():
    for bad in ("../x", "s-../..", "o-" + "/" * 32, "x-abc"):
        with pytest.raises(ValidationError):
            AckRequest(request_id=RID, revision=1, scope="slug", slug_key=bad)


def test_the_forward_reference_to_ack_scope_is_resolved():
    view = SessionDetailView.model_json_schema()
    assert "ack_scope" in view["properties"]


def test_source_state_has_a_distinct_value_for_every_failure_mode():
    assert {s.value for s in SourceState} == {
        "ok", "stale", "unavailable", "oversized", "malformed", "absent"
    }