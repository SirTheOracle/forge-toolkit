"""Startup attestation is not a claim that the package is sandbox-qualified."""

import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader("p2_broker", str(ROOT / "bin/forge-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker = importlib.util.module_from_spec(spec)
loader.exec_module(broker)


def sign(value):
    value.pop("sha256", None)
    value["sha256"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forge-p2-runtime-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / "entry"
        self.path.write_text("startup bytes")
        self.identity = sign({"schema": "forge-broker-runtime/1", "protocol": broker.CONTROL_PROTOCOL,
                              "entry": str(self.path), "interpreter": str(self.path),
                              "process": {"pid": os.getpid(), "started": "fixture"},
                              "files": [{"path": str(self.path), "sha256": broker.sha(self.path.read_bytes())}]})

    def test_manifest_captures_entry_and_interpreter(self):
        identity = broker.runtime_identity()
        broker.verify_runtime_files(identity)
        paths = {entry["path"] for entry in identity["files"]}
        self.assertIn(identity["entry"], paths)
        self.assertIn(identity["interpreter"], paths)

    def test_changed_bytes_do_not_rewrite_startup_identity(self):
        original = copy.deepcopy(self.identity)
        broker.verify_runtime_files(self.identity)
        self.path.write_text("replacement")
        with self.assertRaises(broker.Refusal) as raised:
            broker.verify_runtime_files(self.identity)
        self.assertEqual(raised.exception.code, "BROKER_RUNTIME_CHANGED")
        self.assertEqual(self.identity, original)

    def test_invalid_manifests_fail_closed(self):
        for value in (None, {}, dict(self.identity, sha256="0" * 64),
                      sign(dict(self.identity, files=[])),
                      sign(dict(self.identity, files=self.identity["files"] * 2)),
                      sign(dict(self.identity, protocol="unknown")),
                      sign(dict(self.identity, entry="/missing"))):
            with self.subTest(value=value), self.assertRaises(broker.Refusal):
                broker.verify_runtime_files(value)

    def test_removal_and_symlink_swap_refuse(self):
        self.path.unlink()
        with self.assertRaises(broker.Refusal):
            broker.verify_runtime_files(self.identity)
        target = self.path.with_name("target")
        target.write_text("startup bytes")
        self.path.symlink_to(target)
        with self.assertRaises(broker.Refusal):
            broker.verify_runtime_files(self.identity)

    def test_proof_binds_manifest_nonce_and_secret(self):
        proof = broker.runtime_proof("secret", b"nonce", self.identity)
        for token, nonce, value in (("other", b"nonce", self.identity),
                                    ("secret", b"other", self.identity),
                                    ("secret", b"nonce", dict(self.identity, entry="other"))):
            self.assertNotEqual(proof, broker.runtime_proof(token, nonce, value))

    def control(self, action, **fields):
        return broker.handle_control(self.path.parent, self.path.parent, self.path.parent,
                                     "root", self.path.parent,
                                     dict(action=action, control_token="secret", **fields), "contain", "secret")

    def test_changed_runtime_refuses_new_delivery_without_calling_effects(self):
        self.path.write_text("changed")
        with patch.object(broker, "STARTUP_RUNTIME", self.identity), \
                patch.object(broker, "register_delivery") as delivery:
            with self.assertRaises(broker.Refusal) as raised:
                self.control("register-delivery")
            self.assertEqual(raised.exception.code, "BROKER_RUNTIME_CHANGED")
            delivery.assert_not_called()

    def test_readonly_inspection_survives_changed_files_and_rejects_fields(self):
        self.path.write_text("changed")
        with patch.object(broker, "STARTUP_RUNTIME", self.identity), \
                patch.object(broker, "process_stamp", return_value="started"):
            result = self.control("inspect-runtime")
            self.assertEqual(result["runtime_identity"], self.identity)
            self.assertEqual(result["pid"], os.getpid())
            with self.assertRaises(broker.Refusal):
                self.control("inspect-runtime", pid=123)

    def test_original_delivery_observation_is_not_blocked_by_changed_runtime(self):
        self.path.write_text("changed")
        with patch.object(broker, "STARTUP_RUNTIME", self.identity), \
                patch.object(broker, "inspect_live_codex", return_value={"observed": True}) as inspect:
            self.assertEqual(self.control("attest-launch"), {"observed": True})
            inspect.assert_called_once()

    def client(self, *, runtime=True, tamper=False, expected=None, started="fixture"):
        protected = self.path.parent
        (protected / "control.token").write_text("secret\n")
        (protected / "pid.json").write_text(json.dumps({"control_host": "127.0.0.1", "control_port": 12345,
                                                       "pid": os.getpid(), "pid_started": started}))
        nonce = b"n" * 32
        hello = {"nonce": nonce.hex(), "server_proof": broker.hmac.new(
            b"secret", b"forge-server\0" + nonce, hashlib.sha256).hexdigest()}
        if runtime:
            hello.update(runtime_identity=self.identity,
                         runtime_proof="bad" if tamper else broker.runtime_proof("secret", nonce, self.identity))
        sock = MagicMock()
        sock.recv.side_effect = [(json.dumps(hello) + "\n").encode(), b'{"status":"ok","detail":{}}\n']
        self.socket = sock
        with patch.object(broker.socket, "socket", return_value=sock), \
                patch.object(broker, "process_stamp", return_value="fixture"):
            return broker.control_call(protected, {"action": "inspect-runtime"}, expected_runtime=expected)

    def test_pinned_authenticated_runtime_succeeds(self):
        self.assertEqual(self.client(expected=self.identity["sha256"])["status"], "ok")
        self.socket.sendall.assert_called_once()

    def test_bad_runtime_proof_never_discloses_control_token(self):
        with self.assertRaises(broker.Refusal) as raised:
            self.client(tamper=True)
        self.assertEqual(raised.exception.code, "CONTROL_RUNTIME_UNAUTHENTICATED")
        self.socket.sendall.assert_not_called()

    def test_legacy_hello_remains_legacy_not_qualified(self):
        self.assertEqual(self.client(runtime=False)["status"], "ok")
        with self.assertRaises(broker.Refusal) as raised:
            self.client(runtime=False, expected=self.identity["sha256"])
        self.assertEqual(raised.exception.code, "BROKER_RUNTIME_MISMATCH")
        self.socket.sendall.assert_not_called()

    def test_wrong_runtime_and_reused_pid_refuse_before_request(self):
        for kwargs in ({"expected": "0" * 64}, {"expected": self.identity["sha256"], "started": "different"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(broker.Refusal):
                self.client(**kwargs)
            self.socket.sendall.assert_not_called()

    def test_changed_runtime_remains_available_for_unpinned_recovery_only(self):
        self.path.write_text("changed")
        self.assertEqual(self.client()["status"], "ok")
        with self.assertRaises(broker.Refusal) as raised:
            self.client(expected=self.identity["sha256"])
        self.assertEqual(raised.exception.code, "BROKER_RUNTIME_CHANGED")
        self.socket.sendall.assert_not_called()

    def test_live_daemon_pins_startup_bytes_and_is_not_reaped_for_source_drift(self):
        root = self.path.parent / "project"
        subprocess.run(["/usr/bin/git", "init", "-q", str(root)], check=True)
        entry = self.path.parent / "toolkit/bin/forge-broker"
        entry.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "bin/forge-broker", entry)
        env = dict(os.environ, FORGE_BROKER_TEST_ENABLE="1", TMUX="")
        def run(*args, request=None):
            return subprocess.run([str(entry), *args, "--root", str(root)], env=env, text=True,
                                  input=json.dumps(request) if request else None,
                                  capture_output=True, timeout=15)
        started = run("start", "--session", "p2-fixture", "--incarnation", "1", "--mode", "contain")
        self.assertEqual(started.returncode, 0, started.stderr)
        try:
            info = json.loads(started.stdout)
            identity = info["runtime_identity"]
            inspected = run("control", "--expect-runtime-sha256", identity["sha256"],
                            request={"action": "inspect-runtime"})
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(json.loads(inspected.stdout)["runtime_identity"], identity)
            entry.write_text(entry.read_text() + "\n# source changed after daemon startup\n")
            status = run("status", "--json")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["runtime_state"], "changed")
            self.assertEqual(json.loads(status.stdout)["runtime_identity"], identity)
            refused = run("control", request={"action": "register-delivery"})
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("BROKER_RUNTIME_CHANGED", refused.stderr)
            reuse = run("start", "--session", "p2-fixture", "--incarnation", "1", "--mode", "contain")
            self.assertEqual(reuse.returncode, 0, reuse.stderr)
            self.assertEqual(json.loads(reuse.stdout)["pid"], info["pid"])
            inspected = run("control", request={"action": "inspect-runtime"})
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(json.loads(inspected.stdout)["runtime_identity"], identity)
        finally:
            stopped = run("stop", "--timeout", "5")
            self.assertEqual(stopped.returncode, 0, stopped.stderr)


if __name__ == "__main__":
    unittest.main()
