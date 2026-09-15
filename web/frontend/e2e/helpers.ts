import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const FIXTURE = resolve(here, "..", ".e2e-checkout");

/** Every stub invocation, as tab-separated argv. Proves what the backend really ran. */
export function stubLines(): string[][] {
  try {
    return readFileSync(resolve(FIXTURE, "stub.log"), "utf8")
      .split("\n")
      .filter(Boolean)
      .map((line) => line.split("\t"));
  } catch {
    return [];
  }
}

export function boardCalls(): number {
  return stubLines().filter((parts) => parts[0] === "forge" && parts[1] === "board").length;
}

export function dispatchCalls(): string[][] {
  return stubLines().filter((parts) => parts[0] === "forge" && parts[1] === "dispatch");
}

/** Reset only the self-contained checkout; never invokes real Forge or tmux.
 * All seeded records prove owner through session+incarnation; seed ID is assigned
 * before creating records. Wait for publication so no action uses the old handle. */
async function reset(empty: boolean) {
  const { execFileSync } = await import("node:child_process");
  const { mkdirSync, rmSync, writeFileSync } = await import("node:fs");
  writeFileSync(resolve(FIXTURE,"fixture-state.json"),JSON.stringify({sessions:[], next_id:"$"+String(Date.now())}));
  const dev=resolve(FIXTURE,"fixture/.dev");
  rmSync(dev,{recursive:true,force:true});
  for(const dir of ["attention/payloads","attention/archive","forge-tmp/callbacks"]) mkdirSync(resolve(dev,dir),{recursive:true});
  if (!empty) execFileSync(resolve(FIXTURE,"venv/bin/python3"),[resolve(FIXTURE,"bin/forge"),"spawn","--root",resolve(FIXTURE,"fixture"),"--name","forge-e2e"],{stdio:"pipe"});
  const state=JSON.parse(readFileSync(resolve(FIXTURE,"fixture-state.json"),"utf8"));
  const expected=state.sessions[0]?.id;
  const deadline=Date.now()+20000;
  while (Date.now()<deadline) {
    const response=await fetch(`http://127.0.0.1:${process.env.FORGE_WEB_E2E_PORT ?? "8799"}/api/fleet`);
    const fleet=await response.json();
    if (fleet.tmux_state === "ok" && (empty ? fleet.sessions.length===0 : fleet.sessions.some((s: {identity: {session_id: string}})=>s.identity.session_id===expected))) return;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error("Fixture collector did not publish reset incarnation");
}
export async function resetFixture() { await reset(false); }
export async function resetEmptyFixture() { await reset(true); }