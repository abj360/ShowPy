#!/usr/bin/env python3

"""
ledger_plugin.py --- enforces the defect ledger: every defect test is a strict xfail on its ID

Contains:
    DEFECT_MARKER: the marker name ledger tests carry
    OPEN_COUNT_KEY: config stash key holding the selected defect count
    WORKER_COUNT_KEY: pytest-xdist worker output key carrying a worker's selected defect count
    CRASHED_WORKERS_KEY: config stash key holding the workers that died without sending a count
    XdistGateway: the part of a pytest-xdist gateway that names its worker
    XdistWorker: the part of a pytest-xdist worker node the controller reads
    LEDGER_USAGE: the hint appended to every ledger violation
    DEFECT_ID_PATTERN: the shape a ledger ID must have
    find_problem(): names what is wrong with a defect test's markers
    pytest_configure(): registers the defect marker
    pytest_collection_modifyitems(): rejects rule-breaking defect tests
    pytest_collection_finish(): counts the defect tests still selected
    pytest_sessionfinish(): sends a worker's count, and fails the run on a crashed worker
    pytest_testnodedown(): records a worker's count on the controller, or that it crashed
    pytest_terminal_summary(): prints the Ledger: N open line, or why the count is unknown
"""

import re
from typing import Protocol

import pytest

DEFECT_MARKER = "defect"
DEFECT_ID_PATTERN = re.compile(r"^UP-(\d+(\.\d+)?|E)-\d{2,3}$")
OPEN_COUNT_KEY = pytest.StashKey[int]()
WORKER_COUNT_KEY = "ledger_open_count"
CRASHED_WORKERS_KEY = pytest.StashKey[tuple[str, ...]]()
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


class XdistGateway(Protocol):
    """Names the pytest-xdist worker behind a gateway.

    Attributes:
        id: The worker's ID, such as gw3.
    """

    id: str


class XdistWorker(Protocol):
    """Exposes a pytest-xdist worker node to the controller.

    A worker that died sends nothing back, so its node has no workeroutput attribute.

    Attributes:
        config: The controller's pytest configuration.
        gateway: The execnet gateway that ran the worker.
    """

    config: pytest.Config
    gateway: XdistGateway


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Sends a worker's defect count to the controller, and fails the run if a worker crashed.

    A crash between tests fails no test report, so the controller sets the exit status itself.
    """
    workeroutput: dict[str, object] | None = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput[WORKER_COUNT_KEY] = session.config.stash.get(OPEN_COUNT_KEY, 0)
        return
    if (
        session.config.stash.get(CRASHED_WORKERS_KEY, ())
        and session.exitstatus == pytest.ExitCode.OK
    ):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: XdistWorker) -> None:
    """Records a worker's count on the controller, which collects no tests under pytest-xdist.

    Every worker collects the whole selection, so each one reports the same count. A worker that
    died sent no count; it is recorded as crashed so the summary never prints a partial count.
    """
    workeroutput: dict[str, object] | None = getattr(node, "workeroutput", None)
    count = None if workeroutput is None else workeroutput.get(WORKER_COUNT_KEY)
    stash = node.config.stash
    if not isinstance(count, int):
        stash[CRASHED_WORKERS_KEY] = (*stash.get(CRASHED_WORKERS_KEY, ()), node.gateway.id)
        return
    stash[OPEN_COUNT_KEY] = max(stash.get(OPEN_COUNT_KEY, 0), count)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Prints the Ledger: N open line, or Ledger: unknown when a worker died without a count."""
    stash = terminalreporter.config.stash
    crashed_workers = stash.get(CRASHED_WORKERS_KEY, ())
    if crashed_workers:
        names = ", ".join(crashed_workers)
        terminalreporter.write_line(f"Ledger: unknown (worker {names} crashed)", red=True)
        return
    terminalreporter.write_line(f"Ledger: {stash.get(OPEN_COUNT_KEY, 0)} open")
