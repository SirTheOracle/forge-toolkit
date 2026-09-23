"""Real tmux/process transaction tests with non-model fixture executables."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from host_fixture import Fixture, ROOT, host


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(tmux=True)
        self.addCleanup(self.f.close)
        self.f.register()

    def start(self, provider="codex", action="start", **env):
        return self.f.run(action, "--root", str(self.f.root), "--session", "fixture", "--provider", provider,
                          "--no-attach", env=env)

    def test_direct_start_observes_provider_and_broker_before_ready(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["provider"], "codex")
        current = self.f.ctx.store.current()
        self.assertEqual(current["record"]["state"], "ready")
        self.assertEqual(current["record"]["process_epoch"], 1)
        self.assertEqual(current["record"]["handoff"]["claimant"], current["record"]["owner"])
        result = self.f.run("start", "--root", str(self.f.root), "--session", "fixture", "--mode", "contain",
                            "--incarnation", current["record"]["seat"]["incarnation"], "--fresh",
                            entry=self.f.package / "bin/forge-broker")
        self.assertIn("BROKER_ALREADY_RUNNING", result.stderr)
        self.assertEqual(self.f.ctx.store.current(), current)

    def test_tmux_server_environment_cannot_inject_pane_environment(self):
        subprocess.run([self.f.tmux, "-L", self.f.socket, "set-environment", "-g", "P2_SERVER_POISON", "1"], check=True)
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.f.ctx.store.current()["record"]["state"], "ready")

    def test_contradictory_advisory_metadata_does_not_override_binding(self):
        self.assertEqual(self.start().returncode, 0)
        current = self.f.ctx.store.current()
        subprocess.run([self.f.tmux, "-L", self.f.socket, "set-option", "-p", "-t", current["record"]["seat"]["pane_id"],
                        "@forge-provider", "claude"], check=True)
        self.assertIn("LAUNCH_IDENTITY_MISMATCH", self.start().stderr)
        self.assertEqual(self.f.ctx.store.current(), current)

    def test_readonly_observation_validates_provider_and_event_pane(self):
        self.assertEqual(self.start().returncode, 0)
        current = self.f.ctx.store.current()
        pane = current["record"]["seat"]["pane_id"]
        for requested, expected in ((pane, "codex"), ("%99999", "unknown")):
            result = self.f.run("observe", "--root", str(self.f.root), "--session", "fixture", "--pane-id", requested)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["provider"], expected)
            self.assertEqual(self.f.ctx.store.current(), current)

    def test_spawn_transfers_one_use_handoff_through_builtin_populate(self):
        result = self.start(action="spawn")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = self.f.ctx.store.current()
        self.assertEqual(current["record"]["state"], "ready")
        self.assertNotEqual(current["record"]["owner"]["pid"], current["record"]["handoff"]["claimant"]["pid"])
        self.assertTrue(current["record"]["handoff"]["chain"])

    def test_arbitrary_idle_shell_has_no_populate_authority(self):
        f = self.f
        subprocess.run([f.tmux, "-L", f.socket, "new-session", "-d", "-s", "fixture", "-c", str(f.root),
                        "/bin/bash", "--noprofile", "--norc"], check=True)
        before = subprocess.check_output([f.tmux, "-L", f.socket, "list-panes", "-t", "fixture", "-F", "#{pane_id}:#{pane_pid}"], text=True)
        result = f.run("populate", "--root", str(f.root), "--session", "fixture", "--provider", "codex", "--launch-id", "a" * 32,
                       env={"FORGE_ORCHESTRATOR_PROVIDER": "codex", "FORGE_ORCHESTRATOR_REQUEST_VERSION": "1"})
        self.assertIn("HANDOFF_MISSING", result.stderr)
        after = subprocess.check_output([f.tmux, "-L", f.socket, "list-panes", "-t", "fixture", "-F", "#{pane_id}:#{pane_pid}"], text=True)
        self.assertEqual(before, after)
        self.assertIsNone(f.ctx.store.current())

    def panes(self):
        return subprocess.check_output([self.f.tmux, "-L", self.f.socket, "list-panes", "-t", "fixture",
                                        "-F", "#{pane_id}:#{pane_pid}:#{@forge-worker}:#{@forge-provider}"], text=True)

    def stop_session(self):
        result = self.f.run("stop", "--root", str(self.f.root), entry=self.f.package / "bin/forge-broker")
        self.assertEqual(result.returncode, 0, result.stderr)
        subprocess.run([self.f.tmux, "-L", self.f.socket, "kill-session", "-t", "fixture"], check=True)

    def test_matching_ensure_and_omission_preserve_every_pane_and_record(self):
        self.assertEqual(self.start().returncode, 0)
        before, record = self.panes(), self.f.ctx.store.current()
        for args in (("--provider", "codex"), ()):
            result = self.f.run("start", "--root", str(self.f.root), "--session", "cosmetic", "--no-attach", *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["provider"], "codex")
            self.assertEqual(self.panes(), before)
            self.assertEqual(self.f.ctx.store.current(), record)
        result = subprocess.run([str(self.f.package / "bin/forge-start"), "--here", "--no-attach"],
                                cwd=self.f.root, env=self.f.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["provider"], "codex")

    def test_mismatch_and_reused_handoff_cannot_mutate_live_provider(self):
        self.assertEqual(self.start().returncode, 0)
        before, current = self.panes(), self.f.ctx.store.current()
        result = self.start("claude")
        self.assertIn("PROVIDER_MISMATCH", result.stderr)
        for provider in ("claude", "codex"):
            result = self.f.run("populate", "--root", str(self.f.root), "--session", "fixture", "--provider", provider,
                                "--launch-id", current["record"]["launch_id"],
                                env={"FORGE_ORCHESTRATOR_PROVIDER": provider, "FORGE_ORCHESTRATOR_REQUEST_VERSION": "1"})
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.panes(), before)
            self.assertEqual(self.f.ctx.store.current(), current)

    def test_provider_exec_replacement_refuses_without_restamping(self):
        self.assertEqual(self.start().returncode, 0)
        current = self.f.ctx.store.current()
        pane = current["record"]["seat"]["pane_id"]
        subprocess.run([self.f.tmux, "-L", self.f.socket, "respawn-pane", "-k", "-t", pane,
                        str(self.f.provider), "--wrong-argv"], check=True)
        before = self.panes()
        result = self.start()
        self.assertIn("PROVIDER_PROCESS_MISMATCH", result.stderr)
        self.assertEqual(self.panes(), before)
        self.assertEqual(self.f.ctx.store.current(), current)
        # The daemon independently performs the same check before allocating
        # a delivery; this must not recurse into its own control socket.
        result = subprocess.run([str(self.f.package / "bin/forge-broker"), "control", "--root", str(self.f.root)],
                                input=json.dumps({"action": "register-delivery", "session": "fixture", "pane": "1"}),
                                env=self.f.env, cwd=self.f.root, capture_output=True, text=True, timeout=10)
        self.assertIn("PROVIDER_PROCESS_MISMATCH", result.stderr)
        self.assertFalse(any((self.f.ctx.protected / "deliveries").glob("*.json")))

    def test_registration_during_live_work_preserves_fixed_binding(self):
        self.assertEqual(self.start().returncode, 0)
        current, before = self.f.ctx.store.current(), self.panes()
        self.f.register(revision=1)
        self.assertIn("PROFILE_CHANGED", self.start().stderr)
        self.assertEqual(self.f.ctx.store.current(), current)
        self.assertEqual(self.panes(), before)
        self.stop_session()

    def test_disabled_profile_refuses_new_admission_without_stopping_broker(self):
        self.assertEqual(self.start().returncode, 0)
        current = self.f.ctx.store.current()
        result = self.f.run("orchestrator-profile", "disable", "--root", str(self.f.root), "--expect-revision", "1",
                            entry=self.f.ctx.management_entry)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PROFILE_DISABLED", self.start().stderr)
        self.assertEqual(current, self.f.ctx.store.current())
        self.stop_session()

    def test_rollout_uses_live_binding_and_never_hot_switches_provider(self):
        self.assertEqual(self.start().returncode, 0)
        current, panes = self.f.ctx.store.current(), self.panes()
        for provider, expected in (("codex", 0), ("claude", 3)):
            result = self.f.run("rollout", "--root", str(self.f.root), "--provider", provider, "--check")
            self.assertEqual(result.returncode, expected, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["binding"]["launch_id"], current["record"]["launch_id"])
            self.assertEqual(report["eligible"], provider == "codex")
            if provider == "codex":
                self.assertEqual(report["boundary"], "existing-session")
        self.assertEqual(self.f.ctx.store.current(), current)
        self.assertEqual(self.panes(), panes)

    def test_rollout_allows_terminal_handoff_without_rewriting_old_binding(self):
        self.assertEqual(self.start().returncode, 0)
        current = self.f.ctx.store.current()
        self.stop_session()
        self.f.register(revision=1)
        result = self.f.run("rollout", "--root", str(self.f.root), "--provider", "claude", "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["boundary"], "new-session")
        self.assertEqual(report["profile"]["revision"], 2)
        self.assertEqual(report["binding"]["launch_id"], current["record"]["launch_id"])
        self.assertEqual(self.f.ctx.store.current(), current)
        unresolved = self.f.root / ".dev/forge-tmp/callbacks/unconsumed.txt"
        unresolved.parent.mkdir(parents=True)
        unresolved.write_text("unconsumed evidence")
        result = self.f.run("rollout", "--root", str(self.f.root), "--provider", "claude", "--check")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout)["reason"]["code"], "HANDOFF_REQUIRED")
        self.assertEqual(unresolved.read_text(), "unconsumed evidence")

    def test_rollback_blocks_new_delivery_but_preserves_owned_callback_recovery(self):
        self.assert_callback_recovery("disable")

    def test_input_drift_blocks_new_delivery_but_preserves_owned_callback_recovery(self):
        self.assert_callback_recovery("drift")

    def assert_callback_recovery(self, change):
        self.assertEqual(self.start().returncode, 0)
        f, current = self.f, self.f.ctx.store.current()
        def control(request):
            return subprocess.run([str(f.package / "bin/forge-broker"), "control", "--root", str(f.root)],
                                  input=json.dumps(request), env=f.env, cwd=f.root,
                                  capture_output=True, text=True, timeout=15)

        # Seed a previously admitted, owned workspace delivery. Only the rollback
        # and real daemon reconciliation paths are under test, not model execution.
        delivery_id = "delivery-" + "a" * 32
        delivery = {"schema": host.broker.DELIVERY_SCHEMA,
                    "delivery_id": delivery_id, "state": "open", "session": "fixture", "pane_index": 1,
                    "physical_code_root": str(f.root), "root_identity": f.ctx.root_id,
                    "session_incarnation": current["record"]["seat"]["incarnation"],
                    "slug": "rollback", "stage": "review", "worker": "claude-opus",
                    "capability_class": "workspace", "expires_at": "2099-01-01T00:00:00Z"}
        for path in (f.ctx.protected / "deliveries" / (delivery_id + ".json"),
                     f.ctx.protected / "active/fixture.p1.json"):
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(delivery))
        if change == "disable":
            disabled = f.run("orchestrator-profile", "disable", "--root", str(f.root), "--expect-revision", "1",
                             entry=f.ctx.management_entry)
            self.assertEqual(disabled.returncode, 0, disabled.stderr)
            code = "PROFILE_DISABLED"
        else:
            (f.package / "assets/core").write_text("changed protocol")
            code = "INPUT_CHANGED"
        refused = control({"action": "register-delivery", "session": "fixture", "pane": "1"})
        self.assertIn(code, refused.stderr)
        diagnostic = f.run("rollout", "--root", str(f.root), "--check")
        self.assertEqual(diagnostic.returncode, 3, diagnostic.stderr)
        self.assertEqual(json.loads(diagnostic.stdout)["reason"]["code"], code)
        active = control({"action": "active-delivery", "session": "fixture", "pane": "1"})
        self.assertEqual(active.returncode, 0, active.stderr)
        callback = f.root / ".dev/forge-tmp/callbacks/rollback.txt"
        callback.parent.mkdir(parents=True, exist_ok=True)
        callback.write_text("\n".join(f"{key}: {value}" for key, value in {
            "slug": "rollback", "stage": "review", "worker": "claude-opus", "session": "fixture",
            "incarnation": delivery["session_incarnation"], "delivery_id": delivery_id,
            "status": "DONE", "selected_pending_timestamp": "2026-09-23T00:00:00Z"}.items()) + "\n")
        log = f.root / ".dev/proposals/rollback/forge-log.yml"
        log.parent.mkdir(parents=True)
        log.write_text('entries:\n  - timestamp: "2026-09-23T00:00:00Z"\n    stage: review\n    response: FORGE_DONE\n')
        request = {"action": "reconcile-delivery", "delivery_id": delivery_id, "terminal": "completed",
                   "callback_path": str(callback), "callback_sha256": host.file_identity(callback)["sha256"]}
        forged = control(dict(request, callback_sha256="0" * 64))
        self.assertIn("LIFECYCLE_ARTIFACT_MISMATCH", forged.stderr)
        self.assertTrue((f.ctx.protected / "active/fixture.p1.json").exists())
        for _ in range(2):
            recovered = control(request)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(host.read_json(f.ctx.protected / "deliveries" / (delivery_id + ".json"))["state"], "completed")
        self.assertFalse((f.ctx.protected / "active/fixture.p1.json").exists())
        self.assertEqual(f.ctx.store.current(), current)

    def test_terminal_handoff_keeps_old_record_and_increments_generation(self):
        self.assertEqual(self.start().returncode, 0)
        old = self.f.ctx.store.current()
        self.stop_session()
        self.assertEqual(self.start("claude").returncode, 0)
        new = self.f.ctx.store.current()
        self.assertNotEqual(old["reference"]["launch_id"], new["reference"]["launch_id"])
        self.assertEqual(new["reference"]["generation"], old["reference"]["generation"] + 1)
        retained = host.read_json(self.f.ctx.store.path / "launches" / (old["record"]["launch_id"] + ".json"))
        self.assertEqual(retained, old["record"])

    def test_terminal_audit_residue_does_not_block_but_open_request_does(self):
        self.assertEqual(self.start().returncode, 0)
        self.stop_session()
        ctx = self.f.ctx
        for name in ("control-requests", "results"):
            (ctx.protected / name).mkdir(exist_ok=True)
        (ctx.protected / "control-requests/request.json").write_text(json.dumps({"operation_id": "operation-123"}))
        (ctx.protected / "results/operation-123.json").write_text(json.dumps({
            "schema": host.broker.RESULT_SCHEMA, "operation_id": "operation-123", "status": "error", "finished_at": host.records.now()}))
        queue = self.f.root / ".dev/forge-broker/requests"
        queue.mkdir(parents=True, exist_ok=True)
        (queue / "old.request.json.claimed.123.done").write_text("{}")
        (self.f.root / ".dev/forge-tmp/callbacks/archive").mkdir(parents=True)
        pending = queue / "new.request.json"
        pending.write_text("{}")
        self.assertIn("HANDOFF_REQUIRED", self.start().stderr)
        pending.unlink()
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_attach_failure_cannot_rewrite_ready_or_clean_workers(self):
        result = self.f.run("start", "--root", str(self.f.root), "--session", "fixture", "--provider", "codex", "--attach")
        self.assertNotEqual(result.returncode, 0)  # no tmux client is attached
        self.assertEqual(self.f.ctx.store.current()["record"]["state"], "ready")
        self.assertEqual(len(self.panes().splitlines()), 5)

    def test_outside_attach_exec_observes_ready_before_control_transfer(self):
        f = self.f
        with patch.dict(os.environ, {"TMUX": f.env["TMUX"]}):
            rt = host.Runtime(f.ctx, f.candidate)
        args = SimpleNamespace(command="start", provider="codex", session="fixture", dry_run=False,
                               no_attach=False, attach=True)
        def transfer(binary, argv, env):
            self.assertIn("attach-session", argv)
            self.assertEqual(f.ctx.store.current()["record"]["state"], "ready")
            self.assertEqual(len(rt.pane_ids("fixture")), 5)
            raise OSError("fixture attach failure")
        with patch.dict(os.environ, {"TMUX": ""}), patch.object(host, "Runtime", return_value=rt), \
                patch.object(host, "ENTRY", Path(f.candidate["tools"]["host"])), patch.object(host.os, "execve", side_effect=transfer):
            with self.assertRaisesRegex(OSError, "fixture attach failure"):
                host.launch(f.ctx, args)
        self.assertEqual(f.ctx.store.current()["record"]["state"], "ready")

    def test_concurrent_creators_cannot_adopt_each_others_shell(self):
        argv = [str(ROOT / "bin/forge-orchestrator-host"), "start", "--root", str(self.f.root),
                "--session", "fixture", "--provider", "codex", "--no-attach"]
        children = [subprocess.Popen(argv, cwd=self.f.root, env=self.f.env, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [child.communicate(timeout=60) for child in children]
        self.assertIn(0, [child.returncode for child in children], results)
        self.assertEqual(self.f.ctx.store.current()["reference"]["generation"], 1)
        self.assertEqual(self.f.ctx.store.current()["record"]["state"], "ready")
        self.assertEqual(len(self.panes().splitlines()), 5)

    def custom_hook(self, prefix=""):
        f = self.f
        hook = f.package / "bin/custom-hook"
        hook.write_text("#!/bin/bash\nset -e\n" + prefix + "\nexec " +
                        __import__("shlex").quote(str(f.package / "bin/forge-start")) + " --populate-existing \"$@\"\n")
        hook.chmod(0o755)
        f.candidate["files"].append(host.file_identity(hook))
        f.candidate["files"].append(host.file_identity("/bin/sleep"))
        f.candidate["on_spawn"].update(kind="custom", argv=[str(hook)])
        config = f.root / ".claude/forge-project.yml"
        config.parent.mkdir()
        config.write_text(json.dumps({"forge": {"control_center": {"on_spawn": [str(hook)]}}}))
        f.candidate["project_config_sha256"] = host.file_identity(config)["sha256"]
        f.qualify_double()
        f.register(revision=1)
        return hook

    def test_failed_split_cleans_only_new_panes_and_keeps_owned_host(self):
        f = self.f
        with patch.dict(os.environ, {"TMUX": f.env["TMUX"]}):
            rt = host.Runtime(f.ctx, f.candidate)
            current = host.reserve_launch(f.ctx, rt, f.ctx.store.profile(), "fixture", "codex")
            seat, shell = rt.new_shell("fixture", "codex")
            handoff = {"shell": shell, "expected_hook": f.candidate["on_spawn"]["argv"],
                       "expected_populate": f.candidate["tools"]["host"], "created_at": host.records.now()}
            f.ctx.store.preparing(current["record"]["launch_id"], 1, 1, "shell-created", seat=seat, handoff=handoff)
            current = host.claim_handoff(f.ctx, rt, current["record"]["launch_id"], "fixture", "codex")
            original, calls = rt.tmux, []
            def failing(*args, **kwargs):
                calls.append(args)
                if args[0] == "split-window" and sum(call[0] == "split-window" for call in calls) == 3:
                    raise host.Refusal("SPLIT_FAILURE", "fixture")
                return original(*args, **kwargs)
            with patch.object(rt, "tmux", side_effect=failing), self.assertRaises(host.Refusal):
                host.populate_launch(f.ctx, rt, current)
            self.assertEqual(rt.pane_ids("fixture"), [seat["pane_id"]])
            self.assertTrue(host.same_process(shell))
            self.assertEqual(f.ctx.store.current()["record"]["state"], "failed")
            self.assertFalse(any(call[0] == "respawn-pane" for call in calls))

    def test_approved_custom_hook_and_changed_bytes_before_effects(self):
        hook = self.custom_hook()
        self.assertEqual(self.start(action="spawn").returncode, 0)
        self.stop_session()
        before = self.f.ctx.store.current()
        hook.write_text(hook.read_text() + "\n# drift\n")
        self.assertIn("INPUT_CHANGED", self.start(action="spawn").stderr)
        self.assertEqual(self.f.ctx.store.current(), before)

    def test_stolen_locator_and_dead_creator_leave_live_hook_for_recovery(self):
        release = self.f.base / "release"
        self.custom_hook("while [ ! -f " + str(release) + " ]; do /bin/sleep 0.02; done")
        child = subprocess.Popen([str(ROOT / "bin/forge-orchestrator-host"), "spawn", "--root", str(self.f.root),
                                  "--session", "fixture", "--provider", "codex"],
                                 cwd=self.f.root, env=self.f.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while True:
                current = self.f.ctx.store.current()
                if current and current["record"].get("construction") == "hook-started":
                    break
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.03)
            before = self.panes()
            launch_id = current["record"]["launch_id"]
            result = self.f.run("populate", "--root", str(self.f.root), "--session", "fixture", "--provider", "codex",
                                "--launch-id", launch_id,
                                env={"FORGE_ORCHESTRATOR_PROVIDER": "codex", "FORGE_ORCHESTRATOR_REQUEST_VERSION": "1"})
            self.assertIn("HANDOFF_PROVENANCE_INVALID", result.stderr)
            self.assertEqual(self.panes(), before)
            child.kill()
            child.wait(timeout=5)
            subprocess.run([self.f.tmux, "-L", self.f.socket, "kill-session", "-t", "fixture"], check=True)
            result = self.f.run("recover", "--root", str(self.f.root), "--launch-id", launch_id)
            self.assertIn("HANDOFF_REQUIRED", result.stderr)
            self.assertEqual(self.f.ctx.store.current(), current)
            release.touch()
            child.communicate(timeout=10)
            deadline = time.monotonic() + 5
            while host.alive(current["record"]["handoff"]["hook_process"]):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.03)
            result = self.f.run("recover", "--root", str(self.f.root), "--launch-id", launch_id)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.f.ctx.store.current()["record"]["state"], "failed")
            result = self.start(action="spawn")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(self.f.ctx.store.current()["record"]["launch_id"], launch_id)
        finally:
            release.touch()
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)


class LegacyEnsureTests(unittest.TestCase):
    def test_native_claude_and_receipts_are_required_without_writing_binding(self):
        f = Fixture(tmux=True)
        self.addCleanup(f.close)
        claude = f.package / "bin/claude"
        shutil.copy2(f.provider, claude)
        tmux = [f.tmux, "-L", f.socket]
        subprocess.run([*tmux, "new-session", "-d", "-s", "legacy", "-c", str(f.root), str(claude)], check=True)
        for _ in range(4):
            subprocess.run([*tmux, "split-window", "-t", "legacy", "-c", str(f.root), str(claude)], check=True)
            subprocess.run([*tmux, "select-layout", "-t", "legacy", "tiled"], check=True, capture_output=True)
        panes = subprocess.check_output([*tmux, "list-panes", "-t", "legacy", "-F", "#{pane_id}"], text=True).splitlines()
        for pane, alias in zip(panes, ("claude", "claude-opus", "claude-sonnet", "codex-a", "codex-b")):
            subprocess.run([*tmux, "set-option", "-p", "-t", pane, "@forge-worker", alias], check=True)
        env = {"PATH": str(claude.parent) + ":" + os.environ["PATH"]}
        args = ("legacy-ensure", "--root", str(f.root), "--session", "legacy", "--provider", "claude")
        result = f.run(*args, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["admission"], "legacy-observed")
        self.assertIsNone(f.ctx.store.current())
        subprocess.run([*tmux, "respawn-pane", "-k", "-t", panes[0], "/bin/bash", "--noprofile", "--norc"], check=True)
        result = f.run(*args, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(f.ctx.store.current())


if __name__ == "__main__":
    unittest.main()
