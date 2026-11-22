#!/usr/bin/env python3

"""
check_tracked_paths.py --- refuses tracked symlinks and tracked local agent folders

A tracked symlink to a local, ignored folder replaces that folder on checkout, and git deletes
ignored files without asking; this once destroyed a working folder of agent notes. The repository
needs no symlinks, so any tracked one is refused, as is anything under the local agent folders.

Contains:
    SYMLINK_MODE: the git index mode of a symlink
    LOCAL_ONLY_PREFIXES: folders that hold local agent state and are never tracked
    find_violations(): lists the tracked entries that break either rule
    parse_index(): reads `git ls-files -s -z` output into (mode, path) pairs
    main(): runs git, prints each violation as an E: line, exits non-zero on any
"""

import subprocess
import sys
from collections.abc import Iterable

SYMLINK_MODE = "120000"
LOCAL_ONLY_PREFIXES = (".superpowers/", ".claude/")
LS_FILES = ("git", "ls-files", "-s", "-z")


def find_violations(entries: Iterable[tuple[str, str]]) -> list[str]:
    """Lists the tracked entries that are symlinks or sit under a local-only folder.

    Args:
        entries: (mode, path) pairs from the git index.

    Returns:
        violations: One message per offending path, in index order.
    """
    violations = []
    for mode, path in entries:
        if mode == SYMLINK_MODE:
            violations.append(f"tracked symlink: {path}")
        elif path.startswith(LOCAL_ONLY_PREFIXES):
            violations.append(f"tracked local agent file: {path}")
    return violations


def parse_index(output: str) -> list[tuple[str, str]]:
    """Reads NUL-separated `git ls-files -s` records into (mode, path) pairs.

    Args:
        output: Raw output of `git ls-files -s -z`.

    Returns:
        entries: (mode, path) for each record.
    """
    entries = []
    for record in output.split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        entries.append((meta.split(" ", 1)[0], path))
    return entries


def main() -> int:
    """Runs git, prints each violation as an E: line and returns the exit code."""
    output = subprocess.run(LS_FILES, check=True, capture_output=True, text=True).stdout
    violations = find_violations(parse_index(output))
    for message in violations:
        print(f"E: {message}", file=sys.stderr)
    if violations:
        print("E: fix: git rm --cached <path>", file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
