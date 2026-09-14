#!/usr/bin/env python3
"""Measure write-open denial on the actual copied files, without changing them."""

import errno
import hashlib
import json
import os
from pathlib import Path
import sys


def check(path, expected_hash):
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    except OSError as exc:
        outcome = "denied" if exc.errno in (errno.EACCES, errno.EPERM) else "error"
    else:
        os.close(descriptor)
        outcome = "allowed"
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": str(path), "write_open": outcome,
            "unchanged": before == after == expected_hash}


if __name__ == "__main__":
    manifest = json.loads(Path(sys.argv[1]).read_text())
    positive = Path(sys.argv[2])
    try:
        with positive.open("x") as stream:
            stream.write("P1 workspace write succeeded\n")
        control = "allowed"
    except OSError:
        control = "denied-or-error"
    rows = [check(Path(item["path"]), item["sha256"]) for item in manifest["files"].values()]
    print(json.dumps({"schema": "forge-p1-package-access/1", "positive_control": control,
                      "files": rows, "pass": control == "allowed" and
                      all(row["write_open"] == "denied" and row["unchanged"] for row in rows)}))
