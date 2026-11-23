#!/usr/bin/env python3

"""
test_check_tracked_paths.py --- tests the gate that refuses tracked symlinks and agent folders

Contains:
    test_regression_tracked_workspace_symlink_is_refused: the link that destroyed agent notes
    test_tracked_file_under_a_local_agent_folder_is_refused: .superpowers/ and .claude/ files
    test_ordinary_files_pass: regular tracked files raise nothing
    test_parse_index_reads_mode_and_path: keeps paths with spaces and tabs after the first
"""

from scripts.check_tracked_paths import find_violations, parse_index

WORKSPACE_LINK = ".superpowers/sdd/2026-11-19-phase-2-foundations"


def test_regression_tracked_workspace_symlink_is_refused() -> None:
    assert find_violations([("120000", WORKSPACE_LINK)]) == [f"tracked symlink: {WORKSPACE_LINK}"]
    assert find_violations([("120000", "docs/link")]) == ["tracked symlink: docs/link"]


def test_tracked_file_under_a_local_agent_folder_is_refused() -> None:
    entries = [("100644", ".superpowers/sdd/notes.md"), ("100644", ".claude/settings.json")]
    assert find_violations(entries) == [
        "tracked local agent file: .superpowers/sdd/notes.md",
        "tracked local agent file: .claude/settings.json",
    ]


def test_ordinary_files_pass() -> None:
    entries = [("100644", "e8s/__init__.py"), ("100755", "scripts/ci_changes.py")]
    assert find_violations(entries) == []


def test_parse_index_reads_mode_and_path() -> None:
    output = f"120000 {'a' * 40} 0\t{WORKSPACE_LINK}\x00100644 {'b' * 40} 0\tdocs/a b.md\x00"
    assert parse_index(output) == [("120000", WORKSPACE_LINK), ("100644", "docs/a b.md")]
