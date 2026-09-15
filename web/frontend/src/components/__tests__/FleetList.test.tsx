import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import FleetList, { orderSessions } from "../FleetList";
import type { FleetSnapshot, SessionCard } from "../../types";

function card(key: string, overrides: Partial<SessionCard> = {}): SessionCard {
  return {
    identity: {
      key, name: `n-${key}`, root: `/p/${key}`, session_id: "$1", created: "1700000000",
      label: key, label_source: "root_basename", repo_alias: null, repo_id: null, has_dev: true,
    },
    objective_excerpt: null, objective_source: null,
    stage: {
      session_pending_stage: null, session_pending_source: "", current_pending: null,
      concurrent_pending: [], parked: [], running: false, running_evidence: "",
      active_pipeline: null, last_stage_completed: null, last_stage_status: null,
      last_worker: null, next_stage_intent: null, context_updated_at: null,
      board_stages: [], ambiguous: false, ambiguity_detail: "", status_excerpt: null,
    },
    attention_count: 0, open_ask_count: 0, board_items: [], sources: [],
    partial_startup: false, pane_count: null,
    ...overrides,
  };
}

const hot = { condition: "NEEDS-ASK", row_class: "hot", state: "needs-input", msg: "m",
              slug: null, stage: null, acked: false, session: null, root: "/p/a", label: "a" };

function snapshot(sessions: SessionCard[]): FleetSnapshot {
  return {
    revision: 1, generated_at: "t", server_instance: "i", mutations_enabled: true,
    mutations_detail: "", tmux_state: "ok", tmux_detail: "",
    board: {
      schema_name: "cc-board/1", generated_at: null, heartbeat: null, heartbeat_age_s: null,
      stale: null, busy: false, unavailable_reason: null, exit_code: 0, last_success_at: null,
      last_success_age_s: null, last_attempt_at: null, collection_state: "ok",
      next_attempt_in_s: null,
    },
    sessions, root_notices: [], spawn_roots: [],
  };
}

describe("fleet ordering", () => {
  it("puts unacked hot rows first and open asks next", () => {
    const ordered = orderSessions([
      card("c"),
      card("b", { open_ask_count: 1 }),
      card("a", { board_items: [hot] }),
    ]);
    expect(ordered.map((s) => s.identity.key)).toEqual(["a", "b", "c"]);
  });

  it("is stable, so equal-priority rows do not reshuffle under the cursor on a poll", () => {
    const input = [card("m"), card("z"), card("a")];
    const first = orderSessions(input).map((s) => s.identity.key);
    const second = orderSessions(orderSessions(input)).map((s) => s.identity.key);
    expect(second).toEqual(first);
  });

  it("does not order by a mutable label", () => {
    const relabelled = [card("m"), card("z")].map((s) => ({
      ...s,
      identity: { ...s.identity, label: "zzz" },
    }));
    expect(orderSessions(relabelled).map((s) => s.identity.key)).toEqual(["m", "z"]);
  });
});

describe("deliberate states", () => {
  it("renders an empty fleet distinctly from an unknown one", () => {
    const { rerender } = render(
      <FleetList fleet={snapshot([])} selected={null} onSelect={vi.fn()} />,
    );
    expect(screen.getByText(/No live Forge sessions/)).toBeInTheDocument();
    rerender(
      <FleetList
        fleet={{ ...snapshot([]), tmux_state: "unavailable" }}
        selected={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText(/Fleet unknown/)).toBeInTheDocument();
  });

  it("marks a partial startup", () => {
    render(
      <FleetList fleet={snapshot([card("a", { partial_startup: true })])}
                 selected={null} onSelect={vi.fn()} />,
    );
    expect(screen.getByText(/partial startup/)).toBeInTheDocument();
  });

  it("shows a repository alias as metadata, never as the worktree label", () => {
    render(
      <FleetList
        fleet={snapshot([
          card("a", {
            identity: { ...card("a").identity, label: "sibling-worktree",
                        label_source: "root_basename", repo_alias: "myrepo" },
          }),
        ])}
        selected={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText(/sibling-worktree/)).toBeInTheDocument();
    expect(screen.getByText(/repo: myrepo/)).toBeInTheDocument();
  });

  it("renders a root notice without offering a session action", () => {
    render(
      <FleetList
        fleet={{ ...snapshot([]), root_notices: [{ root: "/p/orphan", label: "orphan",
                                                   detail: "Project-scoped evidence only",
                                                   board_items: [] }] }}
        selected={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText(/orphan \(project evidence\)/)).toBeInTheDocument();
    expect(screen.queryByRole("option")).toBeNull();
  });
});