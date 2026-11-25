import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, it, expect, vi } from "vitest";
import App from "./App";
import * as api from "./api";
import type { FleetSnapshot, SessionDetailView, OperationReceipt } from "./types";
vi.mock("./api", async () => {
  const actual=await vi.importActual<typeof api>("./api");
  return {...actual,getFleet:vi.fn(),getSession:vi.fn(),getOperation:vi.fn(),postAction:vi.fn(),postSpawn:vi.fn()};
});
const stage={session_pending_stage:null,session_pending_source:"",current_pending:null,concurrent_pending:[],parked:[],running:false,running_evidence:"",active_pipeline:"alpha",last_stage_completed:null,last_stage_status:null,last_worker:null,next_stage_intent:"qa",context_updated_at:null,board_stages:[],ambiguous:false,ambiguity_detail:"",status_excerpt:"context only status"};
function detail(key: string): SessionDetailView {
  return {revision:1,generated_at:"t",identity:{key,name:key,root:"/project",session_id:"$1",created:"1700000000",label:"project",label_source:"root_basename",repo_alias:null,repo_id:null,has_dev:true},objective_excerpt:"Build "+key,objective_source:"Source excerpt",stage,asks:[],board_items:[],feed:[],feed_backlog:false,feed_backlog_detail:"",responses:[],sources:[],callbacks:[],ack_scope:null};
}
function fleet():FleetSnapshot {
  return {revision:1,generated_at:"t",server_instance:"instance",mutations_enabled:true,mutations_detail:"",tmux_state:"ok",tmux_detail:"",board:{schema_name:null,generated_at:null,heartbeat:null,heartbeat_age_s:null,stale:false,busy:false,unavailable_reason:null,exit_code:0,last_success_at:"t",last_success_age_s:8,last_attempt_at:"t",collection_state:"ok",next_attempt_in_s:5},sessions:["a","b"].map(key=>({...detail(key),attention_count:0,open_ask_count:0,partial_startup:false,pane_count:5})),root_notices:[],spawn_roots:[{key:"root",root:"/project",label:"project",live:true,registered:true}]};
}
function receipt(state: OperationReceipt["state"]="queued"):OperationReceipt {
  return {operation_id:"instance:1",server_instance:"instance",request_id:"rid",session_key:"a",action:"answer",state,duplicate:false,created_at:"t",finished_at:null,rc:0,summary:state==="exited"?"Answer delivered; callback cleanup FAILED.":"Queued",stdout:"",stderr:"",truncated_output:false,components:{recorded:true,delivered:state==="exited",cleanup:state==="exited"?"failed":null,lock_release_state:null,finalize:null,park_result:null,spawn_result:null,ack_outcome:null,ack_applied_count:null,offers_redispatch:false},uncertainty:""};
}
function mount(){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><App/></QueryClientProvider>);}
beforeEach(()=>{
 vi.resetAllMocks();window.localStorage.clear();window.sessionStorage.clear();
 vi.mocked(api.getFleet).mockResolvedValue(fleet());vi.mocked(api.getSession).mockImplementation(async key=>detail(key));
 vi.mocked(api.postAction).mockResolvedValue(receipt());vi.mocked(api.getOperation).mockResolvedValue(receipt("exited"));
});
it("polls a queued receipt to a partial outcome and keeps it on its originating session",async()=>{
 mount();await screen.findByRole("button",{name:/^a.*Build a/});fireEvent.click(screen.getByRole("button",{name:/^a.*Build a/}));
 fireEvent.change(await screen.findByLabelText("message"),{target:{value:"hello"}});fireEvent.click(screen.getByRole("button",{name:"Send"}));
 fireEvent.click(screen.getByRole("button",{name:/^b.*Build b/}));
 await waitFor(()=>expect(api.getOperation).toHaveBeenCalled());expect(screen.queryByText(/callback cleanup FAILED/)).toBeNull();
 fireEvent.click(screen.getByRole("button",{name:/^a.*Build a/}));await screen.findByText(/callback cleanup FAILED/);
 expect(screen.getByLabelText("message")).toHaveValue("hello");
});
it("reconciles a dropped POST using exactly the original ID and body",async()=>{
 vi.mocked(api.postAction).mockRejectedValueOnce(new TypeError("network"));mount();fireEvent.click(await screen.findByRole("button",{name:/^a.*Build a/}));
 fireEvent.change(await screen.findByLabelText("message"),{target:{value:"once"}});fireEvent.click(screen.getByRole("button",{name:"Send"}));
 fireEvent.click(await screen.findByRole("button",{name:"Reconcile same request"}));
 await waitFor(()=>expect(api.postAction).toHaveBeenCalledTimes(2));expect(vi.mocked(api.postAction).mock.calls[0]).toEqual(vi.mocked(api.postAction).mock.calls[1]);
 expect(vi.mocked(api.postAction).mock.calls[0]?.[2].expected_server_instance).toBe("instance");
});
it("shows restart uncertainty without replaying a stored operation",async()=>{
 window.sessionStorage.setItem("forge-web.intents",JSON.stringify({a:{owner:"a",action:"message",instance:"old",body:{request_id:"id",revision:1},receipt:receipt()}}));
 mount();fireEvent.click(await screen.findByRole("button",{name:/^a.*Build a/}));await screen.findByText(/Server restarted: outcome uncertain/);expect(api.postAction).not.toHaveBeenCalled();
});
it("spawns from an empty fleet and displays the completed ensure result",async()=>{
 const empty=fleet();empty.sessions=[];vi.mocked(api.getFleet).mockResolvedValue(empty);
 vi.mocked(api.postSpawn).mockResolvedValue({...receipt(),action:"spawn",session_key:null});
 vi.mocked(api.getOperation).mockResolvedValue({...receipt("exited"),action:"spawn",summary:"Already active at that root",components:{...receipt().components,spawn_result:"ensured"}});
 mount();await screen.findByText("No live Forge sessions.");fireEvent.change(screen.getByLabelText("Spawn name"),{target:{value:"desired"}});fireEvent.click(screen.getByRole("button",{name:"Spawn"}));
 await screen.findByText("Already active at that root");expect(api.postSpawn).toHaveBeenCalledWith(expect.objectContaining({root_key:"root",name:"desired"}));
});
it("starts a fresh intent after a completed spawn selected its session",async()=>{
 vi.mocked(api.postSpawn).mockResolvedValue({...receipt(),action:"spawn",session_key:null});
 vi.mocked(api.getOperation).mockResolvedValue({...receipt("exited"),action:"spawn",session_key:null,stdout:"ENSURE @a\n",summary:"Already active at that root",components:{...receipt().components,spawn_result:"ensured"}});
 mount();await screen.findByRole("button",{name:/^a.*Build a/});
 fireEvent.change(screen.getByLabelText("Spawn name"),{target:{value:"a"}});fireEvent.click(screen.getByRole("button",{name:"Spawn"}));
 await screen.findByText("Already active at that root");await waitFor(()=>expect(window.localStorage.getItem("forge-web.selection")).toBe("a"));
 fireEvent.change(screen.getByLabelText("Spawn name"),{target:{value:"another"}});fireEvent.click(screen.getByRole("button",{name:"Spawn"}));
 await waitFor(()=>expect(api.postSpawn).toHaveBeenCalledTimes(2));
 const calls=vi.mocked(api.postSpawn).mock.calls;expect(calls[0]?.[0].request_id).not.toBe(calls[1]?.[0].request_id);
});
it("scope changes replace target and outside-fleet preview before submission",async()=>{
 const d=detail("a");const scope={target:"a",target_kind:"session",visible:[],outside_fleet_count:0,estimate_note:"estimate",available:true,unavailable_reason:""};d.ack_scope=scope;d.ack_scopes={session:scope,root_basename:{...scope,target:"project",target_kind:"root_basename",outside_fleet_count:3}};
 vi.mocked(api.getSession).mockResolvedValue(d);mount();fireEvent.click(await screen.findByRole("button",{name:/^a.*Build a/}));await screen.findByLabelText("Ack scope");fireEvent.change(screen.getByLabelText("Ack scope"),{target:{value:"root_basename"}});
 await screen.findByText(/Plus 3 matching/);fireEvent.click(screen.getByRole("button",{name:"Acknowledge project"}));await waitFor(()=>expect(api.postAction).toHaveBeenCalledWith("a","ack",expect.objectContaining({scope:"root_basename"})));
});
it("parked-only and concurrent entries submit their own handles and retain notes",async()=>{
 const d=detail("a");const p={object_key:"parked",slug:"alpha",stage:"coding",worker:null,timestamp:"t",parked_at:"t",parked_reason:"wait",uncommitted:null,prompt_sha256:null};
 d.stage={...stage,parked:[p],concurrent_pending:[{...p,object_key:"other",slug:"beta",parked_at:null}]};vi.mocked(api.getSession).mockResolvedValue(d);mount();fireEvent.click(await screen.findByRole("button",{name:/^a.*Build a/}));
 fireEvent.change(await screen.findByLabelText("note alpha/coding"),{target:{value:"done"}});fireEvent.click(screen.getByRole("button",{name:"Resolve"}));
 await waitFor(()=>expect(api.postAction).toHaveBeenCalledWith("a","resolve",expect.objectContaining({pending_key:"parked",note:"done"})));
});
it("recovers the exact lost intent after switching, departure and reload",async()=>{
 let mutations=0;const seen=new Set<string>();
 vi.mocked(api.postAction).mockImplementation(async (_owner,_action,body)=>{
   if(!seen.has(body.request_id)){seen.add(body.request_id);mutations++;throw new TypeError("lost response");}
   return {...receipt("exited"),duplicate:true};
 });
 const first=mount();fireEvent.click(await screen.findByRole("button",{name:/^a.*Build a/}));
 fireEvent.change(await screen.findByLabelText("message"),{target:{value:"exactly once"}});
 fireEvent.click(screen.getByRole("button",{name:"Send"}));await screen.findByRole("button",{name:"Reconcile same request"});
 fireEvent.click(screen.getByRole("button",{name:/^b.*Build b/}));
 await waitFor(()=>expect(window.localStorage.getItem("forge-web.selection")).toBe("b"));
 first.unmount();const departed=fleet();departed.sessions=departed.sessions.filter(s=>s.identity.key!=="a");
 vi.mocked(api.getFleet).mockResolvedValue(departed);
 vi.mocked(api.getSession).mockImplementation(async key=>{if(key==='a')throw new api.ApiProblem(404,{code:'not_found',detail:'gone',revision:null,current_object_key:null});return detail(key);});
 mount();fireEvent.click(await screen.findByRole("button",{name:"Recover operation for a"}));
 await screen.findByText(/Session absent from/);
 fireEvent.click(await screen.findByRole("button",{name:"Reconcile same request"}));
 await screen.findByText(/callback cleanup FAILED/);
 expect(mutations).toBe(1);expect(api.postAction).toHaveBeenCalledTimes(2);
 expect(vi.mocked(api.postAction).mock.calls[0]).toEqual(vi.mocked(api.postAction).mock.calls[1]);
});
