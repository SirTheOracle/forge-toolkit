#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for suite in \
  tests/codex-policy-smoke/run.sh \
  tests/forge-broker/run.sh \
  tests/forge-start/run.sh \
  tests/forge-bridge/run.sh \
  tests/forge-cc/run.sh \
  tests/forge-cc/spawn.sh \
  tests/forge-watch/run.sh \
  tests/forge-infra-lock/run.sh \
  tests/forge-recover/run.sh; do
  echo "== $suite =="; bash "$ROOT/$suite"
done
