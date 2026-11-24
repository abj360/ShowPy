#!/usr/bin/env python3

"""
test_check_api_changes.py --- tests the commit-range check that API changes are named in commits

Contains:
    GitRepo: drives a throwaway git repository with a fixed identity and no user config
    repo: fixture returning an empty GitRepo with one root commit
    test_regression_commit_changing_the_api_without_naming_it_fails: an unnamed change fails
    test_commit_naming_every_changed_name_in_its_subject_passes: subject names count
    test_names_in_a_breaking_change_footer_count: both footer spellings count
    test_names_in_the_body_outside_the_footer_do_not_count: prose in the body never counts
    test_commits_that_leave_the_snapshot_alone_are_ignored: non-API commits never fail
    test_commits_up_to_the_last_release_tag_are_not_checked: the range starts at the last tag
    test_commits_up_to_the_rule_start_are_not_checked: the range starts at the rule start
    test_merge_commits_are_not_checked: only the commits that made a change are checked
    test_removed_and_deprecated_alias_names_must_be_named: every kind of entry change counts
    test_regression_deleting_the_snapshot_needs_every_name: a deleted or regenerated snapshot
        cannot hide drift, since each commit is diffed against its parent's snapshot
    test_run_reports_the_commit_and_the_missing_names: the failure names the commit and names
    test_run_passes_and_counts_the_checked_commits: a clean range exits 0 with a count
    test_regression_unknown_rule_start_fails_closed: a missing rule-start commit is an error
    test_regression_inherited_git_dir_never_redirects_the_check: a GIT_DIR inherited from a git
        hook or rebase --exec neither redirects the check nor the test helper to another repo
    test_changed_public_names_lists_each_kind_of_change: checks the name diff on a synthetic pair
    test_announcing_text_keeps_the_subject_and_breaking_footers: checks the message slicing
    test_is_named_accepts_an_attribute_path_but_not_a_longer_name: checks the name match
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.check_api_changes import (
    SNAPSHOT_PATH,
    JsonValue,
    announcing_text,
    changed_public_names,
    find_violations,
    git_env,
    is_named,
    run,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

ISOLATED_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.invalid",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.invalid",
}
BASE_API: dict[str, JsonValue] = {"alpha": "(a)", "deprecated": {}}
GROWN_API: dict[str, JsonValue] = {"alpha": "(a, b)", "beta": "()", "deprecated": {}}
OTHER_PATH = "notes.txt"


class GitRepo:
    """Drives a throwaway git repository with its own GIT_DIR, a fixed identity and no user config.

    Attributes:
        path: The repository's working tree.
        config: An empty file standing in for the user's global git config.
    """

    def __init__(self, path: Path, config: Path) -> None:
        self.path = path
        self.config = config

    def git(self, *args: str) -> str:
        """Runs one git command in the repository and returns its stdout."""
        env = {
            **git_env(),
            **ISOLATED_GIT_ENV,
            "GIT_CONFIG_GLOBAL": str(self.config),
            "GIT_DIR": str(self.path / ".git"),
            "GIT_WORK_TREE": str(self.path),
        }
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        return result.stdout.strip()

    def commit(self, message: str, *, api: dict[str, JsonValue] | None = None) -> str:
        """Commits the snapshot when given, else a change to another file, and returns the SHA."""
        changed = OTHER_PATH if api is None else SNAPSHOT_PATH
        target = self.path / changed
        if api is None:
            existing = target.read_text() if target.exists() else ""
            target.write_text(existing + message + "\n")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(api, indent=2, sort_keys=True) + "\n")
        self.git("add", "--", changed)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    config = tmp_path / "gitconfig"
    config.touch()
    git_repo = GitRepo(tmp_path / "repo", config)
    git_repo.path.mkdir()
    git_repo.git("init", "-q", "-b", "main")
    git_repo.commit("chore: start", api=BASE_API)
    return git_repo


def _root(repo: GitRepo) -> str:
    return repo.git("rev-list", "--max-parents=0", "HEAD")


def test_regression_commit_changing_the_api_without_naming_it_fails(repo: GitRepo) -> None:
    sha = repo.commit("feat: grow `alpha`", api=GROWN_API)

    violations = find_violations(repo.path, rule_start=_root(repo))

    assert [(v.sha, v.missing) for v in violations] == [(sha, ("beta",))]


def test_commit_naming_every_changed_name_in_its_subject_passes(repo: GitRepo) -> None:
    repo.commit("feat: grow `alpha` and add `beta`", api=GROWN_API)

    assert find_violations(repo.path, rule_start=_root(repo)) == []


@pytest.mark.parametrize("token", ["BREAKING CHANGE", "BREAKING-CHANGE"])
def test_names_in_a_breaking_change_footer_count(repo: GitRepo, token: str) -> None:
    message = f"feat!: grow the API\n\nWhy.\n\n{token}: `alpha` grows b; new `beta`"
    repo.commit(message, api=GROWN_API)

    assert find_violations(repo.path, rule_start=_root(repo)) == []


def test_names_in_the_body_outside_the_footer_do_not_count(repo: GitRepo) -> None:
    message = "feat: grow the API\n\n`alpha` grows b and `beta` is new.\n\nRefs: `beta`"
    repo.commit(message, api=GROWN_API)

    violations = find_violations(repo.path, rule_start=_root(repo))

    assert [v.missing for v in violations] == [("alpha", "beta")]


def test_commits_that_leave_the_snapshot_alone_are_ignored(repo: GitRepo) -> None:
    repo.commit("docs: no names here")
    repo.commit("fix: still none")

    assert find_violations(repo.path, rule_start=_root(repo)) == []


def test_commits_up_to_the_last_release_tag_are_not_checked(repo: GitRepo) -> None:
    repo.commit("feat: grow it", api=GROWN_API)
    repo.git("tag", "v0.1.0")
    sha = repo.commit("feat: drop it", api=BASE_API)

    violations = find_violations(repo.path, rule_start=_root(repo))

    assert [(v.sha, v.missing) for v in violations] == [(sha, ("alpha", "beta"))]


def test_commits_up_to_the_rule_start_are_not_checked(repo: GitRepo) -> None:
    rule_start = repo.commit("feat: grow it", api=GROWN_API)

    assert find_violations(repo.path, rule_start=rule_start) == []


def test_merge_commits_are_not_checked(repo: GitRepo) -> None:
    rule_start = _root(repo)
    repo.git("checkout", "-q", "-b", "lane")
    repo.commit("feat: grow `alpha` and add `beta`", api=GROWN_API)
    repo.git("checkout", "-q", "main")
    repo.commit("docs: unrelated")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge lane", "lane")

    assert find_violations(repo.path, rule_start=rule_start) == []


def test_removed_and_deprecated_alias_names_must_be_named(repo: GitRepo) -> None:
    renamed: dict[str, JsonValue] = {
        "omega": "(a)",
        "deprecated": {"alpha": {"target": "omega", "since": "0.2.0", "remove": "0.3.0"}},
    }
    repo.commit("refactor!: rename `omega`", api=renamed)

    violations = find_violations(repo.path, rule_start=_root(repo))

    assert [v.missing for v in violations] == [("alpha",)]


def test_regression_deleting_the_snapshot_needs_every_name(repo: GitRepo) -> None:
    rule_start = _root(repo)
    repo.git("rm", "-q", "--", SNAPSHOT_PATH)
    repo.git("commit", "-q", "-m", "chore: drop the snapshot")
    deleted = repo.git("rev-parse", "HEAD")
    regenerated = repo.commit("chore: regenerate the snapshot", api=GROWN_API)

    violations = find_violations(repo.path, rule_start=rule_start)

    assert [(v.sha, v.missing) for v in violations] == [
        (deleted, ("alpha",)),
        (regenerated, ("alpha", "beta")),
    ]


def test_run_reports_the_commit_and_the_missing_names(
    repo: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    sha = repo.commit("feat: grow `alpha`", api=GROWN_API)

    assert run(repo.path, rule_start=_root(repo)) == 1

    out = capsys.readouterr().out
    assert f"E: {sha[:12]} feat: grow `alpha`" in out
    assert "not named in the subject or a BREAKING CHANGE footer: `beta`" in out


def test_run_passes_and_counts_the_checked_commits(
    repo: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    repo.commit("feat: grow `alpha` and add `beta`", api=GROWN_API)
    repo.commit("docs: unrelated")

    assert run(repo.path, rule_start=_root(repo)) == 0
    assert "Checked 2 commits, API snapshot changes: 1" in capsys.readouterr().out


def test_regression_unknown_rule_start_fails_closed(
    repo: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(repo.path, rule_start="f" * 40) == 1
    assert capsys.readouterr().out.startswith("E: git ")


def test_regression_inherited_git_dir_never_redirects_the_check(
    repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = GitRepo(tmp_path / "outer", repo.config)
    outer.path.mkdir()
    outer.git("init", "-q", "-b", "main")
    outer_head = outer.commit("chore: outer start", api=BASE_API)
    monkeypatch.setenv("GIT_DIR", str(outer.path / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outer.path))
    sha = repo.commit("feat: grow `alpha`", api=GROWN_API)

    violations = find_violations(repo.path, rule_start=_root(repo))

    assert [(v.sha, v.missing) for v in violations] == [(sha, ("beta",))]
    assert outer.git("rev-parse", "HEAD") == outer_head


def test_changed_public_names_lists_each_kind_of_change() -> None:
    old: dict[str, JsonValue] = {
        "keep": "(a)",
        "drop": "(a)",
        "edit": "(a)",
        "deprecated": {"gone": {"target": "keep"}, "kept": {"target": "keep"}},
        "leaving": {"exit": {"kind": "function", "replacement": ["keep"]}},
    }
    new: dict[str, JsonValue] = {
        "keep": "(a)",
        "edit": "(a, b)",
        "add": "()",
        "deprecated": {"kept": {"target": "edit"}, "fresh": {"target": "keep"}},
        "leaving": {"exit": {"kind": "function", "replacement": ["edit"]}, "soon": {"kind": "x"}},
    }

    assert changed_public_names(old, new) == [
        "add",
        "drop",
        "edit",
        "exit",
        "fresh",
        "gone",
        "kept",
        "soon",
    ]
    assert changed_public_names({}, {"deprecated": {}, "leaving": {}}) == []


def test_announcing_text_keeps_the_subject_and_breaking_footers() -> None:
    message = (
        "feat!: rename `a`\n\nbody `b`\n\nBREAKING CHANGE: `c` and\n`d` change\nRefs: `e`\n"
        "BREAKING-CHANGE: `f`\n"
    )

    text = announcing_text(message)

    assert all(f"`{name}`" in text for name in "acdf")
    assert all(f"`{name}`" not in text for name in "be")


def test_is_named_accepts_an_attribute_path_but_not_a_longer_name() -> None:
    assert is_named("PathRow", "`PathRow`")
    assert is_named("PathRow", "holds `PathRow.motion_params` now")
    assert not is_named("PathRow", "`PathRows` and PathRow")
    assert not is_named("Path", "`PathRow`")
    assert not is_named("Row", "`PathRow`")
