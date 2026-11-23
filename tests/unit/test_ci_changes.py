#!/usr/bin/env python3

"""
test_ci_changes.py --- tests the CI rule that only a docs-only pull request skips the code jobs

Contains:
    FakeGit: records each diff request and answers with fixed paths
    test_docs_only_is_true_only_when_every_path_is_under_docs: the exact docs-only rule
    test_regression_empty_change_list_runs_the_code_jobs: no changed path is not docs-only
    test_diff_command_splits_renames_and_uses_the_merge_base: renames and the three-dot range
    test_regression_rename_out_of_docs_counts_as_code: a move out of docs/ is a code change
    test_split_paths_reads_nul_separated_output: keeps paths with spaces and drops the tail
    test_pull_request_with_only_docs_skips_the_code_jobs: answers "false" for docs-only
    test_pull_request_with_code_runs_the_code_jobs: answers "true" when any path is outside docs/
    test_other_events_run_the_code_jobs_without_a_diff: push and others never diff
    test_regression_malformed_sha_fails_loudly: a missing or malformed SHA exits non-zero
    test_main_writes_the_code_output: main appends code=<value> to GITHUB_OUTPUT
    test_regression_main_without_github_output_fails_loudly: no output file exits non-zero
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.ci_changes import (
    code_output,
    diff_command,
    is_docs_only,
    main,
    split_paths,
)

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
PULL_REQUEST = "pull_request"


class FakeGit:
    """Records each diff request and answers with fixed paths.

    Attributes:
        paths: The changed paths every request returns.
        requests: Each (base, head) pair asked for, in order.
    """

    def __init__(self, paths: Sequence[str]) -> None:
        self.paths = list(paths)
        self.requests: list[tuple[str, str]] = []

    def __call__(self, base: str, head: str) -> list[str]:
        self.requests.append((base, head))
        return self.paths


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["docs/adr/0001.md"], True),
        (["docs/a.md", "docs/b/c.png"], True),
        (["docs/a.md", "README.md"], False),
        (["CHANGELOG.md"], False),
        (["README.md"], False),
        (["docs"], False),
        (["docsx/a.md"], False),
        (["Docs/a.md"], False),
        ([".github/workflows/ci.yml"], False),
        (["e8s/docs/a.md"], False),
    ],
)
def test_docs_only_is_true_only_when_every_path_is_under_docs(
    paths: list[str], *, expected: bool
) -> None:
    assert is_docs_only(paths) is expected


def test_regression_empty_change_list_runs_the_code_jobs() -> None:
    assert is_docs_only([]) is False


def test_diff_command_splits_renames_and_uses_the_merge_base() -> None:
    command = diff_command(BASE_SHA, HEAD_SHA)
    assert command[:3] == ["git", "diff", "--name-only"]
    assert "--no-renames" in command
    assert "-z" in command
    assert command[-1] == f"{BASE_SHA}...{HEAD_SHA}"


def test_regression_rename_out_of_docs_counts_as_code() -> None:
    git = FakeGit(["docs/engine.py", "e8s/choreo/engine.py"])
    assert code_output(PULL_REQUEST, BASE_SHA, HEAD_SHA, git) == "true"


def test_split_paths_reads_nul_separated_output() -> None:
    assert split_paths("docs/a b.md\0docs/c.md\0") == ["docs/a b.md", "docs/c.md"]


def test_pull_request_with_only_docs_skips_the_code_jobs() -> None:
    git = FakeGit(["docs/HANDOFF.md", "docs/adr/0022.md"])
    assert code_output(PULL_REQUEST, BASE_SHA, HEAD_SHA, git) == "false"
    assert git.requests == [(BASE_SHA, HEAD_SHA)]


def test_pull_request_with_code_runs_the_code_jobs() -> None:
    git = FakeGit(["docs/HANDOFF.md", "noxfile.py"])
    assert code_output(PULL_REQUEST, BASE_SHA, HEAD_SHA, git) == "true"


@pytest.mark.parametrize(
    "event", ["push", "schedule", "workflow_dispatch", "pull_request_target", ""]
)
def test_other_events_run_the_code_jobs_without_a_diff(event: str) -> None:
    git = FakeGit(["docs/HANDOFF.md"])
    assert code_output(event, "", "", git) == "true"
    assert git.requests == []


@pytest.mark.parametrize(
    ("base", "head"),
    [("", HEAD_SHA), (BASE_SHA, ""), ("--output=x", HEAD_SHA), (BASE_SHA, "main")],
)
def test_regression_malformed_sha_fails_loudly(base: str, head: str) -> None:
    git = FakeGit(["docs/HANDOFF.md"])
    with pytest.raises(SystemExit, match="E: "):
        code_output(PULL_REQUEST, base, head, git)
    assert git.requests == []


def test_main_writes_the_code_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "github_output"
    output.write_text("earlier=1\n")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("EVENT_NAME", "push")
    monkeypatch.delenv("BASE_SHA", raising=False)
    monkeypatch.delenv("HEAD_SHA", raising=False)
    main()
    assert output.read_text() == "earlier=1\ncode=true\n"


def test_regression_main_without_github_output_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setenv("EVENT_NAME", "push")
    with pytest.raises(SystemExit, match="E: GITHUB_OUTPUT"):
        main()
