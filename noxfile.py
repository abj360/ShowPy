#!/usr/bin/env python3

"""
noxfile.py --- single task runner for every local, hook and CI gate

Contains:
    lint: checks formatting, lint rules and Prettier formatting
    types: runs mypy --strict over the library and tests
    pyright: runs pyright over the library (not a default session yet)
    tests: runs the full pytest suite under a fixed hash seed
    hashseeds: runs the full pytest suite under each seed in HASH_SEEDS
    audit: scans the locked dependencies for known vulnerabilities
    wheel: placeholder until the wheel gate lands
    bench: placeholder until the benchmark gate lands
    docs: placeholder until the docs build lands
"""

import nox

PRETTIER_GLOB = "**/*.{md,yml,yaml,json}"
HASH_SEEDS = ("0", "1", "2")
DEFAULT_HASH_SEED = "0"

nox.options.sessions = ["lint", "types", "tests", "hashseeds", "audit"]
nox.options.default_venv_backend = "none"


def _uv_run(session: nox.Session, *args: str) -> None:
    """Runs a command inside the uv-managed project environment."""
    session.run("uv", "run", "--locked", *args, external=True)


@nox.session(venv_backend="none")
def lint(session: nox.Session) -> None:
    """Checks ruff formatting, ruff lint rules and Prettier formatting."""
    _uv_run(session, "ruff", "format", "--check", ".")
    _uv_run(session, "ruff", "check", ".")
    session.run("npx", "--no-install", "prettier", "--check", PRETTIER_GLOB, external=True)


@nox.session(venv_backend="none")
def types(session: nox.Session) -> None:
    """Runs mypy --strict over the library and tests."""
    _uv_run(session, "mypy", "stage", "tests")


@nox.session(venv_backend="none")
def pyright(session: nox.Session) -> None:
    """Runs pyright over the library."""
    _uv_run(session, "pyright")


@nox.session(venv_backend="none")
def tests(session: nox.Session) -> None:
    """Runs the full pytest suite under a fixed hash seed."""
    session.env["PYTHONHASHSEED"] = DEFAULT_HASH_SEED
    _uv_run(session, "pytest", *session.posargs)


@nox.session(venv_backend="none")
def hashseeds(session: nox.Session) -> None:
    """Runs the full pytest suite once per seed in HASH_SEEDS."""
    for seed in HASH_SEEDS:
        session.env["PYTHONHASHSEED"] = seed
        _uv_run(session, "pytest", *session.posargs)


@nox.session(venv_backend="none")
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


@nox.session(venv_backend="none")
def wheel(session: nox.Session) -> None:
    """Reports that the wheel gate is not built yet."""
    session.log("W: wheel gate not implemented")


@nox.session(venv_backend="none")
def bench(session: nox.Session) -> None:
    """Reports that the benchmark gate is not built yet."""
    session.log("W: bench gate not implemented")


@nox.session(venv_backend="none")
def docs(session: nox.Session) -> None:
    """Reports that the docs build is not built yet."""
    session.log("W: docs build not implemented")


if __name__ == "__main__":
    nox.main()
