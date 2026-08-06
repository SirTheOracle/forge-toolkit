"""Core tests for the forge-fix-runner queue predicate and ordering.

Run: python3 -m pytest test_queue.py   (or: python3 test_queue.py for a quick check)
These cover the load-bearing safety logic: the actionable predicate (the #81
hazard), exactly-one-service validation, worst-first ordering, and per-service
severity ordering.
"""
import queue as q


def issue(number, *labels, state="OPEN", title="t"):
    return {"number": number, "state": state, "title": title,
            "labels": [{"name": n} for n in labels]}


FF = "forge-fix"


def test_actionable_happy_path():
    assert q.is_actionable(issue(1, FF, "service:eval", "severity:critical"))


def test_needs_retest_excluded():
    # The live #81 hazard: forge-fix + needs-retest must NOT be actionable.
    assert not q.is_actionable(issue(81, FF, "service:plan", "severity:critical", "needs-retest"))


def test_in_progress_and_pr_open_excluded():
    assert not q.is_actionable(issue(2, FF, "service:eval", "severity:high", "in-progress"))
    assert not q.is_actionable(issue(3, FF, "service:eval", "severity:high", "fix-pr-open"))


def test_missing_forge_fix_excluded():
    assert not q.is_actionable(issue(4, "service:eval", "severity:critical"))


def test_closed_excluded():
    assert not q.is_actionable(issue(5, FF, "service:eval", "severity:low", state="CLOSED"))


def test_zero_or_multiple_service_labels_excluded():
    assert not q.is_actionable(issue(6, FF, "severity:critical"))  # none
    assert not q.is_actionable(issue(7, FF, "service:eval", "service:plan", "severity:critical"))  # two


def test_worst_first_orders_by_criticals():
    issues = [
        issue(10, FF, "service:plan", "severity:critical"),
        issue(11, FF, "service:plan", "severity:critical"),
        issue(12, FF, "service:eval", "severity:critical"),
        issue(13, FF, "service:eval", "severity:critical"),
        issue(14, FF, "service:eval", "severity:critical"),
        issue(15, FF, "service:eval", "severity:medium"),
        # non-actionable: must not inflate eval's count
        issue(16, FF, "service:eval", "severity:critical", "needs-retest"),
    ]
    order = [r["service"] for r in q.worst_first(issues)]
    assert order == ["eval", "plan"]
    eval_row = next(r for r in q.worst_first(issues) if r["service"] == "eval")
    assert eval_row["critical"] == 3  # the needs-retest one is excluded
    assert eval_row["medium"] == 1


def test_queue_for_severity_then_number():
    issues = [
        issue(20, FF, "service:eval", "severity:medium"),
        issue(21, FF, "service:eval", "severity:critical"),
        issue(19, FF, "service:eval", "severity:critical"),
        issue(22, FF, "service:eval", "severity:high"),
        issue(23, FF, "service:plan", "severity:critical"),  # other service
    ]
    got = [i["number"] for i in q.queue_for("eval", issues)]
    assert got == [19, 21, 22, 20]  # crit(19,21) -> high(22) -> medium(20)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
