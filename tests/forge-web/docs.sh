#!/bin/bash
# Read-only documentation acceptance; no installer or operator state access.
set -euo pipefail
TEST_ROOT="${FORGE_WEB_TEST_ROOT:-$(cd "$(dirname "$0")/../.." && pwd -P)}"
python3 - "$TEST_ROOT" <<'PY'
from pathlib import Path
import re, sys
root = Path(sys.argv[1])
readme = (root / 'web/README.md').read_text()
guide = (root / 'docs/forge-operator-guide.md').read_text()
for phrase in ('python3 -m pip install -e', 'npm --prefix web/frontend ci',
               'npm --prefix web/frontend run build', 'forge-web', 'forge-resume'):
    assert phrase in readme, f'README missing setup/resume instruction: {phrase}'
assert re.search(r'ack\s+scope|acknowledg', readme, re.I), 'README missing ack scope'
assert re.search(r'terminal.required|trust|permission', readme, re.I), 'README missing modal limit'
assert re.search(r'partial|cleanup', readme, re.I), 'README missing partial outcomes'
assert 'forge-web' in guide and 'forge-resume' in guide, 'operator guide missing web/resume'
assert not re.search(r'^\s*(?:source\s+|PYTHONPATH=)', readme, re.M), 'README bypasses direnv'
print('forge-web docs: setup, scope, partial outcomes and resume passed')
PY