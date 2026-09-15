"""Cross-source regressions found during independent synthesis; no live Forge state."""
import asyncio
import hashlib
from pathlib import Path

import pytest
from forge_web import readers
from forge_web.actions import ActionRunner, Plan
from forge_web.discovery import Inventory
from forge_web.feed import attention_entries, response_for_dispatch
from forge_web.handles import ReceiptStore
from forge_web.sampler import BoardSampler
from conftest import write_json
from test_web_revision import collector, session, seed, CREATED, RID


def test_real_callback_uppercase_and_yaml_timestamp_bind_exact_bytes(project_root):
    path = project_root / '.dev/forge-tmp/callbacks/alpha-coding.forge-a.1700000000.callback'
    path.write_text("slug: alpha\nstage: coding\nstatus: BLOCKED\nworker: codex-a\ntimestamp: 2026-09-14T10:00:00Z\nsession: forge-a\n"
                    "incarnation: '1700000000'\ncallback_id: cb-real\n"
                    "selected_pending_timestamp: '2026-09-14T10:00:00Z'\nmessage: |\n  original\n")
    entry = {'to':'codex-a','stage':'coding', 'timestamp':'2026-09-14T10:00:00Z'}
    got = readers.callback_binding(str(project_root), 'alpha', entry, 'forge-a', CREATED)
    assert got == ('cb-real', hashlib.sha256(path.read_bytes()).hexdigest())
    path.write_text(path.read_text().replace("'2026-09-14T10:00:00Z'", '2026-09-14T10:00:00Z'))
    assert readers.callback_binding(str(project_root), 'alpha', entry, 'forge-a', CREATED)[0] == 'cb-real'
    path.write_text(path.read_text().replace('BLOCKED', 'PARKED'))
    assert readers.callback_binding(str(project_root), 'alpha', entry, 'forge-a', CREATED)[0] == 'cb-real'
    path.write_text('unreadable: [')
    assert readers.callback_binding(str(project_root), 'alpha', entry, 'forge-a', CREATED) == ('ambiguous', 'ambiguous')


def test_real_hyphen_dispatch_record_associates_response(project_root):
    write_json(project_root / '.dev/attention/dispatch-real-id.json', {
        'root':str(project_root), 'session':'forge-a', 'schema':'cc-dispatch/1','event':'dispatch',
        'dispatch_id':'real-id', 'dispatched_at':'2026-09-14T10:00:00Z'})
    (project_root / '.dev/attention/payloads/response.real-id.txt').write_text('actual reply')
    card = response_for_dispatch(str(project_root), 'forge-a', 'real-id', int(CREATED))
    assert card and card.dispatch_id == 'real-id' and card.text == 'actual reply'


def test_ask_and_feed_directory_consumers_do_not_starve(project_root):
    for name in ('forge-a','forge-b'):
        for n in range(450):
            write_json(project_root / f'.dev/attention/dispatch-{name}-{n}.json', {
                'root':str(project_root), 'session':name, 'schema':'cc-dispatch/1','event':'dispatch',
                'dispatch_id':str(n), 'dispatched_at':'2026-09-14T10:00:00Z'})
    found = {'forge-a':set(),'forge-b':set()}; seen = {'forge-a':{},'forge-b':{}}
    for _ in range(15):
        for name in found:
            readers.open_asks(str(project_root), name, int(CREATED))
            found[name].update(e.detail['dispatch_id'] for e in attention_entries(
                str(project_root), name=name, created_epoch=int(CREATED), seen=seen[name]))
    assert {name:len(ids) for name,ids in found.items()} == {'forge-a':450,'forge-b':450}


def test_live_root_sessionless_notice_does_not_claim_no_live_session(checkout, project_root):
    seed(project_root); c=collector(checkout)
    c._board={'hot':[{'condition':'NOTICE','root':str(project_root),'msg':'project notice'}]}
    c._publish(Inventory(sessions=[session(project_root)]))
    notice=c.snapshot().fleet.root_notices[0]
    assert 'Sessionless project evidence' in notice.detail
    assert 'no live session' not in notice.detail
    assert c.snapshot().fleet.sessions[0].board_items == []


def test_stale_source_cache_does_not_retain_absent_or_escaped_current_path(checkout, project_root, tmp_path):
    seed(project_root); c=collector(checkout); inv=Inventory(sessions=[session(project_root)])
    c._publish(inv)
    path=project_root/'.dev/forge-context.forge-a.yml';path.unlink()
    c._publish(inv)
    assert c.snapshot().fleet.sessions[0].stage.active_pipeline is None
    seed(project_root); c._publish(inv);path.unlink()
    foreign=tmp_path/'foreign.yml';foreign.write_text('active_pipeline: SECRET\n');path.symlink_to(foreign)
    c._publish(inv)
    assert c.snapshot().fleet.sessions[0].stage.active_pipeline is None
    assert 'SECRET' not in c.snapshot().fleet.model_dump_json()


async def test_cancelled_owner_keeps_serialization_until_child_finishes(checkout):
    runner=ActionRunner(checkout.cfg(), receipts=ReceiptStore(),sampler=BoardSampler(),
        live_identity=lambda _:asyncio.sleep(0), mutations_enabled=lambda:(True,''))
    release=asyncio.Event(); started=asyncio.Event(); calls=[]
    async def execute(argv,cwd):
        calls.append(argv);started.set();await release.wait()
        return 0,'SPAWNED @fixture','',True
    runner.execute=execute
    plan=Plan(action='spawn',argv=['fixture'],cwd=str(checkout.root),lock_key='one',lock_kind='root')
    first=await runner.submit('spawn',{'request_id':RID,'revision':0},lambda:plan,None)
    await started.wait();runner._live[first.operation_id].task.cancel()
    second=await runner.submit('spawn',{'request_id':'22222222-2222-4222-a222-222222222222','revision':0},lambda:plan,None)
    await asyncio.sleep(.05)
    assert len(calls)==1 and first.state.value=='running' and second.state.value=='queued'
    release.set()
    for _ in range(100):
        if not runner._live: break
        await asyncio.sleep(.01)
    assert len(calls)==2 and first.state.value=='exited' and not runner._live


async def test_drain_failure_does_not_return_before_child_exit(checkout, monkeypatch):
    release=asyncio.Event(); waiting=asyncio.Event()
    class Broken:
        async def read(self,n): raise OSError('read failed')
    class Empty:
        async def read(self,n): return b''
    class Child:
        stdout=Broken();stderr=Empty()
        async def wait(self): waiting.set();await release.wait();return 0
    async def launch(*args,**kwargs): return Child()
    monkeypatch.setattr(asyncio,'create_subprocess_exec',launch)
    runner=ActionRunner(checkout.cfg(),receipts=ReceiptStore(),sampler=BoardSampler(),
        live_identity=lambda _:asyncio.sleep(0),mutations_enabled=lambda:(True,''))
    work=asyncio.create_task(runner.execute(['fixture'],str(checkout.root)))
    await waiting.wait();assert not work.done()
    release.set()
    with pytest.raises(RuntimeError,match='capture failed after exit'): await work


def canonical_callback(project_root):
    path=project_root/'.dev/forge-tmp/callbacks/alpha-coding.forge-a.1700000000.callback'
    text=('slug: alpha\nstage: coding\nstatus: BLOCKED\nworker: codex\nsession: forge-a\n'
          'incarnation: "1700000000"\ncallback_id: cb-real\ntimestamp: 2026-09-14T10:00:00Z\n'
          'message: callback\nselected_pending_timestamp: "2026-09-14T10:00:00Z"\n')
    path.write_text(text)
    entry={'stage':'coding','to':'codex-a','timestamp':'2026-09-14T10:00:00Z'}
    return path,text,entry


@pytest.mark.parametrize('change', ['duplicate_id','duplicate_session','duplicate_status','nested','invalid_utf8','missing_worker','missing_selected','delivery','worker'])
def test_callback_schema_and_selection_fail_closed(project_root, change):
    path,text,entry=canonical_callback(project_root)
    assert readers.callback_binding(str(project_root),'alpha',entry,'forge-a',CREATED)[0]=='cb-real'
    edits={'duplicate_id':text+'callback_id: second\n','duplicate_session':text+'session: forge-a\n',
           'duplicate_status':text+'status: PARKED\n','nested':text.replace('worker: codex','worker: [codex]'),
           'missing_worker':text.replace('worker: codex\n',''),
           'missing_selected':text.replace('selected_pending_timestamp: "2026-09-14T10:00:00Z"\n',''),
           'delivery':text+'delivery_id: wrong\n','worker':text.replace('worker: codex','worker: sonnet')}
    path.write_bytes(text.encode()+b'\xff' if change=='invalid_utf8' else edits[change].encode())
    assert readers.callback_binding(str(project_root),'alpha',entry,'forge-a',CREATED)==('ambiguous','ambiguous')


def test_callback_eligible_paths_ignore_unrelated_and_select_sole_valid(project_root):
    path,text,entry=canonical_callback(project_root)
    (path.parent/'unrelated.callback').write_text('malformed: [')
    (path.parent/'alpha-coding.forge-a.callback').write_text('malformed: [')
    assert readers.callback_binding(str(project_root),'alpha',entry,'forge-a',CREATED)==('cb-real',hashlib.sha256(path.read_bytes()).hexdigest())
    path.unlink()
    assert readers.callback_binding(str(project_root),'alpha',entry,'forge-a',CREATED)==('ambiguous','ambiguous')


@pytest.mark.parametrize('field,value', [('dispatch_id','different'),('event','stop'),('schema','wrong'),('session','other'),('dispatched_at','2020-01-01T00:00:00Z')])
def test_reply_requires_exact_dispatch_writer_envelope(project_root, field, value):
    row={'schema':'cc-dispatch/1','event':'dispatch','root':str(project_root),'session':'forge-a',
         'dispatch_id':'real','dispatched_at':'2026-09-14T10:00:00Z'}
    path=project_root/'.dev/attention/dispatch-real.json';write_json(path,row)
    (project_root/'.dev/attention/payloads/response.real.txt').write_text('reply')
    assert response_for_dispatch(str(project_root),'forge-a','real',int(CREATED))
    row[field]=value;write_json(path,row)
    assert response_for_dispatch(str(project_root),'forge-a','real',int(CREATED)) is None


@pytest.mark.parametrize('stamp,ambiguous', [('2020-01-01T00:00:00Z',False),('not-a-time',True),('2035-01-01T00:00:00Z',True)])
def test_named_legacy_pending_never_becomes_current_after_recreation(checkout,project_root,stamp,ambiguous):
    from conftest import write_log
    seed(project_root);c=collector(checkout);c._publish(Inventory(sessions=[session(project_root)]))
    write_log(project_root,'alpha',[{'session':'forge-a','timestamp':stamp,'stage':'coding','response':None,'to':'codex-a'}])
    c._publish(Inventory(sessions=[session(project_root,sid='$9',created='2000000000')]))
    facts=c.snapshot().fleet.sessions[0].stage
    assert facts.session_pending_stage is None and facts.current_pending is None
    assert facts.ambiguous == ambiguous


def test_reordered_tasks_use_task_id_and_do_not_duplicate(checkout,project_root):
    seed(project_root);c=collector(checkout);inv=Inventory(sessions=[session(project_root)])
    def task(identity):return {'task_id':identity,'episode_id':'shared','root':str(project_root),'session':'forge-a','started_at':'2026-09-14T10:00:00Z','state':'running'}
    a,b=task('a'),task('b');c._board={'tasks':[a,b]};c._publish(inv)
    key=c.snapshot().fleet.sessions[0].identity.key
    before=[e.entry_id for e in c.snapshot().details[key].feed if e.kind=='tasks']
    assert len(before)==2
    c._board={'tasks':[task('c'),b,a,{'root':str(project_root)}]};c._publish(inv)
    after=[e.entry_id for e in c.snapshot().details[key].feed if e.kind=='tasks']
    assert len(after)==3 and set(before).issubset(after) and len(set(after))==3


def test_callback_duplicate_selected_pending_is_ambiguous(project_root):
    _path,_text,entry=canonical_callback(project_root)
    entry.update(session='forge-a',incarnation=CREATED,response=None)
    assert readers.callback_binding(str(project_root),'alpha',entry,'forge-a',CREATED,pending_entries=[entry,dict(entry)])==('ambiguous','ambiguous')


def test_active_slug_ack_preview_uses_exact_matcher(checkout,project_root):
    seed(project_root);c=collector(checkout)
    c._board={'hot':[{'condition':'NEEDS-ASK','root':str(project_root),'session':'forge-a','slug':'alpha','msg':'mine'}]}
    c._publish(Inventory(sessions=[session(project_root)]))
    detail=next(iter(c.snapshot().details.values()))
    assert detail.ack_scopes['slug'].target == 'alpha'
    assert len(detail.ack_scopes['slug'].visible)==1