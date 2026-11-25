"""forge_web — the local Forge control center backend.

DELIBERATELY NON-TRIVIAL. `bin/forge-web` imports this package to prove the active
interpreter resolves it from THIS checkout; an empty __init__.py would make that probe
pass while FastAPI, Uvicorn or PyYAML were still missing (review R16). `self_check()`
imports every runtime dependency by name and returns a machine-readable line, WITHOUT
starting a server, binding a socket, reading .dev/ or spawning a subprocess.
"""

from __future__ import annotations

import os
import sys
from importlib import import_module

__version__ = "0.1.0"

#: Advertised by `forge web-contract-version` and `forge-bridge web-contract-version`.
CONTRACT_MARKER = "forge-web-contract/1"

#: Every runtime import the server performs, checked eagerly by self_check().
RUNTIME_REQUIREMENTS = ("fastapi", "uvicorn", "pydantic", "yaml")

MIN_PYTHON = (3, 11)


class SetupError(RuntimeError):
    """A precondition the operator must fix before the server can start."""


def package_root() -> str:
    """Realpath of this package's directory. The launcher compares it to
    <checkout>/web/backend/src/forge_web so a second editable install from another
    worktree cannot silently drive this UI."""
    return os.path.realpath(os.path.dirname(__file__))


def self_check() -> str:
    """Return one line: `forge_web/<version> python=<x.y.z> path=<dir> deps=ok`.

    Raises SetupError naming EVERY unmet requirement, not just the first — an operator
    setting this up is usually missing two or three things at once.
    """
    problems: list[str] = []
    if sys.version_info[:2] < MIN_PYTHON:
        problems.append(
            "python>=%d.%d required, running %s"
            % (MIN_PYTHON[0], MIN_PYTHON[1], ".".join(str(p) for p in sys.version_info[:3]))
        )
    missing = []
    for mod in RUNTIME_REQUIREMENTS:
        try:
            import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        problems.append(
            "missing runtime dependencies: %s (fix: python3 -m pip install -e 'web/backend[test]')"
            % ", ".join(missing)
        )
    if problems:
        raise SetupError("; ".join(problems))
    return "forge_web/%s python=%s path=%s deps=ok" % (
        __version__,
        ".".join(str(p) for p in sys.version_info[:3]),
        package_root(),
    )


__all__ = [
    "CONTRACT_MARKER",
    "MIN_PYTHON",
    "RUNTIME_REQUIREMENTS",
    "SetupError",
    "__version__",
    "package_root",
    "self_check",
]