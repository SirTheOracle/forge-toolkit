"""Hermetic fixtures: a disposable checkout, stub binaries, a fake clock, recording CLIs.

THE STUBS ASSERT THE CHILD ENVIRONMENT THEMSELVES. Each one writes its argv AND the pinned
FORGE_* variables it received to a log, and exits 97 if `FORGE_WEB_CHILD` is missing. A
top-level argv assertion cannot prove that a NESTED binary was pinned (review R7); a stub that
refuses an unpinned environment can.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forge_web import config as config_module
from forge_web.config import BASE_PATH, Config

MARKER = "forge-web-contract/1"

_STUB = """#!/bin/bash
[ "${FORGE_WEB_CHILD:-}" = "1" ] || { echo "stub %(name)s: not a pinned web child" >&2; exit 97; }
{
  printf '%%s' "%(name)s"
  for a in "$@"; do printf '\\t%%s' "$a"; done
  printf '\\tENV'
  for v in FORGE_BIN FORGE_FORGE_BIN FORGE_BRIDGE_BIN FORGE_WATCH_BIN FORGE_START_BIN \\
           FORGE_BROKER_BIN FORGE_CODEX_DOCTOR_BIN FORGE_TMUX_BIN FORGE_IDENTITY_ENFORCE \\
           TMUX TMUX_PANE TMUX_SESSION FORGE_ROLE FORGE_SENDER FORGE_INITIATIVE; do
    eval "printf '\\t%%s=%%s' \\"$v\\" \\"\\${$v:-}\\""
  done
  printf '\\n'
} >> "$FORGE_STUB_LOG"
if [ "${1:-}" = "web-contract-version" ]; then printf '%(marker)s\\n'; exit 0; fi
if [ -n "${STUB_STDOUT_FILE:-}" ] && [ -f "$STUB_STDOUT_FILE" ]; then cat "$STUB_STDOUT_FILE"; fi
exit "${STUB_RC:-0}"
"""

_TMUX = """#!/bin/bash
[ -f "${FAKE_TMUX_TSV:-/nonexistent}" ] && cat "$FAKE_TMUX_TSV"
exit "${FAKE_TMUX_RC:-0}"
"""


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


class Checkout:
    def __init__(self, root: Path, tools: Path, env: dict[str, str]) -> None:
        self.root = root
        self.tools = tools
        self.env = env
        self.stub_log = Path(env["FORGE_STUB_LOG"])
        self.tmux_tsv = Path(env["FAKE_TMUX_TSV"])

    def cfg(self) -> Config:
        return config_module.load(self.env)

    def calls(self) -> list[list[str]]:
        if not self.stub_log.exists():
            return []
        return [line.split("\t") for line in self.stub_log.read_text().splitlines() if line]

    def set_sessions(self, rows: list[tuple[str, str, str, str]]) -> None:
        self.tmux_tsv.write_text(
            "".join("%s\t%s\t%s\t%s\n" % row for row in rows)
        )


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Checkout:
    root = tmp_path / "checkout"
    (root / "bin").mkdir(parents=True)
    (root / "web" / "backend").mkdir(parents=True)
    (root / "web" / "frontend" / "dist").mkdir(parents=True)

    # The imported forge_web must live under <checkout>/web/backend/src. A symlink to the REAL
    # package satisfies config.load()'s containment check without copying the source tree.
    real_src = Path(config_module.__file__).resolve().parent.parent
    (root / "web" / "backend" / "src").symlink_to(real_src, target_is_directory=True)

    for name in ("forge", "forge-bridge", "forge-watch", "forge-start", "forge-broker"):
        _write_exec(root / "bin" / name, _STUB % {"name": name, "marker": MARKER})

    tools = tmp_path / "tools"
    tools.mkdir()
    _write_exec(tools / "tmux", _TMUX)

    home = tmp_path / "home"
    (home / ".config" / "forge").mkdir(parents=True)
    env = {
        "FORGE_WEB_CHECKOUT": str(root),
        # The launcher's two exports. THE FIXTURE MUST MIRROR bin/forge-web's exec block:
        # config.load() keeps no fallback list, so a fixture that forgets these fails at
        # startup — which is the point, and is also why `PINNED` below is derived from this
        # string rather than restated.
        "FORGE_WEB_BIN_DIR": str(root / "bin"),
        "FORGE_WEB_PINNED_BINS": "forge forge-bridge forge-watch forge-start forge-broker",
        "FORGE_WEB_HOST": "127.0.0.1",
        "FORGE_WEB_PORT": "8765",
        "FORGE_WEB_FRONTEND_DIST": str(root / "web" / "frontend" / "dist"),
        "FORGE_WEB_CONTRACT": MARKER,
        "FORGE_WEB_MUTATIONS_ENABLED": "1",
        "FORGE_WEB_ALLOW_TEST_OVERRIDES": "1",
        "FORGE_STUB_LOG": str(tmp_path / "stub.log"),
        "FAKE_TMUX_TSV": str(tmp_path / "sessions.tsv"),
        "PATH": "%s:%s" % (tools, BASE_PATH),
        "HOME": str(home),
    }
    (tmp_path / "sessions.tsv").write_text("")
    # child_env() copies HOME and the permitted overrides from the PROCESS environment, so the
    # process must agree with the config env for the child to be reproducible.
    for key in ("HOME", "FORGE_STUB_LOG", "FAKE_TMUX_TSV", "FORGE_WEB_ALLOW_TEST_OVERRIDES"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.setenv("FORGE_WATCH_TRIGGER", "0")
    env["FORGE_WATCH_TRIGGER"] = "0"
    return Checkout(root, tools, env)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """A disposable project root with the `.dev/` layout the readers expect."""
    root = tmp_path / "project"
    for sub in ("attention/payloads", "attention/archive", "forge-tmp/callbacks", "proposals"):
        (root / ".dev" / sub).mkdir(parents=True, exist_ok=True)
    return root


def write_log(root: Path, slug: str, entries: list[dict]) -> Path:
    """Write a forge-log.yml the way bin/forge-bridge writes it (a YAML list under
    `entries:`), so the reader is exercised against the real shape."""
    import yaml

    path = root / ".dev" / "proposals" / slug / "forge-log.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"entries": entries}, sort_keys=False))
    return path


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


class FakeClock:
    """Monotonic, deterministic, and it ADVANCES ON SLEEP — so an async retry ladder can be
    asserted exactly without any real waiting."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class RecordingAckCli:
    """Queued (rc, stdout, stderr[, duration]) tuples; records the targets it was asked for."""

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.queued: list[tuple[int, str, str, float]] = []
        self.targets: list[str] = []
        self.clock = clock

    def queue(self, items, durations=None) -> None:
        for index, item in enumerate(items):
            duration = durations[index] if durations else 0.0
            self.queued.append((item[0], item[1], item[2], duration))

    @property
    def calls(self) -> int:
        return len(self.targets)

    async def run(self, target: str):
        self.targets.append(target)
        if not self.queued:
            return 0, "", "", True
        rc, out, err, duration = self.queued.pop(0)
        if self.clock is not None and duration:
            self.clock.advance(duration)
        return rc, out, err, True


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def recording_cli(fake_clock: FakeClock) -> RecordingAckCli:
    return RecordingAckCli(fake_clock)


class SeqRandom:
    """A Random stand-in that returns the LOW end of every declared window, so a jittered
    schedule is exactly predictable while the jitter code path still runs."""

    def uniform(self, low: float, _high: float) -> float:
        return low