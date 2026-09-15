import { useState } from "react";
import type { ActionBody } from "../api";
import type { OperationReceipt, SessionDetailView } from "../types";

/** A native permission/trust selector cannot be answered by a pane-0 dispatch. */
const TERMINAL_REQUIRED = new Set(["NEEDS-PERMISSION"]);

interface Props {
  detail: SessionDetailView;
  mutationsEnabled: boolean;
  onAction: (action: string, body: Omit<ActionBody, "request_id" | "revision">) => void;
  busy: boolean;
  receipt: OperationReceipt | null;
  problem: string | null;
}

export function Receipt({ receipt }: { receipt: OperationReceipt }) {
  const c = receipt.components;
  return (
    <div className="card">
      <h3>
        {receipt.action} · {receipt.state}
        {receipt.duplicate && " · duplicate request (nothing was launched twice)"}
      </h3>
      <p>{receipt.summary}</p>
      <dl className="fact">
        {c.delivered !== null && (
          <>
            <dt>Delivered</dt>
            <dd className={c.delivered ? "good" : "bad"}>{String(c.delivered)}</dd>
          </>
        )}
        {c.cleanup && (
          <>
            <dt>Callback cleanup</dt>
            <dd className={c.cleanup === "failed" ? "bad" : ""}>{c.cleanup}</dd>
          </>
        )}
        {c.lock_release_state && (
          <>
            <dt>Infra lock release</dt>
            <dd>{c.lock_release_state}</dd>
          </>
        )}
        {c.finalize && (
          <>
            <dt>Finalize</dt>
            <dd>{c.finalize}</dd>
          </>
        )}
        {c.ack_outcome && (
          <>
            <dt>Ack outcome</dt>
            <dd className={c.ack_outcome === "applied" ? "good" : "stale"}>
              {c.ack_outcome}
              {c.ack_applied_count !== null && ` · ${c.ack_applied_count} applied`}
            </dd>
          </>
        )}
        {c.spawn_result && (
          <>
            <dt>Spawn</dt>
            <dd>{c.spawn_result}</dd>
          </>
        )}
      </dl>
      {c.offers_redispatch && (
        <p className="bad">
          Delivery failed after the answer was recorded. Re-deliver with a PLAIN message — do
          not answer the ask again; it is already archived.
        </p>
      )}
      {receipt.uncertainty && <p className="stale">{receipt.uncertainty}</p>}
      {receipt.stdout && <pre>{receipt.stdout}</pre>}
      {receipt.stderr && <pre>{receipt.stderr}</pre>}
    </div>
  );
}

export default function ActionCards(props: Props) {
  const { detail, mutationsEnabled, onAction, busy, receipt, problem } = props;
  const draftKey = `forge-web.action-drafts:${detail.identity.key}`;
  const [answers, setAnswerState] = useState<Record<string, string>>(() => {
    try { return JSON.parse(window.sessionStorage.getItem(draftKey) || "{}"); } catch { return {}; }
  });
  const setAnswers = (v: Record<string, string>) => { setAnswerState(v); window.sessionStorage.setItem(draftKey, JSON.stringify(v)); };
  const [scope, setScope] = useState<"session" | "slug" | "root_basename" | "root">("session");
  const disabled = busy || !mutationsEnabled;

  const terminalRows = detail.board_items.filter((row) => TERMINAL_REQUIRED.has(row.condition));
  const ack = detail.ack_scopes?.[scope] ?? (scope === "session" ? detail.ack_scope : null);

  return (
    <>
      {problem && <p className="bad">{problem}</p>}
      {receipt && <Receipt receipt={receipt} />}

      {terminalRows.map((row) => (
        <div className="card terminal-required" key={row.condition + row.msg}>
          <h3>Terminal required</h3>
          <p>{row.msg}</p>
          <p className="provenance">
            This is a native permission or trust selector in the pane, not a Forge ask. It
            cannot be answered from the browser and no answer control is offered. Attach to{" "}
            {detail.identity.name} in tmux. An ordinary message to pane 0 is still available
            below, but it is not modal approval.
          </p>
        </div>
      ))}

      {detail.asks.map((ask) => (
        <div className="card" key={ask.object_key}>
          <h3>
            Ask {ask.ask_id} · {ask.mode}
          </h3>
          <p className="provenance">{ask.text_label}</p>
          <pre>{ask.question_text ?? ask.snippet}</pre>
          {ask.text_state === "truncated" && (
            <p className="stale">
              The saved question was clipped; the remainder was never retained and is not
              generated here.
            </p>
          )}
          {!ask.actionable ? (
            <p className="stale">{ask.ownership_note}</p>
          ) : (
            <>
              <textarea
                aria-label={`answer ${ask.ask_id}`}
                value={answers[ask.object_key] ?? ""}
                onChange={(event) =>
                  setAnswers({ ...answers, [ask.object_key]: event.target.value })
                }
              />
              <div className="actions">
                <button
                  disabled={disabled || !(answers[ask.object_key] ?? "").trim()}
                  onClick={() =>
                    onAction("answer", {
                      ask_key: ask.object_key,
                      text: answers[ask.object_key] ?? "",
                    })
                  }
                >
                  Answer
                </button>
              </div>
            </>
          )}
        </div>
      ))}

      {[...(detail.stage.current_pending ? [detail.stage.current_pending] : []),
         ...detail.stage.concurrent_pending, ...detail.stage.parked].map(entry => (
        <div className="card" key={entry.object_key}>
          <h3>{entry.parked_at ? "Resolve parked" : "Park pending"} · {entry.slug}/{entry.stage}</h3>
          <textarea aria-label={`note ${entry.slug}/${entry.stage}`}
            value={answers[entry.object_key] ?? ""}
            onChange={e => setAnswers({ ...answers, [entry.object_key]: e.target.value })} />
          <button disabled={disabled || (!entry.parked_at && !answers[entry.object_key]?.trim())}
            onClick={() => onAction(entry.parked_at ? "resolve" : "park", {
              pending_key: entry.object_key,
              [entry.parked_at ? "note" : "reason"]: answers[entry.object_key] ?? "",
            })}>{entry.parked_at ? "Resolve" : "Park"}</button>
          <p className="provenance">Selected pending identity and displayed callback bytes are revalidated before mutation.</p>
        </div>
      ))}

      <div className="card">
        <h3>Acknowledge</h3>
        <label>Scope <select aria-label="Ack scope" value={scope} onChange={e => setScope(e.target.value as typeof scope)}>
          <option value="session">this session</option><option value="slug">active pipeline</option><option value="root_basename">this root's basename</option><option value="root">this full root</option>
        </select></label>
        {!ack || !ack.available ? (
          <p className="stale">
            Ack is disabled: {ack?.unavailable_reason ?? "no board scope is available"}.
          </p>
        ) : (
          <>
            <ul className="feed">
              {ack.visible.map((row) => (
                <li key={row.condition + row.msg}>
                  {row.condition} — {row.msg}
                  {row.acked && <span className="provenance"> · already acked</span>}
                </li>
              ))}
            </ul>
            {ack.outside_fleet_count > 0 && (
              <p className="stale">
                Plus {ack.outside_fleet_count} matching condition(s) outside this fleet.
              </p>
            )}
            {/* NOT "up to N": the count can go UP as well as down. */}
            <p className="provenance">{ack.estimate_note}</p>
            <div className="actions">
              <button disabled={disabled} onClick={() => onAction("ack", { scope })}>
                Acknowledge {ack.target}
              </button>
            </div>
          </>
        )}
      </div>

      <div className="card">
        <h3>Control</h3>
        <div className="actions">
          <button disabled={disabled} onClick={() => onAction("pause", { control: "pause" })}>
            Pause
          </button>
          <button
            className="danger"
            disabled={disabled}
            onClick={() => onAction("stop", { control: "stop" })}
          >
            Stop
          </button>
        </div>
        <p className="provenance">
          Both send a fixed literal phrase to pane 0 and are REQUESTS. Stop does not kill tmux
          or any running worker. Resume with <code>forge-resume</code> through the composer
          below — there is no Resume or Skip control, and nothing here can bypass verify.
        </p>
      </div>
    </>
  );
}