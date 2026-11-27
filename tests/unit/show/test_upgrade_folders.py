#!/usr/bin/env python3

"""
test_upgrade_folders.py --- tests that an upgrade never writes into its source, however named

Contains:
    test_regression_a_target_that_names_the_source_another_way_is_refused: refuses a target
        inside the source named with another case, another Unicode normalization, a symlink or
        `..`, and the source itself, changing nothing under the source
"""

import shutil
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest

from e8s.core.errors import StorageError
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.upgrade_repos import FIXTURES, LATEST, fingerprint

FOLDER = "Répertoire"
SKIP_LINKS = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege")


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
