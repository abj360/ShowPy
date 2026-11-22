#!/usr/bin/env python3

"""
ci_changes.py --- decides whether a CI run needs the code jobs, failing closed to "yes"

Only a pull request whose every changed path sits under docs/ skips the code jobs. README.md
is not docs here: the wheel smoke test runs the README example. Every other event runs the code
jobs. Runs on the runner's system Python, so it uses the standard library only.

Contains:
    DOCS_PREFIX: the one folder whose changes alone skip the code jobs
    is_docs_only(): tells whether a non-empty change list lies entirely under DOCS_PREFIX
    diff_command(): builds the git command that lists a pull request's changed paths
    split_paths(): parses NUL-separated git output into paths
    git_changed_paths(): runs diff_command and returns the changed paths
    code_output(): returns "true" or "false" for the `code` output of one event
    main(): writes code=<value> to GITHUB_OUTPUT, exiting non-zero on any error
"""

import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

DOCS_PREFIX = "docs/"
PULL_REQUEST_EVENT = "pull_request"
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
OUTPUT_ENV = "GITHUB_OUTPUT"
RUN_CODE_JOBS = "true"
SKIP_CODE_JOBS = "false"

type ChangeLister = Callable[[str, str], list[str]]


def is_docs_only(paths: Sequence[str]) -> bool:
    """Tells whether every changed path is under docs/; an empty list is not docs-only.

    Args:
        paths: Repository-relative paths as git prints them.

    Returns:
        docs_only: True only for a non-empty list of paths that all start with docs/.
    """
    return bool(paths) and all(path.startswith(DOCS_PREFIX) for path in paths)


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
    result = subprocess.run(diff_command(base, head), check=True, capture_output=True, text=True)
    return split_paths(result.stdout)


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
    if event_name != PULL_REQUEST_EVENT:
        return RUN_CODE_JOBS
    for name, sha in (("base", base), ("head", head)):
        if not COMMIT_SHA.match(sha):
            raise SystemExit(f"E: pull request {name} SHA {sha!r} is not a commit SHA")
    if is_docs_only(list_changes(base, head)):
        return SKIP_CODE_JOBS
    return RUN_CODE_JOBS


def main() -> None:
    """Writes code=<value> to GITHUB_OUTPUT; any error exits non-zero and fails the job."""
    output = os.environ.get(OUTPUT_ENV)
    if not output:
        raise SystemExit(f"E: {OUTPUT_ENV} is not set")
    code = code_output(
        os.environ.get("EVENT_NAME", ""),
        os.environ.get("BASE_SHA", ""),
        os.environ.get("HEAD_SHA", ""),
        git_changed_paths,
    )
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.write(f"code={code}\n")
    print(f"code={code}")


if __name__ == "__main__":
    main()
