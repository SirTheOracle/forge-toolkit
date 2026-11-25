"""Invocation-owned tmux fixture. Never selects or modifies a production server."""

import json
from pathlib import Path
import re
import shlex
import secrets
import time

import runtime_probe


class Fixture:
    def __init__(self, probe, base, packet):
        self.probe, self.base, self.packet = probe, Path(base), packet
        self.package = Path(json.loads((self.base / "package.json").read_text())["package"])
        self.nonce = secrets.token_hex(4)
        self.directory = self.base / ("host-" + self.nonce)
        self.directory.mkdir()
        self.socket = "forge-p1-" + packet["id"] + "-" + self.nonce
        self.tmux = json.loads((self.package / "runtime/runtime.json").read_text())["tools"]["tmux"]
        self.home = self.directory / "home"
        self.home.mkdir()
        self.env = runtime_probe.environment(self.package, self.home)
        self.sessions = {}
        self.commands = []
        self.created = False

    @classmethod
    def resume(cls, probe, directory):
        directory = Path(directory).resolve(strict=True)
        if not re.fullmatch(r"host-[a-f0-9]{8}", directory.name):
            raise ValueError("invalid fixture directory")
        base, packet = probe.load_packet(directory.parent)
        obj = cls.__new__(cls)
        obj.probe, obj.base, obj.packet = probe, base, packet
        obj.package = Path(json.loads((base / "package.json").read_text())["package"])
        obj.nonce, obj.directory = directory.name[5:], directory
        obj.socket = "forge-p1-" + packet["id"] + "-" + obj.nonce
        obj.tmux = json.loads((obj.package / "runtime/runtime.json").read_text())["tools"]["tmux"]
        obj.home = directory / "home"
        obj.env = runtime_probe.environment(obj.package, obj.home)
        obj.sessions, obj.commands, obj.created = {}, [], True
        for topology in ("primary", "linked"):
            session = "p1-" + obj.nonce + "-" + topology
            root = str(packet["topologies"][topology]["root"])
            observed = obj.call("display-message", "-p", "-t", session, "#{session_path}")["stdout"].strip()
            if observed != root:
                raise ValueError("fixture session root mismatch")
            obj.sessions[topology] = {"name": session, "root": root}
        return obj

    def call(self, *args, check=True):
        argv = [self.tmux, "-L", self.socket, "-f", "/dev/null", *args]
        result = self.probe.execute(argv, cwd=self.base, env=self.env)
        if check and (result["returncode"] != 0 or result["error"]):
            raise ValueError("fixture tmux failed: " + result["stderr"][:500])
        return result

    def binding(self, topology, index):
        if type(index) is not int or index not in range(5):
            raise ValueError("invalid fixture pane index")
        session = self.sessions[topology]["name"]
        observed = self.call("display-message", "-p", "-t", session + ":." + str(index),
                             "#{socket_path}|#{pid}|#{session_id}|#{pane_id}|#{session_name}")
        fields = observed["stdout"].strip().split("|")
        if len(fields) != 5:
            raise ValueError("invalid fixture tmux binding")
        socket, pid, session_id, pane, name = fields
        if (not Path(socket).is_absolute() or Path(socket).name != self.socket
                or not re.fullmatch(r"[1-9][0-9]*", pid)
                or not re.fullmatch(r"\$[0-9]+", session_id)
                or not re.fullmatch(r"%[0-9]+", pane) or name != session):
            raise ValueError("fixture tmux binding mismatch")
        # tmux formats a session ID as $N, but TMUX stores the numeric N.
        return {"TMUX": socket + "," + pid + "," + session_id[1:],
                "TMUX_PANE": pane, "TMUX_SESSION": name}

    def start(self):
        existing = self.call("list-sessions", check=False)
        if existing["returncode"] == 0:
            raise ValueError("refusing an existing fixture server")
        for name in ("primary", "linked"):
            root = Path(self.packet["topologies"][name]["root"])
            (root / ".dev").mkdir(exist_ok=True)
            (root / ".claude").mkdir(exist_ok=True)
            config = {"name": "p1-" + name, "forge": {"expected_root": str(root)}}
            config_path = root / ".claude/forge-project.yml"
            if config_path.exists():
                if json.loads(config_path.read_text()) != config:
                    raise ValueError("fixture project config changed")
            else:
                self.probe.write_json(config_path, config)
            shell = shlex.join(["/usr/bin/env", "-i", "HOME=" + str(self.home),
                               "PATH=" + self.env["PATH"], "TERM=xterm-256color"])
            shell += ' TMUX="$TMUX" TMUX_PANE="$TMUX_PANE" /bin/bash --noprofile --norc'
            session = "p1-" + self.nonce + "-" + name
            self.call("new-session", "-d", "-s", session, "-c", str(root),
                      "-x", "160", "-y", "50", shell)
            self.created = True
            self.call("set-environment", "-t", session, "FORGE_LAYOUT", "2")
            for _ in range(4):
                self.call("split-window", "-d", "-t", session, "-c", str(root), shell)
                self.call("select-layout", "-t", session, "tiled")
            for index, role in enumerate(("claude", "claude-opus", "claude-sonnet", "codex-a", "codex-b")):
                self.call("set-option", "-p", "-t", session + ":." + str(index), "@forge-worker", role)
            panes = self.call("list-panes", "-t", session, "-F",
                              "#{pane_index}\t#{pane_id}\t#{session_created}\t#{session_path}")["stdout"]
            self.sessions[name] = {"name": session, "root": str(root), "panes": panes}
        self.probe.write_json(self.directory / "fixture.json", {"socket_name": self.socket,
                              "sessions": self.sessions, "scope": "disposable shell fixture, not provider admission"})

    def command(self, topology, helper, arguments, *, input_value=None):
        if helper not in {"forge-bridge", "forge-broker", "forge"}:
            raise ValueError("unsupported fixture helper")
        row = self.sessions[topology]
        root = Path(row["root"])
        number = secrets.token_hex(6)
        output = root / ".dev" / ("p1-host-%s-%s.out" % (self.nonce, number))
        status = output.with_suffix(".status")
        env_argv = ["/usr/bin/env", "-i", *(k + "=" + v for k, v in self.env.items())]
        # Expansion happens in the actual pane; no synthetic TMUX identity.
        command = shlex.join(env_argv) + ' TMUX="$TMUX" TMUX_PANE="$TMUX_PANE" ' + shlex.join(
            [str(self.package / "bin" / helper), *arguments])
        if input_value is not None:
            command = "printf '%s' " + shlex.quote(json.dumps(input_value)) + " | " + command
        command += " > " + shlex.quote(str(output)) + " 2>&1"
        command += "; printf '%s\\n' $? > " + shlex.quote(str(status))
        target = row["name"] + ":.0"
        self.call("send-keys", "-t", target, "-l", command)
        self.call("send-keys", "-t", target, "Enter")
        deadline = time.monotonic() + 30
        while not status.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        result = {"helper": helper, "arguments": arguments, "topology": topology,
                  "returncode": int(status.read_text().strip()) if status.exists() else None,
                  "output": output.read_text() if output.exists() else "",
                  "surface": "operator-driven copied helper in a real disposable tmux pane"}
        self.commands.append(result)
        return result

    def stop(self):
        if self.created:
            # Socket name derives from this packet, never an ambient TMUX target.
            self.call("kill-server")
            self.created = False
