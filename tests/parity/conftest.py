#!/usr/bin/env python3

"""
conftest.py --- marks every parity test and gives them the oracle comparison of an area

The default pytest run deselects the `parity` mark; `nox -s parity -- <area>` runs the
area's tests/parity/test_parity_<area>.py after the comparison.

Contains:
    pytest_collection_modifyitems(): adds the `parity` mark to every test under tests/parity
    parity_area(): fixture that runs an area on both sides once per session and returns the run
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from scripts.build_oracle import build_oracle
from tests.parity.cases import load_all_cases
from tests.parity.compare import load_flipped, load_intended
from tests.parity.run import FLIPPED_FILE, INTENDED_FILE, REPO_ROOT, AreaRun, run_area

PARITY_FOLDER = Path(__file__).resolve().parent


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Adds the `parity` mark to every collected test under tests/parity, before deselection."""
    for item in items:
        if item.path.is_relative_to(PARITY_FOLDER):
            item.add_marker(pytest.mark.parity)


@pytest.fixture(scope="session")
def parity_area(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Callable[[str], AreaRun]]:
    """Runs an area's cases under the oracle and the working tree, once per area per session.

    Builds the oracle from the repository root, where build_oracle expects to run.

    Returns:
        run_for: Area name to its AreaRun, whose `reports` map `<area>.<case>` to a report.
    """
    runs: dict[str, AreaRun] = {}
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(REPO_ROOT)
        oracle_python = build_oracle()
        flips = load_flipped(FLIPPED_FILE, load_all_cases())
        intended = load_intended(INTENDED_FILE, load_all_cases(), flips)

        def run_for(area: str) -> AreaRun:
            if area not in runs:
                work = tmp_path_factory.mktemp(area)
                runs[area] = run_area(area, oracle_python, work, flips, intended=intended)
            return runs[area]

        yield run_for
