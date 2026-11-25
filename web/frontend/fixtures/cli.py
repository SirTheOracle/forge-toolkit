#!/usr/bin/env python3
"""Disposable executable fixture. All state lives beside this script, never in user roots."""
import datetime
import json
import os
from pathlib import Path
import sys
import time
import uuid
import yaml

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / 'fixture-state.json'
args = sys.argv[1:]
name = Path(sys.argv[0]).name

def load():
    return json.loads(STATE.read_text())

def save(data):
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(data))
    temporary.replace(STATE)

def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace('+00:00', 'Z')

def value(flag, default=None):
    return args[args.index(flag)+1] if flag in args else default

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))

if args == ['web-contract-version']:
    print('forge-web-contract/1')
    raise SystemExit
state = load()
if name == 'tmux':
    if state.get('tmux_fail'):
        print('fixture tmux timeout', file=sys.stderr)
        raise SystemExit(1)
    for row in state['sessions']:
        print('\t'.join([row['name'], row['root'], row['id'], str(row['created'])]))
    raise SystemExit
with (BASE/'stub.log').open('a') as handle:
    handle.write('\t'.join([name, *args])+'\n')
if args[:2] == ['board', '--json']:
    if state.get('board_busy'):
        print(json.dumps({'schema': 'cc-board/1', 'busy': True, 'unavailable_reason': 'state_lock_busy'}))
        raise SystemExit(75)
    rows = []
    for s in state['sessions']:
        rows.append({'condition': 'NEEDS-ASK', 'class': 'hot', 'state': 'needs-ask', 'session': s['name'],
          'root': s['root'], 'slug': s['slug'], 'stage': 'coding', 'incarnation': str(s['created']),
          'msg': s['slug']+' needs input', 'acked': bool(state.get('acked'))})
    print(json.dumps({'schema':'cc-board/1', 'generated_at':stamp(), 'heartbeat':stamp(),
      'heartbeat_age_s':0, 'stale':False, 'hot':rows, 'active':[], 'parked':[],
      'tasks': [], 'episodes': [], 'maintenance':{'rows':[{'condition':'OLD', 'root':str(BASE/'other'/'fixture'), 'msg':'outside fleet', 'acked':False}]}}))
    raise SystemExit
if args[:1] in (['spawn'], ['seed-session']):
    root = value('--root'); chosen = value('--name', 'forge-e2e')
    existing = next((s for s in state['sessions'] if s['root'] == root), None) if args[0] == 'spawn' else None
    if existing:
        if existing['root'] != root:
            print('name collision', file=sys.stderr); raise SystemExit(1)
        print('ENSURE @'+existing['name']); raise SystemExit
    if any(s['name'] == chosen for s in state['sessions']):
        print('name collision', file=sys.stderr); raise SystemExit(1)
    slug = 'alpha' if not state['sessions'] else 'beta'
    s = {'name':chosen, 'root':root, 'id':state.pop('next_id', '$'+str(len(state['sessions'])+4)), 'created':1700000000, 'slug':slug}
    state['sessions'].append(s);save(state)
    dev=Path(root)/'.dev'
    (dev/f'forge-context.{chosen}.yml').write_text(yaml.safe_dump({'active_pipeline':slug, 'next_stage':'qa'}))
    (dev/f'forge-status.{chosen}.md').write_text('Status evidence for '+slug)
    p=dev/'proposals'/slug;p.mkdir(parents=True,exist_ok=True)
    (p/'problem-statement.md').write_text('# Objective\n\nBuild the '+slug+' capability for operators.\n')
    entry={'timestamp':'2026-09-14T10:00:00Z','stage':'coding','session':chosen,'incarnation':str(s['created']),'response':None,'to':'codex-a'}
    (p/'forge-log.yml').write_text(yaml.safe_dump({'entries':[entry]}))
    callback={**{k:v for k,v in entry.items() if k not in ('response','to')},'slug':slug,'status':'BLOCKED','worker':'codex-a','callback_id':'cb-'+slug,'selected_pending_timestamp':entry['timestamp'],'message':'callback'}
    (dev/'forge-tmp'/'callbacks'/f"{slug}-coding.{s['name']}.{s['created']}.callback").write_text(yaml.safe_dump(callback,sort_keys=False))
    write(dev/'attention'/f'ask-{slug}.json',{'schema':'cc-ask/1','event':'ask','ask_id':'ask-'+slug,'session':chosen,'root':root,'emitted_at':stamp(),'slug':slug,'stage':'coding','mode':'session-scope','question_sha256':'abc','question_text':'Choose a path for '+slug,'question_snippet':'Choose a path'})
    print('SPAWNED @'+chosen);raise SystemExit
if args[:1] == ['dispatch']:
    chosen=args[1].removeprefix('@');s=next(s for s in state['sessions'] if s['name']==chosen)
    if value('--expected-session-id')!=s['id'] or value('--expected-incarnation')!=str(s['created']):
        print('identity changed',file=sys.stderr);raise SystemExit(1)
    time.sleep(0.2)
    text=args[-1];did='cc-'+uuid.uuid4().hex[:10];dev=Path(s['root'])/'.dev'
    write(dev/'attention'/f'dispatch-{did}.json',{'schema':'cc-dispatch/1','event':'dispatch','session':chosen,'root':s['root'],'dispatch_id':did,'dispatched_at':stamp(),'instruction_snippet':text,'sender':'seat'})
    reply='reply: '+text
    rel=f'.dev/attention/payloads/response.{did}.txt'
    (Path(s['root'])/rel).write_text(reply)
    write(dev/'attention'/f'stop.{chosen}.json',{'event':'stop','session':chosen,'root':s['root'],'emitted_at':stamp(),'snippet':reply,'response_paths':[rel],'response_dispatch_ids':[did],'truncated':False,'full_bytes':len(reply.encode())})
    ask=value('--answers')
    if ask:
        path=dev/'attention'/f'{ask}.json'
        if path.exists(): path.rename(dev/'attention'/'archive'/path.name)
        print('answer archived cleanup=consumed')
    print(f'DISPATCHED @{chosen} pane=0 sender=seat dispatch_id={did}')
    raise SystemExit
if 'park' in args:
    s=next(s for s in state['sessions'] if s['name']==value('--target-session'))
    root=Path(s['root']);slug=value('--slug');path=root/'.dev'/'proposals'/slug/'forge-log.yml'
    data=yaml.safe_load(path.read_text());entry=data['entries'][0]
    import hashlib
    callback=root/'.dev/forge-tmp/callbacks'/f"{slug}-coding.{s['name']}.{s['created']}.callback"
    cb=yaml.safe_load(callback.read_text()) if callback.exists() else None
    expected_hash=hashlib.sha256(callback.read_bytes()).hexdigest() if cb else 'none'
    if (value('--expected-session-id') != s['id'] or value('--expected-incarnation') != str(s['created'])
            or value('--expected-pending-ts') != str(entry['timestamp'])
            or value('--expected-callback-id') != (cb['callback_id'] if cb else 'none')
            or value('--expected-callback-sha256') != expected_hash):
        print('expected identity or callback changed',file=sys.stderr);raise SystemExit(1)
    if '--resolve' in args:
        if not entry.get('parked_at'): raise SystemExit(1)
        entry['response']='resolved'
        if callback.exists(): callback.unlink()
        print('PARK_RESOLVED')
    else:
        if not cb or cb['status'] != 'BLOCKED': raise SystemExit(1)
        entry['parked_at']=stamp();cb['status']='PARKED'
        callback.write_text(yaml.safe_dump(cb,sort_keys=False))
        print('PARKED entry lock_release_state=RELEASED')
    path.write_text(yaml.safe_dump(data));raise SystemExit
if args[:1] == ['ack']:
    state['acked']=True;save(state)
    print('  acked: NEEDS-ASK  alpha needs input')
    print("Acknowledged 1 condition(s) for '%s'." % args[1]);raise SystemExit
print('fixture refuses unknown argv',file=sys.stderr)
raise SystemExit(2)