#!/usr/bin/env python3

"""
check_api_changes.py --- checks that each commit changing the public API names what it changed

Walks the non-merge commits after the last release tag. For each commit that changes the API
snapshot, every public name whose entry was added, removed or changed, deprecated aliases and
leaving names included, must appear backticked in the commit's subject or a `BREAKING CHANGE:`
footer, so the release-time changelog names it. Runs on the standard library only.

Contains:
    JsonValue: the shape of one snapshot entry
    Violation: one commit that changes the API without naming every changed name
    diff_snapshots(): lists the names added, removed and changed between two snapshots
    changed_public_names(): lists every public, deprecated and leaving name two snapshots dispute
    announcing_text(): returns the subject and BREAKING CHANGE footers of a commit message
    is_named(): tells whether text names a public name in backticks, alone or with an attribute
    git_env(): returns the environment without variables that point git at another repository
    find_violations(): lists the commits in range that change the API without naming it all
    run(): prints the violations and returns the exit code
    main(): checks the repository in the current directory
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SNAPSHOT_PATH = "tests/fixtures/api_snapshot.json"
DEPRECATED_KEY = "deprecated"
LEAVING_KEY = "leaving"
NESTED_KEYS = (DEPRECATED_KEY, LEAVING_KEY)
RELEASE_TAGS = "v[0-9]*"
# Commits up to this one followed the earlier rule (names in the changelog's Unreleased
# section) and are public, so they are never rechecked; the range starts at the later of it
# and the last tag.
RULE_START_COMMIT = "330ab98c003cb9e3afb4bd5c65b9788c7600704b"
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE: ", re.MULTILINE)
NEXT_FOOTER = re.compile(r"^(?:BREAKING[ -]CHANGE: |[A-Za-z][A-Za-z-]*(?:: | #))", re.MULTILINE)
SHORT_SHA = 12
# The variables `git rev-parse --local-env-vars` lists (git 2.50).
REPOSITORY_ENV_VARS = frozenset(
    (
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_OBJECT_DIRECTORY",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_GRAFT_FILE",
        "GIT_INDEX_FILE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_REPLACE_REF_BASE",
        "GIT_PREFIX",
        "GIT_SHALLOW_FILE",
        "GIT_COMMON_DIR",
    )
)

type JsonValue = str | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True)
class Violation:
    """Records one commit that changes the API without naming every changed name.

    Attributes:
        sha: The commit's full SHA.
        subject: The commit's subject line.
        missing: The changed names its subject and footers never name, sorted.
    """

    sha: str
    subject: str
    missing: tuple[str, ...]


def diff_snapshots(old: dict[str, JsonValue], new: dict[str, JsonValue]) -> dict[str, list[str]]:
    """Lists the names added, removed and changed between two snapshots.

    Args:
        old: The earlier snapshot.
        new: The later snapshot.

    Returns:
        diff: Sorted name lists under the keys "added", "removed" and "changed".
    """
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(name for name in old.keys() & new.keys() if old[name] != new[name]),
    }


def _nested(snapshot: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    section = snapshot.get(key, {})
    return section if isinstance(section, dict) else {}


def changed_public_names(old: dict[str, JsonValue], new: dict[str, JsonValue]) -> list[str]:
    """Lists every public, deprecated and leaving name whose entry two snapshots disagree on.

    Args:
        old: The snapshot before the commit, empty when the file did not exist.
        new: The snapshot after the commit, empty when the commit deleted the file.

    Returns:
        names: The added, removed and changed names, nested sections included, sorted.
    """
    names = {name for found in diff_snapshots(old, new).values() for name in found}
    names.difference_update(NESTED_KEYS)
    for key in NESTED_KEYS:
        nested = diff_snapshots(_nested(old, key), _nested(new, key))
        names.update(name for found in nested.values() for name in found)
    return sorted(names)


def announcing_text(message: str) -> str:
    """Returns the subject line and every BREAKING CHANGE footer of a commit message.

    A footer runs until the next footer token (`Token: ` or `Token #`) or the message's end.
    """
    subject, _, body = message.partition("\n")
    parts = [subject]
    for footer in BREAKING_FOOTER.finditer(body):
        rest = body[footer.end() :]
        following = NEXT_FOOTER.search(rest)
        parts.append(rest if following is None else rest[: following.start()])
    return "\n".join(parts)


def is_named(name: str, text: str) -> bool:
    """Tells whether text names a public name in backticks, alone or with an attribute path.

    `PathRow` and `PathRow.motion_params` both name `PathRow`; `PathRows` and a bare word do not.
    """
    return re.search(rf"`{re.escape(name)}(?:\.\w+)*`", text) is not None


def git_env() -> dict[str, str]:
    """Returns the environment without the variables that point git at another repository.

    Git hooks and `git rebase --exec` export GIT_DIR and friends; inherited, they would send a
    call meant for one repository to another.
    """
    return {key: value for key, value in os.environ.items() if key not in REPOSITORY_ENV_VARS}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=git_env()
    )
    return result.stdout


def _snapshot_at(repo: Path, rev: str | None) -> dict[str, JsonValue]:
    if rev is None or not _git(repo, "ls-tree", "--name-only", rev, "--", SNAPSHOT_PATH).strip():
        return {}
    snapshot: dict[str, JsonValue] = json.loads(_git(repo, "show", f"{rev}:{SNAPSHOT_PATH}"))
    return snapshot


def _commits_in_range(repo: Path, rule_start: str) -> list[str]:
    _git(repo, "rev-parse", "--verify", "--quiet", f"{rule_start}^{{commit}}")
    tags = _git(repo, "tag", "--merged", "HEAD", "--list", RELEASE_TAGS, "--sort=-v:refname")
    excluded = [f"^{rule_start}", *(f"^{tag}" for tag in tags.split()[:1])]
    return _git(repo, "rev-list", "--no-merges", "--reverse", "HEAD", *excluded).split()


def _violation(repo: Path, sha: str) -> Violation | None:
    parents = _git(repo, "rev-list", "--parents", "-n", "1", sha).split()[1:]
    old = _snapshot_at(repo, parents[0] if parents else None)
    names = changed_public_names(old, _snapshot_at(repo, sha))
    message = _git(repo, "log", "-1", "--format=%B", sha)
    text = announcing_text(message)
    missing = tuple(name for name in names if not is_named(name, text))
    if not missing:
        return None
    return Violation(sha=sha, subject=message.partition("\n")[0], missing=missing)


def _changes_snapshot(repo: Path, sha: str) -> bool:
    changed = _git(repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha)
    return SNAPSHOT_PATH in changed.splitlines()


def _snapshot_commits(repo: Path, rule_start: str) -> tuple[list[str], list[str]]:
    commits = _commits_in_range(repo, rule_start)
    return commits, [sha for sha in commits if _changes_snapshot(repo, sha)]


def _violations(repo: Path, shas: list[str]) -> list[Violation]:
    found = (_violation(repo, sha) for sha in shas)
    return [violation for violation in found if violation is not None]


def find_violations(repo: Path, *, rule_start: str) -> list[Violation]:
    """Lists the commits after the last release tag that change the API without naming it all.

    Args:
        repo: The repository's working tree.
        rule_start: The commit up to which commits are never checked.

    Returns:
        violations: One entry per offending commit, oldest first.
    """
    return _violations(repo, _snapshot_commits(repo, rule_start)[1])


def run(repo: Path, *, rule_start: str) -> int:
    """Prints each violation, or the count of checked commits, and returns the exit code.

    Args:
        repo: The repository's working tree.
        rule_start: The commit up to which commits are never checked.

    Returns:
        exit_code: 0 when every API change is named, 1 on a violation or a git error.
    """
    try:
        commits, changing = _snapshot_commits(repo, rule_start)
        violations = _violations(repo, changing)
    except subprocess.CalledProcessError as error:
        print(f"E: git {' '.join(error.cmd[3:])} failed: {(error.stderr or '').strip()}")
        return 1
    for violation in violations:
        names = ", ".join(f"`{name}`" for name in violation.missing)
        print(f"E: {violation.sha[:SHORT_SHA]} {violation.subject}")
        print(f"  not named in the subject or a BREAKING CHANGE footer: {names}")
    if violations:
        print("Fix: reword each commit to name every listed name in backticks")
        return 1
    print(f"Checked {len(commits)} commits, API snapshot changes: {len(changing)}")
    return 0


def main() -> None:
    """Checks the repository in the current directory and exits with run's code."""
    sys.exit(run(Path.cwd(), rule_start=RULE_START_COMMIT))


if __name__ == "__main__":
    main()
