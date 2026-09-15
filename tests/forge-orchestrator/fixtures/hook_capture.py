"""Record native P1 hook input and forward supported events to the copied adapter."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


EVENTS = {"UserPromptSubmit", "Stop", "PermissionRequest", "Notification", "PostToolUse"}


def main():
    config_path = Path(sys.argv[1]).resolve(strict=True)
    config = json.loads(config_path.read_text())
    raw = sys.stdin.buffer.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("native hook payload must be an object")
    event = payload.get("hook_event_name")
    argv = None
    result = None
    if event in EVENTS:
        argv = [config["python"], config["adapter"]]
        if config["provider"] == "codex":
            argv.append("--codex")
        argv.append(event.lower())
        completed = subprocess.run(argv, input=raw, capture_output=True,
                                   cwd=config["root"], env=config["environment"], timeout=12)
        result = {"returncode": completed.returncode,
                  "stdout": completed.stdout.decode("utf-8", "replace"),
                  "stderr": completed.stderr.decode("utf-8", "replace")}
    directory = Path(config["evidence"])
    record = {"schema": "forge-p1-native-hook/1", "provider": config["provider"],
              "pid": os.getpid(), "parent_pid": os.getppid(), "time_ns": time.time_ns(),
              "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
              "payload": payload, "adapter_argv": argv, "adapter_result": result}
    with (directory / (str(time.time_ns()) + "-" + uuid.uuid4().hex + ".json")).open("x") as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
        stream.write("\n")
    # The observer never approves, denies, rewrites or adds model context.


if __name__ == "__main__":
    main()
