"""Run against the already started disposable fixture (never the real Forge service)."""
import argparse
import json
import time
import urllib.request
import uuid
from pathlib import Path

parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8917)
args=parser.parse_args();base=f'http://127.0.0.1:{args.port}'

def get(path):
    return json.load(urllib.request.urlopen(base+path))

def post(path,body):
    data=json.dumps(body).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'})))

def until(predicate):
    for _ in range(100):
        value=predicate()
        if value:return value
        time.sleep(.1)
    raise AssertionError('fixture did not converge')

fleet=get('/api/fleet');assert not fleet['sessions'], 'Start a fresh e2e-server fixture first'
root=fleet['spawn_roots'][0];assert '/.e2e-checkout/' in root['root']
instance=fleet['server_instance']

def action(path,body):
    envelope={'request_id':str(uuid.uuid4()),'revision':0,'expected_server_instance':instance,**body}
    initial=post(path,envelope)
    done=until(lambda: (r if (r:=get('/api/operations/'+initial['operation_id']))['state'] not in ('queued','running') else None))
    assert done['rc']==0,done
    return done,envelope

action('/api/spawn',{'root_key':root['key'],'name':'forge-e2e'})
fleet=until(lambda: (f if (f:=get('/api/fleet'))['sessions'] else None))
key=fleet['sessions'][0]['identity']['key'];detail=get('/api/sessions/'+key)
assert detail['stage']['active_pipeline']=='alpha'
ask=detail['asks'][0]
answer,_=action(f'/api/sessions/{key}/actions/answer',{'ask_key':ask['object_key'],'text':'choose path A'})
assert answer['components']['delivered']
reply=until(lambda: (d if (d:=get('/api/sessions/'+key))['responses'] else None))
assert reply['responses'][0]['text']=='reply: choose path A'
pending=reply['stage']['current_pending']
park,_=action(f'/api/sessions/{key}/actions/park',{'pending_key':pending['object_key'],'reason':'wait'})
parked=until(lambda: (d if (d:=get('/api/sessions/'+key))['stage']['parked'] else None))
action(f'/api/sessions/{key}/actions/resolve',{'pending_key':parked['stage']['parked'][0]['object_key'],'note':'done'})
get('/api/fleet');time.sleep(1)
detail=get('/api/sessions/'+key);assert detail['ack_scopes']['root_basename']['target']=='fixture'
ack,_=action(f'/api/sessions/{key}/actions/ack',{'scope':'root_basename'})
assert ack['components']['ack_outcome']=='applied',ack
pause,_=action(f'/api/sessions/{key}/actions/pause',{'control':'pause'});assert 'REQUESTED' in pause['summary']
resume,envelope=action(f'/api/sessions/{key}/actions/message',{'text':'forge-resume'})
retry=post(f'/api/sessions/{key}/actions/message',envelope)
assert retry['operation_id']==resume['operation_id'] and retry['duplicate']
state_path=Path(root['root']).parent/'fixture-state.json';state=json.loads(state_path.read_text());state['tmux_fail']=True;state_path.write_text(json.dumps(state))
unavailable=until(lambda: (f if (f:=get('/api/fleet'))['tmux_state']=='unavailable' else None));assert unavailable['sessions']
state['tmux_fail']=False;state_path.write_text(json.dumps(state))
print('PASS: served fixture spawn, live identity/objective, answer/reply, park/resolve, scoped ack, pause/resume, duplicate reconciliation and stale inventory')