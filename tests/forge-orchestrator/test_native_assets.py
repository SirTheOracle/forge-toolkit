"""Unit coverage for trace interpretation, never native qualification evidence."""
import importlib.util
import copy
import json
import shlex
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

TREE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("native_assets", TREE / "lib/forge_native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeAssets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / "SKILL.md"
        self.path.write_text("# Protocol\n\nFirst contract.\nLast contract.\n")

    def event(self, **item):
        return {"method": "item/completed", "params": {"item": item}}

    def test_claude_manual_policy_requires_exact_native_permission_denial(self):
        argv = ["/protected/python", "-I", "/protected/probe.py"]
        call = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "id": "command-1",
                "input": {"command": shlex.join(argv)}}]}}
        result = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "command-1", "is_error": True, "content": "denied"}]}}
        final = {"type": "result", "subtype": "success", "is_error": False,
                 "permission_denials": [{"tool_name": "Bash", "tool_use_id": "command-1", "tool_input": {"command": shlex.join(argv)}}]}
        self.assertEqual(native.claude_denial_result([call, result, final], argv)["status"], "pass")
        for mutate in ("wrong-id", "success", "bypass", "extra-command", "claim-only", "ordinary-error", "failed-turn", "bad-denials", "wrong-denial-id"):
            events = copy.deepcopy([call, result, final])
            if mutate == "wrong-id":
                events[1]["message"]["content"][0]["tool_use_id"] = "other"
            elif mutate == "success":
                events[1]["message"]["content"][0]["is_error"] = False
            elif mutate == "bypass":
                events[0]["message"]["content"][0]["input"]["dangerouslyDisableSandbox"] = True
            elif mutate == "extra-command":
                events.append(copy.deepcopy(call))
            elif mutate == "ordinary-error":
                events[2]["permission_denials"] = []
            elif mutate == "failed-turn":
                events[2]["is_error"] = True
            elif mutate == "bad-denials":
                events[2]["permission_denials"] = "Bash denied"
            elif mutate == "wrong-denial-id":
                events[2]["permission_denials"][0]["tool_use_id"] = "other"
            else:
                events = [{"type": "assistant", "message": {"content": [{"type": "text", "text": '{"measured":true}'}]}}]
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                native.claude_denial_result(events, argv)

    def test_claude_configuration_requires_native_init_and_closed_policy(self):
        settings = self.path.parent / "settings.json"
        policy = {}
        settings.write_text(json.dumps(policy))
        receipt = {"provider_argv": ["/native/claude", "--safe-mode", "--permission-mode", "default",
                   "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--settings", str(settings), "prompt"], "environment": {},
                   "root": {"git_common_dir": "/protected/git", "git_dir": "/protected/git", "physical_root": "/fixture"}}
        with patch.object(native, "ClaudeStream") as stream:
            client = stream.return_value.__enter__.return_value
            event = {"type": "system", "subtype": "init", "permissionMode": "manual", "model": "fixture-model", "mcp_servers": [], "plugins": []}
            client.turn.return_value = [event]
            self.assertEqual(native.claude_configuration(receipt, self.path.parent)["status"], "pass")
            for events in ([], [event, event], [dict(event, permissionMode="bypassPermissions")],
                           [dict(event, mcp_servers=[{"name": "unexpected"}])]):
                client.turn.return_value = events
                self.assertEqual(native.claude_configuration(receipt, self.path.parent)["status"], "fail")
            policy["permissions"] = {"defaultMode": "bypassPermissions"}
            settings.write_text(json.dumps(policy))
            client.turn.return_value = [event]
            self.assertEqual(native.claude_configuration(receipt, self.path.parent)["status"], "fail")

    def test_codex_requires_complete_successful_tool_output(self):
        def read(output, **extra):
            return self.event(type="commandExecution", command="cat " + str(self.path),
                              aggregatedOutput=output, **extra)
        events = [read("# Protocol\nFirst contract.", exitCode=0)]
        self.assertFalse(native.codex_reads(events, [self.path])[str(self.path)])
        events.append(read("Last contract.", exitCode=1))
        self.assertFalse(native.codex_reads(events, [self.path])[str(self.path)])
        events.append(read("Last contract.", exitCode=0))
        self.assertTrue(native.codex_reads(events, [self.path])[str(self.path)])

    def test_claude_attention_policy_uses_explicit_settings_not_safe_mode(self):
        settings = self.path.parent / "settings.json"
        attention = self.path.parent / "attention.json"
        policy = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/protected/forge-cc-hook stop"}]}]}}
        for path in (settings, attention):
            path.write_text(json.dumps(policy))
        receipt = {"provider_argv": ["/native/claude", "--disable-slash-commands", "--permission-mode", "default",
                   "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--settings", str(settings), "prompt"], "attention_hooks": str(attention),
                   "environment": {key: "1" for key in ("CLAUDE_CODE_DISABLE_CLAUDE_MDS", "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
                                                         "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS")},
                   "root": {"git_common_dir": "/protected/git", "git_dir": "/protected/git", "physical_root": "/fixture"}}
        with patch.object(native, "ClaudeStream") as stream:
            client = stream.return_value.__enter__.return_value
            client.turn.return_value = [{"type": "system", "subtype": "init", "permissionMode": "default",
                                         "model": "fixture-model", "mcp_servers": [], "plugins": []}]
            self.assertEqual(native.claude_configuration(receipt, self.path.parent)["status"], "pass")
            receipt["provider_argv"].insert(1, "--safe-mode")
            self.assertEqual(native.claude_configuration(receipt, self.path.parent)["status"], "fail")
            receipt["provider_argv"].remove("--safe-mode")
            receipt["environment"].pop("CLAUDE_CODE_DISABLE_CLAUDE_MDS")
            self.assertEqual(native.claude_configuration(receipt, self.path.parent)["status"], "fail")

    def test_codex_model_claim_and_checksum_are_not_loads(self):
        events = [self.event(type="agentMessage", text=self.path.read_text()),
                  self.event(type="commandExecution", command="shasum " + str(self.path),
                             aggregatedOutput="a" * 64, exitCode=0)]
        self.assertFalse(native.codex_reads(events, [self.path])[str(self.path)])

    def test_claude_requires_matching_read_tool_result(self):
        call = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "id": "read-1", "input": {"file_path": str(self.path)}}]}}
        result = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "wrong", "content": self.path.read_text()}]}}
        self.assertFalse(native.claude_reads([call, result], [self.path])[str(self.path)])
        result["message"]["content"][0]["tool_use_id"] = "read-1"
        self.assertTrue(native.claude_reads([call, result], [self.path])[str(self.path)])
        result["message"]["content"][0]["is_error"] = True
        self.assertFalse(native.claude_reads([call, result], [self.path])[str(self.path)])

    def claude_read(self, path):
        return [{"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Read", "id": "read-1", "input": {"file_path": str(path)}}]}},
                {"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "read-1", "content": path.read_text()}]}}]

    def test_claude_stream_requires_success_and_stable_native_session(self):
        stream = native.ClaudeStream([], {}, "/", self.path)
        stream.handshake()
        stream.send = Mock()
        final = {"type": "result", "subtype": "success", "is_error": False, "session_id": "native-session"}
        stream.until = Mock(return_value=[final])
        self.assertEqual(stream.turn("Read test"), [final])
        self.assertEqual(stream.session, "native-session")
        self.assertEqual(stream.send.call_args.args[0]["message"]["content"], "Read test")
        for changed in (dict(final, is_error=True), dict(final, subtype="error_during_execution"),
                        dict(final, session_id=None), dict(final, session_id="different")):
            stream.until.return_value = [changed]
            with self.assertRaises((RuntimeError, ValueError)):
                stream.turn("Read test")

    def test_stream_real_transport_reuses_process_and_cleans_up(self):
        # An explicit protocol double, not native authentication/qualification.
        code = ('import sys,json\nfor line in sys.stdin:\n'
                ' value=json.loads(line)\n'
                ' print(json.dumps({"type":"result","subtype":"success","is_error":False,'
                '"session_id":"fixture-session"}),flush=True)\n')
        stream = native.ClaudeStream([sys.executable, "-I", "-S", "-B", "-c", code], {},
                                    str(self.path.parent), self.path.parent / "stream.jsonl", timeout=3)
        with stream as client:
            first = client.process.pid
            client.turn("one")
            client.turn("two")
            self.assertEqual(client.process.pid, first)
            self.assertIsNone(client.process.poll())
        self.assertIsNotNone(stream.process.poll())
        self.assertTrue(stream.log.closed)
        self.assertTrue(stream.stderr.closed)
        self.assertEqual(len(stream.transcript.read_text().splitlines()), 2)

    def test_transport_launch_failure_closes_opened_logs(self):
        stream = native.ClaudeStream(["/nonexistent/forge-probe"], {}, "/", self.path.parent / "failure.jsonl")
        with self.assertRaises(FileNotFoundError):
            with stream:
                self.fail("missing executable must not start")
        self.assertTrue(stream.log.closed)
        self.assertTrue(stream.stderr.closed)

    def test_transport_exclusive_log_failure_closes_stderr(self):
        stream = native.Rpc([], {}, "/", self.path)
        with self.assertRaises(FileExistsError):
            with stream:
                self.fail("existing log must not be replaced")
        self.assertTrue(stream.stderr.closed)
        self.assertIn("# Protocol", self.path.read_text())

    def test_claude_stream_launch_has_only_read_and_no_approval_bypass(self):
        receipt = {"provider_argv": ["/pinned/claude", "--safe-mode", "--permission-mode", "default", "prompt"]}
        argv = native.claude_stream_argv(receipt, session="native-id", persist=False)
        self.assertEqual(argv[argv.index("--tools") + 1], "Read")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read")
        self.assertEqual(argv[argv.index("--permission-prompts") + 1], "none")
        self.assertIn("--no-session-persistence", argv)
        self.assertIn("native-id", argv)
        self.assertNotIn("prompt", argv)
        self.assertFalse(any("bypass" in value or "dangerous" in value for value in argv))

    def test_legacy_snapshot_detects_bytes_and_same_byte_alias_swap(self):
        alias = self.path.parent / "legacy.md"
        alias.symlink_to(self.path)
        before = native.legacy_snapshot([alias])
        alternate = self.path.parent / "same.md"
        alternate.write_bytes(self.path.read_bytes())
        alias.unlink()
        alias.symlink_to(alternate)
        after = native.legacy_snapshot([alias])
        self.assertEqual(before[0]["sha256"], after[0]["sha256"])
        self.assertNotEqual(before, after)
        alternate.write_text("changed bytes")
        self.assertNotEqual(after, native.legacy_snapshot([alias]))

    def test_concurrent_rounds_require_fresh_reads_and_two_live_processes(self):
        legacy_file = self.path.parent / "legacy.md"
        legacy_file.write_text("Legacy only.\n")
        legacy, candidate = Mock(), Mock()
        legacy.process.pid, candidate.process.pid = 100, 200
        legacy.process.poll.return_value = candidate.process.poll.return_value = None
        legacy.turn.return_value = self.claude_read(legacy_file)
        read = Mock(return_value=self.claude_read(self.path))
        cases = native.coexistence_rounds(legacy, candidate, read, [legacy_file], [self.path], "claude")
        self.assertEqual([c["case"] for c in cases], ["candidate-fresh", "legacy-reread", "candidate-reinvoke", "legacy-final"])
        self.assertTrue(all(c["status"] == "pass" and c["concurrent"] for c in cases))
        self.assertEqual(legacy.turn.call_count, 2)
        self.assertEqual(read.call_count, 2)
        legacy.process.poll.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "both original"):
            native.coexistence_rounds(legacy, candidate, read, [legacy_file], [self.path], "claude")
        legacy.process.poll.return_value = None
        read.return_value = []
        cases = native.coexistence_rounds(legacy, candidate, read, [legacy_file], [self.path], "claude")
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["status"], "fail")

    def test_coexistence_rejects_cross_read_and_parent_claim(self):
        other = self.path.parent / "other.md"
        other.write_text("Other protocol")
        reads = self.claude_read(self.path)
        self.assertEqual(native.read_separation(reads, [self.path], [other], "claude")["status"], "pass")
        crossed = copy.deepcopy(self.claude_read(other))
        for event in crossed:
            for item in event["message"]["content"]:
                item["id" if item["type"] == "tool_use" else "tool_use_id"] = "other-read"
        self.assertEqual(native.read_separation(reads + crossed, [self.path], [other], "claude")["status"], "fail")
        claim = [{"type": "assistant", "message": {"content": [{"type": "text", "text": self.path.read_text()}]}}]
        self.assertEqual(native.read_separation(claim, [self.path], [other], "claude")["status"], "fail")

    def claude_graph(self):
        expected = {"A": {"own": "A_UNIQUE", "other": "UNKNOWN", "value": 11},
                    "B": {"own": "B_UNIQUE", "other": "UNKNOWN", "value": 17}, "C": {"total": 28}}
        events = []
        for label, generation in (("A", 0), ("B", 0), ("A", 1), ("B", 1), ("C", 0)):
            task, tool = label.lower(), label + str(generation)
            events.extend([
                {"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Agent" if generation == 0 else "SendMessage", "id": tool,
                     "input": {"to": task, "prompt": "bounded fixture"}}]}},
                {"type": "system", "subtype": "task_started", "task_id": task, "tool_use_id": tool,
                 "spawn_depth": 1, "prompt": "bounded fixture"},
                {"type": "assistant", "parent_tool_use_id": tool, "message": {"content": [
                    {"type": "text", "text": json.dumps(expected[label])}]}},
                {"type": "system", "subtype": "task_notification", "task_id": task, "tool_use_id": tool,
                 "status": "completed"},
            ])
        return events, expected

    def test_claude_children_require_child_results_and_native_generations(self):
        events, expected = self.claude_graph()
        result = native.claude_child_graph(events, expected, followups=True)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["children"], {"A": "a", "B": "b", "C": "c"})
        self.assertFalse(result["plaintext_isolation_attested"])
        self.assertEqual(native.claude_child_graph([], expected, followups=True)["status"], "fail")
        cases = []
        for index, key, value in ((2, "parent_tool_use_id", None), (3, "status", "running"),
                                  (9, "task_id", "unknown"), (9, "prompt", "B_UNIQUE"),
                                  (9, "tool_use_id", "A0"), (13, "spawn_depth", 2)):
            changed = copy.deepcopy(events)
            changed[index][key] = value
            cases.append(changed)
        cases.extend([events[:-1], events + [events[-1]], events[:8] + events[16:] + events[8:16]])
        no_followup = copy.deepcopy(events)
        no_followup[8]["message"]["content"][0]["name"] = "Agent"
        cases.append(no_followup)
        for changed in cases:
            with self.subTest(events=changed):
                self.assertEqual(native.claude_child_graph(changed, expected, followups=True)["status"], "fail")

    def test_claude_digest_read_evidence_cannot_come_from_parent_or_other_child(self):
        events = self.claude_read(self.path)
        self.assertFalse(native.claude_reads(events, [self.path], parent_tool_use_id="child")[str(self.path)])
        for event in events:
            event["parent_tool_use_id"] = "child"
        self.assertFalse(native.claude_reads(events, [self.path])[str(self.path)])
        self.assertTrue(native.claude_reads(events, [self.path], parent_tool_use_id="child")[str(self.path)])
        self.assertFalse(native.claude_reads(events, [self.path], parent_tool_use_id="other")[str(self.path)])
        self.assertEqual(native.claude_blocks({"message": {"content": "a replayed user prompt"}}), [])

    def test_codex_rpc_preserves_policy_but_cwd_is_not_a_config_key(self):
        receipt = {"provider_argv": ["/bin/codex", "-C", "/a/.worktree", "--strict-config",
                   "--sandbox", "workspace-write", "--ask-for-approval", "on-request", "-c",
                   'projects={"/a/.worktree"={trust_level="untrusted"}}', "--disable", "hooks",
                   "--enable", "skip_host_skill_discovery", "-m", "test-model", "prompt"]}
        argv = native.codex_argv(receipt)
        self.assertEqual(argv[:2], ["/bin/codex", "app-server"])
        self.assertIn('sandbox_mode="workspace-write"', argv)
        self.assertIn('approval_policy="on-request"', argv)
        self.assertFalse(any(arg.startswith("cwd=") for arg in argv))
        self.assertIn(receipt["provider_argv"][10], argv)
        receipt["provider_argv"].insert(-1, "--unknown-policy")
        with self.assertRaises(ValueError):
            native.codex_argv(receipt)

    def test_policy_evidence_requires_exact_native_command_and_real_exit(self):
        argv = ["/pinned/python", "-I", "-S", "-B", "/protected/probe with spaces.py", "nonce"]
        command = shlex.join(argv)
        self.assertTrue(native.exact_command(command, argv))
        self.assertTrue(native.exact_command("/bin/zsh -lc " + shlex.quote(command), argv))
        for changed in (command + " ; true", "echo " + command, command.replace("-I", "-i"),
                        "/bin/zsh -lc " + shlex.quote(command + " || true"), "'"):
            self.assertFalse(native.exact_command(changed, argv))
        event = self.event(type="commandExecution", command=command, status="completed", exitCode=7,
                           aggregatedOutput='{"opened":["forbidden"]}')
        result = native.command_result([event], argv)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, event["params"]["item"]["aggregatedOutput"])
        for events in ([event, event], [self.event(type="agentMessage", text="passed")], []):
            with self.assertRaises(ValueError):
                native.command_result(events, argv)
        event["params"]["item"]["status"] = "declined"
        with self.assertRaises(ValueError):
            native.command_result([event], argv)

    def graph(self):
        expected = {"A": {"own": "A", "other": "UNKNOWN", "value": 11},
                    "B": {"own": "B", "other": "UNKNOWN", "value": 17}, "C": {"total": 28}}
        def call(tool, *threads):
            return self.event(type="collabAgentToolCall", tool=tool, status="completed",
                              receiverThreadIds=list(threads),
                              agentsStates={thread: {"status": "completed"} for thread in threads})
        events = [call("spawnAgent", "a"), call("spawnAgent", "b"), call("wait", "a", "b"),
                  call("followupTask", "a"), call("sendInput", "b"), call("wait", "a", "b"),
                  call("spawnAgent", "c"), call("wait", "c")]
        transcripts = {label.lower(): [{"type": "agentMessage", "text": json.dumps(value)}]
                       * (1 if label == "C" else 2) for label, value in expected.items()}
        return events, transcripts, expected

    def test_children_require_native_graph_and_child_owned_final_results(self):
        events, transcripts, expected = self.graph()
        result = native.child_graph(events, transcripts, expected, followups=True)
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["plaintext_isolation_attested"])
        parent_claim = [self.event(type="agentMessage", text=json.dumps(expected))]
        self.assertEqual(native.child_graph(parent_claim, transcripts, expected)["status"], "fail")
        transcripts["a"].append({"type": "agentMessage", "text": '{"own":"wrong"}'})
        self.assertEqual(native.child_graph(events, transcripts, expected, followups=True)["status"], "fail")

    def test_children_reject_missing_followup_timed_out_wait_and_early_synthesis(self):
        events, transcripts, expected = self.graph()
        cases = []
        cases.append(events[:3] + events[4:])
        timed_out = copy.deepcopy(events)
        timed_out[5]["params"]["item"]["agentsStates"]["a"]["status"] = "running"
        cases.append(timed_out)
        cases.append(events[:5] + [events[6], events[5], events[7]])
        cases.append([events[2], *events[:2], *events[3:]])
        cases.append(events[:5] + events[6:])
        for invalid in cases:
            with self.subTest(events=invalid):
                self.assertEqual(native.child_graph(invalid, transcripts, expected, followups=True)["status"], "fail")

    def test_children_reject_duplicate_children_and_cross_contamination(self):
        events, transcripts, expected = self.graph()
        duplicate = copy.deepcopy(events)
        duplicate[1]["params"]["item"]["receiverThreadIds"] = ["a"]
        self.assertEqual(native.child_graph(duplicate, transcripts, expected, followups=True)["status"], "fail")
        transcripts["a"][-1] = {"type": "agentMessage", "text": json.dumps(dict(expected["A"], other="B"))}
        self.assertEqual(native.child_graph(events, transcripts, expected, followups=True)["status"], "fail")

    def test_native_events_are_scoped_to_the_parent_thread(self):
        parent, child = self.event(type="agentMessage", text="parent"), self.event(type="agentMessage", text="child")
        parent["params"]["threadId"], child["params"]["threadId"] = "p", "c"
        self.assertEqual(native.thread_events([child, parent, self.event(type="agentMessage")], "p"), [parent])

    def test_only_completed_child_turns_count_as_followup_answers(self):
        commentary = {"type": "agentMessage", "phase": "commentary", "text": "working"}
        final = {"type": "agentMessage", "phase": "final_answer", "text": "{}"}
        thread = {"turns": [{"status": "completed", "items": [commentary, final, final]}]}
        self.assertEqual(native.child_turn_items(thread), [final])
        thread["turns"][0]["status"] = "inProgress"
        with self.assertRaises(ValueError):
            native.child_turn_items(thread)
        with self.assertRaises(ValueError):
            native.child_turn_items({"turns": []})

    def test_digest_parent_gets_only_path_not_artifact_contents(self):
        directory = Path(self.temp.name)
        prompt, expected, artifact = native.child_packet(directory, {"definition": {"provider": "codex"}},
                                                       directory, "digest")
        self.assertNotIn(str(artifact), prompt)
        self.assertNotIn(expected["digest"]["result"], prompt)
        self.assertIn(json.dumps("Follow " + str(directory / "digest-prompt.txt")), prompt)
        self.assertIn(str(artifact), (directory / "digest-prompt.txt").read_text())
        self.assertEqual(json.loads(artifact.read_text()), expected["digest"])

    def test_duplicate_assets_clean_only_owned_inputs_after_failure(self):
        root = Path(self.temp.name).resolve()
        paths = [root / "global/skills/forge-orchestrator/SKILL.md", root / "local/skills/forge-orchestrator/SKILL.md"]
        with self.assertRaises(RuntimeError):
            with native.duplicate_assets(paths, "test-marker"):
                self.assertTrue(all(path.exists() for path in paths))
                raise RuntimeError("interrupted")
        self.assertFalse((root / "global").exists())
        self.assertFalse((root / "local").exists())

    def test_duplicate_assets_refuse_existing_file_and_discovery_alias(self):
        root = Path(self.temp.name).resolve()
        fresh = root / "fresh/skill.md"
        with self.assertRaises(FileExistsError):
            with native.duplicate_assets([fresh, self.path], "test-marker"):
                self.fail("existing file should refuse")
        self.assertFalse(fresh.exists())
        self.assertIn("# Protocol", self.path.read_text())
        alias = root / "alias"
        alias.symlink_to(root, target_is_directory=True)
        with self.assertRaises(OSError):
            with native.duplicate_assets([alias / "new-skill.md"], "test-marker"):
                self.fail("symlink should refuse")
        self.assertFalse((root / "new-skill.md").exists())

    def test_duplicate_assets_preserve_concurrent_replacement(self):
        path = Path(self.temp.name).resolve() / "owned/SKILL.md"
        with self.assertRaisesRegex(ValueError, "preserving replacements"):
            with native.duplicate_assets([path], "test-marker"):
                path.write_text("concurrent edit")
        self.assertEqual(path.read_text(), "concurrent edit")

    def test_duplicate_creation_is_anchored_across_parent_symlink_swap(self):
        root = Path(self.temp.name).resolve()
        outside = root / "outside"
        outside.mkdir()
        path = root / "global/skills/forge-orchestrator/SKILL.md"
        original, swapped = native.os.open, False
        def swap(name, flags, *args, **kwargs):
            nonlocal swapped
            if name == "forge-orchestrator" and not swapped:
                swapped = True
                (root / "global/skills").rename(root / "moved")
                (root / "global/skills").symlink_to(outside, target_is_directory=True)
            return original(name, flags, *args, **kwargs)
        with patch.object(native.os, "open", side_effect=swap):
            with native.duplicate_assets([path], "test-marker"):
                self.assertEqual(list(outside.iterdir()), [])
                self.assertTrue((root / "moved/forge-orchestrator/SKILL.md").is_file())
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue((root / "global/skills").is_symlink())

    def test_duplicate_probe_requires_candidate_reads_without_decoy_contamination(self):
        decoy = Path(self.temp.name) / "decoy.md"
        events = [self.event(type="commandExecution", command="cat " + str(self.path), exitCode=0,
                             aggregatedOutput=self.path.read_text())]
        self.assertEqual(native.duplicate_result(events, [self.path], [decoy], "UNIQUE_DECOY", "codex")["status"], "pass")
        contaminated = [*events, self.event(type="agentMessage", text="UNIQUE_DECOY")]
        self.assertEqual(native.duplicate_result(contaminated, [self.path], [decoy], "UNIQUE_DECOY", "codex")["status"], "fail")
        read_decoy = [*events, self.event(type="commandExecution", command="cat " + str(decoy), exitCode=0)]
        self.assertEqual(native.duplicate_result(read_decoy, [self.path], [decoy], "UNIQUE_DECOY", "codex")["status"], "fail")
        self.assertEqual(native.duplicate_result([], [self.path], [decoy], "UNIQUE_DECOY", "codex")["status"], "fail")


if __name__ == "__main__":
    unittest.main()
