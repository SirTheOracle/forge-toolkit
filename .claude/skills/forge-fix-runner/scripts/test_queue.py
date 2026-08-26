"""Core tests for the forge-fix-runner queue predicate, ordering, deal and claims.

Run:  cd <this directory> && python3 test_queue.py
`pytest` is NOT installed on this machine. `import queue as q` below resolves to the
sibling queue.py ONLY because python3 puts the script directory at sys.path[0]; run
it from anywhere else and every test fails with AttributeError. Always cd first.
For the same reason no test here may import multiprocessing / concurrent.futures /
logging.handlers — they import the stdlib `queue` and collide (measured).
These cover the load-bearing safety logic: the actionable predicate (the
needs-retest hazard), exactly-one-classifier validation, worst-first ordering,
and per-classifier severity ordering.

forge-toolkit copy: fixtures use this project's `component:` classifier and its
bash-suite readiness rung, plus a case for the extra `operator-next` BLOCKING_LABEL
(live-gate issues the runner must never pick up — only the operator can close them).
"""
import contextlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

import queue as q


def issue(number, *labels, state="OPEN", title="t"):
    return {"number": number, "state": state, "title": title,
            "labels": [{"name": n} for n in labels]}


FF = "forge-fix"


def test_actionable_happy_path():
    assert q.is_actionable(issue(1, FF, "component:bridge", "severity:critical"))


def test_needs_retest_excluded():
    # forge-fix + needs-retest must NOT be actionable (already awaiting retest).
    assert not q.is_actionable(issue(81, FF, "component:watch", "severity:critical", "needs-retest"))


def test_in_progress_and_pr_open_excluded():
    assert not q.is_actionable(issue(2, FF, "component:bridge", "severity:high", "in-progress"))
    assert not q.is_actionable(issue(3, FF, "component:bridge", "severity:high", "fix-pr-open"))


def test_missing_forge_fix_excluded():
    assert not q.is_actionable(issue(4, "component:bridge", "severity:critical"))


def test_closed_excluded():
    assert not q.is_actionable(issue(5, FF, "component:bridge", "severity:low", state="CLOSED"))


def test_zero_or_multiple_service_labels_excluded():
    assert not q.is_actionable(issue(6, FF, "severity:critical"))  # none
    assert not q.is_actionable(issue(7, FF, "component:bridge", "component:watch", "severity:critical"))  # two


def test_worst_first_orders_by_criticals():
    issues = [
        issue(10, FF, "component:watch", "severity:critical"),
        issue(11, FF, "component:watch", "severity:critical"),
        issue(12, FF, "component:bridge", "severity:critical"),
        issue(13, FF, "component:bridge", "severity:critical"),
        issue(14, FF, "component:bridge", "severity:critical"),
        issue(15, FF, "component:bridge", "severity:medium"),
        # non-actionable: must not inflate eval's count
        issue(16, FF, "component:bridge", "severity:critical", "needs-retest"),
    ]
    order = [r["service"] for r in q.worst_first(issues)]
    assert order == ["bridge", "watch"]
    eval_row = next(r for r in q.worst_first(issues) if r["service"] == "bridge")
    assert eval_row["critical"] == 3  # the needs-retest one is excluded
    assert eval_row["medium"] == 1


def test_queue_for_severity_then_number():
    issues = [
        issue(20, FF, "component:bridge", "severity:medium"),
        issue(21, FF, "component:bridge", "severity:critical"),
        issue(19, FF, "component:bridge", "severity:critical"),
        issue(22, FF, "component:bridge", "severity:high"),
        issue(23, FF, "component:watch", "severity:critical"),  # other service
    ]
    got = [i["number"] for i in q.queue_for("bridge", issues)]
    assert got == [19, 21, 22, 20]  # crit(19,21) -> high(22) -> medium(20)


@contextlib.contextmanager
def tmpdir():
    d = tempfile.mkdtemp(prefix="fanout-")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def bucket(gid, tier="quick", issues=(1,), **kw):
    b = {"group_id": gid, "service": "svc", "tier": tier,
         "covered_issue_numbers": list(issues), "depends_on": [],
         "base_branch": "main", "base_sha": "a" * 40,
         "slug": gid, "branch_name": f"fix/{gid}", "packet": f"packets/{gid}.md",
         "approval_status": "approved",
         "close_keywords": " ".join(f"Closes #{n}" for n in issues),
         "required_tests": ["cd backend && pytest -q"]}
    b.update(kw)
    return b


def write_packet(plan_dir, b, queues, **override):
    import yaml
    qid = next((k for k, v in queues.items() if b["group_id"] in v), "S1")
    front = {"group_id": b["group_id"], "service": b["service"], "tier": b["tier"],
             "slug": b["slug"], "branch_name": b["branch_name"], "queue_id": qid,
             "plan_dir": plan_dir, "base_branch": b["base_branch"],
             "base_sha": b["base_sha"],
             "covered_issue_numbers": b["covered_issue_numbers"],
             "close_keywords": b["close_keywords"],
             "required_tests": b["required_tests"], "drop_conditions": "n/a",
             "verification_targets": [
                 {"issue": n, "coded_id": f"SVC-{n}", "symptom": "s",
                  "check": "c", "evidence": "e"} for n in b["covered_issue_numbers"]]}
    front.update(override)
    with open(os.path.join(plan_dir, b["packet"]), "w") as fh:
        fh.write("---\n" + yaml.safe_dump(front, sort_keys=True) + "---\n\nbody\n")


def make_plan(root, buckets, queues=None, seal=True):
    """A minimal on-disk plan directory, sealed by default."""
    plan_dir = os.path.join(root, "plan")
    os.makedirs(os.path.join(plan_dir, "packets"), exist_ok=True)
    queues = q.deal_buckets(buckets) if queues is None else queues
    deal = {"schema": q.DEAL_SCHEMA, "service": "svc", "created_at": "now",
            "plan_dir": plan_dir, "buckets": buckets, "queues": queues}
    open(os.path.join(plan_dir, "plan.md"), "w").write("# plan\n")
    q._write_json_atomic(os.path.join(plan_dir, q.DEAL_FILE), deal)
    for b in buckets:
        write_packet(plan_dir, b, queues)
    if seal:
        q.seal_plan(plan_dir)
    return plan_dir


def fake_tmux(root, rows):
    """FORGE_TMUX_LIST seam: rows = [(name, path, pid, incarnation), …]."""
    path = os.path.join(root, "tmux.tsv")
    with open(path, "w") as fh:
        for name, p, pid, inc in rows:
            fh.write(f"{name}\t{p}\t{pid}\t{inc}\n")
    os.environ["FORGE_TMUX_LIST"] = path
    return path


def git_root(root, name="wt"):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    return os.path.realpath(d)


def _issue(n, *labels, state="OPEN"):
    return {"number": n, "state": state, "title": f"t{n}",
            "labels": [{"name": x} for x in labels]}


class _Args:
    """Stand-in for argparse.Namespace. Defaults mirror main()'s parser."""
    def __init__(self, **kw):
        self.root = "."; self.plans_dir = None; self.repo = None; self.json = None
        self.deal_status = False; self.new_deal = False
        self.apply = False; self.plan_dir = []; self.allow_gh_failure = False
        self.__dict__.update(kw)


def test_tier_dominates_for_realistic_bucket_sizes():
    """The PROPERTY, not the numbers: no realistic quick bucket outweighs any full
    one. Observed n in the real artifact is 1-3; 1-5 is the safety margin."""
    w = lambda t, n: q.bucket_weight(bucket("g", tier=t, issues=tuple(range(n))))
    assert min(w("full", n) for n in range(1, 6)) > max(w("quick", n) for n in range(1, 6))


def test_bucket_weight_is_additive():
    assert q.bucket_weight(bucket("g", tier="quick", issues=(1, 2, 3))) == q.TIER_BASE["quick"] + 3
    assert q.bucket_weight(bucket("g", tier="full", issues=())) == q.TIER_BASE["full"]
    assert q.bucket_weight(bucket("g", tier="full", issues=(1, 2))) == q.TIER_BASE["full"] + 2


def test_weight_warning_fires_for_a_ten_issue_bucket():
    """The plan requires a WARNING, never a silent inversion: the bucket must still
    be dealt."""
    assert q.weight_warnings([bucket("small", issues=(1, 2, 3))]) == []
    big = [bucket("big", issues=tuple(range(q.BIG_BUCKET_ISSUES)))]
    warns = q.weight_warnings(big)
    assert len(warns) == 1 and "big" in warns[0]
    dealt = [g for members in q.deal_buckets(big, 3).values() for g in members]
    assert dealt == ["big"], "a warned bucket was dropped or re-ordered instead of dealt"


def test_deal_worked_example_initiator_holds_heaviest_weight_not_most_buckets():
    bs = [bucket(f"h{i}", tier="full") for i in range(4)] + \
         [bucket(f"l{i}", tier="quick") for i in range(3)]
    queues = q.deal_buckets(bs, 3)
    tot = q.queue_weights(bs, queues)
    assert [len(queues[k]) for k in ("S1", "S2", "S3")] == [2, 3, 2]
    assert tot["S1"] == max(tot.values())            # heaviest WEIGHT
    assert len(queues["S1"]) < len(queues["S2"])     # but FEWER buckets


def test_deal_uniform_seven_splits_three_two_two():
    queues = q.deal_buckets([bucket(f"u{i}") for i in range(7)], 3)
    assert sorted(len(v) for v in queues.values()) == [2, 2, 3]
    assert len(queues["S1"]) == 3          # the remainder goes to the initiator


def test_deal_exact_tie_goes_to_the_initiator():
    three = q.deal_buckets([bucket("a"), bucket("b"), bucket("c")], 3)
    assert [len(three[k]) for k in ("S1", "S2", "S3")] == [1, 1, 1]
    assert three["S1"] == ["a"]            # tie -> lowest index -> S1
    assert q.deal_buckets([bucket("a"), bucket("b"), bucket("c"), bucket("d")], 3)["S1"] \
        == ["a", "d"]                      # the 4th uniform bucket lands on S1, not S3


def test_deal_is_deterministic_under_shuffle():
    import random
    bs = [bucket(f"g{i}", tier="full" if i % 2 else "quick", issues=tuple(range(i % 3 + 1)))
          for i in range(7)]
    want = q.deal_buckets(bs, 3)
    for seed in range(5):
        shuffled = bs[:]; random.Random(seed).shuffle(shuffled)
        assert q.deal_buckets(shuffled, 3) == want


def test_deal_to_n_buckets_when_fewer_than_sessions():
    assert list(q.deal_buckets([bucket("a"), bucket("b")], 3)) == ["S1", "S2"]
    assert list(q.deal_buckets([bucket("a")], 3)) == ["S1"]
    assert q.deal_buckets([], 3) == {}     # never an empty queue an operator opens a tab for


def test_deal_rejects_duplicate_group_ids():
    """A duplicate group_id in an operator-edited plan would deal one bucket twice."""
    try:
        q.deal_buckets([bucket("g1"), bucket("g1", tier="full")], 3)
    except ValueError as ex:
        assert "duplicate group_id" in str(ex)
    else:
        raise AssertionError("a duplicate group_id was dealt instead of refused")
    d = {"buckets": [bucket("g1"), bucket("g1")], "queues": {"S1": ["g1"]}}
    assert any("duplicate group_id" in e for e in q.verify_deal(d))


def test_deal_rejects_nonempty_depends_on():
    try:
        q.deal_buckets([bucket("a6"), bucket("a8", depends_on=["a6"])], 3)
    except ValueError as ex:
        assert "depends_on" in str(ex) and "one bucket" in str(ex)
    else:
        raise AssertionError("a chain was dealt instead of refused")


def test_verify_deal_rejects_nonempty_depends_on():
    d = {"buckets": [bucket("a"), bucket("b", depends_on=["a"])],
         "queues": {"S1": ["a"], "S2": ["b"]}}
    assert any("depends_on" in e for e in q.verify_deal(d))


def test_empty_depends_on_is_accepted():
    """The key survives so the 39 legacy routing artifacts still parse."""
    assert q.dependency_errors([bucket("a"), bucket("b")]) == []


def test_claim_rejects_a_chain_that_reached_the_plan():
    with tmpdir() as root:
        wt = git_root(root); fake_tmux(root, [("s1", wt, "1", "111")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"; os.environ["TMUX_SESSION"] = "s1"
        plan_dir = make_plan(root, [bucket("a"), bucket("b", depends_on=["a"])],
                             queues={"S1": ["a", "b"]})
        try:
            q.claim_queue(plan_dir, "S1", wt)
        except q.ClaimRefused as ex:
            assert ex.code == q.EXIT_INVALID_PLAN
        else:
            raise AssertionError("claimed a queue containing a chain")


def test_packet_check_rejects_nonempty_depends_on():
    with tmpdir() as root:
        b = bucket("g1", depends_on=["g0"])
        plan_dir = make_plan(root, [b], queues={"S1": ["g1"]})
        assert any("depends_on" in e for e in q.check_packets(plan_dir))


def test_base_branch_rejects_a_remote_qualified_value():
    for bad in ("origin/main", "refs/heads/main", "origin/develop"):
        try:
            q.normalize_base_branch(bad)
        except ValueError as ex:
            assert "BARE" in str(ex)
        else:
            raise AssertionError(f"{bad} accepted")


def test_base_remote_ref_never_double_prefixes():
    assert q.base_remote_ref(bucket("a")) == "origin/main"
    try:
        q.base_remote_ref(bucket("a", base_branch="origin/main"))
    except ValueError:
        pass
    else:
        raise AssertionError("base_remote_ref produced origin/origin/main")


def test_verify_deal_flags_a_prefixed_base_branch():
    d = {"buckets": [bucket("a", base_branch="origin/main")], "queues": {"S1": ["a"]}}
    assert any("BARE" in e for e in q.verify_deal(d))


def test_verify_rejects_empty_queue_double_assignment_and_over_cap():
    # DISTINCT issues per bucket: bucket() defaults every bucket to issues=(1,), and
    # verify_deal correctly rejects the same issue being covered twice (that is its own
    # test). Sharing the default here would make this baseline non-empty for a reason
    # that has nothing to do with what this test is about.
    base = {"buckets": [bucket("a", issues=(1,)), bucket("b", issues=(2,))],
            "queues": {"S1": ["a"], "S2": ["b"]}}
    assert q.verify_deal(base) == []
    errs = lambda d: " | ".join(q.verify_deal(d))
    d = json.loads(json.dumps(base)); d["queues"]["S3"] = []
    assert "queue S3 is empty" in errs(d)
    d = json.loads(json.dumps(base)); d["queues"]["S2"] = ["a", "b"]
    assert "assigned to 2 queues" in errs(d)
    d = json.loads(json.dumps(base)); d["queues"] = {"S1": ["a"]}
    assert "bucket b is not assigned" in errs(d)
    d = json.loads(json.dumps(base)); d["queues"]["S1"] = ["ghost"]
    assert "unknown bucket ghost" in errs(d)
    d = {"buckets": [bucket(f"q{i}", issues=(i + 1,)) for i in range(4)],
         "queues": {"S1": ["q0"], "S2": ["q1"], "S3": ["q2"], "S4": ["q3"]}}
    assert "exceeds the 3-session cap" in errs(d)


def test_verify_rejects_duplicate_issue_coverage_across_approved_buckets():
    d = {"buckets": [bucket("a", issues=(7, 8)), bucket("b", issues=(8,))],
         "queues": {"S1": ["a"], "S2": ["b"]}}
    assert any("#8 is covered by both" in e for e in q.verify_deal(d))


def test_seal_detects_modification_addition_and_removal():
    with tmpdir() as root:
        plan_dir = make_plan(root, [bucket("a"), bucket("b")])
        assert q.verify_seal(plan_dir) == []
        open(os.path.join(plan_dir, "plan.md"), "a").write("tampered\n")
        assert any("MODIFIED after approval" in e for e in q.verify_seal(plan_dir))
    with tmpdir() as root:
        plan_dir = make_plan(root, [bucket("a")])
        open(os.path.join(plan_dir, "packets", "smuggled.md"), "w").write("---\n---\n")
        assert any("added after approval" in e for e in q.verify_seal(plan_dir))
    with tmpdir() as root:
        plan_dir = make_plan(root, [bucket("a")])
        os.remove(os.path.join(plan_dir, "packets", "a.md"))
        assert any("removed after approval" in e for e in q.verify_seal(plan_dir))


def test_seal_ignores_claims_and_journal():
    """The two mutable subtrees ARE the narrow scope-guard exception; writing them
    must never invalidate the seal, or every executor breaks its own plan."""
    with tmpdir() as root:
        wt = git_root(root); fake_tmux(root, [("s1", wt, "1", "111")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"; os.environ["TMUX_SESSION"] = "s1"
        plan_dir = make_plan(root, [bucket("a")])
        q.claim_queue(plan_dir, "S1", wt)
        os.makedirs(os.path.join(plan_dir, "journal"), exist_ok=True)
        open(os.path.join(plan_dir, "journal", "S1.md"), "a").write("bucket a done\n")
        assert q.verify_seal(plan_dir) == []


def test_unsealed_plan_cannot_be_claimed():
    with tmpdir() as root:
        plan_dir = make_plan(root, [bucket("a")], seal=False)
        wt = git_root(root); fake_tmux(root, [("s1", wt, "1", "111")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"; os.environ["TMUX_SESSION"] = "s1"
        try:
            q.claim_queue(plan_dir, "S1", wt)
        except q.ClaimRefused as ex:
            assert ex.code == q.EXIT_SEAL_MISMATCH
        else:
            raise AssertionError("claimed an unsealed plan")


def test_tampered_plan_cannot_be_claimed():
    with tmpdir() as root:
        plan_dir = make_plan(root, [bucket("a")])
        open(os.path.join(plan_dir, "plan.md"), "a").write("edited after approval\n")
        wt = git_root(root); fake_tmux(root, [("s1", wt, "1", "111")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"; os.environ["TMUX_SESSION"] = "s1"
        try:
            q.claim_queue(plan_dir, "S1", wt)
        except q.ClaimRefused as ex:
            assert ex.code == q.EXIT_SEAL_MISMATCH
        else:
            raise AssertionError("claimed a tampered plan")


def test_claim_fresh_then_already_claimed_then_root_conflict():
    with tmpdir() as root:
        wt_a = git_root(root, "a"); wt_b = git_root(root, "b")
        plan_dir = make_plan(root, [bucket("g1"), bucket("g2")])
        fake_tmux(root, [("sA", wt_a, "1", "111"), ("sB", wt_b, "2", "222")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"

        os.environ["TMUX_SESSION"] = "sA"
        rec = q.claim_queue(plan_dir, "S1", wt_a)
        assert rec["queue"] == "S1" and rec["owner_token"]

        os.environ["TMUX_SESSION"] = "sB"
        try:
            q.claim_queue(plan_dir, "S1", wt_b)
        except q.ClaimRefused as ex:
            assert ex.code == q.EXIT_ALREADY_CLAIMED
        else:
            raise AssertionError("double-claimed one queue")

        # A different queue from the SAME physical root, entered from a SUBDIRECTORY:
        # the comparison is realpath(git toplevel), never $PWD.
        sub = os.path.join(wt_a, "backend"); os.makedirs(sub, exist_ok=True)
        try:
            q.claim_queue(plan_dir, "S2", sub)
        except q.ClaimRefused as ex:
            assert ex.code == q.EXIT_ROOT_CONFLICT
        else:
            raise AssertionError("two queues claimed from one physical root")


def test_claim_steals_a_dead_incarnation_but_never_a_foreign_host():
    with tmpdir() as root:
        wt_a = git_root(root, "a"); wt_b = git_root(root, "b")
        plan_dir = make_plan(root, [bucket("g1")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"
        fake_tmux(root, [("sA", wt_a, "1", "111")])
        os.environ["TMUX_SESSION"] = "sA"; q.claim_queue(plan_dir, "S1", wt_a)

        # A reborn same-NAME session is NOT the same session: the incarnation moved.
        fake_tmux(root, [("sA", wt_a, "1", "999"), ("sB", wt_b, "2", "222")])
        os.environ["TMUX_SESSION"] = "sB"
        rec = q.claim_queue(plan_dir, "S1", wt_b)
        assert rec["tmux_session"] == "sB" and rec["stole_from"] == "sA"

        path = os.path.join(plan_dir, "claims", "S1.json")
        r = json.load(open(path)); r["host"] = "other-mac"; q._write_json_atomic(path, r)
        try:
            q.claim_queue(plan_dir, "S1", wt_b)
        except q.ClaimRefused as ex:
            assert ex.code == q.EXIT_ALREADY_CLAIMED and "foreign host" in str(ex)
        else:
            raise AssertionError("stole a foreign-host claim")


def test_old_owner_release_cannot_delete_a_successors_claim():
    """A dies; B steals; A's slow release must be a no-op, not a deletion — otherwise
    the queue silently reopens and two sessions can work it."""
    with tmpdir() as root:
        wt_a = git_root(root, "a"); wt_b = git_root(root, "b")
        plan_dir = make_plan(root, [bucket("g1")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"
        fake_tmux(root, [("sA", wt_a, "1", "111")])
        os.environ["TMUX_SESSION"] = "sA"; old = q.claim_queue(plan_dir, "S1", wt_a)
        fake_tmux(root, [("sB", wt_b, "2", "222")])
        os.environ["TMUX_SESSION"] = "sB"; new = q.claim_queue(plan_dir, "S1", wt_b)
        os.environ["TMUX_SESSION"] = "sA"
        assert "not-owner" in q.release_queue(plan_dir, "S1", wt_a, old["owner_token"])
        assert os.path.exists(os.path.join(plan_dir, "claims", "S1.json"))
        os.environ["TMUX_SESSION"] = "sB"
        assert "RELEASED" in q.release_queue(plan_dir, "S1", wt_b, new["owner_token"])
        assert not os.path.exists(os.path.join(plan_dir, "claims", "S1.json"))


def test_concurrent_claims_from_one_root_produce_exactly_one_winner():
    """V8's barrier test. O_EXCL is per FILE, so two processes creating S1.json and
    S2.json could both pass a naive root scan; only the plan-wide flock covering the
    SCAN makes exactly one win.

    subprocess, NOT multiprocessing: multiprocessing imports the stdlib `queue`, which
    this directory shadows (measured: ImportError 'cannot import name Empty').
    """
    with tmpdir() as root:
        wt = git_root(root, "shared")
        plan_dir = make_plan(root, [bucket("g1"), bucket("g2")])
        tsv = fake_tmux(root, [("sA", wt, "1", "111"), ("sB", wt, "2", "222")])
        prog = os.path.join(root, "claimer.py")
        open(prog, "w").write(
            "import sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import queue as q\n"
            "start = float(sys.argv[5])\n"
            "time.sleep(max(0.0, start - time.time()))\n"     # barrier, no busy-wait
            "try:\n"
            "    q.claim_queue(sys.argv[2], sys.argv[3], sys.argv[4]); print('WIN')\n"
            "except q.ClaimRefused as ex:\n"
            "    print('LOSE', ex.code)\n")
        here = os.path.dirname(os.path.abspath(q.__file__))
        t0 = str(time.time() + 0.5)
        procs = [subprocess.Popen(
                    [sys.executable, prog, here, plan_dir, qid, wt, t0],
                    stdout=subprocess.PIPE, text=True,
                    env={**os.environ, "TMUX_SESSION": sess, "FORGE_TMUX_LIST": tsv,
                         "FORGE_RUNNER_SELF_HOST": "h1"})
                 for qid, sess in (("S1", "sA"), ("S2", "sB"))]
        outs = [p.communicate()[0].strip() for p in procs]
        assert sum(o.startswith("WIN") for o in outs) == 1, outs
        assert any(f"LOSE {q.EXIT_ROOT_CONFLICT}" in o for o in outs), outs
        assert len([f for f in os.listdir(os.path.join(plan_dir, "claims"))
                    if f.endswith(".json")]) == 1


def test_reclaiming_from_the_same_session_is_reentrant():
    with tmpdir() as root:
        wt = git_root(root); plan_dir = make_plan(root, [bucket("g1")])
        fake_tmux(root, [("sA", wt, "1", "111")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"; os.environ["TMUX_SESSION"] = "sA"
        a = q.claim_queue(plan_dir, "S1", wt)
        b = q.claim_queue(plan_dir, "S1", wt)
        assert a["owner_token"] == b["owner_token"]


def test_reconcile_finds_an_orphan_but_never_a_protected_label():
    with tmpdir() as root:
        wt = git_root(root)
        bs = [bucket("g1", issues=(1,)), bucket("g2", issues=(2,)), bucket("g3", issues=(3,))]
        plan_dir = make_plan(root, bs, queues={"S1": ["g1"], "S2": ["g2"], "S3": ["g3"]})
        fake_tmux(root, [("live", wt, "1", "111")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"; os.environ["TMUX_SESSION"] = "live"
        q.claim_queue(plan_dir, "S2", wt)                       # protects #2
        issues = [_issue(n, "forge-fix", "in-progress") for n in (1, 2, 3)]
        prs = [{"number": 90, "body": "Closes #3", "title": "", "headRefName": "fix/g3"}]
        rep = q.reconcile(issues, prs, [plan_dir], wt)
        assert [r["number"] for r in rep["orphans"]] == [1]      # only #1 is unowned
        assert rep["dead_claims"] == []


def test_reconcile_never_uses_elapsed_time_and_expires_only_dead_claims():
    """V6: a full-tier bucket can legitimately run past any hour threshold. The ONLY
    expiry signal is a dead incarnation."""
    assert not hasattr(q, "STALE_IN_PROGRESS_H")
    with tmpdir() as root:
        wt = git_root(root); plan_dir = make_plan(root, [bucket("g1", issues=(1,))])
        fake_tmux(root, [("gone", wt, "1", "111")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"; os.environ["TMUX_SESSION"] = "gone"
        q.claim_queue(plan_dir, "S1", wt)
        path = os.path.join(plan_dir, "claims", "S1.json")
        r = json.load(open(path)); r["claimed_at"] = "1999-01-01T00:00:00Z"
        q._write_json_atomic(path, r)
        assert q.reconcile([_issue(1, "forge-fix", "in-progress")], [], [plan_dir], wt)["orphans"] == []
        fake_tmux(root, [])                                     # the incarnation is gone
        rep = q.reconcile([_issue(1, "forge-fix", "in-progress")], [], [plan_dir], wt)
        assert [r["number"] for r in rep["orphans"]] == [1]
        assert len(rep["dead_claims"]) == 1


def test_reconcile_has_no_wall_clock_threshold_in_source():
    src = pathlib.Path(q.__file__).read_text()
    assert "STALE_IN_PROGRESS_H" not in src, \
        "a wall-clock staleness knob reappeared; 'stale' means generation, never age"


def test_reconcile_reports_a_foreign_claim_without_removing_it():
    with tmpdir() as root:
        wt = git_root(root); plan_dir = make_plan(root, [bucket("g1", issues=(1,))])
        fake_tmux(root, [("sA", wt, "1", "111")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"; os.environ["TMUX_SESSION"] = "sA"
        q.claim_queue(plan_dir, "S1", wt)
        path = os.path.join(plan_dir, "claims", "S1.json")
        r = json.load(open(path)); r["host"] = "other-mac"; q._write_json_atomic(path, r)
        rep = q.reconcile([_issue(1, "forge-fix", "in-progress")], [], [plan_dir], wt)
        assert rep["dead_claims"] == [] and len(rep["foreign_claims"]) == 1
        assert rep["orphans"] == []                              # still protected


def test_reconcile_requires_apply_to_mutate():
    """Invariant 4: status is a human-owned control plane. A dry run must not call the
    mutating path AT ALL — citing the handler's default flag is not a test."""
    with tmpdir() as root:
        wt = git_root(root); plan_dir = make_plan(root, [bucket("g1", issues=(1,))])
        fake_tmux(root, []); os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"
        os.environ["TMUX_SESSION"] = "gone"
        issues = [_issue(1, "forge-fix", "in-progress")]
        calls = []
        saved = (q._load, q.open_prs, q.apply_reconcile)
        q._load = lambda a: issues
        q.open_prs = lambda repo: []
        q.apply_reconcile = lambda repo, report: calls.append(report)
        try:
            args = _Args(root=wt, plans_dir=os.path.dirname(plan_dir), repo="o/r",
                         apply=False, plan_dir=[plan_dir])
            q._cmd_reconcile(args)
            assert calls == [], "a dry run reached apply_reconcile"
            args.apply = True
            q._cmd_reconcile(args)
            assert len(calls) == 1 and [r["number"] for r in calls[0]["orphans"]] == [1]
        finally:
            q._load, q.open_prs, q.apply_reconcile = saved


def test_guard_refuses_triage_while_a_deal_is_live_and_passes_once_it_retires():
    with tmpdir() as root:
        wt = git_root(root)
        plan_dir = make_plan(root, [bucket("g1", issues=(5,))])
        args = _Args(root=wt, plans_dir=os.path.dirname(plan_dir))
        fake_tmux(root, []); os.environ["TMUX_SESSION"] = "none"
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"
        try:
            q.guard_live_deal(args, [_issue(5, "forge-fix", "in-progress")])
        except SystemExit as ex:
            assert ex.code == q.EXIT_LIVE_DEAL
        else:
            raise AssertionError("triage was permitted while a deal was live")
        # step 6 swaps in-progress for fix-pr-open -> the deal SELF-retires
        q.guard_live_deal(args, [_issue(5, "forge-fix", "fix-pr-open")])
        q.guard_live_deal(args, [_issue(5, "forge-fix", "in-progress", state="CLOSED")])


def test_guard_scans_every_deal_not_only_the_newest():
    """V9: after --new-deal, a NEWER inactive deal must not hide an OLDER live one."""
    with tmpdir() as root:
        wt = git_root(root)
        plans = os.path.join(root, "plans"); os.makedirs(plans)
        for name, num in (("a-2026-01-01", 5), ("z-2026-09-09", 6)):
            pd = os.path.join(plans, name); os.makedirs(os.path.join(pd, "packets"))
            q._write_json_atomic(os.path.join(pd, q.DEAL_FILE),
                                 {"schema": q.DEAL_SCHEMA, "service": "svc",
                                  "buckets": [bucket("g1", issues=(num,))],
                                  "queues": {"S1": ["g1"]}})
        args = _Args(root=wt, plans_dir=plans)
        fake_tmux(root, []); os.environ["TMUX_SESSION"] = "none"
        try:
            q.guard_live_deal(args, [_issue(5, "forge-fix", "in-progress"),
                                     _issue(6, "forge-fix", "fix-pr-open")])
        except SystemExit as ex:
            assert ex.code == q.EXIT_LIVE_DEAL      # the OLDER deal is still live
        else:
            raise AssertionError("an older live deal was hidden by a newer inactive one")


def test_guard_reads_instead_of_refusing_for_deal_status_and_claim_holders():
    with tmpdir() as root:
        wt = git_root(root)
        plan_dir = make_plan(root, [bucket("g1", issues=(5,))])
        plans = os.path.dirname(plan_dir)
        issues = [_issue(5, "forge-fix", "in-progress")]
        fake_tmux(root, [("live", wt, "1", "111")])
        os.environ["FORGE_RUNNER_SELF_HOST"] = "h1"; os.environ["TMUX_SESSION"] = "live"
        try:
            q.guard_live_deal(_Args(root=wt, plans_dir=plans, deal_status=True), issues)
        except SystemExit as ex:
            assert ex.code == 0
        q.claim_queue(plan_dir, "S1", wt)           # Hard Rule 16 re-orientation
        try:
            q.guard_live_deal(_Args(root=wt, plans_dir=plans), issues)
        except SystemExit as ex:
            assert ex.code == 0
        else:
            raise AssertionError("the claim holder did not get the read path")


def test_guard_fails_closed_when_gh_is_unreachable():
    with tmpdir() as root:
        wt = git_root(root)
        plan_dir = make_plan(root, [bucket("g1", issues=(5,))])
        try:
            q.guard_live_deal(_Args(root=wt, plans_dir=os.path.dirname(plan_dir)), None)
        except SystemExit as ex:
            assert ex.code == q.EXIT_LIVE_DEAL
        else:
            raise AssertionError("an unreachable gh was treated as an empty queue")


def test_gh_unreachable_fails_closed_even_with_no_deals_on_disk():
    """The ordinary first-run state on a project that has never fanned out. With the
    None check below the deal scan, this returned early and let None reach
    worst_first(), raising TypeError instead of refusing."""
    with tmpdir() as root:
        wt = git_root(root)
        empty = os.path.join(root, "no-plans"); os.makedirs(empty)
        try:
            q.guard_live_deal(_Args(root=wt, plans_dir=empty), None)
        except SystemExit as ex:
            assert ex.code == q.EXIT_LIVE_DEAL
        else:
            raise AssertionError("gh-unreachable with no deals did not fail closed")


def test_packet_check_fails_when_a_covered_issue_has_no_verification_row():
    with tmpdir() as root:
        b = bucket("g1", issues=(7, 8))
        plan_dir = make_plan(root, [b], queues={"S1": ["g1"]})
        write_packet(plan_dir, b, {"S1": ["g1"]},
                     verification_targets=[{"issue": 7, "coded_id": "SVC-7",
                                            "symptom": "s", "check": "c", "evidence": "e"}])
        assert any("#8 is covered but has NO verification row" in e
                   for e in q.check_packets(plan_dir))


def test_packet_check_rejects_extra_rows_extra_closes_and_field_disagreement():
    with tmpdir() as root:
        b = bucket("g1", issues=(7,))
        plan_dir = make_plan(root, [b], queues={"S1": ["g1"]})
        write_packet(plan_dir, b, {"S1": ["g1"]}, close_keywords="Closes #7 Closes #99")
        assert any("names #99" in e for e in q.check_packets(plan_dir))
        write_packet(plan_dir, b, {"S1": ["g1"]}, base_sha="b" * 40)
        assert any("base_sha" in e for e in q.check_packets(plan_dir))
        write_packet(plan_dir, b, {"S1": ["g1"]}, queue_id="S3")
        assert any("queue_id" in e for e in q.check_packets(plan_dir))
        # Override BOTH: adding 42 to covered_issue_numbers alone leaves the packet's
        # verification_targets built from the bucket's [7], so the "row for an issue this
        # bucket does not cover" direction would never fire and only one of the two
        # assertions below would be exercised.
        write_packet(plan_dir, b, {"S1": ["g1"]}, covered_issue_numbers=[7, 42],
                     verification_targets=[{"issue": n, "coded_id": f"SVC-{n}",
                                            "symptom": "s", "check": "c", "evidence": "e"}
                                           for n in (7, 42)])
        errs = q.check_packets(plan_dir)
        assert any("packet covers [7, 42]" in e for e in errs)
        assert any("verification row for #42" in e for e in errs)   # BOTH directions


def test_packet_check_refuses_a_symlink_escape():
    with tmpdir() as root:
        b = bucket("g1")
        plan_dir = make_plan(root, [b], queues={"S1": ["g1"]})
        outside = os.path.join(root, "outside.md"); open(outside, "w").write("---\n---\n")
        target = os.path.join(plan_dir, b["packet"])
        os.remove(target); os.symlink(outside, target)
        assert any("escapes packets/" in e for e in q.check_packets(plan_dir))


def test_packet_check_requires_a_missing_front_matter_to_fail_loudly():
    """A free-text scan cannot gate invariant 1: the verbatim `gh issue view` bodies
    below the fence routinely contain tables and #N references."""
    with tmpdir() as root:
        b = bucket("g1")
        plan_dir = make_plan(root, [b], queues={"S1": ["g1"]})
        open(os.path.join(plan_dir, b["packet"]), "w").write(
            "| #1 | SVC-1 | sym | cmd | path |\nno front matter here\n")
        assert any("front-matter" in e for e in q.check_packets(plan_dir))


def test_readiness_commands_are_the_deduplicated_union_over_the_queue():
    b1 = bucket("g1", required_tests=["cd frontend && npm run test:run"])
    b2 = bucket("g2", required_tests=["cd backend && pytest -q",
                                      "cd frontend && npm run test:run"])
    deal = {"schema": q.DEAL_SCHEMA, "buckets": [b1, b2], "queues": {"S1": ["g1", "g2"]}}
    assert q.readiness_commands(deal, "S1") == [
        "cd frontend && npm run test:run", "cd backend && pytest -q"]


def test_readiness_covers_every_toolchain_not_just_the_first_buckets():
    """V2: a frontend-only FIRST bucket must not let a later backend bucket through
    unprobed — which is exactly what 'one cheap command from the first bucket' did."""
    b1 = bucket("g1", required_tests=["tests/forge-watch/run.sh"])
    b2 = bucket("g2", required_tests=["bash tests/forge-bridge/run.sh"])
    deal = {"schema": q.DEAL_SCHEMA, "buckets": [b1, b2], "queues": {"S1": ["g1", "g2"]}}
    assert set(q.queue_toolchains(deal, "S1")) == {"bash"}


def _no_rung_deal():
    return {"schema": q.DEAL_SCHEMA, "buckets": [bucket("g1", required_tests=[])],
            "queues": {"S1": ["g1"]}}


def test_readiness_flags_the_hardcoded_database_fallback():
    """M6: an unseeded worktree does NOT fail to start — it resolves the hardcoded
    default. A zero exit is not proof of readiness."""
    saved = q.DB_FALLBACK_MARKERS
    q.DB_FALLBACK_MARKERS = [r"localhost:5433"]
    try:
        with tmpdir() as root:
            fails = q.run_readiness(
                _no_rung_deal(), "S1", root,
                db_probe="echo postgresql+asyncpg://u@localhost:5433/example_app")
            assert any("hardcoded-fallback marker" in f for f in fails), fails
    finally:
        q.DB_FALLBACK_MARKERS = saved


def test_readiness_accepts_a_correctly_seeded_database_url():
    """The paired POSITIVE case, and the reason the check is negative-only: the
    healthy value is `…@localhost:5432/example_app` with NO `_test` substring,
    because conftest derives the `_test` name by .replace() at import. Any positive
    assertion on `_test` would refuse every healthy worktree."""
    saved = q.DB_FALLBACK_MARKERS
    q.DB_FALLBACK_MARKERS = [r"localhost:5433"]
    try:
        with tmpdir() as root:
            fails = q.run_readiness(
                _no_rung_deal(), "S1", root,
                db_probe="echo postgresql+asyncpg://u@localhost:5432/example_app")
            assert fails == [], fails
    finally:
        q.DB_FALLBACK_MARKERS = saved


def test_readiness_flags_an_import_resolving_outside_the_worktree():
    """M7: a symlinked venv carries an editable-install .pth pointing at the PRIMARY
    checkout. Tests importing through it verify code this session never changed."""
    with tmpdir() as root:
        wt = os.path.join(root, "wt"); os.makedirs(wt)
        outside = os.path.join(root, "primary", "backend", "src", "app", "__init__.py")
        os.makedirs(os.path.dirname(outside), exist_ok=True); open(outside, "w").close()
        fails = q.run_readiness(_no_rung_deal(), "S1", wt, import_probe=f"echo {outside}")
        assert any("OUTSIDE this worktree" in f for f in fails), fails


def test_readiness_accepts_an_import_resolving_inside_the_worktree():
    """The paired POSITIVE case: the healthy configuration must NOT be refused."""
    with tmpdir() as root:
        wt = os.path.join(root, "wt"); os.makedirs(wt)
        inside = os.path.join(wt, "backend", "src", "app", "__init__.py")
        os.makedirs(os.path.dirname(inside), exist_ok=True); open(inside, "w").close()
        assert q.run_readiness(_no_rung_deal(), "S1", wt, import_probe=f"echo {inside}") == []


def test_readiness_reports_a_missing_toolchain_with_the_seed_remediation():
    deal = {"schema": q.DEAL_SCHEMA,
            "buckets": [bucket("g1", required_tests=["cd backend && pytest -q"])],
            "queues": {"S1": ["g1"]}}
    saved = q.READINESS_RUNGS
    q.READINESS_RUNGS = {"python": {"match": r"\bpytest\b", "probe": "exit 3"}}
    try:
        fails = q.run_readiness(deal, "S1", ".")
        assert len(fails) == 1 and "forge.worktree.seed" in fails[0]
    finally:
        q.READINESS_RUNGS = saved


def test_operator_next_is_never_actionable():
    """forge-toolkit's extra BLOCKING_LABEL. A live-gate issue can only be closed by
    the operator running the gate, so the runner must never pick one up — tagging it
    for the operator lane must not also feed it to the fix pipeline."""
    assert not q.is_actionable(issue(90, FF, "component:recover", "severity:high",
                                     "operator-next"))
    assert q.queue_for("recover", [issue(90, FF, "component:recover",
                                         "severity:high", "operator-next")]) == []


def test_in_progress_makes_queue_empty():
    """Executable proof of success criterion 2: once approval labels the covered
    issues, a second tab's actionable queue is EMPTY, so it cannot produce a rival
    grouping even if the guard were bypassed."""
    issues = [issue(50, FF, "component:bridge", "severity:critical"),
              issue(51, FF, "component:bridge", "severity:high")]
    assert len(q.queue_for("bridge", issues)) == 2
    labelled = [dict(i, labels=i["labels"] + [{"name": "in-progress"}]) for i in issues]
    assert q.queue_for("bridge", labelled) == [] and q.worst_first(labelled) == []


def test_label_move_and_reconcile_ship_together():
    """The plan is explicit: the label move alone turns a crashed session into
    permanently non-actionable issues. If a future change removes `reconcile` while
    approval-time labelling stays, THIS fails."""
    src = pathlib.Path(q.__file__).read_text()
    assert "def reconcile(" in src and "def apply_reconcile(" in src
    assert "def claim_liveness(" in src, "claim expiry is part of reconcile's contract"
    skill = pathlib.Path(q.__file__).resolve().parents[1] / "SKILL.md"
    if skill.exists():
        assert "reconcile" in skill.read_text(), \
            "SKILL.md must document the reconcile escape hatch alongside the label move"


def test_infra_free_only_for_allowlisted_commands():
    saved = q.INFRA_FREE_TEST_PATTERNS
    q.INFRA_FREE_TEST_PATTERNS = [
        r"^\s*(cd\s+frontend\s*&&\s*)?npm run test:run\s*$",
        r"^\s*(cd\s+frontend\s*&&\s*)?npx tsc -b --noEmit\s*$"]
    try:
        assert q.infra_required([]) is True and q.infra_required(None) is True
        assert q.infra_required(["cd frontend && npm run test:run"]) is False
        assert q.infra_required(["npx tsc -b --noEmit"]) is False
        assert q.infra_required(["pytest tests/test_hearing_ready_section_contracts.py -v"]) is True
        assert q.infra_required(["npm run test:run", "pytest -q"]) is True
        assert q.infra_required(["npm run test:run"], forced=True) is True
    finally:
        q.INFRA_FREE_TEST_PATTERNS = saved


def test_infra_free_rejects_compound_commands():
    """V5: a `\\b` terminator classified `npm run test:run && pytest ...` as infra-free
    — the wrong-False this lever must never produce."""
    saved = q.INFRA_FREE_TEST_PATTERNS
    q.INFRA_FREE_TEST_PATTERNS = [r"^\s*(cd\s+frontend\s*&&\s*)?npm run test:run\s*$"]
    try:
        for cmd in ("npm run test:run && pytest tests/ -v",
                    "npm run test:run; pytest tests/ -v",
                    "npm run test:run | tee out",
                    "npm run test:run --reporter=json"):
            assert q.infra_required([cmd]) is True, cmd
    finally:
        q.INFRA_FREE_TEST_PATTERNS = saved


def test_template_ships_an_empty_infra_free_allowlist():
    """A project earns entries by declaring them; the template never guesses."""
    tmpl = pathlib.Path.home() / (
        "sirtheoracle/automation/forge-toolkit/skills/forge-fix-runner/scripts/queue.py")
    if not tmpl.exists():
        return
    assert re.search(r"^INFRA_FREE_TEST_PATTERNS: list\[str\] = \[\]$",
                     tmpl.read_text(), re.M)


CONFIG_END = "# " + "─" * 74


def test_matches_template_outside_config_block():
    """A project copy must match the template BELOW the PER-PROJECT CONFIG block.

    Scoped deliberately: each project's module DOCSTRING is per-project prose by
    design and sits OUTSIDE the CONFIG markers, so a naive 'everything outside the
    block' comparison is red on day one in all three projects. Skips when the template
    is absent, or when this IS the template.
    """
    here = pathlib.Path(q.__file__).resolve().parent
    tmpl = pathlib.Path.home() / (
        "sirtheoracle/automation/forge-toolkit/skills/forge-fix-runner/scripts/queue.py")
    if not tmpl.exists() or tmpl.resolve() == (here / "queue.py").resolve():
        return
    def tail(p):
        text = pathlib.Path(p).read_text()
        return text[text.rindex(CONFIG_END):]
    assert tail(here / "queue.py") == tail(tmpl), (
        "this project's queue.py has drifted from the template BELOW the PER-PROJECT "
        "CONFIG block — only the CONFIG block and the module docstring may differ. "
        "Propagate the template change instead of forking.")


def test_template_lockstep_scope_excludes_the_docstring():
    """Negative guard on the test above: prove the comparison starts AFTER the CONFIG
    block, so nobody 'fixes' a failure by widening the scope to the whole file."""
    text = pathlib.Path(q.__file__).read_text()
    assert text.index('"""') < text.rindex(CONFIG_END), \
        "the module docstring must precede the CONFIG block for the scoping to hold"


def test_runner_collects_every_test_function():
    """Meta-test: a test the runner cannot call is a SILENT GAP, not a pass.

    The runner calls only zero-arg, module-level callables named test_*. Anything
    else — a test taking a fixture parameter, a test nested in a class, a helper
    mis-named test_* — would never run and never be reported. Assert the count the
    runner will execute equals the count of `def test_` in the source.

    LIMITATION, stated so nobody trusts it too far: this compares the source against
    itself. It cannot detect a test the SPECIFICATION promised and the source never
    declared. The Definition of Done carries the paired check for that.
    """
    import inspect
    declared = len(re.findall(r"^def (test_\w+)\s*\(", open(__file__).read(), re.M))
    collected = [v for k, v in globals().items()
                 if k.startswith("test_") and callable(v)
                 and not inspect.signature(v).parameters]
    assert declared == len(collected), (
        f"{declared} `def test_` in the file but the runner would call {len(collected)} "
        "— a test is uncollectable (takes parameters, or is nested in a class). Fix the "
        "test; do not loosen this check.")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = []
    for fn in fns:
        try:
            fn()
        except Exception as exc:                 # isolation: one failure must not
            failures.append((fn.__name__, exc))  # hide the other sixty-three
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok  {fn.__name__}")
    print(f"\n{len(fns) - len(failures)} passed, {len(failures)} failed")
    if failures:
        print("failed: " + ", ".join(n for n, _ in failures))
        sys.exit(1)
