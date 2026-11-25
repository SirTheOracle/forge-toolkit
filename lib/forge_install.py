"""Ownership-aware legacy asset installation; never refresh an existing path."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


def identity(path):
    if path.is_symlink():
        return {"link": os.readlink(path)}
    if path.is_dir():
        return {p.name: identity(p) for p in sorted(path.iterdir())}
    if path.is_file():
        return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return None


def manage(action, source, target, state):
    source, target, state = map(Path, (source, target, state))
    if any(p.is_symlink() for p in (state, *state.parents, *target.parents)):
        raise ValueError("symlinked ownership store or destination parent")
    state.parent.mkdir(parents=True, exist_ok=True)
    lock = os.open(str(state) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(lock).st_nlink != 1:
            raise ValueError("hardlinked ownership lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        _manage_locked(action, source, target, state)
    finally:
        os.close(lock)


def _manage_locked(action, source, target, state):
    if state.exists() and state.stat().st_nlink != 1:
        raise ValueError("hardlinked ownership store")
    owned = json.loads(state.read_text()) if state.exists() else {}
    key = str(target.absolute())
    if action in ("copy", "link"):
        if target.exists() or target.is_symlink():
            print("Preserved existing: " + key)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if action == "link":
            target.symlink_to(source)
        elif source.is_dir():
            shutil.copytree(source, target)
        else:
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
        owned[key] = identity(target)
    elif action == "remove":
        if key not in owned or identity(target) != owned[key]:
            print("Preserved unowned or modified: " + key)
            return
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        del owned[key]
    else:
        raise ValueError("unknown ownership operation")
    state.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ownership-", dir=state.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(owned, stream, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, state)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


if __name__ == "__main__":
    try:
        manage(*sys.argv[1:])
    except (OSError, ValueError) as exc:
        sys.exit("INSTALL_REFUSED: " + str(exc))
