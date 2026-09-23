"""Production assets, route contracts, render recovery and installer ownership."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

TREE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("assets", TREE / "lib/forge_assets.py")
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class Assets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_inventory_and_every_supported_route(self):
        self.assertEqual(assets.inventory(), [])
        for stage, route in assets.manifest()["stages"].items():
            for provider in route["providers"]:
                root = TREE / ("skills" if provider == "claude" else "codex-skills")
                selected = assets.route(stage, provider, skill_root=root)
                if route["skill"]:
                    self.assertEqual(selected, str(root / route["skill"] / "SKILL.md"))

    def test_unsupported_worker_never_borrows_other_provider_skill(self):
        for stage in ("fix-qa", "fix-plan", "fix-reproduce", "fix-investigate"):
            with self.assertRaisesRegex(ValueError, "unsupported worker route"):
                assets.route(stage, "codex-a", skill_root=TREE / "skills")

    def test_missing_skill_is_not_hidden_by_missing_install(self):
        for name in ("config", "skills", "codex-skills", "prompts", "digests", "lib", "agents", "commands"):
            shutil.copytree(TREE / name, self.root / name)
        shutil.rmtree(self.root / "codex-skills/adversarial-qa")
        errors = assets.inventory(self.root)
        self.assertTrue(any("codex-skills/adversarial-qa/SKILL.md" in e for e in errors), errors)

    def test_missing_digest_shadow_escape_nested_and_link(self):
        prompts, digests = self.root / "prompts", self.root / "digests"
        shutil.copytree(TREE / "prompts", prompts)
        shutil.copytree(TREE / "digests", digests)
        (digests / "coding.txt").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            assets.route("coding", "claude-sonnet", prompts=prompts, digests=digests)
        sample = prompts / "sample.txt"
        for contents in ("<<<INCLUDE ../secret>>>", "<<<INCLUDE child>>>"):
            sample.write_text(contents)
            (prompts / "child.txt").write_text("<<<INCLUDE _preamble>>>")
            with self.assertRaises(ValueError):
                assets.template(sample, prompts, assets.manifest())
        sample.write_text("<<<INCLUDE _preamble>>>")
        (prompts / "_preamble.txt").write_text("shadow")
        with self.assertRaisesRegex(ValueError, "shadowed"):
            assets.template(sample, prompts, assets.manifest())
        sample.unlink()
        sample.symlink_to(TREE / "prompts/coding.txt")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            assets.template(sample, prompts, assets.manifest())

    def shell(self, commands):
        names = ("_render_preamble", "_render_operator_constraints", "_render_template", "_stage_assets", "_snapshot_qa_findings",
                 "_publish_render", "cmd_local_render", "_wait_emit_callback", "cmd_digest")
        source = (TREE / "bin/forge-bridge").read_text()
        functions = []
        for name in names:
            functions.append(re.search(r"^" + name + r"\(\).*?^\}", source, re.M | re.S)[0])
        env = {**os.environ, "ROOT": str(self.root), "BIN": str(TREE / "bin"),
               "FORGE_PROMPTS_DIR": str(TREE / "prompts"), "FORGE_DIGESTS_DIR": str(TREE / "digests"),
               "FORGE_CLAUDE_SKILLS_DIR": str(TREE / "skills"), "FORGE_CODEX_SKILLS_DIR": str(TREE / "codex-skills")}
        script = '\n'.join(functions) + '''
_forge_sibling_bin() { printf '%s/forge' "$BIN"; }
_resolve_project_root() { printf '%s' "$ROOT"; }
canonical_pane_name() { printf '%s' "$1"; }
pane_index() { printf '1'; }
_emit_event() { :; }
''' + commands
        return subprocess.run(["/bin/bash", "-c", script], env=env, text=True, capture_output=True)

    def test_local_proposal_constraints_before_pending(self):
        result = self.shell('cmd_local_render --slug sample --provider codex')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / ".dev/forge-tmp").exists())
        directory = self.root / ".dev/proposals/sample"
        directory.mkdir(parents=True)
        (directory / "constraints.yml").write_text('schema: forge-constraints/1\nconstraints: []\nnone_recorded_reason: Test canary\n')
        result = self.shell('cmd_local_render --slug sample --provider codex')
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = (self.root / ".dev/forge-tmp/local-proposal-sample.txt").read_text()
        self.assertIn("Test canary", rendered)
        self.assertIn("BOTH isolated", rendered)
        self.assertIn("REJECT: <clause>", rendered)
        self.assertIn(str(TREE / "codex-skills/adversarial-proposal/SKILL.md"), rendered)
        self.assertNotIn("<<<INCLUDE", rendered)
        self.assertFalse((directory / "forge-log.yml").exists())
        bridge = (TREE / "bin/forge-bridge").read_text()
        log = bridge[bridge.index("cmd_log() {"):bridge.index("cmd_log_response() {")]
        self.assertLess(log.index("cmd_local_render"), log.index('ensure_pipeline_log "$slug"'))

    def test_digest_failure_retains_done_and_successful_bytes(self):
        callback = self.root / "callback.yml"
        callback.write_text("status: DONE\nmessage: finished\ncallback_id: original\nselected_pending_timestamp: original-time\n")
        args = '"$ROOT/callback.yml" sample coding claude-sonnet coding "$ROOT"'
        first = self.shell('_wait_emit_callback ' + args)
        self.assertEqual(first.returncode, 0, first.stderr)
        out = self.root / ".dev/forge-tmp/digest-coding-sample.txt"
        before = out.read_bytes()
        failed = self.shell('FORGE_DIGESTS_DIR="$ROOT/missing"; _wait_emit_callback ' + args)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("STATUS: DONE", failed.stdout)
        self.assertIn("CALLBACK_ID: original", failed.stdout)
        self.assertIn("DIGEST_PROMPT_ERROR:", failed.stdout)
        self.assertNotIn("DIGEST_PROMPT:", failed.stdout)
        self.assertEqual(out.read_bytes(), before)
        bad = self.root / "bad"
        bad.mkdir()
        (bad / "coding.txt").write_text("<<<INCLUDE nonexistent>>>")
        failed = self.shell('FORGE_DIGESTS_DIR="$ROOT/bad"; _wait_emit_callback ' + args)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("DIGEST_PROMPT_ERROR:", failed.stdout)
        self.assertEqual(out.read_bytes(), before)
        retry = self.shell('cmd_digest --slug sample --stage coding --template coding')
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertIn("DIGEST_PROMPT:", retry.stdout)
        self.assertTrue(callback.exists())

    def install(self, *args):
        return subprocess.run(["bash", str(TREE / "install.sh"), *args],
                              env={**os.environ, "HOME": str(self.root), "FORGE_CONFIG_DIR": str(self.root / ".config/forge")},
                              text=True, capture_output=True)

    def test_fresh_repeat_all_drift_and_owned_uninstall(self):
        (self.root / ".codex").mkdir()
        for args in ((), (), ("--check-drift",)):
            result = self.install(*args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        claude = self.root / ".claude/skills/forge-coder/SKILL.md"
        codex = self.root / ".codex/skills/fix-coder/SKILL.md"
        digest = self.root / ".config/forge/digests/verify.txt"
        for path in (claude, codex, digest):
            path.write_text("operator customization")
        (self.root / ".config/forge/prompts/_preamble.txt").write_text("unowned shadow")
        result = self.install("--check-drift")
        self.assertNotEqual(result.returncode, 0)
        for name in ("skills/forge-coder", "codex-skills/fix-coder", "digests/verify.txt", "shadows a synthetic include", "Drift found"):
            self.assertIn(name, result.stdout)
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.install("--uninstall").returncode, 0)
        for path in (claude, codex, digest):
            self.assertEqual(path.read_text(), "operator customization")
        self.assertFalse((self.root / "bin/forge").is_symlink())
        self.assertFalse((self.root / ".claude/skills/adversarial-qa").exists())

    def test_unowned_legacy_assets_are_never_repointed_or_removed(self):
        old = self.root / "legacy"
        old.write_text("live legacy bytes")
        (self.root / "bin").mkdir()
        (self.root / "bin/forge").symlink_to(old)
        target = self.root / ".claude/skills/forge-orchestrator/SKILL.md"
        target.parent.mkdir(parents=True)
        target.write_text("active legacy protocol")
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.install("--uninstall").returncode, 0)
        self.assertEqual((self.root / "bin/forge").read_text(), "live legacy bytes")
        self.assertEqual(target.read_text(), "active legacy protocol")

    def test_ownership_preserves_empty_directories_and_concurrent_records(self):
        source = self.root / "source"
        source.mkdir()
        (source / "asset").write_text("owned")
        helper = str(TREE / "lib/forge_install.py")
        state = self.root / "ownership.json"
        targets = [self.root / ("target-" + str(i)) for i in range(4)]
        processes = [subprocess.Popen(["python3", helper, "copy", str(source), str(target), str(state)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for target in targets]
        for process in processes:
            _, error = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, error)
        self.assertEqual(len(json.loads(state.read_text())), len(targets))
        empty = targets[0] / "operator-empty-directory"
        empty.mkdir()
        cp = subprocess.run(["python3", helper, "remove", str(source), str(targets[0]), str(state)],
                            text=True, capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertTrue(empty.is_dir())


if __name__ == "__main__":
    unittest.main()
