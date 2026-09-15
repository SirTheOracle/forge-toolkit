"""Regression paths W6–W15 through collector, HTTP adapter and operation ownership."""
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from forge_web.app import create_app
from forge_web.collector import Collector
from forge_web.discovery import Inventory, LiveSession
from forge_web.handles import HandleTable, ReceiptStore, ReceiptConflict
from forge_web.sampler import BoardSampler, BoardOutcome
from forge_web.feed import attention_entries, current_session_reply, RESPONSE_READ_MAX
from forge_web.actions import ActionRunner, Plan
from forge_web.config import child_env
from conftest import write_log, write_json

CREATED='1700000000'
RID='11111111-1111-4111-a111-111111111111'

def collector(checkout):
    return Collector(checkout.cfg(),handles=HandleTable(),sampler=BoardSampler(),server_instance='instance',mutations_enabled=True,mutations_detail='')

def session(root, name='forge-a', sid='$1', created=CREATED):
    return LiveSession(name,str(root),sid,created,True)

def seed(root,name='forge-a',slug='alpha'):
    (root/'.dev'/f'forge-context.{name}.yml').write_text('active_pipeline: '+slug+'\n')
    write_log(root,slug,[{'timestamp':'2026-09-14T10:00:00Z','stage':'coding','session':name,'incarnation':CREATED,'response':None,'to':'codex-a'}])

def test_same_root_rows_and_attention_do_not_leak(checkout,project_root):
    seed(project_root);seed(project_root,'forge-b','beta')
    c=collector(checkout)
    c._board={'hot':[{'condition':'NEEDS-ASK','root':str(project_root),'session':'forge-b','slug':'beta','stage':'coding','state':'needs-input','incarnation':CREATED}], 'active':[]}
    c._publish(Inventory(sessions=[session(project_root),session(project_root,'forge-b','$2')]))
    a,b=sorted(c.snapshot().fleet.sessions,key=lambda card:card.identity.name)
    assert a.board_items==[] and a.attention_count==0 and not a.stage.running
    assert b.attention_count==1 and not b.stage.running
    c._board['hot'][0]['acked']=True;c._publish(Inventory(sessions=[session(project_root,'forge-b','$2')]))
    assert c.snapshot().fleet.sessions[0].attention_count==0

def test_inventory_failure_retains_and_successful_empty_removes(checkout,project_root):
    seed(project_root);c=collector(checkout);c._publish(Inventory(sessions=[session(project_root)]))
    key=c.snapshot().fleet.sessions[0].identity.key
    c._publish(Inventory(state='unavailable',detail='timeout'))
    assert key in c.snapshot().details and c.snapshot().fleet.tmux_state=='unavailable'
    c._publish(Inventory(sessions=[]));assert not c.snapshot().details

def test_context_last_good_and_containment_at_snapshot(checkout,project_root,tmp_path):
    seed(project_root);c=collector(checkout);i=Inventory(sessions=[session(project_root)]);c._publish(i)
    path=project_root/'.dev'/'forge-context.forge-a.yml';path.write_text('active_pipeline: [broken')
    c._publish(i);assert c.snapshot().fleet.sessions[0].stage.active_pipeline=='alpha'
    assert c.snapshot().fleet.sessions[0].sources[0].state.value=='stale'
    path.unlink();outside=tmp_path/'outside.yml';outside.write_text('active_pipeline: SECRET_SENTINEL');path.symlink_to(outside)
    fresh=collector(checkout);fresh._publish(i)
    assert 'SECRET_SENTINEL' not in fresh.snapshot().fleet.model_dump_json()
    path.unlink();path.write_text('active_pipeline: ../../outside')
    fresh._publish(i);assert fresh.snapshot().fleet.sessions[0].objective_excerpt is None

def test_session_recreation_drops_feed_without_empty_inventory(checkout,project_root):
    seed(project_root)
    write_json(project_root/'.dev/attention/dispatch-old.json',{'schema':'cc-dispatch/1','event':'dispatch','root':str(project_root),'session':'forge-a','dispatch_id':'old','dispatched_at':'2026-09-14T10:00:00Z'})
    c=collector(checkout);c._publish(Inventory(sessions=[session(project_root)]));assert next(iter(c.snapshot().details.values())).feed
    c._publish(Inventory(sessions=[session(project_root,sid='$9',created='2000000000')]))
    assert not next(iter(c.snapshot().details.values())).feed

def test_dispatch_source_specific_time_and_rotating_names(project_root):
    for n in range(205):
        write_json(project_root/f'.dev/attention/d-{n:04d}.json',{'schema':'cc-dispatch/1','event':'dispatch','root':str(project_root),'session':'forge-a','dispatch_id':str(n),'dispatched_at':'2020-01-01T00:00:00Z' if n<204 else '2026-09-14T10:00:00Z'})
    seen={};entries=[]
    for _ in range(3): entries.extend(attention_entries(str(project_root),name='forge-a',created_epoch=int(CREATED),seen=seen))
    assert len(entries)==1 and entries[0].detail['dispatch_id']=='204' and entries[0].occurred_at

def test_no_id_current_reply_uses_recorded_path_and_prefix(project_root,tmp_path):
    p=project_root/'.dev/attention/payloads/response.forge-a.txt';p.write_text('x'*(RESPONSE_READ_MAX+1))
    stop={'root':str(project_root),'session':'forge-a','event':'stop','emitted_at':'2026-09-14T10:00:00Z','response_paths':['.dev/attention/payloads/response.forge-a.txt'],'response_dispatch_ids':[]}
    write_json(project_root/'.dev/attention/stop.forge-a.json',stop)
    card=current_session_reply(str(project_root),'forge-a',int(CREATED))
    assert card and len(card.text)==RESPONSE_READ_MAX and card.reader_limit_hit and not card.attributed_dispatch_ids
    assert current_session_reply(str(project_root),'forge-a',2000000000) is None
    p.unlink();outside=tmp_path/'secret';outside.write_text('secret');p.symlink_to(outside)
    assert current_session_reply(str(project_root),'forge-a',int(CREATED)) is None

def test_pending_handle_captures_displayed_callback_and_rejects_foreign(checkout,project_root):
    seed(project_root);seed(project_root,'forge-b','beta')
    callback=project_root/'.dev/forge-tmp/callbacks/alpha-coding.forge-a.1700000000.callback'
    callback.write_text('slug: alpha\nstage: coding\nstatus: BLOCKED\nworker: codex-a\ntimestamp: 2026-09-14T10:00:00Z\nsession: forge-a\nincarnation: "1700000000"\nselected_pending_timestamp: "2026-09-14T10:00:00Z"\ncallback_id: original\nmessage: callback\n')
    app=create_app(checkout.cfg());c=app.state.collector;c._publish(Inventory(sessions=[session(project_root),session(project_root,'forge-b','$2')]))
    a,b=sorted(c.snapshot().fleet.sessions,key=lambda x:x.identity.name)
    old=a.stage.current_pending.object_key
    ref=app.state.handles.resolve(old);assert ref.payload['callback_id']=='original'
    callback.write_text(callback.read_text().replace('original','replacement'))
    c._publish(Inventory(sessions=[session(project_root),session(project_root,'forge-b','$2')]))
    assert c.snapshot().details[a.identity.key].stage.current_pending.object_key!=old
    with TestClient(app,raise_server_exceptions=False) as client:
        r=client.post(f'/api/sessions/{b.identity.key}/actions/park',json={'request_id':RID,'revision':0,'pending_key':old,'reason':'wait'})
        assert r.status_code==409 and 'another session' in r.text

async def test_active_receipts_survive_ttl_pressure_and_bind_target():
    clock=[0.0];store=ReceiptStore(ttl_s=1,maxsize=1,clock=lambda:clock[0])
    _,active=await store.reserve('active','same','message','a');await store.complete(active,'instance:1')
    clock[0]=100
    for n in range(4):
        _,r=await store.reserve(str(n),'other','message','b');await store.complete(r,'x');await store.finish(r)
    kind,again=await store.reserve('active','same','message','a');assert kind=='duplicate' and again is active
    with pytest.raises(ReceiptConflict): await store.reserve('active','same','message','b')

def test_duplicate_lookup_survives_departure_and_instance_fence(checkout,project_root):
    seed(project_root);checkout.set_sessions([('forge-a',str(project_root),'$1',CREATED)])
    app=create_app(checkout.cfg());c=app.state.collector;c._publish(Inventory(sessions=[session(project_root)]));key=c.snapshot().fleet.sessions[0].identity.key
    with TestClient(app) as client:
        body={'request_id':RID,'revision':0,'text':'once','expected_server_instance':app.state.receipts.instance}
        first=client.post(f'/api/sessions/{key}/actions/message',json=body);assert first.status_code==200
        c._publish(Inventory(sessions=[]))
        repeat=client.post(f'/api/sessions/{key}/actions/message',json=body)
        assert repeat.status_code==200 and repeat.json()['operation_id']==first.json()['operation_id']
        body['expected_server_instance']='old-instance'
        refused=client.post(f'/api/sessions/{key}/actions/message',json=body)
        assert refused.status_code==409 and 'restarted' in refused.text

async def test_oversized_board_is_bounded_and_cancel_reaps_group(checkout,tmp_path):
    p=tmp_path/'read-only';p.write_text('#!/bin/sh\npython3 -c "print(\'x\'*5000000)"\n');p.chmod(0o755)
    c=collector(checkout);c.cfg=replace(c.cfg,board_cmd=(str(p),))
    outcome,*_=await c._scan_board();assert outcome is BoardOutcome.INVALID
    child=tmp_path/'child.py'
    lockfile=tmp_path/'lock'
    child.write_text("import fcntl,time,pathlib\nf=open("+repr(str(lockfile))+",'w')\nfcntl.flock(f,fcntl.LOCK_EX)\npathlib.Path("+repr(str(tmp_path/'ready'))+").write_text('ready')\ntime.sleep(60)\n")
    p.write_text('#!/bin/sh\npython3 "'+str(child)+'" &\nwait\n')
    task=asyncio.create_task(c._scan_board())
    for _ in range(100):
        if (tmp_path/'ready').exists():break
        await asyncio.sleep(.01)
    assert (tmp_path/'ready').exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    import fcntl
    with lockfile.open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)


async def test_removed_executable_terminal_receipt_and_cleanup(checkout):
    runner=ActionRunner(checkout.cfg(),receipts=ReceiptStore(),sampler=BoardSampler(),live_identity=lambda _:asyncio.sleep(0),mutations_enabled=lambda:(True,''))
    plan=Plan(action='spawn',argv=[str(checkout.root/'missing')],cwd=str(checkout.root),lock_key='root',lock_kind='root')
    receipt=await runner.submit('spawn',{'request_id':RID,'revision':0},lambda:plan,None)
    for _ in range(50):
        if receipt.finished_at:break
        await asyncio.sleep(.01)
    assert receipt.state.value=='refused' and not runner._live

def test_nested_runtime_reads_yaml_with_cellar_tmux(checkout,tmp_path,monkeypatch):
    cellar=tmp_path/'Cellar/tmux/3.5/bin';cellar.mkdir(parents=True)
    tmux=cellar/'tmux';tmux.write_text('#!/bin/sh\nexit 0\n');tmux.chmod(0o755)
    checkout.env['FORGE_TMUX_BIN']=str(tmux)
    cfg=checkout.cfg();env=child_env(cfg)
    import subprocess,sys
    result=subprocess.run(['/bin/sh','-c','python3 -c "import yaml; print(yaml.safe_load(\'answer: 42\')[\'answer\'])"'],env=env,capture_output=True,text=True)
    assert result.returncode==0 and result.stdout.strip()=='42'


def test_callback_task_episode_adapters_prove_time_owner_and_deduplicate(project_root):
    from forge_web.feed import structured_entries
    root=str(project_root)
    rows=[('callback:cb',{'root':root,'session':'forge-a','incarnation':CREATED,'timestamp':'2026-09-14T10:00:00Z','status':'BLOCKED'}),
          ('tasks:did',{'root':root,'session':'forge-a','dispatched_at':'2026-09-14T10:00:00Z','state':'queued'}),
          ('episodes:ep',{'root':root,'session':'forge-a','first_at':'2026-09-14T10:00:00Z','state':'working'}),
          ('tasks:old',{'root':root,'session':'forge-a','dispatched_at':'2020-01-01T00:00:00Z'}),
          ('tasks:unknown',{'root':root,'session':'forge-a'})]
    seen={}
    result=structured_entries(root,name='forge-a',created_epoch=int(CREATED),records=rows,seen=seen)
    assert {r.kind for r in result}=={'callback','tasks','episodes'}
    assert structured_entries(root,name='forge-a',created_epoch=int(CREATED),records=rows,seen=seen)==[]


async def test_blocked_operation_retries_survive_expiry_and_pressure(checkout):
    clock=[0.0];store=ReceiptStore(ttl_s=1,maxsize=1,clock=lambda:clock[0])
    runner=ActionRunner(checkout.cfg(),receipts=store,sampler=BoardSampler(),live_identity=lambda _:asyncio.sleep(0),mutations_enabled=lambda:(True,''))
    gate=asyncio.Event();started=asyncio.Event();calls=[]
    async def execute(argv,cwd):
        calls.append(argv);started.set();await gate.wait();return 0,'SPAWNED @once','',True
    runner.execute=execute
    plan=Plan(action='spawn',argv=['fixture'],cwd=str(checkout.root),lock_key='root',lock_kind='root')
    body={'request_id':RID,'revision':0}
    first=await runner.submit('spawn',body,lambda:plan,None)
    await started.wait();clock[0]=100
    for n in range(5):
        _,reservation=await store.reserve(str(n),'hash','spawn',None)
        await store.complete(reservation,'other');await store.finish(reservation)
    retry=await runner.submit('spawn',body,lambda:plan,None)
    assert retry.operation_id==first.operation_id and len(calls)==1 and retry.duplicate
    gate.set()
    for _ in range(50):
        if first.finished_at:break
        await asyncio.sleep(.01)
    assert not runner._live and first.state.value=='exited'


def test_callback_same_stage_other_session_and_ambiguous_duplicate(project_root):
    from forge_web.readers import callback_binding
    root=str(project_root);entry={'to':'codex-a','stage':'coding','timestamp':'2026-09-14T10:00:00Z'}
    directory=project_root/'.dev/forge-tmp/callbacks'
    base='message: callback\nslug: alpha\nstage: coding\nstatus: BLOCKED\nworker: codex-a\ntimestamp: 2026-09-14T10:00:00Z\nincarnation: "1700000000"\nselected_pending_timestamp: "2026-09-14T10:00:00Z"\n'
    (directory/'alpha-coding.forge-a.1700000000.callback').write_text(base+'session: forge-a\ncallback_id: mine\n')
    (directory/'alpha-coding.forge-b.1700000000.callback').write_text(base+'session: forge-b\ncallback_id: theirs\n')
    assert callback_binding(root,'alpha',entry,'forge-a',CREATED)[0]=='mine'
    (directory/'alpha-coding.forge-a.callback').write_text(base.replace('incarnation: \"1700000000\"\n','')+'session: forge-a\ncallback_id: duplicate\n')
    assert callback_binding(root,'alpha',entry,'forge-a',CREATED)==('ambiguous','ambiguous')


def test_large_inventory_rotates_actual_projection_work(checkout,project_root):
    c=collector(checkout)
    sessions=[session(project_root,name='forge-'+str(n),sid='$'+str(n)) for n in range(24)]
    inv=Inventory(sessions=sessions)
    c._publish(inv)
    assert 0<len(c.snapshot().fleet.sessions)<=8
    for _ in range(8):c._publish(inv)
    assert len(c.snapshot().fleet.sessions)==24

def test_real_nested_cli_read_keeps_venv_runtime_with_cellar_tmux(checkout,project_root,tmp_path):
    import shutil
    repo=Path(os.environ.get('FORGE_WEB_REAL_CHECKOUT',str(Path(__file__).resolve().parents[3])))
    for name in ('forge','forge-watch'):
        shutil.copyfile(repo/'bin'/name,checkout.root/'bin'/name)
        (checkout.root/'bin'/name).chmod(0o755)
    cellar=tmp_path/'Cellar/tmux/3.5a/bin';cellar.mkdir(parents=True)
    shutil.copyfile(checkout.tools/'tmux',cellar/'tmux');(cellar/'tmux').chmod(0o755)
    cfg=checkout.cfg();cfg=replace(cfg,exe=replace(cfg.exe,tmux=cellar/'tmux'))
    env=child_env(cfg)
    roots=tmp_path/'roots.txt';roots.write_text(str(project_root)+'\n')
    env.update(FORGE_WATCH_ROOTS_FILE=str(roots),FORGE_WATCH_CONFIG_DIR=str(tmp_path/'config'),
               FORGE_WATCH_CACHE_DIR=str(tmp_path/'cache'),FORGE_WATCH_TRIGGER='0')
    result=subprocess.run([str(cfg.exe.forge),'board','--json'],cwd=project_root,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['schema']=='cc-board/1'
    chosen=subprocess.check_output(['python3','-c','import sys,yaml;print(sys.executable)'],env=env,text=True).strip()
    assert Path(chosen).parent == Path(sys.executable).parent