#!/usr/bin/env python3

"""
test_parity_workflow.py --- checks the parity session and its pytest mark

Contains:
    test_parity_session_runs_the_harness_driver: `nox -s parity` runs tests.parity.run
    test_parity_session_is_not_a_default_session: the oracle build never runs by default
    test_default_pytest_run_deselects_the_parity_mark: `nox -s tests` skips tests/parity
"""

import ast
import tomllib

from tests.unit.tooling.test_noxfile import (
    NOXFILE,
    PYPROJECT,
    ROOT,
    module_constant,
    session_names,
)

PARITY_SESSION = "parity"


def test_parity_session_runs_the_harness_driver() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    assert "PARITY_MODULE" in session_names(source, PARITY_SESSION)
    module = module_constant(source, "PARITY_MODULE")
    assert (ROOT / f"{module.replace('.', '/')}.py").is_file()


def test_parity_session_is_not_a_default_session() -> None:
    tree = ast.parse(NOXFILE.read_text(encoding="utf-8"))
    defaults = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "nox.options.sessions"
    )
    assert PARITY_SESSION not in ast.literal_eval(defaults)


def test_default_pytest_run_deselects_the_parity_mark() -> None:
    options = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]
    addopts = options["ini_options"]["addopts"]
    selection = addopts[addopts.index("-m") + 1]
    assert "not parity" in selection.split(" and ")
    assert any(marker.startswith("parity:") for marker in options["ini_options"]["markers"])
