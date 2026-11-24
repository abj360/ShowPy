#!/usr/bin/env python3

"""
test_nightly_core.py --- checks the fuzz crate, the rustfuzz and mutants sessions and their workflow

Contains:
    test_fuzz_crate_declares_one_target_per_parser: fuzz/ is a cargo-fuzz crate outside the
        workspace, with a lock file and exactly the six parser targets the session runs
    test_fuzz_targets_are_empty_until_their_lanes_fill_them: each target ignores its input
    function_source(): returns the unparsed source of one noxfile function
    session_calls(): lists the source of every `session.run` call one noxfile function makes
    test_rustfuzz_session_runs_each_target_on_the_pinned_nightly: a dated nightly, a locked
        fuzz build and one libFuzzer run per target
    test_fuzz_plan_reads_a_target_and_the_seconds: every target for FUZZ_SECONDS by default
    test_fuzz_plan_refuses_unknown_arguments: an unknown target or a bad count is refused
    test_mutants_session_reports_without_gating: missed mutants still exit 0 with a summary
    test_mutants_session_fails_when_a_tool_cannot_run: a cargo-mutants error exits 1
    test_mutants_session_installs_cargo_mutants_into_the_repository: at a pinned version, and
        neither session runs by default
    test_nightly_core_runs_fuzz_and_mutants: daily and on dispatch only, 10 minutes per target,
        then the scores, with crash inputs and the summary uploaded
    test_nightly_core_keeps_the_workflow_conventions: SHA pins, no persisted credentials,
        read-only permissions, the pinned toolchain and Rust cache before uv
    test_nightly_core_saves_caches_only_from_main: the Rust, uv and cargo tool caches
"""

import ast
import json
import re
import sys
import tomllib
from pathlib import Path

import pytest

from noxfile import _fuzz_plan
from scripts.run_mutants import CANNOT_RUN, NO_KEPT_MODULES, SCORED, SUMMARY, record_scores
from tests.unit.tooling.test_noxfile import (
    CARGO_TOOLS_ROOT,
    NOXFILE,
    PINNED_VERSION,
    ROOT,
    SAVE_FROM_MAIN,
    WITH_INDENT,
    cargo_tools_cache_key_problems,
    ci_job,
    ci_job_names,
    job_steps,
    module_constant,
    module_literal,
    pinned_version_lines,
    rust_caches_saved_off_main,
    session_names,
    step_value,
    unpinned_actions,
    unprepared_rust_jobs,
    uv_runs,
    workflow_section,
)

FUZZ_TARGETS = ("refs", "strict_json", "svg_path", "font", "selection", "timeline")
FUZZ_ROOT = ROOT / "fuzz"
FUZZ_MANIFEST = FUZZ_ROOT / "Cargo.toml"
WORKSPACE_MANIFEST = ROOT / "Cargo.toml"
EMPTY_TARGET = "fuzz_target!(|_data: &[u8]| {});"
DATED_NIGHTLY = re.compile(r"^nightly-\d{4}-\d{2}-\d{2}$")
RUSTFUZZ_SESSION = "rustfuzz"
MUTANTS_SESSION = "mutants"
RUSTFUZZ_CALLS = [
    (
        "session.run('rustup', 'toolchain', 'install', FUZZ_TOOLCHAIN, '--profile', 'minimal', "
        "external=True)"
    ),
    (
        "session.run('cargo', 'fetch', '--locked', '--manifest-path', FUZZ_MANIFEST, "
        "external=True, env=nightly)"
    ),
    (
        "session.run(_cargo_tool(CARGO_FUZZ), 'run', target, '--fuzz-dir', FUZZ_DIR, '--', "
        "f'-max_total_time={seconds}', external=True, env=nightly)"
    ),
]
NIGHTLY_ENV = "nightly = {'RUSTUP_TOOLCHAIN': FUZZ_TOOLCHAIN}"
TARGET_LOOP = "for target in targets:"
DEFAULT_SESSIONS = "nox.options.sessions"
NIGHTLY_WORKFLOW = ROOT / ".github" / "workflows" / "nightly-core.yml"
NIGHTLY_TRIGGERS = 'on:\n  schedule:\n    - cron: "17 4 * * *"\n  workflow_dispatch:\n'
NIGHTLY_PERMISSIONS = "permissions:\n  contents: read\n"
NIGHTLY_JOB = "core"
FUZZ_STEP = "run: uv run --locked nox -s rustfuzz -- --seconds 600"
MUTANTS_STEP = "run: uv run --locked nox -s mutants"
NOT_CANCELLED = "${{ !cancelled() }}"
UPLOAD_STEP = "uses: actions/upload-artifact@"
CRASH_INPUTS = "fuzz/artifacts/"
SUMMARY_FILE = "mutants-summary.json"
SETUP_UV_STEP = "uses: astral-sh/setup-uv@"
UV_CACHE_SAVE = f"save-cache: ${{{{ {SAVE_FROM_MAIN} }}}}"
FAKE_CARGO_MUTANTS = """
import json, pathlib, sys
out = pathlib.Path("mutants.out")
out.mkdir()
counts = {{"caught": 4, "missed": 6, "timeout": 0, "unviable": 20, "total_mutants": 30}}
(out / "outcomes.json").write_text(json.dumps(counts), encoding="utf-8")
sys.exit({exit_code})
"""
MISSED_MUTANTS = 2
BASELINE_FAILED = 4


def test_fuzz_crate_declares_one_target_per_parser() -> None:
    manifest = tomllib.loads(FUZZ_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["package"]["name"] == "e8s-fuzz"
    assert manifest["package"]["metadata"]["cargo-fuzz"] is True
    assert manifest["dependencies"]["e8s-core"] == {"path": "../crates/e8s-core"}
    assert PINNED_VERSION.match(manifest["dependencies"]["libfuzzer-sys"].removeprefix("="))
    targets = {target["name"]: target["path"] for target in manifest["bin"]}
    assert targets == {name: f"fuzz_targets/{name}.rs" for name in FUZZ_TARGETS}
    on_disk = sorted(path.stem for path in (FUZZ_ROOT / "fuzz_targets").glob("*.rs"))
    assert on_disk == sorted(FUZZ_TARGETS)
    assert module_literal(NOXFILE.read_text(encoding="utf-8"), "FUZZ_TARGETS") == FUZZ_TARGETS
    workspace = tomllib.loads(WORKSPACE_MANIFEST.read_text(encoding="utf-8"))["workspace"]
    assert workspace["exclude"] == ["fuzz"]
    assert (FUZZ_ROOT / "Cargo.lock").is_file()


def test_fuzz_targets_are_empty_until_their_lanes_fill_them() -> None:
    for name in FUZZ_TARGETS:
        target = (FUZZ_ROOT / "fuzz_targets" / f"{name}.rs").read_text(encoding="utf-8")
        assert target.startswith(f"//! {name}.rs --- "), name
        assert "#![no_main]" in target, name
        assert target.rstrip().endswith(EMPTY_TARGET), name


def function_source(source: str, function_name: str) -> str:
    """Returns the unparsed source of one noxfile function."""
    return next(
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )


def session_calls(source: str, function_name: str) -> list[str]:
    """Lists the source text of every `session.run` call one noxfile function makes, in order."""
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    calls = sorted(
        (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "session.run"
        ),
        key=lambda call: (call.lineno, call.col_offset),
    )
    return [ast.unparse(call) for call in calls]


def test_rustfuzz_session_runs_each_target_on_the_pinned_nightly() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    assert DATED_NIGHTLY.match(module_constant(source, "FUZZ_TOOLCHAIN"))
    assert module_constant(source, "FUZZ_DIR") == "fuzz"
    assert module_constant(source, "FUZZ_MANIFEST") == "fuzz/Cargo.toml"
    assert module_constant(source, "FUZZ_SECONDS") == "60"
    assert PINNED_VERSION.match(module_constant(source, "CARGO_FUZZ_VERSION"))
    assert {"_install_cargo_tool", "CARGO_FUZZ", "CARGO_FUZZ_VERSION"} <= session_names(
        source, RUSTFUZZ_SESSION
    )
    assert session_calls(source, RUSTFUZZ_SESSION) == RUSTFUZZ_CALLS
    body = function_source(source, RUSTFUZZ_SESSION)
    assert NIGHTLY_ENV in body
    assert TARGET_LOOP in body


def test_fuzz_plan_reads_a_target_and_the_seconds() -> None:
    assert _fuzz_plan([]) == (FUZZ_TARGETS, "60")
    assert _fuzz_plan(["refs"]) == (("refs",), "60")
    assert _fuzz_plan(["refs", "--seconds", "5"]) == (("refs",), "5")
    assert _fuzz_plan(["--seconds", "600"]) == (FUZZ_TARGETS, "600")
    assert _fuzz_plan(["--seconds", "600", "timeline"]) == (("timeline",), "600")


@pytest.mark.parametrize(
    "posargs",
    [
        ["parser"],
        ["refs", "font"],
        ["--seconds"],
        ["--seconds", "0"],
        ["--seconds", "-5"],
        ["--seconds", "1.5"],
        ["refs", "--seconds", "five"],
    ],
)
def test_fuzz_plan_refuses_unknown_arguments(posargs: list[str]) -> None:
    with pytest.raises(ValueError, match="usage: nox -s rustfuzz"):
        _fuzz_plan(posargs)


def _fake_cargo_mutants(folder: Path, exit_code: int) -> list[str]:
    script = folder / "fake_cargo_mutants.py"
    script.write_text(FAKE_CARGO_MUTANTS.format(exit_code=exit_code), encoding="utf-8")
    return [sys.executable, str(script)]


def test_mutants_session_reports_without_gating(tmp_path: Path) -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    script = module_constant(source, "MUTANTS_SCRIPT")
    assert (ROOT / script).is_file()
    assert uv_runs(source, MUTANTS_SESSION) == [
        ["python", script, "--cargo-mutants", "_cargo_tool(CARGO_MUTANTS)"]
    ]
    (tmp_path / "ported").mkdir()
    tool = _fake_cargo_mutants(tmp_path, MISSED_MUTANTS)
    assert record_scores(tool, tmp_path, ported_root=tmp_path / "ported") == 0
    summary = json.loads((tmp_path / SUMMARY).read_text(encoding="utf-8"))
    assert summary["rust"]["status"] == SCORED
    assert summary["rust"]["missed"] == 6
    assert summary["rust"]["score"] == 0.4
    assert summary["python"] == {"status": NO_KEPT_MODULES}


def test_mutants_session_fails_when_a_tool_cannot_run(tmp_path: Path) -> None:
    (tmp_path / "ported").mkdir()
    tool = _fake_cargo_mutants(tmp_path, BASELINE_FAILED)
    assert record_scores(tool, tmp_path, ported_root=tmp_path / "ported") == 1
    summary = json.loads((tmp_path / SUMMARY).read_text(encoding="utf-8"))
    assert summary["rust"] == {"status": CANNOT_RUN, "detail": "cargo-mutants exited 4"}
    assert summary["python"] == {"status": NO_KEPT_MODULES}


def test_mutants_session_installs_cargo_mutants_into_the_repository() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    assert module_constant(source, "CARGO_TOOLS_ROOT") == CARGO_TOOLS_ROOT
    assert PINNED_VERSION.match(module_constant(source, "CARGO_MUTANTS_VERSION"))
    assert module_literal(source, "CARGO_MUTANTS_VERSION_ARGS") == ("mutants", "--version")
    assert {
        "_install_cargo_tool",
        "CARGO_MUTANTS",
        "CARGO_MUTANTS_VERSION",
        "CARGO_MUTANTS_VERSION_ARGS",
    } <= session_names(source, MUTANTS_SESSION)
    defaults = module_literal(source, DEFAULT_SESSIONS)
    assert isinstance(defaults, list)
    assert RUSTFUZZ_SESSION not in defaults
    assert MUTANTS_SESSION not in defaults


def _nightly() -> str:
    return NIGHTLY_WORKFLOW.read_text(encoding="utf-8")


def test_nightly_core_runs_fuzz_and_mutants() -> None:
    workflow = _nightly()
    assert workflow_section(workflow, "on") == NIGHTLY_TRIGGERS
    assert ci_job_names(workflow) == [NIGHTLY_JOB]
    steps = job_steps(ci_job(workflow, NIGHTLY_JOB))
    fuzz = next(index for index, step in enumerate(steps) if FUZZ_STEP in step)
    scores = next(index for index, step in enumerate(steps) if MUTANTS_STEP in step)
    assert fuzz < scores
    assert step_value(steps[scores], "if") == NOT_CANCELLED
    uploads = [step for step in steps[scores:] if UPLOAD_STEP in step]
    assert len(uploads) == len([step for step in steps if UPLOAD_STEP in step]) == 2
    assert all(step_value(step, "if") == NOT_CANCELLED for step in uploads)
    assert step_value(uploads[0], "path", WITH_INDENT) == CRASH_INPUTS
    assert SUMMARY_FILE in uploads[1]


def test_nightly_core_keeps_the_workflow_conventions() -> None:
    workflow = _nightly()
    assert workflow_section(workflow, "permissions") == NIGHTLY_PERMISSIONS
    assert workflow.count("permissions:") == 1
    assert unpinned_actions(workflow) == []
    checkouts = workflow.count("uses: actions/checkout@")
    assert checkouts == 1
    assert workflow.count("persist-credentials: false") == checkouts
    assert unprepared_rust_jobs(workflow) == []


def test_nightly_core_saves_caches_only_from_main() -> None:
    workflow = _nightly()
    noxfile = NOXFILE.read_text(encoding="utf-8")
    sessions = (RUSTFUZZ_SESSION, MUTANTS_SESSION)
    assert len(pinned_version_lines(noxfile, sessions)) == 2
    assert rust_caches_saved_off_main(workflow) == []
    job = ci_job(workflow, NIGHTLY_JOB)
    problems = cargo_tools_cache_key_problems(job, noxfile, sessions=sessions, run_step=FUZZ_STEP)
    assert problems == []
    setup_uv = [step for step in job_steps(job) if SETUP_UV_STEP in step]
    assert len(setup_uv) == 1
    assert UV_CACHE_SAVE in setup_uv[0]
    assert "uses: actions/cache@" not in job
