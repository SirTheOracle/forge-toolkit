import type { OperationReceipt, SessionDetailView } from "../types";
import type { ActionBody } from "../api";
import ActionCards from "./ActionCards";
import Composer from "./Composer";
import Feed from "./Feed";
import SourceDrawer from "./SourceDrawer";

interface Props {
  detail: SessionDetailView;
  mutationsEnabled: boolean;
  draft: string;
  draftTick: number;
  onDraftChange: (value: string) => void;
  onAction: (action: string, body: Omit<ActionBody, "request_id" | "revision">) => void;
  busy: boolean;
  receipt: OperationReceipt | null;
  problem: string | null;
}

export default function SessionDetail(props: Props) {
  const { detail } = props;
  const stage = detail.stage;
  return (
    <>
      <div className="card">
        <h3>
          {detail.identity.name} · {detail.identity.session_id}
        </h3>
        <dl className="fact">
          <dt>Physical root</dt>
          <dd>{detail.identity.root}</dd>
          <dt>Worktree label</dt>
          <dd>
            {detail.identity.label}{" "}
            <span className="provenance">
              ({detail.identity.label_source === "registry_alias"
                ? "registry alias — exact path match"
                : "root basename"})
            </span>
          </dd>
          {detail.identity.repo_alias && detail.identity.label_source === "root_basename" && (
            <>
              <dt>Repository</dt>
              <dd>
                {detail.identity.repo_alias}{" "}
                <span className="provenance">
                  repository metadata; it does NOT name this worktree
                </span>
              </dd>
            </>
          )}
          <dt>Created</dt>
          <dd>{detail.identity.created}</dd>
        </dl>
      </div>

      <div className="card">
        <h3>Objective</h3>
        {detail.objective_excerpt ? (
          <>
            <p>{detail.objective_excerpt}</p>
            <p className="provenance">{detail.objective_source}</p>
          </>
        ) : (
          <p className="unknown">
            No problem statement excerpt available. {detail.objective_source ?? ""}
          </p>
        )}
      </div>

      <div className="card">
        <h3>Stage facts</h3>
        <dl className="fact">
          <dt>Active slug</dt><dd>{stage.active_pipeline ?? "—"}</dd>
          <dt>Session pending stage</dt>
          <dd>
            {stage.session_pending_stage ?? <span className="unknown">unknown</span>}
            {stage.running ? " · running" : " · not shown as running"}
          </dd>
          <dt>Running evidence</dt>
          <dd className="provenance">{stage.running_evidence || "none"}</dd>
          <dt>Board stage tags</dt>
          <dd>
            {stage.board_stages.length > 0 ? stage.board_stages.join(", ") : (
              <span className="unknown">none</span>
            )}
          </dd>
          <dt>Last completed</dt>
          <dd>
            {stage.last_stage_completed ?? "—"}
            {stage.last_stage_status ? ` (${stage.last_stage_status})` : ""}
          </dd>
          <dt>Next-stage intent</dt>
          <dd>
            {stage.next_stage_intent ?? "—"}{" "}
            <span className="provenance">an intent, not a running stage</span>
          </dd>
          <dt>Concurrent pending</dt>
          <dd>
            {stage.concurrent_pending.length === 0
              ? "—"
              : stage.concurrent_pending.map((p) => `${p.slug}/${p.stage}`).join(", ")}
          </dd>
          <dt>Parked</dt>
          <dd>
            {stage.parked.length === 0
              ? "—"
              : stage.parked.map((p) => `${p.slug}/${p.stage}`).join(", ")}
          </dd>
        </dl>
        <p className="provenance">{stage.session_pending_source}</p>
        {stage.status_excerpt && <pre>{stage.status_excerpt}</pre>}
        {stage.ambiguous && (
          <>
            <p className="stale">Ambiguous ownership — session stage is unknown.</p>
            <p className="provenance">{stage.ambiguity_detail}</p>
          </>
        )}
      </div>

      <ActionCards {...props} />
      <Composer {...props} />
      <Feed
        entries={detail.feed}
        backlog={detail.feed_backlog}
        backlogDetail={detail.feed_backlog_detail}
        responses={detail.responses}
      />
      <SourceDrawer sessionKey={detail.identity.key} sources={detail.sources} />
    </>
  );
}