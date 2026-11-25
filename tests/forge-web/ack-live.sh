#!/bin/bash
# Actual watcher bytes from disposable empty roots; no live acknowledgements.
set -euo pipefail
TEST_ROOT="${FORGE_WEB_TEST_ROOT:-$(cd "$(dirname "$0")/../.." && pwd -P)}"
python3 - "$TEST_ROOT" <<'PY'
import fcntl, json, os, pathlib, subprocess, sys, tempfile
root = pathlib.Path(sys.argv[1]).resolve()
# Test the exact checkout implementation without installing or changing the environment.
sys.path.insert(0, str(root / 'web/backend/src'))
from forge_web.ack import AckOutcome, classify_ack

with tempfile.TemporaryDirectory(prefix='forge-web-ack-') as work:
    base = pathlib.Path(work)
    for name in ('home', 'cache', 'config', 'tools', 'fixture/.claude', 'fixture/.dev/proposals'):
        (base / name).mkdir(parents=True, exist_ok=True)
    (base / 'fixture/.claude/forge-project.yml').write_text('forge: {}\n')
    (base / 'config/watch-roots').write_text(str(base / 'fixture') + '\n')
    # Shadow every external session probe; a missing stub must never reach real tmux.
    tmux = base / 'tools/tmux'
    tmux.write_text('#!/bin/sh\nprintf "no server running\\n" >&2\nexit 1\n')
    tmux.chmod(0o755)
    env = {'PATH': str(base / 'tools') + ':' + os.environ.get('PATH', '/usr/bin:/bin'),
           'HOME': str(base / 'home'), 'FORGE_WATCH_CACHE_DIR': str(base / 'cache'),
           'FORGE_WATCH_CONFIG_DIR': str(base / 'config'),
           'FORGE_BROKER_BIN': '/usr/bin/false', 'FORGE_TMUX_BIN': str(tmux)}
    def run(*args):
        return subprocess.run(['bash', str(root / 'bin/forge-watch'), *args], env=env,
                              cwd=base / 'fixture', capture_output=True, text=True, timeout=15)
    def classify(proc):
        return classify_ack(proc.returncode, proc.stdout, proc.stderr, completed=True).outcome
    free = run('ack', '__forge_web_empty_fixture__')
    assert free.returncode == 0 and classify(free) == AckOutcome.NO_MATCH, free
    lock = open(base / 'cache/state.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held = run('ack', '__forge_web_empty_fixture__')
        assert held.returncode == 0 and not held.stdout.strip(), held
        assert classify(held) == AckOutcome.NOT_APPLIED_BUSY, held
        board = run('status', '--board')
        assert board.returncode == 75, board
        assert json.loads(board.stdout)['unavailable_reason'] == 'state_lock_busy', board
        # rc75 belongs to the board contract; on the ack classifier it is UNKNOWN.
        assert classify(board) == AckOutcome.UNKNOWN, board
        assert classify_ack(0, '', '', completed=True).outcome == AckOutcome.UNKNOWN
        assert classify_ack(held.returncode, held.stdout, held.stderr,
                            completed=False).outcome == AckOutcome.UNKNOWN
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    recovered = run('ack', '__forge_web_empty_fixture__')
    assert classify(recovered) == AckOutcome.NO_MATCH, recovered
    print('forge-web ack: real free/held/board/recovery bytes classified; no skips')
PY