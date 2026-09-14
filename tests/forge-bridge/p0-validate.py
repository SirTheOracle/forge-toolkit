#!/usr/bin/env python3
"""One finite bridge gate with retained redacted evidence; no native provider runs."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('p0_harness', root/'tests/forge-bridge/p0-harness.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)


ACM_EXPECTED = ('T-ACM-SEND warns, records the real headroom, and DELIVERS (rc 0)', 'T-ACM-SEND the floor refuses a plain send at 40 with an explicit --force hint', 'T-ACM-SEND headroom == floor is PERMITTED (the floor is strictly-below, unlike the four inclusive knobs)', 'T-ACM-SEND --force at headroom 9 is PERMITTED under a floor of 50 (the BLOCKED path is untouchable)', 'T-ACM-SEND an unknown reading never triggers the floor', 'T-ACM-SEND the family override moves the warning point', 'T-ACM-SEND a pre-send measurement is never coverage for the new generation (F8)')


def snapshot(root):
    files = sorted(p for directory in ('bin', 'tests', 'skills', 'agents', 'commands', 'prompts', 'config', 'codex-skills', 'docs', 'swiftbar', 'hooks', 'orchestrator')
                   for p in (root/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    files += [root/'install.sh', root/'README.md']
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.exists()}


def validate(root, evidence):
    root, evidence = Path(root).resolve(), Path(evidence).resolve()
    if root == evidence or root in evidence.parents:
        raise ValueError('evidence directory must be outside the candidate tree')
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    hashes = snapshot(root)
    report = {'scope': 'P0 only; one instrumented bridge suite', 'started_at': helper.utc(),
              'candidate_files_sha256': hashes,
              'candidate_content_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
              'git_head': helper.command(['git', '-C', str(root), 'rev-parse', 'HEAD']),
              'historical_acm': 'INSUFFICIENT EVIDENCE',
              'parent_baseline': {'head': 'f8ad7a31aaa6aa402ea72e479c9fc64e959f12be', 'passed': 552, 'failed': 4, 'exit': 1},
              'other_suites': {name: 'NOT RUN in bounded P0 final runner' for name in (
                  'forge-start', 'forge-worktree', 'forge-cc', 'forge-cc/spawn', 'forge-broker',
                  'forge-watch', 'forge-infra-lock', 'forge-recover', 'forge-fix-runner',
                  'forge-sync-main', 'adversarial-skills', 'codex-policy-smoke', 'aggregate tests/run.sh')},
              'native_provider_loading_policy_children': 'NOT RUN; unmeasured',
              'new_host_direct_send_matrices': 'NOT APPLICABLE: P0 adds no host configuration'}
    helper.write_json(evidence/'candidate.json', report)
    env = dict(os.environ, FORGE_BRIDGE_EVIDENCE_DIR=str(evidence))
    for key in ('FORGE_BRIDGE_TEST_MISSING_MARKER', 'FORGE_BRIDGE_TEST_SEED_FAIL', 'FORGE_BRIDGE_TEST_MARKER_TIMEOUT_S'):
        env.pop(key, None)
    started = time.monotonic()
    proc = subprocess.run(['/bin/bash', str(root/'tests/forge-bridge/run.sh')], cwd=root,
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (evidence/'bridge.redacted.log').write_bytes(helper.redact(proc.stdout))
    text = proc.stdout.decode('utf-8', 'replace')
    summaries = re.findall(r'^forge-bridge: (\d+) passed, (\d+) failed(?:, (\d+) not run)?$', text, re.M)
    counts = dict(zip(('passed', 'failed', 'not_run'), [int(x or 0) for x in summaries[-1]])) if summaries else None
    captures = [p.name for p in evidence.glob('D1-*')]
    acm_lines = [line for line in text.splitlines() if 'T-ACM-SEND' in line]
    acm_passes = [line.strip()[4:].strip() for line in acm_lines if re.match(r'\s*ok:', line)]
    acm_missing = [name for name in ACM_EXPECTED if acm_passes.count(name) != 1]
    acm_complete = not acm_missing and not captures
    report.update(exit_code=proc.returncode, elapsed_seconds=time.monotonic()-started,
                  completed_at=helper.utc(), d1_captures=captures, counts=counts,
                  failures=[line for line in text.splitlines() if 'FAIL:' in line],
                  skips=[line for line in text.splitlines() if re.search(r'SKIP|skip:|NOT RUN', line)],
                  candidate_bytes_unchanged=(snapshot(root) == hashes),
                  acm_direct_send_assertions=acm_lines,
                  mandatory_acm_complete=acm_complete, missing_or_duplicate_acm_cells=acm_missing)
    if captures:
        report['acm_current_run'] = 'missing marker captured; gate stopped; bounded investigation required; initiating cause unproven'
    elif acm_complete:
        report['acm_current_run'] = 'not observed in this validation'
    else:
        report['acm_current_run'] = 'affected-path gate incomplete or failed; no nonrecurrence claim'
    report['validation_exit_code'] = proc.returncode or (0 if counts is not None and counts['failed'] == 0 and counts['not_run'] == 0 and report['candidate_bytes_unchanged'] and acm_complete else 1)
    report['acceptance_state'] = 'complete' if report['validation_exit_code'] == 0 else 'incomplete-or-failed'
    helper.write_json(evidence/'validation-summary.json', report)
    print('bridge exit=%s; validation exit=%s; evidence=%s' % (proc.returncode, report['validation_exit_code'], evidence))
    return report['validation_exit_code']


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: python3 tests/forge-bridge/p0-validate.py NEW_EVIDENCE_DIRECTORY')
    raise SystemExit(validate(root, sys.argv[1]))
