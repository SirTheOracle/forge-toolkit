#!/usr/bin/env python3
"""Focused P0 checks; no native provider launch, installation or live session access."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'tests/forge-bridge/p0-harness.py'
spec = importlib.util.spec_from_file_location('p0_harness', HELPER)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
BRIDGE = (ROOT / 'bin/forge-bridge').read_text()
RUNNER = (ROOT / 'tests/forge-bridge/run.sh').read_text()


def function(source, name):
    match = re.search(r'^' + re.escape(name) + r'\(\).*?^}', source, re.M | re.S)
    if not match:
        raise AssertionError('missing shell function: ' + name)
    return match.group() + "\n"


def shell(code, *args, env=None):
    return subprocess.run(['/bin/bash', '-c', code, 'p0', *map(str, args)],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=env, timeout=25)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = self.root / ".dev/proposals/p0/constraints.yml"
        self.ledger.parent.mkdir(parents=True)
        self.prompts = self.root / "prompts"; self.prompts.mkdir()
        (self.prompts / "review.txt").write_text('<<<INCLUDE _operator_constraints>>>\n')
        source = (ROOT / "bin/forge-bridge").read_text()
        funcs = ''.join(function(source, name) for name in ("_render_preamble", "_render_operator_constraints", "_render_template", "_constraint_carrier_stage"))
        start = source.index('    local _cg_mode="${FORGE_CONSTRAINTS_MODE:-enforce}"')
        end = source.index('    local tmpl_path="$FORGE_PROMPTS_DIR/$stage.txt"', start)
        self.script = self.root / "guard.sh"
        self.script.write_text(funcs + '\n_emit_event(){ printf "%s\\n" "$*" >> "$AUDIT"; }\n_sanitize_event_value(){ printf "%s" "$1"; }\nguard(){\n' + source[start:end] + '\n}\nif [ "$ENTRY" = renderer ]; then\n _render_operator_constraints "$project_root" "$slug"\nelse\n guard || exit $?\n printf "GUARD_HASH=%s\\n" "${_FORGE_P_CONSTRAINTS_SHA:-}"\n _render_template "$FORGE_PROMPTS_DIR/review.txt" "$slug" "$stage" codex-a 3 "$project_root"\nfi\n')
        self.env = dict(os.environ, project_root=str(self.root), slug="p0", stage="review", worker_canonical="codex-a", source_prompt="", FORGE_PROMPTS_DIR=str(self.prompts), AUDIT=str(self.root/"audit"), ENTRY="guard")

    def tearDown(self):
        self.tmp.cleanup()

    def run_case(self, mode, entry="guard", source_prompt=""):
        (self.root/"audit").unlink(missing_ok=True)
        p = subprocess.run(["bash", str(self.script)], env=dict(self.env, FORGE_CONSTRAINTS_MODE=mode, ENTRY=entry, source_prompt=source_prompt), capture_output=True, text=True)
        audit = (self.root/"audit").read_text() if (self.root/"audit").exists() else ""
        return p, audit

    def test_invalid_modes_guard_and_render_twin(self):
        cases = [None, 'schema: nope\n', 'schema: forge-constraints/1\nconstraints:\n- id: C1\n  source: operator\n  principle: p\n  scope: internal\n  check: c\n', 'schema: forge-constraints/1\nconstraints:\n' + ''.join('- id: C%d\n  source: inferred\n  principle: %s\n  scope: internal\n  check: c\n' % (i, "p"*200) for i in range(40))]
        for body in cases:
            with self.subTest(body=None if body is None else len(body)):
                if body is None: self.ledger.unlink(missing_ok=True)
                else: self.ledger.write_text(body)
                p, audit = self.run_case("enforce")
                self.assertEqual(p.returncode, 6, p.stdout+p.stderr)
                self.assertIn("GUARD_BLOCK", audit)
                p, audit = self.run_case("observe")
                self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
                self.assertIn("WARN: CONSTRAINT_LEDGER_REQUIRED (observe mode)", p.stderr)
                self.assertIn("OPERATOR CONSTRAINTS — UNAVAILABLE", p.stdout)
                self.assertIn("reason=constraints-ledger-invalid mode=observe", audit)
                p, audit = self.run_case("observe", entry="renderer")
                self.assertEqual(p.returncode, 0)
                self.assertIn("OPERATOR CONSTRAINTS — UNAVAILABLE", p.stdout)
                self.assertEqual(audit, "")
        p, _ = self.run_case("enfroce")
        self.assertEqual(p.returncode, 1)
        self.assertIn("must be enforce|observe", p.stderr)

    def test_valid_hash_and_source_prompt_refusal(self):
        self.ledger.write_text('schema: forge-constraints/1\nconstraints: []\nnone_recorded_reason: synthetic fixture\n')
        for mode in ("enforce", "observe"):
            p, audit = self.run_case(mode)
            self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
            self.assertEqual(audit, "")
            guard = re.search(r'GUARD_HASH=([0-9a-f]{16})', p.stdout).group(1)
            rendered = re.search(r'ledger_hash: ([0-9a-f]{16})', p.stdout).group(1)
            self.assertEqual(guard, rendered)
            p, audit = self.run_case(mode, source_prompt="raw.txt")
            self.assertEqual(p.returncode, 6)
            self.assertIn("source-prompt is not available", p.stderr)
            self.assertIn("source-prompt-on-carrier-stage", audit)



class P0Tests(unittest.TestCase):
    def test_inventory_archive_and_tracked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / 'bin', root / 'bin')
            (root / 'tests/forge-bridge').mkdir(parents=True)
            manifest = root / 'tests/forge-bridge/executable-inventory.txt'
            shutil.copy2(ROOT / manifest.relative_to(root), manifest)
            self.assertEqual(h.inventory(root), 3)
            backup = root / 'bin/forge.bak'
            shutil.copy2(root / 'bin/forge', backup)
            self.assertEqual(h.inventory(root), 3)
            self.assertTrue(backup.exists())
            extra = root / 'bin/new-real-tool'
            extra.write_text('#!/bin/bash\n# SECRETS = re.compile\n'); extra.chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'extra='):
                h.inventory(root)
            manifest.write_text(manifest.read_text() + 'bin/new-real-tool\n')
            with self.assertRaisesRegex(ValueError, 'count=4'):
                h.inventory(root)
            extra.unlink()
            with self.assertRaisesRegex(ValueError, 'missing='):
                h.inventory(root)
            # In a checkout, Git is the inventory; ignored backups never count.
            subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
            # Mirror the repo's real backup ignores: the global-edit protocol writes
            # <tool>.bak-pre-<label>-<timestamp>, which '*.bak' alone does not match (#97).
            (root / '.gitignore').write_text('*.bak\n*.bak-*\n')
            protocol_backup = root / 'bin/forge.bak-pre-fixture-20260101T000000Z'
            protocol_backup.write_text('#!/bin/bash\n# SECRETS = re.compile\n')
            protocol_backup.chmod(0o755)
            ignored = subprocess.run(['git', '-C', str(root), 'check-ignore', '-q',
                                      str(protocol_backup.relative_to(root))])
            self.assertEqual(ignored.returncode, 0,
                             'protocol-style backup must be ignored by the fixture .gitignore')
            subprocess.run(['git', '-C', str(root), 'add', '.gitignore', 'bin'], check=True)
            self.assertEqual(h.inventory(root), 3)
            extra.write_text('#!/bin/bash\n# SECRETS = re.compile\n'); extra.chmod(0o755)
            subprocess.run(['git', '-C', str(root), 'add', 'bin/new-real-tool'], check=True)
            with self.assertRaisesRegex(ValueError, 'count=4'):
                h.inventory(root)

    def test_loader_contract_and_hr24(self):
        start = RUNNER.index("sed -n '/^4\\. \\*\\*Usage")
        end = RUNNER.index('\n\n# ═', start)
        block = RUNNER[start:end]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('agents/forge-orchestrator.md', 'skills/forge-orchestrator/SKILL.md'):
                dest = root / name; dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, dest)
            code = 'ROOT="$1"; WORK="$1"; FAIL=0; ok(){ :; }; bad(){ FAIL=$((FAIL+1)); };\n' + block + '\nexit "$FAIL"'
            self.assertEqual(shell(code, root).returncode, 0)
            agent = root / 'agents/forge-orchestrator.md'; original = agent.read_text()
            agent.write_text(original.replace('in full and follow it exactly.', 'partially.'))
            self.assertEqual(shell(code, root).returncode, 2)
            agent.write_text(original.replace('a worker never self-certifies.', 'a worker self-certifies.'))
            self.assertEqual(shell(code, root).returncode, 1)

    def test_seed_dependents_not_run(self):
        start = RUNNER.index('  if [ "$(rc_of acmfloor-seed)" != 0 ]; then')
        end = RUNNER.index('  tmux kill-session -t "$DS"', start)
        code = '''rc_of(){ echo 1; }; bad(){ echo "FAIL $*"; }; not_run(){ echo "NOT RUN $*"; }
run_in_pane(){ echo UNEXPECTED_INJECTION; }
python3(){ :; }
''' + RUNNER[start:end]
        p = shell(code)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(p.stdout.count(b'NOT RUN'), 7)
        self.assertNotIn(b'UNEXPECTED_INJECTION', p.stdout)

    def test_redaction_full_bytes_and_bounded_command(self):
        secret = b'BEGIN\x00\xff\npassword="space secret" token=abc FORGE_API_TOKEN=envcanary --password clicanary\nBearer bbbbb\nsk-abcdefghijklmnopqrst\nEND'
        data = h.redact(secret)
        for sensitive in (b'space secret', b'abc', b'envcanary', b'clicanary', b'bbbbb', b'sk-abcdefghijklmnopqrst'):
            self.assertNotIn(sensitive, data)
        self.assertTrue(data.startswith(b'BEGIN\x00\xff'))
        self.assertTrue(data.endswith(b'END'))
        before = time.monotonic()
        result = h.command([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=0.1)
        self.assertLess(time.monotonic()-before, 2)
        self.assertIn('timed out', result['unavailable'])

    def test_redaction_infix_key_names_and_embedded_quotes(self):
        # F-1: sensitive term as an infix component of an underscore-joined identifier.
        self.assertNotIn(b'wJalrXUtnFEMI', h.redact(
            b'aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'))
        self.assertNotIn(b'sekritvalue123', h.redact(b'db_secret_key=sekritvalue123'))
        # F-2: a quoted value containing an embedded quote must be fully masked,
        # not truncated at the first embedded quote with a plaintext tail surviving.
        clean = h.redact(b'token: "abc"def123456789"')
        self.assertNotIn(b'def123456789', clean)
        self.assertNotIn(b'"abc"def123456789"', clean)
        # F-3: underscore-joined Authorization variant without a Bearer/Basic scheme.
        self.assertNotIn(b'abcSecretValue1234567890', h.redact(
            b'Proxy_Authorization: abcSecretValue1234567890'))

    def test_redaction_unquoted_value_has_no_plaintext_tail_after_delimiter(self):
        # F-5: the unquoted-value path must consume to a real terminator (end of
        # line, or end of an enclosing quoted string) rather than stopping at the
        # first comma/semicolon/brace *inside* the secret and leaving a tail.
        self.assertNotIn(b'def1234567890', h.redact(b'token=abc,def1234567890'))
        self.assertNotIn(b'def1234567890', h.redact(b'token: abc;def1234567890'))
        # An unquoted value ending in '}' adjacent to a JSON-like structure: the
        # brace must not be treated as a safe early stop when it sits inside,
        # rather than immediately after, the secret's own bytes.
        self.assertNotIn(b'def1234567890', h.redact(b'token=abc}def1234567890'))
        # Same defect class in the dedicated Authorization/Bearer path (line 27):
        # a comma or semicolon inside the credential itself must not truncate it.
        self.assertNotIn(b'def1234567890', h.redact(b'Authorization: Bearer abc,def1234567890'))
        self.assertNotIn(b'def1234567890', h.redact(b'Authorization: Bearer abc;def1234567890'))
        # F-1/F-2/F-3 repros must keep passing under the restructured terminator.
        self.assertNotIn(b'wJalrXUtnFEMI', h.redact(
            b'aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'))
        clean = h.redact(b'token: "abc"def123456789"')
        self.assertNotIn(b'def123456789', clean)
        self.assertNotIn(b'"abc"def123456789"', clean)
        self.assertNotIn(b'abcSecretValue1234567890', h.redact(
            b'Proxy_Authorization: abcSecretValue1234567890'))


    def test_tracked_missing_or_mode_changed_file_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT/'bin', root/'bin')
            subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
            subprocess.run(['git', '-C', str(root), 'add', 'bin'], check=True)
            target = root/'bin/forge-start'
            target.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'nonexecutable'):
                h.inventory(root)
            target.unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                h.inventory(root)

    def test_actual_wrapper_stops_before_direct_injection_and_cleanup(self):
        wrapper = function(RUNNER, 'run_in_pane')
        counters = RUNNER[RUNNER.index('PASS=0;'):RUNNER.index('\n\nexport FORGE_WATCH_TRIGGER=0')]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)/'work'; work.mkdir()
            evidence = Path(directory)/'retained'; evidence.mkdir()
            helper = Path(directory)/'fake-helper.py'
            # Driver must synchronously await retained capture, then exit before raw send.
            helper.write_text('import pathlib,sys\np=pathlib.Path(sys.argv[3]); (p/"COMPLETE").write_text("captured")\nsys.exit(124)\n')
            env = dict(os.environ, WORK=str(work), P0_EVIDENCE=str(evidence), P0_HELPER=str(helper))
            code = counters + '\n' + wrapper + r'''
trap 'test -f "$P0_EVIDENCE/COMPLETE" && printf retained > "$P0_EVIDENCE/trap-order"; rm -rf "$WORK"' EXIT
run_in_pane unused acmfloor-seed true
touch "$P0_EVIDENCE/late-direct-injection"
'''
            result = shell(code, env=env)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(b'1 failed, 8 not run', result.stdout)
            self.assertIn(b'remaining bridge suite', result.stdout)
            self.assertFalse((evidence/'late-direct-injection').exists())
            self.assertFalse(work.exists())
            self.assertEqual((evidence/'trap-order').read_text(), 'retained')
            # A successful marker whose command failed is not a timeout.
            helper.write_text('import pathlib,sys\n(pathlib.Path(sys.argv[2])/"out.seed").write_text("DONE_1\\n")\n')
            work.mkdir()
            result = shell(counters+'\n'+wrapper+'\nrun_in_pane unused seed false\necho AFTER_MARKER', env=env)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn(b'AFTER_MARKER', result.stdout)

    def test_evidence_root_rejected_and_json_remains_parseable(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'survive'):
                h.run(directory, str(Path(directory)/'retained'), 'unused', 'case', 'true')
            dest=Path(directory)/'record.json'
            h.write_json(dest, {'command': 'env API_KEY="private words" tool --password "other words"', 'tail': 'kept'})
            report=json.loads(dest.read_text())
            self.assertEqual(report['tail'], 'kept')
            self.assertNotIn('private words', dest.read_text())
            self.assertNotIn('other words', dest.read_text())

    def test_validator_exit_summary_seams_and_hash(self):
        spec = importlib.util.spec_from_file_location('p0_validate', ROOT/'tests/forge-bridge/p0-validate.py')
        validator = importlib.util.module_from_spec(spec); spec.loader.exec_module(validator)
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); root=base/'candidate'; (root/'tests/forge-bridge').mkdir(parents=True)
            runner=root/'tests/forge-bridge/run.sh'
            success_lines = ''.join('echo '+shlex.quote('  ok: '+name)+'\n' for name in validator.ACM_EXPECTED)
            cases=[('exit 0\n',1,None),
                   ("echo 'SKIP: tmux unavailable — real-tmux identity tests skipped'\necho 'forge-bridge: 65 passed, 0 failed'\n",1,{'passed':65,'failed':0,'not_run':0}),
                   ("echo 'forge-bridge: 2 passed, 1 failed, 0 not run'\nexit 1\n",1,{'passed':2,'failed':1,'not_run':0}),
                   ("echo 'forge-bridge: 2 passed, 0 failed, 1 not run'\n",1,{'passed':2,'failed':0,'not_run':1}),
                   ("env | grep -q '^FORGE_BRIDGE_TEST_' && exit 9\n"+success_lines+"echo 'forge-bridge: 7 passed, 0 failed, 0 not run'\n",0,{'passed':7,'failed':0,'not_run':0})]
            for i,(body,rc,counts) in enumerate(cases):
                runner.write_text(body)
                env={key:'forced' for key in ('FORGE_BRIDGE_TEST_SEED_FAIL','FORGE_BRIDGE_TEST_MISSING_MARKER','FORGE_BRIDGE_TEST_MARKER_TIMEOUT_S')}
                with patch.dict(os.environ,env):
                    result=validator.validate(root,base/('evidence-%d'%i))
                self.assertEqual(result,rc)
                report=json.loads((base/('evidence-%d'%i)/'validation-summary.json').read_text())
                self.assertEqual(report['counts'],counts)
                self.assertEqual(report['historical_acm'],'INSUFFICIENT EVIDENCE')
                self.assertEqual(len(report['candidate_content_sha256']),64)
                self.assertEqual(report['mandatory_acm_complete'],rc == 0)
                self.assertEqual(report['acceptance_state'],'complete' if rc == 0 else 'incomplete-or-failed')
                if rc:
                    self.assertIn('incomplete',report['acm_current_run'])
            runner.write_text("echo '# changed' >> \"$0\"\n"+success_lines+"echo 'forge-bridge: 7 passed, 0 failed, 0 not run'\n")
            self.assertEqual(validator.validate(root,base/'changed'),1)
    def test_authorization_secrets_raw_assignment_and_json(self):
        for scheme, credential in [('Bearer', 'header-canary.jwt.signature'), ('Basic', 'dXNlcjpwYXNz')]:
            for text in [f'Authorization: {scheme} {credential}',
                         f'authorization={scheme} {credential}',
                         f'Authorization="{scheme} {credential}"',
                         f'Proxy-Authorization: {scheme} {credential}']:
                with self.subTest(text=text):
                    raw = b'BEGIN\x00\xff\n' + text.encode() + b'\nEND'
                    clean = h.redact(raw)
                    self.assertNotIn(credential.encode(), clean)
                    self.assertTrue(clean.startswith(b'BEGIN\x00\xff\n'))
                    self.assertTrue(clean.endswith(b'\nEND'))
                    with tempfile.TemporaryDirectory() as directory:
                        dest = Path(directory)/'record.json'
                        h.write_json(dest, {'Authorization': f'{scheme} {credential}', 'command': text, 'nested': [{'value': text}], 'tail': 'END'})
                        result = json.loads(dest.read_text())
                        self.assertEqual(result['tail'], 'END')
                        self.assertNotIn(credential, dest.read_text())
        self.assertNotIn(b'standalone-canary', h.redact(b'Bearer standalone-canary'))

    def test_actual_exit_trap_cleans_all_fixture_families(self):
        wrapper = function(RUNNER, 'run_in_pane')
        cleanup = function(RUNNER, 'p0_cleanup')
        counters = RUNNER[RUNNER.index('PASS=0;'):RUNNER.index('\n\nexport FORGE_WATCH_TRIGGER=0')]
        # Check the declared trap keeps up with every uppercase construction variable.
        created = set(re.findall(r'mk_session "\$([A-Z][A-Z0-9_]*)"', RUNNER))
        created.update(re.findall(r'tmux new-session[^\n]* -s "\$([A-Z][A-Z0-9_]*)"', RUNNER))
        cleaned = set(re.findall(r'\$\{([A-Z][A-Z0-9_]*):-\}', cleanup))
        self.assertEqual(created, cleaned)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)/'work'; work.mkdir()
            evidence = Path(directory)/'retained'; evidence.mkdir()
            helper = Path(directory)/'driver.py'
            helper.write_text('import pathlib,sys\n(pathlib.Path(sys.argv[3])/"COMPLETE").touch()\nsys.exit(124)\n')
            env = dict(os.environ, WORK=str(work), P0_EVIDENCE=str(evidence), P0_HELPER=str(helper))
            code = counters + '\n' + wrapper + '\n' + cleanup + r'''
S1=owned-one; S2=""; RPS=owned-reap; GV9S=owned-verify; UNKBAD=owned-invalid
unset S3 S4 GS HS DS VS FS US SW RS HH SMS
tmux(){
    test -d "$WORK" && test -f "$P0_EVIDENCE/COMPLETE" || exit 98
    printf '%s\n' "$*" >> "$P0_EVIDENCE/kills"
}
trap p0_cleanup EXIT
run_in_pane unused reap-t1 true
touch "$P0_EVIDENCE/late-direct-action"
'''
            result = shell(code, env=env)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertFalse(work.exists())
            self.assertFalse((evidence/'late-direct-action').exists())
            self.assertEqual((evidence/'kills').read_text().splitlines(),
                             ['kill-session -t ='+name for name in ('owned-one','owned-reap','owned-verify','owned-invalid')])

    def test_abnormal_lock_capture_is_bounded_and_retains_complete(self):
        # Actual capture runs in a subprocess with an outer deadline. Only platform
        # introspection is stubbed; filesystem opens/flock and retained artifacts are real.
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); work=base/'work'; project=work/'project'
            locks=project/'.dev/forge-tmp/hygiene-locks'; locks.mkdir(parents=True)
            fifo=locks/'fixture.42.codex-a.lock'; os.mkfifo(fifo)
            directory_lock=locks/'directory.lock'; directory_lock.mkdir()
            unlocked=locks/'unlocked.lock'; unlocked.touch()
            held=locks/'held.lock'; held.touch()
            symlink=locks/'symlink.lock'; symlink.symlink_to(unlocked)
            (work/'out.forced').write_bytes(b'FIRST\x00\xff\nAuthorization: Bearer capture-canary\nLAST')
            evidence=base/'evidence'; evidence.mkdir()
            driver=base/'capture.py'
            driver.write_text('''import importlib.util,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('helper',sys.argv[1]); h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
h.command=lambda argv,timeout=2: {'argv':argv,'rc':0,'output':''}
os.environ['P0_REAL_TMUX']='/usr/bin/true'
work,evidence,project=map(Path,sys.argv[2:])
ident={'rc':0,'output':'fixture\\t42\\t%%fixture\\t%d\\t%s\\tbash' % (os.getpid(),project)}
h.capture(str(work),str(evidence),'%fixture','forced',h.utc(),ident,'Authorization: Basic capture-basic-canary')
''')
            with held.open('rb') as handle:
                h.fcntl.flock(handle, h.fcntl.LOCK_EX | h.fcntl.LOCK_NB)
                result=subprocess.run([sys.executable,str(driver),str(HELPER),str(work),str(evidence),str(project)],capture_output=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr)
            capture=next(evidence.glob('D1-*')); self.assertTrue((capture/'COMPLETE').exists())
            report=json.loads((capture/'diagnostic.json').read_text())
            rows={Path(row['path']).name:row for row in report['locks']}
            for name in (fifo.name,directory_lock.name,symlink.name):
                self.assertIn('non-regular',rows[name]['probe'])
            self.assertEqual(rows['unlocked.lock']['probe'],'unlocked at capture')
            self.assertIn('held at capture',rows['held.lock']['probe'])
            self.assertNotIn(b'capture-canary',(capture/'result.redacted.bin').read_bytes())
            self.assertNotIn('capture-basic-canary',(capture/'diagnostic.json').read_text())

    def test_lock_replacement_between_lstat_and_open_refuses(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'race.lock'; path.touch()
            original_open=os.open
            def replace_with_fifo(target,flags):
                self.assertTrue(flags & os.O_NONBLOCK)
                self.assertTrue(flags & os.O_NOFOLLOW)
                path.unlink(); os.mkfifo(path)
                return original_open(target,flags)
            with patch.object(h.os,'open',replace_with_fifo):
                row=h.inspect_lock(path)
            self.assertIn('non-regular',row['probe'])
            path.unlink(); path.touch()
            replacement=Path(directory)/'replacement'; replacement.touch()
            def replace_regular(target,flags):
                replacement.replace(path)
                return original_open(target,flags)
            with patch.object(h.os,'open',replace_regular):
                row=h.inspect_lock(path)
            self.assertIn('identity changed',row['probe'])

    def test_capture_excludes_roots_outside_the_fixture(self):
        # B2 (impl-review 2026-09-10): the X-ISOLATE/C5 guarantee is ONE filter line in
        # capture(). Drive a pane whose cwd AND whose command's `cd` target are outside the
        # fixture, with real lock files waiting at both. Delete the filter and this fails;
        # no other test's disposition changes, which is why it has to exist.
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); work=base/'work'; work.mkdir()
            outside=base/'outside'
            outside_locks=outside/'.dev/forge-tmp/hygiene-locks'; outside_locks.mkdir(parents=True)
            decoy=outside_locks/'fixture.42.codex-a.lock'; decoy.touch()
            usage_decoy=outside/'.dev'/'forge-usage.fixture.yml.lock'; usage_decoy.touch()
            (work/'out.forced').write_bytes(b'no marker here')
            evidence=base/'evidence'; evidence.mkdir()
            driver=base/'capture.py'
            driver.write_text('''import importlib.util,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('helper',sys.argv[1]); h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
h.command=lambda argv,timeout=2: {'argv':argv,'rc':0,'output':''}
os.environ['P0_REAL_TMUX']='/usr/bin/true'
work,evidence,outside=map(Path,sys.argv[2:])
ident={'rc':0,'output':'fixture\\t42\\t%%fixture\\t%d\\t%s\\tbash' % (os.getpid(),outside)}
h.capture(str(work),str(evidence),'%fixture','forced',h.utc(),ident,'cd %s && run' % outside)
''')
            result=subprocess.run([sys.executable,str(driver),str(HELPER),str(work),str(evidence),str(outside)],capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            capture=next(evidence.glob('D1-*'))
            report=json.loads((capture/'diagnostic.json').read_text())
            # Both candidate roots were outside the fixture, so NOTHING is in scope.
            self.assertEqual(report['expected_lock_paths'],[])
            self.assertEqual(report['locks'],[])
            self.assertEqual(report['lock_holders'].get('unavailable'),'no matching fixture lock files')
            # The decoys were genuinely reachable: exclusion emptied the set, not absence.
            self.assertTrue(decoy.exists() and usage_decoy.exists())
            self.assertTrue((capture/'COMPLETE').exists())

    def test_actual_no_tmux_branch_is_incomplete(self):
        begin=RUNNER.index('if ! command -v tmux >/dev/null 2>&1; then')
        end=RUNNER.index('\nfi',begin)+3
        counters=RUNNER[RUNNER.index('PASS=0;'):RUNNER.index('\n\nexport FORGE_WATCH_TRIGGER=0')]
        result=shell(counters+'\ncommand(){ return 1; };\n'+RUNNER[begin:end]+'\necho UNEXPECTED')
        self.assertEqual(result.returncode,1,result.stdout)
        self.assertIn(b'0 failed, 1 not run',result.stdout)
        self.assertNotIn(b'UNEXPECTED',result.stdout)

    @unittest.skipUnless(shutil.which('tmux'), 'tmux unavailable; forced native-pane diagnostic NOT RUN')
    def test_real_timeout_poison_alias_and_seed(self):
        real = shutil.which('tmux')
        session = 'fbp0-' + uuid.uuid4().hex[:12]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); work = root / 'work'; evidence = root / 'retained'
            work.mkdir(); evidence.mkdir()
            fixture = work / 'project'; fixture.mkdir()
            subprocess.run([real, 'new-session', '-d', '-s', session, '-c', str(fixture), '/bin/bash --noprofile --norc'], check=True)
            try:
                pane = subprocess.check_output([real, 'display-message', '-p', '-t', session + ':0.0', '#{pane_id}']).decode().strip()
                env = dict(os.environ, P0_REAL_TMUX=real, FORGE_BROKER_BIN='/usr/bin/true', FORGE_BROKER_CAPTURE=str(work/'broker'), FORGE_BRIDGE_TEST_MARKER_TIMEOUT_S='0.25')
                time.sleep(0.2)
                def invoke(name, cmd, extra=None, target=pane):
                    return subprocess.run([sys.executable, str(HELPER), 'run', str(work), str(evidence), target, name, cmd], env=dict(env, **(extra or {})), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
                p = invoke('good', 'printf READY')
                self.assertEqual(p.returncode, 0, p.stdout)
                self.assertIn(b'DONE_0', (work/'out.good').read_bytes())
                p = invoke('seed', 'true', {'FORGE_BRIDGE_TEST_SEED_FAIL': 'seed'})
                self.assertEqual(p.returncode, 0, p.stdout)
                self.assertIn(b'DONE_1', (work/'out.seed').read_bytes())
                p = invoke('timeout', "printf 'FIRST\\ntoken=private-canary\\nLAST\\n'", {'FORGE_BRIDGE_TEST_MISSING_MARKER': 'timeout'})
                self.assertEqual(p.returncode, 124, p.stdout)
                captures = list(evidence.glob('D1-*')); self.assertEqual(len(captures), 1)
                self.assertTrue((captures[0]/'COMPLETE').exists())
                data = (captures[0]/'result.redacted.bin').read_bytes()
                self.assertIn(b'FIRST', data); self.assertIn(b'LAST', data)
                self.assertNotIn(b'private-canary', data)
                report = json.loads((captures[0]/'diagnostic.json').read_text())
                self.assertIn('process_tree', report); self.assertIn('lock_holders', report)
                self.assertEqual(len(report['expected_lock_paths']), 5)
                p = invoke('alias', 'touch ' + shlex.quote(str(work/'should-not-exist')), target=session + ':0.0')
                self.assertEqual(p.returncode, 125)
                # Direct sends use the same guard, including C-c and alternate aliases.
                p = subprocess.run([sys.executable, str(HELPER), 'send', str(work), 'send-keys', '-t', session + ':0.0', 'C-c'], env=env)
                self.assertEqual(p.returncode, 125)
                self.assertFalse((work/'should-not-exist').exists())
                subprocess.run([real, 'split-window', '-d', '-t', session, '/bin/bash --noprofile --norc'], check=True)
                time.sleep(0.2)
                p = invoke('fresh', 'true', target=session + ':0.1')
                self.assertEqual(p.returncode, 0, p.stdout)
                self.assertEqual(len(list(evidence.glob('D1-*'))), 1)
                shutil.rmtree(work)
                self.assertTrue((captures[0]/'COMPLETE').exists())
            finally:
                subprocess.run([real, 'kill-session', '-t', session], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    unittest.main(verbosity=2)
