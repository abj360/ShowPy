#!/usr/bin/env python3

"""
test_parity_workflow.py --- checks the parity session, its pytest mark and its two CI jobs

Contains:
    test_parity_session_runs_the_harness_driver: `nox -s parity` runs tests.parity.run
    test_parity_session_is_not_a_default_session: the oracle build never runs by default
    test_default_pytest_run_deselects_the_parity_mark: `nox -s tests` skips tests/parity
    test_parity_job_runs_every_check_cell_under_three_seeds: 6 cells, seeds 0 to 2, digests
    test_parity_job_reads_its_own_changes_answer: the legs gate on the `parity` output
    test_parity_oracle_cache_is_keyed_by_ref_platform_python_and_lock: and saved from main only
    test_parity_compare_requires_18_equal_digests: fails on a missing or differing digest
    test_parity_jobs_pin_every_action_and_keep_no_credentials: SHA pins, no persisted token
"""

import ast
import tomllib

from scripts.build_oracle import DEFAULT_OUT, DEFAULT_REF
from tests.unit.tooling.test_noxfile import (
    CI_WORKFLOW,
    ENV_INDENT,
    MATRIX_JOB_CONDITION,
    NEEDS_CHANGES,
    NOXFILE,
    PYPROJECT,
    ROOT,
    SAVE_FROM_MAIN,
    WITH_INDENT,
    ci_job,
    job_condition,
    job_steps,
    job_value,
    module_constant,
    session_names,
    step_value,
    unpinned_actions,
)

PARITY_JOB = "parity"
COMPARE_JOB = "parity-compare"
CHECK_JOB = "check"
CHANGES_JOB = "changes"
PARITY_SESSION = "parity"
RUNS_PARITY = "needs.changes.result != 'success' || needs.changes.outputs.parity != 'false'"
PARITY_STEP_ENV = f"${{{{ {RUNS_PARITY} }}}}"
COMPARE_CONDITION = f"${{{{ !cancelled() && ({RUNS_PARITY}) }}}}"
PARITY_OUTPUT = "parity: ${{ steps.classify.outputs.parity }}"
SEED_LOOP = "for seed in 0 1 2; do"
SEED_RUN = 'PYTHONHASHSEED="$seed" uv run --locked nox -s parity -- all'
CACHE_KEY_PARTS = (
    f"parity-oracle-{DEFAULT_REF}-",
    "${{ runner.os }}",
    "${{ runner.arch }}",
    "steps.python.outputs.version",
    "hashFiles('uv.lock', 'scripts/build_oracle.py')",
)
DIGEST_COUNT = "-ne 18"


def _workflow() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


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


def test_parity_job_runs_every_check_cell_under_three_seeds() -> None:
    workflow = _workflow()
    job, check = ci_job(workflow, PARITY_JOB), ci_job(workflow, CHECK_JOB)
    for key in ("os", "python"):
        assert job_value(job, key, ENV_INDENT + 2) == job_value(check, key, ENV_INDENT + 2)
    run = next(step for step in job_steps(job) if SEED_LOOP in step)
    assert SEED_RUN in run
    assert "--digest" in run
    assert any("actions/upload-artifact@" in step for step in job_steps(job))


def test_parity_job_reads_its_own_changes_answer() -> None:
    workflow = _workflow()
    job = ci_job(workflow, PARITY_JOB)
    assert NEEDS_CHANGES.search(job)
    assert job_condition(job) == MATRIX_JOB_CONDITION
    assert job_value(job, "RUN_PARITY_JOB", ENV_INDENT) == PARITY_STEP_ENV
    gates = [step_value(step, "if") for step in job_steps(job)]
    assert gates
    assert all(gate.startswith("env.RUN_PARITY_JOB ") for gate in gates)
    assert PARITY_OUTPUT in ci_job(workflow, CHANGES_JOB)


def test_parity_oracle_cache_is_keyed_by_ref_platform_python_and_lock() -> None:
    steps = job_steps(ci_job(_workflow(), PARITY_JOB))
    restore = next(step for step in steps if "actions/cache/restore@" in step)
    save = next(step for step in steps if "actions/cache/save@" in step)
    assert step_value(restore, "path", WITH_INDENT) == DEFAULT_OUT.as_posix()
    assert step_value(save, "path", WITH_INDENT) == DEFAULT_OUT.as_posix()
    key = step_value(restore, "key", WITH_INDENT)
    assert [part for part in CACHE_KEY_PARTS if part not in key] == []
    assert SAVE_FROM_MAIN in step_value(save, "if")
    assert steps.index(restore) < steps.index(save)


def test_parity_compare_requires_18_equal_digests() -> None:
    job = ci_job(_workflow(), COMPARE_JOB)
    assert job_value(job, "needs", 4) == "[changes, parity]"
    assert job_condition(job) == COMPARE_CONDITION
    steps = job_steps(job)
    assert any("actions/download-artifact@" in step for step in steps)
    check = next(step for step in steps if DIGEST_COUNT in step)
    assert "cmp -s" in check
    assert "exit 1" in check


def test_parity_jobs_pin_every_action_and_keep_no_credentials() -> None:
    workflow = _workflow()
    for name in (PARITY_JOB, COMPARE_JOB):
        job = ci_job(workflow, name)
        assert unpinned_actions(job) == []
        checkouts = job.count("actions/checkout@")
        assert job.count("persist-credentials: false") == checkouts
