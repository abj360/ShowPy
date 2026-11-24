#!/usr/bin/env python3

"""
check_release_version.py --- refuses a release tag that names another version than Cargo.toml

`[workspace.package] version` in Cargo.toml is the one source of the version maturin writes into
every wheel, and the release commit bumps it, so a tag vX.Y.Z must name that version exactly.
Runs on the runner's system Python, so it uses the standard library only.

Contains:
    CARGO_TOML: the repository's Cargo.toml, which main checks a tag against
    error_exit(): prints an `E:` line and returns the SystemExit that ends the check
    workspace_version(): reads `[workspace.package] version` from a Cargo.toml
    check_release_version(): exits 1 when a tag differs from the Cargo.toml version
    main(): checks the one tag given on the command line against CARGO_TOML
"""

import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

CARGO_TOML = Path(__file__).resolve().parents[1] / "Cargo.toml"
TAG_PREFIX = "v"
FAILED = 1
USAGE_ERROR = 2


def error_exit(message: str, code: int = FAILED) -> SystemExit:
    """Prints an `E:` line to stderr and returns the SystemExit that ends the check."""
    print(f"E: {message}", file=sys.stderr)
    return SystemExit(code)


def workspace_version(cargo_toml: Path) -> str:
    """Reads `[workspace.package] version` from a Cargo.toml.

    Args:
        cargo_toml: The workspace manifest.

    Returns:
        version: The workspace version, as Cargo.toml spells it.
    """
    manifest = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
    version = manifest.get("workspace", {}).get("package", {}).get("version")
    if not isinstance(version, str) or not version:
        raise error_exit(f"{cargo_toml.name} has no [workspace.package] version")
    return version


def check_release_version(tag: str, cargo_toml: Path) -> None:
    """Exits 1 unless the tag is `v` followed by the Cargo.toml workspace version.

    Args:
        tag: The release tag, such as v0.6.0.
        cargo_toml: The workspace manifest that holds the version.
    """
    version = workspace_version(cargo_toml)
    if tag != f"{TAG_PREFIX}{version}":
        raise error_exit(f"tag {tag} does not match {cargo_toml.name} version {version}")
    print(f"Tag {tag} matches {cargo_toml.name} version {version}")


def main(argv: Sequence[str] | None = None) -> None:
    """Checks the one tag given on the command line against CARGO_TOML.

    Args:
        argv: The command-line arguments after the script name; sys.argv[1:] when None.
    """
    arguments = sys.argv[1:] if argv is None else list(argv)
    if len(arguments) != 1:
        raise error_exit("usage: check_release_version.py vX.Y.Z", USAGE_ERROR)
    check_release_version(arguments[0], CARGO_TOML)


if __name__ == "__main__":
    main()
