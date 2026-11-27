#!/usr/bin/env python3

"""
test_nightly_failures.py --- checks that a nightly failure hides no later check and gets an issue

Contains:
    test_regression_nightly_runs_every_check_after_an_earlier_failure: tests, slow, fuzz and
        bench each run unless the run is cancelled or uv sync failed, and no step tolerates its
        own failure
    step_script(): returns the literal block of one step's `run: |` script
    job_permissions(): reads one job's permissions, comments dropped
    test_regression_each_nightly_reports_from_main_only: both nightly workflows end with a report
        job that needs the check job, runs only on main, and alone may write issues
    test_report_workflow_is_only_callable_and_least_privileged: workflow_call only, no default
        permissions, no checkout, a timeout and the job's own token
    test_regression_report_passes_inputs_and_run_data_only_through_env: no expression inside the
        script; every input and run field it reads comes from env
    test_step_script_reads_a_literal_block: proves the script reader can fail
    test_job_permissions_stop_at_the_next_key: proves the permissions reader can fail
    run_report(): runs the report script against a fake gh that applies its filters with jq
    test_report_opens_an_issue_when_none_is_open: labels it and names the run and the failed job
    test_report_comments_on_the_open_issue_for_any_result_but_success: a repeated failure,
        cancellation, timeout or skipped check opens no second issue
    test_report_closes_the_open_issue_on_success: a later success closes it
    test_report_leaves_a_success_without_an_issue_alone: a success with nothing open
"""

import json
import os
import re
import shutil
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
REPORT_WORKFLOW = ROOT / ".github" / "workflows" / "report-nightly.yml"
NIGHTLY_JOB = "nightly"
CHECK_SESSIONS = ("tests", "slow", "fuzz", "bench")
NOX_SESSION = re.compile(r"\bnox -s (\w+)")
SYNCED = "${{ !cancelled() && steps.sync.outcome == 'success' }}"
SYNC_STEP = "id: sync\n        run: uv sync --locked\n"
NIGHTLY_JOB_NAME = "tests, slow, fuzz and large benchmarks"
CHECK_JOBS = {NIGHTLY_WORKFLOW: NIGHTLY_JOB, NIGHTLY_CORE_WORKFLOW: "core"}
REPORT_JOB = "report"
REPORT_CONDITION = "${{ !cancelled() && github.ref == 'refs/heads/main' }}"
REPORT_CALL = "$/.github/workflows/report-nightly.yml"
REPORT_INPUTS = {
    "workflow": "${{{{ github.workflow }}}}",
    "result": "${{{{ needs.{job}.result }}}}",
}
REPORT_TRIGGERS = """on:
  workflow_call:
    inputs:
      workflow:
        description: The name of the calling nightly workflow.
        required: true
        type: string
      result:
        description: The result of the calling workflow's check job.
        required: true
        type: string
"""
READ_ONLY = "permissions:\n  contents: read\n"
NO_PERMISSIONS = "permissions: {}\n"
REPORT_PERMISSIONS = {"actions": "read", "issues": "write"}
REPORT_TIMEOUT = "10"
JOB_KEY_INDENT = 4
PERMISSION_INDENT = 6
ENV_INDENT = STEP_INDENT + 2
BLOCK_INDENT = STEP_INDENT + 2
RUN_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "GH_REPO": "${{ github.repository }}",
    "LABEL": "nightly-failure",
    "WORKFLOW": "${{ inputs.workflow }}",
    "RESULT": "${{ inputs.result }}",
    "SERVER_URL": "${{ github.server_url }}",
    "RUN_ID": "${{ github.run_id }}",
}
LITERAL_BLOCK = "|"
NO_IGNORES = "zizmor: ignore"
REPOSITORY = "abj360/e8s"
SERVER_URL = "https://github.com"
RUN_ID = "45993884219"
RUN_URL = f"{SERVER_URL}/{REPOSITORY}/actions/runs/{RUN_ID}"
LABEL = "nightly-failure"
OPEN_ISSUE = "12"
ISSUES = [
    {"number": 5, "title": "nightly failure: nightly", "state": "closed", "labels": [LABEL]},
    {"number": 13, "title": "nightly failure: nightly-core", "state": "open", "labels": [LABEL]},
    {"number": 20, "title": "nightly failure: nightly", "state": "open", "labels": []},
    {"number": 12, "title": "nightly failure: nightly", "state": "open", "labels": [LABEL]},
]
NONE_OPEN = ISSUES[:3]
JOBS = {
    "jobs": [
        {
            "name": NIGHTLY_JOB_NAME,
            "status": "completed",
            "conclusion": "failure",
            "html_url": f"{RUN_URL}/job/1",
        },
        {"name": "setup", "status": "completed", "conclusion": "success", "html_url": "s"},
        {"name": "report", "status": "in_progress", "conclusion": None, "html_url": "r"},
    ]
}
FAILED_JOB = f"- {NIGHTLY_JOB_NAME} (failure): {RUN_URL}/job/1"
ISSUE_LIST = ["issue", "list", "--label", LABEL, "--state", "open", "--limit", "100"]
JOBS_API = ["api", f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/jobs?per_page=100"]
NOT_SUCCESS = ("failure", "cancelled", "timed_out", "skipped")
FAKE_GH = """#!PYTHON
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")


def flag(name):
    return args[args.index(name) + 1] if name in args else None


def emit(value):
    text = json.dumps(value)
    if flag("--jq") is None:
        print(text)
        return
    jq = subprocess.run(
        ["jq", "-r", flag("--jq")], input=text, capture_output=True, text=True, check=True
    )
    sys.stdout.write(jq.stdout)


if args[:2] == ["issue", "list"]:
    state, label = flag("--state") or "open", flag("--label")
    fields = (flag("--json") or "number").split(",")
    kept = [
        issue for issue in json.loads(os.environ["FAKE_ISSUES"])
        if state in ("all", issue["state"]) and (label is None or label in issue["labels"])
    ]
    emit([{field: issue[field] for field in fields} for issue in kept])
elif args[0] == "api":
    emit(json.loads(os.environ["FAKE_JOBS"]))
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_regression_nightly_runs_every_check_after_an_earlier_failure() -> None:
    """A failing fuzz step skipped the large benchmarks every night for five days."""
    workflow = _read(NIGHTLY_WORKFLOW)
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


@pytest.mark.parametrize(("path", "check_job"), CHECK_JOBS.items())
def test_regression_each_nightly_reports_from_main_only(path: Path, check_job: str) -> None:
    """A workflow_run alert let a fork's pull_request run named main write issues."""
    workflow = _read(path)
    assert ci_job_names(workflow) == [check_job, REPORT_JOB]
    assert workflow_section(workflow, "permissions") == READ_ONLY
    assert workflow.count("permissions:") == 2
    assert job_permissions(ci_job(workflow, check_job)) == {}
    report = ci_job(workflow, REPORT_JOB)
    assert job_value(report, "needs", JOB_KEY_INDENT) == f"[{check_job}]"
    assert job_condition(report) == REPORT_CONDITION
    assert job_permissions(report) == REPORT_PERMISSIONS
    assert job_value(report, "uses", JOB_KEY_INDENT) == REPORT_CALL
    inputs = {key: job_value(report, key, PERMISSION_INDENT) for key in REPORT_INPUTS}
    assert inputs == {key: value.format(job=check_job) for key, value in REPORT_INPUTS.items()}
    assert "workflow_run" not in workflow
    assert NO_IGNORES not in workflow


def test_report_workflow_is_only_callable_and_least_privileged() -> None:
    workflow = _read(REPORT_WORKFLOW)
    assert workflow_section(workflow, "on") == REPORT_TRIGGERS
    assert workflow_section(workflow, "permissions") == NO_PERMISSIONS
    assert ci_job_names(workflow) == [REPORT_JOB]
    job = ci_job(workflow, REPORT_JOB)
    assert job_permissions(job) == REPORT_PERMISSIONS
    assert workflow.count("permissions:") == 2
    assert unpinned_actions(workflow) == []
    assert "uses:" not in workflow
    assert job_value(job, "timeout-minutes", JOB_KEY_INDENT) == REPORT_TIMEOUT
    assert len(job_steps(job)) == 1
    assert NO_IGNORES not in workflow


def test_regression_report_passes_inputs_and_run_data_only_through_env() -> None:
    """Inputs and run data are untrusted; an expression in a script could inject code."""
    steps = job_steps(ci_job(_read(REPORT_WORKFLOW), REPORT_JOB))
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


def run_report(folder: Path, result: str, issues: list[dict[str, object]]) -> list[list[str]]:
    """Runs the report script against a fake gh that filters fixtures and applies --jq with jq.

    Args:
        folder: An empty folder for the fake gh and its log.
        result: The result of the calling workflow's check job.
        issues: The repository's issues, each with its number, title, state and labels.

    Returns:
        calls: The arguments of each gh call, in order.
    """
    steps = job_steps(ci_job(_read(REPORT_WORKFLOW), REPORT_JOB))
    gh = folder / "gh"
    gh.write_text(FAKE_GH.replace("PYTHON", sys.executable, 1), encoding="utf-8")
    gh.chmod(0o755)
    log = folder / "gh.log"
    env = {
        **os.environ,
        "PATH": f"{folder}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(log),
        "FAKE_ISSUES": json.dumps(issues),
        "FAKE_JOBS": json.dumps(JOBS),
        "GH_REPO": REPOSITORY,
        "LABEL": step_value(steps[0], "LABEL", ENV_INDENT),
        "WORKFLOW": NIGHTLY_JOB,
        "RESULT": result,
        "SERVER_URL": SERVER_URL,
        "RUN_ID": RUN_ID,
    }
    subprocess.run(["bash", "-c", step_script(steps[0])], env=env, check=True, cwd=folder)
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _body(call: list[str], flag: str = "--body") -> str:
    return call[call.index(flag) + 1]


needs_jq = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("jq") is None,
    reason="the fake gh is a POSIX script that applies --jq with jq",
)


@needs_jq
def test_report_opens_an_issue_when_none_is_open(tmp_path: Path) -> None:
    calls = run_report(tmp_path, "failure", NONE_OPEN)
    assert calls[0][: len(ISSUE_LIST)] == ISSUE_LIST
    assert [call[:2] for call in calls] == [
        ["issue", "list"],
        JOBS_API,
        ["label", "create"],
        ["issue", "create"],
    ]
    assert calls[2][2] == LABEL
    create = calls[-1]
    assert create[create.index("--title") + 1] == "nightly failure: nightly"
    assert create[create.index("--label") + 1] == LABEL
    assert _body(create) == (
        f"nightly did not pass on main (result: failure).\n\nRun: {RUN_URL}\n\nJobs:\n{FAILED_JOB}"
    )


@needs_jq
@pytest.mark.parametrize("result", NOT_SUCCESS)
def test_report_comments_on_the_open_issue_for_any_result_but_success(
    tmp_path: Path, result: str
) -> None:
    calls = run_report(tmp_path, result, ISSUES)
    assert calls[0][: len(ISSUE_LIST)] == ISSUE_LIST
    assert [call[:2] for call in calls] == [["issue", "list"], JOBS_API, ["issue", "comment"]]
    assert calls[-1][2] == OPEN_ISSUE
    assert _body(calls[-1]).startswith(f"nightly did not pass on main (result: {result}).\n")
    assert _body(calls[-1]).endswith(f"Jobs:\n{FAILED_JOB}")


@needs_jq
def test_report_closes_the_open_issue_on_success(tmp_path: Path) -> None:
    calls = run_report(tmp_path, "success", ISSUES)
    assert calls[0][: len(ISSUE_LIST)] == ISSUE_LIST
    assert calls[1:] == [
        ["issue", "close", OPEN_ISSUE, "--comment", f"nightly is green again on main: {RUN_URL}"]
    ]


@needs_jq
def test_report_leaves_a_success_without_an_issue_alone(tmp_path: Path) -> None:
    calls = run_report(tmp_path, "success", NONE_OPEN)
    assert [call[:2] for call in calls] == [["issue", "list"]]
