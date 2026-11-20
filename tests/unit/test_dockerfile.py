#!/usr/bin/env python3

"""
test_dockerfile.py --- tests that the Docker test image holds every file the test suite reads

Contains:
    copied_paths(): lists the build-context paths the runtime stage of a Dockerfile copies
    read_paths(): lists the repository paths a test module reads through `ROOT / ...` constants
    is_in_image(): tells whether a repository path is copied and not excluded by .dockerignore
    missing_image_paths(): lists the paths a test module reads that a Dockerfile leaves out
    test_regression_docker_image_holds_every_path_the_noxfile_tests_read: fails when the image
        lacks a file that tests/unit/test_noxfile.py reads, such as the CI workflow
    test_regression_missing_dockerfile_fails_in_a_source_checkout: a moved or renamed Dockerfile
        fails the check instead of skipping it
    test_docker_path_check_flags_a_missing_and_an_ignored_path: proves the check can fail
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"
LIBRARY_ROOT = REPO_ROOT / "showpy"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
NOXFILE_TESTS = REPO_ROOT / "tests" / "unit" / "test_noxfile.py"
RUNTIME_STAGE = "runtime"
ROOT_NAME = "ROOT"
pytestmark = pytest.mark.skipif(
    not LIBRARY_ROOT.is_dir(), reason="needs a source checkout; the wheel test has no showpy tree"
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
        "COPY showpy ./showpy\n"
        "FROM python AS runtime\n"
        "COPY --from=builder /dist /dist\n"
        "COPY noxfile.py ./\n"
        "COPY docs ./docs\n"
    )
    copied = copied_paths(dockerfile)
    assert copied == ["noxfile.py", "docs"]
    assert is_in_image("noxfile.py", copied, [])
    assert not is_in_image("showpy/core/types.py", copied, [])
    assert not is_in_image("docs/a.md", copied, ["docs/"])
    assert read_paths('ROOT = Path(".")\nCI = ROOT / ".github" / "ci.yml"\nX = 1\n') == [
        ".github/ci.yml"
    ]
