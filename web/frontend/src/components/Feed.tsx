import type { FeedEntry, ResponseCard } from "../types";

function ResponseBlock({ card }: { card: ResponseCard }) {
  return (
    <div className="card">
      <h3>
        {card.absorbed
          ? "Session reply (absorbed dispatch)"
          : `Response to ${card.dispatch_id}`}
      </h3>
      {card.absorbed && (
        <p className="provenance">
          This dispatch has no dedicated response file; the turn recorded its reply against{" "}
          {card.attributed_dispatch_ids.join(", ") || "no dispatch id"}. Missing text here is
          NOT a failed send.
        </p>
      )}
      {/* Text node, never innerHTML. */}
      <pre>{card.text}</pre>
      {/* R10: the three facts are SEPARATE and never combined into "N of M". */}
      {card.metadata_state === "matched" ? (
        <p className="provenance">
          Writer truncation: {card.truncated === null ? "unknown" : card.truncated ? "yes" : "no"}
          {card.full_bytes !== null && (
            <> · raw body was {card.full_bytes} bytes BEFORE redaction</>
          )}{" "}
          — the truncation decision was made on the REDACTED bytes, so these two are not a
          before/after pair.
        </p>
      ) : (
        <p className="stale">
          Truncation and size are <strong>unknown</strong> for this response:{" "}
          {card.metadata_state === "lost_overwritten"
            ? "a later turn overwrote the session's stop record, which is the only place that metadata is written."
            : "no stop record was found for this session."}
        </p>
      )}
      {card.reader_limit_hit && (
        <p className="stale">
          This viewer stopped reading at {card.reader_limit_bytes} bytes — a separate limit
          from the writer's.
        </p>
      )}
    </div>
  );
}

interface Props {
  entries: FeedEntry[];
  backlog: boolean;
  backlogDetail: string;
  responses: ResponseCard[];
}

export default function Feed({ entries, backlog, backlogDetail, responses }: Props) {
  return (
    <>
      {responses.filter(card => !entries.some(entry => entry.detail.dispatch_id === card.dispatch_id)).map((card, index) => (
        <ResponseBlock key={`${card.dispatch_id ?? "session"}-${index}`} card={card} />
      ))}
      <div className="card">
        <h3>Feed</h3>
        {backlog && <p className="stale">Behind: {backlogDetail}</p>}
        {entries.length === 0 && (
          <p className="unknown">
            Nothing observed yet for this session. The event cursor starts at the tail of the
            log, so activity from before this server started is not shown here.
          </p>
        )}
        <ul className="feed">
          {entries.map((entry) => (
            <li key={entry.entry_id}>
              {/* "observed", never "occurred": these are OUR read times unless the writer
                  stamped one itself. */}
              <span className="when">observed {entry.observed_at}</span>
              <strong>{entry.kind}</strong> {entry.text}
              {responses.filter(r => r.dispatch_id && r.dispatch_id === entry.detail.dispatch_id).map(r => <ResponseBlock key={r.dispatch_id!} card={r} />)}
              {entry.occurred_at && (
                <span className="provenance"> · writer stamp {entry.occurred_at}</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}