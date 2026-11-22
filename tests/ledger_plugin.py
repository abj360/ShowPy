#!/usr/bin/env python3

"""
ledger_plugin.py --- enforces the defect ledger: every defect test is a strict xfail on its ID

Contains:
    DEFECT_MARKER: the marker name ledger tests carry
    OPEN_COUNT_KEY: config stash key holding the selected defect count
    WORKER_COUNT_KEY: pytest-xdist worker output key carrying a worker's selected defect count
    XdistWorker: the part of a pytest-xdist worker node the controller reads
    LEDGER_USAGE: the hint appended to every ledger violation
    DEFECT_ID_PATTERN: the shape a ledger ID must have
    find_problem(): names what is wrong with a defect test's markers
    pytest_configure(): registers the defect marker
    pytest_collection_modifyitems(): rejects rule-breaking defect tests
    pytest_collection_finish(): counts the defect tests still selected
    pytest_sessionfinish(): sends a pytest-xdist worker's count to the controller
    pytest_testnodedown(): records a finished pytest-xdist worker's count on the controller
    pytest_terminal_summary(): prints the Ledger: N open line
"""

import re
from typing import Protocol

import pytest

DEFECT_MARKER = "defect"
DEFECT_ID_PATTERN = re.compile(r"^UP-(\d+(\.\d+)?|E)-\d{2,3}$")
OPEN_COUNT_KEY = pytest.StashKey[int]()
WORKER_COUNT_KEY = "ledger_open_count"
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


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Rejects defect tests that break the ledger rules."""
    problems = [
        f"{item.nodeid}: {problem}" for item in items if (problem := find_problem(item)) is not None
    ]
    if problems:
        raise pytest.UsageError("\n".join([*problems, LEDGER_USAGE]))


def pytest_collection_finish(session: pytest.Session) -> None:
    """Counts the defect tests still selected after -m and -k deselection."""
    session.config.stash[OPEN_COUNT_KEY] = sum(
        1 for item in session.items if item.get_closest_marker(DEFECT_MARKER) is not None
    )


class XdistWorker(Protocol):
    """Exposes what a finished pytest-xdist worker sent back to the controller.

    Attributes:
        config: The controller's pytest configuration.
        workeroutput: Values the worker stored in its config.workeroutput.
    """

    config: pytest.Config
    workeroutput: dict[str, object]


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Sends a pytest-xdist worker's selected defect count to the controller."""
    workeroutput: dict[str, object] | None = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput[WORKER_COUNT_KEY] = session.config.stash.get(OPEN_COUNT_KEY, 0)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: XdistWorker) -> None:
    """Records a worker's count on the controller, which collects no tests under pytest-xdist.

    Every worker collects the whole selection, so each one reports the same count.
    """
    count = node.workeroutput.get(WORKER_COUNT_KEY)
    if isinstance(count, int):
        stash = node.config.stash
        stash[OPEN_COUNT_KEY] = max(stash.get(OPEN_COUNT_KEY, 0), count)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Prints the Ledger: N open line."""
    open_count = terminalreporter.config.stash.get(OPEN_COUNT_KEY, 0)
    terminalreporter.write_line(f"Ledger: {open_count} open")
