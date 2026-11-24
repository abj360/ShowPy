#!/usr/bin/env python3

"""
test_build_oracle.py --- checks that the parity oracle is reused when ready and fails closed

Each test that needs git builds its own repository under tmp_path and points GIT_DIR at it.

Contains:
    test_interpreter_follows_the_platform: bin/python on POSIX, Scripts/python.exe on Windows
    test_ready_oracle_is_reused_without_any_work: no build when READY matches ref, commit, wheel
    test_regression_ready_oracle_of_another_ref_is_rebuilt: READY of another ref is not reused
    test_is_ready_refuses_a_changed_wheel_commit_ref_or_python: READY must match all of them
    test_regression_planted_venv_file_forces_a_rebuild: a changed or added venv file is not ready
    test_venv_digest_reads_contents_names_and_links: every file and link of the venv counts
    pinned_requirements(): reads name, version and hashes from a hashed requirements file
    test_oracle_build_requirements_are_pinned_with_hashes: every build requirement has hashes
    test_oracle_build_inputs_are_the_v040_build_requires: the inputs are the tag's own pins
    test_wheel_build_requires_hashed_build_requirements: uv build refuses unhashed ones
    test_regression_missing_ref_fails_closed: an absent tag exits with an E: line
    test_regression_moved_pinned_tag_fails_closed: a pinned ref on another commit is refused
    test_default_oracle_is_pinned_to_a_full_commit: the pin is a full SHA, without git
    test_default_oracle_is_pinned_to_the_v040_commit: the pin is the commit the tag names
    test_regression_missing_git_fails_closed: no git binary exits with an E: line
    test_regression_failed_build_removes_the_worktree: no worktree is left on a build failure
    test_refuses_a_symlinked_parity_folder: the oracle is never built through a symlink
    test_regression_refuses_an_oracle_folder_outside_parity: `--out .` deletes nothing
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import scripts.build_oracle as build_module
from scripts.build_oracle import (
    BUILD_REQUIREMENTS,
    DEFAULT_COMMIT,
    DEFAULT_REF,
    READY_FILE,
    VENV_FOLDER,
    WHEEL_FOLDER,
    build_command,
    build_oracle,
    interpreter,
    is_ready,
    require_inside_parity,
    venv_digest,
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
OTHER_REF = "v0.0.0"
COMMIT = "c" * 40
RUNNING = f"{sys.version_info.major}.{sys.version_info.minor}"
ORACLE = Path(".parity", "oracle")
ROOT = Path(__file__).resolve().parents[3]
FULL_SHA = r"^[0-9a-f]{40}$"
BUILD_INPUTS = ROOT / "scripts" / "oracle_build.in"
REQUIREMENT_LINE = re.compile(r"^([a-z0-9][a-z0-9._-]*)==(\S+) \\$")
HASH_LINE = re.compile(r"^    --hash=sha256:([0-9a-f]{64})(?: \\)?$")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
# The Docker image has neither git nor the repository's history; these read the v0.4.0 tag.
IS_GIT_CHECKOUT = shutil.which("git") is not None and (ROOT / ".git").exists()
needs_checkout = pytest.mark.skipif(not IS_GIT_CHECKOUT, reason="needs a git source checkout")


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


def _ready_oracle(out: Path, *, ref: str, commit: str, version: str = RUNNING) -> Path:
    wheel = out / WHEEL_FOLDER / "e8s-0.0.1-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"wheel")
    python = interpreter(out / VENV_FOLDER)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (out / VENV_FOLDER / "pyvenv.cfg").write_text(f"version_info = {version}\n", encoding="utf-8")
    stamp = f"ref {ref}\ncommit {commit}\nwheel-sha256 {hashlib.sha256(b'wheel').hexdigest()}\n"
    stamp += f"venv-sha256 {venv_digest(out / VENV_FOLDER)}\n"
    (out / READY_FILE).write_text(stamp, encoding="utf-8")
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


@needs_git
def test_ready_oracle_is_reused_without_any_work(repo: Path) -> None:
    commit = _git(repo, "rev-parse", f"{TAG}^{{commit}}").strip()
    python = _ready_oracle(ORACLE, ref=TAG, commit=commit)
    assert build_oracle(TAG, ORACLE) == python.absolute()
    assert sorted(path.name for path in (repo / ".parity").iterdir()) == ["oracle"]


@needs_git
def test_regression_ready_oracle_of_another_ref_is_rebuilt(repo: Path) -> None:
    commit = _git(repo, "rev-parse", f"{TAG}^{{commit}}").strip()
    _ready_oracle(ORACLE, ref=OTHER_REF, commit=commit)
    with pytest.raises(SystemExit, match=r"^E: building the wheel failed"):
        build_oracle(TAG, ORACLE)


def test_is_ready_refuses_a_changed_wheel_commit_ref_or_python(tmp_path: Path) -> None:
    out = tmp_path / "oracle"
    _ready_oracle(out, ref=TAG, commit=COMMIT)
    assert is_ready(out, TAG, COMMIT)
    assert not is_ready(out, OTHER_REF, COMMIT)
    assert not is_ready(out, TAG, "d" * 40)
    stamp = (out / READY_FILE).read_text(encoding="utf-8")
    (out / READY_FILE).write_text(stamp.replace("wheel-sha256 ", "wheel-sha256 0"), "utf-8")
    assert not is_ready(out, TAG, COMMIT)
    other = tmp_path / "other"
    _ready_oracle(other, ref=TAG, commit=COMMIT, version="3.12")
    assert not is_ready(other, TAG, COMMIT)


def test_regression_planted_venv_file_forces_a_rebuild(tmp_path: Path) -> None:
    out = tmp_path / "oracle"
    python = _ready_oracle(out, ref=TAG, commit=COMMIT)
    assert is_ready(out, TAG, COMMIT)
    planted = out / VENV_FOLDER / "lib" / "site-packages" / "sitecustomize.py"
    planted.parent.mkdir(parents=True)
    planted.write_text("print('planted')\n", encoding="utf-8")
    assert not is_ready(out, TAG, COMMIT)
    planted.unlink()
    assert is_ready(out, TAG, COMMIT)
    python.write_bytes(b"changed")
    assert not is_ready(out, TAG, COMMIT)


def test_venv_digest_reads_contents_names_and_links(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "lib" / "a.py").write_bytes(b"a")
    first = venv_digest(venv)
    (venv / "lib" / "a.py").rename(venv / "lib" / "b.py")
    renamed = venv_digest(venv)
    (venv / "lib" / "__pycache__").mkdir()
    (venv / "lib" / "__pycache__" / "b.cpython-314.pyc").write_bytes(b"pyc")
    cached = venv_digest(venv)
    assert len({first, renamed, cached}) == 3
    if sys.platform != "win32":
        (venv / "link").symlink_to("lib/b.py")
        assert venv_digest(venv) != cached


def pinned_requirements(text: str) -> dict[str, tuple[str, list[str]]]:
    """Reads each `name==version` requirement of a hashed requirements file with its hashes.

    Args:
        text: The text uv pip compile --generate-hashes wrote.

    Returns:
        pins: Requirement name to its version and its SHA-256 hashes, in file order.
    """
    pins: dict[str, tuple[str, list[str]]] = {}
    name = ""
    for line in text.splitlines():
        requirement, digest = REQUIREMENT_LINE.match(line), HASH_LINE.match(line)
        if requirement:
            name = requirement[1]
            pins[name] = (requirement[2], [])
        elif digest:
            pins[name][1].append(digest[1])
    return pins


def _inputs() -> list[str]:
    lines = BUILD_INPUTS.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line and not line.startswith("#")]


def test_oracle_build_requirements_are_pinned_with_hashes() -> None:
    pins = pinned_requirements(BUILD_REQUIREMENTS.read_text(encoding="utf-8"))
    assert {"hatchling", "hatch-vcs", "packaging", "pluggy"} <= set(pins)
    assert all(hashes for _, hashes in pins.values())
    for line in _inputs():
        name, version = line.split("==")
        assert pins[name][0] == version


@needs_checkout
def test_oracle_build_inputs_are_the_v040_build_requires() -> None:
    shown = _git(ROOT, "show", f"{DEFAULT_COMMIT}:pyproject.toml")
    assert tomllib.loads(shown)["build-system"]["requires"] == _inputs()


def test_wheel_build_requires_hashed_build_requirements() -> None:
    command = [str(part) for part in build_command(Path("wheels"))]
    at = command.index("--build-constraints")
    assert command[at + 1] == str(BUILD_REQUIREMENTS)
    assert "--require-hashes" in command


@needs_git
def test_regression_missing_ref_fails_closed(repo: Path) -> None:
    with pytest.raises(SystemExit, match=r"^E: ref v9\.9\.9 is not in this repository"):
        build_oracle("v9.9.9", ORACLE)
    assert not (repo / ORACLE / READY_FILE).exists()


@needs_git
def test_regression_moved_pinned_tag_fails_closed(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_module, "PINNED_COMMITS", {TAG: COMMIT})
    with pytest.raises(SystemExit, match=rf"^E: {TAG} resolves to [0-9a-f]{{40}}, not {COMMIT}"):
        build_oracle(TAG, ORACLE)
    assert not (repo / ".parity").exists()


def test_default_oracle_is_pinned_to_a_full_commit() -> None:
    assert build_module.PINNED_COMMITS == {DEFAULT_REF: DEFAULT_COMMIT}
    assert re.match(FULL_SHA, DEFAULT_COMMIT)


@needs_checkout
def test_default_oracle_is_pinned_to_the_v040_commit() -> None:
    assert (
        _git(ROOT, "rev-parse", "--verify", f"{DEFAULT_REF}^{{commit}}").strip() == DEFAULT_COMMIT
    )


def test_regression_missing_git_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit, match=r"^E: git is not installed"):
        build_oracle(DEFAULT_REF, ORACLE)
    assert not (tmp_path / ".parity").exists()


@needs_git
def test_regression_failed_build_removes_the_worktree(repo: Path) -> None:
    with pytest.raises(SystemExit, match=r"^E: building the wheel failed"):
        build_oracle(TAG, ORACLE)
    assert sorted(path.name for path in (repo / ".parity").iterdir()) == ["oracle"]
    assert not (repo / ORACLE / READY_FILE).exists()
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_refuses_a_symlinked_parity_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / ".parity").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(SystemExit, match="symlink"):
        build_oracle("v9.9.9", ORACLE)
    assert list((tmp_path / "elsewhere").iterdir()) == []


@pytest.mark.parametrize(
    "out", [Path(), Path(".parity"), Path("elsewhere"), Path(".parity", "..", "elsewhere")]
)
def test_regression_refuses_an_oracle_folder_outside_parity(
    out: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    keep = tmp_path / "elsewhere" / "keep.txt"
    keep.parent.mkdir()
    keep.write_text("kept", encoding="utf-8")
    with pytest.raises(SystemExit, match=r"^E: the oracle folder .* must be inside .*\.parity"):
        build_oracle("v9.9.9", out)
    assert keep.read_text(encoding="utf-8") == "kept"
    require_inside_parity(tmp_path / ".parity" / "oracle", tmp_path)
    require_inside_parity(Path(".parity", "oracle"), tmp_path)
