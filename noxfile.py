#!/usr/bin/env python3

"""
noxfile.py --- single task runner for every local, hook and CI gate

Contains:
    lint: checks formatting, lint rules and Prettier formatting
    types: runs mypy --strict and pyright (basic) over the library, tests and scripts
    tests: runs the full pytest suite under a fixed hash seed
    hashseeds: runs the full pytest suite under each non-default seed in HASH_SEEDS
    slow: runs the slow tests under each seed in SLOW_HASH_SEEDS
    audit: scans the locked dependencies for known vulnerabilities
    wheel: builds the wheel, installs it in a clean venv and runs the README usage example
    bench: runs the budget benchmarks; at tier M fails on a >20% min-time regression
    changelog: regenerates CHANGELOG.md with git-cliff and formats it with Prettier
    docs: placeholder until the docs build lands
"""

import itertools
import os
import re
import shutil
from pathlib import Path

import nox

PRETTIER_GLOB = "**/*.{md,yml,yaml,json}"
HASH_SEEDS = ("1", "2")
SLOW_HASH_SEEDS = ("0", "1")
DEFAULT_HASH_SEED = "0"
NO_SYNC_ENV = "NOX_UV_NO_SYNC"

PYTHON_FOR_WHEEL = "3.14"
USAGE_BLOCK = re.compile(r"^## Usage\n.*?```python\n(.*?)```", re.DOTALL | re.MULTILINE)
WHEEL_CHECK = """
import importlib.resources as resources
import showpy
assert resources.files("showpy").joinpath("py.typed").is_file(), "py.typed missing"
assert showpy.schema_path("showpy.show.v1.json").is_file(), "schema missing"
assert "site-packages" in showpy.__file__, showpy.__file__
print("Found showpy", showpy.__version__)
"""

BENCH_PATH = "tests/bench"
BENCH_BASELINE = "benchmarks/baseline-2026-11-13.json"
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


def _uv_run(session: nox.Session, *args: str) -> None:
    """Runs a command inside the uv-managed project environment.

    The Docker image sets NOX_UV_NO_SYNC so the installed wheel is tested, not a source tree.
    """
    sync_flag = "--no-sync" if os.environ.get(NO_SYNC_ENV) == "1" else "--locked"
    session.run("uv", "run", sync_flag, *args, external=True)


@nox.session
def lint(session: nox.Session) -> None:
    """Checks ruff formatting, ruff lint rules and Prettier formatting."""
    _uv_run(session, "ruff", "format", "--check", ".")
    _uv_run(session, "ruff", "check", ".")
    session.run("npx", "--no-install", "prettier", "--check", PRETTIER_GLOB, external=True)


@nox.session
def types(session: nox.Session) -> None:
    """Runs mypy --strict and pyright (basic) over the library, tests and scripts."""
    _uv_run(session, "mypy", "showpy", "tests", "scripts")
    _uv_run(session, "pyright")


@nox.session
def tests(session: nox.Session) -> None:
    """Runs the full pytest suite under a fixed hash seed."""
    session.env["PYTHONHASHSEED"] = DEFAULT_HASH_SEED
    _uv_run(session, "pytest", *session.posargs)


@nox.session
def hashseeds(session: nox.Session) -> None:
    """Runs the full pytest suite under each seed in HASH_SEEDS."""
    for seed in HASH_SEEDS:
        session.env["PYTHONHASHSEED"] = seed
        _uv_run(session, "pytest", *session.posargs)


@nox.session
def slow(session: nox.Session) -> None:
    """Runs the slow tests (generated-show properties, the large golden) under two seeds.

    They are left out of the per-leg `tests` gate; one extra seed still checks the large golden
    does not depend on hash order.
    """
    for seed in SLOW_HASH_SEEDS:
        session.env["PYTHONHASHSEED"] = seed
        _uv_run(session, "pytest", "-m", "slow", *session.posargs)


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


@nox.session
def wheel(session: nox.Session) -> None:
    """Builds the wheel, installs it in a clean venv and runs the README usage example.

    Not a default session: it builds and creates a venv, so it runs on demand and in CI.
    """
    root = Path.cwd()
    work = Path(session.create_tmp()).resolve()
    dist, venv = work / "dist", work / "venv"
    shutil.rmtree(dist, ignore_errors=True)
    shutil.rmtree(venv, ignore_errors=True)
    session.run("uv", "build", "--wheel", "--out-dir", str(dist), external=True)
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        session.error(f"expected one wheel in {dist}, found {len(wheels)}")
    python = venv / "bin" / "python"
    session.run("uv", "venv", "--seed", "--python", PYTHON_FOR_WHEEL, str(venv), external=True)
    session.run(str(python), "-m", "pip", "install", "--quiet", str(wheels[0]), external=True)
    example = work / "readme_usage.py"
    example.write_text(_readme_usage_block(root / "README.md"))
    session.chdir(work)
    session.run(str(python), "-I", "-c", WHEEL_CHECK, external=True)
    session.run(str(python), "-I", str(example), external=True)


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
    args = ["pytest", "-m", "bench", BENCH_PATH]
    if _tier(session.posargs) == GATED_TIER:
        args += [
            f"--benchmark-compare={BENCH_BASELINE}",
            f"--benchmark-compare-fail={BENCH_REGRESSION}",
            "-W",
            BENCH_MACHINE_WARNING,
        ]
    _uv_run(session, *args, *session.posargs)


@nox.session
def changelog(session: nox.Session) -> None:
    """Regenerates CHANGELOG.md from Conventional Commits and formats it with Prettier."""
    _uv_run(session, "git-cliff", "--config", "cliff.toml", "--output", "CHANGELOG.md")
    session.run("npx", "--no-install", "prettier", "--write", "CHANGELOG.md", external=True)


@nox.session
def docs(session: nox.Session) -> None:
    """Reports that the docs build is not built yet."""
    session.log("W: docs build not implemented")


if __name__ == "__main__":
    nox.main()
