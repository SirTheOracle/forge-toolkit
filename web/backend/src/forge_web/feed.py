"""The running feed: log cursors, the identity join, and the response return path.

THE CURSOR IS AN ALGORITHM, NOT A DESIGN CHOICE (review). Every branch below is specified:

  PRIMING. On the first read of a file the cursor seeks to max(0, size - STARTUP_BUDGET) and
  DISCARDS the first partial line, because a mid-line start would otherwise produce one
  garbage entry per session at startup. 12 MB and 6.6 MB orchestrator-events.log files exist
  today; a full read on every 2 s poll is the thing this exists to prevent.

  PARTIAL-LINE CARRYOVER. A read that ends mid-line keeps the tail in `carry` and prepends it
  to the next read. Only COMPLETE lines (terminated by \\n) are ever emitted.

  OVERLONG LINES. If `carry` reaches LINE_CAP the line is abandoned: `carry` is cleared and
  `discarding` is latched until the next \\n, so a single 40 MB line costs LINE_CAP of memory
  and one discard notice — not an OOM and not a stalled cursor.

  ROTATION AND TRUNCATION. Rotation is (st_dev, st_ino) change; truncation is size < offset.
  Both bump `generation`, reset offset and carry, re-prime, and raise a visible backlog
  marker. Generation is part of every entry id so a post-rotation offset 0 cannot collide
  with a pre-rotation offset 0.

  BACKLOG. If size - offset exceeds the incremental budget, the budget still binds and
  `backlog` is set with the byte count, so the UI says "behind by N bytes" instead of
  pretending it is current.

  FILE TYPE. The cursor refuses anything that is not S_ISREG and opens with O_NONBLOCK. A
  FIFO at `.dev/forge-tmp/orchestrator-events.log` would otherwise block the collector
  forever on open(2); a changing file cannot block it either, because every read is bounded
  and the offset only ever moves forward within one generation.

  BOUNDED SCHEDULING ACROSS SOURCES. `collect_feeds` walks sources round-robin from a
  persistent index with MAX_SOURCES_PER_TICK and a wall-clock budget, so twenty live roots
  cannot starve the two-second cadence: each tick advances a few, and the next tick resumes
  where it stopped.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import (
    FeedEntry,
    FeedSource,
    ResponseCard,
    ResponseMetadataState,
)

STARTUP_BUDGET = 256 * 1024
INCREMENTAL_BUDGET = 256 * 1024
LINE_CAP = 64 * 1024
MAX_ENTRIES_PER_SESSION = 200
MAX_SOURCES_PER_TICK = 8
SOURCE_TICK_BUDGET_S = 0.25
RESPONSE_READ_MAX = 256 * 1024
ATTENTION_FILE_BUDGET = 200


# ── cursors ──────────────────────────────────────────────────────────────────


@dataclass
class LogCursor:
    path: str
    dev: int = -1
    ino: int = -1
    offset: int = 0
    generation: int = 0
    carry: bytes = b""
    discarding: bool = False
    primed: bool = False
    backlog: bool = False
    backlog_detail: str = ""
    discarded_lines: int = 0
    unusable: str = ""
    anchor: bytes = b""

    def read_new(self, budget: int = INCREMENTAL_BUDGET) -> list[str]:
        """Return the complete lines that became available since the last call."""
        self.backlog = False
        self.backlog_detail = ""
        self.unusable = ""
        from .readers import safe_source
        if not safe_source(self.path):
            self.unusable = "event log escapes project state tree"
            return []
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        if not stat.S_ISREG(st.st_mode):
            self.unusable = "not a regular file; the feed will not read it"
            return []

        changed_prefix = False
        if self.offset and self.anchor:
            try:
                with open(self.path, "rb") as handle:
                    handle.seek(max(0, self.offset - len(self.anchor)))
                    changed_prefix = handle.read(len(self.anchor)) != self.anchor
            except OSError:
                pass
        if (st.st_dev, st.st_ino) != (self.dev, self.ino):
            if self.primed:
                self.generation += 1
                self.backlog = True
                self.backlog_detail = "log rotated; the cursor restarted at the new file's tail"
            self.dev, self.ino = st.st_dev, st.st_ino
            self.offset = 0
            self.carry = b""
            self.discarding = False
            self.primed = False
        elif st.st_size < self.offset or changed_prefix:
            self.generation += 1
            self.offset = 0
            self.carry = b""
            self.discarding = False
            self.primed = False
            self.backlog = True
            self.backlog_detail = "log truncated; the cursor restarted"

        discard_first_partial = False
        if not self.primed:
            if st.st_size > STARTUP_BUDGET:
                self.offset = st.st_size - STARTUP_BUDGET
                discard_first_partial = True
            else:
                self.offset = 0
            self.primed = True

        available = st.st_size - self.offset
        if available <= 0:
            return []
        if available > budget:
            self.backlog = True
            self.backlog_detail = "behind by %d bytes; reading %d this tick" % (available, budget)
        want = min(available, budget)

        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                blob = os.pread(fd, want, self.offset)
            finally:
                os.close(fd)
        except OSError as exc:
            self.unusable = "read failed: %s" % exc
            return []
        self.offset += len(blob)
        self.anchor = blob[-64:]

        if discard_first_partial:
            cut = blob.find(b"\n")
            blob = b"" if cut < 0 else blob[cut + 1:]

        data = self.carry + blob
        self.carry = b""
        lines: list[str] = []
        start = 0
        while True:
            nl = data.find(b"\n", start)
            if nl < 0:
                break
            raw = data[start:nl]
            start = nl + 1
            if self.discarding:
                self.discarding = False
                self.discarded_lines += 1
                continue
            if len(raw) > LINE_CAP:
                self.discarded_lines += 1
                continue
            lines.append(raw.decode("utf-8", "replace"))
        tail = data[start:]
        if self.discarding:
            pass                                    # still inside an abandoned line
        elif len(tail) > LINE_CAP:
            self.discarding = True
            self.discarded_lines += 1
            tail = b""
        self.carry = tail
        return lines


# ── event lines ──────────────────────────────────────────────────────────────

_EVENT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_.-]*): pipeline=(\S*)\s*(.*)$")
_PROVENANCE_RE = re.compile(
    r"\s+event_at=(?P<at>\S+)"
    r"(?:\s+event_session=(?P<sess>\S+))?"
    r"(?:\s+event_session_id=(?P<sid>\S+))?"
    r"(?:\s+event_incarnation=(?P<inc>\S+))?\s*$"
)
_KV_RE = re.compile(r"(?<![\w-])([a-z][a-z0-9_]*)=(\S*)")


@dataclass(frozen=True)
class ParsedEvent:
    type: str
    slug: str
    fields: dict[str, str]
    event_at: str | None
    event_session: str | None
    event_session_id: str | None
    event_incarnation: str | None
    raw: str

    @property
    def legacy(self) -> bool:
        """An ABSENT event_at is LEGACY, not a parse error.

        `_callback_incarnation_event_locked` (bin/forge-bridge:299) is a third writer to this
        log that deliberately avoids `_emit_event` so it spawns no child under a callback
        lock. Its lines carry no provenance at all, by design and permanently.
        """
        return self.event_at is None


def parse_event_line(line: str) -> ParsedEvent | None:
    match = _EVENT_RE.match(line)
    if not match:
        return None
    etype, slug, rest = match.group(1), match.group(2), match.group(3)
    prov = _PROVENANCE_RE.search(rest)
    at = sess = sid = inc = None
    if prov:
        at = prov.group("at")
        sess = prov.group("sess")
        sid = prov.group("sid")
        inc = prov.group("inc")
        rest = rest[: prov.start()]
    fields = {k: v for k, v in _KV_RE.findall(rest)}
    return ParsedEvent(
        type=etype, slug=slug, fields=fields, event_at=at, event_session=sess,
        event_session_id=sid, event_incarnation=inc, raw=line,
    )


def event_belongs_to(event: ParsedEvent, *, name: str, session_id: str, created: str) -> bool:
    """Exact physical identity join. ALL THREE must match.

    `#{session_created}` has one-second resolution, so name+created alone cannot exclude
    same-second reuse; the tmux session id is what makes this a proof. An event missing any
    of the three has no proof and is omitted from the SESSION feed — it remains project-scoped
    evidence, which is a different view.
    """
    return (
        event.event_session == name
        and event.event_session_id == session_id
        and event.event_incarnation == created
    )


def _entry_id(*parts: object) -> str:
    blob = "\x00".join(str(p) for p in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def events_to_entries(
    lines: Iterable[str], cursor: LogCursor, *, name: str, session_id: str, created: str
) -> tuple[list[FeedEntry], int]:
    """(session-owned entries, count of lines omitted for lack of identity proof)."""
    entries: list[FeedEntry] = []
    omitted = 0
    for index, line in enumerate(lines):
        event = parse_event_line(line)
        if event is None:
            continue
        if not event_belongs_to(event, name=name, session_id=session_id, created=created):
            omitted += 1
            continue
        entries.append(
            FeedEntry(
                entry_id=_entry_id(cursor.path, cursor.generation, cursor.offset, index, line),
                source=FeedSource.EVENT_LOG,
                kind=event.type,
                observed_at=_now_iso(),
                occurred_at=event.event_at,
                text=line.strip(),
                slug=event.slug or None,
                stage=event.fields.get("stage") or event.fields.get("result_stage"),
                session_proof=True,
                detail=dict(list(event.fields.items())[:12]),
            )
        )
    return entries, omitted


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── attention startup facts ──────────────────────────────────────────────────

_ATTENTION_AGE_FIELDS = ("age_s", "heartbeat_age_s", "observed_at", "last_seen")


def _meaningful_revision(record: dict[str, Any]) -> str:
    """Dedup key: the record's content MINUS fields that only encode age.

    Plan §6: "Deduplicate source IDs plus meaningful revisions; age changes are not events."
    """
    trimmed = {k: v for k, v in record.items() if k not in _ATTENTION_AGE_FIELDS and not k.startswith("_")}
    blob = json.dumps(trimmed, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def attention_entries(
    root: str, *, name: str, created_epoch: int | None, seen: dict[str, str]
) -> list[FeedEntry]:
    """Current structured ask/dispatch/callback/prompt/stop/task/episode facts, bounded.

    These provide STARTUP activity — the log cursor starts at the tail and has no history —
    with explicit source identity and time checks. A record whose `session` or `root` does
    not match, or whose `emitted_at` predates this session's creation, is skipped: a previous
    incarnation's residue is not this session's activity.
    """
    from .readers import read_json, rotating_names

    adir = os.path.join(root, ".dev", "attention")
    try:
        names = rotating_names(adir, ATTENTION_FILE_BUDGET, "feed:" + name)
    except OSError:
        return []
    out: list[FeedEntry] = []
    for filename in names:
        if not filename.endswith(".json"):
            continue
        result = read_json(os.path.join(adir, filename))
        if result.state != "ok" or not isinstance(result.data, dict):
            continue
        record = result.data
        if str(record.get("session") or "") != name:
            continue
        if os.path.realpath(str(record.get("root") or "")) != os.path.realpath(root):
            continue
        emitted = str(record.get("dispatched_at") or record.get("emitted_at") or "")
        if not record_current(record, root, name, created_epoch):
            continue
        revision = _meaningful_revision(record)
        if seen.get(filename) == revision:
            continue                                  # unchanged, or only its age changed
        seen[filename] = revision
        kind = str(record.get("event") or record.get("variant") or "attention")
        out.append(
            FeedEntry(
                entry_id=_entry_id(root, filename, revision),
                source=FeedSource.ATTENTION,
                kind=kind,
                observed_at=_now_iso(),
                occurred_at=emitted or None,
                text=_attention_text(kind, record),
                slug=str(record.get("slug") or "") or None,
                stage=str(record.get("stage") or "") or None,
                session_proof=True,
                detail={
                    k: str(v)[:200]
                    for k, v in record.items()
                    if k in ("ask_id", "dispatch_id", "mode", "sender", "state", "worker",
                             "snippet_source", "looks_like_question")
                },
            )
        )
    return out


def _attention_text(kind: str, record: dict[str, Any]) -> str:
    if kind == "ask":
        return "ask %s (%s): %s" % (
            record.get("ask_id"), record.get("mode"), record.get("question_snippet") or ""
        )
    if kind == "dispatch":
        return "dispatch %s (%s): %s" % (
            record.get("dispatch_id"), record.get("mode"),
            record.get("instruction_snippet") or "",
        )
    if kind == "stop":
        return "turn ended: %s" % (record.get("snippet") or "")
    return "%s: %s" % (kind, record.get("snippet") or record.get("msg") or "")


# ── the response return path (R10) ───────────────────────────────────────────


def record_current(record: dict[str, Any], root: str, name: str, created_epoch: int | None) -> bool:
    import datetime
    if record.get("session") != name or os.path.realpath(str(record.get("root") or "")) != os.path.realpath(root):
        return False
    if created_epoch is None:
        return False
    inc = record.get("incarnation") or record.get("session_incarnation")
    if inc is not None and str(inc) != str(created_epoch):
        return False
    stamp = record.get("dispatched_at") or record.get("emitted_at") or record.get("timestamp")
    try:
        observed = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return observed.tzinfo is not None and observed.timestamp() >= created_epoch
    except (TypeError, ValueError):
        return False


def current_session_reply(root: str, session_name: str, created_epoch: int | None) -> ResponseCard | None:
    from .readers import read_json, read_prefix, contained
    stop = read_json(os.path.join(root, ".dev", "attention", "stop.%s.json" % session_name))
    data = stop.data
    if stop.state != "ok" or not isinstance(data, dict) or not record_current(data, root, session_name, created_epoch):
        return None
    ids = data.get("response_dispatch_ids") or []
    paths = data.get("response_paths") or []
    if not isinstance(ids, list) or not isinstance(paths, list):
        return None
    for rel in paths:
        if not isinstance(rel, str) or not rel.startswith(".dev/attention/payloads/response."):
            continue
        path = os.path.join(root, rel)
        if not contained(root, path):
            continue
        payload = read_prefix(path, RESPONSE_READ_MAX)
        if payload.state != "ok":
            continue
        return ResponseCard(dispatch_id=None, text=payload.text or "", absorbed=True,
            attributed_dispatch_ids=[str(i) for i in ids], metadata_state=ResponseMetadataState.MATCHED,
            truncated=data.get("truncated") if isinstance(data.get("truncated"), bool) else None,
            full_bytes=data.get("full_bytes") if isinstance(data.get("full_bytes"), int) else None,
            reader_limit_hit=payload.limit_hit,
            reader_limit_bytes=RESPONSE_READ_MAX if payload.limit_hit else None, observed_at=_now_iso())
    return None


def response_for_dispatch(root: str, session_name: str, dispatch_id: str,
                          created_epoch: int | None = None) -> ResponseCard | None:
    from .readers import read_json, read_prefix, contained
    if not re.fullmatch(r"[A-Za-z0-9._-]+", dispatch_id):
        return None
    dispatch = read_json(os.path.join(root, ".dev", "attention", "dispatch-%s.json" % dispatch_id))
    if (dispatch.state != "ok" or not isinstance(dispatch.data, dict)
            or dispatch.data.get("dispatch_id") != dispatch_id
            or dispatch.data.get("event") != "dispatch"
            or dispatch.data.get("schema") != "cc-dispatch/1"
            or not record_current(dispatch.data, root, session_name, created_epoch)):
        return None
    rel = ".dev/attention/payloads/response.%s.txt" % dispatch_id
    path = os.path.join(root, rel)
    if not contained(root, path):
        return None
    payload = read_prefix(path, RESPONSE_READ_MAX)
    if payload.state != "ok":
        return None
    stop = read_json(os.path.join(root, ".dev", "attention", "stop.%s.json" % session_name))
    data = stop.data if stop.state == "ok" and isinstance(stop.data, dict) else {}
    matched = (record_current(data, root, session_name, created_epoch)
               and dispatch_id in (data.get("response_dispatch_ids") or [])
               and rel in (data.get("response_paths") or []))
    return ResponseCard(dispatch_id=dispatch_id, text=payload.text or "",
        metadata_state=ResponseMetadataState.MATCHED if matched else (ResponseMetadataState.LOST_OVERWRITTEN if data else ResponseMetadataState.NO_STOP_RECORD),
        truncated=data.get("truncated") if matched else None,
        full_bytes=data.get("full_bytes") if matched else None,
        attributed_dispatch_ids=[dispatch_id], reader_limit_hit=payload.limit_hit,
        reader_limit_bytes=RESPONSE_READ_MAX if payload.limit_hit else None, observed_at=_now_iso())


def _contained(root: str, candidate: str) -> bool:
    from .readers import contained
    return contained(root, candidate)


# ── bounded round-robin scheduling ───────────────────────────────────────────


@dataclass
class FeedState:
    """Per-session feed state owned by the collector."""

    cursor: LogCursor
    entries: list[FeedEntry] = field(default_factory=list)
    attention_seen: dict[str, str] = field(default_factory=dict)
    omitted_without_proof: int = 0

    def extend(self, new: Iterable[FeedEntry]) -> None:
        self.entries.extend(new)
        if len(self.entries) > MAX_ENTRIES_PER_SESSION:
            del self.entries[: len(self.entries) - MAX_ENTRIES_PER_SESSION]


class RoundRobin:
    """A persistent index over source keys with a per-tick count and wall-clock budget.

    Twenty live roots at 2 s cannot each get a full read every tick; without this, one slow
    root would either starve the others or blow the cadence. Each tick advances at most
    MAX_SOURCES_PER_TICK sources or SOURCE_TICK_BUDGET_S of wall time, whichever comes first,
    and the next tick resumes from where this one stopped.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._index = 0
        self._clock = clock

    def take(self, keys: list[str]) -> list[str]:
        if not keys:
            return []
        self._index %= len(keys)
        take = keys[self._index:] + keys[: self._index]
        self._index = (self._index + MAX_SOURCES_PER_TICK) % len(keys)
        return take[:MAX_SOURCES_PER_TICK]

    def budget_expired(self, started: float) -> bool:
        return (self._clock() - started) > SOURCE_TICK_BUDGET_S


def structured_entries(root: str, *, name: str, created_epoch: int | None,
                       records: Iterable[tuple[str, dict[str, Any]]], seen: dict[str, str]) -> list[FeedEntry]:
    """Current callback and board task/episode adapters. Missing owner/time is omitted."""
    out=[]
    for source_id, original in records:
        record=dict(original)
        # The board's task timestamps differ from attention envelopes.
        record.setdefault('emitted_at', record.get('answered_at') or record.get('started_at')
                          or record.get('last_at') or record.get('first_at') or record.get('timestamp'))
        if not record_current(record, root, name, created_epoch):
            continue
        revision=_meaningful_revision(record)
        if seen.get(source_id)==revision:
            continue
        seen[source_id]=revision
        out.append(FeedEntry(entry_id=_entry_id(root,source_id,revision), source=FeedSource.ATTENTION,
            kind=source_id.split(':',1)[0],observed_at=_now_iso(),occurred_at=str(record.get('emitted_at') or record.get('dispatched_at')),
            text='%s: %s' % (source_id, record.get('state') or record.get('status') or record.get('msg') or ''),
            slug=record.get('slug'),stage=record.get('stage'),session_proof=True,
            detail={'source_id':source_id}))
    return out