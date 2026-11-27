#!/usr/bin/env python3

"""
test_nightly_failures.py --- checks that a nightly failure hides no later check and gets an issue

Contains:
    test_regression_nightly_runs_every_check_after_an_earlier_failure: tests, slow, fuzz and
        bench each run unless the run is cancelled or uv sync failed, and no step tolerates its
        own failure
    workflow_name(): returns the top-level `name:` of one workflow
    step_script(): returns the literal block of one step's `run: |` script
    job_permissions(): reads one job's permissions, comments dropped
    test_nightly_alert_runs_when_a_nightly_completes_on_main: both nightly workflows by name,
        on main only, for a failure or a success
    test_nightly_alert_keeps_the_workflow_conventions: read-only by default, issue writes on the
        job only, SHA pins, no checkout, a timeout and the job's own token
    test_regression_nightly_alert_passes_run_data_only_through_env: no expression inside the
        script; every run field it reads comes from env
    test_step_script_reads_a_literal_block: proves the script reader can fail
    test_job_permissions_stop_at_the_next_key: proves the permissions reader can fail
    run_alert(): runs the alert script against a fake gh that logs each call
    test_nightly_alert_opens_an_issue_naming_the_run_and_failed_jobs: the first failure
    test_nightly_alert_comments_on_the_open_issue: a repeated failure opens no second issue
    test_nightly_alert_closes_the_open_issue_when_green_again: a later success closes it
    test_nightly_alert_leaves_a_green_run_without_an_issue_alone: a success with nothing open
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.tooling.test_noxfile import (
    ROOT,
    STEP_INDENT,
    ci_job,
    ci_job_names,
    job_condition,
    job_steps,
    job_value,
    step_value,
    unpinned_actions,
    workflow_section,
)

NIGHTLY_WORKFLOW = ROOT / ".github" / "workflows" / "nightly.yml"
NIGHTLY_CORE_WORKFLOW = ROOT / ".github" / "workflows" / "nightly-core.yml"
ALERT_WORKFLOW = ROOT / ".github" / "workflows" / "nightly-alert.yml"
NIGHTLY_JOB = "nightly"
CHECK_SESSIONS = ("tests", "slow", "fuzz", "bench")
NOX_SESSION = re.compile(r"\bnox -s (\w+)")
SYNCED = "${{ !cancelled() && steps.sync.outcome == 'success' }}"
SYNC_STEP = "id: sync\n        run: uv sync --locked\n"
NIGHTLY_JOB_NAME = "tests, slow, fuzz and large benchmarks"
ALERT_JOB = "alert"
ALERT_TRIGGERS = (
    "on:\n"
    "  workflow_run: # zizmor: ignore[dangerous-triggers]\n"
    "    workflows: [nightly, nightly-core]\n"
    "    types: [completed]\n"
    "    branches: [main]\n"
)
ALERT_CONDITION = (
    "${{ github.event.workflow_run.head_branch == 'main' && "
    "(github.event.workflow_run.conclusion == 'failure' || "
    "github.event.workflow_run.conclusion == 'success') }}"
)
READ_ONLY = "permissions:\n  contents: read\n"
ALERT_PERMISSIONS = {"actions": "read", "issues": "write"}
ALERT_TIMEOUT = "10"
JOB_KEY_INDENT = 4
PERMISSION_INDENT = 6
ENV_INDENT = STEP_INDENT + 2
BLOCK_INDENT = STEP_INDENT + 2
RUN_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "GH_REPO": "${{ github.repository }}",
    "LABEL": "nightly-failure",
    "WORKFLOW": "${{ github.event.workflow_run.name }}",
    "CONCLUSION": "${{ github.event.workflow_run.conclusion }}",
    "RUN_ID": "${{ github.event.workflow_run.id }}",
    "RUN_URL": "${{ github.event.workflow_run.html_url }}",
}
LITERAL_BLOCK = "|"
REPOSITORY = "abj360/e8s"
RUN_ID = "45993884219"
RUN_URL = f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}"
FAILED_JOB = f"- nightly (failure): {RUN_URL}/job/140488290636"
OPEN_ISSUE = "12"
FAKE_GH = """#!{python}
import json, os, sys
with open(os.environ["GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["issue", "list"]:
    print(os.environ["FAKE_OPEN_ISSUE"])
elif sys.argv[1] == "api":
    print(os.environ["FAKE_FAILED_JOBS"])
"""


def _nightly() -> str:
    return NIGHTLY_WORKFLOW.read_text(encoding="utf-8")


def _alert() -> str:
    return ALERT_WORKFLOW.read_text(encoding="utf-8")


def test_regression_nightly_runs_every_check_after_an_earlier_failure() -> None:
    """A failing fuzz step skipped the large benchmarks every night for five days."""
    workflow = _nightly()
    job = ci_job(workflow, NIGHTLY_JOB)
    assert job_value(job, "name", JOB_KEY_INDENT) == NIGHTLY_JOB_NAME
    steps = job_steps(job)
    sync = steps.index(SYNC_STEP)
    checks = {
        found[1]: step for step in steps if (found := NOX_SESSION.search(step_value(step, "run")))
    }
    assert tuple(checks) == CHECK_SESSIONS
    for session, step in checks.items():
        assert steps.index(step) > sync, session
        assert step_value(step, "if") == SYNCED, session
    assert "continue-on-error" not in workflow


def workflow_name(workflow: str) -> str:
    """Returns the top-level `name:` of one workflow's text."""
    return workflow_section(workflow, "name").removeprefix("name: ").strip()


def step_script(step: str) -> str:
    """Returns the literal block of one step's `run: |` script, dedented; empty when absent.

    Args:
        step: One step's text from job_steps.

    Returns:
        script: The script lines with the block's indentation removed, or "" when the step's
            `run:` is missing or not a literal block.
    """
    if step_value(step, "run") != LITERAL_BLOCK:
        return ""
    lines = step.split(f"\n{' ' * STEP_INDENT}run: |\n", 1)[1].splitlines()
    indent = " " * BLOCK_INDENT
    block = []
    for line in lines:
        if line and not line.startswith(indent):
            break
        block.append(line.removeprefix(indent))
    return "\n".join(block).rstrip() + "\n"


def job_permissions(job: str) -> dict[str, str]:
    """Reads the permissions block of one job's text, with trailing comments dropped.

    Args:
        job: The text of one job.

    Returns:
        permissions: Each scope mapped to its access level; empty when the job sets none.
    """
    header = f"\n{' ' * JOB_KEY_INDENT}permissions:\n"
    if header not in job:
        return {}
    permissions: dict[str, str] = {}
    prefix = " " * PERMISSION_INDENT
    for line in job.split(header, 1)[1].splitlines():
        if not line.startswith(prefix) or line.startswith(prefix + " "):
            break
        scope, access = line.split("#", 1)[0].strip().split(": ", 1)
        permissions[scope] = access
    return permissions


def test_nightly_alert_runs_when_a_nightly_completes_on_main() -> None:
    workflow = _alert()
    assert workflow_section(workflow, "on") == ALERT_TRIGGERS
    nightlies = (NIGHTLY_WORKFLOW, NIGHTLY_CORE_WORKFLOW)
    watched = [workflow_name(path.read_text(encoding="utf-8")) for path in nightlies]
    assert f"    workflows: [{', '.join(watched)}]\n" in ALERT_TRIGGERS
    assert ci_job_names(workflow) == [ALERT_JOB]
    assert job_condition(ci_job(workflow, ALERT_JOB)) == ALERT_CONDITION


def test_nightly_alert_keeps_the_workflow_conventions() -> None:
    workflow = _alert()
    job = ci_job(workflow, ALERT_JOB)
    assert workflow_section(workflow, "permissions") == READ_ONLY
    assert job_permissions(job) == ALERT_PERMISSIONS
    assert workflow.count("permissions:") == 2
    assert unpinned_actions(workflow) == []
    assert "uses: actions/checkout@" not in workflow
    assert job_value(job, "timeout-minutes", JOB_KEY_INDENT) == ALERT_TIMEOUT
    steps = job_steps(job)
    assert len(steps) == 1
    assert step_value(steps[0], "GH_TOKEN", ENV_INDENT) == RUN_ENV["GH_TOKEN"]


def test_regression_nightly_alert_passes_run_data_only_through_env() -> None:
    """Run data from workflow_run is untrusted; an expression in a script could inject code."""
    steps = job_steps(ci_job(_alert(), ALERT_JOB))
    scripts = [step_script(step) for step in steps]
    assert all(scripts)
    assert not any("${{" in script for script in scripts)
    assert {key: step_value(steps[0], key, ENV_INDENT) for key in RUN_ENV} == RUN_ENV
    assert all(f"${key}" in scripts[0] for key in RUN_ENV if key != "GH_TOKEN")


def test_step_script_reads_a_literal_block() -> None:
    step = (
        "name: x\n        env:\n          A: b\n"
        "        run: |\n          echo 1\n\n          echo 2\n"
    )
    assert step_script(step) == "echo 1\n\necho 2\n"
    assert step_script("name: x\n        run: echo ${{ github.ref }}\n") == ""
    assert step_script("uses: actions/cache@x # v1\n") == ""


def test_job_permissions_stop_at_the_next_key() -> None:
    job = (
        "  a:\n    permissions:\n      issues: write # why\n      actions: read\n"
        "    steps:\n      - run: x\n"
    )
    assert job_permissions(job) == {"issues": "write", "actions": "read"}
    assert job_permissions("  a:\n    steps:\n      - run: x\n") == {}


def run_alert(folder: Path, conclusion: str, open_issue: str) -> list[list[str]]:
    """Runs the alert script against a fake gh that logs each call and answers from env.

    Args:
        folder: An empty folder for the fake gh and its log.
        conclusion: The conclusion of the triggering run.
        open_issue: The number of the open nightly failure issue, or "" when none is open.

    Returns:
        calls: The arguments of each gh call, in order.
    """
    steps = job_steps(ci_job(_alert(), ALERT_JOB))
    gh = folder / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
    gh.chmod(0o755)
    log = folder / "gh.log"
    env = {
        **os.environ,
        "PATH": f"{folder}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(log),
        "FAKE_OPEN_ISSUE": open_issue,
        "FAKE_FAILED_JOBS": FAILED_JOB,
        "GH_REPO": REPOSITORY,
        "LABEL": step_value(steps[0], "LABEL", ENV_INDENT),
        "WORKFLOW": NIGHTLY_JOB,
        "CONCLUSION": conclusion,
        "RUN_ID": RUN_ID,
        "RUN_URL": RUN_URL,
    }
    subprocess.run(["bash", "-c", step_script(steps[0])], env=env, check=True, cwd=folder)
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake gh is an executable script with a shebang"
)


@posix_only
def test_nightly_alert_opens_an_issue_naming_the_run_and_failed_jobs(tmp_path: Path) -> None:
    calls = run_alert(tmp_path, "failure", "")
    assert [call[:2] for call in calls] == [
        ["issue", "list"],
        ["api", f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/jobs?per_page=100"],
        ["label", "create"],
        ["issue", "create"],
    ]
    create = calls[-1]
    assert create[create.index("--title") + 1] == "nightly failure: nightly"
    assert create[create.index("--label") + 1] == "nightly-failure"
    body = create[create.index("--body") + 1]
    assert body.startswith("nightly failed on main.\n")
    assert f"Run: {RUN_URL}\n" in body
    assert body.endswith(f"Failed jobs:\n{FAILED_JOB}")


@posix_only
def test_nightly_alert_comments_on_the_open_issue(tmp_path: Path) -> None:
    calls = run_alert(tmp_path, "failure", OPEN_ISSUE)
    assert [call[:2] for call in calls] == [
        ["issue", "list"],
        ["api", f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/jobs?per_page=100"],
        ["issue", "comment"],
    ]
    assert calls[-1][2] == OPEN_ISSUE
    assert FAILED_JOB in calls[-1][calls[-1].index("--body") + 1]


@posix_only
def test_nightly_alert_closes_the_open_issue_when_green_again(tmp_path: Path) -> None:
    calls = run_alert(tmp_path, "success", OPEN_ISSUE)
    assert calls[1] == [
        "issue",
        "close",
        OPEN_ISSUE,
        "--comment",
        f"nightly is green again on main: {RUN_URL}",
    ]
    assert len(calls) == 2


@posix_only
def test_nightly_alert_leaves_a_green_run_without_an_issue_alone(tmp_path: Path) -> None:
    calls = run_alert(tmp_path, "success", "")
    assert [call[:2] for call in calls] == [["issue", "list"]]
