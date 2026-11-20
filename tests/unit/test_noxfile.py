#!/usr/bin/env python3

"""
test_noxfile.py --- tests that the nox gates fail loudly instead of passing on nothing

Contains:
    tolerated_exit_codes(): lists the pytest exit codes a nox session accepts besides 0
    test_fuzz_session_accepts_only_exit_code_zero: fails when fuzz tolerates "no tests collected"
    test_tolerated_exit_codes_sees_an_accepted_code: proves the scan can fail
    default_exit_codes(): lists the default of every success_codes parameter of one function
    test_uv_run_default_accepts_only_exit_code_zero: fails when the _uv_run default tolerates codes
    test_default_exit_codes_sees_an_accepted_default: proves the default scan can fail
    test_ci_matrix_matches_requires_python: keeps the CI Python matrix and the metadata in step
    session_names(): lists the names a nox session's body reads
    ci_job(): returns the text of one job of the CI workflow
    module_constant(): returns the string value of one module-level noxfile constant
    test_core_session_installs_no_extra: the core session never reads ALL_EXTRAS
    test_wheel_session_installs_every_extra: the wheel session installs the `all` extra
    test_core_check_script_exists: the core session's check script is in the repository
    test_ci_runs_the_core_session_on_linux: CI runs `nox -s core` on an Ubuntu runner
    test_ci_job_is_empty_for_a_missing_job: proves the job lookup can fail
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
CORE_SESSION = "core"
WHEEL_SESSION = "wheel"
EXTRAS_CONSTANT = "ALL_EXTRAS"
CHECK_SCRIPT_CONSTANT = "CORE_CHECK_SCRIPT"
LINUX_RUNNER = re.compile(r"^\s*runs-on:\s*ubuntu-", re.MULTILINE)
JOB_HEADER = re.compile(r"^  (\S+):\s*$", re.MULTILINE)


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


def default_exit_codes(source: str, function_name: str) -> list[str]:
    """Lists the source text of each non-None default of a success_codes parameter.

    Args:
        source: The text of a noxfile.
        function_name: The function whose signature to inspect.

    Returns:
        defaults: One entry per success_codes default other than None.
    """
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    arguments = function.args
    pairs = [
        *zip(reversed(arguments.args), reversed(arguments.defaults), strict=False),
        *(
            (arg, default)
            for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
            if default is not None
        ),
    ]
    return [
        ast.unparse(default)
        for arg, default in pairs
        if arg.arg == SUCCESS_CODES and ast.unparse(default) != "None"
    ]


def test_uv_run_default_accepts_only_exit_code_zero() -> None:
    assert default_exit_codes(NOXFILE.read_text(), "_uv_run") == []


def test_default_exit_codes_sees_an_accepted_default() -> None:
    source = "def _uv_run(session, *args, success_codes=[0, 5]):\n    pass\n"
    assert default_exit_codes(source, "_uv_run") == ["[0, 5]"]


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


def session_names(source: str, session_name: str) -> set[str]:
    """Lists the names a nox session's body reads.

    Args:
        source: The text of a noxfile.
        session_name: The function name of the session to inspect.

    Returns:
        names: Every bare name loaded inside the session.
    """
    session = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == session_name
    )
    return {node.id for node in ast.walk(session) if isinstance(node, ast.Name)}


def ci_job(workflow: str, job: str) -> str:
    """Returns the text of one top-level job of a workflow, empty when the job is missing."""
    headers = list(JOB_HEADER.finditer(workflow))
    for index, header in enumerate(headers):
        if header.group(1) == job:
            end = headers[index + 1].start() if index + 1 < len(headers) else len(workflow)
            return workflow[header.start() : end]
    return ""


def module_constant(source: str, name: str) -> str:
    """Returns the string value of one module-level constant of a noxfile."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == name:
            value = ast.literal_eval(node.value)
            assert isinstance(value, str)
            return value
    raise AssertionError(f"noxfile defines no {name}")


def test_core_session_installs_no_extra() -> None:
    assert EXTRAS_CONSTANT not in session_names(NOXFILE.read_text(), CORE_SESSION)


def test_wheel_session_installs_every_extra() -> None:
    source = NOXFILE.read_text()
    assert EXTRAS_CONSTANT in session_names(source, WHEEL_SESSION)
    assert module_constant(source, EXTRAS_CONSTANT) == "[all]"


def test_core_check_script_exists() -> None:
    assert (ROOT / module_constant(NOXFILE.read_text(), CHECK_SCRIPT_CONSTANT)).is_file()


def test_ci_runs_the_core_session_on_linux() -> None:
    job = ci_job(CI_WORKFLOW.read_text(), CORE_SESSION)
    assert LINUX_RUNNER.search(job)
    assert "nox -s core" in job


def test_ci_job_is_empty_for_a_missing_job() -> None:
    assert ci_job("jobs:\n  core:\n    runs-on: ubuntu-24.04\n", "wheel") == ""
