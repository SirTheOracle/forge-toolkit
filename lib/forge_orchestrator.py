"""Protected orchestrator records, not a router or an effect/recovery engine.

Callers resolve root and live identity through the existing broker/bridge first.
Storage integrity does not establish operator authority or qualify a sandbox.
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid


class Refusal(RuntimeError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def require(condition, code, detail):
    if not condition:
        raise Refusal(code, detail)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def number(value):
    return type(value) is int and value >= 0


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) is not None


def sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def filename(name):
    require(isinstance(name, str) and name not in {"", ".", ".."}
            and "/" not in name and "\x00" not in name, "UNSAFE_PATH", str(name))


def directory(path, create=False):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "UNSAFE_PATH", str(path))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_at(fd, name, missing=False):
    filename(name)
    try:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        if missing:
            return None
        raise
    try:
        info = os.fstat(child)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 1024 * 1024,
                "UNSAFE_RECORD", name)
        with os.fdopen(child, "rb", closefd=False) as stream:
            data = stream.read(1024 * 1024 + 1)
        require(len(data) <= 1024 * 1024, "OVERSIZED_RECORD", name)
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, "INVALID_RECORD", "duplicate JSON field")
                result[key] = value
            return result
        def reject_constant(value):
            raise Refusal("INVALID_RECORD", "nonfinite JSON number")
        value = json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
        require(isinstance(value, dict), "INVALID_RECORD", name)
        return value
    finally:
        os.close(child)


def write_at(fd, name, value, exclusive=False):
    filename(name)
    data = encoded(value)
    require(len(data) <= 1024 * 1024, "OVERSIZED_RECORD", name)
    temporary = "." + name + "." + uuid.uuid4().hex
    try:
        child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            view = memoryview(data)
            while view:
                written = os.write(child, view)
                require(written > 0, "WRITE_FAILED", name)
                view = view[written:]
            os.fsync(child)
        finally:
            os.close(child)
        if exclusive:
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            os.unlink(temporary, dir_fd=fd)
        else:
            # Refuse an existing symlink/device/hardlink rather than silently
            # replacing evidence that another reader would refuse.
            read_at(fd, name, missing=True)
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def validate_root(value):
    require(isinstance(value, dict) and set(value) == {"physical_root", "git_dir", "git_common_dir", "root_id"},
            "INVALID_ROOT", "resolved broker root descriptor required")
    for field in ("physical_root", "git_dir", "git_common_dir"):
        require(isinstance(value[field], str) and Path(value[field]).is_absolute()
                and ".." not in Path(value[field]).parts, "INVALID_ROOT", field)
    expected = hashlib.sha256((value["physical_root"] + "\0" + value["git_dir"]).encode()).hexdigest()
    require(value["root_id"] == expected, "ROOT_MISMATCH", "broker root identity differs")


def validate_process(value):
    require(isinstance(value, dict), "INVALID_PROCESS", "process descriptor required")
    require(type(value.get("pid")) is int and value["pid"] > 0, "INVALID_PROCESS", "positive PID required")
    require(isinstance(value.get("started"), str) and bool(value["started"]), "INVALID_PROCESS", "start identity required")
    require(isinstance(value.get("executable"), str) and Path(value["executable"]).is_absolute(),
            "INVALID_PROCESS", "resolved executable required")
    require(sha256(value.get("sha256")), "INVALID_PROCESS", "executable hash required")
    require(isinstance(value.get("version"), str) and bool(value["version"]), "INVALID_PROCESS", "version required")


def validate_inputs(value):
    require(isinstance(value, dict) and set(value) == {"package", "core", "adapter", "helpers"},
            "INVALID_INPUTS", "package/core/adapter/helpers required")
    for name, item in value.items():
        require(isinstance(item, dict) and set(item) == {"path", "sha256"}
                and isinstance(item["path"], str) and Path(item["path"]).is_absolute()
                and ".." not in Path(item["path"]).parts and sha256(item["sha256"]), "INVALID_INPUTS", name)


def validate_evidence(value):
    require(isinstance(value, dict) and set(value) == {"reports", "loading", "native_policy", "workflows"},
            "INVALID_EVIDENCE", "admission evidence required")
    require(isinstance(value["reports"], list) and value["reports"]
            and all(sha256(report) for report in value["reports"]), "INVALID_EVIDENCE", "report identities required")
    require(isinstance(value["loading"], str) and value["loading"] in {"behavioral", "plaintext-attested"},
            "INVALID_EVIDENCE", "loading evidence required")
    require(value["native_policy"] == "pass", "INVALID_EVIDENCE", "native policy must pass")
    require(isinstance(value["workflows"], list) and value["workflows"]
            and all(isinstance(flow, str) and flow for flow in value["workflows"]),
            "INVALID_EVIDENCE", "supported workflow subset required")


def validate_seat(value, reserved=False):
    require(isinstance(value, dict) and value.get("role") == "orchestrator", "INVALID_SEAT", "orchestrator seat required")
    require(isinstance(value.get("provider"), str) and value["provider"] in {"claude", "codex"},
            "INVALID_PROVIDER", "claude|codex required")
    for field in (("session", "socket") if reserved else ("session", "incarnation", "session_id", "pane_id", "socket")):
        require(isinstance(value.get(field), str) and bool(value[field]), "INVALID_SEAT", field)
    if reserved:
        require(not any(value.get(field) for field in ("incarnation", "session_id", "pane_id")),
                "INVALID_SEAT", "a reservation cannot invent tmux objects")
        return
    require(re.fullmatch(r"\$[0-9]+", value["session_id"]) is not None, "INVALID_SEAT", "session object required")
    require(re.fullmatch(r"%[0-9]+", value["pane_id"]) is not None, "INVALID_SEAT", "pane object required")


def validate_launch(value, root):
    require(isinstance(value, dict), "INVALID_LAUNCH", "launch object required")
    require(value.get("schema") == "forge-orchestrator-launch/1", "INVALID_LAUNCH", "unknown schema")
    require(value.get("root") == root, "ROOT_MISMATCH", "launch belongs to another physical root")
    require(identifier(value.get("launch_id")) and number(value.get("revision")) and value["revision"] >= 1,
            "INVALID_LAUNCH", "launch id/revision required")
    require(isinstance(value.get("state"), str) and value["state"] in {"preparing", "ready", "failed"},
            "INVALID_LAUNCH", "invalid state")
    for name in ("created_at", "updated_at"):
        try:
            parsed = datetime.datetime.fromisoformat(value[name])
            require(parsed.tzinfo is not None, "INVALID_LAUNCH", "timezone required")
        except (KeyError, ValueError, TypeError) as exc:
            raise Refusal("INVALID_LAUNCH", "timestamps required") from exc
    reserved = value.get("construction") == "reserved"
    require(not reserved or value["state"] in {"preparing", "failed"}, "INVALID_LAUNCH", "unbound launch cannot be ready")
    validate_seat(value.get("seat"), reserved=reserved)
    validate_process(value.get("owner"))
    if "construction" in value:
        require(value["construction"] in {"reserved", "shell-created", "hook-started", "claimed"},
                "INVALID_LAUNCH", "unknown construction phase")
        if not reserved:
            handoff = value.get("handoff")
            require(isinstance(handoff, dict), "INVALID_LAUNCH", "handoff required")
            validate_process(handoff.get("shell"))
            require(isinstance(handoff.get("expected_hook"), list) and handoff["expected_hook"]
                    and isinstance(handoff.get("expected_populate"), str), "INVALID_LAUNCH", "handoff closure required")
            if value["construction"] == "claimed":
                validate_process(handoff.get("claimant"))
                require(isinstance(handoff.get("chain"), list), "INVALID_LAUNCH", "ancestry required")
            if "hook_process" in handoff:
                validate_process(handoff["hook_process"])
            require(value["construction"] != "hook-started" or "hook_process" in handoff,
                    "INVALID_LAUNCH", "hook process must precede hook execution")
    require(sha256(value.get("profile_hash")) and sha256(value.get("package_hash")),
            "INVALID_LAUNCH", "fixed protected input hashes required")
    validate_inputs(value.get("inputs"))
    require(value["inputs"]["package"]["sha256"] == value["package_hash"],
            "INVALID_INPUTS", "package identity differs")
    validate_evidence(value.get("evidence"))
    require(number(value.get("process_epoch")), "INVALID_PROCESS", "process epoch required")
    if value["state"] == "ready":
        require(value["process_epoch"] > 0, "INVALID_PROCESS", "observed process epoch required")
        validate_process(value.get("process"))
        require(isinstance(value.get("broker"), dict) and sha256(value["broker"].get("runtime_sha256")),
                "INVALID_LAUNCH", "broker startup runtime identity required")
        validate_process(value["broker"].get("process"))
        require(value["broker"].get("protocol") == "forge-broker-control/1",
                "INVALID_LAUNCH", "broker protocol required")
    if value["state"] == "failed":
        require(isinstance(value.get("diagnostic"), str) and bool(value["diagnostic"]),
                "INVALID_LAUNCH", "failure diagnostic required")


def compare_live(record, root, seat, process, broker, profile_hash, package_hash):
    validate_launch(record, root)
    require(record["state"] == "ready", "LAUNCH_NOT_READY", record["state"])
    expected = {"root": root, "seat": seat, "process": process, "broker": broker,
                "profile_hash": profile_hash, "package_hash": package_hash}
    mismatches = [field for field, value in expected.items() if record.get(field) != value]
    require(not mismatches, "LAUNCH_IDENTITY_MISMATCH", ",".join(mismatches))
    return record


class Store:
    def __init__(self, protected, root):
        validate_root(root)
        expected = Path(root["git_common_dir"]) / "forge/broker-v1/roots" / root["root_id"]
        require(Path(protected) == expected, "PROTECTED_PATH_MISMATCH", str(protected))
        self.path = expected / "orchestrator"
        self.root = dict(root)

    @contextlib.contextmanager
    def locked(self, name="launch.lock"):
        require(name in {"launch.lock", "profile.lock"}, "UNSAFE_LOCK", name)
        fd = directory(self.path, create=True)
        lock = None
        try:
            lock = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
            info = os.fstat(lock)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSAFE_LOCK", "launch.lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield fd
        finally:
            if lock is not None:
                os.close(lock)
            os.close(fd)

    def current(self):
        try:
            fd = directory(self.path)
        except FileNotFoundError:
            return None
        try:
            reference = read_at(fd, "current.json", missing=True)
            if reference is None:
                try:
                    launches = directory(self.path / "launches")
                except FileNotFoundError:
                    return None
                try:
                    for name in os.listdir(launches):
                        prior = read_at(launches, name)
                        validate_launch(prior, self.root)
                        require(prior["state"] == "failed" and prior.get("disposition") == "unselected-terminal",
                                "UNSELECTED_LAUNCH", "retained records exist without a current reference")
                finally:
                    os.close(launches)
                return None
            require(set(reference) == {"launch_id", "generation"} and identifier(reference["launch_id"])
                    and number(reference["generation"]) and reference["generation"] > 0,
                    "INVALID_REFERENCE", "invalid current reference")
            launches = directory(self.path / "launches")
            try:
                record = read_at(launches, reference["launch_id"] + ".json")
            finally:
                os.close(launches)
            validate_launch(record, self.root)
            require(record["launch_id"] == reference["launch_id"], "INVALID_REFERENCE", "launch id differs")
            return {"reference": reference, "record": record}
        finally:
            os.close(fd)

    def reserve(self, record, expected_generation, predecessor=None):
        validate_launch(record, self.root)
        require(record["state"] == "preparing" and record["revision"] == 1,
                "INVALID_TRANSITION", "new launch must start preparing at revision 1")
        require(number(expected_generation), "INVALID_REVISION", "expected generation required")
        with self.locked() as fd:
            current = self.current()
            generation = current["reference"]["generation"] if current else 0
            require(generation == expected_generation, "REFERENCE_CONFLICT", "current generation changed")
            # The launcher establishes actual terminal ownership outside this
            # short lock. This compare-and-swap binds that observation to the
            # exact predecessor; it is not itself a lifecycle/authority check.
            if current is not None:
                require(predecessor == {"reference": current["reference"], "record_sha256": digest(current["record"])},
                        "HANDOFF_REQUIRED", "exact terminally observed predecessor required")
            launches = directory(self.path / "launches", create=True)
            try:
                if current is None:
                    for name in os.listdir(launches):
                        prior = read_at(launches, name)
                        validate_launch(prior, self.root)
                        require(prior["state"] == "failed" and prior.get("disposition") == "unselected-terminal",
                                "UNSELECTED_LAUNCH", "retained records require explicit terminal disposition")
                # Publish the durable record before its reference. A crash in
                # between leaves an unselected record, never a ready launch.
                write_at(launches, record["launch_id"] + ".json", record, exclusive=True)
            finally:
                os.close(launches)
            write_at(fd, "current.json", {"launch_id": record["launch_id"], "generation": generation + 1})
            return self.current()

    def disposition_unselected(self, expected):
        validate_launch(expected, self.root)
        with self.locked() as directory_fd:
            require(read_at(directory_fd, "current.json", missing=True) is None,
                    "REFERENCE_CONFLICT", "a selected launch now exists")
            launches = directory(self.path / "launches")
            try:
                name = expected["launch_id"] + ".json"
                require(read_at(launches, name) == expected, "REVISION_CONFLICT", "unselected record changed")
                require(expected["state"] == "preparing", "INVALID_TRANSITION", "only interrupted preparation")
                updated = dict(expected, state="failed", revision=expected["revision"] + 1,
                               updated_at=now(), disposition="unselected-terminal",
                               diagnostic="explicit recovery: unselected terminal ownership independently verified")
                validate_launch(updated, self.root)
                write_at(launches, name, updated)
                return updated
            finally:
                os.close(launches)

    def preparing(self, launch_id, expected_revision, expected_generation, construction, **observed):
        transitions = {"shell-created": (("reserved",), {"seat", "handoff"}),
                       "hook-started": (("shell-created",), {"handoff"}),
                       "claimed": (("shell-created", "hook-started"), {"handoff"})}
        require(construction in transitions, "INVALID_TRANSITION", "invalid construction transition")
        previous, fields = transitions[construction]
        require(set(observed) == fields, "FIXED_BINDING", "unexpected construction fields")
        with self.locked():
            current = self.current()
            require(current is not None and current["reference"] == {"launch_id": launch_id, "generation": expected_generation},
                    "REFERENCE_CONFLICT", "current launch changed")
            record = current["record"]
            require(record["revision"] == expected_revision, "REVISION_CONFLICT", "record revision changed")
            require(record["state"] == "preparing" and record.get("construction") in previous,
                    "HANDOFF_ALREADY_CLAIMED", "construction handoff is one-use")
            if "seat" in observed:
                for key in ("provider", "role", "session", "socket"):
                    require(record["seat"][key] == observed["seat"].get(key), "FIXED_BINDING", key)
            updated = dict(record, **observed, construction=construction, revision=expected_revision + 1, updated_at=now())
            validate_launch(updated, self.root)
            launches = directory(self.path / "launches")
            try:
                write_at(launches, launch_id + ".json", updated)
            finally:
                os.close(launches)
            return updated

    def profile(self):
        try:
            fd = directory(self.path)
        except FileNotFoundError:
            return None
        try:
            value = read_at(fd, "profile.json", missing=True)
            if value is None:
                return None
            require(value.get("schema") == "forge-orchestrator-profile/1" and value.get("root") == self.root,
                    "INVALID_PROFILE", "unknown schema or wrong root")
            require(number(value.get("revision")) and value["revision"] > 0 and type(value.get("enabled")) is bool,
                    "INVALID_PROFILE", "revision and enabled state required")
            require(isinstance(value.get("candidate"), dict), "INVALID_PROFILE", "candidate manifest required")
            identity = digest(value)
            snapshots = directory(self.path / "profiles")
            try:
                require(read_at(snapshots, identity + ".json") == value, "PROFILE_SNAPSHOT_MISMATCH", identity)
            finally:
                os.close(snapshots)
            return {"sha256": identity, "profile": value}
        finally:
            os.close(fd)

    def profile_snapshot(self, identity):
        require(sha256(identity), "INVALID_PROFILE", "snapshot hash required")
        fd = directory(self.path / "profiles")
        try:
            value = read_at(fd, identity + ".json")
            require(digest(value) == identity and value.get("root") == self.root
                    and value.get("schema") == "forge-orchestrator-profile/1", "PROFILE_SNAPSHOT_MISMATCH", identity)
            return value
        finally:
            os.close(fd)

    def publish_profile(self, candidate, expected_revision, *, enabled=True):
        """Called only by the admitted operator entry after qualification checks."""
        require(number(expected_revision) and type(enabled) is bool, "INVALID_REVISION", "profile revision required")
        require(isinstance(candidate, dict) and candidate.get("root") == self.root,
                "INVALID_PROFILE", "candidate belongs to another root")
        with self.locked("profile.lock") as fd:
            current = self.profile()
            revision = current["profile"]["revision"] if current else 0
            require(revision == expected_revision, "PROFILE_REVISION_CONFLICT", "profile revision changed")
            if not enabled:
                require(current is not None and candidate == current["profile"]["candidate"],
                        "INVALID_PROFILE", "disable cannot replace approved inputs")
            value = {"schema": "forge-orchestrator-profile/1", "root": self.root, "revision": revision + 1,
                     "enabled": enabled, "candidate": candidate, "registered_at": now()}
            snapshots = directory(self.path / "profiles", create=True)
            try:
                write_at(snapshots, digest(value) + ".json", value, exclusive=True)
            finally:
                os.close(snapshots)
            write_at(fd, "profile.json", value)
            return self.profile()

    def transition(self, launch_id, expected_revision, expected_generation, state, **observed):
        require(state in {"ready", "failed"}, "INVALID_TRANSITION", state)
        require(identifier(launch_id) and number(expected_revision) and number(expected_generation),
                "INVALID_REVISION", "exact launch/revision/generation required")
        allowed = {"process", "broker", "process_epoch"} if state == "ready" else {"diagnostic"}
        require(not (set(observed) - allowed), "FIXED_BINDING", "launch identity is immutable")
        with self.locked():
            current = self.current()
            require(current is not None and current["reference"] == {"launch_id": launch_id, "generation": expected_generation},
                    "REFERENCE_CONFLICT", "current launch changed")
            record = current["record"]
            require(record["revision"] == expected_revision, "REVISION_CONFLICT", "record revision changed")
            require(record["state"] == "preparing", "INVALID_TRANSITION", "terminal records cannot be rewritten")
            updated = dict(record, **observed, state=state, revision=expected_revision + 1, updated_at=now())
            validate_launch(updated, self.root)
            launches = directory(self.path / "launches")
            try:
                write_at(launches, launch_id + ".json", updated)
            finally:
                os.close(launches)
            return updated
