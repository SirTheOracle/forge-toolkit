"""Bounded tmux inventory, registry labelling and the spawn root union.

TMUX SESSION IDS ARE `$N`. `%N` is a PANE id (tests/forge-infra-lock/run.sh:762). Every
identity below carries the session id because `#{session_created}` has only
second resolution and cannot distinguish same-second name reuse on its own.

Registry membership NEVER establishes liveness, and an alias NEVER labels a physical root
unless the registry's stored path realpath-equals that root — one registry entry can name a
worktree while a sibling worktree of the same repo is the live one (plan-review S4).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, child_env

TMUX_FORMAT = "#{session_name}\t#{session_path}\t#{session_id}\t#{session_created}"
TMUX_TIMEOUT_S = 5.0
REGISTRY_MAX_BYTES = 1 << 20
ROOTS_MAX_BYTES = 1 << 20

#: Never a session card, in any circumstance (plan-review M6).
SENTINEL_SESSION = "__nosession__"


@dataclass(frozen=True)
class LiveSession:
    name: str
    root: str                 # realpath
    session_id: str           # "$N"
    created: str              # epoch seconds as written by tmux
    has_dev: bool

    @property
    def identity_version(self) -> str:
        return "%s|%s|%s" % (self.name, self.session_id, self.created)


@dataclass(frozen=True)
class RepoEntry:
    repo_id: str
    alias: str
    path: str                 # realpath of the registry's declared path
    path_exists: bool


@dataclass
class Inventory:
    sessions: list[LiveSession] = field(default_factory=list)
    #: "ok" | "unavailable". A tmux FAILURE is not an empty fleet: an empty list with
    #: state "ok" means "no sessions", state "unavailable" means "we do not know".
    state: str = "ok"
    detail: str = ""
    registry: dict[str, RepoEntry] = field(default_factory=dict)
    registry_state: str = "ok"
    registry_detail: str = ""
    watch_roots: list[str] = field(default_factory=list)


def registry_path(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    explicit = env.get("FORGE_REGISTRY_FILE")
    if explicit:
        return Path(explicit).expanduser()
    return Path(env.get("HOME", "~")).expanduser() / ".config" / "forge" / "registry.yml"


def watch_roots_path(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    explicit = env.get("FORGE_WATCH_ROOTS_FILE")
    if explicit:
        return Path(explicit).expanduser()
    config_dir = env.get("FORGE_WATCH_CONFIG_DIR")
    base = Path(config_dir).expanduser() if config_dir else (
        Path(env.get("HOME", "~")).expanduser() / ".config" / "forge"
    )
    return base / "watch-roots"


def _realpath(value: str) -> str:
    try:
        return os.path.realpath(value)
    except OSError:
        return value


def read_registry(path: Path) -> tuple[dict[str, RepoEntry], str, str]:
    """(entries_by_realpath, state, detail). A corrupt registry DEGRADES: labels fall back to
    basenames and the fleet still renders. It never throws."""
    if not path.exists():
        return {}, "absent", "no registry at %s" % path
    try:
        if path.stat().st_size > REGISTRY_MAX_BYTES:
            return {}, "oversized", "registry above %d bytes; not parsed" % REGISTRY_MAX_BYTES
        import yaml

        with open(path) as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:                      # noqa: BLE001 - degrade, never throw
        return {}, "malformed", "registry unreadable: %s" % exc
    if not isinstance(data, dict) or not isinstance(data.get("repos"), dict):
        return {}, "malformed", "registry is not a cc-registry/1 map"
    out: dict[str, RepoEntry] = {}
    for repo_id, value in data["repos"].items():
        if not isinstance(value, dict):
            continue
        declared = str(value.get("path") or "")
        alias = str(value.get("alias") or "")
        if not declared or not alias:
            continue
        out[_realpath(declared)] = RepoEntry(
            repo_id=str(value.get("repo_id") or repo_id),
            alias=alias,
            path=_realpath(declared),
            path_exists=bool(value.get("path_exists", True)),
        )
    return out, "ok", ""


def read_watch_roots(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        if path.stat().st_size > ROOTS_MAX_BYTES:
            return []
        with open(path) as fh:
            raw = fh.read()
    except OSError:
        return []
    roots: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        roots.append(_realpath(line))
    return roots


def list_sessions(cfg: Config, *, timeout_s: float = TMUX_TIMEOUT_S) -> tuple[list[LiveSession], str, str]:
    """Run the one tmux probe. Returns (sessions, state, detail).

    `tmux list-sessions` exits 1 with "no server running" when nothing is up. That is an
    EMPTY FLEET, not a failure — it is the only nonzero rc treated as ok.
    """
    argv = [str(cfg.exe.tmux), "list-sessions", "-F", TMUX_FORMAT]
    try:
        proc = subprocess.run(
            argv, env=child_env(cfg), capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return [], "unavailable", "tmux list-sessions timed out after %.0fs" % timeout_s
    except OSError as exc:
        return [], "unavailable", "tmux could not be executed: %s" % exc
    if proc.returncode != 0:
        blob = (proc.stderr or "").lower()
        if "no server running" in blob or "no sessions" in blob:
            return [], "ok", ""
        return [], "unavailable", "tmux list-sessions rc %d: %s" % (
            proc.returncode, (proc.stderr or "").strip()[:200]
        )
    sessions: list[LiveSession] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        name, path, sid, created = (p.strip() for p in parts)
        if not name or not path or not sid.startswith("$"):
            continue
        if name == SENTINEL_SESSION:
            continue
        root = _realpath(path)
        sessions.append(
            LiveSession(
                name=name,
                root=root,
                session_id=sid,
                created=created,
                has_dev=os.path.isdir(os.path.join(root, ".dev")),
            )
        )
    return sessions, "ok", ""


def collect(cfg: Config, *, env: dict[str, str] | None = None) -> Inventory:
    sessions, state, detail = list_sessions(cfg)
    registry, reg_state, reg_detail = read_registry(registry_path(env))
    roots = read_watch_roots(watch_roots_path(env))
    return Inventory(
        sessions=sessions,
        state=state,
        detail=detail,
        registry=registry,
        registry_state=reg_state,
        registry_detail=reg_detail,
        watch_roots=roots,
    )


def label_for(root: str, registry: dict[str, RepoEntry]) -> tuple[str, str, str | None, str | None]:
    """(label, label_source, repo_alias, repo_id).

    The alias labels this PHYSICAL ROOT only on exact realpath equality. Otherwise the label
    is the root basename (bin/forge-watch:732-733), and the alias — if any repo in the
    registry claims a path at all — is returned separately as REPOSITORY METADATA, which the
    UI must render as such and never as this worktree's name.
    """
    real = _realpath(root)
    entry = registry.get(real)
    if entry is not None:
        return entry.alias, "registry_alias", entry.alias, entry.repo_id
    basename = os.path.basename(real.rstrip("/")) or real
    # Repository metadata: a sibling worktree of a registered repo. Purely informational.
    for candidate in registry.values():
        if real.startswith(os.path.join(candidate.path, "")) or candidate.path.startswith(
            os.path.join(real, "")
        ):
            return basename, "root_basename", candidate.alias, candidate.repo_id
    return basename, "root_basename", None, None


def spawn_roots(inv: Inventory) -> list[str]:
    """The deduplicated UNION of registered roots and live-discovered session roots.

    Deduplicated by PHYSICAL ROOT, so two sibling worktrees of one repo are two entries and
    a registered root that is also live is one. Registry membership establishes nothing about
    liveness; a live root absent from the registry is still offered.
    """
    seen: dict[str, None] = {}
    for entry in inv.registry.values():
        if entry.path_exists and os.path.isdir(entry.path):
            seen.setdefault(entry.path, None)
    for root in inv.watch_roots:
        if os.path.isdir(root):
            seen.setdefault(root, None)
    for session in inv.sessions:
        if os.path.isdir(session.root):
            seen.setdefault(session.root, None)
    return sorted(seen)


def unowned_roots(inv: Inventory, board_roots: list[str]) -> list[str]:
    """Roots the board has findings for with no live session at that exact physical path.
    These become project-scoped notices — never a guessed session action."""
    live = {s.root for s in inv.sessions}
    return sorted({_realpath(r) for r in board_roots} - live)