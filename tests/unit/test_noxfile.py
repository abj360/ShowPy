#!/usr/bin/env python3

"""
test_noxfile.py --- tests that the nox gates fail loudly instead of passing on nothing

Contains:
    tolerated_exit_codes(): lists the pytest exit codes a nox session accepts besides 0
    test_fuzz_session_accepts_only_exit_code_zero: fails when fuzz tolerates "no tests collected"
    test_tolerated_exit_codes_sees_an_accepted_code: proves the scan can fail
"""

import ast
from pathlib import Path

NOXFILE = Path(__file__).resolve().parents[2] / "noxfile.py"
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
