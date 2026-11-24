#!/usr/bin/env python3

"""
test_check_release_version.py --- tests that a release tag must name the Cargo.toml version

Contains:
    write_manifest(): writes a Cargo.toml holding one workspace version
    test_refuses_a_tag_that_differs_from_cargo: exits 1 with the exact `E:` line
    test_accepts_the_matching_tag: passes for v plus the workspace version
    test_regression_refuses_a_tag_without_the_v_prefix: 0.6.0 is not the tag v0.6.0
    test_regression_refuses_a_manifest_without_a_workspace_version: no version exits 1
    test_main_checks_the_repository_manifest: main reads the repository's Cargo.toml
    test_main_refuses_anything_but_one_tag: no tag or two tags exit 2
    test_script_exits_one_on_a_mismatched_tag: the command line fails the job
"""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts.check_release_version import CARGO_TOML, check_release_version, main

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check_release_version.py"
VERSION = "0.6.0"


def write_manifest(folder: Path, package: str) -> Path:
    """Writes a Cargo.toml whose `[workspace.package]` table holds the given lines."""
    manifest = folder / "Cargo.toml"
    manifest.write_text(f'[workspace]\nmembers = ["crates/*"]\n\n[workspace.package]\n{package}')
    return manifest


def test_refuses_a_tag_that_differs_from_cargo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = write_manifest(tmp_path, f'version = "{VERSION}"\n')
    with pytest.raises(SystemExit) as stop:
        check_release_version("v0.5.0", manifest)
    assert stop.value.code == 1
    assert capsys.readouterr().err == "E: tag v0.5.0 does not match Cargo.toml version 0.6.0\n"


def test_accepts_the_matching_tag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = write_manifest(tmp_path, f'version = "{VERSION}"\n')
    check_release_version(f"v{VERSION}", manifest)
    assert capsys.readouterr().out == "Tag v0.6.0 matches Cargo.toml version 0.6.0\n"


def test_regression_refuses_a_tag_without_the_v_prefix(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path, f'version = "{VERSION}"\n')
    with pytest.raises(SystemExit) as stop:
        check_release_version(VERSION, manifest)
    assert stop.value.code == 1


def test_regression_refuses_a_manifest_without_a_workspace_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = write_manifest(tmp_path, 'edition = "2024"\n')
    with pytest.raises(SystemExit) as stop:
        check_release_version(f"v{VERSION}", manifest)
    assert stop.value.code == 1
    assert capsys.readouterr().err == "E: Cargo.toml has no [workspace.package] version\n"


def test_main_checks_the_repository_manifest() -> None:
    version = tomllib.loads(CARGO_TOML.read_text())["workspace"]["package"]["version"]
    assert CARGO_TOML == ROOT / "Cargo.toml"
    main([f"v{version}"])
    with pytest.raises(SystemExit) as stop:
        main([f"v{version}.1"])
    assert stop.value.code == 1


@pytest.mark.parametrize("argv", [[], ["v0.6.0", "v0.6.1"]])
def test_main_refuses_anything_but_one_tag(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        main(argv)
    assert stop.value.code == 2


def test_script_exits_one_on_a_mismatched_tag() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "v0.0.0-not-a-release"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stderr.startswith("E: tag v0.0.0-not-a-release does not match Cargo.toml")
