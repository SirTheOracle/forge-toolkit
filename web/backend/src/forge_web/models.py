"""Typed wire contracts. Pydantic v2.

Two rules govern this file:

1. Every *unknown* is a real tri-state, never a defaulted lie. `truncated: bool | None`
   means "yes / no / the metadata was overwritten and cannot be recovered" (R10). A
   projection that cannot prove something says so; it never guesses a False.
2. No client-supplied path, cwd, pane, callback filename, executable or session name exists
   anywhere in a request model. Every target is an opaque server-issued handle (R9).
"""

from __future__ import annotations

import enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Plan §7: "Validate segments/NULs, 64 KiB text limit and realpath containment".
TEXT_MAX_BYTES = 64 * 1024

#: Opaque handles issued by handles.py. `s-` session, `o-` object. Charset is deliberately
#: narrow so a handle can never be a path fragment.
HANDLE_RE = r"^[so]-[0-9a-f]{8,64}$"
REQUEST_ID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

Handle = Annotated[str, Field(pattern=HANDLE_RE, max_length=80)]
RequestId = Annotated[str, Field(pattern=REQUEST_ID_RE)]


class Strict(BaseModel):
    """Reject unknown fields everywhere. A typo in a request body is an error, not a
    silently ignored precondition."""

    model_config = ConfigDict(extra="forbid", frozen=False)


# ── source freshness ─────────────────────────────────────────────────────────


class SourceState(str, enum.Enum):
    OK = "ok"
    STALE = "stale"                 # last-good retained; the current read failed or is old
    UNAVAILABLE = "unavailable"     # never read successfully
    OVERSIZED = "oversized"         # above the cap; explicitly NOT parsed
    MALFORMED = "malformed"         # present, readable, not parseable
    ABSENT = "absent"               # the file simply does not exist


class SourceStatus(Strict):
    key: str
    label: str
    state: SourceState
    detail: str = ""
    #: When this backend last *observed* the source. Never presented as an occurrence time.
    observed_at: str | None = None
    #: The source's own recorded timestamp, when it has one. Distinct from observed_at:
    #: plan §5 "Distinguish source heartbeat staleness from collection delay."
    source_at: str | None = None
    size_bytes: int | None = None
    read_limit_hit: bool = False


# ── identity ─────────────────────────────────────────────────────────────────


class LabelSource(str, enum.Enum):
    REGISTRY_ALIAS = "registry_alias"     # realpath(registry.path) == realpath(root)
    ROOT_BASENAME = "root_basename"


class SessionIdentity(Strict):
    key: Handle
    name: str
    root: str
    session_id: str                       # tmux `$N` — `%N` is a PANE id
    created: str                          # #{session_created}, epoch seconds, 1s resolution
    label: str
    label_source: LabelSource
    #: Repository metadata only. Explicitly NOT a claim about this physical root.
    repo_alias: str | None = None
    repo_id: str | None = None
    has_dev: bool = True


# ── stage projection ─────────────────────────────────────────────────────────


class PendingEntry(Strict):
    object_key: Handle
    slug: str
    stage: str
    worker: str | None = None
    timestamp: str
    parked_at: str | None = None
    parked_reason: str | None = None
    uncommitted: bool | None = None
    prompt_sha256: str | None = None


class StageFacts(Strict):
    """Every field is a separately labelled fact. Nothing here is a pipeline transition."""

    #: The web-derived field. Labelled "Session pending stage" in the UI because
    #: bin/forge-watch:909,916-927 keys closed_max_ts on (slug, stage) with NO owner binding,
    #: so this can legitimately disagree with the board and the menubar.
    session_pending_stage: str | None = None
    session_pending_source: str = ""
    current_pending: PendingEntry | None = None
    concurrent_pending: list[PendingEntry] = Field(default_factory=list)
    parked: list[PendingEntry] = Field(default_factory=list)
    #: "Running" requires supporting current worker/board evidence — never inferred from
    #: next_stage.
    running: bool = False
    running_evidence: str = ""
    #: Separate facts from the context file, never merged into the pending calculation.
    active_pipeline: str | None = None
    last_stage_completed: str | None = None
    last_stage_status: str | None = None
    last_worker: str | None = None
    next_stage_intent: str | None = None
    context_updated_at: str | None = None
    #: Board annotations, kept distinct and attributed.
    board_stages: list[str] = Field(default_factory=list)
    ambiguous: bool = False
    ambiguity_detail: str = ""
    status_excerpt: str | None = None


# ── asks ─────────────────────────────────────────────────────────────────────


class AskTextState(str, enum.Enum):
    FULL = "full"
    TRUNCATED = "truncated"
    LEGACY_EXCERPT = "legacy_excerpt"


class AskCard(Strict):
    object_key: Handle
    ask_id: str
    mode: str                              # "stage" | "session-scope"
    emitted_at: str
    slug: str | None = None
    stage: str | None = None
    worker: str | None = None
    snippet: str
    question_text: str | None = None
    text_state: AskTextState
    text_label: str
    saved_bytes: int | None = None
    full_redacted_bytes: int | None = None
    #: True only when the record's session equals this session AND emitted_at >= created.
    actionable: bool = True
    ownership_note: str = ""


# ── board ────────────────────────────────────────────────────────────────────


class BoardItem(Strict):
    incarnation: str | None = None
    condition: str
    row_class: str
    state: str | None = None
    msg: str
    slug: str | None = None
    stage: str | None = None
    acked: bool = False
    session: str | None = None
    root: str
    label: str | None = None


class BoardFreshness(Strict):
    """Preserved from the source, plus this backend's own collection facts. Six distinct
    numbers, because collapsing them is how "board unavailable" got shown for a healthy
    watcher (bin/forge-watch:331-352 exists for that reason)."""

    schema_name: str | None = None
    generated_at: str | None = None        # the watcher's own stamp
    heartbeat: str | None = None
    heartbeat_age_s: int | None = None
    stale: bool | None = None              # deliberately ABSENT on a busy payload
    busy: bool = False
    unavailable_reason: str | None = None
    exit_code: int | None = None
    #: This backend's facts, never conflated with the watcher's.
    last_success_at: str | None = None
    last_success_age_s: float | None = None
    last_attempt_at: str | None = None
    collection_state: Literal["ok", "busy", "stale", "unavailable", "suspended"] = "unavailable"
    next_attempt_in_s: float | None = None


# ── feed & responses ─────────────────────────────────────────────────────────


class FeedSource(str, enum.Enum):
    EVENT_LOG = "event_log"
    ATTENTION = "attention"


class FeedEntry(Strict):
    entry_id: str
    source: FeedSource
    kind: str
    #: ALWAYS an observation time. Plan §6: 'Label observation times "observed", never
    #: occurrence.' `occurred_at` is populated only from a field the writer itself stamped.
    observed_at: str
    occurred_at: str | None = None
    text: str
    slug: str | None = None
    stage: str | None = None
    session_proof: bool = False
    detail: dict[str, str] = Field(default_factory=dict)


class ResponseMetadataState(str, enum.Enum):
    MATCHED = "matched"                   # stop.<session>.json still names THIS dispatch
    LOST_OVERWRITTEN = "lost_overwritten" # a later turn replaced the LWW stop record
    NO_STOP_RECORD = "no_stop_record"


class ResponseCard(Strict):
    """R10. `bin/forge-cc-hook:452-460` writes the text to response.<dispatch-id>.txt but
    writes full_bytes/truncated/response_paths/response_dispatch_ids ONLY into the
    last-write-wins stop.<session>.json. The next turn overwrites that record, so an older
    payload can outlive its metadata. This model makes that loss explicit."""

    dispatch_id: str | None
    text: str
    #: None means UNKNOWN — the metadata was overwritten, not "not truncated".
    truncated: bool | None = None
    full_bytes: int | None = None
    metadata_state: ResponseMetadataState
    #: `full_bytes` is len(body.encode()) of the RAW body; `truncated` was decided on
    #: len(redact(body).encode()) > FORGE_CC_RESPONSE_MAX. They are NOT a before/after pair
    #: and the UI must never render them as "N of M".
    full_bytes_basis: Literal["raw_pre_redaction"] = "raw_pre_redaction"
    truncated_basis: Literal["redacted_post_redaction"] = "redacted_post_redaction"
    #: Independent of the two above: did OUR bounded read stop early?
    reader_limit_hit: bool = False
    reader_limit_bytes: int | None = None
    #: An absorbed dispatch has no dedicated file; the session reply is shown separately and
    #: attributed to the ids the stop record actually names.
    absorbed: bool = False
    attributed_dispatch_ids: list[str] = Field(default_factory=list)
    observed_at: str


# ── snapshots ────────────────────────────────────────────────────────────────


class SessionCard(Strict):
    identity: SessionIdentity
    objective_excerpt: str | None = None
    objective_source: str | None = None
    stage: StageFacts
    attention_count: int = 0
    open_ask_count: int = 0
    board_items: list[BoardItem] = Field(default_factory=list)
    sources: list[SourceStatus] = Field(default_factory=list)
    partial_startup: bool = False
    pane_count: int | None = None


class RootNotice(Strict):
    """A project-scoped observation with no owning live session. Never a session action."""

    root: str
    label: str
    detail: str
    board_items: list[BoardItem] = Field(default_factory=list)


class SpawnRoot(Strict):
    """A root offered in the spawn picker. The client selects a HANDLE, never a path."""

    key: Handle
    root: str
    label: str
    live: bool
    registered: bool


class FleetSnapshot(Strict):
    revision: int
    generated_at: str
    server_instance: str
    mutations_enabled: bool
    mutations_detail: str = ""
    tmux_state: Literal["ok", "unavailable"] = "ok"
    tmux_detail: str = ""
    board: BoardFreshness
    sessions: list[SessionCard] = Field(default_factory=list)
    root_notices: list[RootNotice] = Field(default_factory=list)
    spawn_roots: list[SpawnRoot] = Field(default_factory=list)


class SessionDetailView(Strict):
    revision: int
    generated_at: str
    identity: SessionIdentity
    objective_excerpt: str | None = None
    objective_source: str | None = None
    stage: StageFacts
    asks: list[AskCard] = Field(default_factory=list)
    board_items: list[BoardItem] = Field(default_factory=list)
    feed: list[FeedEntry] = Field(default_factory=list)
    feed_backlog: bool = False
    feed_backlog_detail: str = ""
    responses: list[ResponseCard] = Field(default_factory=list)
    sources: list[SourceStatus] = Field(default_factory=list)
    callbacks: list[PendingEntry] = Field(default_factory=list)
    #: Forward reference: AckScope is defined below, next to the other operation types, and
    #: the module ends with SessionDetailView.model_rebuild() to resolve it. Keeping the
    #: scope INSIDE the detail response is deliberate — plan §7 fixes the endpoint list, and
    #: a separate /ack-scope route would be a sixth GET the plan does not authorise.
    ack_scope: "AckScope | None" = None
    ack_scopes: dict[str, "AckScope"] = Field(default_factory=dict)


# ── requests ─────────────────────────────────────────────────────────────────


def _check_text(value: str) -> str:
    if "\x00" in value:
        raise ValueError("text contains a NUL byte")
    if len(value.encode("utf-8")) > TEXT_MAX_BYTES:
        raise ValueError("text exceeds %d bytes of UTF-8" % TEXT_MAX_BYTES)
    return value


class ActionEnvelope(Strict):
    """R9. Every mutation carries a client-generated request_id and the revision the
    operator was LOOKING AT. The revision is not a lock: it is the displayed state, and a
    mismatch is reported as a stale view rather than silently acting on newer truth."""

    expected_server_instance: str | None = None
    request_id: RequestId
    revision: int = Field(ge=0)


class MessageRequest(ActionEnvelope):
    text: str

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message text is empty")
        return _check_text(v)


class AnswerRequest(MessageRequest):
    ask_key: Handle


class ControlRequest(ActionEnvelope):
    """Pause / stop. No free text at all: the literal phrase is fixed server-side."""

    control: Literal["pause", "stop"]


class AckRequest(ActionEnvelope):
    #: "session" | "slug" | "root_basename" | "root". The VALUE is resolved server-side
    #: from the session identity; the client picks the breadth, never the string.
    scope: Literal["session", "slug", "root_basename", "root"]
    #: Optional pending handle for slug scope; absent selects the current active pipeline.
    slug_key: Handle | None = None


class ParkRequest(ActionEnvelope):
    pending_key: Handle
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        return _check_text(v)


class ResolveRequest(ActionEnvelope):
    pending_key: Handle
    note: str = ""

    @field_validator("note")
    @classmethod
    def _note(cls, v: str) -> str:
        return _check_text(v)


class SpawnRequest(ActionEnvelope):
    root_key: Handle
    name: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v or len(v) > 128:
            raise ValueError("name must be 1..128 characters")
        if any(c in v for c in "\x00/\\ \t\n"):
            raise ValueError("name may not contain NUL, path separators or whitespace")
        return v


# ── operations ───────────────────────────────────────────────────────────────


class OperationState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    EXITED = "exited"
    #: Server restarted, or the receipt aged out of the bound. The child may have completed.
    UNKNOWN = "unknown"
    #: Refused BEFORE any subprocess ran.
    REFUSED = "refused"


class OutcomeComponents(Strict):
    """Plan §7: preserve recorded/delivered/cleanup/partial components, never just rc."""

    recorded: bool | None = None
    delivered: bool | None = None
    cleanup: Literal["consumed", "noop", "failed", "not-applicable", "unknown"] | None = None
    lock_release_state: str | None = None
    finalize: Literal["ready", "deferred"] | None = None
    park_result: Literal["parked", "partial", "noop", "resolved"] | None = None
    spawn_result: Literal["spawned", "ensured", "partial", "plan"] | None = None
    ack_outcome: str | None = None
    ack_applied_count: int | None = None
    #: Only a delivery failure offers plain redispatch (plan §1).
    offers_redispatch: bool = False


class OperationReceipt(Strict):
    operation_id: str
    server_instance: str
    request_id: RequestId
    session_key: Handle | None = None
    action: str
    state: OperationState
    #: True when this POST reused an existing receipt rather than launching anything.
    duplicate: bool = False
    created_at: str
    finished_at: str | None = None
    rc: int | None = None
    summary: str = ""
    stdout: str = ""
    stderr: str = ""
    truncated_output: bool = False
    components: OutcomeComponents = Field(default_factory=OutcomeComponents)
    #: Carried verbatim into the UI so a restart is never rendered as success or failure.
    uncertainty: str = ""


class AckScopeRow(Strict):
    condition: str
    msg: str
    root: str
    session: str | None = None
    slug: str | None = None
    acked: bool = False
    in_fleet: bool = True


class AckScope(Strict):
    target: str
    target_kind: str
    visible: list[AckScopeRow] = Field(default_factory=list)
    outside_fleet_count: int = 0
    #: The preview is an ESTIMATE IN BOTH DIRECTIONS — ack rescans at ack time
    #: (bin/forge-watch:2340-2355), so a condition can APPEAR as well as disappear.
    #: UI copy must not say "up to N".
    estimate_note: str = (
        "Estimate only: ack rescans at ack time, so the applied count can be higher or "
        "lower than this preview."
    )
    available: bool = True
    unavailable_reason: str = ""


class Problem(Strict):
    """One error envelope for every refusal, so the UI never has to guess."""

    code: str
    detail: str
    revision: int | None = None
    #: Populated on 409 object_changed so the UI can re-point the operator at the new object.
    current_object_key: Handle | None = None


# AckScope is referenced by SessionDetailView above; resolve that forward reference now that
# every name in this module exists.
SessionDetailView.model_rebuild()