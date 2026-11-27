#!/usr/bin/env python3

"""
test_upgrade_folders.py --- tests that an upgrade never writes into its source, however named

Contains:
    test_regression_a_target_that_names_the_source_another_way_is_refused: refuses a target
        inside the source named with another case, another Unicode normalization, a symlink or
        `..`, and the source itself, changing nothing under the source
    test_regression_dotdot_below_a_missing_folder_never_reaches_the_source: refuses a target
        that spells `..` below a folder that does not exist yet, writing nothing anywhere
    test_dotdot_below_a_missing_folder_is_refused_outside_the_source_too: refuses such a target
        where the file system follows `..`, even when it would land outside the source
    test_a_symlink_to_nothing_in_the_target_is_refused: refuses a target through a dangling
        symlink before anything is written
    test_a_new_nested_target_outside_the_source_is_upgraded: upgrades into fresh nested folders,
        and through `..` under folders that exist
    test_a_dot_below_a_missing_folder_is_dropped: upgrades `x/./y` with `x` missing into `x/y`
"""

import shutil
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest

from e8s.core.errors import StorageError
from e8s.show.file_repository import FileRepository
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.upgrade_repos import FIXTURES, LATEST, fingerprint

FOLDER = "Répertoire"
LEGACY_SHOW = "legacy-show"
SKIP_LINKS = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege")
FOLLOWS_DOTDOT = pytest.mark.skipif(
    sys.platform == "win32", reason="Windows removes `..` from a path before the file system"
)


def _aliases(folder: Path, alias: str) -> bool:
    """Tells whether the file system finds `folder` under the other spelling `alias`."""
    other = folder.parent / alias
    return other.exists() and other.samefile(folder)


def _case_changed(tmp_path: Path, source: Path) -> Path:
    if not _aliases(source, source.name.lower()):
        pytest.skip("the file system tells names apart by case")
    return tmp_path / source.name.lower() / "new"


def _renormalized(tmp_path: Path, source: Path) -> Path:
    other = unicodedata.normalize("NFD", source.name)
    if not _aliases(source, other):
        pytest.skip("the file system tells Unicode normalizations apart")
    return tmp_path / other / "new"


def _symlinked(tmp_path: Path, source: Path) -> Path:
    (tmp_path / "link").symlink_to(source, target_is_directory=True)
    return tmp_path / "link" / "new"


def _dotted(tmp_path: Path, source: Path) -> Path:
    (tmp_path / "elsewhere").mkdir()
    return tmp_path / "elsewhere" / ".." / source.name / "new"


def _itself(_tmp_path: Path, source: Path) -> Path:
    return source


TARGETS = [
    pytest.param(_case_changed, id="case"),
    pytest.param(_renormalized, id="nfd"),
    pytest.param(_symlinked, marks=SKIP_LINKS, id="symlink"),
    pytest.param(_dotted, id="dotdot"),
    pytest.param(_itself, id="itself"),
]


@pytest.mark.parametrize("target_of", TARGETS)
def test_regression_a_target_that_names_the_source_another_way_is_refused(
    tmp_path: Path, target_of: Callable[[Path, Path], Path]
) -> None:
    source = tmp_path / unicodedata.normalize("NFC", FOLDER)
    shutil.copytree(FIXTURES / LATEST / "repo", source)
    target = target_of(tmp_path, source)
    before = fingerprint(source)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, target)
    assert caught.value.code == "BAD_PATH"
    assert fingerprint(source) == before


def _source_in(tmp_path: Path) -> Path:
    source = tmp_path / unicodedata.normalize("NFC", FOLDER)
    shutil.copytree(FIXTURES / LATEST / "repo", source)
    return source


def _names_in(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir())


MISSING_DOTS = [
    pytest.param(("missing", "..", FOLDER, "new"), id="missing-dotdot"),
    pytest.param(("a", "b", "..", "..", FOLDER, "new"), id="missing-deeper"),
    pytest.param(("missing", "..", FOLDER), id="missing-dotdot-source"),
    pytest.param(("x", ".", "..", FOLDER, "y"), id="missing-dot-dotdot"),
]


@pytest.mark.parametrize("names", MISSING_DOTS)
def test_regression_dotdot_below_a_missing_folder_never_reaches_the_source(
    tmp_path: Path, names: tuple[str, ...]
) -> None:
    source = _source_in(tmp_path)
    before = fingerprint(source)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, tmp_path.joinpath(*names))
    assert caught.value.code == "BAD_PATH"
    assert fingerprint(source) == before
    assert _names_in(tmp_path) == [source.name]


@FOLLOWS_DOTDOT
def test_dotdot_below_a_missing_folder_is_refused_outside_the_source_too(tmp_path: Path) -> None:
    source = _source_in(tmp_path)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, tmp_path / "missing" / ".." / "elsewhere" / "new")
    assert caught.value.code == "BAD_PATH"
    assert "`..`" in caught.value.message
    assert _names_in(tmp_path) == [source.name]


@SKIP_LINKS
def test_a_symlink_to_nothing_in_the_target_is_refused(tmp_path: Path) -> None:
    source = _source_in(tmp_path)
    (tmp_path / "dangling").symlink_to(source / "missing", target_is_directory=True)
    before = fingerprint(source)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, tmp_path / "dangling" / "new")
    assert caught.value.code == "BAD_PATH"
    assert "not a folder" in caught.value.message
    assert fingerprint(source) == before


@pytest.mark.parametrize(
    "names", [("out", "a", "b"), ("elsewhere", "..", "out", "new")], ids=["nested", "dotdot"]
)
def test_a_new_nested_target_outside_the_source_is_upgraded(
    tmp_path: Path, names: tuple[str, ...]
) -> None:
    source = _source_in(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    before = fingerprint(source)
    target = tmp_path.joinpath(*names)
    report = upgrade_repository(source, target)
    assert [show.show for show in report.shows] == [LEGACY_SHOW]
    assert FileRepository(target).head(LEGACY_SHOW)
    assert fingerprint(source) == before


def test_a_dot_below_a_missing_folder_is_dropped(tmp_path: Path) -> None:
    source = _source_in(tmp_path)
    upgrade_repository(source, tmp_path / "x" / "." / "y")
    assert _names_in(tmp_path / "x") == ["y"]
    assert FileRepository(tmp_path / "x" / "y").head(LEGACY_SHOW)
