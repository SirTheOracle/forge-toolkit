"""Immutable process configuration.

Every path is resolved ONCE, at load(), from the environment `bin/forge-web` pinned.
Nothing in this module is reachable from a request body.

THE PINNED SET IS THE LAUNCHER'S, NOT THIS FILE'S. `bin/forge-web` exports:

    FORGE_WEB_BIN_DIR       absolute path to the checkout's bin/
    FORGE_WEB_PINNED_BINS   space-separated basenames of every pinned executable

and drives its own presence and contract loops from the second one. This module READS both
and validates them against the set it models. It does NOT keep a fallback list: a fallback is
how you get two lists that agree today and diverge later, which is the defect this arrangement
exists to prevent (the same class the pane alias map guards with
`forge-bridge alias-self-test --strict`). A missing, malformed or disagreeing export is a
HARD STARTUP FAILURE.

The disagreement is checked in BOTH directions, and the one that matters is the first:

  * the launcher advertises a binary this backend does not model  -> fatal, naming it and the
    two edits required (PIN_ENV_BY_NAME, and the child_env variable it implies). This is the
    direction that catches a FUTURE ADDITION to the shell half.
  * this backend models a binary the launcher does not advertise  -> fatal, naming it. This
    catches a launcher that was narrowed.

`tmux` is deliberately OUTSIDE the advertised set: it does not live in bin/, it is resolved
from PATH, and it is validated by _resolve_tmux. It is still fingerprinted.
`codex-doctor` is likewise NOT a separate entry: `bin/forge-start:39` gives it its own
VARIABLE (FORGE_CODEX_DOCTOR_BIN) but it points at the same FILE as FORGE_BIN, so it is a row
in PIN_ENV_BY_NAME, not a binary.

R7: the nested executable graph is SEVEN binaries behind EIGHT variables, read out of the
scripts themselves at f3f1a4a:

    bin/forge:45,46,51,52   FORGE_BRIDGE_BIN FORGE_WATCH_BIN FORGE_TMUX_BIN FORGE_START_BIN
    bin/forge-bridge:129    FORGE_BROKER_BIN  -> default is the BARE WORD `forge-broker`,
                            i.e. PATH. This is the one that silently picks a foreign broker.
    bin/forge-bridge:130    FORGE_FORGE_BIN
    bin/forge-bridge:2317   FORGE_WATCH_BIN
    bin/forge-watch:124     FORGE_BROKER_BIN  -> honours an INHERITED override
    bin/forge-start:37      FORGE_BRIDGE_BIN
    bin/forge-start:38      FORGE_BIN          (NOT FORGE_FORGE_BIN)
    bin/forge-start:39      FORGE_CODEX_DOCTOR_BIN  (separate variable; pinning FORGE_BIN
                            does NOT pin the doctor)
    bin/forge-start:200     FORGE_BROKER_BIN

PIN_ENV_BY_NAME below is that table, in code. The shell half's T25-2 asserts that every
FORGE_*_BIN read by any bin/ executable appears in the launcher's exec block; this table is
the second half of that lockstep, and `test_every_advertised_binary_has_at_least_one_pinned_env_var`
fails if a newly advertised binary arrives without one.

TMUX NORMALISATION, precisely. `bin/forge-bridge` and `bin/forge-watch` DO NOT read
FORGE_TMUX_BIN — grep finds zero occurrences in either file; they invoke bare `tmux`. So
FORGE_TMUX_BIN alone cannot make the seams agree. The mechanism is:

    1. resolve tmux once, via shutil.which() over the SERVER's PATH, then realpath it;
    2. use that exact path for the backend's own discovery probes;
    3. set FORGE_TMUX_BIN to it, which pins bin/forge (the only honouring caller);
    4. build the child PATH so `tmux` resolves to the SAME realpath there, and assert it.

Step (4) is the load-bearing one. If it fails, load() raises: a server that discovers
sessions on one tmux server and sends to another is worse than one that will not start.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import CONTRACT_MARKER

LOOPBACK = "127.0.0.1"
PROBE_TIMEOUT_S = 5.0
FINGERPRINT_READ_CHUNK = 1 << 20

#: The launcher's two exports. Named constants so the error messages and the tests cannot
#: drift from the lookups.
BIN_DIR_VAR = "FORGE_WEB_BIN_DIR"
PINNED_BINS_VAR = "FORGE_WEB_PINNED_BINS"

#: Basename grammar: no separators, no NUL, no dot-leading names, no absolute paths. The
#: launcher advertises BASENAMES under FORGE_WEB_BIN_DIR, never paths.
_BIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: THE TABLE. name -> every FORGE_*_BIN that must point at that file.
#: Adding a binary means adding a row here; child_env() iterates it and load() validates
#: against it, so there is exactly one place to edit and a missing edit is fatal.
PIN_ENV_BY_NAME: dict[str, tuple[str, ...]] = {
    "forge": ("FORGE_BIN", "FORGE_FORGE_BIN", "FORGE_CODEX_DOCTOR_BIN"),
    "forge-bridge": ("FORGE_BRIDGE_BIN",),
    "forge-watch": ("FORGE_WATCH_BIN",),
    "forge-start": ("FORGE_START_BIN",),
    "forge-broker": ("FORGE_BROKER_BIN",),
}
MODELLED_BINS = frozenset(PIN_ENV_BY_NAME)

#: Minimal PATH for every child. The checkout's bin/ and the resolved tmux's own directory are
#: prepended by child_path_for() so a bare `forge-broker` (bin/forge-bridge:129) and a bare
#: `tmux` (bridge and watcher) both resolve inside this checkout's world.
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

#: Copied into every child when present in the server environment. Nothing else is.
_PASSTHROUGH = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR")

#: R7 "decide and state which config/test overrides are permitted in web children".
#: Inherited ONLY when the SERVER's own environment sets FORGE_WEB_ALLOW_TEST_OVERRIDES=1.
#: A browser can never set this: it is read from os.environ at load(), once.
PERMITTED_TEST_OVERRIDES = (
    "FORGE_WATCH_TRIGGER",
    "FORGE_WATCH_CACHE_DIR",
    "FORGE_WATCH_CONFIG_DIR",
    "FORGE_WATCH_ROOTS_FILE",
    "FORGE_REGISTRY_FILE",
    "FORGE_TMUX_LIST",
    "FORGE_DISPATCH_DRY_RUN",
    "FORGE_SPAWN_DRY_RUN",
    "FORGE_DISPATCH_WAIT_TIMEOUT",
    "FORGE_STUB_LOG", "FAKE_TMUX_TSV", "FAKE_TMUX_RC", "STUB_STDOUT_FILE", "STUB_RC",
    "FORGE_FIXTURE_STATE",
)

#: Never inherited and never settable, in any mode. FORGE_IDENTITY_ENFORCE is PINNED to "1"
#: below rather than merely dropped: review R3 showed the new root/ID/incarnation comparisons
#: sit inside that legacy switch, so an inherited observe configuration would silently
#: disable a precondition this layer explicitly requested. Pinning is the "web layer refuses
#: to operate in that mode" half of R3's required correction.
FORBIDDEN_IN_CHILDREN = (
    "FORGE_IDENTITY_ENFORCE",
    "FORGE_CODEX_ROLLOUT",
    "FORGE_WORKER_HYGIENE_MODE",
    "FORGE_EVENT_PROVENANCE",
    "FORGE_INTERNAL_ASK_ORIGIN",
    "FORGE_CALLBACK_PRELOCK_READY",
    "FORGE_CALLBACK_PRELOCK_RELEASE",
)

#: Ambient routing/role identity. Absent by construction — child_env() builds a fresh dict
#: rather than copying os.environ, which is strictly stronger than `env -u`.
AMBIENT_ROUTING = (
    "TMUX",
    "TMUX_PANE",
    "TMUX_SESSION",
    "FORGE_ROLE",
    "FORGE_SENDER",
    "FORGE_INITIATIVE",
    "FORGE_ROOT",
)


class ConfigError(RuntimeError):
    """A fatal, import-time configuration violation. The process must not start."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(FINGERPRINT_READ_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def parse_pinned_bins(raw: str | None) -> tuple[str, ...]:
    """Parse FORGE_WEB_PINNED_BINS. Raises ConfigError on anything unexpected.

    Canonical spelling is space-separated basenames, because the launcher iterates it with an
    unquoted `for _t in $FORGE_WEB_PINNED_BINS`. Colon separation is accepted and normalised
    so the two halves cannot disagree on punctuation alone. NOTHING ELSE is accepted: a value
    containing a path separator, a NUL, a dot-leading name, or a duplicate is malformed, and a
    malformed value is a hard failure rather than a silent fallback to a hardcoded list.
    """
    if raw is None:
        raise ConfigError(
            "%s is not set. bin/forge-web must export the pinned executable list; this "
            "backend deliberately keeps no fallback copy of it." % PINNED_BINS_VAR
        )
    names = [part for part in re.split(r"[\s:]+", raw.strip()) if part]
    if not names:
        raise ConfigError("%s is empty; it must list at least %s"
                          % (PINNED_BINS_VAR, " ".join(sorted(MODELLED_BINS))))
    seen: set[str] = set()
    for name in names:
        if not _BIN_NAME_RE.match(name):
            raise ConfigError(
                "%s contains %r, which is not a bare executable basename. The launcher "
                "advertises names under %s, never paths." % (PINNED_BINS_VAR, name, BIN_DIR_VAR)
            )
        if name in seen:
            raise ConfigError("%s lists %r twice" % (PINNED_BINS_VAR, name))
        seen.add(name)
    return tuple(names)


@dataclass(frozen=True)
class Executables:
    """Every Forge binary any child of this server can reach, pinned to one checkout.

    `binaries` is the LAUNCHER'S advertised set, in its advertised order. The named fields are
    conveniences over it for the five roles the rest of the package refers to by name; they
    are populated by load() after validating that each one is advertised.
    """

    bin_dir: Path
    binaries: tuple[tuple[str, Path], ...]      # the launcher's list, order preserved
    tmux: Path                                  # realpath of the one tmux both seams must use

    forge: Path
    forge_bridge: Path
    forge_watch: Path
    forge_start: Path
    forge_broker: Path

    @property
    def codex_doctor(self) -> Path:
        """bin/forge-start:39 gives the doctor its OWN variable but it is the same FILE as
        FORGE_BIN. Kept as a property so nothing fingerprints one inode twice."""
        return self.forge

    @property
    def advertised(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.binaries)

    def as_map(self) -> dict[str, Path]:
        """Everything subject to fingerprinting: the full advertised set plus tmux."""
        out = {name: path for name, path in self.binaries}
        out["tmux"] = self.tmux
        return out

    def pinned_env(self) -> dict[str, str]:
        """The FORGE_*_BIN half of the child environment, derived from PIN_ENV_BY_NAME over
        the launcher's advertised set. ONE source; child_env() does not restate it."""
        env: dict[str, str] = {}
        for name, path in self.binaries:
            for var in PIN_ENV_BY_NAME[name]:
                env[var] = str(path)
        env["FORGE_TMUX_BIN"] = str(self.tmux)      # bin/forge:51, the only honouring caller
        return env

    def fingerprint(self) -> dict[str, tuple[int, int, int, int, str]]:
        """(st_dev, st_ino, st_size, st_mtime_ns, sha256) for the FULL ADVERTISED SET plus
        tmux — not a hardcoded seven.

        Recomputed before EVERY mutation. Any change forces a fresh contract probe. This is
        how a mid-session install.sh, a swapped sibling binary, a replaced ~/bin copy or a
        different tmux on PATH is caught. A binary that has vanished fingerprints as
        (-1, -1, -1, -1, "") so disappearance is a CHANGE, not a crash.
        """
        out: dict[str, tuple[int, int, int, int, str]] = {}
        for name, path in self.as_map().items():
            try:
                st = path.stat()
                out[name] = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, _sha256_file(path))
            except OSError:
                out[name] = (-1, -1, -1, -1, "")
        return out


@dataclass(frozen=True)
class ContractStatus:
    """Result of probing both CLIs for the capability marker."""

    ok: bool
    detail: str
    markers: tuple[tuple[str, str], ...]        # (executable name, observed first line)
    fingerprint: tuple[tuple[str, tuple[int, int, int, int, str]], ...]

    @property
    def fingerprint_map(self) -> dict[str, tuple[int, int, int, int, str]]:
        return dict(self.fingerprint)


@dataclass(frozen=True)
class Config:
    checkout: Path
    frontend_dist: Path
    host: str
    port: int
    exe: Executables
    board_cmd: tuple[str, ...]
    mutations_enabled_at_launch: bool
    allow_test_overrides: bool
    inherited_overrides: tuple[tuple[str, str], ...]


def _require_under(child: Path, parent: Path, what: str) -> Path:
    resolved = child.resolve()
    parent = parent.resolve()
    if resolved != parent and parent not in resolved.parents:
        raise ConfigError("%s resolves to %s, which is outside %s" % (what, resolved, parent))
    return resolved


def _require_exec(path: Path, what: str) -> Path:
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ConfigError("%s is not an executable file: %s" % (what, path))
    return path


def _resolve_bin_dir(env: dict[str, str], checkout: Path) -> Path:
    """Read FORGE_WEB_BIN_DIR and prove it is THIS checkout's bin/.

    Both halves compute the directory; this is where they are made to agree. A launcher
    pointing somewhere else is exactly the "stale ~/bin copy" hazard the contract marker
    exists for, arriving through a different door.
    """
    raw = env.get(BIN_DIR_VAR)
    if not raw:
        raise ConfigError(
            "%s is not set. bin/forge-web must export the directory it resolved its symlink "
            "chain to; this backend does not re-derive it." % BIN_DIR_VAR
        )
    declared = Path(raw).expanduser()
    if not declared.is_dir():
        raise ConfigError("%s is not a directory: %s" % (BIN_DIR_VAR, raw))
    expected = (checkout / "bin").resolve()
    if declared.resolve() != expected:
        raise ConfigError(
            "%s is %s but FORGE_WEB_CHECKOUT implies %s. The launcher and this package "
            "disagree about which checkout is running."
            % (BIN_DIR_VAR, declared.resolve(), expected)
        )
    return expected


def _build_executables(env: dict[str, str], checkout: Path) -> Executables:
    bin_dir = _resolve_bin_dir(env, checkout)
    advertised = parse_pinned_bins(env.get(PINNED_BINS_VAR))

    unmodelled = [name for name in advertised if name not in MODELLED_BINS]
    if unmodelled:
        raise ConfigError(
            "the launcher pins %s, which this backend does not model. Add a row to "
            "PIN_ENV_BY_NAME in config.py naming every FORGE_*_BIN that must point at it "
            "(check what reads it: `grep -n '_BIN' bin/*`), then rerun. Refusing to start "
            "rather than launch a child with an unpinned nested binary."
            % ", ".join(repr(n) for n in unmodelled)
        )
    missing = sorted(MODELLED_BINS - set(advertised))
    if missing:
        raise ConfigError(
            "the launcher does not advertise a binary this backend models: %s. Either "
            "bin/forge-web's FORGE_WEB_PINNED_BINS was narrowed, or PIN_ENV_BY_NAME is stale. "
            "bin/forge-bridge:129 defaults the broker to the bare word `forge-broker`, so "
            "forge-broker in particular is not optional." % ", ".join(missing)
        )

    binaries: list[tuple[str, Path]] = []
    for name in advertised:
        path = bin_dir / name
        _require_under(path, checkout, "pinned executable %r" % name)
        _require_exec(path, "pinned executable %r" % name)
        binaries.append((name, path))
    by_name = dict(binaries)

    tmux = _resolve_tmux(env, bin_dir)
    return Executables(
        bin_dir=bin_dir,
        binaries=tuple(binaries),
        tmux=tmux,
        forge=by_name["forge"],
        forge_bridge=by_name["forge-bridge"],
        forge_watch=by_name["forge-watch"],
        forge_start=by_name["forge-start"],
        forge_broker=by_name["forge-broker"],
    )


def load(env: dict[str, str] | None = None) -> Config:
    """Build the immutable configuration, or raise ConfigError.

    A violation here must abort startup. The process must never reach a state in which it
    is able to invoke an unrelated checkout.
    """
    env = dict(os.environ if env is None else env)

    checkout = Path(env.get("FORGE_WEB_CHECKOUT", "")).expanduser()
    if not checkout.is_dir():
        raise ConfigError("FORGE_WEB_CHECKOUT is not a directory: %r" % env.get("FORGE_WEB_CHECKOUT"))
    checkout = checkout.resolve()

    host = env.get("FORGE_WEB_HOST", LOOPBACK)
    if host != LOOPBACK:
        raise ConfigError("FORGE_WEB_HOST must be the literal %s (got %r)" % (LOOPBACK, host))

    try:
        port = int(env.get("FORGE_WEB_PORT", "8765"))
    except ValueError as exc:
        raise ConfigError("FORGE_WEB_PORT is not an integer: %r" % env.get("FORGE_WEB_PORT")) from exc
    if not 1 <= port <= 65535:
        raise ConfigError("FORGE_WEB_PORT out of range: %d" % port)

    marker = env.get("FORGE_WEB_CONTRACT", CONTRACT_MARKER)
    if marker != CONTRACT_MARKER:
        raise ConfigError(
            "launcher advertises %r but this package implements %r" % (marker, CONTRACT_MARKER)
        )

    exe = _build_executables(env, checkout)

    dist = Path(env.get("FORGE_WEB_FRONTEND_DIST", str(checkout / "web" / "frontend" / "dist")))
    dist = _require_under(dist, checkout, "FORGE_WEB_FRONTEND_DIST")

    # The imported package must belong to this checkout too — a second editable install
    # from another worktree would otherwise silently drive this UI.
    from . import package_root

    _require_under(
        Path(package_root()), checkout / "web" / "backend" / "src", "the imported forge_web package"
    )

    allow_overrides = env.get("FORGE_WEB_ALLOW_TEST_OVERRIDES") == "1"
    inherited: list[tuple[str, str]] = []
    if allow_overrides:
        inherited = [(k, env[k]) for k in PERMITTED_TEST_OVERRIDES if k in env]

    runtime = subprocess.run([str(Path(sys.executable).parent / "python3"), "-c",
        "import yaml; import sys; assert sys.version_info >= (3, 10)"],
        capture_output=True, timeout=PROBE_TIMEOUT_S)
    if runtime.returncode:
        raise ConfigError("Child Python must provide PyYAML; install web/backend[test] in the active environment")

    return Config(
        checkout=checkout,
        frontend_dist=dist,
        host=host,
        port=port,
        exe=exe,
        board_cmd=(str(exe.forge), "board", "--json"),
        mutations_enabled_at_launch=env.get("FORGE_WEB_MUTATIONS_ENABLED", "1") == "1",
        allow_test_overrides=allow_overrides,
        inherited_overrides=tuple(inherited),
    )


def _resolve_tmux(env: dict[str, str], bin_dir: Path) -> Path:
    """Resolve the single tmux binary both seams must use, and prove the child PATH agrees.

    FORGE_TMUX_BIN in the SERVER environment is honoured (it is how a test points at a stub);
    otherwise tmux is taken from the server's PATH. Either way the result is realpath'd and
    then re-resolved against the CHILD PATH — because bin/forge-bridge and bin/forge-watch
    invoke bare `tmux` and ignore FORGE_TMUX_BIN entirely.
    """
    declared = env.get("FORGE_TMUX_BIN")
    if declared:
        found = shutil.which(declared) or (declared if os.path.isabs(declared) else None)
    else:
        found = shutil.which("tmux", path=env.get("PATH", BASE_PATH))
    if not found:
        raise ConfigError(
            "tmux was not found (set FORGE_TMUX_BIN or put tmux on PATH); the control center "
            "cannot inventory sessions without it"
        )
    resolved = Path(os.path.realpath(found))
    _require_exec(resolved, "tmux")

    # The child PATH is bin/ : the resolved tmux's OWN directory : BASE_PATH. Including the
    # tmux directory is not a formality — a Homebrew tmux lives in /opt/homebrew/bin, which is
    # in nobody's minimal PATH, and without it every bridge/watcher `tmux` call in a child
    # would fail while discovery kept working. The assertion below still catches the case that
    # matters: a DIFFERENT tmux shadowing it from bin/.
    child_path = child_path_for(bin_dir, resolved)
    via_child = shutil.which("tmux", path=child_path)
    if via_child is None or Path(os.path.realpath(via_child)) != resolved:
        raise ConfigError(
            "tmux seam disagreement: discovery would use %s but a child resolving bare `tmux` "
            "over PATH=%s would use %s. bin/forge-bridge and bin/forge-watch ignore "
            "FORGE_TMUX_BIN, so these must be the same binary." % (resolved, child_path, via_child)
        )
    return resolved


def child_path_for(bin_dir: Path, tmux: Path) -> str:
    """The one PATH every child gets. Defined once so `_resolve_tmux`'s agreement check and
    `child_env`'s actual value cannot drift apart."""
    return os.pathsep.join(dict.fromkeys([str(bin_dir), str(Path(sys.executable).parent),
        str(tmux.parent), *os.environ.get("PATH", BASE_PATH).split(os.pathsep), BASE_PATH]))


def child_env(cfg: Config) -> dict[str, str]:
    """The COMPLETE environment for every spawned CLI.

    Built from scratch, never copied from os.environ. Ambient routing (TMUX, TMUX_PANE,
    TMUX_SESSION, FORGE_ROLE, FORGE_SENDER, FORGE_INITIATIVE, FORGE_ROOT) is therefore
    absent by construction rather than removed — a stronger guarantee than `env -u`, and it
    cannot be defeated by a variable nobody thought to list. Browser input can never add,
    remove or override a key here: this function takes no request-derived argument.

    UNCHANGED FROM REVISION 0 IN EVERY OBSERVABLE WAY. The FORGE_*_BIN keys now come from
    Executables.pinned_env() — one table, derived from the launcher's advertised list —
    instead of being restated as literals here. The emitted values are byte-identical.
    """
    env: dict[str, str] = {
        "PATH": child_path_for(cfg.exe.bin_dir, cfg.exe.tmux),
        # R3 web half: an explicitly requested precondition must not be disabled by an
        # inherited observe configuration.
        "FORGE_IDENTITY_ENFORCE": "1",
        # Marks the child for anything that wants to know, and for the tests.
        "FORGE_WEB_CHILD": "1",
    }
    env.update(cfg.exe.pinned_env())
    # Preserve operator toolchain, credentials and billing configuration. Remove routing
    # and explicit test seams below; Forge binaries remain pinned after inheritance.
    inherited = dict(os.environ)
    for key in (*AMBIENT_ROUTING, *FORBIDDEN_IN_CHILDREN, *PERMITTED_TEST_OVERRIDES):
        inherited.pop(key, None)
    inherited.update(env)
    env = inherited
    for key in _PASSTHROUGH:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    for key, value in cfg.inherited_overrides:
        if key in FORBIDDEN_IN_CHILDREN:      # belt and braces; the tuples are disjoint
            continue
        env[key] = value
    for key in FORBIDDEN_IN_CHILDREN:
        if key != "FORGE_IDENTITY_ENFORCE":
            env.pop(key, None)
    env["FORGE_IDENTITY_ENFORCE"] = "1"
    return env


def probe_contract(cfg: Config, timeout_s: float = PROBE_TIMEOUT_S) -> ContractStatus:
    """Probe BOTH CLIs for the exact marker. argv arrays, no shell.

    .ok is True ONLY when `forge` AND `forge-bridge` each print exactly CONTRACT_MARKER on
    their first stdout line with rc 0. Anything else -> ok False, and the action layer
    refuses EVERY mutation before any subprocess runs. There is no fallback to unguarded
    commands: an old forge-bridge falls through to `help|*` and exits 0 with help on stdout,
    and a partially upgraded one can accept cross-session park while ignoring expectation
    operands. Only a byte-exact marker proves the contract.

    Only these two CLIs implement `web-contract-version` (Steps 1 and 22); the rest of the
    advertised set is covered by the FINGERPRINT, which is why recheck_contract watches all
    of it rather than only these two.
    """
    env = child_env(cfg)
    observed: list[tuple[str, str]] = []
    problems: list[str] = []
    for name, path in (("forge", cfg.exe.forge), ("forge-bridge", cfg.exe.forge_bridge)):
        first = ""
        try:
            proc = subprocess.run(
                [str(path), "web-contract-version"],
                cwd=str(cfg.checkout),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
            if proc.returncode != 0:
                problems.append("%s: rc %d" % (path, proc.returncode))
            elif first != CONTRACT_MARKER:
                problems.append("%s advertises %r (want %r)" % (path, first or "<nothing>", CONTRACT_MARKER))
        except subprocess.TimeoutExpired:
            problems.append("%s: contract probe timed out after %.1fs" % (path, timeout_s))
        except OSError as exc:
            problems.append("%s: %s" % (path, exc))
        observed.append((name, first))
    fp = tuple(sorted(cfg.exe.fingerprint().items()))
    return ContractStatus(
        ok=not problems,
        detail="; ".join(problems) if problems else "contract %s on both CLIs" % CONTRACT_MARKER,
        markers=tuple(observed),
        fingerprint=fp,
    )


def recheck_contract(cfg: Config, last: ContractStatus | None) -> ContractStatus:
    """Cheap recheck before a mutation.

    Skips the subprocess only when EVERY fingerprint tuple in the advertised set (plus tmux)
    is unchanged; otherwise re-probes. A changed sibling binary, a re-run install.sh, a
    replaced ~/bin copy or a different tmux all force the full probe.
    """
    if last is None:
        return probe_contract(cfg)
    current = tuple(sorted(cfg.exe.fingerprint().items()))
    if current == last.fingerprint:
        return last
    return probe_contract(cfg)