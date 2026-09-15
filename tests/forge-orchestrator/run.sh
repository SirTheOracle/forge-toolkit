#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 -m unittest discover -s "$HERE" -p 'test_*.py'
