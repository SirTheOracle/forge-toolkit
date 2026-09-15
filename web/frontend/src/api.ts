import type {
  FleetSnapshot,
  OperationReceipt,
  Problem,
  SessionDetailView,
} from "./types";

export class ApiProblem extends Error {
  readonly problem: Problem;
  readonly status: number;
  constructor(status: number, problem: Problem) {
    super(problem.detail);
    this.status = status;
    this.problem = problem;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let problem: Problem = {
      code: `http_${response.status}`,
      detail: response.statusText,
      revision: null,
      current_object_key: null,
    };
    try {
      problem = (await response.json()) as Problem;
    } catch {
      /* the body was not a Problem; keep the synthesized one */
    }
    throw new ApiProblem(response.status, problem);
  }
  return (await response.json()) as T;
}

export const getFleet = () => request<FleetSnapshot>("/api/fleet");
export const getSession = (key: string) =>
  request<SessionDetailView>(`/api/sessions/${encodeURIComponent(key)}`);
export const getOperation = (id: string) =>
  request<OperationReceipt>(`/api/operations/${encodeURIComponent(id)}`);

export async function getSource(sessionKey: string, sourceKey: string): Promise<string> {
  const response = await fetch(
    `/api/sessions/${encodeURIComponent(sessionKey)}/sources/${encodeURIComponent(sourceKey)}`,
  );
  if (!response.ok) {
    throw new ApiProblem(response.status, (await response.json()) as Problem);
  }
  // text/plain, and it is rendered into a <pre> as a text node — never innerHTML.
  return await response.text();
}

/**
 * ONE request_id PER OPERATOR INTENT, generated when the operator commits to the action and
 * REUSED for every retry of that same intent (backend R9).
 *
 * This is the client half of the deduplication contract: a fresh id per fetch attempt would
 * defeat it entirely, and the operator's double click would dispatch twice. `crypto.randomUUID`
 * is available in every browser this tool targets and in jsdom 26; the fallback exists so a
 * unit test in an older environment does not silently produce a colliding constant.
 */
export function newRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const hex = (n: number) =>
    Array.from({ length: n }, () => Math.floor(Math.random() * 16).toString(16)).join("");
  return `${hex(8)}-${hex(4)}-4${hex(3)}-a${hex(3)}-${hex(12)}`;
}

export interface ActionBody {
  request_id: string;
  revision: number;
  [key: string]: unknown;
}

export const postAction = (sessionKey: string, action: string, body: ActionBody) =>
  request<OperationReceipt>(
    `/api/sessions/${encodeURIComponent(sessionKey)}/actions/${action}`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const postSpawn = (body: ActionBody) =>
  request<OperationReceipt>("/api/spawn", { method: "POST", body: JSON.stringify(body) });

export const postRefresh = () =>
  request<{ coalesced: number; next_attempt_in_s: number }>("/api/refresh", { method: "POST" });