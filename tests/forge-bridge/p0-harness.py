#!/usr/bin/env python3
"""Disposable bridge harness diagnostics; never imported by production code."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone


def utc():
    return datetime.now(timezone.utc).isoformat()


def redact(data):
    # Preserve all non-secret bytes, including NUL and undecodable output.
    for key, value in os.environ.items():
        if re.search(r'token|secret|password|credential|api.?key', key, re.I) and len(value) >= 4:
            data = data.replace(value.encode(), b'[REDACTED]')
    text = data.decode('latin1')
    text = re.sub(r'(?i)(\b(?:proxy-)?authorization["\x27]?\s*[:=]\s*["\x27]?)(?:Bearer|Basic)\s+[^\s"\x27]+',
                  r'\1[REDACTED]', text)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----',
                  '[REDACTED PRIVATE KEY]', text, flags=re.S)
    # Value terminator is real (end of line, or end of the enclosing quoted string),
    # never an ad-hoc punctuation class: a secret containing ',', ';' or '}' must not
    # leave a plaintext tail after [REDACTED] (F-5). A quoted value that fails the
    # close-quote lookahead (an embedded quote, F-2) falls through to the unquoted
    # alternative, which then consumes to end of line rather than re-truncating.
    text = re.sub(r'(?i)(\b[A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|authorization)[A-Za-z0-9_-]*\b["\x27]?\s*[=:]\s*)'
                  r'(?:"[^"]*"(?=\s|[,;}]|$)|\x27[^\x27]*\x27(?=\s|[,;}]|$)|[^\r\n]+)',
                  r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(--(?:api[_-]?key|token|secret|password)\s+)(?:"[^"]*"|\x27[^\x27]*\x27|\S+)', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)\b(Bearer|Basic)\s+[^\s"\x27]+', r'\1 [REDACTED]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|AIza[0-9A-Za-z_-]{20,})\b', '[REDACTED]', text)
    text = re.sub(r'(https?://)[^\s/@:]+:[^\s/@]+@', r'\1[REDACTED]@', text)
    return text.encode('latin1')


def write_json(path, value):
    def clean(item):
        if isinstance(item, str):
            return redact(item.encode()).decode('utf-8', 'replace')
        if isinstance(item, dict):
            return {key: clean(child) for key, child in item.items()}
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item
    path.write_text(json.dumps(clean(value), indent=2) + '\n')


def command(argv, timeout=2):
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, check=False)
        return {'argv': argv, 'rc': p.returncode, 'output': p.stdout.decode('utf-8', 'replace')}
    except subprocess.TimeoutExpired as exc:
        return {'argv': argv, 'rc': None, 'unavailable': 'command timed out',
                'output': (exc.stdout or b'').decode('utf-8', 'replace')}
    except OSError as exc:
        return {'argv': argv, 'rc': None, 'unavailable': str(exc), 'output': ''}


def real_tmux():
    return os.environ['P0_REAL_TMUX']


def identity(target):
    fields = '#{session_name}\t#{session_created}\t#{pane_id}\t#{pane_pid}\t#{pane_current_path}\t#{pane_current_command}'
    return command([real_tmux(), 'display-message', '-p', '-t', target, fields])


def poison_path(work, pane):
    return Path(work) / ('poison.' + hashlib.sha256(pane.encode()).hexdigest())


def permitted(work, target):
    ident = identity(target)
    fields = ident['output'].strip().split('\t')
    if ident['rc'] != 0 or len(fields) != 6 or not fields[2].startswith('%'):
        return False, ident, 'identity unavailable'
    if poison_path(work, fields[2]).exists():
        return False, ident, 'pane poisoned after missing marker'
    return True, ident, ''


def inventory(root):
    root = Path(root).resolve()
    top = command(['git', '-C', str(root), 'rev-parse', '--show-toplevel'])
    if top['rc'] == 0 and Path(top['output'].strip()).resolve() == root:
        proc = subprocess.run(['git', '-C', str(root), 'ls-files', '--stage', '-z', '--', 'bin/'],
                              stdout=subprocess.PIPE, check=True)
        paths = []
        for row in proc.stdout.split(b'\0'):
            if not row:
                continue
            meta, name = row.split(b'\t', 1)
            mode, _, stage = meta.split()
            if stage != b'0':
                raise ValueError('unmerged executable inventory')
            if mode == b'100755':
                paths.append(root / os.fsdecode(name))
    else:
        manifest = root / 'tests/forge-bridge/executable-inventory.txt'
        names = manifest.read_text().splitlines()
        if len(names) != len(set(names)) or any(not re.fullmatch(r'bin/[A-Za-z0-9_-]+', n) for n in names):
            raise ValueError('invalid executable archive manifest')
        expected = set(names)
        # Only declared backup suffixes are excluded. Every other executable is real.
        backup = re.compile(r'(?:\.bak(?:[._-].*)?|\.backup(?:[._-].*)?|\.orig|~)$')
        actual = {str(p.relative_to(root)) for p in (root / 'bin').rglob('*')
                  if p.is_file() and os.access(p, os.X_OK) and not backup.search(p.name)}
        if actual != expected:
            raise ValueError('archive executable inventory mismatch: missing=%s extra=%s' %
                             (sorted(expected-actual), sorted(actual-expected)))
        paths = [root / n for n in sorted(expected)]
    if not paths or any(p.is_symlink() or not p.is_file() or not os.access(p, os.X_OK) for p in paths):
        raise ValueError('empty, missing, nonexecutable or symlinked executable inventory')
    count = sum(p.read_bytes().count(b'SECRETS = re.compile') for p in paths)
    if count != 3:
        raise ValueError('redaction count=%d expected=3' % count)
    return count


def inspect_lock(path):
    """Diagnostic only: never block opening a malformed/replaced lock path."""
    row = {'path': str(path)}
    fd = None
    try:
        before = path.lstat()
        row.update(device=before.st_dev, inode=before.st_ino, mode=before.st_mode,
                   mtime_ns=before.st_mtime_ns)
        if not stat.S_ISREG(before.st_mode):
            row['probe'] = 'unavailable: non-regular lock path'
        elif not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_NONBLOCK'):
            row['probe'] = 'unavailable: safe no-follow/nonblocking open unsupported'
        else:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0))
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                row['probe'] = 'unavailable: opened lock descriptor is non-regular'
            elif (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                row['probe'] = 'unavailable: lock identity changed during open'
            else:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    row['probe'] = 'unlocked at capture'
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except BlockingIOError:
                    row['probe'] = 'held at capture; holder not inferred'
    except OSError as exc:
        row['probe'] = 'unavailable: ' + str(exc)
    finally:
        if fd is not None:
            os.close(fd)
    return row


def capture(work, evidence, target, name, started, ident, cmd):
    """One bounded D1 per actual pane, captured before suite cleanup."""
    fields = ident['output'].strip().split('\t')
    pane = fields[2] if len(fields) == 6 else target
    poisoned = poison_path(work, pane)
    # This is the admission fence, published BEFORE any slow diagnostic command.
    try:
        with poisoned.open('x') as handle:
            handle.write(name + '\n')
    except FileExistsError:
        return
    directory = Path(evidence) / ('D1-' + hashlib.sha256(pane.encode()).hexdigest()[:12])
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = Path(work) / ('out.' + name)
    raw = result.read_bytes() if result.exists() else b''
    (directory / 'result.redacted.bin').write_bytes(redact(raw))
    report = {'schema': 'forge-bridge-D1/1', 'case': name, 'started_at': started,
              'missing_marker_at': utc(), 'pane_identity': ident, 'command': cmd,
              'result_bytes_before_redaction': len(raw), 'result_sha256_before_redaction': hashlib.sha256(raw).hexdigest(),
              'historical_acm': 'INSUFFICIENT EVIDENCE'}
    report['pane_capture'] = command([real_tmux(), 'capture-pane', '-p', '-S', '-', '-t', pane])
    report['session_panes'] = command([real_tmux(), 'list-panes', '-s', '-t', pane, '-F',
                                     '#{session_name}\t#{session_created}\t#{pane_id}\t#{pane_pid}\t#{pane_current_path}\t#{pane_current_command}'])
    # ps without environment; retain only the pane shell, ancestors and descendants.
    ps = command(['ps', '-axo', 'pid=,ppid=,lstart=,stat=,command='])
    rows = {}
    for line in ps['output'].splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows[int(parts[0])] = (int(parts[1]), line)
    pid = int(fields[3]) if len(fields) == 6 and fields[3].isdigit() else -1
    keep = {pid}
    while True:
        added = {p for p, (pp, _) in rows.items() if pp in keep}
        if added <= keep:
            break
        keep |= added
    ancestor = pid
    while ancestor in rows and rows[ancestor][0] not in keep:
        ancestor = rows[ancestor][0]
        keep.add(ancestor)
    ps['output'] = '\n'.join(rows[p][1] for p in sorted(keep) if p in rows)
    if not ps['output']:
        ps['unavailable'] = 'pane process tree unavailable'
    report['process_tree'] = ps
    report['wait_channels'] = command(['ps', '-o', 'pid=,wchan=', '-p', ','.join(str(p) for p in sorted(keep) if p > 0)]) if pid > 0 else {'unavailable': 'no pane PID'}
    # Fixture scope only. Exact absolute paths + inode identity + immediate lock probe.
    roots = set()
    if len(fields) == 6:
        roots.add(fields[4])
    try:
        words = shlex.split(cmd)
        roots.update(words[i+1] for i, word in enumerate(words[:-1]) if word == 'cd')
    except ValueError:
        report['command_root_parse'] = 'unavailable: shell text could not be tokenized'
    fixture_work = Path(work).resolve()
    roots = {Path(p).resolve() for p in roots if p}
    roots = {p for p in roots if p == fixture_work or fixture_work in p.parents}
    locks = sorted({lock for root in roots for pattern in
                    ('.dev/forge-tmp/hygiene-locks/*.lock', '.dev/forge-usage.*.yml.lock')
                    for lock in root.glob(pattern)})
    report['expected_lock_paths'] = []
    if len(fields) == 6:
        session, incarnation, _, _, _, _ = fields
        for root in sorted(roots):
            report['expected_lock_paths'] += [str(root / '.dev' / ('forge-usage.' + session + '.yml.lock'))] + [
                str(root / '.dev/forge-tmp/hygiene-locks' / (session + '.' + incarnation + '.' + worker + '.lock'))
                for worker in ('claude-opus', 'claude-sonnet', 'codex-a', 'codex-b')]
    report['absent_expected_locks'] = [p for p in report['expected_lock_paths'] if not Path(p).exists()]
    report['locks'] = []
    report['locks_omitted'] = max(0, len(locks)-128)
    for path in locks[:128]:
        report['locks'].append(inspect_lock(path))
    report['lock_holders'] = command(['lsof', '-nP'] + [str(p) for p in locks[:128]]) if locks else {'unavailable': 'no matching fixture lock files'}
    report['kernel_locks'] = {'unavailable': '/proc/locks unavailable on this platform'}
    if Path('/proc/locks').exists():
        # lsof above relates file identity to processes; keep unrelated kernel rows private.
        report['kernel_locks'] = {'unavailable': 'not collected: lsof and exact file probes used'}
    report['captured_at'] = utc()
    write_json(directory / 'diagnostic.json', report)
    (directory / 'COMPLETE').write_text(utc() + '\n')


def run(work, evidence, target, name, cmd):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise ValueError('unsafe fixture name')
    work_path, evidence_path = Path(work).resolve(), Path(evidence).resolve()
    if evidence_path == work_path or work_path in evidence_path.parents:
        raise ValueError('evidence must survive WORK cleanup')
    out = Path(work) / ('out.' + name)
    allowed, ident, reason = permitted(work, target)
    if not allowed:
        out.write_text('NOT RUN: ' + reason + '\n')
        return 125
    out.write_bytes(b'')
    started = utc()
    seconds = float(os.environ.get('FORGE_BRIDGE_TEST_MARKER_TIMEOUT_S', '30'))
    if not 0 < seconds <= 30:
        raise ValueError('marker test timeout must be > 0 and <= 30 seconds')
    marker = 'P0_DONE_' + os.urandom(12).hex() + '_'
    if os.environ.get('FORGE_BRIDGE_TEST_SEED_FAIL') == name:
        cmd = 'false'
    prefix = 'export FORGE_BROKER_BIN=%s FORGE_BROKER_CAPTURE=%s; ' % tuple(
        shlex.quote(os.environ[k]) for k in ('FORGE_BROKER_BIN', 'FORGE_BROKER_CAPTURE'))
    payload = '{ ' + prefix + cmd + '; } > ' + shlex.quote(str(out)) + ' 2>&1; printf "\\n' + marker + '%s\\n" "$?" >> ' + shlex.quote(str(out))
    if os.environ.get('FORGE_BRIDGE_TEST_MISSING_MARKER') == name:
        # Real injection, real partial result, deliberately omit only the completion marker.
        payload = '{ ' + prefix + cmd + '; } > ' + shlex.quote(str(out)) + ' 2>&1'
    injected = command([real_tmux(), 'send-keys', '-t', ident['output'].strip().split('\t')[2], payload, 'Enter'])
    deadline = time.monotonic() + seconds
    while injected['rc'] == 0 and time.monotonic() < deadline:
        data = out.read_bytes()
        match = re.search(rb'^' + marker.encode() + rb'([0-9]+)$', data, re.M)
        if match:
            out.write_bytes(data[:match.start()] + b'DONE_' + match[1] + b'\n' + data[match.end():])
            return 0
        time.sleep(min(0.05, max(0, deadline-time.monotonic())))
    capture(work, evidence, target, name, started, ident, cmd)
    with out.open('ab') as handle:
        handle.write(b'\nTIMEOUT: missing completion marker; pane poisoned\n')
    return 124


def main():
    action, *args = sys.argv[1:]
    if action == 'inventory':
        print(inventory(*args))
    elif action == 'retain':
        source, destination = map(Path, args)
        destination.write_bytes(redact(source.read_bytes()))
    elif action == 'run':
        return run(*args)
    elif action == 'send':
        work, *tmux_args = args
        target = tmux_args[tmux_args.index('-t')+1] if '-t' in tmux_args else ''
        if not target or not permitted(work, target)[0]:
            print('NOT RUN: injection target unavailable or poisoned', file=sys.stderr)
            return 125
        os.execv(real_tmux(), [real_tmux()] + tmux_args)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print('P0 harness error: ' + str(exc), file=sys.stderr)
        sys.exit(2)
