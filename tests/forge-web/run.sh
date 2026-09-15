#!/bin/bash
# Launcher/installer contract tests. All writes and command targets are disposable.
set -euo pipefail
TEST_ROOT="${FORGE_WEB_TEST_ROOT:-$(cd "$(dirname "$0")/../.." && pwd -P)}"
python3 - "$TEST_ROOT" <<'PY'
import json, os, pathlib, re, shutil, subprocess, sys, tempfile
root = pathlib.Path(sys.argv[1]).resolve()
launcher = (root / 'bin/forge-web').read_text()
assert 'SECRETS = re.compile' not in launcher, 'launcher added a redactor'
subprocess.run(['bash', '-n', str(root / 'bin/forge-web')], check=True)
for variable in ('TMUX', 'TMUX_PANE', 'TMUX_SESSION', 'FORGE_ROLE', 'FORGE_SENDER',
                 'FORGE_INITIATIVE', 'FORGE_TMUX_LIST', 'FORGE_DISPATCH_DRY_RUN'):
    assert re.search(r'-u\s+' + variable + r'\b', launcher), f'ambient {variable} not cleared'
exported = set(re.findall(r'^\s+(FORGE_[A-Z0-9_]*BIN)=', launcher, re.M))
reads = set()
for name in ('forge', 'forge-bridge', 'forge-watch', 'forge-start', 'forge-broker'):
    reads.update(re.findall(r'^(FORGE_[A-Z0-9_]*BIN)="\$\{\1:-',
                           (root / 'bin' / name).read_text(), re.M))
assert reads <= exported, f'unpinned nested binaries: {sorted(reads - exported)}'

with tempfile.TemporaryDirectory(prefix='forge-web-launcher-') as work:
    base = pathlib.Path(work).resolve()
    checkout, home, tools = (base / n for n in ('checkout', 'home', 'tools'))
    checkout.mkdir(); home.mkdir(); tools.mkdir()
    # Copy installer inputs: install/uninstall/drift never use the real checkout or home.
    for name in ('bin', 'lib', 'skills', 'codex-skills', 'commands', 'agents', 'config', 'prompts', 'digests'):
        src = root / name
        if src.is_dir(): shutil.copytree(src, checkout / name, symlinks=False)
    shutil.copy2(root / 'install.sh', checkout / 'install.sh')
    shutil.copy2(root / '.gitignore', checkout / '.gitignore')
    (checkout / 'web/backend/src/forge_web').mkdir(parents=True)
    (checkout / 'web/frontend/dist').mkdir(parents=True)
    (checkout / 'web/frontend/dist/index.html').write_text('<!doctype html><title>fixture</title>')
    env = {'PATH': str(tools) + ':' + os.environ.get('PATH', '/usr/bin:/bin'),
           'HOME': str(home), 'FORGE_CONFIG_DIR': str(home / '.config/forge'),
           'LANG': 'en_US.UTF-8', 'FORGE_WATCH_TRIGGER': '0'}
    def run(argv, *, expected=0):
        p = subprocess.run(argv, env=env, cwd=checkout, capture_output=True, text=True, timeout=60)
        assert p.returncode == expected, (argv, p.returncode, p.stdout[-2000:], p.stderr[-2000:])
        return p
    drift = run(['bash', 'install.sh', '--check-drift'], expected=1)
    assert 'bin/forge-web' in drift.stdout and 'not installed' in drift.stdout
    run(['bash', 'install.sh'])
    installed = home / 'bin/forge-web'
    assert installed.is_symlink() and installed.resolve() == (checkout / 'bin/forge-web').resolve()
    drift = subprocess.run(['bash', 'install.sh', '--check-drift'], env=env, cwd=checkout,
                           capture_output=True, text=True, timeout=60)
    assert re.search(r'bin/forge-web.*linked', drift.stdout), drift.stdout
    run(['bash', 'install.sh', '--uninstall'])
    assert not installed.exists() and not installed.is_symlink()

    # A deterministic interpreter shim tests launcher routing, not runtime dependencies.
    # Actual Python/dependency/build readiness is covered by backend + served-browser tests.
    real_python = sys.executable
    capture = base / 'launch.json'
    shim = tools / 'python3'
    shim.write_text('#!' + real_python + '\n' + '''import json, os, sys
from pathlib import Path
if sys.argv[1:3] == ['-m', 'forge_web']:
    Path(os.environ['TEST_CAPTURE']).write_text(json.dumps(dict(os.environ)))
    sys.exit(0)
body = sys.stdin.read()
if 'importlib' in body:
    print('path=' + os.environ['TEST_PACKAGE'])
    print('missing=')
    sys.exit(0)
if 'socket' in body: sys.exit(1)
os.execv(os.environ['TEST_REAL_PYTHON'], [os.environ['TEST_REAL_PYTHON'], *sys.argv[1:]])
''')
    shim.chmod(0o755)
    tmux = tools / 'tmux'
    tmux.write_text('#!/bin/sh\nexit 1\n'); tmux.chmod(0o755)
    env.update(TEST_CAPTURE=str(capture), TEST_PACKAGE=str(checkout / 'web/backend/src/forge_web'),
               TEST_REAL_PYTHON=real_python)
    # Contract probes go only to these fixture binaries.
    real_forge = (checkout / 'bin/forge').read_bytes()
    for name in ('forge', 'forge-bridge', 'forge-watch', 'forge-start', 'forge-broker'):
        p = checkout / 'bin' / name
        p.write_text('#!/bin/sh\nprintf "forge-web-contract/1\\n"\n'); p.chmod(0o755)
    help_result = run(['bash', 'bin/forge-web', '--help'])
    assert '--check' in help_result.stdout and 'set -euo' not in help_result.stdout
    for args in (['--nope'], ['--port', '0'], ['--port']):
        run(['bash', 'bin/forge-web', *args], expected=1)
    ready = run(['bash', 'bin/forge-web', '--check', '--port', '18765'])
    assert 'checkout=' + str(checkout) in ready.stdout
    for name in ('forge', 'forge-bridge', 'forge-watch', 'forge-start', 'forge-broker'):
        assert f'pinned={name}={checkout}/bin/{name}' in ready.stdout
    (home / 'bin').mkdir(exist_ok=True)
    installed.symlink_to(checkout / 'bin/forge-web')
    env.update(TMUX='poison', TMUX_PANE='%999', TMUX_SESSION='wrong', FORGE_ROLE='wrong',
               FORGE_TMUX_LIST='/poison', FORGE_DISPATCH_DRY_RUN='1')
    run(['bash', str(installed), '--port', '18765'])
    launched = json.loads(capture.read_text())
    for name in ('TMUX', 'TMUX_PANE', 'TMUX_SESSION', 'FORGE_ROLE', 'FORGE_TMUX_LIST',
                 'FORGE_DISPATCH_DRY_RUN'):
        assert name not in launched, name
    assert launched['FORGE_WEB_HOST'] == '127.0.0.1'
    assert pathlib.Path(launched['FORGE_TMUX_BIN']).resolve() == tmux.resolve()
    for variable in reads - {'FORGE_TMUX_BIN'}:
        assert pathlib.Path(launched[variable]).parent == checkout / 'bin', variable

    # Real copied forge -> recording bridge, with a poisoned HOME/bin copy present.
    (checkout / 'bin/forge').write_bytes(real_forge)
    chain = base / 'chain.log'
    bridge = checkout / 'bin/forge-bridge'
    bridge.write_text('#!/bin/sh\nprintf "PINNED %s\\n" "$*" >> ' + repr(str(chain)) + '\n')
    bridge.chmod(0o755)
    poisoned = home / 'bin/forge-bridge'
    poisoned.write_text('#!/bin/sh\nexit 97\n'); poisoned.chmod(0o755)
    project = base / 'project'; (project / '.dev/attention').mkdir(parents=True)
    topology = base / 'tmux.tsv'; topology.write_text(f'forge-fixture\t{project}\t$1\t1700000000\n')
    chainenv = dict(launched)
    chainenv.update(PATH=str(home / 'bin') + ':' + os.environ.get('PATH', ''),
                    FORGE_TMUX_LIST=str(topology), FORGE_WATCH_TRIGGER='0')
    p = subprocess.run(['bash', str(checkout / 'bin/forge'), 'dispatch', '@forge-fixture',
                        '--allow-api-billing', '--', 'chain-probe'], env=chainenv, cwd=project,
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and 'send ' in chain.read_text(), (p.returncode, p.stderr)

    # Test ignore rules in an initialized disposable Git repository, including archive runs.
    run(['git', 'init', '-q'])
    for name in ('web/frontend/node_modules/', 'web/frontend/dist/', 'web/backend/.pytest_cache/'):
        run(['git', 'check-ignore', '-q', '--no-index', name])
    for name in ('web/frontend/package-lock.json', 'web/backend/pyproject.toml'):
        run(['git', 'check-ignore', '-q', '--no-index', name], expected=1)
print('forge-web launcher: isolated install/drift/uninstall, help, pinning, nested dispatch and ignores passed')
PY
