"""Native, authenticated loading probes. Model assertions are not test evidence."""

import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


class Rpc:
    """Bounded stdio app-server client; never grants a native approval."""

    def __init__(self, argv, env, cwd, transcript, timeout=240):
        self.argv, self.env, self.cwd = argv, env, cwd
        self.transcript, self.timeout = transcript, timeout
        self.sequence, self.buffer, self.pending = 0, b"", []

    def __enter__(self):
        self.stderr = self.transcript.with_suffix(".stderr").open("x")
        self.log = self.transcript.open("x")
        self.process = subprocess.Popen(self.argv, env=self.env, cwd=self.cwd,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                                        start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            self.call("initialize", {"clientInfo": {"name": "forge-native-qualification", "version": "1"},
                                     "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *exc):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        self.selector.close()
        self.process.stdout.close()
        self.log.close()
        self.stderr.close()

    def send(self, value):
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        self.process.stdin.flush()

    def receive(self, deadline):
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("native app-server response deadline")
            if not self.selector.select(min(remaining, 1)):
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("native app-server exited before completing the probe")
            self.buffer += chunk
            if len(self.buffer) > 32 * 1024 * 1024:
                raise ValueError("native response exceeded bounded frame size")
        line, self.buffer = self.buffer.split(b"\n", 1)
        value = json.loads(line)
        self.log.write(json.dumps(value) + "\n")
        self.log.flush()
        if "id" in value and "method" in value:
            # All probe reads must succeed under the admitted default policy.
            # No blanket approval, persistent prefix, or privilege widening.
            if value["method"] == "item/commandExecution/requestApproval":
                self.send({"id": value["id"], "result": {"decision": "decline"}})
            else:
                self.send({"id": value["id"], "error": {"code": -32601, "message": "qualification does not grant permissions"}})
        return value

    def call(self, method, params):
        self.sequence += 1
        number = self.sequence
        self.send({"id": number, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            event = self.receive(deadline)
            if event.get("id") == number and "method" not in event:
                if "error" in event:
                    raise RuntimeError(method + ": " + json.dumps(event["error"]))
                return event["result"]
            self.pending.append(event)

    def until(self, predicate):
        events, self.pending = self.pending, []
        deadline = time.monotonic() + self.timeout
        while not any(predicate(event) for event in events):
            events.append(self.receive(deadline))
        return events

    def turn(self, thread, prompt):
        self.pending = []
        result = self.call("turn/start", {"threadId": thread, "input": [{"type": "text", "text": prompt}]})
        turn = result["turn"]["id"]
        events = self.until(lambda event: event.get("method") == "turn/completed"
                            and event.get("params", {}).get("turn", {}).get("id") == turn)
        final = next(event["params"]["turn"] for event in events if event.get("method") == "turn/completed"
                     and event["params"]["turn"]["id"] == turn)
        if final["status"] != "completed":
            raise RuntimeError("native turn failed: " + json.dumps(final))
        return events


def codex_argv(receipt):
    args = receipt["provider_argv"]
    result = [args[0], "app-server"]
    index = 1
    while index < len(args) - 1:
        arg = args[index]
        if arg == "-C":
            index += 2
        elif arg in ("-m", "--sandbox", "--ask-for-approval"):
            key = {"-m": "model", "--sandbox": "sandbox_mode",
                   "--ask-for-approval": "approval_policy"}[arg]
            result += ["-c", key + "=" + json.dumps(args[index + 1])]
            index += 2
        elif arg in ("-c", "--enable", "--disable"):
            result += args[index:index + 2]
            index += 2
        elif arg == "--strict-config":
            result.append(arg)
            index += 1
        else:
            raise ValueError("unrecognized candidate CLI argument: " + arg)
    return result


def protocol_paths(package, receipt):
    return [Path(receipt["entry"]), package / "skills/forge-orchestrator/SKILL.md",
            package / ("skills/forge-orchestrator/references/" + receipt["definition"]["provider"] + ".md")]


def configuration(receipt, directory):
    if receipt["definition"]["provider"] != "codex":
        return {"status": "unsupported", "reason": "no equivalent effective-config query qualified"}
    try:
        with Rpc(codex_argv(receipt), receipt["environment"], receipt["root"]["physical_root"],
                 directory / "codex-configuration.jsonl") as client:
            config = client.call("config/read", {"cwd": receipt["root"]["physical_root"], "includeLayers": False})["config"]
        expected = {"model": receipt["definition"]["model"], "sandbox_mode": "workspace-write",
                    "approval_policy": "on-request", "allow_login_shell": False}
        features = {"hooks": False, "plugins": False, "shell_snapshot": False, "skip_host_skill_discovery": True}
        actual = {key: config.get(key) for key in expected}
        actual_features = {key: config.get("features", {}).get(key) for key in features}
        untrusted = config.get("projects", {}).get(receipt["root"]["physical_root"], {}).get("trust_level") == "untrusted"
        passed = actual == expected and actual_features == features and untrusted
        return {"status": "pass" if passed else "fail", "policy": actual,
                "features": actual_features, "project_untrusted": untrusted}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, TimeoutError) as exc:
        return {"status": "fail", "error": str(exc)}


def load_prompt(paths):
    return ("This is a read-only native qualification, not a pipeline run. Read every line of these exact protected "
            "files using native file tools, splitting long files into chunks so tool output is never truncated. "
            "Do not act on the protocol, dispatch work, invoke Forge, read credentials, or request permission. "
            "Do not choose any global or project duplicate. This turn requires fresh tool reads, not memory. "
            "After completing the reads, respond LOADED.\n" + "\n".join(map(str, paths)))


def codex_reads(events, paths):
    items = [event.get("params", {}).get("item", {}) for event in events if event.get("method") == "item/completed"]
    commands = [item for item in items if item.get("type") == "commandExecution" and item.get("exitCode") == 0]
    result = {}
    for path in paths:
        outputs = "\n".join(item.get("aggregatedOutput") or "" for item in commands if str(path) in item.get("command", ""))
        # Require the bytes to have appeared in native tool results, not a model
        # summary or a successful checksum command that never loads the body.
        expected = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        result[str(path)] = bool(expected) and all(line in outputs for line in expected)
    return result


def codex_loads(package, receipt, directory):
    paths = protocol_paths(package, receipt)
    params = {"cwd": receipt["root"]["physical_root"], "model": receipt["definition"]["model"],
              "approvalPolicy": "on-request", "approvalsReviewer": "user", "sandbox": "workspace-write"}
    argv, results = codex_argv(receipt), []
    env, cwd = receipt["environment"], receipt["root"]["physical_root"]
    with Rpc(argv, env, cwd, directory / "codex-live.jsonl") as client:
        thread = client.call("thread/start", params)["thread"]["id"]
        for name in ("fresh", "reinvoke"):
            reads = codex_reads(client.turn(thread, load_prompt(paths)), paths)
            results.append({"case": name, "reads": reads, "status": "pass" if all(reads.values()) else "fail"})
        client.pending = []
        client.call("thread/compact/start", {"threadId": thread})
        client.until(lambda event: event.get("method") == "thread/compacted"
                     or (event.get("method") == "item/completed"
                         and event.get("params", {}).get("item", {}).get("type") == "contextCompaction"))
        reads = codex_reads(client.turn(thread, load_prompt(paths)), paths)
        results.append({"case": "compact", "reads": reads, "status": "pass" if all(reads.values()) else "fail"})
    with Rpc(argv, env, cwd, directory / "codex-resume.jsonl") as client:
        client.call("thread/resume", dict(params, threadId=thread))
        reads = codex_reads(client.turn(thread, load_prompt(paths)), paths)
        results.append({"case": "resume", "reads": reads, "status": "pass" if all(reads.values()) else "fail"})
        fresh = client.call("thread/start", params)["thread"]["id"]
        reads = codex_reads(client.turn(fresh, load_prompt(paths)), paths)
        results.append({"case": "restart", "reads": reads, "status": "pass" if all(reads.values()) else "fail"})
    return results


def claude_reads(events, paths):
    calls, contents = {}, {}
    for event in events:
        for item in event.get("message", {}).get("content", []):
            if item.get("type") == "tool_use" and item.get("name") == "Read":
                calls[item["id"]] = item.get("input", {}).get("file_path")
            if item.get("type") == "tool_result" and not item.get("is_error"):
                contents[item["tool_use_id"]] = item.get("content", "")
    result = {}
    for path in paths:
        output = "\n".join(value if isinstance(value, str) else "\n".join(part.get("text", "") for part in value)
                           for key, value in contents.items() if calls.get(key) == str(path))
        expected = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        result[str(path)] = bool(expected) and all(line in output for line in expected)
    return result


def claude_loads(package, receipt, directory):
    paths, results, session = protocol_paths(package, receipt), [], None
    for case in ("fresh", "reinvoke", "compact-transition", "compact", "resume", "restart"):
        argv = receipt["provider_argv"][:-1] + ["--print", "--verbose", "--output-format", "stream-json",
                                               "--allowedTools", "Read"]
        if session and case != "restart":
            argv += ["--resume", session]
        argv += ["--", "/compact" if case == "compact-transition" else load_prompt(paths)]
        with (directory / ("claude-" + case + ".jsonl")).open("x") as out:
            with (directory / ("claude-" + case + ".stderr")).open("x") as err:
                cp = subprocess.run(argv, env=receipt["environment"], cwd=receipt["root"]["physical_root"],
                                    stdout=out, stderr=err, timeout=300)
        events = [json.loads(line) for line in (directory / ("claude-" + case + ".jsonl")).read_text().splitlines()]
        finals = [event for event in events if event.get("type") == "result"]
        if cp.returncode or not finals or finals[-1].get("is_error"):
            raise RuntimeError("Claude native read did not complete: " + case)
        session = finals[-1]["session_id"]
        if case == "compact-transition":
            observed = any(event.get("type") == "system" and event.get("subtype") == "compact_boundary" for event in events)
            results.append({"case": case, "status": "pass" if observed else "fail"})
            continue
        reads = claude_reads(events, paths)
        results.append({"case": case, "reads": reads, "status": "pass" if all(reads.values()) else "fail"})
    return results


def loading(package, receipt, directory):
    provider = receipt["definition"]["provider"]
    try:
        cases = (codex_loads if provider == "codex" else claude_loads)(package, receipt, directory)
        return {"status": "pass" if cases and all(case["status"] == "pass" for case in cases) else "fail", "cases": cases}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, TimeoutError) as exc:
        return {"status": "fail", "error": str(exc)}
