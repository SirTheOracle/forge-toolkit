import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiProblem, getFleet, getSession, getOperation, newRequestId, postAction, postSpawn } from "./api";
import type { ActionBody } from "./api";
import type { OperationReceipt } from "./types";
import FleetList from "./components/FleetList";
import SessionDetail from "./components/SessionDetail";
import { Receipt } from "./components/ActionCards";

interface Intent {
  owner: string; ownerLabel?: string; action: string; body: ActionBody; instance: string;
  receipt?: OperationReceipt; error?: string; retry?: boolean; uncertain?: boolean;
}
const STORE = "forge-web.intents";
const pending = (i: Intent) => !i.uncertain && (!i.receipt || ["queued", "running"].includes(i.receipt.state));
function savedIntents(): Record<string, Intent> {
  try { return JSON.parse(window.sessionStorage.getItem(STORE) || "{}"); } catch { return {}; }
}

export default function App() {
  const [selected, setSelected] = useState<string | null>(() => window.localStorage.getItem("forge-web.selection"));
  const [drafts, setDrafts] = useState<Record<string, string>>(() => {
    try { return JSON.parse(window.sessionStorage.getItem("forge-web.drafts") || "{}"); } catch { return {}; }
  });
  const [intents, setIntents] = useState<Record<string, Intent>>(savedIntents);
  const current = useRef(intents);
  const sending = useRef(new Set<string>());
  const [root, setRoot] = useState("");
  const [name, setName] = useState("");
  const fleet = useQuery({ queryKey: ["fleet"], queryFn: getFleet, refetchInterval: 2000 });
  const detail = useQuery({ queryKey: ["session", selected], queryFn: () => getSession(selected!),
    enabled: Boolean(selected), refetchInterval: 2000, retry: false });
  const snapshot = fleet.data;
  const update = useCallback((owner: string, change: Partial<Intent>, requestId?: string) => {
    setIntents(old => {
      if (requestId && old[owner]?.body.request_id !== requestId) return old;
      const next = { ...old, [owner]: { ...old[owner]!, ...change } };
      current.current = next;
      return next;
    });
  }, []);
  useEffect(() => { current.current = intents; window.sessionStorage.setItem(STORE, JSON.stringify(intents)); }, [intents]);
  useEffect(() => { window.sessionStorage.setItem("forge-web.drafts", JSON.stringify(drafts)); }, [drafts]);
  useEffect(() => { if (selected) window.localStorage.setItem("forge-web.selection", selected); }, [selected]);
  useEffect(() => {
    if (!snapshot) return;
    for (const i of Object.values(current.current)) {
      if (pending(i) && i.instance !== snapshot.server_instance) {
        update(i.owner, { uncertain: true, retry: false, error: "Server restarted: outcome uncertain. Refresh authoritative state before acting; this intent will not be replayed." });
      }
    }
  }, [snapshot, update]);
  useEffect(() => {
    const tick = async () => {
      for (const i of Object.values(current.current)) {
        if (!pending(i) || !i.receipt) continue;
        try {
          const receipt = await getOperation(i.receipt.operation_id);
          update(i.owner, { receipt, uncertain: receipt.state === "unknown", error: undefined }, i.body.request_id);
        } catch { update(i.owner, { error: "Operation status unavailable; still reconciling the same intent." }, i.body.request_id); }
      }
    };
    const timer = setInterval(() => { void tick(); }, 500);
    return () => clearInterval(timer);
  }, [update]);
  const submit = useCallback(async (i: Intent) => {
    if (sending.current.has(i.owner)) return;
    sending.current.add(i.owner);
    update(i.owner, { ...i, receipt: undefined, error: undefined, retry: false, uncertain: false });
    try {
      const receipt = i.action === "spawn" ? await postSpawn(i.body) : await postAction(i.owner, i.action, i.body);
      update(i.owner, { receipt, error: undefined, retry: false });
    } catch (e) {
      if (e instanceof ApiProblem) update(i.owner, { error: `${e.problem.code}: ${e.problem.detail}`, uncertain: true });
      else update(i.owner, { error: "Response lost; reconcile this exact request before another action.", retry: true });
    } finally { sending.current.delete(i.owner); }
  }, [update]);
  const act = useCallback((action: string, body: Omit<ActionBody, "request_id" | "revision">) => {
    if (!selected || !snapshot || (current.current[selected] && pending(current.current[selected]!))) return;
    void submit({ owner: selected, ownerLabel: snapshot.sessions.find(s=>s.identity.key===selected)?.identity.name ?? selected, action, instance: snapshot.server_instance,
      body: { ...body, request_id: newRequestId(), revision: snapshot.revision, expected_server_instance: snapshot.server_instance } });
  }, [selected, snapshot, submit]);
  const spawn = intents.spawn;
  useEffect(() => {
    if (!snapshot || !spawn?.receipt || spawn.receipt.state !== "exited" || spawn.receipt.rc !== 0) return;
    const chosen = snapshot.spawn_roots.find(r => r.key === spawn.body.root_key);
    const reported = spawn.receipt.stdout.match(/(?:SPAWNED|ENSURE) @([^\s]+)/)?.[1];
    const found = snapshot.sessions.find(s => s.identity.root === chosen?.root
      && Boolean(reported) && s.identity.name === reported);
    if (found && !spawn.uncertain) { setSelected(found.identity.key); update("spawn", { uncertain: true }); }
  }, [snapshot, spawn, update]);
  const chosenRoot = root || snapshot?.spawn_roots[0]?.key || "";
  const selectedIntent = selected ? intents[selected] : undefined;
  const draftKey = selected ? `${selected}:composer` : "";
  const retry = (i: Intent | undefined) => i?.retry && !i.uncertain && (
    <button onClick={() => { void submit(i); }}>Reconcile same request</button>
  );
  return <div className="app">
    <aside className="fleet">
      {snapshot && !snapshot.mutations_enabled && <p role="status">Read-only: {snapshot.mutations_detail}</p>}
      {snapshot?.tmux_state === "unavailable" && <p role="status">tmux unavailable; retained fleet is stale. {snapshot.tmux_detail}</p>}
      {fleet.isError && <p role="status">Backend unreachable; cached data is stale.</p>}
      {snapshot && <p role="status">Board: {snapshot.board.collection_state} · last successful read {snapshot.board.last_success_age_s === null ? "never" : `${Math.floor(snapshot.board.last_success_age_s)}s ago`}. {snapshot.board.unavailable_reason} {snapshot.board.stale && "Source heartbeat stale."}</p>}
      <div className="card">
        <h3>Spawn session</h3>
        <label>Root <select aria-label="Spawn root" value={chosenRoot} onChange={e => setRoot(e.target.value)}>
          {snapshot?.spawn_roots.map(r => <option key={r.key} value={r.key}>{r.label} · {r.root}</option>)}
        </select></label>
        <label>Optional name <input aria-label="Spawn name" value={name} onChange={e => setName(e.target.value)} /></label>
        <button disabled={!snapshot?.mutations_enabled || !chosenRoot || Boolean(spawn && pending(spawn))}
          onClick={() => { if (snapshot) void submit({ owner: "spawn", action: "spawn", instance: snapshot.server_instance,
            body: { request_id: newRequestId(), revision: snapshot.revision, expected_server_instance: snapshot.server_instance, root_key: chosenRoot, ...(name ? { name } : {}) } }); }}>Spawn</button>
        {spawn?.receipt && <Receipt receipt={spawn.receipt} />}
        {spawn?.error && <p role="alert">{spawn.error}</p>}{retry(spawn)}
      </div>
      {Object.values(intents).filter(i => i.owner !== "spawn" && i.owner !== selected
        && (pending(i) || i.uncertain) && !snapshot?.sessions.some(s => s.identity.key === i.owner))
        .map(i => <button key={i.owner} onClick={() => setSelected(i.owner)}>Recover operation for {i.ownerLabel ?? i.owner}</button>)}
      <FleetList fleet={snapshot ?? null} selected={selected} onSelect={setSelected} />
    </aside>
    <main className="detail">
      {!selected && <p>Select a session.</p>}
      {selected && detail.isError && <p role="alert">{detail.error instanceof ApiProblem && detail.error.status === 404
        ? "Session absent from the latest confirmed inventory. Select another."
        : "Session data unavailable; retained detail is stale."}</p>}
      {selected && <>
        {retry(selectedIntent)}
        {selectedIntent?.receipt && <Receipt receipt={selectedIntent.receipt} />}
        {selectedIntent?.error && <p role="alert">{selectedIntent.error}</p>}
      </>}
      {selected && detail.data && snapshot && <>
        <SessionDetail key={selected} detail={detail.data}
          mutationsEnabled={snapshot.mutations_enabled && snapshot.tmux_state === "ok"}
          draft={drafts[draftKey] ?? ""} onDraftChange={v => setDrafts(old => ({ ...old, [draftKey]: v }))}
          onAction={act} busy={Boolean(selectedIntent && pending(selectedIntent))}
          receipt={null} problem={null} draftTick={0} />
      </>}
    </main>
  </div>;
}
