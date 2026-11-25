import type { FleetSnapshot, SessionCard } from "../types";

/**
 * STABLE SORT, ATTENTION FIRST. `Array.prototype.sort` is stable per the ECMAScript spec, so
 * equal-priority rows keep their previous relative order — that is what stops the list from
 * reshuffling under the operator's cursor on every 2 s poll. The tiebreakers are session KEY,
 * never a mutable label, for the same reason.
 */
export function orderSessions(sessions: SessionCard[]): SessionCard[] {
  const rank = (card: SessionCard) => {
    if (card.board_items.some((item) => item.row_class === "hot" && !item.acked)) return 0;
    if (card.open_ask_count > 0) return 1;
    if (card.board_items.some((item) => item.row_class === "active")) return 2;
    return 3;
  };
  return [...sessions].sort((a, b) => {
    const diff = rank(a) - rank(b);
    if (diff !== 0) return diff;
    return a.identity.key < b.identity.key ? -1 : a.identity.key > b.identity.key ? 1 : 0;
  });
}

interface Props {
  fleet: FleetSnapshot | null;
  selected: string | null;
  onSelect: (key: string) => void;
}

export default function FleetList({ fleet, selected, onSelect }: Props) {
  if (!fleet) {
    return <p className="unknown">Loading…</p>;
  }
  return (
    <>
      {fleet.sessions.length === 0 && <p>{fleet.tmux_state === "ok" ? "No live Forge sessions." : "Fleet unknown — tmux could not be queried."}</p>}
      {orderSessions(fleet.sessions).map((card) => (
        <button
          key={card.identity.key}
          className="session"
          aria-pressed={card.identity.key === selected}
          onClick={() => onSelect(card.identity.key)}
        >
          <div className="name">
            {card.identity.name}
            {card.attention_count > 0 && (
              <span className="attention"> · {card.attention_count} needs you</span>
            )}
          </div>
          <div className="path">
            {card.identity.label}
            {card.identity.label_source === "root_basename" && card.identity.repo_alias && (
              <> · repo: {card.identity.repo_alias}</>
            )}
          </div>
          <div className="path">{card.identity.root}</div>
          <div>{card.stage.active_pipeline ?? "No active slug"}</div>
          <p>{card.objective_excerpt ?? "No objective excerpt available"}</p>
          <div className="path">
            {card.identity.session_id} · stage:{" "}
            {card.stage.session_pending_stage ?? <span className="unknown">unknown</span>}
            {card.partial_startup && <span className="stale"> · partial startup</span>}
          </div>
        </button>
      ))}
      {fleet.root_notices.map((notice) => (
        <div key={notice.root} className="session" aria-disabled="true">
          <div className="name stale">{notice.label} (project evidence)</div>
          <div className="path">{notice.detail}</div>
        </div>
      ))}
    </>
  );
}