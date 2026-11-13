#!/usr/bin/env python3

"""
noxfile.py --- single task runner for every local, hook and CI gate

Contains:
    lint: checks formatting, lint rules and Prettier formatting
    types: runs mypy --strict over the library and tests
    pyright: runs pyright over the library (not a default session yet)
    tests: runs the full pytest suite under a fixed hash seed
    hashseeds: runs the full pytest suite under each non-default seed in HASH_SEEDS
    audit: scans the locked dependencies for known vulnerabilities
    wheel: builds the wheel, installs it in a clean venv and runs the README usage example
    bench: placeholder until the benchmark gate lands
    docs: placeholder until the docs build lands
"""

import os
import re
import shutil
from pathlib import Path

import nox

PRETTIER_GLOB = "**/*.{md,yml,yaml,json}"
HASH_SEEDS = ("1", "2")
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

nox.options.sessions = ["lint", "types", "tests", "hashseeds", "audit"]
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
    """Runs mypy --strict over the library and tests."""
    _uv_run(session, "mypy", "showpy", "tests")


@nox.session
def pyright(session: nox.Session) -> None:
    """Runs pyright over the library."""
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


@nox.session
def bench(session: nox.Session) -> None:
    """Reports that the benchmark gate is not built yet."""
    session.log("W: bench gate not implemented")


@nox.session
def docs(session: nox.Session) -> None:
    """Reports that the docs build is not built yet."""
    session.log("W: docs build not implemented")


if __name__ == "__main__":
    nox.main()
