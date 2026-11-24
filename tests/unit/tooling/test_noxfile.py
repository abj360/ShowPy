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
    test_slow_session_gives_each_module_to_one_worker: the parallel run passes --dist
        loadscope, so a module-scoped fixture is built once
    test_serial_sessions_start_no_workers: tests and hashseeds never pass -n, nor does addopts
    test_pytest_runs_resolves_constants_and_keeps_posargs: proves the run scan resolves names
    test_last_value_reads_the_last_occurrence: proves the flag lookup obeys the last value
    test_uv_rebuilds_the_extension_when_rust_sources_change: the uv cache keys name every
        Rust build input, so a changed crate rebuilds e8s._core
    unprepared_rust_jobs(): lists the jobs that reach uv before the pinned toolchain and cache
    test_every_job_that_syncs_installs_the_pinned_rust_toolchain: each uv job installs the
        rust-toolchain.toml pin, then restores the Rust cache pinned by SHA, before uv builds
    test_unprepared_rust_jobs_sees_a_missing_or_late_step: proves the toolchain check can fail
    rust_caches_saved_off_main(): lists the jobs whose Rust cache step may save outside main
    test_rust_cache_saves_only_from_main: a pull request never writes a Rust cache that main
        later restores and runs
    test_rust_caches_saved_off_main_sees_a_missing_or_wrong_condition: proves the check can fail
    unlocked_uv_builds(): lists the noxfile functions that run `uv build` without cargo's
        --locked in the build environment
    test_wheel_builds_fail_on_a_stale_cargo_lock: every nox build passes cargo's --locked
    test_unlocked_uv_builds_sees_a_build_without_the_lock: proves the lock check can fail
    session_runs(): lists the arguments of every `session.run` call one noxfile function makes
    test_rust_session_runs_fmt_clippy_test_and_the_wasm_build: the cargo gates, the FMA check,
        then cargo deny
    session_env(): lists the `session.env` assignments of one noxfile function, with lines
    test_regression_rust_session_builds_against_the_project_interpreter: every cargo command
        runs with PYO3_PYTHON and VIRTUAL_ENV set to nox's own environment, where e8s is
        installed
    uv_runs(): lists the arguments of every `_uv_run` call one noxfile function makes
    test_rust_session_checks_crate_licences_and_the_notices: cargo deny, then the notice check
        and the cargo_tools tests
    test_types_session_checks_the_core_stubs: mypy, pyright, then stubtest on e8s._core with no
        allowlist
    test_wheel_sessions_refuse_an_rpath_or_libpython_in_the_extension: wheel and wheels run
        scripts/check_extension_links.py on the wheel they built
    test_cargo_tools_install_locked_at_pinned_versions_into_the_repository: cargo install
        --locked --root .cargo-tools at exact versions, never into ~/.cargo/bin
    test_fma_check_builds_the_extension_and_runs_the_script: cargo rustc on the fma-check
        profile, then scripts/check_no_fma.py on its output
    test_fma_check_profile_keeps_symbols_and_line_tables: ships the dist codegen, unstripped
    test_session_runs_keeps_source_order: proves the run scan reads calls in order
    module_literal(): returns the literal value of one module-level noxfile constant
    uv_build_envs(): lists the env= constant of each `uv build` call of one noxfile function
    test_wheels_session_tests_the_wheel_on_313_and_314: builds the sdist, a dist wheel from it,
        and runs the ported packages' tests against that wheel on each supported Python
    job_steps(): splits one job's text into its steps
    test_rust_job_runs_the_rust_session: the rust job runs `nox -s rust` on Linux and arm64
        macOS when the changes job reports a Rust change, its legs always starting
    pinned_version_lines(): returns the noxfile lines that pin the cargo tools some sessions
        install
    cargo_tools_cache_key_problems(): lists what lets the cached cargo tools outlive their pins
    test_rust_job_caches_the_cargo_tools_by_their_pinned_versions: .cargo-tools is restored by
        a key naming each pinned version and saved only from main
    test_cargo_tools_cache_key_problems_sees_a_loose_key: proves the cache check can fail
    workflow_section(): returns one top-level key of a workflow with its body
    test_workflow_section_stops_at_the_next_top_level_key: proves the section reader
    test_wheels_workflow_runs_on_rust_changes_and_tags: the exact triggers of wheels.yml
    matrix_entries(): reads the block mappings under a job's `include:` matrix key
    test_matrix_entries_reads_each_block_mapping: proves the matrix reader
    maturin_steps(): returns the maturin-action steps of one job
    build_targets(): lists the job, runner and target of every wheel the workflow builds
    test_wheels_workflow_builds_eight_wheels_and_the_sdist: eight native wheel builds with
        the dist profile and a locked Cargo.lock, the pinned maturin, and the sdist
    test_wheels_jobs_wait_for_the_release_version_check: every build needs the version job,
        which checks the tag against Cargo.toml on a tag
    test_wheels_test_the_installed_wheel_on_313_and_314: the ported tests run against the
        installed wheel from a copy of the suite on each supported Python
    imported_modules(): lists the third-party modules that test files reach
    test_imported_modules_follows_test_modules: proves the import scan follows `tests` modules
    requirement_name(): returns the distribution name a requirement starts with
    test_wheels_install_every_package_the_ported_tests_import: the wheel's dependencies plus
        WHEEL_TEST_TOOLS are exactly what the ported tests import, all from the dev group
    unpinned_actions(): lists the `uses:` lines that do not pin a full commit SHA
    test_every_wheels_action_is_pinned_to_a_sha: every action in wheels.yml is SHA-pinned
    test_unpinned_actions_sees_a_tag_or_short_sha: proves the pin check can fail
    test_wheels_workflow_holds_least_privilege_and_writes_no_cache: read-only jobs, no
        persisted credentials and no cache
    test_wheel_builds_check_the_pinned_toolchain: each build proves it compiled with the pin
    run_toolchain_check(): runs the workflow's toolchain check against a fake rustc
    test_toolchain_check_refuses_a_rustc_other_than_the_pin: passes the pin, refuses another
    test_regression_wheels_never_cancel_a_tag_run: a newer run cancels only non-tag runs
    test_regression_wheels_upload_before_the_tests_run: the uploaded wheel is the built one,
        never one the test step could replace
    unpinned_images(): lists the python image references that name no digest
    test_unpinned_images_sees_a_tag_without_a_digest: proves the image check can fail
    test_regression_wheel_test_images_are_pinned_by_digest: each Linux leg tests in python
        images pinned to multi-arch index digests, the same per variant
"""

import ast
import itertools
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
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
DIST_FLAG = "--dist"
MODULE_DIST = "loadscope"
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
RUST_BUILD_INPUTS = frozenset(
    {
        "pyproject.toml",
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        "crates/**/Cargo.toml",
        "crates/**/*.rs",
    }
)
UV_STEP = re.compile(r"\buv (sync|run)\b")
TOOLCHAIN_STEP = "rustup toolchain install"
RUST_CACHE_STEP = re.compile(r"uses: Swatinem/rust-cache@[0-9a-f]{40}\b")
LOCKED_BUILD = "LOCKED_BUILD"
BUILD_ARGS = "MATURIN_PEP517_ARGS"
LOCKED_FLAG = "--locked"
CARGO_MANIFEST = ROOT / "Cargo.toml"
SESSION_RUN = "session.run"
RUST_SESSION = "rust"
TYPES_SESSION = "types"
RUST_SESSION_ENV = {"PYO3_PYTHON": "sys.executable", "VIRTUAL_ENV": "sys.prefix"}
WHEEL_BUILDING_SESSIONS = ("wheel", "wheels")
TYPES_SESSION_RUNS = [
    ["mypy", "e8s", "tests", "scripts"],
    ["pyright"],
    ["python", "-m", "mypy.stubtest", "e8s._core"],
]
FMA_STEP = "_check_no_fma"
FMA_CHECK_SCRIPT = "FMA_CHECK_SCRIPT"
RUST_SESSION_RUNS = [
    ["cargo", "fmt", "--all", "--check"],
    ["cargo", "clippy", "--workspace", "--all-targets", "--locked", "--", "-D", "warnings"],
    ["cargo", "test", "--workspace", "--locked"],
    [
        "cargo",
        "build",
        "-p",
        "e8s-wasm",
        "--target",
        "wasm32-unknown-unknown",
        "--profile",
        "dist",
        "--locked",
    ],
    ["_cargo_tool(CARGO_DENY)", "--locked", "check"],
]
NOTICES_CHECK = ["python", "scripts/gen_third_party_notices.py", "--check"]
CARGO_TOOLS_RUN = ["pytest", "-m", "cargo_tools", "tests/unit/tooling/test_third_party_notices.py"]
CARGO_TOOLS_ROOT = ".cargo-tools"
CARGO_INSTALL = {"cargo", "install", "--locked", "--root", "--version"}
PINNED_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
FMA_CHECK_BUILD = [
    "cargo",
    "rustc",
    "-p",
    "e8s-py",
    "--profile",
    "fma-check",
    "--locked",
    "*config",
    "*link_args",
]
# The checked build has the shipped codegen (fat LTO, one codegen unit) plus symbols and the
# line tables that record what was inlined where.
FMA_CHECK_PROFILE = {"inherits": "dist", "strip": "none", "debug": "line-tables-only"}
WHEELS_SESSION = "wheels"
WHEEL_FROM_SDIST = "_build_wheel_from_sdist"
WHEEL_TEST_PYTHONS = ("3.13", "3.14")
PORTED_TESTS = (
    "tests/unit/core",
    "tests/unit/geo",
    "tests/unit/time",
    "tests/unit/sheet",
    "tests/unit/cast",
    "tests/unit/choreo",
    "tests/golden",
)
DIST_BUILD = {BUILD_ARGS: "--locked --profile dist"}
SDIST_THEN_WHEEL = [
    ["uv", "build", "--sdist", "--out-dir", "str(dist)"],
    ["uv", "build", "--wheel", "--out-dir", "str(dist)", "str(sdist)"],
]
PORTED_TEST_RUN = ["str(python)", "-P", "-m", "pytest", "*PORTED_TESTS", "*session.posargs"]
SAVE_ON_MAIN_ONLY = re.compile(
    r"^\s+save-if: \$\{\{ github\.ref == 'refs/heads/main' \}\}$", re.MULTILINE
)
RUST_JOB = "rust"
PARITY_JOBS = ("parity", "parity-compare")
RUNS_RUST_JOB = "needs.changes.result != 'success' || needs.changes.outputs.rust != 'false'"
RUST_STEP_ENV = f"${{{{ {RUNS_RUST_JOB} }}}}"
RUST_STEP_GATE = re.compile(r"^env\.RUN_RUST_JOB [!=]= 'true'( && .+)?$")
RUST_JOB_OSES = "[ubuntu-24.04, macos-15]"
RUST_SESSION_STEP = "run: uv run --locked nox -s rust"
RUST_OUTPUT = "rust: ${{ steps.classify.outputs.rust }}"
CACHE_RESTORE_STEP = re.compile(r"uses: actions/cache/restore@[0-9a-f]{40}\b")
CACHE_SAVE_STEP = re.compile(r"uses: actions/cache/save@[0-9a-f]{40}\b")
CARGO_TOOLS_PATH = re.compile(r"^\s+path: \.cargo-tools$", re.MULTILINE)
CARGO_TOOLS_VERSIONS_ID = "pins"
VERSIONS_GREP = re.compile(r"grep -hE '([^']+)' rust-toolchain\.toml noxfile\.py")
TOOLCHAIN_FILE = ROOT / "rust-toolchain.toml"
TOOLCHAIN_PIN = re.compile(r'^channel = "[^"]+"$')
CARGO_TOOLS_KEY_PARTS = (
    "${{ runner.os }}",
    "${{ runner.arch }}",
    "${{ steps.pins.outputs.versions }}",
)
STEP_INDENT = 8
WITH_INDENT = 10
SAVE_FROM_MAIN = "github.ref == 'refs/heads/main'"
INSTALL_CARGO_TOOL = "_install_cargo_tool"
WHEELS_WORKFLOW = ROOT / ".github" / "workflows" / "wheels.yml"
WHEELS_TRIGGERS = """on:
  workflow_dispatch:
  pull_request:
    paths:
      - "crates/**"
      - "Cargo.toml"
      - "Cargo.lock"
      - "rust-toolchain.toml"
      - "pyproject.toml"
      - ".github/workflows/wheels.yml"
  push:
    tags: ["v*"]
"""
VERSION_JOB = "version"
WHEEL_JOBS = ("linux", "windows", "macos")
SDIST_JOB = "sdist"
WHEEL_TARGETS = [
    ("linux", "ubuntu-24.04", "x86_64 2_28"),
    ("linux", "ubuntu-24.04-arm", "aarch64 2_28"),
    ("linux", "ubuntu-24.04", "x86_64 musllinux_1_2"),
    ("linux", "ubuntu-24.04-arm", "aarch64 musllinux_1_2"),
    ("windows", "windows-2025", "x64"),
    ("windows", "windows-11-arm", "aarch64"),
    ("macos", "macos-15", "aarch64"),
    ("macos", "macos-15-intel", "x86_64"),
]
MATRIX_ITEM_INDENT = " " * 10
DIST_BUILD_ARGS = "--profile dist --locked --out dist"
UPLOAD_STEP = "uses: actions/upload-artifact@"
PORTED_TEST_STEP = "-P -m pytest $PORTED_TESTS"
EXPRESSION = re.compile(r"\$\{\{ ([\w.]+) \}\}")
IMAGE_REFERENCE = re.compile(r"\bpython:[^\s'\"@]+(?:@[^\s'\"]+)?")
PINNED_IMAGE = re.compile(r"@(?:sha256:[0-9a-f]{64}|<matrix\.digest_\d+>)$")
IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_LOOP = 'for image in "$IMAGE_313" "$IMAGE_314"; do'
LOCK_UNCHANGED = "run: git diff --exit-code -- Cargo.lock"
ON_A_TAG = "github.ref_type == 'tag'"
RELEASE_VERSION_RUN = 'python3 scripts/check_release_version.py "$TAG"'
TESTS_FROM_SUITE = "run: mkdir suite && cp -R tests pyproject.toml suite/"
WHEEL_INSTALL = re.compile(
    r'--constraint (?:/w/)?constraints\.txt \\\n\s+"\$wheel\[all\]" \$WHEEL_TEST_TOOLS$',
    re.MULTILINE,
)
FIRST_PARTY = "e8s"
FIRST_PARTY_TESTS = "tests"
SUITE_ENTRY_FILES = ("tests/conftest.py", "tests/ledger_plugin.py")
REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
PINNED_ACTION = re.compile(r"uses: [\w.-]+/[\w.-]+(?:/[\w.-]+)*@[0-9a-f]{40} # v\d+(?:\.\d+)*$")
READ_ONLY_JOB = "    permissions:\n      contents: read\n"
CACHE_ACTIONS = ("actions/cache", "Swatinem/rust-cache")
CACHE_INPUT = re.compile(r"^\s+cache: ", re.MULTILINE)
WHEELS_CONCURRENCY = """concurrency:
  group: wheels-${{ github.ref }}
  cancel-in-progress: ${{ github.ref_type != 'tag' }}
"""


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
    assert tolerated_exit_codes(NOXFILE.read_text(encoding="utf-8"), FUZZ_SESSION) == []


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
    assert default_exit_codes(NOXFILE.read_text(encoding="utf-8"), "_uv_run") == []


def test_default_exit_codes_sees_an_accepted_default() -> None:
    source = "def _uv_run(session, *args, success_codes=[0, 5]):\n    pass\n"
    assert default_exit_codes(source, "_uv_run") == ["[0, 5]"]


def minor_of(version: str) -> int:
    """Extracts the minor number of a 3.x version string."""
    return int(version.split(".")[1])


def test_ci_matrix_matches_requires_python() -> None:
    match = MATRIX_PYTHONS.search(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert match is not None, "ci.yml has no python matrix line"
    matrix = {item.strip().strip("\"'") for item in match.group(1).split(",")}
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
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
    assert EXTRAS_CONSTANT not in session_names(NOXFILE.read_text(encoding="utf-8"), CORE_SESSION)


def test_wheel_session_installs_every_extra() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    assert EXTRAS_CONSTANT in session_names(source, WHEEL_SESSION)
    assert module_constant(source, EXTRAS_CONSTANT) == "[all]"


def test_core_check_script_exists() -> None:
    assert (
        ROOT / module_constant(NOXFILE.read_text(encoding="utf-8"), CHECK_SCRIPT_CONSTANT)
    ).is_file()


def test_ci_runs_the_core_session_on_linux() -> None:
    job = ci_job(CI_WORKFLOW.read_text(encoding="utf-8"), CORE_SESSION)
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
    names = ci_job_names(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert names[0] == CHANGES_JOB
    assert sorted(names) == sorted([CHANGES_JOB, UNGATED_JOB, RUST_JOB, *PARITY_JOBS, *GATED_JOBS])


def test_job_value_joins_a_folded_value() -> None:
    job = "  slow:\n    if: >-\n      ${{ a ||\n      b }}\n    runs-on: x\n"
    assert job_condition(job) == "${{ a || b }}"
    assert job_value(job, "runs-on", JOB_INDENT) == "x"
    assert job_value(job, "needs", JOB_INDENT) is None


def test_ci_job_names_skip_trigger_keys() -> None:
    workflow = "on:\n  push:\n  pull_request:\n\njobs:\n  changes:\n    runs-on: x\n"
    assert ci_job_names(workflow) == ["changes"]


def test_heavy_jobs_wait_for_the_changes_job() -> None:
    assert ungated_jobs(CI_WORKFLOW.read_text(encoding="utf-8"), GATED_JOBS) == []


def test_ungated_jobs_sees_a_job_without_the_gate() -> None:
    workflow = (
        "jobs:\n"
        "  slow:\n    needs: changes\n    runs-on: x\n"
        f"  wheel:\n    needs: changes\n    if: ${{{{ {RUNS_CODE_JOBS} }}}}\n"
        f"  core:\n    if: ${{{{ {RUNS_CODE_JOBS} }}}}\n"
    )
    assert ungated_jobs(workflow, ("slow", "wheel", "core")) == ["slow", "core"]


def test_single_jobs_skip_only_on_a_successful_docs_only_answer() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    for name in GATED_JOBS:
        if name not in MATRIX_JOBS:
            assert job_condition(ci_job(workflow, name)) == SINGLE_JOB_CONDITION, name


@pytest.mark.parametrize("name", MATRIX_JOBS)
def test_matrix_job_gates_its_steps_not_itself(name: str) -> None:
    job = ci_job(CI_WORKFLOW.read_text(encoding="utf-8"), name)
    assert job_condition(job) == MATRIX_JOB_CONDITION
    assert job_value(job, "RUN_CODE_JOBS", ENV_INDENT) == MATRIX_STEP_ENV
    steps = STEP_START.split(job)[1:]
    assert steps
    assert all(MATRIX_STEP_GATE.search(f"      - {step}") for step in steps)


def test_lint_types_runs_on_every_change() -> None:
    job = ci_job(CI_WORKFLOW.read_text(encoding="utf-8"), UNGATED_JOB)
    assert job
    assert "    needs:" not in job
    assert job_condition(job) is None


def test_changes_job_runs_the_classifier_on_full_history() -> None:
    job = ci_job(CI_WORKFLOW.read_text(encoding="utf-8"), CHANGES_JOB)
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
    source = NOXFILE.read_text(encoding="utf-8")
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
    runs = pytest_runs(NOXFILE.read_text(encoding="utf-8"), SLOW_SESSION)
    assert [last_value(run, MARK_FLAG) for run in runs] == [PARALLEL_MARKS, TIMING_MARKS]


def test_slow_session_runs_timing_tests_without_workers() -> None:
    """Ends the timing run with `-n 0`, after the posargs, so no posarg can give it workers."""
    runs = pytest_runs(NOXFILE.read_text(encoding="utf-8"), SLOW_SESSION)
    timing = [run for run in runs if last_value(run, MARK_FLAG) == TIMING_MARKS]
    assert timing
    assert all(run[-2:] == [WORKERS_FLAG, NO_WORKERS] for run in timing)


def test_slow_session_runs_the_other_slow_tests_on_workers() -> None:
    runs = pytest_runs(NOXFILE.read_text(encoding="utf-8"), SLOW_SESSION)
    parallel = [run for run in runs if last_value(run, MARK_FLAG) == PARALLEL_MARKS]
    assert parallel
    for run in parallel:
        workers = last_value(run, WORKERS_FLAG) or NO_WORKERS
        assert workers == "auto" or int(workers) > 1


def test_slow_session_gives_each_module_to_one_worker() -> None:
    """Passes `--dist loadscope` to the parallel run, so a module fixture is built once."""
    runs = pytest_runs(NOXFILE.read_text(encoding="utf-8"), SLOW_SESSION)
    parallel = [run for run in runs if last_value(run, MARK_FLAG) == PARALLEL_MARKS]
    assert parallel
    assert all(last_value(run, DIST_FLAG) == MODULE_DIST for run in parallel)
    timing = [run for run in runs if last_value(run, MARK_FLAG) == TIMING_MARKS]
    assert all(DIST_FLAG not in run for run in timing)


def test_serial_sessions_start_no_workers() -> None:
    """Keeps the timing tests of the default suite in one process in every full-suite gate."""
    source = NOXFILE.read_text(encoding="utf-8")
    for name in SERIAL_SESSIONS:
        runs = pytest_runs(source, name)
        assert runs, name
        assert all(WORKERS_FLAG not in run for run in runs), name
    addopts = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"][
        "addopts"
    ]
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


def test_uv_rebuilds_the_extension_when_rust_sources_change() -> None:
    keys = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["uv"]["cache-keys"]
    assert {key["file"] for key in keys if "file" in key} >= RUST_BUILD_INPUTS


def _first_index(steps: list[str], found: Callable[[str], bool]) -> int | None:
    return next((index for index, step in enumerate(steps) if found(step)), None)


def unprepared_rust_jobs(workflow: str) -> list[str]:
    """Lists the jobs that reach uv before installing the pinned toolchain and the Rust cache.

    Args:
        workflow: The text of a workflow file.

    Returns:
        unprepared: The jobs with a `uv sync` or `uv run` step that no earlier
            `rustup toolchain install` step, followed by a SHA-pinned Swatinem/rust-cache step,
            prepares.
    """
    unprepared = []
    for name in ci_job_names(workflow):
        steps = STEP_START.split(ci_job(workflow, name))[1:]
        uv = _first_index(steps, lambda step: UV_STEP.search(step) is not None)
        if uv is None:
            continue
        toolchain = _first_index(steps, lambda step: TOOLCHAIN_STEP in step)
        cache = _first_index(steps, lambda step: RUST_CACHE_STEP.search(step) is not None)
        if toolchain is None or cache is None or not toolchain < cache < uv:
            unprepared.append(name)
    return unprepared


def test_every_job_that_syncs_installs_the_pinned_rust_toolchain() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert unprepared_rust_jobs(workflow) == []
    assert "check" in [
        name for name in ci_job_names(workflow) if UV_STEP.search(ci_job(workflow, name))
    ]


def test_unprepared_rust_jobs_sees_a_missing_or_late_step() -> None:
    toolchain = "      - run: rustup toolchain install\n"
    cache = f"      - uses: Swatinem/rust-cache@{'a' * 40} # v2\n"
    tag_cache = "      - uses: Swatinem/rust-cache@v2\n"
    sync = "      - run: uv sync --locked\n"
    jobs = {
        "ready": toolchain + cache + sync,
        "late": sync + toolchain + cache,
        "untagged": toolchain + tag_cache + sync,
        "swapped": cache + toolchain + sync,
        "docs": "      - run: echo docs\n",
    }
    workflow = "on: push\njobs:\n" + "".join(
        f"  {name}:\n    steps:\n{steps}" for name, steps in jobs.items()
    )
    assert unprepared_rust_jobs(workflow) == ["late", "untagged", "swapped"]


def rust_caches_saved_off_main(workflow: str) -> list[str]:
    """Lists the jobs with a Rust cache step that may save its cache outside the main branch.

    Args:
        workflow: The text of a workflow file.

    Returns:
        leaky: One job name per Swatinem/rust-cache step that lacks the main-only `save-if`.
    """
    return [
        name
        for name in ci_job_names(workflow)
        for step in STEP_START.split(ci_job(workflow, name))[1:]
        if "Swatinem/rust-cache@" in step and not SAVE_ON_MAIN_ONLY.search(step)
    ]


def test_rust_cache_saves_only_from_main() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count("Swatinem/rust-cache@") >= len(GATED_JOBS)
    assert rust_caches_saved_off_main(workflow) == []


def test_rust_caches_saved_off_main_sees_a_missing_or_wrong_condition() -> None:
    cache = f"      - uses: Swatinem/rust-cache@{'a' * 40} # v2\n"
    jobs = {
        "main-only": cache
        + "        with:\n          save-if: ${{ github.ref == 'refs/heads/main' }}\n",
        "always": cache,
        "any-branch": cache + "        with:\n          save-if: ${{ true }}\n",
    }
    workflow = "on: push\njobs:\n" + "".join(
        f"  {name}:\n    steps:\n{steps}" for name, steps in jobs.items()
    )
    assert rust_caches_saved_off_main(workflow) == ["always", "any-branch"]


def _is_locked_env(source: str, value: ast.expr) -> bool:
    """Tells whether a build's env= names a module constant that passes cargo's --locked."""
    if not isinstance(value, ast.Name):
        return False
    environment = module_literal(source, value.id)
    return isinstance(environment, dict) and LOCKED_FLAG in environment[BUILD_ARGS].split()


def unlocked_uv_builds(source: str) -> list[str]:
    """Lists the noxfile functions that run `uv build` without cargo's --locked in its env.

    Args:
        source: The text of a noxfile.

    Returns:
        functions: One function name per `uv build` call whose env= is not a module constant
            holding --locked in MATURIN_PEP517_ARGS.
    """
    return [
        function.name
        for function in ast.walk(ast.parse(source))
        if isinstance(function, ast.FunctionDef)
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and [ast.unparse(arg) for arg in call.args[:2]] == ["'uv'", "'build'"]
        and not any(
            keyword.arg == "env" and _is_locked_env(source, keyword.value)
            for keyword in call.keywords
        )
    ]


def test_wheel_builds_fail_on_a_stale_cargo_lock() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    constant = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == LOCKED_BUILD
    )
    assert ast.literal_eval(constant) == {"MATURIN_PEP517_ARGS": "--locked"}
    assert "'uv', 'build'" in ast.unparse(tree)
    assert unlocked_uv_builds(source) == []


def test_unlocked_uv_builds_sees_a_build_without_the_lock() -> None:
    source = (
        "LOCKED_BUILD = {'MATURIN_PEP517_ARGS': '--locked'}\n"
        "DIST_BUILD = {'MATURIN_PEP517_ARGS': '--locked --profile dist'}\n"
        "LOOSE_BUILD = {'MATURIN_PEP517_ARGS': '--profile dist'}\n"
        "def locked(session):\n"
        "    session.run('uv', 'build', '--wheel', env=LOCKED_BUILD)\n"
        "def dist(session):\n"
        "    session.run('uv', 'build', '--wheel', env=DIST_BUILD)\n"
        "def bare(session):\n"
        "    session.run('uv', 'build', '--sdist')\n"
        "def other(session):\n"
        "    session.run('uv', 'build', env={})\n"
        "def loose(session):\n"
        "    session.run('uv', 'build', env=LOOSE_BUILD)\n"
    )
    assert unlocked_uv_builds(source) == ["bare", "other", "loose"]


def session_runs(source: str, function_name: str) -> list[list[str]]:
    """Lists the arguments of every `session.run` call one noxfile function makes.

    Args:
        source: The text of a noxfile.
        function_name: The function to inspect.

    Returns:
        runs: Per call in source order, its positional arguments with module constants
            resolved and starred arguments kept as source text.
    """
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    calls = sorted(
        (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == SESSION_RUN
        ),
        key=lambda call: (call.lineno, call.col_offset),
    )
    return [[_argument_text(source, argument) for argument in call.args] for call in calls]


def test_rust_session_runs_fmt_clippy_test_and_the_wasm_build() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    assert session_runs(source, RUST_SESSION) == RUST_SESSION_RUNS
    assert FMA_STEP in session_names(source, RUST_SESSION)


def session_env(source: str, function_name: str) -> dict[str, tuple[str, int]]:
    """Lists the `session.env[KEY] = value` assignments one noxfile function makes.

    Args:
        source: The text of a noxfile.
        function_name: The function to inspect.

    Returns:
        assignments: Key, with module constants resolved, to the value's source text and line.
    """
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    found: dict[str, tuple[str, int]] = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Subscript) and ast.unparse(target.value) == "session.env":
                key = _argument_text(source, target.slice)
                found[key] = (ast.unparse(node.value), node.lineno)
    return found


def test_regression_rust_session_builds_against_the_project_interpreter() -> None:
    """The crate tests embed the interpreter pyo3 builds against; on macOS CI that was a
    framework Python whose own scheme hid the project's site-packages, so e8s did not import."""
    source = NOXFILE.read_text(encoding="utf-8")
    env = session_env(source, RUST_SESSION)
    assert {key: value for key, (value, _) in env.items()} == RUST_SESSION_ENV
    rust = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == RUST_SESSION
    )
    first_cargo = min(
        node.lineno
        for node in ast.walk(rust)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == SESSION_RUN
        and node.args
        and _argument_text(source, node.args[0]) == "cargo"
    )
    assert all(line < first_cargo for _, line in env.values())


def uv_runs(source: str, function_name: str) -> list[list[str]]:
    """Lists the arguments of every `_uv_run` call one noxfile function makes, in source order."""
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    calls = sorted(
        (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == UV_RUN
        ),
        key=lambda call: (call.lineno, call.col_offset),
    )
    return [[_argument_text(source, argument) for argument in call.args[1:]] for call in calls]


def test_rust_session_checks_crate_licences_and_the_notices() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    assert {"_install_cargo_tool", "CARGO_DENY", "CARGO_ABOUT"} <= session_names(
        source, RUST_SESSION
    )
    assert uv_runs(source, RUST_SESSION)[-2:] == [NOTICES_CHECK, CARGO_TOOLS_RUN]


def test_types_session_checks_the_core_stubs() -> None:
    assert uv_runs(NOXFILE.read_text(encoding="utf-8"), TYPES_SESSION) == TYPES_SESSION_RUNS


def test_wheel_sessions_refuse_an_rpath_or_libpython_in_the_extension() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    script = module_constant(source, "LINK_CHECK_SCRIPT")
    assert (ROOT / script).is_file()
    for session in WHEEL_BUILDING_SESSIONS:
        assert ["python", script, "str(built)"] in uv_runs(source, session), session


def test_cargo_tools_install_locked_at_pinned_versions_into_the_repository() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    assert module_constant(source, "CARGO_TOOLS_ROOT") == CARGO_TOOLS_ROOT
    for name in ("CARGO_DENY_VERSION", "CARGO_ABOUT_VERSION"):
        assert PINNED_VERSION.match(module_constant(source, name)), name
    assert set(session_strings(source, "_install_cargo_tool")) >= CARGO_INSTALL
    assert "CARGO_TOOLS_ROOT" in session_names(source, "_install_cargo_tool")


def test_fma_check_builds_the_extension_and_runs_the_script() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    assert session_runs(source, FMA_STEP) == [FMA_CHECK_BUILD]
    assert FMA_CHECK_SCRIPT in session_names(source, FMA_STEP)
    assert (ROOT / module_constant(source, FMA_CHECK_SCRIPT)).is_file()


def test_fma_check_profile_keeps_symbols_and_line_tables() -> None:
    profiles = tomllib.loads(CARGO_MANIFEST.read_text(encoding="utf-8"))["profile"]
    assert profiles.get("fma-check") == FMA_CHECK_PROFILE


def test_session_runs_keeps_source_order() -> None:
    source = (
        "TARGET = 'wasm'\n\n"
        "def rust(session):\n"
        "    if session:\n"
        "        session.run('cargo', 'test')\n"
        "    session.run('cargo', 'build', TARGET, *flags)\n"
        "    other.run('cargo', 'skip')\n"
    )
    assert session_runs(source, "rust") == [["cargo", "test"], ["cargo", "build", "wasm", "*flags"]]


def module_literal(source: str, name: str) -> object:
    """Returns the literal value of one module-level constant of a noxfile."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"noxfile defines no {name}")


def uv_build_envs(source: str, function_name: str) -> list[str]:
    """Lists the env= argument of each `uv build` call of one noxfile function, in source order."""
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    calls = sorted(
        (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and [ast.unparse(arg) for arg in node.args[:2]] == ["'uv'", "'build'"]
        ),
        key=lambda call: (call.lineno, call.col_offset),
    )
    return [
        ast.unparse(keyword.value)
        for call in calls
        for keyword in call.keywords
        if keyword.arg == "env"
    ]


def test_wheels_session_tests_the_wheel_on_313_and_314() -> None:
    """Tests the wheel a user gets: built from the sdist with the dist profile, cargo locked."""
    source = NOXFILE.read_text(encoding="utf-8")
    classifiers = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["classifiers"]
    supported = tuple(
        match.group(1) for match in map(PYTHON_CLASSIFIER.match, classifiers) if match
    )
    assert module_literal(source, "WHEEL_TEST_PYTHONS") == WHEEL_TEST_PYTHONS == supported
    assert module_literal(source, "PORTED_TESTS") == PORTED_TESTS
    assert module_literal(source, "DIST_BUILD") == DIST_BUILD
    assert session_runs(source, WHEEL_FROM_SDIST) == SDIST_THEN_WHEEL
    assert uv_build_envs(source, WHEEL_FROM_SDIST) == [LOCKED_BUILD, "DIST_BUILD"]
    assert WHEEL_FROM_SDIST in session_names(source, WHEELS_SESSION)
    assert "WHEEL_TEST_PYTHONS" in session_names(source, WHEELS_SESSION)
    assert PORTED_TEST_RUN in session_runs(source, WHEELS_SESSION)


def job_steps(job: str) -> list[str]:
    """Splits one job's text into its steps, each without its leading `      - `."""
    return STEP_START.split(job)[1:]


def step_value(step: str, key: str, indent: int = STEP_INDENT) -> str:
    """Returns one key's value in a step from job_steps, folded lines joined; empty when absent."""
    return job_value(" " * STEP_INDENT + step, key, indent) or ""


def test_rust_job_runs_the_rust_session() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    job = ci_job(workflow, RUST_JOB)
    assert NEEDS_CHANGES.search(job)
    assert job_condition(job) == MATRIX_JOB_CONDITION
    assert job_value(job, "RUN_RUST_JOB", ENV_INDENT) == RUST_STEP_ENV
    assert job_value(job, "os", ENV_INDENT + 2) == RUST_JOB_OSES
    steps = job_steps(job)
    assert steps
    assert all(RUST_STEP_GATE.match(step_value(step, "if")) for step in steps)
    assert sum(RUST_SESSION_STEP in step for step in steps) == 1
    assert RUST_OUTPUT in ci_job(workflow, CHANGES_JOB)


def pinned_version_lines(source: str, sessions: tuple[str, ...] = (RUST_SESSION,)) -> list[str]:
    """Returns the noxfile lines that assign the cargo tool versions some sessions install.

    Args:
        source: The text of a noxfile.
        sessions: The sessions whose `_install_cargo_tool` calls are read.

    Returns:
        lines: The assignments of the version constants those calls pass, in source order.
    """
    names = {
        ast.unparse(call.args[2])
        for function in ast.walk(ast.parse(source))
        if isinstance(function, ast.FunctionDef) and function.name in sessions
        for call in ast.walk(function)
        if isinstance(call, ast.Call) and ast.unparse(call.func) == INSTALL_CARGO_TOOL
    }
    return [line for line in source.splitlines() if line.split(" = ")[0] in names]


def cargo_tools_cache_key_problems(
    job: str,
    noxfile: str,
    *,
    sessions: tuple[str, ...] = (RUST_SESSION,),
    run_step: str = RUST_SESSION_STEP,
) -> list[str]:
    """Lists what lets a job's cached cargo tools outlive the versions the noxfile pins.

    Args:
        job: The text of the job that runs the sessions.
        noxfile: The text of the noxfile that pins the cargo tool versions.
        sessions: The sessions whose cargo tools the job caches.
        run_step: The step text that runs the first of those sessions; the restore comes before
            it and the save after it.

    Returns:
        problems: One line per missing restore, key part, version line, ordering or main-only
            save; empty when the cache is keyed by every pin and saved only from main.
    """
    steps = job_steps(job)
    found = [
        _first_index(steps, lambda step: step_value(step, "id") == CARGO_TOOLS_VERSIONS_ID),
        _first_index(steps, lambda step: CACHE_RESTORE_STEP.search(step) is not None),
        _first_index(steps, lambda step: run_step in step),
        _first_index(steps, lambda step: CACHE_SAVE_STEP.search(step) is not None),
    ]
    indices = [index for index in found if index is not None]
    if len(indices) != len(found):
        return ["missing step"]
    versions, restore, run, save = indices
    problems = [] if versions < restore < run < save else ["steps out of order"]
    key = step_value(steps[restore], "key", WITH_INDENT)
    problems += [f"key lacks {part}" for part in CARGO_TOOLS_KEY_PARTS if part not in key]
    problems += [
        f"{step} does not cache .cargo-tools"
        for step, index in (("restore", restore), ("save", save))
        if not CARGO_TOOLS_PATH.search(steps[index])
    ]
    grep = VERSIONS_GREP.search(step_value(steps[versions], "run"))
    pins = [*TOOLCHAIN_FILE.read_text(encoding="utf-8").splitlines(), *noxfile.splitlines()]
    matched = [line for line in pins if grep and re.search(grep[1], line)]
    expected = [line for line in pins if TOOLCHAIN_PIN.match(line)]
    if matched != [*expected[:1], *pinned_version_lines(noxfile, sessions)]:
        problems.append("versions step does not read exactly the pinned versions")
    if SAVE_FROM_MAIN not in step_value(steps[save], "if"):
        problems.append("save is not limited to main")
    return problems


def test_rust_job_caches_the_cargo_tools_by_their_pinned_versions() -> None:
    noxfile = NOXFILE.read_text(encoding="utf-8")
    assert len(pinned_version_lines(noxfile)) == 2
    job = ci_job(CI_WORKFLOW.read_text(encoding="utf-8"), RUST_JOB)
    assert cargo_tools_cache_key_problems(job, noxfile) == []


def test_cargo_tools_cache_key_problems_sees_a_loose_key() -> None:
    noxfile = (
        'CARGO_DENY_VERSION = "1.0.0"\nCARGO_ABOUT_VERSION = "2.0.0"\nOTHER_VERSION = "3"\n'
        "def rust(session):\n"
        "    _install_cargo_tool(session, CARGO_DENY, CARGO_DENY_VERSION)\n"
        "    _install_cargo_tool(session, CARGO_ABOUT, CARGO_ABOUT_VERSION)\n"
    )
    versions = (
        "      - id: pins\n        run: >-\n"
        "          grep -hE '^(channel|CARGO_[A-Z]+_VERSION) = ' rust-toolchain.toml\n"
        "          noxfile.py\n"
    )
    restore = (
        f"      - id: restore\n        uses: actions/cache/restore@{'a' * 40} # v6\n"
        "        with:\n          path: .cargo-tools\n"
        "          key: tools-${{ runner.os }}-${{ runner.arch }}-"
        "${{ steps.pins.outputs.versions }}\n"
    )
    run = "      - run: uv run --locked nox -s rust\n"
    save = (
        f"      - if: env.RUN_RUST_JOB == 'true' && {SAVE_FROM_MAIN}\n"
        f"        uses: actions/cache/save@{'a' * 40} # v6\n"
        "        with:\n          path: .cargo-tools\n"
    )
    assert cargo_tools_cache_key_problems(versions + restore + run + save, noxfile) == []
    assert cargo_tools_cache_key_problems(restore + run + save, noxfile) == ["missing step"]
    assert cargo_tools_cache_key_problems(versions + run + restore + save, noxfile) == [
        "steps out of order"
    ]
    loose = restore.replace("-${{ steps.pins.outputs.versions }}", "")
    assert cargo_tools_cache_key_problems(versions + loose + run + save, noxfile) == [
        "key lacks ${{ steps.pins.outputs.versions }}"
    ]
    wide = versions.replace("CARGO_[A-Z]+_VERSION", "[A-Z_]+_VERSION")
    assert cargo_tools_cache_key_problems(wide + restore + run + save, noxfile) == [
        "versions step does not read exactly the pinned versions"
    ]
    anywhere = save.replace(f" && {SAVE_FROM_MAIN}", "")
    assert anywhere != save
    assert cargo_tools_cache_key_problems(versions + restore + run + anywhere, noxfile) == [
        "save is not limited to main"
    ]


def workflow_section(workflow: str, key: str) -> str:
    """Returns one top-level key of a workflow with its indented body, empty when absent."""
    found = re.search(rf"^{re.escape(key)}:.*\n(?:(?: .*)?\n)*", workflow, re.MULTILINE)
    return found[0].rstrip() + "\n" if found else ""


def test_workflow_section_stops_at_the_next_top_level_key() -> None:
    workflow = "name: x\n\non:\n  push:\n\n  pull_request:\n\npermissions: {}\n"
    assert workflow_section(workflow, "on") == "on:\n  push:\n\n  pull_request:\n"
    assert workflow_section(workflow, "env") == ""


def test_wheels_workflow_runs_on_rust_changes_and_tags() -> None:
    assert workflow_section(WHEELS_WORKFLOW.read_text(encoding="utf-8"), "on") == WHEELS_TRIGGERS


def matrix_entries(job: str) -> list[dict[str, str]]:
    """Reads the block mappings under a job's `include:` matrix key."""
    entries: list[dict[str, str]] = []
    lines = job.split("\n        include:\n", 1)[-1].splitlines() if "include:" in job else []
    for line in itertools.takewhile(lambda text: text.startswith(MATRIX_ITEM_INDENT), lines):
        key, value = line.strip().removeprefix("- ").split(": ", 1)
        if line.lstrip().startswith("- "):
            entries.append({})
        entries[-1][key] = value.strip('"')
    return entries


def test_matrix_entries_reads_each_block_mapping() -> None:
    job = (
        "  linux:\n    strategy:\n      matrix:\n        include:\n"
        '          - runner: a\n            libc: "2_28"\n          - runner: b\n'
        "    steps:\n"
    )
    assert matrix_entries(job) == [{"runner": "a", "libc": "2_28"}, {"runner": "b"}]
    assert matrix_entries("  sdist:\n    steps:\n") == []


def maturin_steps(job: str) -> list[str]:
    """Returns the maturin-action steps of one job."""
    return [step for step in job_steps(job) if "uses: PyO3/maturin-action@" in step]


def build_targets(workflow: str) -> list[tuple[str, str, str]]:
    """Lists (job, runner, target and libc) for every wheel the workflow builds."""
    return [
        (name, entry["runner"], f"{entry['target']} {entry.get('libc', '')}".strip())
        for name in WHEEL_JOBS
        for entry in matrix_entries(ci_job(workflow, name))
    ]


def test_wheels_workflow_builds_eight_wheels_and_the_sdist() -> None:
    workflow = WHEELS_WORKFLOW.read_text(encoding="utf-8")
    assert ci_job_names(workflow) == [VERSION_JOB, *WHEEL_JOBS, SDIST_JOB]
    assert sorted(build_targets(workflow)) == sorted(WHEEL_TARGETS)
    requires = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["build-system"]["requires"]
    maturin = (job_value(workflow_section(workflow, "env"), "MATURIN_VERSION", 2) or "").strip('"')
    assert requires == [f"maturin=={maturin}"]
    for name in WHEEL_JOBS:
        job = ci_job(workflow, name)
        (build,) = maturin_steps(job)
        assert step_value(build, "args", WITH_INDENT).startswith(DIST_BUILD_ARGS), name
        assert step_value(build, "maturin-version", WITH_INDENT) == "${{ env.MATURIN_VERSION }}"
        assert sum(UPLOAD_STEP in step for step in job_steps(job)) == 1, name
    (sdist,) = maturin_steps(ci_job(workflow, SDIST_JOB))
    assert step_value(sdist, "command", WITH_INDENT) == "sdist"
    assert LOCK_UNCHANGED in ci_job(workflow, SDIST_JOB)
    assert workflow.count("if-no-files-found: error") == len(WHEEL_JOBS) + 1


def test_wheels_jobs_wait_for_the_release_version_check() -> None:
    workflow = WHEELS_WORKFLOW.read_text(encoding="utf-8")
    for name in (*WHEEL_JOBS, SDIST_JOB):
        assert job_value(ci_job(workflow, name), "needs", JOB_INDENT) == VERSION_JOB, name
        assert job_condition(ci_job(workflow, name)) is None, name
    steps = job_steps(ci_job(workflow, VERSION_JOB))
    assert [step_value(step, "if") for step in steps] == [ON_A_TAG, ON_A_TAG]
    assert step_value(steps[-1], "run") == RELEASE_VERSION_RUN
    assert step_value(steps[-1], "TAG", WITH_INDENT) == "${{ github.ref_name }}"


def test_wheels_test_the_installed_wheel_on_313_and_314() -> None:
    workflow = WHEELS_WORKFLOW.read_text(encoding="utf-8")
    env = workflow_section(workflow, "env")
    assert tuple((job_value(env, "PORTED_TESTS", 2) or "").split()) == PORTED_TESTS
    assert job_value(env, "PYTHONHASHSEED", 2) == '"0"'
    linux = ci_job(workflow, "linux")
    assert IMAGE_LOOP in linux
    for name in ("windows", "macos"):
        job = ci_job(workflow, name)
        versions = [step_value(step, "python-version", WITH_INDENT) for step in job_steps(job)]
        assert sorted(version.strip('"') for version in versions if version) == sorted(
            WHEEL_TEST_PYTHONS
        )
        assert 'for python in "$PYTHON_313" "$PYTHON_314"; do' in job
    for name in WHEEL_JOBS:
        job = ci_job(workflow, name)
        assert sum(TESTS_FROM_SUITE in step for step in job_steps(job)) == 1, name
        assert WHEEL_INSTALL.search(job), name
        assert '-P -c "$WHEEL_CHECK"' in job, name
        assert "-P -m pytest $PORTED_TESTS" in job, name


def _module_files(root: Path, module: str) -> list[Path]:
    """Returns the source file of one `tests` module under root, or nothing for a non-module."""
    base = root.joinpath(*module.split("."))
    return [path for path in (base.with_suffix(".py"), base / "__init__.py") if path.is_file()]


def imported_modules(roots: list[Path], root: Path = ROOT) -> set[str]:
    """Lists the third-party modules that test files and the `tests` modules they import load.

    Args:
        roots: Test files and folders to start from.
        root: The repository root that holds the `tests` package.

    Returns:
        modules: Top-level names that are neither stdlib, e8s nor tests.
    """
    pending = [
        path for root in roots for path in ([root] if root.is_file() else root.rglob("*.py"))
    ]
    seen: set[Path] = set()
    found: set[str] = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
            for name in names:
                top = name.split(".")[0]
                if top == FIRST_PARTY_TESTS:
                    pending += _module_files(root, name)
                elif top not in sys.stdlib_module_names and top != FIRST_PARTY:
                    found.add(top)
    return found


def test_imported_modules_follows_test_modules(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "helpers.py").write_text(
        "import hypothesis.strategies\n", encoding="utf-8"
    )
    test = tmp_path / "test_x.py"
    test.write_text(
        "import os\nimport numpy.linalg\nfrom tests import helpers\nimport e8s\n", encoding="utf-8"
    )
    assert imported_modules([test], tmp_path) == {"numpy", "hypothesis"}


def requirement_name(spec: str) -> str:
    """Returns the distribution name a requirement string starts with."""
    found = REQUIREMENT_NAME.match(spec)
    assert found, spec
    return found[0]


def test_wheels_install_every_package_the_ported_tests_import() -> None:
    workflow = WHEELS_WORKFLOW.read_text(encoding="utf-8")
    tools = (job_value(workflow_section(workflow, "env"), "WHEEL_TEST_TOOLS", 2) or "").split()
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    metadata = project["project"]
    requirements = [*metadata["dependencies"], *metadata["optional-dependencies"]["all"]]
    runtime = {requirement_name(spec).replace("-", "_") for spec in requirements}
    roots = [ROOT / path for path in (*PORTED_TESTS, *SUITE_ENTRY_FILES)]
    assert imported_modules(roots) - runtime == set(tools)
    dev = {requirement_name(spec) for spec in project["dependency-groups"]["dev"]}
    assert set(tools) <= dev


def unpinned_actions(workflow: str) -> list[str]:
    """Lists the `uses:` lines of a workflow that do not pin a full commit SHA with its tag."""
    return [
        line.strip()
        for line in workflow.splitlines()
        if line.strip().removeprefix("- ").startswith("uses:") and not PINNED_ACTION.search(line)
    ]


def test_every_wheels_action_is_pinned_to_a_sha() -> None:
    workflow = WHEELS_WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count("uses: ") >= len(WHEEL_JOBS) * 3
    assert unpinned_actions(workflow) == []


def test_unpinned_actions_sees_a_tag_or_short_sha() -> None:
    workflow = (
        f"      - uses: actions/checkout@{'a' * 40} # v7.0.1\n"
        "      - uses: actions/checkout@v7\n"
        f"        uses: actions/cache/save@{'b' * 39} # v6\n"
        f"        uses: PyO3/maturin-action@{'c' * 40}\n"
    )
    assert unpinned_actions(workflow) == [
        "- uses: actions/checkout@v7",
        f"uses: actions/cache/save@{'b' * 39} # v6",
        f"uses: PyO3/maturin-action@{'c' * 40}",
    ]


def test_wheels_workflow_holds_least_privilege_and_writes_no_cache() -> None:
    workflow = WHEELS_WORKFLOW.read_text(encoding="utf-8")
    assert workflow_section(workflow, "permissions") == "permissions: {}\n"
    for name in ci_job_names(workflow):
        assert READ_ONLY_JOB in ci_job(workflow, name), name
    assert workflow.count("permissions:") == len(ci_job_names(workflow)) + 1
    checkouts = workflow.count("uses: actions/checkout@")
    assert workflow.count("persist-credentials: false") == checkouts
    assert not any(cache in workflow for cache in CACHE_ACTIONS)
    assert workflow.count("enable-cache: false") == workflow.count("uses: astral-sh/setup-uv@")
    assert not CACHE_INPUT.search(workflow)


def test_wheel_builds_check_the_pinned_toolchain() -> None:
    workflow = WHEELS_WORKFLOW.read_text(encoding="utf-8")
    (build,) = maturin_steps(ci_job(workflow, "linux"))
    assert step_value(build, "before-script-linux", WITH_INDENT) == "${{ env.CHECK_PINNED_RUSTC }}"
    for name in ("windows", "macos"):
        steps = job_steps(ci_job(workflow, name))
        build_index = _first_index(steps, lambda step: "uses: PyO3/maturin-action@" in step)
        assert build_index is not None
        assert step_value(steps[build_index + 1], "run") == 'eval "$CHECK_PINNED_RUSTC"', name


def run_toolchain_check(folder: Path, rustc_version: str) -> subprocess.CompletedProcess[str]:
    """Runs CHECK_PINNED_RUSTC in folder against a fake rustc that prints one version line."""
    check = job_value(
        workflow_section(WHEELS_WORKFLOW.read_text(encoding="utf-8"), "env"),
        "CHECK_PINNED_RUSTC",
        2,
    )
    assert check
    toolchain = TOOLCHAIN_FILE.read_text(encoding="utf-8")
    (folder / "rust-toolchain.toml").write_text(toolchain, encoding="utf-8")
    bin_folder = folder / "bin"
    bin_folder.mkdir(exist_ok=True)
    rustc = bin_folder / "rustc"
    rustc.write_text(
        f"#!/bin/sh\necho 'rustc {rustc_version} (0ef1bc386 2026-11-09)'\n", encoding="utf-8"
    )
    rustc.chmod(0o755)
    path = f"{bin_folder}{os.pathsep}{os.environ['PATH']}"
    return subprocess.run(
        ["bash", "-c", check],
        cwd=folder,
        env={**os.environ, "PATH": path},
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="the fake rustc is a POSIX shell script")
def test_toolchain_check_refuses_a_rustc_other_than_the_pin(tmp_path: Path) -> None:
    channel = tomllib.loads(TOOLCHAIN_FILE.read_text(encoding="utf-8"))["toolchain"]["channel"]
    assert run_toolchain_check(tmp_path, channel).returncode == 0
    refused = run_toolchain_check(tmp_path, f"{channel}.1")
    assert refused.returncode == 1
    assert refused.stdout == f"E: rustc is not the pinned {channel}\n"


def test_regression_wheels_never_cancel_a_tag_run() -> None:
    """A second dispatch or a re-pushed tag must not kill a release build in progress."""
    assert (
        workflow_section(WHEELS_WORKFLOW.read_text(encoding="utf-8"), "concurrency")
        == WHEELS_CONCURRENCY
    )


def test_regression_wheels_upload_before_the_tests_run() -> None:
    """The tests install packages beside dist/, so the wheel is uploaded before they run."""
    workflow = WHEELS_WORKFLOW.read_text(encoding="utf-8")
    for name in WHEEL_JOBS:
        steps = job_steps(ci_job(workflow, name))
        build = _first_index(steps, lambda step: "uses: PyO3/maturin-action@" in step)
        upload = _first_index(steps, lambda step: UPLOAD_STEP in step)
        tests = _first_index(steps, lambda step: PORTED_TEST_STEP in step)
        assert build is not None, name
        assert upload is not None, name
        assert tests is not None, name
        assert build < upload < tests, name


def unpinned_images(workflow: str) -> list[str]:
    """Lists the python image references of a workflow that name no digest."""
    text = EXPRESSION.sub(r"<\1>", workflow)
    return [ref for ref in IMAGE_REFERENCE.findall(text) if not PINNED_IMAGE.search(ref)]


def test_unpinned_images_sees_a_tag_without_a_digest() -> None:
    digest = f"sha256:{'a' * 64}"
    workflow = (
        f"  A: python:3.13-slim@{digest}\n"
        "  B: python:3.14-${{ matrix.image }}@${{ matrix.digest_314 }}\n"
        '  C: "python:$python-$IMAGE_VARIANT" sh\n'
        "  D: python:3.13-alpine\n"
        f"  E: python:3.13-slim@sha256:{'a' * 63}\n"
    )
    assert unpinned_images(workflow) == [
        "python:$python-$IMAGE_VARIANT",
        "python:3.13-alpine",
        f"python:3.13-slim@sha256:{'a' * 63}",
    ]


def test_regression_wheel_test_images_are_pinned_by_digest() -> None:
    """The Linux tests ran in floating python tags; they run in digest-pinned images now."""
    workflow = WHEELS_WORKFLOW.read_text(encoding="utf-8")
    assert unpinned_images(workflow) == []
    linux = ci_job(workflow, "linux")
    entries = matrix_entries(linux)
    assert entries
    digests: dict[str, tuple[str, str]] = {}
    for entry in entries:
        pair = (entry["digest_313"], entry["digest_314"])
        assert all(IMAGE_DIGEST.match(digest) for digest in pair), entry
        assert digests.setdefault(entry["image"], pair) == pair, entry
    (test,) = [step for step in job_steps(linux) if PORTED_TEST_STEP in step]
    for version in WHEEL_TEST_PYTHONS:
        name = version.replace(".", "")
        assert step_value(test, f"IMAGE_{name}", WITH_INDENT) == (
            f"python:{version}-${{{{ matrix.image }}}}@${{{{ matrix.digest_{name} }}}}"
        )
    assert IMAGE_LOOP in test
    assert '"$image" sh -euc' in test
