#!/usr/bin/env python3

"""
noxfile.py --- single task runner for every local, hook and CI gate

Contains:
    lint: checks formatting, lint rules, dependency declarations and Prettier formatting
    types: runs mypy --strict and pyright (basic) over the library, tests and scripts
    tests: runs the full pytest suite under a fixed hash seed with a branch-coverage floor
    hashseeds: runs the full pytest suite under each non-default seed in HASH_SEEDS
    slow: runs the slow tests under each seed in SLOW_HASH_SEEDS, on SLOW_WORKERS pytest-xdist
        workers that each take whole modules, except the timing tests, which run in one
        process
    fuzz: runs the fuzz targets under the nightly Hypothesis profile
    audit: scans the locked dependencies for known vulnerabilities
    wheel: builds the wheel, installs it with every extra in a clean venv and runs the README
        usage example
    core: installs the wheel without extras and checks imports and extra-gated calls fail closed
    bench: runs the budget benchmarks; at tier M fails on a >20% min-time regression
    api_changes: checks that each commit since the last release names the API names it changes
    changelog: regenerates CHANGELOG.md with git-cliff and formats it with Prettier at release time
    docs: placeholder until the docs build lands
"""

import itertools
import os
import re
import shutil
import sys
from pathlib import Path

import nox

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
COVERAGE_FLOOR = "96"
NO_SYNC_ENV = "NOX_UV_NO_SYNC"

PYTHON_FOR_WHEEL = "3.14"
ALL_EXTRAS = "[all]"
CORE_CHECK_SCRIPT = "scripts/check_core_install.py"
API_CHANGES_SCRIPT = "scripts/check_api_changes.py"
USAGE_BLOCK = re.compile(r"^## Usage\n.*?```python\n(.*?)```", re.DOTALL | re.MULTILINE)
LICENSE_EXPRESSION = " AND ".join(
    ("PolyForm-Noncommercial-1.0.0", "MIT", "OFL-1.1", "LicenseRef-Hershey-Fonts")
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
    match = USAGE_BLOCK.search(readme.read_text())
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
    """Runs mypy --strict and pyright (basic) over the library, tests and scripts."""
    _uv_run(session, "mypy", "e8s", "tests", "scripts")
    _uv_run(session, "pyright")


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
    session.run("uv", "build", "--wheel", "--out-dir", str(dist), external=True)
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        session.error(f"expected one wheel in {dist}, found {len(wheels)}")
    return wheels[0]


def _fresh_venv(session: nox.Session, venv: Path, *, has_pip: bool) -> Path:
    """Creates an empty venv on PYTHON_FOR_WHEEL and returns its interpreter."""
    shutil.rmtree(venv, ignore_errors=True)
    seed = ["--seed"] if has_pip else []
    session.run("uv", "venv", *seed, "--python", PYTHON_FOR_WHEEL, str(venv), external=True)
    return venv / "bin" / "python"


def _write_readme_example(root: Path, work: Path) -> Path:
    """Writes the README usage example to a script under work and returns its path."""
    example = work / "readme_usage.py"
    example.write_text(_readme_usage_block(root / "README.md"))
    return example


@nox.session
def wheel(session: nox.Session) -> None:
    """Builds the wheel, installs it with every extra in a clean venv and runs the README example.

    Not a default session: it builds and creates a venv, so it runs on demand and in CI.
    """
    root = Path.cwd()
    work = Path(session.create_tmp()).resolve()
    built = _build_wheel(session, work)
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
