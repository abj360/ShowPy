#!/usr/bin/env python3

"""
noxfile.py --- single task runner for every local, hook and CI gate

Contains:
    lint: checks formatting, lint rules, dependency declarations and Prettier formatting
    types: runs mypy --strict and pyright (basic) over the library, tests and scripts, then
        checks the stubs of the compiled core against the extension with stubtest
    tests: runs the full pytest suite under a fixed hash seed with a branch-coverage floor
    hashseeds: runs the full pytest suite under each non-default seed in HASH_SEEDS
    slow: runs the slow tests under each seed in SLOW_HASH_SEEDS, on SLOW_WORKERS pytest-xdist
        workers that each take whole modules, except the timing tests, which run in one
        process
    fuzz: runs the fuzz targets under the nightly Hypothesis profile
    storagefuzz: fuzzes every storage property test in turn under Atheris and libFuzzer, each in
        its own process, or lists them, or fuzzes or replays one, on the nightly Hypothesis
        profile
    audit: scans the locked dependencies for known vulnerabilities
    wheel: builds the wheel, refuses an rpath or a linked libpython in its extension, installs
        it with every extra in a clean venv and runs the README usage example
    core: installs the wheel without extras and checks imports and extra-gated calls fail closed
    rust: checks Rust formatting, lints and tests, builds the wasm crate, on arm64 hosts refuses
        any fused multiply-add outside libm's fma in the extension, then checks the crates'
        licences, bans, advisories and sources, refuses a stale fuzz lock, checks the fuzz
        crate's supply chain and that the Rust notice file is current
    wheels: builds the sdist and a dist-profile wheel from it, refuses an rpath or a linked
        libpython in its extension, then runs the ported packages' tests against that wheel in
        fresh venvs on each supported Python
    rustfuzz: runs each cargo-fuzz target of the core's parsers, or the one named, on the pinned
        nightly for a number of seconds each
    mutants: records cargo-mutants' score on e8s-core and mutmut's on the Python modules that
        ported packages keep in mutants-summary.json, failing only when a tool cannot run
    parity: compares the working tree's case outputs with a v0.4.0 oracle built into .parity/,
        failing on a gate; `--speed` also fails a timed case that is slower than the oracle
    bench: runs the budget benchmarks; at tier M fails on a >20% min-time regression
    api_changes: checks that each commit since the last release names the API names it changes
    changelog: regenerates CHANGELOG.md with git-cliff and formats it with Prettier at release time
    docs: placeholder until the docs build lands
"""

import itertools
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import nox
from nox.command import CommandFailed

PRETTIER_GLOB = "**/*.{md,yml,yaml,json}"
HASH_SEEDS = ("1", "2")
SLOW_HASH_SEEDS = ("0", "1")
# One worker per core of the hosted 4-core Linux runner; workers inherit PYTHONHASHSEED.
SLOW_WORKERS = "4"
SLOW_PARALLEL_MARKS = "slow and not timing"
# Under the default `load` split, every worker that runs a test of a module builds that
# module's fixtures; the scenario fixtures build whole shows, so each module goes to one worker.
SLOW_DIST = "loadscope"
# Timing tests compare wall-clock times, which other workers on the same cores distort
# (main CI, 2026-11-22: a 3.0x scaling bound measured 3.40x on unchanged code).
SLOW_TIMING_MARKS = "slow and timing"
NO_WORKERS = "0"
DEFAULT_HASH_SEED = "0"
FUZZ_PROFILE = "nightly"
STORAGE_FUZZ_SCRIPT = "scripts/fuzz_storage.py"
COVERAGE_FLOOR = "96"
NO_SYNC_ENV = "NOX_UV_NO_SYNC"

PYTHON_FOR_WHEEL = "3.14"
# maturin passes these to cargo, so a build fails on a stale Cargo.lock instead of rewriting it.
LOCKED_BUILD = {"MATURIN_PEP517_ARGS": "--locked"}
# Release wheels use the dist profile. A `build-args` config setting would replace these
# arguments, dropping --locked, so the profile goes here too.
DIST_BUILD = {"MATURIN_PEP517_ARGS": "--locked --profile dist"}
WHEEL_TEST_PYTHONS = ("3.13", "3.14")
# The tests of the packages the compiled core takes over; the parity harness needs the source.
PORTED_TESTS = (
    "tests/unit/core",
    "tests/unit/geo",
    "tests/unit/time",
    "tests/unit/sheet",
    "tests/unit/cast",
    "tests/unit/choreo",
    "tests/golden",
)
# pyproject.toml holds the pytest settings; the copied suite has no e8s/ source to shadow the wheel.
SUITE_FILES = ("pyproject.toml",)
SUITE_FOLDER = "tests"
SUITE_SKIP = ("__pycache__", ".results")
WHEEL_TEST_CHECK = """
import e8s
import e8s._core
assert "site-packages" in e8s.__file__, e8s.__file__
assert "site-packages" in e8s._core.__file__, e8s._core.__file__
"""
ALL_EXTRAS = "[all]"
CORE_CHECK_SCRIPT = "scripts/check_core_install.py"
CORE_MODULE = "e8s._core"
API_CHANGES_SCRIPT = "scripts/check_api_changes.py"
FMA_CHECK_SCRIPT = "scripts/check_no_fma.py"
LINK_CHECK_SCRIPT = "scripts/check_extension_links.py"
WASM_TARGET = "wasm32-unknown-unknown"
FMA_CHECK_PROFILE = "fma-check"
ARM64_MACHINES = ("arm64", "aarch64")
PYO3_PYTHON_ENV = "PYO3_PYTHON"
VIRTUAL_ENV_VAR = "VIRTUAL_ENV"
# Cargo tools install here, at these exact versions, never into ~/.cargo/bin. cargo-deny 0.22.0
# (rust-version 1.95) and cargo-about 0.9.3 (1.88) are the newest releases; both build on 1.100.0.
CARGO_TOOLS_ROOT = ".cargo-tools"
CARGO_DENY = "cargo-deny"
CARGO_DENY_VERSION = "0.22.0"
CARGO_ABOUT = "cargo-about"
CARGO_ABOUT_VERSION = "0.9.3"
# cargo-about builds its command-line binary only with this feature.
CARGO_ABOUT_FEATURES = ("--features", "cli")
# cargo-fuzz 0.13.2 and cargo-mutants 27.1.0 (rust-version 1.88) are the newest releases; both
# build on 1.100.0.
CARGO_FUZZ = "cargo-fuzz"
CARGO_FUZZ_VERSION = "0.13.2"
CARGO_MUTANTS = "cargo-mutants"
CARGO_MUTANTS_VERSION = "27.1.0"
# cargo-mutants reads its version flag only after its cargo subcommand name.
CARGO_MUTANTS_VERSION_ARGS = ("mutants", "--version")
# cargo-fuzz needs a nightly compiler for its sanitizer flags; a dated one keeps runs repeatable.
FUZZ_TOOLCHAIN = "nightly-2026-11-20"
# rustup picks this toolchain over rust-toolchain.toml's pin for every cargo and rustc call.
FUZZ_ENV = {"RUSTUP_TOOLCHAIN": FUZZ_TOOLCHAIN}
FUZZ_DIR = "fuzz"
FUZZ_MANIFEST = "fuzz/Cargo.toml"
FUZZ_TARGETS = ("refs", "strict_json", "svg_path", "font", "selection", "timeline")
FUZZ_SECONDS = "60"
SECONDS_FLAG = "--seconds"
RUSTFUZZ_USAGE = f"usage: nox -s rustfuzz -- [{'|'.join(FUZZ_TARGETS)}] [--seconds N]"
MUTANTS_SCRIPT = "scripts/run_mutants.py"
NOTICES_SCRIPT = "scripts/gen_third_party_notices.py"
CARGO_TOOLS_MARK = "cargo_tools"
CARGO_TOOLS_TESTS = "tests/unit/tooling/test_third_party_notices.py"
# maturin passes these on macOS; a plain cargo link of a Python extension needs them there.
MACOS_EXTENSION_LINK_ARGS = ("-C", "link-arg=-undefined", "-C", "link-arg=dynamic_lookup")
# macOS keeps line tables in a dSYM only when packed; llvm-objdump reads it beside the library.
MACOS_PACKED_DEBUG_INFO = ("--config", f'profile.{FMA_CHECK_PROFILE}.split-debuginfo="packed"')
USAGE_BLOCK = re.compile(r"^## Usage\n.*?```python\n(.*?)```", re.DOTALL | re.MULTILINE)
LICENSE_EXPRESSION = " AND ".join(
    (
        "PolyForm-Noncommercial-1.0.0",
        "MIT",
        "OFL-1.1",
        "LicenseRef-Hershey-Fonts",
        "BSD-2-Clause",
    )
)
WHEEL_CHECK = f"""
import importlib.metadata as metadata
import importlib.resources as resources
import e8s
import e8s._core
expression = metadata.metadata("e8s")["License-Expression"]
assert expression == {LICENSE_EXPRESSION!r}, expression
assert resources.files("e8s").joinpath("py.typed").is_file(), "py.typed missing"
assert e8s.schema_path("showpy.show.v1.json").is_file(), "schema missing"
assert e8s.schema_path("LICENSE-MIT.txt").is_file(), "schema licence missing"
for font in ("fonts/relief-singleline.json", "fonts/licenses/OFL-1.1.txt"):
    assert resources.files("e8s").joinpath(font).is_file(), "fonts missing"
assert "site-packages" in e8s.__file__, e8s.__file__
assert "site-packages" in e8s._core.__file__, e8s._core.__file__
assert e8s._core.__version__ == e8s.__version__, e8s._core.__version__
print("Found e8s", e8s.__version__)
"""

PARITY_MODULE = "tests.parity.run"
PARITY_USAGE = "usage: nox -s parity -- <area>|all [--speed] [--digest PATH]"

BENCH_PATH = "tests/bench"
BENCH_BASELINE = "tests/bench/baseline-2026-11-13.json"
BENCH_STORAGE = "tests/bench/.results"
BENCH_REGRESSION = "min:20%"
GATED_TIER = "M"
# The baseline comes from another machine than most runs, so pytest-benchmark always warns.
BENCH_MACHINE_WARNING = "ignore::pytest_benchmark.logger.PytestBenchmarkWarning"
TIER_FLAG = "--tier"

nox.options.sessions = ["lint", "types", "tests", "hashseeds", "slow", "audit"]
nox.options.default_venv_backend = "none"


def _readme_usage_block(readme: Path) -> str:
    """Extracts the first python code block after the Usage heading of the README."""
    match = USAGE_BLOCK.search(readme.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit("E: README.md has no python block under '## Usage'")
    return match.group(1)


def _uv_run(session: nox.Session, *args: str, success_codes: list[int] | None = None) -> None:
    """Runs a command inside the uv-managed project environment.

    The Docker image sets NOX_UV_NO_SYNC so the installed wheel is tested, not a source tree.
    """
    sync_flag = "--no-sync" if os.environ.get(NO_SYNC_ENV) == "1" else "--locked"
    # Pin the nested run to nox's own interpreter: without it the nested uv re-resolved the
    # Python from .python-version and rebuilt .venv, so CI's older legs ran on 3.14.
    session.run(
        "uv",
        "run",
        sync_flag,
        "--python",
        sys.executable,
        *args,
        external=True,
        success_codes=success_codes,
    )


def _require_same_python(session: nox.Session) -> None:
    """Fails the session when the nested environment's Python differs from nox's own."""
    expected = f"{sys.version_info.major}.{sys.version_info.minor}"
    check = (
        "import sys; v = f'{sys.version_info.major}.{sys.version_info.minor}'; "
        f"assert v == {expected!r}, f'tests would run on Python {{v}}, not {expected}'"
    )
    _uv_run(session, "python", "-c", check)


@nox.session
def lint(session: nox.Session) -> None:
    """Checks ruff formatting, ruff lint rules, dependency declarations and Prettier formatting."""
    session.run("python", "scripts/check_tracked_paths.py", external=True)
    _uv_run(session, "ruff", "format", "--check", ".")
    _uv_run(session, "ruff", "check", ".")
    _uv_run(session, "deptry", ".")
    session.run("npx", "--no-install", "prettier", "--check", PRETTIER_GLOB, external=True)


@nox.session
def types(session: nox.Session) -> None:
    """Runs mypy --strict and pyright (basic), then stubtest over the compiled core's stubs.

    stubtest runs with no allowlist: every stub in e8s/_core/ must match the built extension.
    """
    _uv_run(session, "mypy", "e8s", "tests", "scripts")
    _uv_run(session, "pyright")
    _uv_run(session, "python", "-m", "mypy.stubtest", CORE_MODULE)


@nox.session
def tests(session: nox.Session) -> None:
    """Runs the full pytest suite under a fixed hash seed and enforces the branch-coverage floor."""
    session.env["PYTHONHASHSEED"] = DEFAULT_HASH_SEED
    _require_same_python(session)
    _uv_run(
        session,
        "pytest",
        "--cov=e8s",
        "--cov-branch",
        "--cov-report=term:skip-covered",
        *session.posargs,
        f"--cov-fail-under={COVERAGE_FLOOR}",
    )


@nox.session
def hashseeds(session: nox.Session) -> None:
    """Runs the full pytest suite under each seed in HASH_SEEDS."""
    _require_same_python(session)
    for seed in HASH_SEEDS:
        session.env["PYTHONHASHSEED"] = seed
        _uv_run(session, "pytest", *session.posargs)


@nox.session
def slow(session: nox.Session) -> None:
    """Runs the slow tests (generated-show properties, the large golden) under two seeds.

    They are left out of the per-leg `tests` gate; one extra seed still checks the large golden
    does not depend on hash order. pytest-xdist spreads them over SLOW_WORKERS processes, each
    module on one worker so its module-scoped fixtures are built once; pass
    `-- -n 0` to run them in one process. The timing tests always run in one process, after
    the others, and no posarg can give them workers.
    """
    _require_same_python(session)
    for seed in SLOW_HASH_SEEDS:
        session.env["PYTHONHASHSEED"] = seed
        _uv_run(
            session,
            "pytest",
            "-m",
            SLOW_PARALLEL_MARKS,
            "-n",
            SLOW_WORKERS,
            "--dist",
            SLOW_DIST,
            *session.posargs,
        )
        _uv_run(session, "pytest", "-m", SLOW_TIMING_MARKS, *session.posargs, "-n", NO_WORKERS)


@nox.session
def fuzz(session: nox.Session) -> None:
    """Runs the fuzz targets under the nightly Hypothesis profile unless one is already set.

    Not a default session: it is slow and runs on demand and in the nightly workflow. Fails when
    pytest collects no target, so broken marker plumbing cannot pass as green.
    """
    _require_same_python(session)
    session.env["HYPOTHESIS_PROFILE"] = os.environ.get("HYPOTHESIS_PROFILE", FUZZ_PROFILE)
    session.env["PYTHONHASHSEED"] = DEFAULT_HASH_SEED
    _uv_run(session, "pytest", "-m", "fuzz", *session.posargs)


@nox.session
def storagefuzz(session: nox.Session) -> None:
    """Fuzzes the storage property tests: `nox -s storagefuzz -- [--list | --target ID ...]`.

    With no argument it fuzzes every target in turn, each in its own process for `--seconds N`
    (300 by default), and fails after the last one when any failed. `--list` prints the target
    IDs as JSON; `--target ID [--seconds N]` fuzzes one target under Atheris and libFuzzer,
    `--target ID --replay FILE` runs one saved crash input. Atheris installs on Linux x86_64
    only. Not a default session: it runs on demand and in the nightly storage workflow.
    """
    session.env["HYPOTHESIS_PROFILE"] = FUZZ_PROFILE
    session.env["PYTHONHASHSEED"] = DEFAULT_HASH_SEED
    _uv_run(session, "python", STORAGE_FUZZ_SCRIPT, *session.posargs)


@nox.session
def audit(session: nox.Session) -> None:
    """Scans the locked dependencies with pip-audit."""
    requirements = f"{session.create_tmp()}/requirements.txt"
    session.run(
        "uv",
        "export",
        "--format",
        "requirements-txt",
        "--no-emit-project",
        "--quiet",
        "--output-file",
        requirements,
        external=True,
    )
    _uv_run(
        session, "pip-audit", "--strict", "--disable-pip", "--require-hashes", "-r", requirements
    )


def _only_file(session: nox.Session, folder: Path, pattern: str) -> Path:
    """Returns the one file of a folder that matches a glob pattern, failing on none or several."""
    found = sorted(folder.glob(pattern))
    if len(found) != 1:
        session.error(f"expected one {pattern} in {folder}, found {len(found)}")
    return found[0]


def _build_wheel(session: nox.Session, work: Path) -> Path:
    """Builds the one wheel of the project into a fresh folder under work.

    Args:
        session: The running nox session.
        work: The session's scratch folder.

    Returns:
        wheel: Path of the built wheel.
    """
    dist = work / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    session.run("uv", "build", "--wheel", "--out-dir", str(dist), external=True, env=LOCKED_BUILD)
    return _only_file(session, dist, "*.whl")


def _build_wheel_from_sdist(session: nox.Session, work: Path) -> Path:
    """Builds the sdist, then a wheel from that sdist with the dist profile, as releases are.

    Args:
        session: The running nox session.
        work: The session's scratch folder.

    Returns:
        wheel: Path of the wheel built from the sdist.
    """
    dist = work / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    session.run("uv", "build", "--sdist", "--out-dir", str(dist), external=True, env=LOCKED_BUILD)
    sdist = _only_file(session, dist, "*.tar.gz")
    session.run(
        "uv", "build", "--wheel", "--out-dir", str(dist), str(sdist), external=True, env=DIST_BUILD
    )
    return _only_file(session, dist, "*.whl")


def _fresh_venv(
    session: nox.Session, venv: Path, *, has_pip: bool, python: str = PYTHON_FOR_WHEEL
) -> Path:
    """Creates an empty venv on `python` (PYTHON_FOR_WHEEL by default); returns its interpreter."""
    shutil.rmtree(venv, ignore_errors=True)
    seed = ["--seed"] if has_pip else []
    session.run("uv", "venv", *seed, "--python", python, str(venv), external=True)
    return venv / "bin" / "python"


def _export_test_requirements(session: nox.Session, work: Path) -> Path:
    """Writes the locked, hashed requirements of the dev group, without the project, under work."""
    requirements = work / "test-requirements.txt"
    session.run(
        "uv",
        "export",
        "--format",
        "requirements-txt",
        "--only-group",
        "dev",
        "--no-emit-project",
        "--quiet",
        "--output-file",
        str(requirements),
        external=True,
    )
    return requirements


def _copy_test_suite(root: Path, suite: Path) -> Path:
    """Copies the tests and their pytest settings to a folder that holds no e8s source."""
    shutil.rmtree(suite, ignore_errors=True)
    shutil.copytree(
        root / SUITE_FOLDER, suite / SUITE_FOLDER, ignore=shutil.ignore_patterns(*SUITE_SKIP)
    )
    for name in SUITE_FILES:
        shutil.copy2(root / name, suite / name)
    return suite


def _write_readme_example(root: Path, work: Path) -> Path:
    """Writes the README usage example to a script under work and returns its path."""
    example = work / "readme_usage.py"
    example.write_text(_readme_usage_block(root / "README.md"), encoding="utf-8")
    return example


@nox.session
def wheel(session: nox.Session) -> None:
    """Builds the wheel, installs it with every extra in a clean venv and runs the README example.

    First refuses an rpath or a linked libpython in the wheel's extension. Not a default
    session: it builds and creates a venv, so it runs on demand and in CI.
    """
    root = Path.cwd()
    work = Path(session.create_tmp()).resolve()
    built = _build_wheel(session, work)
    _uv_run(session, "python", LINK_CHECK_SCRIPT, str(built))
    python = _fresh_venv(session, work / "venv", has_pip=True)
    session.run(
        str(python), "-m", "pip", "install", "--quiet", f"{built}{ALL_EXTRAS}", external=True
    )
    example = _write_readme_example(root, work)
    session.chdir(work)
    session.run(str(python), "-I", "-c", WHEEL_CHECK, external=True)
    session.run(str(python), "-I", str(example), external=True)


@nox.session
def core(session: nox.Session) -> None:
    """Installs the wheel without extras and checks the core install fails closed.

    Runs the README usage example, imports every module with no optional dependency loaded,
    and expects MissingExtraError naming the extra from every extra-gated call. Not a default
    session: it builds and creates a venv, so it runs on demand and in CI.
    """
    root = Path.cwd()
    work = Path(session.create_tmp()).resolve()
    built = _build_wheel(session, work)
    python = _fresh_venv(session, work / "venv", has_pip=False)
    session.run(
        "uv", "pip", "install", "--quiet", "--python", str(python), str(built), external=True
    )
    example = _write_readme_example(root, work)
    check = root / CORE_CHECK_SCRIPT
    session.chdir(work)
    session.run(str(python), "-I", str(example), external=True)
    session.run(str(python), "-I", str(check), external=True)


@nox.session
def wheels(session: nox.Session) -> None:
    """Tests the release wheel: built from the sdist with the dist profile, on each Python.

    Refuses an rpath or a linked libpython in the wheel's extension first. Then installs the
    locked dev group and the wheel into a fresh venv per WHEEL_TEST_PYTHONS entry,
    then runs PORTED_TESTS from a copy of the suite, so the installed wheel, not the source tree,
    is imported. Not a default session: it builds and creates venvs, so it runs on demand and in
    CI.
    """
    root = Path.cwd()
    work = Path(session.create_tmp()).resolve()
    built = _build_wheel_from_sdist(session, work)
    _uv_run(session, "python", LINK_CHECK_SCRIPT, str(built))
    requirements = _export_test_requirements(session, work)
    suite = _copy_test_suite(root, work / "suite")
    session.env["PYTHONHASHSEED"] = DEFAULT_HASH_SEED
    for version in WHEEL_TEST_PYTHONS:
        python = _fresh_venv(session, work / f"venv-{version}", has_pip=False, python=version)
        install = ("uv", "pip", "install", "--quiet", "--python", str(python))
        session.run(*install, "--require-hashes", "-r", str(requirements), external=True)
        session.run(*install, "--no-deps", str(built), external=True)
        session.chdir(suite)
        session.run(str(python), "-P", "-c", WHEEL_TEST_CHECK, external=True)
        session.run(
            str(python), "-P", "-m", "pytest", *PORTED_TESTS, *session.posargs, external=True
        )


def _fma_check_library() -> Path:
    """Returns the path of the extension that the fma-check profile builds on this host."""
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    return Path("target") / FMA_CHECK_PROFILE / f"lib_core{suffix}"


def _check_no_fma(session: nox.Session) -> None:
    """Builds the extension unstripped with line tables and refuses any FMA outside libm's fma.

    Runs on arm64 hosts only: aarch64 has scalar FMA instructions in its base set, so a fused
    multiply-add shows there first.
    """
    machine = platform.machine()
    if machine not in ARM64_MACHINES:
        session.log(f"Skipping FMA check: host is {machine}")
        return
    darwin = sys.platform == "darwin"
    config = MACOS_PACKED_DEBUG_INFO if darwin else ()
    link_args = ("--", *MACOS_EXTENSION_LINK_ARGS) if darwin else ()
    session.run(
        "cargo",
        "rustc",
        "-p",
        "e8s-py",
        "--profile",
        FMA_CHECK_PROFILE,
        "--locked",
        *config,
        *link_args,
        external=True,
    )
    _uv_run(session, "python", FMA_CHECK_SCRIPT, str(_fma_check_library()))


def _cargo_tool(name: str) -> str:
    """Returns the absolute path of one cargo tool installed under CARGO_TOOLS_ROOT."""
    suffix = ".exe" if sys.platform == "win32" else ""
    return str(Path(CARGO_TOOLS_ROOT).resolve() / "bin" / f"{name}{suffix}")


def _install_cargo_tool(
    session: nox.Session,
    name: str,
    version: str,
    *features: str,
    version_args: tuple[str, ...] = ("--version",),
) -> None:
    """Installs one cargo tool, locked, into CARGO_TOOLS_ROOT unless that version is there.

    Args:
        session: The running nox session.
        name: The crate that ships the tool, which is also the executable's name.
        version: The exact version to install.
        features: Extra `cargo install` arguments that select the crate's features.
        version_args: The arguments that make the installed executable print its version.
    """
    tool = _cargo_tool(name)
    if Path(tool).is_file():
        installed = subprocess.run([tool, *version_args], capture_output=True, encoding="utf-8")
        if installed.stdout.strip() == f"{name} {version}":
            session.log(f"Found {name} {version}")
            return
    session.run(
        "cargo",
        "install",
        "--locked",
        "--root",
        CARGO_TOOLS_ROOT,
        name,
        "--version",
        version,
        *features,
        external=True,
    )


@nox.session
def rust(session: nox.Session) -> None:
    """Checks Rust formatting, lints and tests, builds the wasm crate and runs the FMA check.

    Then checks every crate's licence, bans, advisories and source with cargo deny, refuses a
    stale fuzz/Cargo.lock, checks the fuzz crate's advisories, bans and sources, and checks that
    the Rust notice file the wheel ships is current.

    Every cargo command builds pyo3 against nox's own interpreter and environment, the project
    environment where e8s is installed: the crate tests embed that interpreter and import e8s.

    Not a default session: it compiles every crate, so it runs on demand and in CI.
    """
    session.env[PYO3_PYTHON_ENV] = sys.executable
    session.env[VIRTUAL_ENV_VAR] = sys.prefix
    session.run("cargo", "fmt", "--all", "--check", external=True)
    session.run(
        "cargo",
        "clippy",
        "--workspace",
        "--all-targets",
        "--locked",
        "--",
        "-D",
        "warnings",
        external=True,
    )
    session.run("cargo", "test", "--workspace", "--locked", external=True)
    session.run(
        "cargo",
        "build",
        "-p",
        "e8s-wasm",
        "--target",
        WASM_TARGET,
        "--profile",
        "dist",
        "--locked",
        external=True,
    )
    _check_no_fma(session)
    _install_cargo_tool(session, CARGO_DENY, CARGO_DENY_VERSION)
    _install_cargo_tool(session, CARGO_ABOUT, CARGO_ABOUT_VERSION, *CARGO_ABOUT_FEATURES)
    session.run(_cargo_tool(CARGO_DENY), "--locked", "check", external=True)
    # The fuzz crate keeps its own lock, which a core version or dependency change leaves stale;
    # only the nightly builds it, so the stable gate refuses that lock here. It never ships, so
    # cargo deny skips its licences (libfuzzer-sys carries NCSA) but checks what CI executes.
    session.run("cargo", "fetch", "--locked", "--manifest-path", FUZZ_MANIFEST, external=True)
    session.run(
        _cargo_tool(CARGO_DENY),
        "--manifest-path",
        FUZZ_MANIFEST,
        "--locked",
        "check",
        "advisories",
        "bans",
        "sources",
        external=True,
    )
    _uv_run(session, "python", NOTICES_SCRIPT, "--check")
    _uv_run(session, "pytest", "-m", CARGO_TOOLS_MARK, CARGO_TOOLS_TESTS)


def _fuzz_plan(posargs: list[str]) -> tuple[tuple[str, ...], str]:
    """Reads `[target] [--seconds N]` into the fuzz targets to run and the seconds each one runs.

    Args:
        posargs: The arguments given after `--`.

    Returns:
        plan: The named target, or every one of FUZZ_TARGETS, and the seconds per target,
            FUZZ_SECONDS unless `--seconds` gives a positive whole number.

    Raises:
        ValueError: On an unknown target, more than one target, or a bad `--seconds` value.
    """
    rest = list(posargs)
    seconds = FUZZ_SECONDS
    if SECONDS_FLAG in rest:
        at = rest.index(SECONDS_FLAG)
        value = rest[at + 1] if at + 1 < len(rest) else ""
        if not value.isdecimal() or int(value) == 0:
            raise ValueError(f"{SECONDS_FLAG} needs a positive whole number; {RUSTFUZZ_USAGE}")
        seconds = value
        del rest[at : at + 2]
    if len(rest) > 1 or (rest and rest[0] not in FUZZ_TARGETS):
        raise ValueError(f"unknown arguments {rest}; {RUSTFUZZ_USAGE}")
    return (tuple(rest) or FUZZ_TARGETS), seconds


def _fuzz_targets(run: Callable[..., object], targets: Sequence[str], seconds: str) -> list[str]:
    """Runs each fuzz target in turn, going on to the next after one fails.

    Args:
        run: The session's run, which raises CommandFailed when a command fails.
        targets: The fuzz targets to run.
        seconds: How long libFuzzer runs each target.

    Returns:
        failed: The targets whose run failed, such as on a crash, in run order.
    """
    failed: list[str] = []
    for target in targets:
        try:
            run(
                _cargo_tool(CARGO_FUZZ),
                "run",
                target,
                "--fuzz-dir",
                FUZZ_DIR,
                "--",
                f"-max_total_time={seconds}",
                external=True,
                env=FUZZ_ENV,
            )
        except CommandFailed:
            failed.append(target)
    return failed


@nox.session
def rustfuzz(session: nox.Session) -> None:
    """Fuzzes the core's parsers: `nox -s rustfuzz -- [target] [--seconds N]`.

    Installs FUZZ_TOOLCHAIN and cargo-fuzz, refuses a stale fuzz/Cargo.lock, then runs each
    target, or the one named, for N seconds (FUZZ_SECONDS by default). A crash leaves its input
    under fuzz/artifacts/ and the next target still runs; the session then fails, naming every
    failed target. Not a default session: it runs on demand and in the nightly core workflow.
    """
    try:
        targets, seconds = _fuzz_plan(session.posargs)
    except ValueError as error:
        session.error(str(error))
    session.run(
        "rustup", "toolchain", "install", FUZZ_TOOLCHAIN, "--profile", "minimal", external=True
    )
    _install_cargo_tool(session, CARGO_FUZZ, CARGO_FUZZ_VERSION)
    session.run(
        "cargo", "fetch", "--locked", "--manifest-path", FUZZ_MANIFEST, external=True, env=FUZZ_ENV
    )
    failed = _fuzz_targets(session.run, targets, seconds)
    if failed:
        session.error(
            f"fuzz targets failed: {', '.join(failed)}; inputs under {FUZZ_DIR}/artifacts/"
        )


@nox.session
def mutants(session: nox.Session) -> None:
    """Records mutation scores for e8s-core and the kept Python modules, never gating on them.

    Installs cargo-mutants, then scripts/run_mutants.py runs it on e8s-core and mutmut over the
    modules listed under `stays` in tests/unit/rules/ported/*.toml, and writes both scores to
    mutants-summary.json. Fails only when a tool cannot run. Not a default session: it runs on
    demand and in the nightly core workflow.
    """
    _install_cargo_tool(
        session, CARGO_MUTANTS, CARGO_MUTANTS_VERSION, version_args=CARGO_MUTANTS_VERSION_ARGS
    )
    _uv_run(session, "python", MUTANTS_SCRIPT, "--cargo-mutants", _cargo_tool(CARGO_MUTANTS))


@nox.session
def parity(session: nox.Session) -> None:
    """Compares the working tree with the v0.4.0 oracle: `nox -s parity -- <area> [--speed]`.

    Builds the oracle's venv into .parity/ once (reused while its READY stamp matches), runs the
    area's cases under both interpreters, prints the table and fails on any gate. `--digest
    PATH` writes the SHA-256 of every core output. Runs under PYTHONHASHSEED 0 unless one is
    set. Not a default session: the first run builds the oracle.
    """
    if not session.posargs:
        session.error(PARITY_USAGE)
    _require_same_python(session)
    if "PYTHONHASHSEED" not in os.environ:
        session.env["PYTHONHASHSEED"] = DEFAULT_HASH_SEED
    _uv_run(session, "python", "-m", PARITY_MODULE, *session.posargs)


def _tier(posargs: list[str]) -> str:
    """Returns the --tier value passed after `--`, or the gated tier when none is given."""
    for flag, value in itertools.pairwise(posargs):
        if flag == TIER_FLAG:
            return value
    for arg in posargs:
        if arg.startswith(f"{TIER_FLAG}="):
            return arg.split("=", 1)[1]
    return GATED_TIER


@nox.session
def bench(session: nox.Session) -> None:
    """Runs the budget benchmarks; at tier M fails on a >20% min-time regression.

    Pass a tier with `nox -s bench -- --tier L`. Only tier M compares against the baseline:
    larger tiers on shared runners are too noisy to block on, so they only report.
    The baseline is machine-specific: re-record it on the runner class before any hosted gate.
    """
    args = ["pytest", "-m", "bench", BENCH_PATH, f"--benchmark-storage={BENCH_STORAGE}"]
    if _tier(session.posargs) == GATED_TIER:
        args += [
            f"--benchmark-compare={BENCH_BASELINE}",
            f"--benchmark-compare-fail={BENCH_REGRESSION}",
            "-W",
            BENCH_MACHINE_WARNING,
        ]
    _uv_run(session, *args, *session.posargs)


@nox.session(name="api-changes")
def api_changes(session: nox.Session) -> None:
    """Checks that each commit since the last release tag names the public names it changes.

    A commit that changes the API snapshot must name every added, removed or changed public
    name in backticks in its subject or a `BREAKING CHANGE:` footer.
    """
    _uv_run(session, "python", API_CHANGES_SCRIPT)


@nox.session
def changelog(session: nox.Session) -> None:
    """Regenerates CHANGELOG.md from Conventional Commits at release time, then runs Prettier."""
    _uv_run(session, "git-cliff", "--config", "cliff.toml", "--output", "CHANGELOG.md")
    session.run("npx", "--no-install", "prettier", "--write", "CHANGELOG.md", external=True)


@nox.session
def docs(session: nox.Session) -> None:
    """Reports that the docs build is not built yet."""
    session.log("W: docs build not implemented")


if __name__ == "__main__":
    nox.main()
