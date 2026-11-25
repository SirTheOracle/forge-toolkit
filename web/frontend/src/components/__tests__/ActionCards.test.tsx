import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ActionCards from "../ActionCards";
import type { AckScope, BoardItem, OperationReceipt, SessionDetailView } from "../../types";

const base = {
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

const scope: AckScope = {
  target: "forge-x", target_kind: "session",
  visible: [{ condition: "NEEDS-ASK", msg: "asked", root: "/p/a", session: "forge-x",
              slug: "s1", acked: false, in_fleet: true }],
  outside_fleet_count: 3,
  estimate_note: "Estimate only: ack rescans at ack time, so the applied count can be higher "
    + "or lower than this preview.",
  available: true, unavailable_reason: "",
};

function show(detail: Partial<SessionDetailView>, receipt: OperationReceipt | null = null) {
  return render(
    <ActionCards detail={{ ...base, ...detail } as SessionDetailView} mutationsEnabled
                 onAction={vi.fn()} busy={false} receipt={receipt} problem={null} />,
  );
}

describe("terminal-required", () => {
  it("renders a native permission prompt as terminal-required with NO answer control", () => {
    const row: BoardItem = { condition: "NEEDS-PERMISSION", row_class: "hot",
                             state: "needs-input", msg: "allow Bash(rm)?", slug: null,
                             stage: null, acked: false, session: "forge-x", root: "/p/a",
                             label: "a" };
    show({ board_items: [row] });
    expect(screen.getByText("Terminal required")).toBeInTheDocument();
    expect(screen.getByText(/not a Forge ask/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Answer" })).toBeNull();
    expect(screen.queryByLabelText(/^answer /)).toBeNull();
  });
});

describe("ack", () => {
  it("advertises the scope and the outside-fleet aggregate", () => {
    show({ ack_scope: scope });
    expect(screen.getByText(/NEEDS-ASK — asked/)).toBeInTheDocument();
    expect(screen.getByText(/3 matching condition\(s\) outside this fleet/)).toBeInTheDocument();
  });

  it("never says 'up to N' — the estimate runs in BOTH directions", () => {
    const { container } = show({ ack_scope: scope });
    expect(screen.getByText(/higher or lower/)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/up to \d/);
  });

  it("is disabled with a reason when no scope is available", () => {
    show({ ack_scope: { ...scope, available: false,
                        unavailable_reason: "state.lock held by another run" } });
    expect(screen.getByText(/Ack is disabled/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Acknowledge/ })).toBeNull();
  });
});

describe("asks", () => {
  const ask = {
    object_key: "o-" + "a".repeat(32), ask_id: "ask-1", mode: "stage",
    emitted_at: "2026-09-14T10:00:00Z", slug: "s", stage: "coding", worker: "codex-a",
    snippet: "Which option?", question_text: "Which option?\n1. a\n2. b",
    text_state: "full" as const, text_label: "Saved question", saved_bytes: 20,
    full_redacted_bytes: 20, actionable: true, ownership_note: "",
  };

  it("offers an answer control for an actionable ask", () => {
    show({ asks: [ask] });
    expect(screen.getByLabelText("answer ask-1")).toBeInTheDocument();
  });

  it("shows a wrong-session ask as evidence with no answer control", () => {
    show({ asks: [{ ...ask, actionable: false,
                    ownership_note: "This ask belongs to session 'forge-y', not 'forge-x'." }] });
    expect(screen.getByText(/belongs to session 'forge-y'/)).toBeInTheDocument();
    expect(screen.queryByLabelText("answer ask-1")).toBeNull();
  });

  it("labels a truncated question and never implies the remainder exists", () => {
    show({ asks: [{ ...ask, text_state: "truncated" as const,
                    text_label: "Saved question truncated (65536 of 90000 redacted bytes saved)" }] });
    expect(screen.getByText(/Saved question truncated/)).toBeInTheDocument();
    expect(screen.getByText(/never retained and is not/)).toBeInTheDocument();
  });
});

describe("receipts", () => {
  function receipt(overrides: Partial<OperationReceipt>): OperationReceipt {
    return {
      operation_id: "i:1", server_instance: "i",
      request_id: "11111111-1111-4111-a111-111111111111", session_key: "s-1",
      action: "answer", state: "exited", duplicate: false, created_at: "t",
      finished_at: "t", rc: 0, summary: "", stdout: "", stderr: "", truncated_output: false,
      components: { recorded: null, delivered: null, cleanup: null, lock_release_state: null,
                    finalize: null, park_result: null, spawn_result: null, ack_outcome: null,
                    ack_applied_count: null, offers_redispatch: false },
      uncertainty: "",
      ...overrides,
    };
  }

  it("shows delivered and cleanup as separate components", () => {
    show({}, receipt({
      summary: "Answer delivered; callback cleanup FAILED.",
      components: { ...receipt({}).components, delivered: true, cleanup: "failed" },
    }));
    expect(screen.getByText("Delivered")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.queryByText(/Re-deliver with a PLAIN message/)).toBeNull();
  });

  it("offers a plain redispatch ONLY on a delivery failure", () => {
    show({}, receipt({
      components: { ...receipt({}).components, delivered: false, offers_redispatch: true },
    }));
    expect(screen.getByText(/Re-deliver with a PLAIN message/)).toBeInTheDocument();
  });

  it("surfaces restart uncertainty rather than a success or failure verdict", () => {
    show({}, receipt({ state: "unknown", uncertainty: "The server restarted." }));
    expect(screen.getByText("The server restarted.")).toBeInTheDocument();
  });

  it("says a duplicate request launched nothing twice", () => {
    show({}, receipt({ duplicate: true }));
    expect(screen.getByText(/nothing was launched twice/)).toBeInTheDocument();
  });
});