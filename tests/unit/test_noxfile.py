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
    ci_job_names(): lists the job names under the `jobs:` key of a workflow
    job_value(): returns one key's value in a job, joining a folded `>-` value into one line
    job_condition(): returns the job-level `if:` expression of one job
    ungated_jobs(): lists the jobs that do not wait for and obey the changes job
    test_ci_jobs_are_changes_lint_types_or_gated: every new CI job must be classed here
    test_job_value_joins_a_folded_value: reads plain, folded and missing keys
    test_ci_job_names_skip_trigger_keys: proves the job lister ignores `on:` keys
    test_heavy_jobs_wait_for_the_changes_job: every heavy job needs changes and reads its output
    test_ungated_jobs_sees_a_job_without_the_gate: proves the gate check can fail
    test_single_jobs_skip_only_on_a_successful_docs_only_answer: the fail-closed job condition
    test_matrix_job_gates_its_steps_not_itself: each matrix job's legs always start and expand
    test_lint_types_runs_on_every_change: lint-types waits for nothing and is never skipped
    test_changes_job_runs_the_classifier_on_full_history: the job diffs with the script
    session_strings(): lists the string literals a nox session's body holds
    test_slow_session_runs_on_xdist_workers: the slow session passes -n SLOW_WORKERS to pytest
    test_session_strings_sees_only_the_session: proves the literal scan can fail
    pytest_runs(): lists the arguments of every pytest run one nox session makes
    last_value(): returns the value after the last occurrence of a flag in a run
    test_slow_session_splits_on_the_timing_mark: runs the non-timing slow tests, then the timing
        ones
    test_slow_session_runs_timing_tests_without_workers: the timing run ends with `-n 0`
    test_slow_session_runs_the_other_slow_tests_on_workers: the other run keeps xdist workers
    test_serial_sessions_start_no_workers: tests and hashseeds never pass -n, nor does addopts
    test_pytest_runs_resolves_constants_and_keeps_posargs: proves the run scan resolves names
    test_last_value_reads_the_last_occurrence: proves the flag lookup obeys the last value
"""

import ast
import itertools
import re
import tomllib
from pathlib import Path

import pytest

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
SLOW_SESSION = "slow"
WORKERS_FLAG = "-n"
WORKERS_CONSTANT = "SLOW_WORKERS"
NO_WORKERS = "0"
MARK_FLAG = "-m"
PARALLEL_MARKS = "slow and not timing"
TIMING_MARKS = "slow and timing"
SERIAL_SESSIONS = ("tests", "hashseeds")
UV_RUN = "_uv_run"
PYTEST = "pytest"
WHEEL_SESSION = "wheel"
EXTRAS_CONSTANT = "ALL_EXTRAS"
CHECK_SCRIPT_CONSTANT = "CORE_CHECK_SCRIPT"
LINUX_RUNNER = re.compile(r"^\s*runs-on:\s*ubuntu-", re.MULTILINE)
JOB_HEADER = re.compile(r"^  (\S+):\s*$", re.MULTILINE)
JOBS_KEY = "\njobs:\n"
FOLDED = ">-"
JOB_INDENT = 4
ENV_INDENT = 6
STEP_START = re.compile(r"^      - ", re.MULTILINE)
CHANGES_JOB = "changes"
UNGATED_JOB = "lint-types"
MATRIX_JOBS = ("check", "slow-cross")
GATED_JOBS = ("check", "hashseed", "slow", "slow-cross", "lowest", "wheel", "core", "docker")
NEEDS_CHANGES = re.compile(r"^    needs: changes$", re.MULTILINE)
RUNS_CODE_JOBS = "needs.changes.result != 'success' || needs.changes.outputs.code != 'false'"
SINGLE_JOB_CONDITION = f"${{{{ !cancelled() && ({RUNS_CODE_JOBS}) }}}}"
MATRIX_JOB_CONDITION = "${{ !cancelled() }}"
MATRIX_STEP_ENV = f"${{{{ {RUNS_CODE_JOBS} }}}}"
MATRIX_STEP_GATE = re.compile(
    r"^(      - |        )if: env\.RUN_CODE_JOBS [!=]= 'true'$", re.MULTILINE
)


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


def ci_job_names(workflow: str) -> list[str]:
    """Lists the job names under the `jobs:` key of a workflow, in file order."""
    jobs = workflow[workflow.index(JOBS_KEY) :]
    return [header.group(1) for header in JOB_HEADER.finditer(jobs)]


def job_value(job: str, key: str, indent: int) -> str | None:
    """Returns one key's value in a job, joining a folded `>-` value into one line.

    Args:
        job: The text of one job.
        key: The mapping key to look up.
        indent: The number of spaces before the key.

    Returns:
        value: The key's value with folded lines joined by single spaces, None when absent.
    """
    lines = job.splitlines()
    prefix = f"{' ' * indent}{key}: "
    deeper = " " * (indent + 1)
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        value = line.removeprefix(prefix)
        if value != FOLDED:
            return value
        folded = itertools.takewhile(lambda part: part.startswith(deeper), lines[index + 1 :])
        return " ".join(part.strip() for part in folded)
    return None


def job_condition(job: str) -> str | None:
    """Returns the job-level `if:` expression of one job's text, or None when it has none."""
    return job_value(job, "if", JOB_INDENT)


def ungated_jobs(workflow: str, jobs: tuple[str, ...]) -> list[str]:
    """Lists the jobs that do not both need the changes job and read its fail-closed answer.

    Args:
        workflow: The text of a workflow file.
        jobs: The names of the jobs that must be gated.

    Returns:
        ungated: The names among jobs whose text lacks the dependency or the gate expression.
    """
    ungated = []
    for name in jobs:
        job = ci_job(workflow, name)
        gates = (job_condition(job), job_value(job, "RUN_CODE_JOBS", ENV_INDENT))
        if not NEEDS_CHANGES.search(job) or not any(
            RUNS_CODE_JOBS in (gate or "") for gate in gates
        ):
            ungated.append(name)
    return ungated


def test_ci_jobs_are_changes_lint_types_or_gated() -> None:
    names = ci_job_names(CI_WORKFLOW.read_text())
    assert names[0] == CHANGES_JOB
    assert sorted(names) == sorted([CHANGES_JOB, UNGATED_JOB, *GATED_JOBS])


def test_job_value_joins_a_folded_value() -> None:
    job = "  slow:\n    if: >-\n      ${{ a ||\n      b }}\n    runs-on: x\n"
    assert job_condition(job) == "${{ a || b }}"
    assert job_value(job, "runs-on", JOB_INDENT) == "x"
    assert job_value(job, "needs", JOB_INDENT) is None


def test_ci_job_names_skip_trigger_keys() -> None:
    workflow = "on:\n  push:\n  pull_request:\n\njobs:\n  changes:\n    runs-on: x\n"
    assert ci_job_names(workflow) == ["changes"]


def test_heavy_jobs_wait_for_the_changes_job() -> None:
    assert ungated_jobs(CI_WORKFLOW.read_text(), GATED_JOBS) == []


def test_ungated_jobs_sees_a_job_without_the_gate() -> None:
    workflow = (
        "jobs:\n"
        "  slow:\n    needs: changes\n    runs-on: x\n"
        f"  wheel:\n    needs: changes\n    if: ${{{{ {RUNS_CODE_JOBS} }}}}\n"
        f"  core:\n    if: ${{{{ {RUNS_CODE_JOBS} }}}}\n"
    )
    assert ungated_jobs(workflow, ("slow", "wheel", "core")) == ["slow", "core"]


def test_single_jobs_skip_only_on_a_successful_docs_only_answer() -> None:
    workflow = CI_WORKFLOW.read_text()
    for name in GATED_JOBS:
        if name not in MATRIX_JOBS:
            assert job_condition(ci_job(workflow, name)) == SINGLE_JOB_CONDITION, name


@pytest.mark.parametrize("name", MATRIX_JOBS)
def test_matrix_job_gates_its_steps_not_itself(name: str) -> None:
    job = ci_job(CI_WORKFLOW.read_text(), name)
    assert job_condition(job) == MATRIX_JOB_CONDITION
    assert job_value(job, "RUN_CODE_JOBS", ENV_INDENT) == MATRIX_STEP_ENV
    steps = STEP_START.split(job)[1:]
    assert steps
    assert all(MATRIX_STEP_GATE.search(f"      - {step}") for step in steps)


def test_lint_types_runs_on_every_change() -> None:
    job = ci_job(CI_WORKFLOW.read_text(), UNGATED_JOB)
    assert job
    assert "    needs:" not in job
    assert job_condition(job) is None


def test_changes_job_runs_the_classifier_on_full_history() -> None:
    job = ci_job(CI_WORKFLOW.read_text(), CHANGES_JOB)
    assert "fetch-depth: 0" in job
    assert "code: ${{ steps.classify.outputs.code }}" in job
    assert "run: python3 scripts/ci_changes.py" in job
    assert job_condition(job) is None


def session_strings(source: str, session_name: str) -> list[str]:
    """Lists the string literals a nox session's body holds.

    Args:
        source: The text of a noxfile.
        session_name: The function name of the session to inspect.

    Returns:
        strings: Every str constant inside the session, docstring included.
    """
    session = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == session_name
    )
    return [
        node.value
        for node in ast.walk(session)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_slow_session_runs_on_xdist_workers() -> None:
    source = NOXFILE.read_text()
    assert WORKERS_FLAG in session_strings(source, SLOW_SESSION)
    assert WORKERS_CONSTANT in session_names(source, SLOW_SESSION)
    workers = module_constant(source, WORKERS_CONSTANT)
    assert workers == "auto" or int(workers) > 1


def test_session_strings_sees_only_the_session() -> None:
    source = "X = '-n'\n\ndef slow(session):\n    run('pytest', '-m', 'slow')\n"
    assert session_strings(source, "slow") == ["pytest", "-m", "slow"]


def _argument_text(source: str, argument: ast.expr) -> str:
    """Returns a call argument as a literal, a resolved module constant or `*` plus its source."""
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    if isinstance(argument, ast.Name):
        return module_constant(source, argument.id)
    return ast.unparse(argument)


def pytest_runs(source: str, session_name: str) -> list[list[str]]:
    """Lists the arguments of every pytest run one nox session makes through _uv_run.

    Args:
        source: The text of a noxfile.
        session_name: The function name of the session to inspect.

    Returns:
        runs: Per pytest call in source order, its arguments from "pytest" on, with module
            constants resolved and starred arguments kept as source text.
    """
    session = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == session_name
    )
    calls = [
        node
        for node in ast.walk(session)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == UV_RUN
    ]
    runs = [[_argument_text(source, argument) for argument in call.args[1:]] for call in calls]
    return [run for run in runs if run[:1] == [PYTEST]]


def last_value(run: list[str], flag: str) -> str | None:
    """Returns the value after the last occurrence of `flag` in a run, the one pytest obeys."""
    values = [value for option, value in itertools.pairwise(run) if option == flag]
    return values[-1] if values else None


def test_slow_session_splits_on_the_timing_mark() -> None:
    runs = pytest_runs(NOXFILE.read_text(), SLOW_SESSION)
    assert [last_value(run, MARK_FLAG) for run in runs] == [PARALLEL_MARKS, TIMING_MARKS]


def test_slow_session_runs_timing_tests_without_workers() -> None:
    """Ends the timing run with `-n 0`, after the posargs, so no posarg can give it workers."""
    runs = pytest_runs(NOXFILE.read_text(), SLOW_SESSION)
    timing = [run for run in runs if last_value(run, MARK_FLAG) == TIMING_MARKS]
    assert timing
    assert all(run[-2:] == [WORKERS_FLAG, NO_WORKERS] for run in timing)


def test_slow_session_runs_the_other_slow_tests_on_workers() -> None:
    runs = pytest_runs(NOXFILE.read_text(), SLOW_SESSION)
    parallel = [run for run in runs if last_value(run, MARK_FLAG) == PARALLEL_MARKS]
    assert parallel
    for run in parallel:
        workers = last_value(run, WORKERS_FLAG) or NO_WORKERS
        assert workers == "auto" or int(workers) > 1


def test_serial_sessions_start_no_workers() -> None:
    """Keeps the timing tests of the default suite in one process in every full-suite gate."""
    source = NOXFILE.read_text()
    for name in SERIAL_SESSIONS:
        runs = pytest_runs(source, name)
        assert runs, name
        assert all(WORKERS_FLAG not in run for run in runs), name
    addopts = tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]["addopts"]
    assert WORKERS_FLAG not in addopts


def test_pytest_runs_resolves_constants_and_keeps_posargs() -> None:
    source = (
        "MARKS = 'slow and timing'\n\n"
        "def slow(session):\n"
        "    _uv_run(session, 'python', '-c', 'pass')\n"
        "    _uv_run(session, 'pytest', '-m', MARKS, *session.posargs, '-n', '0')\n"
    )
    assert pytest_runs(source, "slow") == [
        ["pytest", "-m", "slow and timing", "*session.posargs", "-n", "0"]
    ]


def test_last_value_reads_the_last_occurrence() -> None:
    assert last_value(["pytest", "-n", "4", "*args", "-n", "0"], "-n") == "0"
    assert last_value(["pytest", "-m", "slow"], "-n") is None
