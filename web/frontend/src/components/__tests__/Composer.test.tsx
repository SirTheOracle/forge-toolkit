import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Composer from "../Composer";
import type { SessionDetailView } from "../../types";

const detail = {
  revision: 1, generated_at: "t",
  identity: { key: "s-1", name: "forge-x", root: "/p/a", session_id: "$4",
              created: "1700000000", label: "a", label_source: "root_basename",
              repo_alias: null, repo_id: null, has_dev: true },
  objective_excerpt: null, objective_source: null,
  stage: { session_pending_stage: null, session_pending_source: "", current_pending: null,
           concurrent_pending: [], parked: [], running: false, running_evidence: "",
           active_pipeline: null, last_stage_completed: null, last_stage_status: null,
           last_worker: null, next_stage_intent: null, context_updated_at: null,
           board_stages: [], ambiguous: false, ambiguity_detail: "", status_excerpt: null },
  asks: [], board_items: [], feed: [], feed_backlog: false, feed_backlog_detail: "",
  responses: [], sources: [], callbacks: [], ack_scope: null,
} as unknown as SessionDetailView;

function harness(draft: string, onDraftChange = vi.fn(), onAction = vi.fn(), busy = false) {
  return render(
    <Composer detail={detail} mutationsEnabled draft={draft}
              onDraftChange={onDraftChange} onAction={onAction} busy={busy} />,
  );
}

describe("composer", () => {
  it("keeps the draft across a re-render, which is what a 2 s poll is", () => {
    const { rerender } = harness("half typed");
    expect(screen.getByLabelText("message")).toHaveValue("half typed");
    rerender(
      <Composer detail={{ ...detail, revision: 2 }} mutationsEnabled draft="half typed"
                onDraftChange={vi.fn()} onAction={vi.fn()} busy={false} />,
    );
    expect(screen.getByLabelText("message")).toHaveValue("half typed");
  });

  it("sends the draft and does not clear it itself", async () => {
    const onAction = vi.fn();
    const onDraftChange = vi.fn();
    harness("hello", onDraftChange, onAction);
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onAction).toHaveBeenCalledWith("message", { text: "hello" });
    expect(onDraftChange).not.toHaveBeenCalled();
  });

  it("offers forge-resume through the composer and no Resume or Skip control", async () => {
    const onDraftChange = vi.fn();
    harness("", onDraftChange);
    expect(screen.queryByRole("button", { name: /^Resume$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Skip/i })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Insert forge-resume/ }));
    expect(onDraftChange).toHaveBeenCalledWith("forge-resume");
  });

  it("refuses to send past the 64 KiB byte limit, counting UTF-8 not characters", () => {
    harness("é".repeat(33000));                        // 66000 bytes, 33000 characters
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(screen.getByText(/66000 \/ 65536 bytes/)).toBeInTheDocument();
  });

  it("disables sending in read-only mode", () => {
    render(
      <Composer detail={detail} mutationsEnabled={false} draft="hi"
                onDraftChange={vi.fn()} onAction={vi.fn()} busy={false} />,
    );
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});