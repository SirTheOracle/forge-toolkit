import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Feed from "../Feed";
import SourceDrawer from "../SourceDrawer";
import type { FeedEntry, ResponseCard, SourceStatus } from "../../types";

const entry: FeedEntry = {
  entry_id: "e1", source: "event_log", kind: "STAGE", observed_at: "2026-09-14T10:00:05Z",
  occurred_at: "2026-09-14T10:00:00Z", text: "STAGE: pipeline=demo result_stage=coding",
  slug: "demo", stage: "coding", session_proof: true, detail: {},
};

function response(overrides: Partial<ResponseCard>): ResponseCard {
  return {
    dispatch_id: "cc-1", text: "the reply", truncated: null, full_bytes: null,
    metadata_state: "matched", full_bytes_basis: "raw_pre_redaction",
    truncated_basis: "redacted_post_redaction", reader_limit_hit: false,
    reader_limit_bytes: null, absorbed: false, attributed_dispatch_ids: ["cc-1"],
    observed_at: "2026-09-14T10:00:05Z",
    ...overrides,
  };
}

describe("feed", () => {
  it("labels times as observed and shows the writer stamp separately", () => {
    render(<Feed entries={[entry]} backlog={false} backlogDetail="" responses={[]} />);
    expect(screen.getByText(/observed 2026-09-14T10:00:05Z/)).toBeInTheDocument();
    expect(screen.getByText(/writer stamp 2026-09-14T10:00:00Z/)).toBeInTheDocument();
  });

  it("shows a backlog instead of pretending to be current", () => {
    render(<Feed entries={[]} backlog backlogDetail="behind by 900000 bytes" responses={[]} />);
    expect(screen.getByText(/behind by 900000 bytes/)).toBeInTheDocument();
  });

  it("says an empty feed is a tail start, not an absence of history", () => {
    render(<Feed entries={[]} backlog={false} backlogDetail="" responses={[]} />);
    expect(screen.getByText(/starts at the tail/)).toBeInTheDocument();
  });
});

describe("response metadata (R10)", () => {
  it("renders lost metadata as UNKNOWN, never as 'not truncated'", () => {
    render(
      <Feed entries={[]} backlog={false} backlogDetail=""
            responses={[response({ metadata_state: "lost_overwritten" })]} />,
    );
    expect(screen.getByText(/unknown/)).toBeInTheDocument();
    expect(screen.getByText(/overwrote the session's stop record/)).toBeInTheDocument();
  });

  it("never pairs the raw byte count with the redacted truncation decision", () => {
    const { container } = render(
      <Feed entries={[]} backlog={false} backlogDetail=""
            responses={[response({ truncated: true, full_bytes: 90000 })]} />,
    );
    expect(screen.getByText(/BEFORE redaction/)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\d+ of \d+ bytes/);
  });

  it("shows the reader limit as a separate fact from the writer's truncation", () => {
    render(
      <Feed entries={[]} backlog={false} backlogDetail=""
            responses={[response({ truncated: false, full_bytes: 10,
                                   reader_limit_hit: true, reader_limit_bytes: 262144 })]} />,
    );
    expect(screen.getByText(/This viewer stopped reading at 262144 bytes/)).toBeInTheDocument();
  });

  it("attributes an absorbed reply to the recorded ids and not to the unanswered one", () => {
    render(
      <Feed entries={[]} backlog={false} backlogDetail=""
            responses={[response({ dispatch_id: null, absorbed: true,
                                   attributed_dispatch_ids: ["cc-2"] })]} />,
    );
    expect(screen.getByText(/Session reply \(absorbed dispatch\)/)).toBeInTheDocument();
    expect(screen.getByText(/NOT a failed send/)).toBeInTheDocument();
  });
});

describe("source drawer", () => {
  const source: SourceStatus = {
    key: "s-1:status", label: ".dev/forge-status.forge-x.md", state: "ok", detail: "",
    observed_at: "2026-09-14T10:00:05Z", source_at: "2026-09-14T09:00:00Z",
    size_bytes: 20, read_limit_hit: false,
  };

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      "<script>alert(1)</script>\n# heading",
      { status: 200, headers: { "content-type": "text/plain" } },
    )));
  });

  it("renders source content escaped — markup is TEXT, never markup", async () => {
    const { container } = render(<SourceDrawer sessionKey="s-1" sources={[source]} />);
    await userEvent.click(screen.getByRole("button", { name: source.label }));
    await waitFor(() => expect(container.querySelector("pre")).not.toBeNull());
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("pre")!.textContent).toContain("<script>alert(1)</script>");
  });

  it("distinguishes observation time from the source's own stamp", () => {
    render(<SourceDrawer sessionKey="s-1" sources={[source]} />);
    expect(screen.getByText(/observed 2026-09-14T10:00:05Z/)).toBeInTheDocument();
    expect(screen.getByText(/source stamp 2026-09-14T09:00:00Z/)).toBeInTheDocument();
  });

  it("marks a stale or oversized source instead of rendering it as current", () => {
    render(
      <SourceDrawer sessionKey="s-1"
                    sources={[{ ...source, state: "oversized",
                                detail: "1200000 bytes exceeds the 1048576 byte cap" }]} />,
    );
    expect(screen.getByText(/oversized/)).toBeInTheDocument();
    expect(screen.getByText(/exceeds the 1048576 byte cap/)).toBeInTheDocument();
  });
});