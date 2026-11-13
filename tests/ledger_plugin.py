#!/usr/bin/env python3

"""
ledger_plugin.py --- enforces the defect ledger: every defect test is a strict xfail on its ID

Contains:
    DEFECT_ID_PATTERN: the shape a ledger ID must have
    find_problem(): names what is wrong with a defect test's markers
    pytest_configure(): registers the defect marker
    pytest_collection_modifyitems(): rejects rule-breaking defect tests, counts open ones
    pytest_terminal_summary(): prints the Ledger: N open line
"""

import re

import pytest

DEFECT_MARKER = "defect"
DEFECT_ID_PATTERN = re.compile(r"^UP-(\d+(\.\d+)?)-\d{2}$")
OPEN_COUNT_KEY = pytest.StashKey[int]()
LEDGER_USAGE = 'use @pytest.mark.defect("UP-<section>-<nn>") with xfail(strict=True, reason=<id>)'


def pytest_configure(config: pytest.Config) -> None:
    """Registers the defect marker."""
    config.addinivalue_line("markers", "defect(id): open audited defect, a strict xfail on its ID")


def find_problem(item: pytest.Item) -> str | None:
    """Returns what is wrong with a defect test's markers, or None when they are valid."""
    marker = item.get_closest_marker(DEFECT_MARKER)
    if marker is None:
        return None
    if len(marker.args) != 1 or not isinstance(marker.args[0], str):
        return "defect marker takes exactly one string ID"
    defect_id = marker.args[0]
    if not DEFECT_ID_PATTERN.match(defect_id):
        return f"defect ID {defect_id!r} does not match UP-<section>-<nn>"
    xfail = item.get_closest_marker("xfail")
    if xfail is None or xfail.kwargs.get("strict") is not True:
        return f"defect {defect_id} needs xfail(strict=True)"
    if xfail.kwargs.get("reason") != defect_id:
        return f"defect {defect_id} needs xfail reason equal to the ID {defect_id!r}"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Rejects defect tests that break the ledger rules and counts the open ones."""
    problems = [
        f"{item.nodeid}: {problem}" for item in items if (problem := find_problem(item)) is not None
    ]
    if problems:
        raise pytest.UsageError("\n".join([*problems, LEDGER_USAGE]))
    config.stash[OPEN_COUNT_KEY] = sum(
        1 for item in items if item.get_closest_marker(DEFECT_MARKER) is not None
    )


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Prints the Ledger: N open line."""
    open_count = terminalreporter.config.stash.get(OPEN_COUNT_KEY, 0)
    terminalreporter.write_line(f"Ledger: {open_count} open")
