#!/usr/bin/env python3
"""Execute only the disposable mutations declared by the P1 operator harness."""

import errno
import json
import os
from pathlib import Path
import sys


def attempt(case):
    path = Path(case["path"])
    try:
        if case["operation"] == "create":
            with path.open("x") as stream:
                stream.write("mutated\n")
        elif case["operation"] in ("overwrite", "symlink"):
            path.write_text("mutated\n")
        elif case["operation"] == "rename":
            os.rename(path, case["destination"])
        elif case["operation"] == "unlink":
            path.unlink()
        else:
            raise ValueError("unknown operation")
        return {"id": case["id"], "outcome": "allowed"}
    except OSError as exc:
        return {"id": case["id"], "outcome": "denied" if exc.errno in
                (errno.EPERM, errno.EACCES) else "error", "errno": exc.errno}


if __name__ == "__main__":
    plan = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps({"schema": "forge-p1-canary/1", "nonce": plan["nonce"],
                      "results": [attempt(case) for case in plan["cases"]]}))
