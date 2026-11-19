#!/usr/bin/env python3

"""
test_noxfile.py --- tests that the nox gates fail loudly instead of passing on nothing

Contains:
    tolerated_exit_codes(): lists the pytest exit codes a nox session accepts besides 0
    test_fuzz_session_accepts_only_exit_code_zero: fails when fuzz tolerates "no tests collected"
    test_tolerated_exit_codes_sees_an_accepted_code: proves the scan can fail
    test_ci_matrix_matches_requires_python: keeps the CI Python matrix and the metadata in step
"""

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOXFILE = ROOT / "noxfile.py"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = ROOT / "pyproject.toml"
MATRIX_PYTHONS = re.compile(r"^\s*python:\s*\[(.*?)\]", re.MULTILINE)
FLOOR_SPEC = re.compile(r"^>=(3\.\d+)$")
PYTHON_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")
FUZZ_SESSION = "fuzz"
SUCCESS_CODES = "success_codes"


def tolerated_exit_codes(source: str, session_name: str) -> list[str]:
    """Lists the source text of every success_codes argument inside one nox session.

    Args:
        source: The text of a noxfile.
        session_name: The function name of the session to inspect.

    Returns:
        codes: One entry per success_codes keyword found, empty when the session accepts only 0.
    """
    session = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == session_name
    )
    return [
        ast.unparse(keyword.value)
        for node in ast.walk(session)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == SUCCESS_CODES
    ]


def test_fuzz_session_accepts_only_exit_code_zero() -> None:
    assert tolerated_exit_codes(NOXFILE.read_text(), FUZZ_SESSION) == []


def test_tolerated_exit_codes_sees_an_accepted_code() -> None:
    source = "def fuzz(session):\n    run('pytest', success_codes=[0, 5])\n"
    assert tolerated_exit_codes(source, "fuzz") == ["[0, 5]"]


def minor_of(version: str) -> int:
    """Extracts the minor number of a 3.x version string."""
    return int(version.split(".")[1])


def test_ci_matrix_matches_requires_python() -> None:
    match = MATRIX_PYTHONS.search(CI_WORKFLOW.read_text())
    assert match is not None, "ci.yml has no python matrix line"
    matrix = {item.strip().strip("\"'") for item in match.group(1).split(",")}
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    classified = {
        found.group(1)
        for classifier in project["classifiers"]
        if (found := PYTHON_CLASSIFIER.match(classifier))
    }
    floor = FLOOR_SPEC.match(project["requires-python"])
    assert floor is not None, "requires-python is not a >=3.x floor"
    assert matrix == classified
    assert all(minor_of(version) >= minor_of(floor.group(1)) for version in matrix)
    assert min(matrix, key=minor_of) == floor.group(1)
