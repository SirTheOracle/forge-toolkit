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
        self.root = Path(self.tmp.name).resolve()
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


# C1 web CLI regressions live here so all three shell suites can run them before
# constructing native tmux panes. These fixtures never contact a real tmux server.
class WebContractFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='forge-web-c1-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name).resolve()
        self.project = self.work/'project'
        self.project.mkdir()
        subprocess.run(['git','init','-q',str(self.project)],check=True)
        (self.project/'.claude').mkdir()
        (self.project/'.claude/forge-project.yml').write_text('name: web-c1\nforge:\n  expected_root: '+str(self.project)+'\n')
        self.bin = self.work/'bin'; self.bin.mkdir()
        self.calls = self.work/'calls.jsonl'
        self.state = self.work/'tmux.json'
        self.topology = {'web-a': {'session_name':'web-a','session_id':'$71','session_created':'1700000000','session_path':str(self.project),'pid':'991','pane_id':'%71','pane_index':'0','window_panes':'5','pane_dead':'0','pane_in_mode':'1'}}
        self.save_topology()
        self.env = {k:v for k,v in os.environ.items() if not k.startswith(('FORGE_','TMUX','ANTHROPIC_'))}
        self.env.update(HOME=str(self.work/'home'), PATH=str(self.bin)+os.pathsep+os.environ['PATH'], FORGE_WATCH_TRIGGER='0', FORGE_WORKER_HYGIENE_MODE='observe', FORGE_INFRA_LOCK_DIR=str(self.work/'locks'), WEB_STATE=str(self.state), WEB_CALLS=str(self.calls), FORGE_TMUX_PROBE_TIMEOUT_S='1')
        (self.work/'home').mkdir()
        # Preserve the already-installed PyYAML in a private HOME without changing
        # PYTHONPATH or installing anything. This also covers user-site installs.
        import yaml
        site_dir=Path(subprocess.check_output([sys.executable,'-c','import site;print(site.getusersitepackages())'],env=self.env,text=True).strip())
        site_dir.mkdir(parents=True,exist_ok=True)
        shutil.copytree(Path(yaml.__file__).parent,site_dir/'yaml')
        self.write_tool('tmux', '''import json,os,sys,time
from pathlib import Path
args=sys.argv[1:]; p=Path(os.environ['WEB_STATE']); state=json.loads(p.read_text())
with open(os.environ['WEB_CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
if os.environ.get('WEB_HANG')=='1':time.sleep(30);sys.exit(0)
cmd=args[0]; target=args[args.index('-t')+1] if '-t' in args else 'active-other'
name=target.lstrip('=').split(':')[0]
if name.startswith('%'):name=next((n for n,s in state.items() if s['pane_id']==name),'missing')
if cmd=='list-sessions':
 fmt=args[args.index('-F')+1]
 for row in state.values():
  text=fmt
  for k,v in row.items():text=text.replace('#{'+k+'}',v)
  print(text)
 sys.exit(0)
row=state.get(name)
if cmd=='has-session':sys.exit(0 if row else 1)
if cmd=='display-message':
 if target.startswith('=') and ':' not in target:
  print(args[-1].replace('#{pid}','991') if '#{session_' not in args[-1] else '|||991|');sys.exit(0)
 if not row:sys.exit(1)
 text=args[-1]
 for k,v in row.items():text=text.replace('#{'+k+'}',v)
 print(text);sys.exit(0)
if cmd=='show-environment':print('FORGE_LAYOUT=2');sys.exit(0)
if cmd=='list-panes':
 if not row:sys.exit(1)
 for i in range(int(row['window_panes'])):print(str(i)+' '+['claude','claude-opus','claude-sonnet','codex-a','codex-b'][i])
 sys.exit(0)
if cmd in ('select-pane','send-keys'):
 if not row:sys.exit(1)
 if cmd=='send-keys' and '-l' in args and os.environ.get('WEB_RECREATE')=='1':
  row['session_id']='$72';row['pane_id']='%72';p.write_text(json.dumps(state))
  Path(os.environ['WEB_STATE']+'.recreated').touch()
 sys.exit(0)
sys.exit(0)
''')
        self.write_tool('broker', '''import json,sys
p=json.load(sys.stdin)
print(json.dumps({'schema':'forge-session-incarnation-verification/1','session':p.get('session'),'expected_incarnation':p.get('expected_incarnation'),'live_incarnation':p.get('expected_incarnation'),'result':'match','status':'ok'}))
''')
        self.env['FORGE_BROKER_BIN']=str(self.bin/'broker')
        self.env['FORGE_TMUX_BIN']=str(self.bin/'tmux')
        self.forge=ROOT/'bin/forge';self.bridge=ROOT/'bin/forge-bridge'

    def save_topology(self):
        self.state.write_text(json.dumps(self.topology))

    def write_tool(self,name,body):
        p=self.bin/name;p.write_text('#!'+sys.executable+'\n'+body);p.chmod(0o755);return p

    def run_cli(self,*args,env=None,cwd=None,timeout=12):
        # Own the process group even on timeout: grandchildren cannot retain output
        # pipes or a fixture lock after the assertion fails.
        import signal
        p=subprocess.Popen(list(map(str,args)),cwd=cwd or self.project,env=dict(self.env,**(env or {})),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        try:out,_=p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);out,_=p.communicate();self.fail('timed out: '+repr(args)+'\n'+out)
        return p.returncode,out

    def route(self,expect=True,session='web-a',root=None):
        args=['--target-session',session,'--cross-session','--target-root',str(root or self.project)]
        if expect:args+=['--expected-session-id',self.topology[session]['session_id'],'--expected-incarnation',self.topology[session]['session_created']]
        return args

    def park(self,slug='s',resolve=False,extra=(),env=None,expect=True,cwd=None):
        return self.run_cli(self.bridge,*self.route(expect=expect),'park',*(['--resolve'] if resolve else []),'--','--slug',slug,'--stage','coding','--note' if resolve else '--reason','reason',*extra,env=env,cwd=cwd)

    def seed(self,slug='s',status='BLOCKED',origin='worker'):
        d=self.project/'.dev/proposals'/slug;d.mkdir(parents=True,exist_ok=True)
        ts='2026-09-14T00:00:00Z'
        log=d/'forge-log.yml'
        log.write_text('pipeline: '+slug+'\nentries:\n  - timestamp: "'+ts+'"\n    stage: coding\n    to: codex-a\n    session: web-a\n    incarnation: 1700000000\n    response: null\n'+('    parked_at: "2026-09-14T00:00:01Z"\n    parked_reason: hold\n    uncommitted: false\n' if status=='PARKED' else ''))
        cb=self.project/'.dev/forge-tmp/callbacks'/f'{slug}-coding.web-a.1700000000.callback';cb.parent.mkdir(parents=True,exist_ok=True)
        cb.write_text(f'slug: {slug}\nstage: coding\nstatus: {status}\nworker: codex-a\nsession: web-a\nincarnation: 1700000000\norigin: {origin}\ncallback_id: cb-{slug}\ntimestamp: 2026-09-14T00:00:01Z\nselected_pending_timestamp: "{ts}"\nmessage: blocked\n')
        return log,cb,ts

    def selected(self,cb,ts):
        import hashlib
        return ['--expected-pending-ts',ts,'--expected-callback-id',re.search(r'^callback_id: (.*)$',cb.read_text(),re.M)[1],'--expected-callback-sha256',hashlib.sha256(cb.read_bytes()).hexdigest()]

    def records(self):
        return [json.loads(x) for x in self.calls.read_text().splitlines()] if self.calls.exists() else []


class WebCCContractTests(WebContractFixture):
    def stub_bridge(self):
        self.env['FORGE_BRIDGE_BIN']=str(self.write_tool('bridge-spy', '''import json,os,signal,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['WEB_CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
if 'callback-consume' in args:
 mode=os.environ.get('WEB_CLEANUP','ok')
 if mode=='signal':os.kill(os.getpid(),signal.SIGTERM)
 if mode=='loud':
  print('CALLBACK_CONSUME NOOP',flush=True)
  sys.stdout.write('L'*300000);sys.stdout.flush()
  Path(os.environ['WEB_STATE']+'.drained').touch()
 if mode=='noop':print('CALLBACK_CONSUME NOOP')
 if mode=='failed':print('consume refused');sys.exit(1)
 sys.exit(0)
if 'send' in args:sys.exit(int(os.environ.get('WEB_SEND_RC','0')))
'''))

    def ask(self,mode='stage',session='web-a'):
        p=self.project/'.dev/attention/ask-c1.json';p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps({'schema':'cc-ask/1','ask_id':'ask-c1','mode':mode,'session':session,'slug':'s' if mode=='stage' else None,'stage':'coding' if mode=='stage' else None,'worker':'codex-a','question_sha256':'a'*64,'question_snippet':'question'}))
        return p

    def dispatch(self,text='answer',expect=True,answer=False,env=None,pointer=True):
        flags=['--expected-root',str(self.project),'--expected-session-id','$71','--expected-incarnation','1700000000'] if expect else []
        return self.run_cli(self.forge,'dispatch','@web-a','--sender','seat',*(['--pointer'] if pointer else []),*flags,*(['--answers','ask-c1'] if answer else []),'--',text,env=env)

    def test_T1_literal_pointer_and_fixed_control(self):
        import hashlib
        self.stub_bridge()
        for payload in ('--target-session evil --cross-session','@other $PATH `date` "quoted" — ünïcode','a\nb\tc\n--flag\n'):
            rc,out=self.dispatch(payload);self.assertEqual(rc,0,out)
            event=max((self.project/'.dev/attention').glob('dispatch-*.json'),key=lambda p:p.stat().st_mtime_ns)
            record=json.loads(event.read_text());body=(self.project/record['payload_path']).read_bytes()
            self.assertEqual(body,payload.encode());self.assertEqual(record['instruction_sha256'],hashlib.sha256(body).hexdigest())
            self.assertEqual((record['sender'],record['target_pane'],record['mode']),('seat',0,'pointer'))
        for phrase in ('forge-pause','forge-stop'):
            rc,out=self.dispatch(phrase,pointer=False);self.assertEqual(rc,0,out)
            record=json.loads(max((self.project/'.dev/attention').glob('dispatch-*.json'),key=lambda p:p.stat().st_mtime_ns).read_text())
            self.assertEqual(record['mode'],'inline');self.assertEqual(record['target_pane'],0);self.assertEqual(record['instruction_snippet'],phrase)
        for call in self.records():
            if 'send' in call:self.assertEqual(call[call.index('--')+1],'claude');self.assertEqual(call[call.index('--target-session')+1],'web-a')

    def test_T1_T2_refusals_precede_claim_and_writes(self):
        self.stub_bridge();ask=self.ask();before=ask.read_bytes()
        base=[self.forge,'dispatch','@web-a','--answers','ask-c1']
        cases=[['--'],['--','a','b'],['x','--','y'],['--','x','--','y']]
        cases += [['--expected-root',str(self.project),'--expected-session-id',v,'--expected-incarnation','1700000000','--','x'] for v in ('$1garbage','$1 2','$1.2','$-1','$','', '%1','@1')]
        cases += [['--expected-root',''],['--expected-session-id','$71'],['--expected-incarnation','bad']]
        for flag in ('--expected-root','--expected-session-id','--expected-incarnation'):
            rc,out=self.run_cli(*base,flag);self.assertNotEqual(rc,0);self.assertIn('requires a value',out)
        for args in cases:
            rc,out=self.run_cli(*base,*args);self.assertNotEqual(rc,0,(args,out));self.assertEqual(ask.read_bytes(),before)
        for change in ({'session_id':'$72'}, {'session_created':'1700000001'}, {'session_path':str(self.work)}, None):
            original=dict(self.topology['web-a'])
            if change is None:self.topology.clear()
            else:self.topology['web-a'].update(change)
            self.save_topology();rc,out=self.dispatch(answer=True)
            self.assertNotEqual(rc,0,out);self.assertIn('expectation failed',out);self.assertEqual(ask.read_bytes(),before)
            self.topology={'web-a':original};self.save_topology()
        self.assertFalse(list((self.project/'.dev/attention').glob('dispatch-*.json')))
        self.assertFalse(any('send' in x or 'callback-consume' in x for x in self.records()))

    def test_T4_cleanup_components_routing_and_delivery_failures(self):
        self.stub_bridge()
        for mode,want in [('ok','consumed'),('noop','noop'),('failed','failed'),('signal','unknown')]:
            for sendrc in (0,1):
                self.ask();self.calls.unlink(missing_ok=True)
                rc,out=self.dispatch(answer=True,env={'WEB_CLEANUP':mode,'WEB_SEND_RC':str(sendrc)})
                self.assertEqual(rc,sendrc,out);self.assertIn('outcome='+want,out)
                calls=self.records();self.assertEqual(sum('callback-consume' in x for x in calls),1);self.assertEqual(sum('send' in x for x in calls),1)
                for call in [x for x in calls if 'send' in x or 'callback-consume' in x]:
                    self.assertIn('--expected-session-id',call);self.assertIn('--expected-incarnation',call);self.assertIn('--target-root',call)
                self.assertTrue((self.project/'.dev/attention/archive/ask-c1.json').is_file())
                if sendrc:self.assertIn('PLAIN dispatch (no --answers)',out)
                if mode in ('failed','signal') and sendrc:self.assertIn('state is UNKNOWN',out);self.assertNotIn('callback consumed',out)
        self.ask();self.calls.unlink();rc,out=self.dispatch(expect=False,answer=True);self.assertEqual(rc,0,out)
        consume=next(x for x in self.records() if 'callback-consume' in x)
        self.assertEqual(consume,['callback-consume','--slug','s','--stage','coding','--status','BLOCKED'])
        self.ask(mode='session-scope');self.calls.unlink();rc,out=self.dispatch(answer=True);self.assertEqual(rc,0,out)
        self.assertFalse(any('callback-consume' in x for x in self.records()))

    def test_T4_wrong_session_and_concurrent_answer_claim(self):
        from concurrent.futures import ThreadPoolExecutor
        self.stub_bridge();ask=self.ask(session='other');before=ask.read_bytes()
        rc,out=self.dispatch(answer=True);self.assertNotEqual(rc,0);self.assertIn('belongs to session',out);self.assertEqual(ask.read_bytes(),before)
        self.ask();self.calls.unlink(missing_ok=True)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.dispatch(answer=True),range(2)))
        self.assertEqual(sorted(rc for rc,out in results),[0,1],results)
        self.assertEqual(sum('send' in x for x in self.records()),1)
        self.assertEqual(sum('callback-consume' in x for x in self.records()),1)

    def test_T21_capture_allocation_write_failure_and_drain(self):
        self.stub_bridge()
        real=shutil.which('mktemp')
        self.write_tool('mktemp', '''import os,subprocess,sys
if any('forge-consume.' in a for a in sys.argv):
 mode=os.environ.get('WEB_ALLOC','')
 if mode=='fail':sys.exit(1)
 if mode=='directory':print(os.environ['HOME']);sys.exit(0)
os.execv('''+repr(real)+''',['''+repr(real)+''',*sys.argv[1:]])
''')
        for mode in ('fail','directory'):
            self.ask();self.calls.unlink(missing_ok=True)
            rc,out=self.dispatch(answer=True,env={'WEB_ALLOC':mode})
            self.assertEqual(rc,0,out);self.assertIn('capture=unavailable',out);self.assertEqual(sum('send' in x for x in self.records()),1)
        # Save the capped temp BEFORE removal; output length alone is not evidence
        # because successful cleanup never prints its captured stdout.
        realrm=shutil.which('rm')
        self.write_tool('rm', '''import os,shutil,sys
for a in sys.argv[1:]:
 if 'forge-consume.' in a and os.path.isfile(a):shutil.copyfile(a,os.environ['WEB_STATE']+'.capture')
os.execv('''+repr(realrm)+''',['''+repr(realrm)+''',*sys.argv[1:]])
''')
        self.ask();rc,out=self.dispatch(answer=True,env={'WEB_CLEANUP':'loud','FORGE_ANSWER_CLEANUP_CAP':'8192'})
        self.assertEqual(rc,0,out);self.assertIn('outcome=noop',out)
        self.assertEqual(Path(str(self.state)+'.capture').stat().st_size,8192)
        self.assertTrue(Path(str(self.state)+'.drained').exists())

    def test_T12_forge_marker_and_help(self):
        rc,out=self.run_cli(self.forge,'web-contract-version');self.assertEqual((rc,out),(0,'forge-web-contract/1\n'))
        rc,out=self.run_cli(self.forge,'help');self.assertEqual(rc,0,out)
        for text in ('web-contract-version','--expected-session-id','forge board --json'):self.assertIn(text,out)
        self.assertEqual(out.rstrip().splitlines()[-1],'  forge gc [--root PATH] [--days N]')

class WebBridgeContractTests(WebContractFixture):
    def test_T7_T23_classes_expectations_and_legacy_switch(self):
        for enforce in ('0','1'):
            env={'FORGE_IDENTITY_ENFORCE':enforce}
            # A one-pane target proves target-file has no five-pane prerequisite.
            self.topology['web-a']['window_panes']='1';self.save_topology()
            rc,out=self.park(env=env);self.assertEqual(rc,0,out);self.assertIn('PARK NOOP',out)
            for flag,value in (('--expected-session-id','$999'),('--expected-incarnation','1'),('--target-root',str(self.work))):
                route=self.route();route[route.index(flag)+1]=value
                rc,out=self.run_cli(self.bridge,*route,'park','--','--slug','s','--stage','coding','--reason','r',env=env)
                self.assertNotEqual(rc,0,out);self.assertRegex(out,'EXPECTATION MISMATCH|is rooted at')
            self.topology['web-a']['window_panes']='5';self.save_topology()
            rc,out=self.run_cli(self.bridge,*self.route(expect=False),'send','claude','legacy',env=env)
            self.assertEqual(rc,0,out)
        for verb in ('infra-lock','dispatch','log','read','status','emit'):
            rc,out=self.run_cli(self.bridge,*self.route(),verb);self.assertEqual(rc,2,(verb,out));self.assertIn('only valid for',out)
        self.topology.clear();self.save_topology()
        rc,out=self.run_cli(self.bridge,'--target-session','web-a','--cross-session','--target-root',self.project,'--expected-session-id','$71','--expected-incarnation','1700000000','park','--','--slug','s','--stage','coding','--reason','r',env={'FORGE_IDENTITY_ENFORCE':'0'})
        self.assertNotEqual(rc,0,out)

    def test_T7b_pane_object_survives_same_second_name_reuse(self):
        for recreate in ('0','1'):
            self.calls.unlink(missing_ok=True)
            rc,out=self.run_cli(self.bridge,*self.route(),'send','claude','literal',env={'WEB_RECREATE':recreate})
            mutations=[x for x in self.records() if x[0] in ('select-pane','send-keys')]
            self.assertTrue(any('-l' in x for x in mutations),mutations)
            self.assertTrue(any(x[-1]=='Enter' for x in mutations),mutations)
            self.assertEqual({x[x.index('-t')+1] for x in mutations},{'%71'})
            self.assertEqual(rc,0 if recreate=='0' else 1,out)
        self.assertTrue(Path(str(self.state)+'.recreated').exists())
        self.assertEqual(json.loads(self.state.read_text())['web-a']['session_id'],'$72')
        self.assertEqual(json.loads(self.state.read_text())['web-a']['session_created'],'1700000000')

    def test_T8_bounding_harness_is_not_vacuous(self):
        with self.assertRaisesRegex(AssertionError,'timed out'):
            self.run_cli(sys.executable,'-c','import time;time.sleep(30)',timeout=.1)
        rc,out=self.run_cli('/bin/sh','-c','exit 23');self.assertEqual(rc,23,out)

    def test_T8_strict_parser_and_all_missing_operands(self):
        for flag in ('--target-session','--target-root','--expected-session-id','--expected-incarnation'):
            rc,out=self.run_cli(self.bridge,flag);self.assertEqual(rc,2,out);self.assertIn('requires a value',out)
        for verb,flags in [('park',('--slug','--stage','--reason','--worker','--note','--expected-pending-ts','--expected-callback-id','--expected-callback-sha256')),('callback-consume',('--slug','--stage','--status'))]:
            for flag in flags:
                rc,out=self.run_cli(self.bridge,verb,'--',flag);self.assertEqual(rc,2,out);self.assertIn('requires a value',out)
            for suffix in (['--typo','x'],['stray']):
                rc,out=self.run_cli(self.bridge,verb,'--',*suffix);self.assertEqual(rc,2,out)
        for flag in ('--_no_lock','--_closed-selection'):
            rc,out=self.run_cli(self.bridge,'callback-consume',flag);self.assertEqual(rc,2,out);self.assertIn('private callback-consume delegation flags',out)
        for badsid in ('$1garbage','$1 2','$1.2','$-1','$',''):
            route=self.route();route[route.index('--expected-session-id')+1]=badsid
            rc,out=self.run_cli(self.bridge,*route,'park');self.assertEqual(rc,2,out)
        for suffix in (['--expected-callback-id','cb-s'],['--expected-callback-id','none','--expected-callback-sha256','f'*64],['--expected-callback-id','none','--expected-callback-sha256','none'],['--expected-pending-ts','']):
            rc,out=self.run_cli(self.bridge,'park','--','--slug','s','--stage','coding','--reason','r',*suffix);self.assertEqual(rc,2,out)
        body=function(BRIDGE,'cmd_callback_consume')
        rc,out=self.run_cli('/bin/bash','-c',body+'\ncmd_callback_consume --slug s --stage coding --_closed-selection\nprintf "RC=%s\\nSHELL_ALIVE\\n" "$?"')
        self.assertEqual(rc,0,out);self.assertIn('RC=2\nSHELL_ALIVE',out)

    def test_T8_opaque_reason_note_after_routing_boundary(self):
        log,cb,ts=self.seed()
        rc,out=self.park(extra=['--reason','--target-session']);self.assertEqual(rc,0,out)
        self.assertIn('parked_reason: "--target-session"',log.read_text())
        rc,out=self.park(resolve=True,extra=['--note','--cross-session']);self.assertEqual(rc,0,out)
        self.assertIn('--cross-session',log.read_text());self.assertFalse(cb.exists())

    def authoritative_files(self):
        # Synchronization lock artifacts may be created by the rejected operation;
        # authoritative records and the event journal must remain byte-identical.
        return {str(p.relative_to(self.project)):p.read_bytes() for p in (self.project/'.dev').rglob('*')
                if p.is_file() and '/forge-tmp/locks/' not in str(p)}

    def test_T9_prelock_selection_refusals_leave_bytes(self):
        log,cb,ts=self.seed();before=(log.read_bytes(),cb.read_bytes());tree=self.authoritative_files()
        for flag,value in (('--expected-pending-ts','1999-01-01T00:00:00Z'),('--expected-callback-id','cb-replaced'),('--expected-callback-sha256','0'*64)):
            args=self.selected(cb,ts);args[args.index(flag)+1]=value
            rc,out=self.park(extra=args);self.assertNotEqual(rc,0,out);self.assertIn('EXPECTATION MISMATCH (pre-lock)',out)
            self.assertEqual((log.read_bytes(),cb.read_bytes()),before)
            self.assertEqual(self.authoritative_files(),tree)
        rc,out=self.park(extra=self.selected(cb,ts));self.assertEqual(rc,0,out);self.assertIn('PARKED slug=s',out)
        self.assertIn('status: PARKED',cb.read_text());self.assertIn('parked_at:',log.read_text())
        tree=self.authoritative_files()
        rc,out=self.park(resolve=True,extra=['--expected-callback-id','none','--expected-callback-sha256','none'])
        self.assertNotEqual(rc,0,out);self.assertIn("'none' was expected",out)
        self.assertEqual(self.authoritative_files(),tree)

    def race(self,slug,resolve,mutate,extra=(),expected='CALLBACK_IDENTITY_CHANGED',consume=False):
        import signal
        ready=self.work/(slug+'.ready');release=self.work/(slug+'.release')
        args=[str(self.bridge),*self.route(),'park',*(['--resolve'] if resolve else []),'--','--slug',slug,'--stage','coding','--note' if resolve else '--reason','r',*extra]
        if consume:
            args=[str(self.bridge),*self.route(),'callback-consume','--slug',slug,'--stage','coding','--status','BLOCKED']
        env=dict(self.env,FORGE_CALLBACK_PRELOCK_READY=str(ready),FORGE_CALLBACK_PRELOCK_RELEASE=str(release))
        original=self.authoritative_files()
        p=subprocess.Popen(args,cwd=self.project,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        try:
            deadline=time.monotonic()+6
            while not ready.exists() and p.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(ready.exists(),'prelock barrier not reached')
            self.assertEqual(self.authoritative_files(),original,'audit written before selection acceptance')
            mutate();after_mutation=self.authoritative_files();release.touch();out,_=p.communicate(timeout=8)
            self.assertNotEqual(p.returncode,0,out);self.assertIn(expected,out)
            self.assertEqual(self.authoritative_files(),after_mutation,'refusal wrote authoritative/event bytes')
            return out
        finally:
            release.touch()
            if p.poll() is None:os.killpg(p.pid,signal.SIGKILL)
            p.communicate()

    def test_T9_underlock_callback_and_pending_replacement(self):
        for part in ('callback-id','hash','pending','status','session-id'):
            slug='race-'+part;log,cb,ts=self.seed(slug);args=self.selected(cb,ts)
            def mutate():
                if part=='callback-id':cb.write_text(cb.read_text().replace('callback_id: cb-'+slug,'callback_id: replacement'))
                elif part=='hash':cb.write_text(cb.read_text()+'extra: changed\n')
                elif part=='pending':log.write_text(log.read_text().replace(ts,'2026-09-14T00:00:09Z'))
                elif part=='status':cb.write_text(cb.read_text().replace('status: BLOCKED','status: PARKED'))
                else:self.topology['web-a']['session_id']='$72';self.save_topology()
            self.race(slug,False,mutate,args)
            self.assertNotIn('parked_at:',log.read_text())
            self.topology['web-a']['session_id']='$71';self.save_topology()

    def test_T9_underlock_absence_changes_and_unchanged_absence(self):
        log,cb,ts=self.seed('appears',status='PARKED');raw=cb.read_bytes();cb.unlink()
        self.race('appears',True,lambda:cb.write_bytes(raw),['--expected-callback-id','none','--expected-callback-sha256','none'],expected='appeared')
        self.assertIn('response: null',log.read_text());self.assertEqual(cb.read_bytes(),raw)
        log,cb,ts=self.seed('vanishes',status='PARKED')
        self.race('vanishes',True,cb.unlink,expected='vanished');self.assertIn('response: null',log.read_text())
        log,cb,ts=self.seed('absent',status='PARKED');cb.unlink()
        rc,out=self.park('absent',resolve=True,extra=['--expected-callback-id','none','--expected-callback-sha256','none'])
        self.assertEqual(rc,0,out);self.assertIn('PARK RESOLVED',out);self.assertNotIn('response: null',log.read_text())

    def test_T9_ask_origin_and_ambiguous_selection(self):
        log,cb,ts=self.seed(origin='ask');before=(log.read_bytes(),cb.read_bytes())
        rc,out=self.park();self.assertNotEqual(rc,0,out);self.assertIn('operator ask',out);self.assertEqual((log.read_bytes(),cb.read_bytes()),before)
        log,cb,ts=self.seed('ambiguous')
        sibling=cb.parent/'ambiguous-coding.web-a.callback';sibling.write_text(cb.read_text().replace('incarnation: 1700000000\n',''))
        rc,out=self.park('ambiguous');self.assertNotEqual(rc,0,out);self.assertRegex(out,'AMBIGUOUS|ambiguous');self.assertNotIn('parked_at:',log.read_text())

    def test_T9_consume_selector_and_underlock_refusals_preserve_all_bytes(self):
        log,cb,ts=self.seed('consume-ambiguous')
        sibling=cb.parent/'consume-ambiguous-coding.web-a.callback'
        sibling.write_text(cb.read_text().replace('incarnation: 1700000000\n',''))
        before=self.authoritative_files()
        rc,out=self.run_cli(self.bridge,*self.route(),'callback-consume','--slug','consume-ambiguous','--stage','coding','--status','BLOCKED')
        self.assertNotEqual(rc,0,out);self.assertRegex(out,'AMBIGUOUS|ambiguous')
        self.assertEqual(self.authoritative_files(),before)
        log,cb,ts=self.seed('consume-race')
        self.race('consume-race',False,lambda:cb.write_text(cb.read_text()+'extra: changed\n'),consume=True)

    def test_T9_resolve_replacement_and_duplicate_audit_control(self):
        log,cb,ts=self.seed('resolve-race',status='PARKED')
        self.race('resolve-race',True,lambda:cb.write_text(cb.read_text().replace('callback_id: cb-resolve-race','callback_id: changed')),
                  self.selected(cb,ts),expected='callback changed')
        log,cb,ts=self.seed('accepted',status='PARKED')
        rc,out=self.park('accepted',resolve=True,extra=self.selected(cb,ts));self.assertEqual(rc,0,out)
        events=(self.project/'.dev/forge-tmp/orchestrator-events.log').read_text().splitlines()
        self.assertEqual(sum(line.startswith('IDENTITY:') for line in events),1,events)
        self.assertEqual(sum(line.startswith('CROSS:') for line in events),1,events)
        self.assertFalse(any('cmd=callback-consume' in line for line in events),events)

    def test_T7_legacy_pane_audit_survives_rollout_zero(self):
        rc,out=self.run_cli(self.bridge,*self.route(expect=False),'send','claude','legacy message',
                            env={'FORGE_IDENTITY_ENFORCE':'0'})
        self.assertEqual(rc,0,out)
        event=(self.project/'.dev/forge-tmp/orchestrator-events.log').read_text()
        self.assertIn('IDENTITY:',event);self.assertIn('cmd=send class=target-scoped',event)
        self.assertNotIn('CROSS:',event)

    def test_T15_resolve_delegation_archives_exact_callback(self):
        log,cb,ts=self.seed(status='PARKED')
        rc,out=self.park(resolve=True,extra=self.selected(cb,ts));self.assertEqual(rc,0,out)
        self.assertIn('PARK RESOLVED',out);self.assertFalse(cb.exists())
        archives=list((cb.parent/'archive').glob('s-coding.*.callback'));self.assertEqual(len(archives),1)
        self.assertNotIn('response: null',log.read_text())
        # The supersede path has other delivery prerequisites. Exercise its exact
        # delegated argv in-process with the real consume and real closed selection.
        log,cb,ts=self.seed('sup',status='BLOCKED')
        invocation='cmd_callback_consume --_no_lock --_closed-selection "$_selection" --slug "$slug" --stage "$_sup_stage" --status "$_st"'
        self.assertIn(invocation,function(BRIDGE,'cmd_dispatch'))
        # Run the exact delegated call with all production dependencies. There is
        # no public private-flag bypass: only definitions, never the CLI router.
        definitions=BRIDGE[:BRIDGE.index('_FORGE_TARGET="";')]
        setup=r'''
_FORGE_TARGET="";_FORGE_CROSS=0;_FORGE_TARGET_ROOT="";_FORGE_EXPECT_SID="";_FORGE_EXPECT_INC="";_FORGE_VALID_SESSION=""
project_root="$WEB_ROOT";slug=sup;_sup_stage=coding;_st=BLOCKED
require_identity callback-consume host-degrade || exit 8
_selection="$(_callback_mutation_select "$project_root/.dev/forge-tmp/callbacks" "$slug" "$_sup_stage" "$ID_target_session" "$ID_target_incarnation" "$_st" "$project_root/.dev/proposals/$slug/forge-log.yml")" || exit 8
_lifecycle_lock "$slug" "$_sup_stage" || exit 8
_patch_response_by_ts "$slug" "$WEB_TS" "FORGE_SUPERSEDED: fixture" || exit 8
'''
        script=self.work/'delegated.sh';script.write_text(definitions+setup+invocation+' || exit 8\n_lifecycle_unlock\necho DELEGATED\n')
        rc,out=self.run_cli('/bin/bash',script,env={'WEB_ROOT':str(self.project),'WEB_TS':ts})
        self.assertEqual(rc,0,out);self.assertIn('DELEGATED',out);self.assertFalse(cb.exists());self.assertIn('FORGE_SUPERSEDED',log.read_text())

    def test_T22_root_binding_and_exactly_eleven_cwd_gates(self):
        other=self.work/'other';other.mkdir();(other/'.claude').mkdir();(other/'.claude/forge-project.yml').write_text('name: other\n')
        for args in (['callback-consume','--slug','s','--stage','coding','--status','BLOCKED'],['park','--','--slug','s','--stage','coding','--reason','r'],['park','--resolve','--','--slug','s','--stage','coding','--note','n']):
            rc,out=self.run_cli(self.bridge,*self.route(),*args,cwd=other)
            self.assertNotEqual(rc,0,out);self.assertRegex(out,'do not belong to the validated target root|must be run from the project root')
            self.assertNotIn('CALLBACK_CONSUME NOOP',out)
        self.assertFalse((other/'.dev').exists(), 'a refused explicit operation wrote an audit or mutation under the wrong root')
        rc,out=self.run_cli(self.bridge,*self.route(),'callback-consume','--slug','s','--stage','coding','--status','BLOCKED')
        self.assertEqual(rc,0,out);self.assertIn('CALLBACK_CONSUME NOOP',out)
        self.assertEqual(len(re.findall(r'^\s*_require_root_cwd ',BRIDGE,re.M)),11)

    def test_T22_no_audit_before_root_binding_at_both_rollout_values(self):
        for enforce in ('0','1'):
            for expected in (False,True):
                other=self.work/('wrong-'+enforce+'-'+str(expected))
                (other/'.claude').mkdir(parents=True)
                (other/'.claude/forge-project.yml').write_text('name: other\n')
                for verb in (['callback-consume','--slug','s','--stage','coding','--status','BLOCKED'],
                             ['park','--','--slug','s','--stage','coding','--reason','r'],
                             ['park','--resolve','--','--slug','s','--stage','coding','--note','n']):
                    rc,out=self.run_cli(self.bridge,*self.route(expect=expected),*verb,cwd=other,
                                        env={'FORGE_IDENTITY_ENFORCE':enforce})
                    self.assertNotEqual(rc,0,out)
                    self.assertRegex(out,'do not belong to the validated target root|must be run from the project root')
                    self.assertFalse((other/'.dev').exists(),out)
                rc,out=self.run_cli(self.bridge,*self.route(expect=expected),'callback-consume',
                                    '--slug','missing','--stage','coding','--status','BLOCKED',
                                    env={'FORGE_IDENTITY_ENFORCE':enforce})
                self.assertEqual(rc,0,out);self.assertIn('CALLBACK_CONSUME NOOP',out)
                audit=(self.project/'.dev/forge-tmp/orchestrator-events.log').read_text()
                self.assertIn('IDENTITY:',audit);self.assertIn('CROSS:',audit)
                self.assertFalse((other/'.dev').exists())

    def test_T12_bridge_marker_side_effects_help_and_early_route(self):
        clean=self.work/'clean';clean.mkdir()
        for args in (['web-contract-version'],['web-contract-version','--target-session','x']):
            rc,out=self.run_cli(self.bridge,*args,cwd=clean);self.assertEqual((rc,out),(0,'forge-web-contract/1\n'));self.assertEqual(list(clean.iterdir()),[])
        self.assertFalse(self.calls.exists())
        rc,out=self.run_cli(self.bridge,'--target-session','x','--cross-session','web-contract-version');self.assertEqual(rc,2,out)
        rc,out=self.run_cli(self.bridge,'help');self.assertEqual(rc,0,out)
        self.assertEqual(out.count('web-contract-version'),1)
        before,after=out.split('Global routing',1)
        self.assertRegex(before,r'(?m)^  web-contract-version ');self.assertNotRegex(after,r'(?m)^  [a-z]');self.assertGreaterEqual(len(re.findall(r'^  --',after,re.M)),5)

    def test_T11_T20_event_provenance_and_injection(self):
        ev=self.project/'.dev/forge-tmp/orchestrator-events.log'
        rc,out=self.run_cli(self.bridge,'emit','STAGE','--slug','s','--stage','coding','note=ordinary value')
        self.assertEqual(rc,0,out);line=ev.read_text().splitlines()[-1]
        self.assertRegex(line,r'^STAGE: pipeline=s stage=coding note=ordinary.value event_at=\d{4}-\d\d-\d\dT[0-9:]+Z event_session=web-a event_session_id=\$71 event_incarnation=1700000000$')
        before=ev.read_bytes()
        cases=[['--stage','coding event_session=forged'],['--slug','s event_session=forged'],['k v=1'],['k\nevent_session=1']]
        for k in ('event_at','event_session','event_session_id','event_incarnation'):cases.extend([[k],[k+'=forged']])
        for args in cases:
            rc,out=self.run_cli(self.bridge,'emit','STAGE','--slug','injected',*args);self.assertNotEqual(rc,0,(args,out));self.assertEqual(ev.read_bytes(),before)
        self.topology.clear();self.save_topology()
        rc,out=self.run_cli(self.bridge,'emit','STAGE','--slug','unknown');self.assertEqual(rc,0,out)
        self.assertNotIn('event_session=',ev.read_text().splitlines()[-1])
        rc,out=self.run_cli(self.bridge,*['--target-session','missing','--cross-session','--target-root',str(self.project)],'park','--','--slug','s','--stage','coding','--reason','r')
        self.assertNotEqual(rc,0,out);self.assertLessEqual(ev.read_text().count('identity-mismatch'),1)

    def provenance_functions(self):
        names=('_bounded_line','_evp_snapshot','_event_provenance','_emit_event','_event_has_completion','_append_complete_event')
        return ''.join(function(BRIDGE,n) for n in names)+'\n'+re.search(r'^_valid_session_name\(\).*$',BRIDGE,re.M)[0]+'\n_EVP_KEY=""; _EVP_VALUE=""; _EVP_OUT=""\n'

    def test_T11_memo_negative_cache_side_effects_complete_dedup(self):
        # A counted resolver is the only seam; all provenance and COMPLETE code is real.
        code=self.provenance_functions()+r'''
_resolve_project_root(){ printf '%s' "$WEB_ROOT"; }
ownership_root(){ printf '%s' "$1"; }
timestamp(){ printf '2026-09-14T00:00:00Z'; }
_forge_identity(){ echo probe >> "$WEB_PROBES"; ID_state=MATCH; ID_target_session=web-a; ID_target_session_id='$71'; ID_target_incarnation=1700000000; ID_target_root="$WEB_ROOT"; }
ID_state='';ID_target_session=''
_event_provenance "$WEB_ROOT"; first="$_EVP_OUT"
_event_provenance "$WEB_ROOT"; _event_provenance "$WEB_ROOT"; _event_provenance "$WEB_ROOT"
[ -n "$first" ] && [ "$first" = "$_EVP_OUT" ] && [ -z "$ID_state" ] && [ -z "$ID_target_session" ] || exit 8
_append_complete_event "$WEB_ROOT" s cid-1 ' qualifier=none' || exit 8
_append_complete_event "$WEB_ROOT" s cid-1 ' qualifier=none' || exit 8
_EVP_KEY='';ID_state=MISMATCH;ID_target_session=forged
_event_provenance "$WEB_ROOT"; [ -z "$_EVP_OUT" ] || exit 8
_EVP_KEY='';ID_state=MATCH;ID_target_session=other;ID_target_root=/wrong
_event_provenance "$WEB_ROOT"; [ -z "$_EVP_OUT" ] || exit 8
_EVP_KEY='';ID_state='';_forge_identity(){ echo negative >> "$WEB_PROBES"; return 1; }
ID_target_session='';ID_target_session_id='';ID_target_incarnation='';ID_target_root=''
_event_provenance "$WEB_ROOT"; _event_provenance "$WEB_ROOT"; [ -z "$_EVP_OUT" ] || exit 8
echo REACHED
'''
        probes=self.work/'probes'
        rc,out=self.run_cli('/bin/bash','-c',code,env={'WEB_ROOT':str(self.project),'WEB_PROBES':str(probes)})
        self.assertEqual(rc,0,out);self.assertIn('REACHED',out);self.assertEqual(probes.read_text().splitlines(),['probe','negative'])
        lines=(self.project/'.dev/forge-tmp/orchestrator-events.log').read_text().splitlines()
        self.assertEqual(len(lines),1);self.assertIn('completion_id=cid-1',lines[0]);self.assertIn('event_session_id=$71',lines[0]);self.assertIn('event_at=',lines[0])

    def test_T11_T16_wedged_resolver_under_actual_lifecycle_lock(self):
        code=self.provenance_functions()+function(BRIDGE,'_lifecycle_lock')+re.search(r'^_lifecycle_unlock\(\).*$',BRIDGE,re.M)[0]+r'''
_resolve_project_root(){ printf '%s' "$WEB_ROOT"; }
ownership_root(){ printf '%s' "$1"; }
timestamp(){ printf '2026-09-14T00:00:00Z'; }
# A real shell process blocks, exactly as a hung legacy host/discovery probe.
_forge_identity(){ exec sleep 30; }
ID_state=MATCH;ID_target_session=web-a;ID_target_session_id='$71';ID_target_incarnation=1700000000;ID_target_root="$WEB_ROOT"
_lifecycle_lock s coding || exit 8
echo HELD
_emit_event SUPERSEDE_AUDIT s 'stage=coding reason=fixture'
# Force the ungated bounded backstop while the same lock is still held.
_EVP_KEY='';ID_state='';ID_target_session='';ID_target_session_id='';ID_target_incarnation='';ID_target_root=''
_emit_event STAGE s 'stage=coding'
_lifecycle_unlock
echo RELEASED
'''
        start=time.monotonic();rc,out=self.run_cli('/bin/bash','-c',code,env={'WEB_ROOT':str(self.project)},timeout=6)
        self.assertEqual(rc,0,out);self.assertIn('HELD\n',out);self.assertIn('RELEASED\n',out);self.assertLess(time.monotonic()-start,5)
        lines=(self.project/'.dev/forge-tmp/orchestrator-events.log').read_text().splitlines()
        self.assertEqual(len(lines),2);self.assertIn('event_session=web-a',lines[0]);self.assertNotIn('event_session=',lines[1])
        # Fail-open through the real public emit on each ungated identity branch.
        for env in ({'WEB_HANG':'1'},{'WEB_HANG':'1','TMUX':'fixture,0,0','TMUX_PANE':'%71'}):
            rc,out=self.run_cli(self.bridge,'emit','STAGE','--slug','hang',env=env,timeout=6);self.assertEqual(rc,0,out)
        self.assertNotIn('event_session=',(self.project/'.dev/forge-tmp/orchestrator-events.log').read_text().splitlines()[-1])
        # Emitter cannot create a directory where a file stands.
        other=self.work/'unwritable';(other/'.claude').mkdir(parents=True);(other/'.claude/forge-project.yml').write_text('name: unavailable\n');(other/'.dev').mkdir();(other/'.dev/forge-tmp').write_text('not a directory')
        rc,out=self.run_cli(self.bridge,'emit','STAGE','--slug','fail-open',cwd=other);self.assertEqual(rc,0,out)

    def test_T17_source_lockstep_is_supplementary(self):
        names=re.findall(r'^([A-Za-z_][A-Za-z0-9_]*)\(\) \{',BRIDGE,re.M)
        checked=[]
        for name in names:
            body=function(BRIDGE,name)
            if re.search(r'^\s*_lifecycle_lock\b',body,re.M) and re.search(r'^\s*_emit_event\b',body,re.M):
                gate=re.search(r'require_identity[^\n]*\|\|\s*(?:(?:return|exit)|\{[^}]*?return)',body)
                self.assertIsNotNone(gate,name);self.assertLess(gate.start(),re.search(r'^\s*_emit_event\b',body,re.M).start(),name);checked.append(name)
        self.assertIn('cmd_dispatch',checked)
        body=function(BRIDGE,'_append_complete_event');self.assertLess(body.index('_event_has_completion'),body.index('_event_provenance'));self.assertIn('os.fsync',body)
        self.assertIn('ID_state:-}" = "MATCH"',function(BRIDGE,'_event_provenance'))
        for line in BRIDGE.splitlines():
            if re.search(r'^\s*_emit_event\b',line):self.assertNotRegex(line,r'\bevent_(at|session|session_id|incarnation)=')

class WebInfraTargetTests(WebContractFixture):
    def acquire_as(self,session='web-a',sid='$71',created='1700000000'):
        env={'FORGE_LOCK_SELF_HOST':'web-host','FORGE_LOCK_SELF_SESSION':session,'FORGE_LOCK_SELF_SESSION_ID':sid,'FORGE_LOCK_SELF_SESSION_CREATED':created,'FORGE_LOCK_SELF_TMUX_PID':'991'}
        rc,out=self.run_cli(self.bridge,'infra-lock','acquire','--slug','s','--stage','coding',env=env)
        self.assertEqual(rc,0,out);return env


    def test_T10_target_identity_and_original_seam_precedence(self):
        body=function(BRIDGE,'_infra_lock_identity')
        funcs=function(BRIDGE,'_bounded_line')+'\n_tmux_probe(){ _bounded_line tmux "$@"; }\n'+body
        prefix='ownership_root(){ printf "%s" "$1"; }\n_FORGE_VALID_SESSION=web-a;_FORGE_VALID_SID=\'$71\';_FORGE_VALID_CREATED=1700000000;_FORGE_VALID_ROOT='+shlex.quote(str(self.project))+'\n'
        rc,out=self.run_cli('/bin/bash','-c',funcs+prefix+'_infra_lock_identity')
        self.assertEqual(rc,0,out);self.assertIn('|web-a|$71|1700000000|991',out)
        self.assertTrue(any('-t' in a and '=web-a:' in a for a in self.records()))
        rc,out=self.run_cli('/bin/bash','-c',funcs+prefix+'_infra_lock_identity',env={'FORGE_LOCK_SELF_HOST':'seam-host','FORGE_LOCK_SELF_SESSION':'seam','FORGE_LOCK_SELF_SESSION_ID':'$3','FORGE_LOCK_SELF_SESSION_CREATED':'4','FORGE_LOCK_SELF_TMUX_PID':'5'})
        self.assertEqual((rc,out),(0,'seam-host|seam|$3|4|5'))
        self.topology['web-a']['session_id']='$72';self.save_topology()
        rc,out=self.run_cli('/bin/bash','-c',funcs+prefix+'_infra_lock_identity');self.assertNotEqual(rc,0,out)
        rc,out=self.run_cli('/bin/bash','-c',funcs+'_infra_lock_identity');self.assertNotEqual(rc,0,out)

    def test_T10_explicit_park_releases_target_holder_and_preserves_foreign_holder(self):
        import yaml,socket
        holder=self.work/'locks/infra.holder'
        for expect in (True,False):
            # Populate a real lock record with the existing public data-only seam;
            # explicit park must release it using the real validated target channel.
            self.seed();holder.unlink(missing_ok=True)
            env=self.acquire_as();data=yaml.safe_load(holder.read_text());data['host']=socket.gethostname();holder.write_text(yaml.safe_dump(data))
            rc,out=self.park(expect=expect);self.assertEqual(rc,0,out);self.assertIn('lock_release_state=RELEASED',out);self.assertFalse(holder.exists())
            rc,out=self.park(resolve=True,expect=expect);self.assertEqual(rc,0,out);self.assertIn('PARK RESOLVED',out)
        self.seed();holder.unlink(missing_ok=True)
        self.acquire_as('other','$88','1700000000');before=holder.read_bytes()
        rc,out=self.park();self.assertNotEqual(rc,0,out);self.assertIn('PARK PARTIAL',out);self.assertIn('lock_release_state=RELEASE_NOOP_NOT_OWNER',out);self.assertEqual(holder.read_bytes(),before)
        self.assertIn('parked_at:',(self.project/'.dev/proposals/s/forge-log.yml').read_text())
        rc,out=self.run_cli(self.bridge,*self.route(),'infra-lock','status');self.assertEqual(rc,2,out)


class WebInventoryContractTests(unittest.TestCase):
    def test_T13_launcher_inventory_both_real_modes(self):
        launcher=ROOT/'bin/forge-web';manifest=(ROOT/'tests/forge-bridge/executable-inventory.txt').read_text().splitlines()
        self.assertIn('bin/forge-web',manifest);self.assertEqual(manifest,sorted(set(manifest)))
        self.assertTrue(launcher.is_file() and not launcher.is_symlink());self.assertTrue(os.access(launcher,os.X_OK));self.assertNotIn(b'SECRETS = re.compile',launcher.read_bytes())
        with tempfile.TemporaryDirectory() as td:
            archive=Path(td)/'archive';shutil.copytree(ROOT/'bin',archive/'bin');(archive/'tests/forge-bridge').mkdir(parents=True)
            man=archive/'tests/forge-bridge/executable-inventory.txt';man.write_text('\n'.join(manifest)+'\n')
            self.assertEqual(h.inventory(archive),3)
            man.write_text('\n'.join(x for x in manifest if x!='bin/forge-web')+'\n')
            with self.assertRaisesRegex(ValueError,'extra='):h.inventory(archive)
            man.write_text('\n'.join(manifest)+'\n')
            subprocess.run(['git','init','-q',str(archive)],check=True);subprocess.run(['git','-C',str(archive),'add','--',*manifest],check=True)
            self.assertEqual(h.inventory(archive),3)
            row=subprocess.check_output(['git','-C',str(archive),'ls-files','-s','bin/forge-web'],text=True);self.assertTrue(row.startswith('100755 '),row)
            # Fixture Git mode is immediate C1 coverage even before a developer stages
            # the current checkout. Check the real index only once it contains the file.
            real=subprocess.run(['git','-C',str(ROOT),'ls-files','-s','bin/forge-web'],capture_output=True,text=True)
            if real.stdout:self.assertTrue(real.stdout.startswith('100755 '),real.stdout)

    def test_T25_pinned_graph_is_declared_before_server_exec(self):
        source=(ROOT/'bin/forge-web').read_text()
        required={'FORGE_BIN':'forge','FORGE_BRIDGE_BIN':'forge-bridge','FORGE_WATCH_BIN':'forge-watch','FORGE_START_BIN':'forge-start','FORGE_BROKER_BIN':'forge-broker','FORGE_CODEX_DOCTOR_BIN':'forge'}
        for var,exe in required.items():
            self.assertRegex(source,rf'{var}="\$BIN_DIR/{exe}"')
        self.assertIn('FORGE_FORGE_BIN="$BIN_DIR/forge"',source)
        self.assertIn('FORGE_TMUX_BIN="$TMUX_BIN"',source)
        # The C2 launcher integration suite also executes backend loading. Here C1
        # directly invokes the launcher from a symlink with no web files installed.
        # It must fail with setup guidance, never start/install or use ambient pins.
        with tempfile.TemporaryDirectory() as td:
            work=Path(td);repo=work/'checkout';(repo/'bin').mkdir(parents=True)
            for name in set(required.values())|{'forge-web'}:shutil.copy2(ROOT/'bin'/name,repo/'bin'/name)
            link=work/'forge-web';link.symlink_to(repo/'bin/forge-web')
            poison=work/'poison';poison.write_text('#!/bin/sh\necho POISON >&2\nexit 99\n');poison.chmod(0o755)
            env=dict(os.environ,HOME=str(work),**{var:str(poison) for var in required})
            p=subprocess.run([str(link)],env=env,capture_output=True,text=True,timeout=8)
            self.assertNotEqual(p.returncode,0);self.assertNotIn('POISON',p.stdout+p.stderr)
            self.assertIn('web',p.stderr);self.assertEqual(list(repo.iterdir()),[repo/'bin'])


def web_function(source, name):
    """Extract a column-zero shell function, including single-line definitions."""
    match=re.search(r'^'+re.escape(name)+r'\(\) \{[^\n]*',source,re.M)
    if not match: raise AssertionError('missing shell function: '+name)
    if re.search(r'}(?:\s*#.*)?\s*$',match.group()): return match.group()+'\n'
    end=re.search(r'^}',source[match.end():],re.M)
    if not end: raise AssertionError('unterminated shell function: '+name)
    return source[match.start():match.end()+end.end()]+'\n'


class WebNativeFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='forge-web-contract-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        (self.project / '.claude').mkdir()
        (self.project / '.claude/forge-project.yml').write_text(
            'name: web-test\nforge:\n  expected_root: '+str(self.project)+'\n')
        (self.project / '.dev/forge-tmp/callbacks').mkdir(parents=True)
        (self.project / '.dev/proposals').mkdir()
        self.env = {k:v for k,v in os.environ.items()
                    if not k.startswith(('FORGE_', 'TMUX')) and k not in ('SESSION', 'WINDOW')}
        self.env.update(FORGE_WATCH_TRIGGER='0', FORGE_WORKER_HYGIENE_MODE='observe',
                        FORGE_INFRA_LOCK_DIR=str(self.base/'locks'),
                        FORGE_SEND_COMMIT_WAIT_S='0', FORGE_TMUX_PROBE_TIMEOUT_S='1')
        self.bin = self.base/'bin'; self.bin.mkdir()
        self.env['PATH'] = str(self.bin)+os.pathsep+self.env['PATH']
        self.forge = str(ROOT/'bin/forge')
        self.bridge = str(ROOT/'bin/forge-bridge')
        self.name='forge-web-test'; self.sid='$123'; self.inc='1700000000'
        self.topology = self.base/'sessions.tsv'
        self.topology.write_text(f'{self.name}\t{self.project}\t{self.sid}\t{self.inc}\n')
        self.env['FORGE_TMUX_LIST'] = str(self.topology)
        self.calls = self.base/'calls.jsonl'
        self.stub = self.script('bridge-stub', '''import json,os,signal,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['WEB_CALLS'],'a') as f: f.write(json.dumps(args)+'\\n')
if 'callback-consume' in args:
    mode=os.environ.get('WEB_CLEANUP','consumed')
    if mode=='signal': os.kill(os.getpid(),signal.SIGTERM)
    if mode=='loud':
        sys.stdout.write('L'*300000); sys.stdout.flush()
        Path(os.environ['WEB_DRAINED']).touch()
    print('CALLBACK_CONSUME NOOP' if mode=='noop' else 'CALLBACK_CONSUMED')
    sys.exit(1 if mode=='failed' else 0)
sys.exit(int(os.environ.get('WEB_SEND_RC','0')))
''')
        self.env.update(FORGE_BRIDGE_BIN=str(self.stub), WEB_CALLS=str(self.calls),
                        WEB_DRAINED=str(self.base/'drained'), FORGE_BIN=self.forge)

    def script(self, name, body):
        path=self.bin/name
        path.write_text('#!'+sys.executable+'\n'+body)
        path.chmod(0o755)
        return path

    def run_cmd(self, argv, *, env=None, cwd=None, timeout=20):
        import signal
        child=subprocess.Popen(list(map(str,argv)), cwd=cwd or self.project,
                               env=dict(self.env, **(env or {})), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
        try: out=child.communicate(timeout=timeout)[0]
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL); out=child.communicate()[0]
            self.fail('deadline exceeded: %r\n%s' % (argv,out.decode(errors='replace')))
        return child.returncode, out.decode(errors='replace')

    def bash(self, code, *args, **kwargs):
        return self.run_cmd(['/bin/bash','-c',code,'web',*args], **kwargs)

    def assert_ok(self, result):
        self.assertEqual(result[0],0,result[1]); return result[1]

    def ask(self, mode='stage'):
        attention=self.project/'.dev/attention'; attention.mkdir(exist_ok=True)
        aid='ask-20260914T000000Z-bbbbbb'
        record={'schema':'cc-ask/1','event':'ask','variant':'ask','ask_id':aid,
                'session':self.name,'root':str(self.project),'mode':mode,
                'slug':'s1' if mode=='stage' else None,
                'stage':'coding' if mode=='stage' else None,'worker':'codex-a',
                'asked_at':'2026-09-14T00:00:00Z','question_snippet':'which?'}
        (attention/(aid+'.json')).write_text(json.dumps(record))
        return aid

    def dispatch(self, *args, **kwargs):
        return self.run_cmd([self.forge,'dispatch','@'+self.name,'--allow-api-billing',*args],**kwargs)

    def expectations(self):
        return ['--expected-root',str(self.project),'--expected-session-id',self.sid,
                '--expected-incarnation',self.inc]

    def recorded_calls(self):
        return [json.loads(l) for l in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def native(self, panes=1):
        real=shutil.which('tmux')
        self.assertIsNotNone(real, 'tmux required; native acceptance was not run')
        self.socket=str(self.base/'tmux.sock')
        self.script('tmux', 'import os,sys\nos.execv('+repr(real)+', ['+repr(real)+
                    ', "-f", "/dev/null", "-S", '+repr(self.socket)+']+sys.argv[1:])\n')
        self.env.pop('FORGE_TMUX_LIST',None)
        self.addCleanup(lambda: subprocess.run([real,'-S',self.socket,'kill-server'],
                                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        self.assert_ok(self.run_cmd(['tmux','new-session','-d','-s',self.name,'-x','220','-y','60',
                                    '-c',self.project,'-e','FORGE_LAYOUT=2','/bin/cat']))
        for i in range(1,panes):
            self.assert_ok(self.run_cmd(['tmux','split-window','-d','-t',self.name+':0',
                                        '-c',self.project,'/bin/cat']))
            self.assert_ok(self.run_cmd(['tmux','select-layout','-t',self.name+':0','tiled']))
        self.sid=self.assert_ok(self.run_cmd(['tmux','display-message','-p','-t','='+self.name+':','#{session_id}'])).strip()
        self.inc=self.assert_ok(self.run_cmd(['tmux','display-message','-p','-t','='+self.name+':','#{session_created}'])).strip()
        for i,worker in enumerate(('claude','claude-opus','claude-sonnet','codex-a','codex-b')[:panes]):
            self.assert_ok(self.run_cmd(['tmux','set-option','-p','-t',self.name+':0.'+str(i),'@forge-worker',worker]))

    def route(self, *, sid=None, inc=None, root=None):
        return ['--target-session',self.name,'--cross-session','--target-root',str(root or self.project),
                '--expected-session-id',sid or self.sid,'--expected-incarnation',inc or self.inc]

    def cli(self, *args, **kwargs):
        return self.run_cmd([self.bridge,*args],**kwargs)

    def pending(self, slug='s1', status='BLOCKED', origin='worker'):
        import yaml
        self.pts='2026-09-14T00:00:00Z'
        directory=self.project/'.dev/proposals'/slug; directory.mkdir(exist_ok=True)
        log=directory/'forge-log.yml'
        log.write_text(f'pipeline: {slug}\nentries:\n  - timestamp: "{self.pts}"\n'
                       f'    stage: coding\n    to: codex-a\n    session: {self.name}\n'
                       f'    incarnation: {self.inc}\n    response: null\n')
        callback=self.project/'.dev/forge-tmp/callbacks'/f'{slug}-coding.{self.name}.{self.inc}.callback'
        callback.write_text(yaml.safe_dump({'slug':slug,'stage':'coding','worker':'codex-a',
            'status':status,'session':self.name,'incarnation':self.inc,'timestamp':self.pts,
            'selected_pending_timestamp':self.pts,'callback_id':'cb-'+slug,
            'origin':origin,'message':'blocked'}))
        return log,callback

    def selection(self, callback):
        import hashlib,yaml
        rec=yaml.safe_load(callback.read_text())
        return ['--expected-pending-ts',self.pts,'--expected-callback-id',rec['callback_id'],
                '--expected-callback-sha256',hashlib.sha256(callback.read_bytes()).hexdigest()]

    def park(self, slug='s1', resolve=False, extra=(), **kwargs):
        return self.cli(*self.route(),'park',*(['--resolve'] if resolve else []),'--',
                        '--slug',slug,'--stage','coding',
                        '--note' if resolve else '--reason','web-test',*extra,**kwargs)

    def event_functions(self):
        names=['_valid_session_name','ownership_root','_same_root_sessions',
               'session_incarnation_of','_target_ident_of','_bounded_line','_tmux_probe',
               '_forge_identity','_evp_snapshot','_event_provenance','timestamp',
               '_emit_event','_event_has_completion','_append_complete_event']
        return '\n'.join(web_function(BRIDGE,n) for n in names)+ '\n_EVP_KEY=""; _EVP_VALUE=""; _EVP_OUT=""\n'


class WebNativeBridgeTests(WebNativeFixture):
    def test_T7b_pinned_object_survives_name_reuse(self):
        self.native()
        functions='\n'.join(web_function(BRIDGE,n) for n in
                            ('_bounded_line','_tmux_probe','ownership_root','_pin_pane_object','_worker_send_locked'))
        # Real send primitive, real tmux pane; only unrelated hygiene hooks are inert.
        setup=functions+'''\n_cancel_copy_mode(){ tmux send-keys -X -t "$1" cancel 2>/dev/null || :; }
_hygiene_crash_at(){ :; }
_FORGE_EXPECT_SID="$1"; _FORGE_EXPECT_INC="$2"; _FORGE_VALID_ROOT="$3"
_worker_send_locked "$4:0.0" 'literal web text'
'''
        wrapper=self.bin/'tmux'; original=wrapper.read_text()
        real=shutil.which('tmux')
        self.script('tmux', '''import json,os,subprocess,sys
from pathlib import Path
args=sys.argv[1:]; real='''+repr(real)+'''; socket='''+repr(self.socket)+'''
with open(os.environ['WEB_CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
# Kill after successful text delivery, immediately before the distinct Enter call.
if args[0]=='send-keys' and args[-1]=='Enter' and Path(os.environ['WEB_ARM']).exists():
    Path(os.environ['WEB_ARM']).unlink()
    subprocess.run([real,'-S',socket,'kill-session','-t','=forge-web-test'],check=True)
    subprocess.run([real,'-S',socket,'new-session','-d','-s','forge-web-test',
                    '-c',os.environ['WEB_ROOT'],'/bin/cat'],check=True)
    Path(os.environ['WEB_FIRED']).touch()
os.execv(real,[real,'-S',socket]+args)
''')
        env={'WEB_ARM':str(self.base/'arm'),'WEB_FIRED':str(self.base/'fired'),'WEB_ROOT':str(self.project)}
        # Keep the private server alive when the target session is killed.
        self.assert_ok(self.run_cmd(['tmux','new-session','-d','-s','keeper','/bin/cat'],env=env))
        self.assert_ok(self.bash(setup,self.sid,self.inc,self.project,self.name,env=env))
        mutations=[c for c in self.recorded_calls() if c[0] in ('select-pane','send-keys')]
        targets={c[c.index('-t')+1] for c in mutations}
        self.assertEqual(len(targets),1); oldpane=targets.pop(); self.assertRegex(oldpane,r'^%[0-9]+$')
        self.calls.unlink(); (self.base/'arm').touch()
        rc,out=self.bash(setup,self.sid,self.inc,self.project,self.name,env=env)
        self.assertNotEqual(rc,0,out); self.assertTrue((self.base/'fired').exists())
        self.assertIn('SEND_FAILED enter',out)
        mutations=[c for c in self.recorded_calls() if c[0] in ('select-pane','send-keys')]
        self.assertTrue(all(c[c.index('-t')+1]==oldpane for c in mutations))
        # Expectations absent: the legacy primitive retains the original target spelling.
        wrapper.write_text(original); wrapper.chmod(0o755)
        code=functions+'\n_FORGE_EXPECT_SID=""; _pin_pane_object "$1"\n'
        self.assertEqual(self.assert_ok(self.bash(code,self.name+':0.0')),self.name+':0.0')


    def test_T11_wedged_host_and_discovery_are_bounded(self):
        functions=self.event_functions()
        # Drive the actual resolver down each path; only the external tmux is wedged.
        self.script('tmux','import time\ntime.sleep(8)\n')
        minimal=self.base/'no-perl';minimal.mkdir()
        for tool in ('git','grep','cut','head','awk','mktemp','sleep','rm'):
            path=shutil.which(tool);self.assertIsNotNone(path)
            (minimal/tool).symlink_to(path)
        (minimal/'tmux').symlink_to(self.bin/'tmux')
        for extra in ({},{'TMUX':'private,0,0','TMUX_PANE':'%0'}, {'PATH':str(minimal)}):
            env=dict(extra,FORGE_TMUX_LIST='',FORGE_TMUX_PROBE_TIMEOUT_S='1')
            code=functions+'\n_event_provenance "$PWD"; printf "DONE:[%s]" "$_EVP_OUT"\n'
            start=time.monotonic();out=self.assert_ok(self.bash(code,env=env,timeout=5))
            self.assertEqual(out,'DONE:[]');self.assertLess(time.monotonic()-start,4)


    def test_T11_public_emit_captured_pipe_deadline(self):
        self.script('tmux','import time\ntime.sleep(8)\n')
        for extra in ({},{'TMUX':'private,0,0','TMUX_PANE':'%0'}):
            env=dict(extra,FORGE_TMUX_LIST='',FORGE_TMUX_PROBE_TIMEOUT_S='1')
            start=time.monotonic()
            self.assert_ok(self.cli('emit','STAGE','--slug','bounded','--stage','coding',env=env,timeout=5))
            self.assertLess(time.monotonic()-start,4)
        lines=(self.project/'.dev/forge-tmp/orchestrator-events.log').read_text().splitlines()
        self.assertEqual(len(lines),2)
        self.assertTrue(all('event_at=' in line and 'event_session=' not in line for line in lines))


    def test_T11_nonexec_grandchild_is_killed(self):
        stub=self.bin/'tmux'
        stub.write_text('#!/bin/bash\nsleep 20 &\nchild=$!\nprintf "%s" "$child" > "$WEB_CHILD"\nwait "$child"\n')
        stub.chmod(0o755)
        pidfile=self.base/'grandchild';signals=self.base/'signals'
        functions=self.event_functions()
        code=functions+'''\nkill(){
    builtin kill "$@" 2>>"$WEB_SIGNALS"
    local rc=$?
    printf 'kill %s rc=%s\\n' "$*" "$rc" >> "$WEB_SIGNALS"
    return "$rc"
}
_event_provenance "$PWD"; printf 'DONE:[%s]' "$_EVP_OUT"
'''
        start=time.monotonic()
        out=self.assert_ok(self.bash(code,env={'FORGE_TMUX_LIST':'',
            'WEB_CHILD':str(pidfile),'WEB_SIGNALS':str(signals)},timeout=5))
        self.assertLess(time.monotonic()-start,4)
        self.assertEqual(out,'DONE:[]');self.assertTrue(pidfile.exists())
        pid=int(pidfile.read_text())
        deadline=time.monotonic()+2
        alive=True
        while time.monotonic()<deadline:
            try:os.kill(pid,0)
            except ProcessLookupError:alive=False;break
            time.sleep(.02)
        self.assertFalse(alive,signals.read_text())
        # A redundant cleanup after the successful kill can race a vanished group.
        # The child-gone assertion above and successful group signal prove cleanup.
        self.assertRegex(signals.read_text(),r'kill -KILL -- -[0-9]+ rc=0')


    def test_T11_signal_refusal_still_bounds_captured_caller(self):
        pidfile=self.base/'signal-probe'
        # A fake EPERM applies to both signal forms, but not the liveness query.
        # The read-only probe must return unavailable without awaiting that child.
        code=web_function(BRIDGE,'_bounded_line')+'''\nkill(){
    [ "$1" = -0 ] && { builtin kill "$@"; return $?; }
    echo 'fixture: kill: Operation not permitted' >> "$WEB_SIGNALS"
    return 1
}
probe(){ echo "$BASHPID" >/dev/null; sleep 8; }
probe(){ /bin/bash -c 'echo $$ > "$WEB_CHILD"; exec sleep 8'; }
value="$(_bounded_line probe)"; printf 'DONE:[%s]' "$value"
'''
        signals=self.base/'refused-signals'
        try:
            start=time.monotonic()
            out=self.assert_ok(self.bash(code,env={'WEB_CHILD':str(pidfile),
                'WEB_SIGNALS':str(signals)},timeout=5))
            self.assertEqual(out,'DONE:[]');self.assertLess(time.monotonic()-start,4)
            self.assertIn('Operation not permitted',signals.read_text())
        finally:
            if pidfile.exists():
                import signal
                pid=int(pidfile.read_text())
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass


    def test_T11_exited_leader_descendant_cleanup_and_output_cap(self):
        child=self.base/'background-child'
        code=web_function(BRIDGE,'_bounded_line')+'''
probe(){ /bin/bash -c 'sleep 20 & echo $! > "$WEB_CHILD"; printf ready'; }
value="$(_bounded_line probe)"; printf 'VALUE:[%s]' "$value"
'''
        try:
            out=self.assert_ok(self.bash(code,env={'WEB_CHILD':str(child)},timeout=5))
            self.assertEqual(out,'VALUE:[ready]');self.assertTrue(child.exists())
            pid=int(child.read_text());deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                try:os.kill(pid,0)
                except ProcessLookupError:break
                time.sleep(.02)
            else:self.fail('forked descendant survived successful leader exit')
        finally:
            if child.exists():
                import signal
                try:os.kill(int(child.read_text()),signal.SIGKILL)
                except ProcessLookupError:pass
        marker=self.base/'large-output-started'
        loud=self.script('large-output', 'from pathlib import Path\nimport sys\nPath('+repr(str(marker))+').touch()\nsys.stdout.write("x"*1000000)\n')
        code=web_function(BRIDGE,'_bounded_line')+'\nvalue="$(_bounded_line '+shlex.quote(str(loud))+')"; [ -z "$value" ]\n'
        self.assert_ok(self.bash(code,timeout=5));self.assertTrue(marker.exists())

    def test_T11_all_signals_denied_return_and_release_real_locks(self):
        # Real locks on every operation descriptor. Deny liveness as well as both
        # cleanup signals; independent post-return acquisition is the invariant.
        import signal
        for mode in ('slow-probe','fast-probe-slow-watchdog'):
            with self.subTest(mode=mode):
                locks=self.base/mode;locks.mkdir();pidfile=locks/'pid'
                code=web_function(BRIDGE,'_bounded_line')+"\nkill(){ return 1; }\n"
                code+='\n'.join('exec '+str(fd)+'>"$WEB_LOCKS/'+str(fd)+'"' for fd in range(9,14))+'\n'
                code+=shlex.quote(sys.executable)+" -c 'import fcntl; [fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB) for fd in range(9,14)]' || exit 90\n"
                code+=("probe(){ /bin/bash -c 'echo $$ > \"$WEB_CHILD\"; exec sleep 8'; }\n" if mode=='slow-probe'
                       else "probe(){ printf ready; }\n")
                code+='value="$(_bounded_line probe)"\nprintf "RETURNED:[%s]\\n" "$value"\nexec 9>&- 10>&- 11>&- 12>&- 13>&-\n'
                check="import fcntl,sys; from pathlib import Path; handles=[open(Path(sys.argv[1])/str(fd),'a') for fd in range(9,14)]; [fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB) for f in handles]; print('ALL_LOCKS_REACQUIRED')"
                code+=shlex.quote(sys.executable)+' -c '+shlex.quote(check)+' "$WEB_LOCKS"\n'
                try:
                    start=time.monotonic();out=self.assert_ok(self.bash(code,env={'WEB_LOCKS':str(locks),'WEB_CHILD':str(pidfile)},timeout=4))
                    self.assertLess(time.monotonic()-start,2.5);self.assertIn('ALL_LOCKS_REACQUIRED',out)
                    self.assertIn('RETURNED:[]' if mode=='slow-probe' else 'RETURNED:[ready]',out)
                finally:
                    if pidfile.exists():
                        try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
                        except ProcessLookupError:pass

    def test_T11_bound_passes_arguments_and_first_line(self):
        code=web_function(BRIDGE,'_bounded_line')+'''\nprobe(){ printf '%s\\nsecond line\\n' "$1"; }
value="$(_bounded_line probe '--target-session literal')"
[ "$value" = '--target-session literal' ] || exit 1
probe(){ printf no-newline; }
[ "$(_bounded_line probe)" = no-newline ] || exit 2
probe(){ return 1; }
[ -z "$(_bounded_line probe)" ] || exit 3
'''
        self.assert_ok(self.bash(code))


    def test_T15_supersede_runs_the_other_delegated_consume(self):
        self.native(5);log,callback=self.pending()
        prompts=self.base/'prompts';prompts.mkdir()
        (prompts/'adhoc.txt').write_text('Fixture {{slug}} {{stage}} {{worker}}\n')
        # The deliberately refusing broker stops NEW delivery after supersede has
        # done its durable work. No external provider is started by this test.
        rc,out=self.cli('dispatch','--slug','s1','--stage','adhoc','--worker','codex-b',
            '--supersede',env={'FORGE_PROMPTS_DIR':str(prompts),'FORGE_BROKER_BIN':'/usr/bin/false'})
        self.assertNotEqual(rc,0,out)
        self.assertNotRegex(out,'unknown option|unexpected operand|requires a value')
        self.assertIn('FORGE_SUPERSEDED',log.read_text())
        self.assertFalse(callback.exists())
        self.assertTrue(list((callback.parent/'archive').glob('s1-coding.*.callback')))
        event=(self.project/'.dev/forge-tmp/orchestrator-events.log').read_text()
        self.assertIn('SUPERSEDE_AUDIT:',event)

    def test_T22_validated_root_binds_all_file_actions(self):
        self.native();other=self.base/'other';other.mkdir()
        subprocess.run(['git','init','-q',str(other)],check=True)
        (other/'.claude').mkdir();(other/'.claude/forge-project.yml').write_text('name: other\n')
        (other/'.dev/forge-tmp').mkdir(parents=True)
        for action in [('callback-consume','--slug','s1','--stage','coding','--status','BLOCKED'),
                       ('park','--','--slug','s1','--stage','coding','--reason','x'),
                       ('park','--resolve','--','--slug','s1','--stage','coding','--note','x')]:
            before={str(p.relative_to(other)):p.read_bytes() for p in (other/'.dev').rglob('*') if p.is_file()}
            rc,out=self.cli(*self.route(),*action,cwd=other)
            self.assertNotEqual(rc,0,out)
            self.assertRegex(out,'do not belong to the validated target root|must be run from the project root')
            self.assertNotIn('CALLBACK_CONSUME NOOP',out)
            after={str(p.relative_to(other)):p.read_bytes() for p in (other/'.dev').rglob('*') if p.is_file()}
            self.assertEqual(before,after)
        self.assertIn('CALLBACK_CONSUME NOOP',self.assert_ok(self.cli(*self.route(),'callback-consume',
                            '--slug','missing','--stage','coding','--status','BLOCKED')))
        rc,out=self.cli('callback-consume','--slug','missing','--stage','coding','--status','BLOCKED',cwd=other)
        self.assertNotIn('do not belong to the validated target root',out)
        self.assertEqual(len(re.findall(r'^\s*_require_root_cwd ',BRIDGE,re.M)),11)
        events=(self.project/'.dev/forge-tmp/orchestrator-events.log').read_text()
        self.assertIn('IDENTITY:',events);self.assertIn('CROSS:',events)
        self.assertIn('event_session='+self.name,events)


class WebNativeInfraTests(WebNativeFixture):
    def test_T10_explicit_target_lock_release_and_legacy(self):
        import yaml
        self.native(); log,cb=self.pending()
        # Acquire as the target through the unchanged owner seam; PARK must discover
        # that same owner through its validated process-local channel, without the seam.
        pid=self.assert_ok(self.run_cmd(['tmux','display-message','-p','-t','='+self.name+':','#{pid}'])).strip()
        acquired=self.cli('infra-lock','acquire','--slug','s1','--stage','coding',env={
            'FORGE_LOCK_SELF_SESSION':self.name,'FORGE_LOCK_SELF_SESSION_ID':self.sid,
            'FORGE_LOCK_SELF_SESSION_CREATED':self.inc,'FORGE_LOCK_SELF_TMUX_PID':pid})
        self.assertIn('ACQUIRED',self.assert_ok(acquired))
        holder=self.base/'locks/infra.holder'
        self.assertEqual(str(yaml.safe_load(holder.read_text())['session_id']),self.sid)
        # A second session is the adversarial default for every untargeted query.
        self.assert_ok(self.run_cmd(['tmux','new-session','-d','-s','other','-c',self.base,'/bin/cat']))
        real=shutil.which('tmux')
        self.script('tmux', '''import json,os,sys
args=sys.argv[1:]
with open(os.environ['WEB_CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
if args and args[0]=='display-message' and '-t' not in args:args[1:1]=['-t','=other:']
os.execv('''+repr(real)+''', ['''+repr(real)+''','-S','''+repr(self.socket)+''']+args)
''')
        out=self.assert_ok(self.park())
        self.assertIn('lock_release_state=RELEASED',out);self.assertFalse(holder.exists())
        self.assertNotRegex(out,'IDENTITY_FAILURE|RELEASE_NOOP_NOT_OWNER|PARK PARTIAL')
        self.assertTrue(any('-t' in c and '='+self.name+':' in c for c in self.recorded_calls()))
        self.assertIn('PARK RESOLVED',self.assert_ok(self.park(resolve=True)))
        # Validated target publication is also required without optional expectations.
        self.pending('s2')
        self.assert_ok(self.cli('infra-lock','acquire','--slug','s2','--stage','coding',env={
            'FORGE_LOCK_SELF_SESSION':self.name,'FORGE_LOCK_SELF_SESSION_ID':self.sid,
            'FORGE_LOCK_SELF_SESSION_CREATED':self.inc,'FORGE_LOCK_SELF_TMUX_PID':pid}))
        out=self.assert_ok(self.cli('--target-session',self.name,'--cross-session','--target-root',self.project,
            'park','--','--slug','s2','--stage','coding','--reason','x'))
        self.assertIn('lock_release_state=RELEASED',out);self.assertFalse(holder.exists())
        # The old no-target/no-tmux path is tested directly, with no live default session.
        code=web_function(BRIDGE,'_infra_lock_identity')+'\ntmux(){ return 1; }; _FORGE_VALID_SESSION=""; _infra_lock_identity\n'
        self.assertEqual(self.bash(code)[0],1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
