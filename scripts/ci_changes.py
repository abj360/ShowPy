#!/usr/bin/env python3

"""
ci_changes.py --- decides whether a CI run needs the code, Rust and parity jobs, failing closed

Only a pull request whose every changed path sits under docs/ skips the code jobs. README.md
is not docs here: the wheel smoke test runs the README example. Only a pull request that changes
no Rust input skips the Rust job, and only one that changes neither the library, a Rust input
nor the parity harness skips the parity jobs. Every other event runs all three. Runs on the
runner's system Python, so it uses the standard library only.

Contains:
    DOCS_PREFIX: the one folder whose changes alone skip the code jobs
    RUST_PREFIXES: the folders whose changes run the Rust job
    RUST_FILES: the files whose changes run the Rust job
    PARITY_PREFIXES: the folders whose changes, besides the Rust inputs, run the parity jobs
    PARITY_FILES: the files whose changes, besides the Rust inputs, run the parity jobs
    is_docs_only(): tells whether a non-empty change list lies entirely under DOCS_PREFIX
    is_rust_change(): tells whether any changed path is a Rust input or a Rust job input
    is_parity_change(): tells whether any changed path is a library, Rust or harness input
    diff_command(): builds the git command that lists a pull request's changed paths
    split_paths(): parses NUL-separated git output into paths
    git_changed_paths(): runs diff_command and returns the changed paths
    pull_request_paths(): checks a pull request's SHAs and lists its changed paths
    code_output(): returns "true" or "false" for the `code` output of one event
    rust_output(): returns "true" or "false" for the `rust` output of one event
    parity_output(): returns "true" or "false" for the `parity` output of one event
    main(): writes code=, rust= and parity= to GITHUB_OUTPUT, exiting non-zero on any error
"""

import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

DOCS_PREFIX = "docs/"
RUST_PREFIXES = ("crates/", "fuzz/")
CARGO_PREFIX = "Cargo."
# The Rust inputs, then what the Rust job itself reads: its workflow, the nox session and the
# pytest mark it runs, the FMA and notice scripts, cargo-about's settings, the shipped notice and
# the tests that compare it.
RUST_FILES = frozenset(
    {
        "rust-toolchain.toml",
        "deny.toml",
        ".github/workflows/ci.yml",
        "noxfile.py",
        "pyproject.toml",
        "scripts/check_no_fma.py",
        "scripts/gen_third_party_notices.py",
        "about.toml",
        "e8s/licenses/THIRD_PARTY_RUST.txt",
        "tests/unit/tooling/test_third_party_notices.py",
    }
)
# The library and its compiled core, then the harness: its cases, the oracle builder and the
# locked dependencies both sides import.
PARITY_PREFIXES = ("e8s/", "tests/parity/")
PARITY_FILES = frozenset(
    {"scripts/build_oracle.py", "scripts/oracle_build.in", "scripts/oracle_build.txt", "uv.lock"}
)
PULL_REQUEST_EVENT = "pull_request"
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
OUTPUT_ENV = "GITHUB_OUTPUT"
RUN_JOBS = "true"
SKIP_JOBS = "false"

type ChangeLister = Callable[[str, str], list[str]]


def is_docs_only(paths: Sequence[str]) -> bool:
    """Tells whether every changed path is under docs/; an empty list is not docs-only.

    Args:
        paths: Repository-relative paths as git prints them.

    Returns:
        docs_only: True only for a non-empty list of paths that all start with docs/.
    """
    return bool(paths) and all(path.startswith(DOCS_PREFIX) for path in paths)


def is_rust_change(paths: Sequence[str]) -> bool:
    """Tells whether any changed path is a Rust input or a file the Rust job reads.

    Args:
        paths: Repository-relative paths as git prints them.

    Returns:
        rust_change: True when a path is under RUST_PREFIXES, is a root Cargo.* file or is
            one of RUST_FILES.
    """
    return any(
        path.startswith(RUST_PREFIXES)
        or (path.startswith(CARGO_PREFIX) and "/" not in path)
        or path in RUST_FILES
        for path in paths
    )


def is_parity_change(paths: Sequence[str]) -> bool:
    """Tells whether any changed path is a library file, a Rust input or a parity harness input.

    Args:
        paths: Repository-relative paths as git prints them.

    Returns:
        parity_change: True when a path is a Rust input, is under PARITY_PREFIXES or is one of
            PARITY_FILES.
    """
    return is_rust_change(paths) or any(
        path.startswith(PARITY_PREFIXES) or path in PARITY_FILES for path in paths
    )


def diff_command(base: str, head: str) -> list[str]:
    """Builds the git command listing the paths a pull request changes since its merge base.

    Renames are split into a deletion and an addition, so moving a file out of a code folder
    into docs/ still lists the code path.

    Args:
        base: Commit SHA of the pull request's base.
        head: Commit SHA of the pull request's head.

    Returns:
        command: The argument vector for git diff.
    """
    return ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"]


def split_paths(output: str) -> list[str]:
    """Parses NUL-separated git output into paths."""
    return [path for path in output.split("\0") if path]


def git_changed_paths(base: str, head: str) -> list[str]:
    """Runs git diff in the current checkout and returns the changed paths."""
    result = subprocess.run(
        diff_command(base, head), check=True, capture_output=True, encoding="utf-8"
    )
    return split_paths(result.stdout)


def pull_request_paths(
    event_name: str, base: str, head: str, list_changes: ChangeLister
) -> list[str] | None:
    """Lists a pull request's changed paths after checking both SHAs; None for other events.

    Args:
        event_name: The GitHub event that started the workflow.
        base: Commit SHA of the pull request's base, ignored for other events.
        head: Commit SHA of the pull request's head, ignored for other events.
        list_changes: Lists the paths changed between two commits.

    Returns:
        paths: The changed paths of a pull request, or None when the event is not one.
    """
    if event_name != PULL_REQUEST_EVENT:
        return None
    for name, sha in (("base", base), ("head", head)):
        if not COMMIT_SHA.match(sha):
            raise SystemExit(f"E: pull request {name} SHA {sha!r} is not a commit SHA")
    return list_changes(base, head)


def code_output(event_name: str, base: str, head: str, list_changes: ChangeLister) -> str:
    """Returns "false" only for a pull request that changes nothing outside docs/.

    Args:
        event_name: The GitHub event that started the workflow.
        base: Commit SHA of the pull request's base, ignored for other events.
        head: Commit SHA of the pull request's head, ignored for other events.
        list_changes: Lists the paths changed between two commits.

    Returns:
        code: "true" when the code jobs must run, else "false".
    """
    paths = pull_request_paths(event_name, base, head, list_changes)
    if paths is not None and is_docs_only(paths):
        return SKIP_JOBS
    return RUN_JOBS


def rust_output(event_name: str, base: str, head: str, list_changes: ChangeLister) -> str:
    """Returns "false" only for a pull request that changes no Rust input.

    Args:
        event_name: The GitHub event that started the workflow.
        base: Commit SHA of the pull request's base, ignored for other events.
        head: Commit SHA of the pull request's head, ignored for other events.
        list_changes: Lists the paths changed between two commits.

    Returns:
        rust: "true" when the Rust job must run, else "false".
    """
    paths = pull_request_paths(event_name, base, head, list_changes)
    if paths is not None and not is_rust_change(paths):
        return SKIP_JOBS
    return RUN_JOBS


def parity_output(event_name: str, base: str, head: str, list_changes: ChangeLister) -> str:
    """Returns "false" only for a pull request that changes no library, Rust or harness input.

    Args:
        event_name: The GitHub event that started the workflow.
        base: Commit SHA of the pull request's base, ignored for other events.
        head: Commit SHA of the pull request's head, ignored for other events.
        list_changes: Lists the paths changed between two commits.

    Returns:
        parity: "true" when the parity jobs must run, else "false".
    """
    paths = pull_request_paths(event_name, base, head, list_changes)
    if paths is not None and not is_parity_change(paths):
        return SKIP_JOBS
    return RUN_JOBS


def main() -> None:
    """Writes code=, rust= and parity= to GITHUB_OUTPUT; any error exits non-zero."""
    output = os.environ.get(OUTPUT_ENV)
    if not output:
        raise SystemExit(f"E: {OUTPUT_ENV} is not set")
    event = (
        os.environ.get("EVENT_NAME", ""),
        os.environ.get("BASE_SHA", ""),
        os.environ.get("HEAD_SHA", ""),
    )
    lines = [
        f"code={code_output(*event, git_changed_paths)}",
        f"rust={rust_output(*event, git_changed_paths)}",
        f"parity={parity_output(*event, git_changed_paths)}",
    ]
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.writelines(f"{line}\n" for line in lines)
    print(*lines, sep="\n")


if __name__ == "__main__":
    main()
