import type { ActionBody } from "../api";
import type { SessionDetailView } from "../types";

const TEXT_MAX_BYTES = 64 * 1024;

interface Props {
  detail: SessionDetailView;
  mutationsEnabled: boolean;
  draft: string;
  onDraftChange: (value: string) => void;
  onAction: (action: string, body: Omit<ActionBody, "request_id" | "revision">) => void;
  busy: boolean;
}

export default function Composer({
  detail,
  mutationsEnabled,
  draft,
  onDraftChange,
  onAction,
  busy,
}: Props) {
  const bytes = new TextEncoder().encode(draft).length;
  const tooLong = bytes > TEXT_MAX_BYTES;
  return (
    <div className="card">
      <h3>Message pane 0 of {detail.identity.name}</h3>
      <textarea
        aria-label="message"
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        placeholder="Free text to the orchestrator. `forge-resume` resumes a paused pipeline."
      />
      <div className="actions">
        <button
          disabled={busy || !mutationsEnabled || !draft.trim() || tooLong}
          onClick={() => onAction("message", { text: draft })}
        >
          Send
        </button>
        <button
          disabled={busy || !mutationsEnabled}
          onClick={() => onDraftChange("forge-resume")}
        >
          Insert forge-resume
        </button>
        <span className={tooLong ? "bad" : "provenance"}>
          {bytes} / {TEXT_MAX_BYTES} bytes
        </span>
      </div>
      <p className="provenance">
        Every browser message goes to pane 0 (the orchestrator) with sender=seat. The draft
        survives the 2 s poll and any error — it is only cleared when you clear it.
      </p>
    </div>
  );
}