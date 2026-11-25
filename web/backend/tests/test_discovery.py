"""Inventory, labelling, the spawn union, and tmux-failure-versus-empty-fleet."""

from __future__ import annotations

import os

import yaml

from forge_web.discovery import (
    collect,
    label_for,
    list_sessions,
    read_registry,
    read_watch_roots,
    spawn_roots,
    unowned_roots,
)


def make_root(tmp_path, name):
    root = tmp_path / name
    (root / ".dev").mkdir(parents=True)
    return root


def test_idle_custom_named_unregistered_and_partial_sessions_are_all_inventoried(checkout, tmp_path):
    a = make_root(tmp_path, "alpha")
    b = make_root(tmp_path, "beta")
    partial = tmp_path / "gamma"
    partial.mkdir()                                       # NO .dev/ — partial startup
    checkout.set_sessions([
        ("forge-alpha", str(a), "$0", "1700000000"),
        ("weird name", str(b), "$1650", "1700000001"),
        ("forge-gamma", str(partial), "$2", "1700000002"),
    ])
    sessions, state, _ = list_sessions(checkout.cfg())
    assert state == "ok" and len(sessions) == 3
    assert [s.session_id for s in sessions] == ["$0", "$1650", "$2"]
    assert [s.has_dev for s in sessions] == [True, True, False]


def test_the_sentinel_session_never_becomes_a_card(checkout, tmp_path):
    root = make_root(tmp_path, "a")
    checkout.set_sessions([("__nosession__", str(root), "$0", "1")])
    sessions, _state, _ = list_sessions(checkout.cfg())
    assert sessions == []


def test_tmux_failure_is_not_an_empty_fleet(checkout, monkeypatch):
    monkeypatch.setenv("FAKE_TMUX_RC", "1")
    checkout.env["FAKE_TMUX_RC"] = "1"
    sessions, state, detail = list_sessions(checkout.cfg())
    assert sessions == [] and state == "unavailable" and detail


def test_no_server_running_IS_an_empty_fleet(checkout, tmp_path, monkeypatch):
    stub = checkout.tools / "tmux"
    stub.write_text("#!/bin/bash\necho 'no server running on /tmp/tmux-501/default' >&2\nexit 1\n")
    stub.chmod(0o755)
    sessions, state, _ = list_sessions(checkout.cfg())
    assert sessions == [] and state == "ok"


def test_the_alias_labels_a_root_only_on_exact_realpath_equality(tmp_path):
    repo = tmp_path / "repo"
    sibling = tmp_path / "repo-worktree"
    for path in (repo, sibling):
        (path / ".dev").mkdir(parents=True)
    registry = tmp_path / "registry.yml"
    registry.write_text(yaml.safe_dump({
        "schema": "cc-registry/1",
        "repos": {str(repo / ".git"): {"alias": "myrepo", "path": str(repo),
                                       "repo_id": str(repo / ".git"), "path_exists": True}},
    }))
    entries, state, _ = read_registry(registry)
    assert state == "ok"
    assert label_for(str(repo), entries)[:2] == ("myrepo", "registry_alias")
    label, source, alias, _ = label_for(str(sibling), entries)
    assert (label, source) == ("repo-worktree", "root_basename")
    assert alias is None or alias == "myrepo"     # repository metadata only, never the label


def test_a_corrupt_registry_degrades_instead_of_throwing(tmp_path):
    registry = tmp_path / "registry.yml"
    registry.write_text("{ this is not: [valid")
    entries, state, detail = read_registry(registry)
    assert entries == {} and state == "malformed" and detail


def test_a_foreign_yaml_map_is_not_adopted_as_a_registry(tmp_path):
    registry = tmp_path / "registry.yml"
    registry.write_text(yaml.safe_dump({"project": {"name": "MyProject"}}))
    entries, state, _ = read_registry(registry)
    assert entries == {} and state == "malformed"


def test_spawn_offers_the_union_deduplicated_by_physical_root(checkout, tmp_path, monkeypatch):
    registered = make_root(tmp_path, "registered")
    live_only = make_root(tmp_path, "live-only")
    home = tmp_path / "home"
    (home / ".config" / "forge").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "forge" / "registry.yml").write_text(yaml.safe_dump({
        "schema": "cc-registry/1",
        "repos": {"id": {"alias": "reg", "path": str(registered), "repo_id": "id",
                         "path_exists": True}},
    }))
    (home / ".config" / "forge" / "watch-roots").write_text("%s\n" % registered)
    monkeypatch.setenv("HOME", str(home))
    checkout.set_sessions([("forge-live", str(live_only), "$0", "1")])
    inventory = collect(checkout.cfg(), env={"HOME": str(home)})
    roots = spawn_roots(inventory)
    assert str(registered) in roots and str(live_only) in roots
    assert len(roots) == len(set(roots))


def test_registry_membership_never_establishes_liveness(tmp_path):
    from forge_web.discovery import Inventory, RepoEntry

    gone = tmp_path / "gone"
    inventory = Inventory(
        sessions=[],
        registry={str(gone): RepoEntry("id", "alias", str(gone), True)},
    )
    assert spawn_roots(inventory) == [], "a registered but nonexistent root is not offered"


def test_watch_roots_comments_and_blanks_are_ignored(tmp_path):
    path = tmp_path / "watch-roots"
    path.write_text("# a comment\n\n%s\n" % tmp_path)
    assert read_watch_roots(path) == [os.path.realpath(str(tmp_path))]


def test_board_roots_without_a_live_session_become_notices(tmp_path):
    from forge_web.discovery import Inventory, LiveSession

    live = str(tmp_path / "live")
    inventory = Inventory(sessions=[LiveSession("n", live, "$0", "1", True)])
    assert unowned_roots(inventory, [live, str(tmp_path / "orphan")]) == [
        os.path.realpath(str(tmp_path / "orphan"))
    ]