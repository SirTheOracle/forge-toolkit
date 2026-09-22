"""P4 local observation and event-correlation tests; no model calls."""
import json
import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml
from host_fixture import ROOT, host


class HostObservation(unittest.TestCase):
    def test_validated_binding_supplies_provider_and_exact_pane(self):
        for provider in ("claude", "codex"):
            current = {"record": {"seat": {"pane_id": "%7", "incarnation": "123"}}}
            ctx = SimpleNamespace(store=SimpleNamespace(current=Mock(return_value=current)))
            with patch.object(host, "admission_guard", return_value={"provider": provider, "launch_id": "launch"}):
                result = host.observe_host(ctx, "forge-x", "%7")
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["observation"], "verified")
                self.assertEqual(result["incarnation"], "123")
                self.assertEqual(host.observe_host(ctx, "forge-x", "%8")["provider"], "unknown")

    def test_missing_binding_requires_corroborated_legacy_process(self):
        ctx = SimpleNamespace(store=SimpleNamespace(current=Mock(return_value=None)))
        with patch.object(host, "legacy_ensure", return_value={"provider": "claude", "pane_id": "%0"}) as legacy:
            self.assertEqual(host.observe_host(ctx, "forge-x")["provider"], "claude")
            legacy.assert_called_once_with(ctx, "forge-x", None)
        with patch.object(host, "legacy_ensure", side_effect=host.Refusal("MISSING", "not observed")):
            self.assertEqual(host.observe_host(ctx, "forge-x")["provider"], "unknown")

    def test_invalid_or_racing_binding_never_falls_back_to_claude(self):
        current = {"record": {"seat": {"pane_id": "%7", "incarnation": "123"}}}
        ctx = SimpleNamespace(store=SimpleNamespace(current=Mock(return_value=current)))
        with patch.object(host, "admission_guard", side_effect=host.Refusal("DRIFT", "changed")), \
                patch.object(host, "legacy_ensure") as legacy:
            result = host.observe_host(ctx, "forge-x")
            self.assertEqual(result["provider"], "unknown")
            self.assertEqual(result["reason"], "DRIFT")
            legacy.assert_not_called()
        ctx.store.current.side_effect = [current, None]
        with patch.object(host, "admission_guard", return_value={"provider": "codex"}):
            self.assertEqual(host.observe_host(ctx, "forge-x")["reason"], "REVISION_CONFLICT")

    def test_process_probe_timeout_is_unknown(self):
        ctx = SimpleNamespace(store=SimpleNamespace(current=Mock(return_value=None)))
        with patch.object(host, "legacy_ensure", side_effect=subprocess.TimeoutExpired("tmux", 5)):
            result = host.observe_host(ctx, "forge-x")
            self.assertEqual(result["provider"], "unknown")
            self.assertEqual(result["reason"], "OBSERVATION_UNAVAILABLE")


class Attention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p4-", dir=ROOT / ".dev")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".dev").mkdir()
        (self.root / "bin").mkdir()
        self.hook = self.root / "bin/forge-cc-hook"
        shutil.copy2(ROOT / "bin/forge-cc-hook", self.hook)
        (self.root / "bin/forge-orchestrator-host").write_text(
            "import pathlib\nprint(pathlib.Path(__file__).with_name('binding.json').read_text())\n")
        self.binding("codex")

    def binding(self, provider):
        (self.root / "bin/binding.json").write_text(json.dumps({
            "provider": provider, "observation": "verified" if provider != "unknown" else "unknown",
            "launch_id": "launch", "incarnation": "123"}))

    def run_hook(self, event, payload, provider="codex", pane="0", **overrides):
        env = dict(os.environ, FORGE_CC_PANE_META=f"{pane}\tforge-x\t{self.root}\t5",
                   FORGE_WATCH_TRIGGER="0", FORGE_ROLE="", TMUX_PANE="%7")
        env.update(overrides)
        argv = [sys.executable, str(self.hook)] + (["--codex"] if provider == "codex" else []) + [event]
        result = subprocess.run(argv, env=env, input=json.dumps(payload), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        return result

    def record(self, name):
        return json.loads((self.root / ".dev/attention" / name).read_text())

    def test_provider_and_native_turn_correlation_for_both_hosts(self):
        for provider in ("codex", "claude"):
            self.binding(provider)
            self.run_hook("userpromptsubmit", {"prompt": "hello", "session_id": "native", "turn_id": "turn1"}, provider)
            self.run_hook("stop", {"last_assistant_message": "done", "session_id": "native", "turn_id": "turn1"}, provider)
            result = self.record("stop.forge-x.json")
            self.assertEqual(result["provider"], provider)
            self.assertEqual(result["native_turn_id"], "turn1")
            self.assertEqual(result["role"], "orchestrator")
            self.assertNotIn("agent", result)

    def test_worker_role_stamp_and_child_stop_cannot_complete_host(self):
        self.run_hook("stop", {"last_assistant_message": "worker done"}, pane="3", FORGE_ROLE="orchestrator")
        self.assertFalse((self.root / ".dev/attention/stop.forge-x.json").exists())
        self.assertEqual(len(list((self.root / ".dev/attention").glob("wstop.*"))), 1)
        self.run_hook("stop", {"hook_event_name": "SubagentStop", "last_assistant_message": "child"})
        self.run_hook("stop", {"parent_tool_use_id": "child", "last_assistant_message": "child"})
        self.assertFalse((self.root / ".dev/attention/stop.forge-x.json").exists())

    def test_mismatched_provider_or_turn_cannot_answer_host_dispatch(self):
        self.run_hook("userpromptsubmit", {"prompt": "hello", "turn_id": "current"})
        self.run_hook("stop", {"last_assistant_message": "stale", "turn_id": "previous"})
        self.assertFalse((self.root / ".dev/attention/stop.forge-x.json").exists())
        self.run_hook("stop", {"last_assistant_message": "wrong adapter"}, provider="claude")
        self.assertFalse((self.root / ".dev/attention/stop.forge-x.json").exists())

    def test_permission_stdout_empty_and_matching_tool_resolves(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": ["echo", "hello"]}}
        self.run_hook("permissionrequest", payload)
        paths = list((self.root / ".dev/attention").glob("perm.*"))
        self.assertEqual(len(paths), 1)
        self.assertEqual(json.loads(paths[0].read_text())["provider"], "codex")
        self.run_hook("posttooluse", payload)
        self.assertFalse(paths[0].exists())

    def test_unknown_binding_visible_and_nonobject_payload_inert(self):
        self.binding("unknown")
        self.run_hook("permissionrequest", {"tool_name": "Bash"})
        path = next((self.root / ".dev/attention").glob("perm.*"))
        self.assertEqual(json.loads(path.read_text())["provider"], "unknown")
        self.run_hook("stop", [])
        self.assertFalse((self.root / ".dev/attention/stop.forge-x.json").exists())

    def test_malformed_native_fields_are_inert(self):
        for event, payload in (("userpromptsubmit", {"prompt": []}),
                               ("stop", {"last_assistant_message": 42}),
                               ("stop", {"transcript_path": {}}),
                               ("permissionrequest", {"tool_name": []})):
            self.run_hook(event, payload)
        self.assertFalse((self.root / ".dev/attention").exists())

    def test_codex_and_claude_transcript_fallback(self):
        transcript = self.root / "transcript.jsonl"
        for provider, record in (
            ("codex", {"type": "response_item", "payload": {"role": "assistant", "content": [{"type": "output_text", "text": "Codex answer"}]}}),
            ("claude", {"type": "assistant", "message": {"content": [{"type": "text", "text": "Claude answer"}]}}),
        ):
            self.binding(provider)
            transcript.write_text("malformed\n[]\n" + json.dumps(record) + "\n")
            self.run_hook("stop", {"transcript_path": str(transcript)}, provider)
            self.assertEqual(self.record("stop.forge-x.json")["snippet"], provider.title() + " answer")


class BridgeObservation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p4-bridge-", dir=ROOT / ".dev")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / "capture.txt"
        source = (ROOT / "bin/forge-bridge").read_text()
        names = ("_observe_pane1", "_usage_parse_capture", "_usage_upsert", "_hygiene_worker_ok", "pane_index",
                 "cmd_health", "cmd_stall_check", "_worker_health", "canonical_pane_name", "pane_name")
        self.functions = "\n".join(re.search(r"^" + name + r"\(\).*?(?=^[A-Za-z_][A-Za-z0-9_]*\(\)|\Z)",
                                              source, re.M | re.S)[0] for name in names)

    def sample(self, provider, text):
        self.capture.write_text(text)
        script = self.functions + "\n" + '''
_orchestrator_provider() { printf '%s' "$TEST_PROVIDER"; }
_orchestrator_alert_headroom() { echo 30; }
_usage_file() { echo usage.yml; }
timestamp() { echo 2026-09-21T12:00:00Z; }
_emit_event() { printf '%s\\n' "$*" >> "$TEST_ROOT/events"; }
ID_target_session=forge-x
ID_target_incarnation=123
_observe_pane1 "$TEST_ROOT"
'''
        cp = subprocess.run(["bash", "-c", script], env=dict(os.environ, TEST_ROOT=str(self.root),
                            TEST_PROVIDER=provider, FORGE_PANE1_FIXTURE=str(self.capture)), capture_output=True, text=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        return yaml.safe_load((self.root / "usage.yml").read_text())["orchestrator"]

    def test_usage_parser_selected_by_provider_and_unknown_invalidates_prior(self):
        claude = self.sample("claude", "  [Opus 5] ctx: 80k (80%)")
        self.assertEqual((claude["provider"], claude["headroom"]), ("claude", 20))
        codex = self.sample("codex", "Context 15% left")
        self.assertEqual((codex["provider"], codex["headroom"]), ("codex", 15))
        unknown = self.sample("unknown", "Context 15% left")
        self.assertEqual((unknown["provider"], unknown["headroom"]), ("unknown", "unknown"))

    def test_midturn_parse_miss_retains_same_provider_reading(self):
        before = self.sample("codex", "Context 20% left")
        after = self.sample("codex", "working")
        self.assertEqual(before, after)

    def test_every_host_alias_excluded_from_worker_hygiene(self):
        for alias in ("claude", "orchestrator", "claude-orchestrator"):
            cp = subprocess.run(["bash", "-c", self.functions + '\n_hygiene_worker_ok "$1"', "test", alias],
                                text=True, capture_output=True)
            self.assertNotEqual(cp.returncode, 0)

    def test_health_and_stall_reuse_provider_parser_without_worker_actions(self):
        cases = (("codex", "  gpt-6-astra high fast · workspace", "idle", "IDLE"),
                 ("codex", "\u2022 Working (3s)", "busy", "ACTIVE"),
                 ("codex", "Would you like to run the following command?\n  1. Yes, run", "prompting", "PROMPTING"),
                 ("claude", "  [Opus 5] ctx: 80k (80%)", "idle", "IDLE"),
                 ("claude", "\u273b Working\u2026 (3s", "busy", "ACTIVE"),
                 ("claude", " \u276f 1. Yes", "prompting", "PROMPTING"),
                 ("unknown", "  [Opus 5] ctx: 80k (80%)", "unknown", "UNKNOWN"),
                 ("codex", "malformed output", "unknown", "UNKNOWN"))
        for provider, text, health, stall in cases:
            with self.subTest(provider=provider, health=health):
                self.capture.write_text(text)
                script = self.functions + "\n" + '''
_orchestrator_provider() { printf '%s' "$TEST_PROVIDER"; }
require_identity() { :; }
_print_identity_block() { :; }
_resolve_project_root() { echo "$TEST_ROOT"; }
tmux() {
    case "$1" in
        list-panes) printf '0\\n1\\n2\\n3\\n4\\n' ;;
        capture-pane) cat "$FORGE_HEALTH_FIXTURE" ;;
        *) echo "UNEXPECTED TMUX $*" >&2; return 1 ;;
    esac
}
ID_target_root="$TEST_ROOT"
SESSION=forge-x
WINDOW=0
ORCH_IDX=0
FORGE_CACHE_DIR="$TEST_ROOT/cache"
cmd_health
cmd_stall_check orchestrator
'''
                cp = subprocess.run(["bash", "-c", script], env=dict(os.environ, TEST_ROOT=str(self.root),
                                    TEST_PROVIDER=provider, FORGE_HEALTH_FIXTURE=str(self.capture),
                                    FORGE_IDLE_PROMPTS_FILE=str(ROOT / "config/idle-prompts.yml")),
                                    capture_output=True, text=True)
                self.assertNotIn("UNEXPECTED TMUX", cp.stderr)
                if health == "unknown":
                    self.assertIn("UNKNOWN pane=claude idx=0", cp.stdout)
                else:
                    self.assertIn(f"kind={provider} provider={provider} state={health}", cp.stdout, cp.stderr)
                self.assertIn(f"{stall} pane=claude provider={provider}", cp.stdout)
                self.assertNotIn("COMPLETED-PENDING", cp.stdout)


class BoardObservation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p4-board-", dir=ROOT / ".dev")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.att = self.root / ".dev/attention"
        self.att.mkdir(parents=True)
        config = self.root / "config"
        config.mkdir()
        (config / "watch-roots").write_text(str(self.root) + "\n")
        sessions = self.root / "sessions.tsv"
        sessions.write_text(f"forge-x\t{self.root}\n")
        panes = self.root / "panes.tsv"
        panes.write_text("")
        self.env = dict(os.environ, FORGE_WATCH_CONFIG_DIR=str(config), FORGE_WATCH_CACHE_DIR=str(self.root / "cache"),
                        FORGE_WATCH_TMUX_LIST=str(sessions), FORGE_WATCH_TMUX_PANES=str(panes),
                        FORGE_WATCH_SINK_CAPTURE=str(self.root / "notifications"), FORGE_WATCH_TRIGGER="0")
        self.now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def board(self):
        cp = subprocess.run([str(ROOT / "bin/forge-watch"), "check"], env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        cp = subprocess.run([str(ROOT / "bin/forge-watch"), "status", "--board"], env=self.env,
                            capture_output=True, text=True, timeout=20)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        return json.loads(cp.stdout)

    def test_host_provider_propagates_to_tasks_context_and_swiftbar(self):
        for provider in ("claude", "codex", None):
            row = {"session": "forge-x", "root": str(self.root), "provider": provider}
            (self.att / "dispatch-test.json").write_text(json.dumps(dict(row, schema="cc-dispatch/1", event="dispatch",
                dispatch_id="test", dispatched_at=self.now, target_pane=0, instruction_snippet="work")))
            (self.att / "prompt.forge-x.json").write_text(json.dumps(dict(row, schema="cc-attention/1", event="userpromptsubmit",
                role="orchestrator", emitted_at=self.now, dispatch_id="test")))
            (self.root / ".dev/forge-usage.forge-x.yml").write_text(yaml.safe_dump({"orchestrator": {
                "provider": provider, "headroom": 15, "confidence": "high", "measured_at": self.now}}))
            board = self.board()
            expected = provider or "unknown"
            self.assertEqual(board["tasks"][0]["provider"], expected)
            self.assertEqual(board["context"][0]["provider"], expected)
            self.assertEqual(board["context"][0]["role"], "orchestrator")
            working = next(row for row in board["active"] if row["condition"] == "SESSION-WORKING")
            self.assertEqual(working["provider"], expected)
            source = (ROOT / "swiftbar/forge-board.5s.sh").read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
            rendered = subprocess.run([sys.executable, "-c", source], env=dict(os.environ, FW_JSON=json.dumps(board)),
                                      text=True, capture_output=True)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertIn(expected, rendered.stdout)
            self.assertIn("handoff, never a reset", rendered.stdout)
            self.assertNotIn("reset-idle", rendered.stdout)

    def test_dispatch_does_not_prove_hooks_emit_and_worker_stop_cannot_close_host(self):
        (self.att / "codex-register.json").write_text("{}")
        row = {"schema": "cc-dispatch/1", "event": "dispatch", "session": "forge-x", "root": str(self.root),
               "provider": "codex", "dispatch_id": "test", "dispatched_at": self.now, "target_pane": 0}
        (self.att / "dispatch-test.json").write_text(json.dumps(row))
        (self.att / "stop.forge-x.json").write_text(json.dumps(dict(row, schema="cc-attention/1", event="stop",
                                                                 role="worker", emitted_at=self.now)))
        board = self.board()
        self.assertEqual(board["tasks"][0]["state"], "queued")
        self.assertFalse(any(r["condition"] == "SESSION-DONE" for r in board["active"]))
        status = subprocess.run([str(ROOT / "bin/forge-watch"), "status"], env=self.env, capture_output=True, text=True)
        self.assertIn("CODEX-EMISSION-OFF", status.stdout)


if __name__ == "__main__":
    unittest.main()
