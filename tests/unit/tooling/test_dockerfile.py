#!/usr/bin/env python3

"""
test_dockerfile.py --- tests that the Docker test image holds every file the test suite reads

Contains:
    NOT_IN_IMAGE: names each tracked top-level entry the image leaves out, with the reason
    copied_paths(): lists the build-context paths the runtime stage of a Dockerfile copies
    tracked_top_level(): lists the first path segment of every file git tracks
    skipped_entries(): lists the top-level entries the build context holds but the image skips
    read_paths(): lists the repository paths a test module reads through `ROOT / ...` constants
    is_in_image(): tells whether a repository path is copied and not excluded by .dockerignore
    missing_image_paths(): lists the paths a test module reads that a Dockerfile leaves out
    test_regression_docker_image_holds_every_path_the_noxfile_tests_read: fails when the image
        lacks a file that tests/unit/tooling/test_noxfile.py reads, such as the CI workflow
    test_regression_missing_dockerfile_fails_in_a_source_checkout: a moved or renamed Dockerfile
        fails the check instead of skipping it
    test_docker_path_check_flags_a_missing_and_an_ignored_path: proves the check can fail
    test_image_copies_every_tracked_top_level_entry: fails when the image skips a tracked
        top-level entry that NOT_IN_IMAGE does not name, or NOT_IN_IMAGE names a stale entry
    test_top_level_check_flags_a_dropped_copy_and_a_new_folder: proves the check can fail
    unlocked_builds(): lists the stages that run `uv build` without cargo's --locked
    test_image_build_fails_on_a_stale_cargo_lock: the builder never rewrites Cargo.lock
    test_unlocked_builds_sees_a_missing_or_late_lock: proves the lock check can fail
"""

import ast
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"
LIBRARY_ROOT = REPO_ROOT / "e8s"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
NOXFILE_TESTS = REPO_ROOT / "tests" / "unit" / "tooling" / "test_noxfile.py"
RUNTIME_STAGE = "runtime"
BUILDER_STAGE = "builder"
LOCKED_BUILD_ENV = "MATURIN_PEP517_ARGS=--locked"
UV_BUILD = "uv build"
ROOT_NAME = "ROOT"
NOT_IN_IMAGE = {
    "_typos.toml": "spell-check settings; lint runs outside the image",
    ".dockerignore": "read by docker build, not by the tests",
    "about.toml": "notice settings for nox -s rust, outside the image",
    "deny.toml": "crate policy for nox -s rust; read only by source-checkout tests",
    ".env.example": "compose reads .env from the host",
    ".gitattributes": "git settings; the image has no .git",
    ".gitignore": "git settings; the image has no .git",
    ".pre-commit-config.yaml": "hooks run on the host",
    ".prettierignore": "Prettier runs in lint, outside the image",
    ".prettierrc.json": "Prettier runs in lint, outside the image",
    "CHANGELOG.md": "generated at release time; no test reads it",
    "CLAUDE.md": "agent instructions; read only by source-checkout tests",
    "cliff.toml": "the changelog session runs outside the image",
    "CONTRIBUTING.md": "read only by source-checkout tests",
    "docker": "builds the image; read only by source-checkout tests",
    "package-lock.json": "Node tooling for lint, outside the image",
    "package.json": "Node tooling for lint, outside the image",
    "SECURITY.md": "policy text; no test reads it",
    "e8s": "built into the wheel the image installs; tests import it",
    "typings": "stubs for the types session, outside the image",
}
pytestmark = pytest.mark.skipif(
    not LIBRARY_ROOT.is_dir(), reason="needs a source checkout; the wheel test has no e8s tree"
)


def copied_paths(dockerfile: str, stage: str = RUNTIME_STAGE) -> list[str]:
    """Lists the build-context paths that the COPY lines of one stage copy.

    Args:
        dockerfile: The text of a Dockerfile.
        stage: The `AS` name of the stage to read.

    Returns:
        sources: Each COPY source of that stage, relative to the build context, without `./`.
    """
    sources: list[str] = []
    current = ""
    for line in dockerfile.splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        if words[0].upper() == "FROM":
            current = words[-1] if len(words) >= 4 and words[-2].upper() == "AS" else ""
        elif words[0].upper() == "COPY" and current == stage:
            arguments = [word for word in words[1:] if not word.startswith("--")]
            if any(word.startswith("--from") for word in words[1:]):
                continue
            sources += [source.removeprefix("./").rstrip("/") for source in arguments[:-1]]
    return sources


def tracked_top_level(root: Path) -> set[str]:
    """Lists the first path segment of every file git tracks in a checkout.

    Args:
        root: The repository root; git must be installed and the checkout must have .git.

    Returns:
        entries: Each distinct top-level file or folder name.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    return {path.split("/")[0] for path in listing.split("\0") if path}


def skipped_entries(entries: set[str], copied: list[str], ignored: list[str]) -> set[str]:
    """Lists the top-level entries the build context holds but the runtime stage never copies.

    Args:
        entries: Top-level names in the repository.
        copied: Sources the runtime stage copies.
        ignored: Patterns from .dockerignore; only plain paths and folder prefixes are understood.

    Returns:
        skipped: Each entry neither excluded by .dockerignore nor copied, in whole or in part.
    """
    excluded = {pattern.rstrip("/") for pattern in ignored if "*" not in pattern}
    copied_tops = {source.split("/")[0] for source in copied}
    return entries - excluded - copied_tops


def _path_parts(node: ast.expr) -> list[str] | None:
    """Returns the string parts of a `ROOT / "a" / "b"` expression, or None for anything else."""
    if isinstance(node, ast.Name) and node.id == ROOT_NAME:
        return []
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        head = _path_parts(node.left)
        right = node.right
        if head is not None and isinstance(right, ast.Constant) and isinstance(right.value, str):
            return [*head, right.value]
    return None


def read_paths(source: str) -> list[str]:
    """Lists the repository paths a test module names through module-level `ROOT / ...` constants.

    Args:
        source: The text of a test module.

    Returns:
        paths: Each such path, relative to the repository root, in definition order.
    """
    paths: list[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            parts = _path_parts(node.value)
            if parts:
                paths.append("/".join(parts))
    return paths


def is_in_image(path: str, copied: list[str], ignored: list[str]) -> bool:
    """Tells whether a repository path is copied into the image and not excluded from the context.

    Args:
        path: Repository-relative path a test reads.
        copied: Sources the runtime stage copies.
        ignored: Patterns from .dockerignore; only plain paths and folder prefixes are understood.

    Returns:
        is_present: Whether the image holds the path.
    """
    prefixes = [pattern.rstrip("/") for pattern in ignored if pattern and "*" not in pattern]
    if any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes):
        return False
    return any(path == source or path.startswith(f"{source}/") for source in copied)


def _ignored(text: str) -> list[str]:
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def missing_image_paths(dockerfile: Path, dockerignore: Path, test_module: Path) -> list[str]:
    """Lists the paths a test module reads that the Dockerfile's runtime stage leaves out.

    Args:
        dockerfile: The Dockerfile that builds the test image; it must exist.
        dockerignore: The .dockerignore of the build context.
        test_module: The test module whose `ROOT / ...` constants name the paths it reads.

    Returns:
        missing: Each read path the image does not hold. A missing Dockerfile fails the test.
    """
    if not dockerfile.is_file():
        pytest.fail(f"E: {dockerfile} is missing; the Docker image check needs it")
    copied = copied_paths(dockerfile.read_text(encoding="utf-8"))
    ignored = _ignored(dockerignore.read_text(encoding="utf-8"))
    return [
        path
        for path in read_paths(test_module.read_text(encoding="utf-8"))
        if not is_in_image(path, copied, ignored)
    ]


def test_regression_docker_image_holds_every_path_the_noxfile_tests_read() -> None:
    assert ".github/workflows/ci.yml" in read_paths(NOXFILE_TESTS.read_text(encoding="utf-8"))
    missing = missing_image_paths(DOCKERFILE, DOCKERIGNORE, NOXFILE_TESTS)
    assert missing == [], f"E: docker/Dockerfile does not copy {missing}"


def test_regression_missing_dockerfile_fails_in_a_source_checkout() -> None:
    moved = REPO_ROOT / "docker" / "Dockerfile.moved"
    assert LIBRARY_ROOT.is_dir()
    assert not moved.exists()
    with pytest.raises(pytest.fail.Exception, match=r"Dockerfile\.moved is missing"):
        missing_image_paths(moved, DOCKERIGNORE, NOXFILE_TESTS)


def test_docker_path_check_flags_a_missing_and_an_ignored_path() -> None:
    dockerfile = (
        "FROM python AS builder\n"
        "COPY e8s ./e8s\n"
        "FROM python AS runtime\n"
        "COPY --from=builder /dist /dist\n"
        "COPY noxfile.py ./\n"
        "COPY docs ./docs\n"
    )
    copied = copied_paths(dockerfile)
    assert copied == ["noxfile.py", "docs"]
    assert is_in_image("noxfile.py", copied, [])
    assert not is_in_image("e8s/core/types.py", copied, [])
    assert not is_in_image("docs/a.md", copied, ["docs/"])
    assert read_paths('ROOT = Path(".")\nCI = ROOT / ".github" / "ci.yml"\nX = 1\n') == [
        ".github/ci.yml"
    ]


def test_image_copies_every_tracked_top_level_entry() -> None:
    copied = copied_paths(DOCKERFILE.read_text(encoding="utf-8"))
    ignored = _ignored(DOCKERIGNORE.read_text(encoding="utf-8"))
    skipped = skipped_entries(tracked_top_level(REPO_ROOT), copied, ignored)
    unplaced = sorted(skipped - NOT_IN_IMAGE.keys())
    assert unplaced == [], f"E: docker/Dockerfile does not copy {unplaced}"
    stale = sorted(NOT_IN_IMAGE.keys() - skipped)
    assert stale == [], f"E: NOT_IN_IMAGE names {stale}, which the image copies or never sees"


def test_top_level_check_flags_a_dropped_copy_and_a_new_folder() -> None:
    entries = {"scripts", "tests", "docs", ".github"}
    copied = ["scripts", "tests", ".github/workflows/ci.yml"]
    assert skipped_entries(entries, copied, ["docs/"]) == set()
    assert skipped_entries(entries, copied[1:], ["docs/"]) == {"scripts"}
    assert skipped_entries(entries | {"assets"}, copied, ["docs/"]) == {"assets"}


def unlocked_builds(dockerfile: str) -> list[str]:
    """Lists the stages that run `uv build` before setting cargo's --locked for maturin.

    Args:
        dockerfile: The text of a Dockerfile.

    Returns:
        stages: The `AS` name of each stage, once per `uv build` RUN line that no earlier
            `ENV MATURIN_PEP517_ARGS=--locked` of the same stage covers.
    """
    unlocked: list[str] = []
    current = ""
    is_locked = False
    for line in dockerfile.splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        keyword = words[0].upper()
        if keyword == "FROM":
            current = words[-1] if len(words) >= 4 and words[-2].upper() == "AS" else ""
            is_locked = False
        elif keyword == "ENV" and LOCKED_BUILD_ENV in words[1:]:
            is_locked = True
        elif keyword == "RUN" and UV_BUILD in line and not is_locked:
            unlocked.append(current)
    return unlocked


def test_image_build_fails_on_a_stale_cargo_lock() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert any(UV_BUILD in line for line in dockerfile.splitlines() if line.startswith("RUN"))
    assert unlocked_builds(dockerfile) == []


def test_unlocked_builds_sees_a_missing_or_late_lock() -> None:
    dockerfile = (
        "FROM python AS locked\n"
        "ENV MATURIN_PEP517_ARGS=--locked\n"
        "RUN uv build --wheel\n"
        "FROM python AS late\n"
        "RUN uv build --wheel\n"
        "ENV MATURIN_PEP517_ARGS=--locked\n"
        "FROM python AS other\n"
        "RUN uv build --sdist\n"
    )
    assert unlocked_builds(dockerfile) == ["late", "other"]
