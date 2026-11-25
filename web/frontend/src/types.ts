// Wire types, mirroring web/backend/src/forge_web/models.py field-for-field.
//
// EVERY TRI-STATE IS `| null`, NOT A DEFAULT. `truncated: boolean | null` means
// yes / no / the metadata was overwritten and cannot be recovered (backend R10). A UI that
// coerced null to false would tell the operator a clipped response is complete.

export type SourceState =
  | "ok" | "stale" | "unavailable" | "oversized" | "malformed" | "absent";

export interface SourceStatus {
  key: string;
  label: string;
  state: SourceState;
  detail: string;
  observed_at: string | null;
  source_at: string | null;
  size_bytes: number | null;
  read_limit_hit: boolean;
}

export interface SessionIdentity {
  key: string;
  name: string;
  root: string;
  session_id: string;
  created: string;
  label: string;
  label_source: "registry_alias" | "root_basename";
  repo_alias: string | null;
  repo_id: string | null;
  has_dev: boolean;
}

export interface PendingEntry {
  object_key: string;
  slug: string;
  stage: string;
  worker: string | null;
  timestamp: string;
  parked_at: string | null;
  parked_reason: string | null;
  uncommitted: boolean | null;
  prompt_sha256: string | null;
}

export interface StageFacts {
  session_pending_stage: string | null;
  session_pending_source: string;
  current_pending: PendingEntry | null;
  concurrent_pending: PendingEntry[];
  parked: PendingEntry[];
  running: boolean;
  running_evidence: string;
  active_pipeline: string | null;
  last_stage_completed: string | null;
  last_stage_status: string | null;
  last_worker: string | null;
  next_stage_intent: string | null;
  context_updated_at: string | null;
  board_stages: string[];
  ambiguous: boolean;
  ambiguity_detail: string;
  status_excerpt: string | null;
}

export interface AskCard {
  object_key: string;
  ask_id: string;
  mode: string;
  emitted_at: string;
  slug: string | null;
  stage: string | null;
  worker: string | null;
  snippet: string;
  question_text: string | null;
  text_state: "full" | "truncated" | "legacy_excerpt";
  text_label: string;
  saved_bytes: number | null;
  full_redacted_bytes: number | null;
  actionable: boolean;
  ownership_note: string;
}

export interface BoardItem {
  condition: string;
  row_class: string;
  state: string | null;
  msg: string;
  slug: string | null;
  stage: string | null;
  acked: boolean;
  session: string | null;
  root: string;
  label: string | null;
}

export interface BoardFreshness {
  schema_name: string | null;
  generated_at: string | null;
  heartbeat: string | null;
  heartbeat_age_s: number | null;
  stale: boolean | null;
  busy: boolean;
  unavailable_reason: string | null;
  exit_code: number | null;
  last_success_at: string | null;
  last_success_age_s: number | null;
  last_attempt_at: string | null;
  collection_state: "ok" | "busy" | "stale" | "unavailable" | "suspended";
  next_attempt_in_s: number | null;
}

export interface FeedEntry {
  entry_id: string;
  source: "event_log" | "attention";
  kind: string;
  observed_at: string;
  occurred_at: string | null;
  text: string;
  slug: string | null;
  stage: string | null;
  session_proof: boolean;
  detail: Record<string, string>;
}

export interface ResponseCard {
  dispatch_id: string | null;
  text: string;
  truncated: boolean | null;
  full_bytes: number | null;
  metadata_state: "matched" | "lost_overwritten" | "no_stop_record";
  full_bytes_basis: "raw_pre_redaction";
  truncated_basis: "redacted_post_redaction";
  reader_limit_hit: boolean;
  reader_limit_bytes: number | null;
  absorbed: boolean;
  attributed_dispatch_ids: string[];
  observed_at: string;
}

export interface AckScopeRow {
  condition: string;
  msg: string;
  root: string;
  session: string | null;
  slug: string | null;
  acked: boolean;
  in_fleet: boolean;
}

export interface AckScope {
  target: string;
  target_kind: string;
  visible: AckScopeRow[];
  outside_fleet_count: number;
  estimate_note: string;
  available: boolean;
  unavailable_reason: string;
}

export interface SessionCard {
  identity: SessionIdentity;
  objective_excerpt: string | null;
  objective_source: string | null;
  stage: StageFacts;
  attention_count: number;
  open_ask_count: number;
  board_items: BoardItem[];
  sources: SourceStatus[];
  partial_startup: boolean;
  pane_count: number | null;
}

export interface RootNotice {
  root: string;
  label: string;
  detail: string;
  board_items: BoardItem[];
}

export interface SpawnRoot {
  key: string;
  root: string;
  label: string;
  live: boolean;
  registered: boolean;
}

export interface FleetSnapshot {
  revision: number;
  generated_at: string;
  server_instance: string;
  mutations_enabled: boolean;
  mutations_detail: string;
  tmux_state: "ok" | "unavailable";
  tmux_detail: string;
  board: BoardFreshness;
  sessions: SessionCard[];
  root_notices: RootNotice[];
  spawn_roots: SpawnRoot[];
}

export interface SessionDetailView {
  revision: number;
  generated_at: string;
  identity: SessionIdentity;
  objective_excerpt: string | null;
  objective_source: string | null;
  stage: StageFacts;
  asks: AskCard[];
  board_items: BoardItem[];
  feed: FeedEntry[];
  feed_backlog: boolean;
  feed_backlog_detail: string;
  responses: ResponseCard[];
  sources: SourceStatus[];
  callbacks: PendingEntry[];
  ack_scope: AckScope | null;
  ack_scopes?: Record<string, AckScope>;
}

export interface OutcomeComponents {
  recorded: boolean | null;
  delivered: boolean | null;
  cleanup: "consumed" | "noop" | "failed" | "not-applicable" | "unknown" | null;
  lock_release_state: string | null;
  finalize: "ready" | "deferred" | null;
  park_result: "parked" | "partial" | "noop" | "resolved" | null;
  spawn_result: "spawned" | "ensured" | "partial" | "plan" | null;
  ack_outcome: string | null;
  ack_applied_count: number | null;
  offers_redispatch: boolean;
}

export interface OperationReceipt {
  operation_id: string;
  server_instance: string;
  request_id: string;
  session_key: string | null;
  action: string;
  state: "queued" | "running" | "exited" | "unknown" | "refused";
  duplicate: boolean;
  created_at: string;
  finished_at: string | null;
  rc: number | null;
  summary: string;
  stdout: string;
  stderr: string;
  truncated_output: boolean;
  components: OutcomeComponents;
  uncertainty: string;
}

export interface Problem {
  code: string;
  detail: string;
  revision: number | null;
  current_object_key: string | null;
}