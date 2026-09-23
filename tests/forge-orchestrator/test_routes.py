"""Local P5 contracts. No model invocation or native-parity claims."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import test_assets

assets = test_assets.assets
TREE = test_assets.TREE
BUILD = ("review", "incorporate", "implementation", "impl-review", "coding", "qa", "qa-fix", "qa-retry", "verify")
FIX = ("fix-reproduce", "fix-investigate", "fix-investigate-solo", "fix-plan", "fix-plan-solo",
       "fix-plan-review", "fix-plan-revise", "fix-code", "fix-qa", "fix-qa-retry", "fix-scout")


class Routes(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="forge-p5-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def history(self, rows):
        path = self.root / ".dev/proposals/sample/forge-log.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"entries": rows}))
        return path

    def row(self, to="claude-sonnet", stage="qa", timestamp="2026-09-23T10:00:00Z", response="FORGE_DONE: qa"):
        return dict(to=to, stage=stage, timestamp=timestamp, response=response)

    def cli(self, *args, rollout="contain"):
        return subprocess.run([str(TREE / "bin/forge"), "route", "--root", str(self.root), *args],
                              env={**os.environ, "FORGE_CODEX_ROLLOUT": rollout}, text=True, capture_output=True)

    def test_build_and_fix_routes_are_host_independent(self):
        expected = {s: "claude-opus" for s in FIX}
        expected.update(dict(review="codex-a", incorporate="claude-opus", implementation="codex-a",
                             **{"impl-review": "claude-opus", "coding": "claude-sonnet", "qa": "claude-sonnet",
                                "qa-fix": "claude-sonnet", "qa-retry": "claude-sonnet", "verify": "claude-opus"}))
        for host in ("claude", "codex"):
            for stage, worker in expected.items():
                with self.subTest(host=host, stage=stage):
                    selected = assets.select_route(stage, qa_worker="claude-sonnet")
                    self.assertEqual(selected["status"], "selected")
                    self.assertEqual(selected["selected_worker"], worker)
                    provider = worker.split("-")[0]
                    path = TREE / ("skills" if provider == "claude" else "codex-skills")
                    assets.route(stage, worker, skill_root=path)

    def test_tier_skill_lane_matrix_refuses_explicit_ineligible_requests(self):
        for rollout in ("audit", "contain", "broker-shadow", "enforce", "publish-enabled"):
            for stage in (*BUILD, *FIX):
                for worker in assets.WORKERS:
                    with self.subTest(rollout=rollout, stage=stage, worker=worker):
                        out = assets.select_route(stage, worker=worker, rollout=rollout, qa_worker="claude-sonnet")
                        if out["status"] == "selected":
                            self.assertEqual(out["selected_worker"], worker)
                            self.assertEqual(out["tier"], "high" if stage in FIX or stage in BUILD[:4] or stage == "verify" else "throughput")
                            self.assertFalse(worker.startswith("codex") and out["lane"] == "reviewed-host")
                        else:
                            self.assertEqual(out["status"], "refused")
                            self.assertIsNone(out["selected_worker"])

    def test_default_lane_selection_precedes_availability(self):
        out = assets.select_route("qa")
        self.assertEqual(out["requested_worker"], "codex-b")
        self.assertEqual(out["selected_worker"], "claude-sonnet")
        self.assertEqual(out["reason"], "reviewed-host-required")
        self.assertEqual(assets.select_route("qa", unavailable=["claude-sonnet"])["status"], "wait")
        self.assertEqual(assets.select_route("implementation", unavailable=["codex-a"])["selected_worker"], "claude-opus")
        self.assertEqual(assets.select_route("review", unavailable=["codex-a"])["status"], "wait")

    def test_assignment_is_not_silently_replaced(self):
        for key in ("worker", "assigned"):
            out = assets.select_route("implementation", **{key: "codex-a"}, unavailable=["codex-a"])
            self.assertEqual(out["status"], "wait")
            self.assertIsNone(out["selected_worker"])
        with self.assertRaisesRegex(ValueError, "substituted"):
            assets.select_route("implementation", worker="claude-opus", assigned="codex-a")
        self.assertEqual(assets.select_route("qa", assigned="codex-b")["status"], "refused")

    def test_no_tier_downgrade_or_local_fix_fallback(self):
        for stage in FIX:
            out = assets.select_route(stage, unavailable=assets.WORKERS)
            self.assertEqual(out["status"], "wait")
            self.assertIsNone(out["selected_worker"])
        for worker in ("claude", "orchestrator", "0", "codex-other"):
            with self.assertRaisesRegex(ValueError, "unknown worker"):
                assets.select_route("fix-code", worker=worker)

    def test_local_exceptions_are_prerequisites_not_authorization(self):
        out = assets.select_route("proposal")
        self.assertEqual(out["status"], "local-prerequisites")
        self.assertIn("isolated-proposal", out["required"])
        with self.assertRaisesRegex(ValueError, "not worker-dispatched"):
            assets.select_route("proposal", worker="claude-opus")
        for stage in ("qa", "qa-retry"):
            out = assets.select_route(stage, unavailable=["codex-b", "claude-sonnet"])
            self.assertEqual(out["status"], "local-prerequisites")
            self.assertIn("native-live-qa", out["required"])
            self.assertIn("infra-lock", out["required"])
            self.assertEqual(assets.select_route(stage, worker="claude-sonnet", unavailable=assets.WORKERS)["status"], "wait")

    def test_latest_qa_uses_dispatch_order_not_last_closed_row(self):
        self.history([self.row(), self.row(to="opus", stage="qa-retry", timestamp="2026-09-23T11:00:00Z")])
        self.assertEqual(assets.latest_qa(self.root, "sample"), "claude-opus")
        out = assets.select_route("verify", qa_worker="claude-opus")
        self.assertEqual(out["status"], "wait")
        self.assertEqual(out["rejected_workers"]["claude-opus"], "latest-qa-worker")
        self.history([self.row(timestamp="2026-09-23T11:00:00Z", stage="qa-retry", response=None), self.row()])
        with self.assertRaisesRegex(ValueError, "still pending"):
            assets.latest_qa(self.root, "sample")

    def test_qa_history_missing_ambiguous_malformed_unknown_refuses(self):
        for rows in ([], [self.row(), self.row()], [self.row(to="foreign")], [self.row(to=[])],
                     [self.row(timestamp=None)], [self.row(timestamp="not-a-time")],
                     [self.row(timestamp="2026-09-23T10:00:00")], [42]):
            self.history(rows)
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                assets.latest_qa(self.root, "sample")
        self.history([]).write_text("entries: [broken")
        with self.assertRaisesRegex(ValueError, "invalid QA history"):
            assets.latest_qa(self.root, "sample")
        with self.assertRaises(ValueError):
            assets.latest_qa(self.root, "../sample")

    def test_latest_qa_normalizes_timestamp_offsets(self):
        self.history([self.row(to="opus", timestamp="2026-09-23T01:00:00-10:00"), self.row()])
        self.assertEqual(assets.latest_qa(self.root, "sample"), "claude-opus")
        self.history([self.row(timestamp="2026-09-23T00:00:00-10:00"), self.row()])
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            assets.latest_qa(self.root, "sample")

    def test_cli_is_read_only_and_nonzero_for_wait_or_refusal(self):
        self.history([self.row()])
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        out = self.cli("--slug", "sample", "--stage", "verify")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["selected_worker"], "claude-opus")
        for args in (("--stage", "qa", "--worker", "codex-b"),
                     ("--stage", "qa", "--unavailable", "claude-sonnet"),
                     ("--stage", "proposal")):
            self.assertEqual(self.cli(*args).returncode, 3)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertNotEqual(self.cli("--stage", "qa", rollout="typo").returncode, 0)
        missing = self.cli()
        self.assertEqual(missing.returncode, 2)
        self.assertNotIn("Traceback", missing.stderr)

    def test_shared_lane_output_matches_route_and_legacy_format(self):
        for stage in (*BUILD, *FIX):
            out = subprocess.run([str(TREE / "bin/forge"), "codex-lane", "--root", str(self.root),
                                  "--stage", stage, "--worker", "claude-opus"], text=True, capture_output=True,
                                 env={**os.environ, "FORGE_CODEX_ROLLOUT": "contain"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(dict(line.split("=", 1) for line in out.stdout.splitlines()),
                             assets.capability_lane(stage, "claude-opus", "contain"))

    def test_future_commit_lane_still_requires_private_worker_home(self):
        shutil.copytree(TREE / "config", self.root / "config")
        path = self.root / "config/codex-forge-runtime.json"
        cfg = json.loads(path.read_text())
        self.assertEqual(assets.select_route("fix-code", tree=self.root, rollout="enforce", worker="codex-a")["status"], "refused")
        cfg["codex"]["private_codex_home_default"] = True
        path.write_text(json.dumps(cfg))
        self.assertEqual(assets.select_route("fix-code", tree=self.root, rollout="enforce", worker="codex-a")["status"], "selected")
        self.assertEqual(assets.select_route("qa", tree=self.root, rollout="enforce", worker="codex-b")["status"], "refused")

    def test_retry_snapshot_survives_report_overwrite_and_failed_retry(self):
        base = self.root / ".dev/qa/sample"
        base.mkdir(parents=True)
        for stage, name in (("qa-retry", "issues.md"), ("fix-qa-retry", "fix-issues.md")):
            report = base / name
            report.write_text("MAJOR F-1: original evidence")
            snapshot = Path(assets.snapshot_findings(self.root, "sample", stage))
            report.write_text("new run: F-1 RESOLVED")
            self.assertEqual(snapshot.read_text(), "MAJOR F-1: original evidence")
            report.unlink()
            with self.assertRaises(ValueError):
                assets.snapshot_findings(self.root, "sample", stage)
            self.assertEqual(snapshot.read_text(), "MAJOR F-1: original evidence")

    def test_snapshot_refuses_empty_missing_and_linked_artifacts(self):
        base = self.root / ".dev/qa/sample"
        base.mkdir(parents=True)
        report = base / "issues.md"
        report.write_text(" ")
        with self.assertRaisesRegex(ValueError, "empty"):
            assets.snapshot_findings(self.root, "sample", "qa-retry")
        report.unlink()
        report.symlink_to(TREE / "prompts/qa.txt")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            assets.snapshot_findings(self.root, "sample", "qa-retry")
        with self.assertRaisesRegex(ValueError, "invalid slug"):
            assets.snapshot_findings(self.root, "../sample", "qa-retry")

    def test_lane_guard_precedes_all_dispatch_mutations(self):
        source = (TREE / "bin/forge-bridge").read_text()
        body = re.search(r"^cmd_dispatch\(\).*?^\}", source, re.M | re.S)[0]
        refusal = body.index('echo "LANE_REQUIRED')
        for mutation in ('_worker_lock "$worker_canonical"', '_reset_worker_locked', '_patch_response_by_ts', 'DELIVERY_INTENT=', 'cmd_log --slug'):
            self.assertLess(refusal, body.index(mutation))
        self.assertLess(body.index('_snapshot_qa_findings'), body.index('_reset_worker_locked'))

    def bridge_function(self, name, setup, invocation, **overrides):
        source = (TREE / "bin/forge-bridge").read_text()
        body = re.search(r"^" + name + r"\(\).*?^\}", source, re.M | re.S)[0]
        stubs = '''
require_identity() { :; }
_require_root_cwd() { :; }
_orchestrator_admission() { :; }
canonical_pane_name() { printf '%s' "$1"; }
pane_index() { printf 1; }
_resolve_project_root() { printf '%s' "$ROOT"; }
_emit_event() { :; }
_stage_assets() { :; }
_constraint_carrier_stage() { return 1; }
_render_template() { printf 'fixture'; }
_stage_capability_class() { printf 'live-qa'; }
_infra_lock_owned_by_me() { return 0; }
_worker_lock() { touch "$ROOT/MUTATED"; return 1; }
ensure_pipeline_log() { touch "$ROOT/MUTATED"; return 1; }
_resolve_session() { printf 'fixture'; }
timestamp() { printf '2026-09-23T12:00:00Z'; }
yaml_escape() { printf '%s' "$1"; }
'''
        env = {**os.environ, "ROOT": str(self.root), "FORGE_FORGE_BIN": str(TREE / "bin/forge"),
               "FORGE_PROMPTS_DIR": str(TREE / "prompts"), "FORGE_CODEX_ROLLOUT": "contain",
               "FORGE_INFRA_GUARD_MODE": "enforce", "FORGE_ASSETS_REQUIRED": "0", **overrides}
        return subprocess.run(["bash", "-c", body + '\n' + stubs + setup + '\n' + invocation],
                              env=env, text=True, capture_output=True)

    def test_actual_dispatch_lane_refusal_before_clear_or_supersede(self):
        self.history([self.row(response=None)])
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        for stage, worker in (("qa", "codex-b"), ("verify", "codex-a")):
            result = self.bridge_function("cmd_dispatch", "", f"cmd_dispatch --slug sample --stage {stage} --worker {worker} --clear --supersede")
            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertIn("LANE_REQUIRED", result.stderr)
            self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_protected_dispatch_verify_exclusion_before_pending_or_reset(self):
        self.history([self.row(to="claude-opus")])
        result = self.bridge_function("cmd_dispatch", "", "cmd_dispatch --slug sample --stage verify --worker claude-opus --clear --supersede",
                                      FORGE_ASSETS_REQUIRED="1")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("latest-qa-worker", result.stderr)
        self.assertFalse((self.root / "MUTATED").exists())

    def test_protected_local_qa_refuses_unheld_lock_before_row_birth(self):
        for stage in ("qa", "qa-retry"):
            result = self.bridge_function("cmd_log", "\n_infra_lock_owned_by_me() { return 1; }\n",
                                          f"cmd_log --slug sample --stage {stage} --from claude --to claude --prompt fixture",
                                          FORGE_ASSETS_REQUIRED="1")
            self.assertEqual(result.returncode, 5, result.stderr)
            self.assertIn("INFRA_LOCK_REQUIRED", result.stderr)
            self.assertFalse((self.root / "MUTATED").exists())


class WorkflowContracts(unittest.TestCase):
    # Reuse only the production render harness, not the inherited asset test cases.
    setUp = test_assets.Assets.setUp
    shell = test_assets.Assets.shell

    def test_every_build_fix_prompt_and_digest_renders_for_both_hosts(self):
        directory = self.root / ".dev/proposals/sample"
        directory.mkdir(parents=True)
        (directory / "constraints.yml").write_text('schema: forge-constraints/1\nconstraints: []\nnone_recorded_reason: P5 local contract\n')
        for host in ("claude", "codex"):
            for stage in (*BUILD, *FIX):
                worker = assets.select_route(stage, qa_worker="claude-sonnet")["selected_worker"]
                for kind in ("prompts", "digests"):
                    with self.subTest(host=host, stage=stage, kind=kind):
                        path = TREE / kind / (stage + ".txt")
                        result = self.shell(f'_render_template "{path}" sample {stage} {worker} 1 "$ROOT"')
                        self.assertEqual(result.returncode, 0, result.stderr)
                        for marker in ("<<<INCLUDE", "{slug}", "{stage}", "{worker_skill}", "{project_root}"):
                            self.assertNotIn(marker, result.stdout)
                        if stage == "verify" and kind == "prompts":
                            self.assertIn("finding_results and check_results", result.stdout)
                            self.assertIn("Do not append free-form confidence", result.stdout)
                        else:
                            self.assertIn("CONFIDENCE:", result.stdout)
                            self.assertIn("BLOCKING_ITEMS:", result.stdout)
                        if stage in ("qa", "qa-retry") and kind == "prompts":
                            self.assertIn(f"callback --slug sample --stage {stage} --worker {worker}", result.stdout)

    def test_both_local_adapters_render_proposal_and_qa_retry(self):
        directory = self.root / ".dev/proposals/sample"
        directory.mkdir(parents=True)
        (directory / "constraints.yml").write_text('schema: forge-constraints/1\nconstraints: []\nnone_recorded_reason: P5 local contract\n')
        qa = self.root / ".dev/qa/sample"
        qa.mkdir(parents=True)
        (qa / "issues.md").write_text("CRITICAL previous finding")
        for host in ("claude", "codex"):
            for stage in ("proposal", "qa", "qa-retry"):
                result = self.shell(f'cmd_local_render --slug sample --provider {host} --stage {stage}')
                self.assertEqual(result.returncode, 0, result.stderr)
                rendered = Path(result.stdout.strip().split(": ", 1)[1]).read_text()
                self.assertNotIn("forge-bridge callback", rendered)
                self.assertIn("log-response", rendered)
                self.assertIn(".dev/" + ("proposals" if stage == "proposal" else "qa") + "/sample", rendered)
                if stage == "qa-retry":
                    self.assertIn("prior-issues.md", rendered)
                    self.assertEqual((qa / "prior-issues.md").read_text(), "CRITICAL previous finding")
        (qa / "issues.md").write_text("retry result overwrote canonical report")
        for _ in range(2):
            result = self.shell('cmd_digest --slug sample --stage qa-retry --template qa-retry')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((qa / "prior-issues.md").read_text(), "CRITICAL previous finding")

    def test_retry_contracts_carry_prior_findings_and_corrective_work(self):
        for stage, snapshot, correction in (("qa-retry", "prior-issues.md", "qa-fix-report.md"),
                                            ("fix-qa-retry", "prior-fix-issues.md", "fix-coder-report.md")):
            for kind in ("prompts", "digests"):
                text = (TREE / kind / (stage + ".txt")).read_text()
                self.assertIn(snapshot, text)
                self.assertIn(correction, text)
                self.assertIn("STILL OPEN", text)
        for host in ("claude", "codex"):
            adapter = (TREE / "skills/forge-orchestrator/references" / (host + ".md")).read_text()
            self.assertIn("workflow-routes.md", adapter)
            self.assertIn("live-QA", adapter)

    def test_conditional_stops_and_fix_triggers_remain_provider_neutral(self):
        protocol = (TREE / "skills/forge-orchestrator/SKILL.md").read_text()
        routes = " ".join((TREE / "skills/forge-orchestrator/references/workflow-routes.md").read_text().split())
        for marker in ("forge-fix-pipeline {slug} [--reproduce]", "/forge fix-pipeline <slug> [--reproduce]",
                       "INSUFFICIENT EVIDENCE", "SPEC FIDELITY", "TIER-REDUCED", "STILL REPRODUCES",
                       "effort_budget.worker_stages", "One", "second halts", "never open the PR autonomously"):
            self.assertIn(marker, protocol)
        for marker in ("missing artifacts", "digest-only", "Queued, stale, foreign", "unknown legacy rows",
                       "no eligible throughput worker", "native isolated live-QA", "Keep locks", "pause",
                       "dead-worker", "verify-decision", "finalize"):
            self.assertIn(marker.lower(), routes.lower())


if __name__ == "__main__":
    unittest.main()
