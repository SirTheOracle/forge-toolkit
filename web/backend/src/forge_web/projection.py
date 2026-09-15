"""Owner-bound stage projection with explicit provenance.

WHY THIS FIELD IS LABELLED SEPARATELY. `bin/forge-watch:909,916-927` builds
`closed_max_ts[(slug, stage)]` with NO owner binding, so a sibling session finishing `coding`
for the same slug suppresses another owner's open `coding` pending on the board. That is the
watcher's reported finding and it stays authoritative for the watcher's own rows — this
module does not mutate them. The web field is therefore called "Session pending stage" and
carries a source detail explaining the session-bound calculation, and the two are shown as
two facts. Neither writes pipeline truth.

NOTHING HERE IS A PIPELINE TRANSITION. No stage advances, no `next_stage` is treated as
current, and "running" is never inferred from an intent.
"""

from __future__ import annotations

from typing import Any

from .discovery import LiveSession
from .handles import HandleTable
from .models import BoardItem, PendingEntry, StageFacts
from .readers import callback_binding, callbacks_for


def _entry_owner(entry: dict[str, Any], session: LiveSession) -> str:
    """"owned" | "other" | "unknown".

    `incarnation` in a log entry is the session-created stamp (bin/forge-bridge writes it from
    `session_incarnation_of`). An entry naming this session with a DIFFERENT incarnation
    belongs to a previous life of the same name and is not ours.
    """
    name = str(entry.get("session") or "").strip()
    if not name:
        return "unknown"
    if name != session.name:
        return "other"
    inc = str(entry.get("incarnation") or "").strip()
    if inc:
        return "owned" if inc == session.created else "other"
    # A reused name is insufficient to identify an actionable pending entry.
    # Old timestamps prove exclusion; compatible/missing times remain ambiguous.
    from .readers import _emitted_epoch
    timestamp = _emitted_epoch({"emitted_at": entry.get("timestamp")})
    if timestamp is not None and session.created.isdigit() and timestamp < int(session.created):
        return "other"
    return "unknown"


def _ts(entry: dict[str, Any]) -> str:
    return str(entry.get("timestamp") or "")


def _pending_model(handles: HandleTable, session_key: str, slug: str,
                   entry: dict[str, Any], session: LiveSession, callbacks=None, pending_entries=None) -> PendingEntry:
    """Version-encode the handle: `timestamp` identifies the entry, and the version carries
    everything a later action must find unchanged. If the entry is parked, resolved, or has
    its prompt replaced between display and action, the key changes and revalidation refuses."""
    callback_id, callback_sha = callback_binding(session.root, slug, entry, session.name, session.created, callbacks, pending_entries)
    version = "%s|%s|%s|%s|%s" % (
        entry.get("response") is None,
        str(entry.get("parked_at") or ""),
        str(entry.get("prompt_sha256") or ""), callback_id, callback_sha,
    )
    key = handles.issue(
        "pending",
        session_key,
        "%s/%s" % (slug, _ts(entry)),
        version,
        payload={
            "callback_id": callback_id, "callback_sha": callback_sha,
            "slug": slug,
            "stage": str(entry.get("stage") or ""),
            "timestamp": _ts(entry),
            "session": str(entry.get("session") or ""),
            "incarnation": str(entry.get("incarnation") or ""),
        },
    )
    uncommitted = entry.get("uncommitted")
    return PendingEntry(
        object_key=key,
        slug=slug,
        stage=str(entry.get("stage") or ""),
        worker=str(entry.get("to") or "") or None,
        timestamp=_ts(entry),
        parked_at=str(entry.get("parked_at") or "") or None,
        parked_reason=str(entry.get("parked_reason") or "") or None,
        uncommitted=(str(uncommitted).lower() == "true") if uncommitted is not None else None,
        prompt_sha256=str(entry.get("prompt_sha256") or "") or None,
    )


def stage_facts(
    *,
    session: LiveSession,
    session_key: str,
    handles: HandleTable,
    logs: dict[str, tuple[list[dict[str, Any]], bool]],
    context: dict[str, Any] | None,
    board_rows: list[BoardItem],
    status_excerpt: str | None,
) -> StageFacts:
    """Build the complete, provenance-carrying stage picture for one session.

    `logs` maps slug -> (entries, parse_clean) as returned by readers.read_log_entries.
    """
    callbacks = callbacks_for(session.root, None)
    facts = StageFacts(status_excerpt=status_excerpt)
    context = context or {}
    facts.active_pipeline = str(context.get("active_pipeline") or "") or None
    facts.last_stage_completed = str(context.get("last_stage_completed") or "") or None
    facts.last_stage_status = str(context.get("last_stage_status") or "") or None
    facts.last_worker = str(context.get("last_worker") or "") or None
    facts.next_stage_intent = str(context.get("next_stage") or "") or None
    facts.context_updated_at = str(context.get("updated_at") or "") or None
    facts.board_stages = sorted({r.stage for r in board_rows if r.stage})

    owned_open: list[tuple[str, dict[str, Any]]] = []
    ambiguous_detail: list[str] = []

    for slug, (entries, parse_clean) in sorted(logs.items()):
        # Owner-bound closed maxima. A SIBLING session's completion is deliberately absent
        # from this map: plan §5 "Sibling session completion never suppresses another
        # owner's pending."
        closed_owned_max: dict[str, str] = {}
        for entry in entries:
            if entry.get("response") is None:
                continue
            if _entry_owner(entry, session) != "owned":
                continue
            stage = str(entry.get("stage") or "")
            timestamp = _ts(entry)
            if timestamp > closed_owned_max.get(stage, ""):
                closed_owned_max[stage] = timestamp
        for entry in entries:
            if entry.get("response") is not None:
                continue
            owner = _entry_owner(entry, session)
            if owner == "other":
                continue
            if owner == "unknown":
                # Legacy entry with no owner. It NEVER contributes a stage and it never
                # confidently says running; it is reported as ambiguity.
                ambiguous_detail.append(
                    "%s/%s open at %s has no provable current session incarnation"
                    % (slug, entry.get("stage"), _ts(entry))
                )
                continue
            stage = str(entry.get("stage") or "")
            cmax = closed_owned_max.get(stage, "")
            if cmax and _ts(entry) and cmax > _ts(entry):
                continue                            # superseded by OUR OWN newer closed entry
            owned_open.append((slug, entry))
        if not parse_clean:
            ambiguous_detail.append(
                "%s: the per-pipeline log needed the regex fallback and at least one block "
                "could not be classified open-vs-closed" % slug
            )

    parked_entries = [(s, e) for s, e in owned_open if e.get("parked_at")]
    live_entries = [(s, e) for s, e in owned_open if not e.get("parked_at")]
    live_entries.sort(key=lambda pair: _ts(pair[1]))

    facts.parked = [_pending_model(handles, session_key, s, e, session, callbacks, logs[s][0]) for s, e in parked_entries]
    if live_entries:
        slug, entry = live_entries[-1]
        facts.current_pending = _pending_model(handles, session_key, slug, entry, session, callbacks, logs[slug][0])
        facts.concurrent_pending = [
            _pending_model(handles, session_key, s, e, session, callbacks, logs[s][0]) for s, e in live_entries[:-1]
        ]
        facts.session_pending_stage = facts.current_pending.stage
        facts.session_pending_source = (
            "Session pending stage — computed from owned `response: null` entries in "
            ".dev/proposals/%s/forge-log.yml, bound to session %s (%s, created %s). The "
            "watcher's board rows key supersession on (slug, stage) with no owner binding "
            "(bin/forge-watch:909,916-927) and can therefore differ."
            % (slug, session.name, session.session_id, session.created)
        )
    elif ambiguous_detail:
        facts.session_pending_stage = None
        facts.session_pending_source = "Unknown — see ambiguity detail"
    else:
        facts.session_pending_stage = None
        facts.session_pending_source = (
            "No owned open pending entry in any per-slug log for this session."
        )

    if ambiguous_detail:
        facts.ambiguous = True
        facts.ambiguity_detail = "; ".join(ambiguous_detail[:8])
        facts.status_excerpt = status_excerpt

    # "Running" requires supporting CURRENT worker/board evidence. An intent is not evidence.
    if facts.current_pending is not None:
        supporting = [
            r for r in board_rows
            if r.condition in ("SESSION-WORKING", "WORKING", "STAGE-WORKING", "WORKER-RUNNING")
            and r.state in ("working", "running")
            and r.slug == facts.current_pending.slug and r.stage == facts.current_pending.stage
            and r.session == session.name
        ]
        if supporting:
            facts.running = True
            facts.running_evidence = "board row %s (%s) for %s/%s" % (
                supporting[0].condition, supporting[0].row_class,
                supporting[0].slug, supporting[0].stage,
            )
        elif facts.current_pending.worker:
            facts.running_evidence = (
                "an owned pending names worker %s, but no current board row supports it — "
                "shown as pending, not running" % facts.current_pending.worker
            )
    else:
        facts.running_evidence = (
            "No current pending; next-stage intent %r is an intent, not a running stage."
            % (facts.next_stage_intent or "")
        )
    return facts