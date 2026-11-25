"""Bounded reads, exact paths, the regex fallback, and the objective excerpt."""

from __future__ import annotations

import os

import pytest
import yaml

from forge_web.readers import (
    STRUCTURED_MAX,
    context_for,
    objective_excerpt,
    read_log_entries,
    read_text,
    read_yaml,
    session_file,
    slugs_in,
)


def test_paths_are_built_from_the_session_name_never_a_glob(project_root):
    assert session_file(str(project_root), "forge-x", "forge-context.%s.yml").endswith(
        "/.dev/forge-context.forge-x.yml"
    )


@pytest.mark.parametrize("name", ["__nosession__", "..", "a/b", "x\x00y", "forge-x.bak-1", ""])
def test_the_sentinel_legacy_and_backup_spellings_are_refused(project_root, name):
    with pytest.raises(ValueError):
        session_file(str(project_root), name, "forge-context.%s.yml")


def test_a_bare_legacy_context_file_is_unreachable(project_root):
    (project_root / ".dev" / "forge-context.yml").write_text("active_pipeline: legacy\n")
    result = context_for(str(project_root), "forge-x")
    assert result.state == "absent", "the bare file has no live session name and is not read"


def test_an_oversized_source_is_diagnosed_not_parsed(project_root):
    path = project_root / ".dev" / "forge-context.forge-x.yml"
    path.write_text("k: v\n" + "#" * (STRUCTURED_MAX + 10))
    result = read_yaml(str(path))
    assert result.state == "oversized" and result.data is None
    assert str(STRUCTURED_MAX) in result.detail


def test_malformed_yaml_degrades_with_a_diagnostic(project_root):
    path = project_root / ".dev" / "forge-context.forge-x.yml"
    path.write_text("a: [1, 2\nb: {\n")
    result = read_yaml(str(path))
    assert result.state == "malformed" and result.data is None


def test_a_non_regular_source_is_refused_without_opening_it(project_root):
    fifo = project_root / ".dev" / "forge-status.forge-x.md"
    os.mkfifo(fifo)
    result = read_text(str(fifo))
    assert result.state == "malformed" and "not a regular file" in result.detail


def test_a_get_never_modifies_a_source(project_root):
    path = project_root / ".dev" / "forge-status.forge-x.md"
    path.write_text("# status\n")
    before = path.stat()
    read_text(str(path))
    after = path.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)


def test_the_regex_fallback_survives_a_legacy_files_dash_log(project_root):
    """A real corruption class: legacy logs contain `files:-`, which makes yaml.safe_load
    throw for the WHOLE file. Without the fallback an entire project's stage projection
    silently becomes empty."""
    path = project_root / ".dev" / "proposals" / "slug" / "forge-log.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'entries:\n'
        '  - timestamp: "2026-09-14T10:00:00Z"\n'
        '    stage: coding\n'
        '    to: codex-a\n'
        '    session: forge-x\n'
        '    response: null\n'
        '    files:-\n'
        '  - timestamp: "2026-09-14T11:00:00Z"\n'
        '    stage: coding\n'
        '    session: forge-x\n'
        '    response: "done"\n'
    )
    with pytest.raises(Exception):
        yaml.safe_load(path.read_text())          # proves the fixture really is malformed
    entries, clean, result = read_log_entries(str(project_root), "slug")
    assert result.state == "ok" and clean is True
    assert len(entries) == 2
    assert entries[0]["response"] is None and entries[1]["response"] is not None
    assert entries[0]["stage"] == "coding" and entries[0]["session"] == "forge-x"


def test_an_unclassifiable_block_makes_the_log_non_authoritative(project_root):
    path = project_root / ".dev" / "proposals" / "slug" / "forge-log.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'entries:\n'
        '  - timestamp: "2026-09-14T10:00:00Z"\n'
        '    stage: coding\n'
        '    files:-\n'                            # no response: line at all
    )
    _entries, clean, _result = read_log_entries(str(project_root), "slug")
    assert clean is False


def test_slugs_come_from_a_directory_listing_not_a_recursive_crawl(project_root):
    for slug in ("a", "b"):
        path = project_root / ".dev" / "proposals" / slug / "forge-log.yml"
        path.parent.mkdir(parents=True)
        path.write_text("entries: []\n")
    (project_root / ".dev" / "proposals" / "no-log").mkdir()
    assert slugs_in(str(project_root)) == ["a", "b"]


def test_the_objective_excerpt_is_bounded_substantive_plain_text(project_root):
    path = project_root / ".dev" / "proposals" / "slug" / "problem-statement.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# Problem Statement: Web Control Center\n\n"
        "Suggested slug: web-control-center\n"
        "Problem type: feature\n\n"
        "## The Problem\n\n"
        "Forge is operated entirely from `terminals` and there is **no** single place to see\n"
        "what every session is working on.\n\n"
        "## Relevant Files\n\n- bin/forge\n"
    )
    text, source = objective_excerpt(str(project_root), "slug")
    assert text is not None
    assert text.startswith("Forge is operated entirely from terminals")
    assert "`" not in text and "**" not in text and "Relevant" not in text
    assert len(text) <= 401
    assert "problem-statement.md" in source


def test_a_missing_problem_statement_is_labelled_not_invented(project_root):
    text, source = objective_excerpt(str(project_root), "nope")
    assert text is None and "absent" in source