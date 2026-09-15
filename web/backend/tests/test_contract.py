"""The pinned executable graph, the child environment, and the capability probe."""

from __future__ import annotations

import os
import subprocess

import pytest

from forge_web.config import (
    AMBIENT_ROUTING,
    BIN_DIR_VAR,
    FORBIDDEN_IN_CHILDREN,
    MODELLED_BINS,
    PIN_ENV_BY_NAME,
    PINNED_BINS_VAR,
    ConfigError,
    child_env,
    load,
    parse_pinned_bins,
    probe_contract,
    recheck_contract,
)

PINNED = (
    "FORGE_BIN", "FORGE_FORGE_BIN", "FORGE_BRIDGE_BIN", "FORGE_WATCH_BIN",
    "FORGE_START_BIN", "FORGE_BROKER_BIN", "FORGE_CODEX_DOCTOR_BIN", "FORGE_TMUX_BIN",
)


def test_all_eight_variables_of_the_real_graph_are_pinned_to_this_checkout(checkout):
    cfg = checkout.cfg()
    env = child_env(cfg)
    for key in PINNED:
        assert key in env, "%s is unpinned; see the R7 table in config.py's docstring" % key
        if key == "FORGE_TMUX_BIN":
            assert env[key] == str(cfg.exe.tmux)
            continue
        assert env[key].startswith(str(cfg.checkout)), "%s escapes the checkout" % key
    # The two the previous document missed, named explicitly so a regression is unambiguous.
    assert env["FORGE_CODEX_DOCTOR_BIN"] == str(cfg.exe.forge)      # bin/forge-start:39
    assert env["FORGE_BROKER_BIN"] == str(cfg.exe.forge_broker)     # bin/forge-bridge:129
    # bin/forge-bridge:129 defaults the broker to the BARE WORD `forge-broker`, so the child
    # PATH must also put this checkout's bin/ first.
    assert env["PATH"].split(":")[0] == str(cfg.exe.bin_dir)


def test_ambient_routing_is_absent_by_construction(checkout, monkeypatch):
    for key in AMBIENT_ROUTING:
        monkeypatch.setenv(key, "leaked")
    env = child_env(checkout.cfg())
    for key in AMBIENT_ROUTING:
        assert key not in env, "%s leaked into a web child" % key


def test_identity_enforce_is_pinned_on_even_when_the_server_inherits_observe(checkout, monkeypatch):
    # Review R3: the new root/ID/incarnation comparisons live inside this legacy switch, so an
    # inherited observe configuration would silently disable a precondition we requested.
    monkeypatch.setenv("FORGE_IDENTITY_ENFORCE", "0")
    assert child_env(checkout.cfg())["FORGE_IDENTITY_ENFORCE"] == "1"


def test_forbidden_variables_are_never_inherited(checkout, monkeypatch):
    for key in FORBIDDEN_IN_CHILDREN:
        monkeypatch.setenv(key, "0")
    env = child_env(checkout.cfg())
    for key in FORBIDDEN_IN_CHILDREN:
        if key == "FORGE_IDENTITY_ENFORCE":
            assert env[key] == "1"
        else:
            assert key not in env


def test_permitted_test_overrides_pass_only_with_the_server_side_switch(checkout, monkeypatch):
    monkeypatch.setenv("FORGE_WATCH_CACHE_DIR", "/tmp/isolated-cache")
    checkout.env["FORGE_WATCH_CACHE_DIR"] = "/tmp/isolated-cache"
    assert child_env(checkout.cfg())["FORGE_WATCH_CACHE_DIR"] == "/tmp/isolated-cache"
    checkout.env["FORGE_WEB_ALLOW_TEST_OVERRIDES"] = "0"
    assert "FORGE_WATCH_CACHE_DIR" not in child_env(checkout.cfg())


def test_tmux_seam_disagreement_is_fatal_at_load(checkout):
    # bin/forge-bridge and bin/forge-watch invoke bare `tmux` and IGNORE FORGE_TMUX_BIN, so a
    # different tmux earlier on the child PATH would split discovery from delivery.
    shadow = checkout.root / "bin" / "tmux"
    shadow.write_text("#!/bin/bash\nexit 0\n")
    shadow.chmod(0o755)
    with pytest.raises(ConfigError) as excinfo:
        checkout.cfg()
    assert "tmux seam disagreement" in str(excinfo.value)


def test_host_must_be_the_literal_loopback_address(checkout):
    checkout.env["FORGE_WEB_HOST"] = "0.0.0.0"
    with pytest.raises(ConfigError):
        checkout.cfg()


def test_contract_probe_requires_the_exact_marker_from_both_clis(checkout):
    cfg = checkout.cfg()
    assert probe_contract(cfg).ok
    # An older forge-bridge falls through to `help|*` and exits 0 with HELP on stdout.
    bridge = checkout.root / "bin" / "forge-bridge"
    bridge.write_text("#!/bin/bash\necho 'usage: forge-bridge ...'\nexit 0\n")
    bridge.chmod(0o755)
    status = probe_contract(cfg)
    assert not status.ok and "forge-bridge" in status.detail


def test_probe_rejects_a_near_miss_marker(checkout):
    forge = checkout.root / "bin" / "forge"
    forge.write_text("#!/bin/bash\necho forge-web-contract/0\nexit 0\n")
    forge.chmod(0o755)
    assert not probe_contract(checkout.cfg()).ok


def test_recheck_skips_the_subprocess_until_a_fingerprint_changes(checkout):
    cfg = checkout.cfg()
    first = probe_contract(cfg)
    before = len(checkout.calls())
    assert recheck_contract(cfg, first) is first
    assert len(checkout.calls()) == before, "an unchanged fingerprint must not re-probe"
    # Changing a SIBLING binary mid-session must force a fresh probe.
    broker = checkout.root / "bin" / "forge-broker"
    broker.write_text(broker.read_text() + "\n# changed\n")
    again = recheck_contract(cfg, first)
    assert again is not first
    assert len(checkout.calls()) > before


def test_the_fingerprint_covers_the_advertised_set_plus_tmux_not_a_hardcoded_count(checkout):
    """The drift-proof assertion. A hardcoded count of seven would have to be edited every
    time the launcher grows a binary — and an un-edited count is a test that passes while the
    thing it guards is wrong."""
    cfg = checkout.cfg()
    assert set(cfg.exe.fingerprint()) == set(cfg.exe.advertised) | {"tmux"}
    # codex-doctor is NOT a key: bin/forge-start:39 gives it its own VARIABLE, but it is the
    # same FILE as FORGE_BIN, and fingerprinting one inode twice proves nothing.
    assert "codex-doctor" not in cfg.exe.fingerprint()
    assert cfg.exe.codex_doctor == cfg.exe.forge


def test_a_vanished_binary_fingerprints_as_a_change_not_a_crash(checkout):
    cfg = checkout.cfg()
    before = cfg.exe.fingerprint()
    os.remove(cfg.exe.forge_broker)
    after = cfg.exe.fingerprint()
    assert after["forge-broker"] == (-1, -1, -1, -1, "")
    assert after != before


# ── the launcher's list is the only list (Revision 1) ────────────────────────


def test_the_pinned_set_comes_from_the_launcher_not_a_hardcoded_list(checkout):
    """Add a binary to the launcher's export and it must appear in the model — WITHOUT any
    edit to a second list. `forge-issue-sync` is a real executable in bin/ that is not in
    install.sh's BIN_SCRIPTS, so it is a realistic future addition."""
    extra = checkout.root / "bin" / "forge-issue-sync"
    extra.write_text("#!/bin/bash\nexit 0\n")
    extra.chmod(0o755)
    checkout.env[PINNED_BINS_VAR] += " forge-issue-sync"
    # It is advertised but not modelled, so the backend refuses to start and SAYS WHAT TO DO.
    with pytest.raises(ConfigError) as excinfo:
        checkout.cfg()
    assert "forge-issue-sync" in str(excinfo.value)
    assert "PIN_ENV_BY_NAME" in str(excinfo.value)


def test_a_binary_the_launcher_advertises_but_we_do_not_model_is_fatal(checkout):
    """THE DIRECTION THAT MATTERS. A silent fallback here would let the shell half grow a
    nested binary while the backend kept launching children without pinning it."""
    stray = checkout.root / "bin" / "forge-new-thing"
    stray.write_text("#!/bin/bash\nexit 0\n")
    stray.chmod(0o755)
    checkout.env[PINNED_BINS_VAR] += " forge-new-thing"
    with pytest.raises(ConfigError) as excinfo:
        checkout.cfg()
    assert "does not model" in str(excinfo.value)


def test_a_binary_we_model_but_the_launcher_does_not_advertise_is_fatal(checkout):
    """The reverse drift: a narrowed launcher. bin/forge-bridge:129 defaults the broker to a
    BARE WORD, so dropping forge-broker would silently reintroduce PATH selection."""
    checkout.env[PINNED_BINS_VAR] = "forge forge-bridge forge-watch forge-start"
    with pytest.raises(ConfigError) as excinfo:
        checkout.cfg()
    assert "forge-broker" in str(excinfo.value)
    assert "bare word" in str(excinfo.value)


def test_a_missing_pinned_bins_export_is_a_hard_failure_not_a_fallback(checkout):
    del checkout.env[PINNED_BINS_VAR]
    with pytest.raises(ConfigError) as excinfo:
        checkout.cfg()
    assert PINNED_BINS_VAR in str(excinfo.value)
    assert "no fallback" in str(excinfo.value)


def test_a_missing_bin_dir_export_is_a_hard_failure(checkout):
    del checkout.env[BIN_DIR_VAR]
    with pytest.raises(ConfigError) as excinfo:
        checkout.cfg()
    assert BIN_DIR_VAR in str(excinfo.value)


def test_a_bin_dir_that_is_not_this_checkouts_bin_is_refused(checkout, tmp_path):
    other = tmp_path / "other-bin"
    other.mkdir()
    checkout.env[BIN_DIR_VAR] = str(other)
    with pytest.raises(ConfigError) as excinfo:
        checkout.cfg()
    assert "disagree about which checkout" in str(excinfo.value)


@pytest.mark.parametrize(
    "raw",
    [
        "",                                   # empty
        "   ",                                # whitespace only
        "/abs/path/forge",                    # a path, not a basename
        "bin/forge",                          # a relative path
        "forge forge",                        # a duplicate
        "forge ../escape",                    # traversal
        "forge fo\x00rge",                    # NUL
        "forge .hidden",                      # dot-leading
    ],
)
def test_malformed_pinned_bins_values_are_refused(raw):
    with pytest.raises(ConfigError):
        parse_pinned_bins(raw)


def test_both_separator_spellings_parse_to_the_same_list():
    space = parse_pinned_bins("forge forge-bridge forge-watch")
    colon = parse_pinned_bins("forge:forge-bridge:forge-watch")
    assert space == colon == ("forge", "forge-bridge", "forge-watch")


def test_every_advertised_binary_has_at_least_one_pinned_env_var(checkout):
    """The backend half of the shell half's T25-2 lockstep.

    T25-2 asserts every FORGE_*_BIN read by any bin/ executable appears in the launcher's
    exec block. This asserts the launcher's advertised list is fully covered by
    PIN_ENV_BY_NAME and that every one of those variables actually reaches a child. Together
    the two cannot disagree silently."""
    cfg = checkout.cfg()
    env = child_env(cfg)
    for name, path in cfg.exe.binaries:
        variables = PIN_ENV_BY_NAME[name]
        assert variables, "%s is advertised but pins no variable" % name
        for var in variables:
            assert env[var] == str(path), "%s must point at %s" % (var, name)
    assert set(PIN_ENV_BY_NAME) == MODELLED_BINS


def test_the_child_env_values_are_unchanged_by_the_table_refactor(checkout):
    """Revision 1 changed WHERE the list comes from, not WHAT is emitted. These eight are the
    R7 graph, spelled out once more so a regression in pinned_env() is caught by value and not
    only by construction."""
    cfg = checkout.cfg()
    env = child_env(cfg)
    expected = {
        "FORGE_BIN": cfg.exe.forge,
        "FORGE_FORGE_BIN": cfg.exe.forge,
        "FORGE_CODEX_DOCTOR_BIN": cfg.exe.forge,
        "FORGE_BRIDGE_BIN": cfg.exe.forge_bridge,
        "FORGE_WATCH_BIN": cfg.exe.forge_watch,
        "FORGE_START_BIN": cfg.exe.forge_start,
        "FORGE_BROKER_BIN": cfg.exe.forge_broker,
        "FORGE_TMUX_BIN": cfg.exe.tmux,
    }
    for var, path in expected.items():
        assert env[var] == str(path)


def test_a_stub_refuses_an_unpinned_environment(checkout):
    """The stubs exit 97 without FORGE_WEB_CHILD, which is what makes every other call-site
    assertion in this suite meaningful rather than decorative."""
    cfg = checkout.cfg()
    bare = {"PATH": os.environ["PATH"], "FORGE_STUB_LOG": checkout.env["FORGE_STUB_LOG"]}
    result = subprocess.run([str(cfg.exe.forge), "board", "--json"], env=bare,
                            capture_output=True, text=True)
    assert result.returncode == 97