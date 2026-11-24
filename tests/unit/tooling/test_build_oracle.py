#!/usr/bin/env python3

"""
test_build_oracle.py --- checks that the parity oracle is reused when ready and fails closed

Each test that needs git builds its own repository under tmp_path and points GIT_DIR at it.

Contains:
    test_interpreter_follows_the_platform: bin/python on POSIX, Scripts/python.exe on Windows
    test_ready_oracle_is_reused_without_any_work: no git call when READY matches the wheel
    test_is_ready_refuses_a_changed_wheel_or_another_python: READY must match both
    test_regression_missing_ref_fails_closed: an absent tag exits with an E: line
    test_regression_failed_build_removes_the_worktree: no worktree is left on a build failure
    test_refuses_a_symlinked_parity_folder: the oracle is never built through a symlink
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_oracle import (
    READY_FILE,
    VENV_FOLDER,
    WHEEL_FOLDER,
    build_oracle,
    interpreter,
    is_ready,
)

GIT_IDENTITY = (
    "-c",
    "user.name=Parity Test",
    "-c",
    "user.email=parity@example.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
)
BROKEN_PROJECT = """[project]
name = "broken"
version = "0.0.1"

[build-system]
requires = []
build-backend = "no_such_backend"
"""
TAG = "v0.0.1"
RUNNING = f"{sys.version_info.major}.{sys.version_info.minor}"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *GIT_IDENTITY, *args],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_DIR", str(root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root))
    _git(root, "init", "--quiet")
    (root / "pyproject.toml").write_text(BROKEN_PROJECT, encoding="utf-8")
    _git(root, "add", "pyproject.toml")
    _git(root, "commit", "--quiet", "-m", "broken build")
    _git(root, "tag", TAG)
    monkeypatch.chdir(root)
    return root


def _ready_oracle(out: Path, *, version: str = RUNNING) -> Path:
    wheel = out / WHEEL_FOLDER / "e8s-0.0.1-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"wheel")
    python = interpreter(out / VENV_FOLDER)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (out / VENV_FOLDER / "pyvenv.cfg").write_text(f"version_info = {version}\n", encoding="utf-8")
    (out / READY_FILE).write_text(hashlib.sha256(b"wheel").hexdigest() + "\n", encoding="utf-8")
    return python


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("linux", ("bin", "python")), ("win32", ("Scripts", "python.exe"))],
)
def test_interpreter_follows_the_platform(
    platform: str, expected: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    assert interpreter(Path("venv")) == Path("venv", *expected)


def test_ready_oracle_is_reused_without_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "no-repository"))
    python = _ready_oracle(Path(".parity", "oracle"))
    assert build_oracle("v9.9.9", Path(".parity", "oracle")) == python.absolute()


def test_is_ready_refuses_a_changed_wheel_or_another_python(tmp_path: Path) -> None:
    out = tmp_path / "oracle"
    _ready_oracle(out)
    assert is_ready(out)
    (out / READY_FILE).write_text("0" * 64 + "\n", encoding="utf-8")
    assert not is_ready(out)
    other = tmp_path / "other"
    _ready_oracle(other, version="3.12")
    assert not is_ready(other)


@needs_git
def test_regression_missing_ref_fails_closed(repo: Path) -> None:
    with pytest.raises(SystemExit, match=r"^E: ref v9\.9\.9 is not in this repository"):
        build_oracle("v9.9.9", Path(".parity", "oracle"))
    assert not (repo / ".parity" / "oracle" / READY_FILE).exists()


@needs_git
def test_regression_failed_build_removes_the_worktree(repo: Path) -> None:
    with pytest.raises(SystemExit, match=r"^E: building the wheel failed"):
        build_oracle(TAG, Path(".parity", "oracle"))
    assert sorted(path.name for path in (repo / ".parity").iterdir()) == ["oracle"]
    assert not (repo / ".parity" / "oracle" / READY_FILE).exists()
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_refuses_a_symlinked_parity_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / ".parity").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(SystemExit, match="symlink"):
        build_oracle("v9.9.9", Path(".parity", "oracle"))
    assert list((tmp_path / "elsewhere").iterdir()) == []
