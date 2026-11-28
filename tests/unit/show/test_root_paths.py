#!/usr/bin/env python3

"""
test_root_paths.py --- tests that every storage entry point refuses a root path no file system takes

Contains:
    test_regression_a_nul_in_a_root_path_is_refused_with_bad_path: refuses a NUL in the root
        of every file-storage entry point with StorageError BAD_PATH, never a raw ValueError
    test_regression_a_nul_in_an_upgrade_path_is_refused_with_bad_path: refuses a NUL in the
        source or the target of an upgrade with StorageError BAD_PATH, writing nothing
    test_regression_an_unencodable_root_path_is_refused_with_bad_path: refuses a lone surrogate
        in the root of every file-storage entry point, never a raw UnicodeEncodeError
    test_regression_an_unencodable_upgrade_path_is_refused_with_bad_path: refuses a lone
        surrogate in the source or the target of an upgrade, writing nothing
    test_a_surrogate_escaped_byte_in_a_root_path_is_accepted: keeps a POSIX name byte that is
        not UTF-8, which Python spells as an escaped surrogate
"""

import shutil
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.errors import StorageError
from e8s.show.assets import FileAssetStore
from e8s.show.file_backend import FileBackend
from e8s.show.file_repository import FileRepository
from e8s.show.fsck import fsck
from e8s.show.gc import gc
from e8s.show.layout import CURRENT_LAYOUT, read_layout, require_usable_path
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.upgrade_repos import FIXTURES, LATEST, fingerprint

AT = datetime(2026, 11, 27, tzinfo=UTC)
NUL_NAME = "a\0b"
UNPAIRED_NAME = "a\ud800b"
ESCAPED_BYTE_NAME = "a\udcffb"
POSIX_ENCODING = pytest.mark.skipif(
    sys.platform == "win32", reason="Windows paths may hold unpaired surrogates"
)

ENTRY_POINTS: list[tuple[str, Callable[[Path], object]]] = [
    ("read_layout", read_layout),
    ("FileRepository", FileRepository),
    ("FileBackend", FileBackend),
    ("FileAssetStore", FileAssetStore),
    ("fsck", fsck),
    ("fsck-repair", lambda root: fsck(root, repair=True, confirm=True, at=AT)),
    ("gc", lambda root: gc(root, at=AT, confirm=True)),
]


@pytest.mark.parametrize("call", [pytest.param(call, id=name) for name, call in ENTRY_POINTS])
def test_regression_a_nul_in_a_root_path_is_refused_with_bad_path(
    tmp_path: Path, call: Callable[[Path], object]
) -> None:
    with pytest.raises(StorageError) as caught:
        call(tmp_path / NUL_NAME)
    assert caught.value.code == "BAD_PATH"
    assert "NUL" in caught.value.hint


@pytest.mark.parametrize("side", ["source", "target"])
def test_regression_a_nul_in_an_upgrade_path_is_refused_with_bad_path(
    tmp_path: Path, side: str
) -> None:
    source = tmp_path / "old"
    shutil.copytree(FIXTURES / LATEST / "repo", source)
    before = fingerprint(source)
    paths = {"source": source, "target": tmp_path / "new"}
    paths[side] = tmp_path / NUL_NAME
    with pytest.raises(StorageError) as caught:
        upgrade_repository(paths["source"], paths["target"])
    assert caught.value.code == "BAD_PATH"
    assert "NUL" in caught.value.hint
    assert fingerprint(source) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["old"]


@POSIX_ENCODING
@pytest.mark.parametrize("call", [pytest.param(call, id=name) for name, call in ENTRY_POINTS])
def test_regression_an_unencodable_root_path_is_refused_with_bad_path(
    tmp_path: Path, call: Callable[[Path], object]
) -> None:
    with pytest.raises(StorageError) as caught:
        call(tmp_path / UNPAIRED_NAME)
    assert caught.value.code == "BAD_PATH"
    assert "surrogate" in caught.value.hint


@POSIX_ENCODING
@pytest.mark.parametrize("side", ["source", "target"])
def test_regression_an_unencodable_upgrade_path_is_refused_with_bad_path(
    tmp_path: Path, side: str
) -> None:
    source = tmp_path / "old"
    shutil.copytree(FIXTURES / LATEST / "repo", source)
    before = fingerprint(source)
    paths = {"source": source, "target": tmp_path / "new"}
    paths[side] = tmp_path / UNPAIRED_NAME
    with pytest.raises(StorageError) as caught:
        upgrade_repository(paths["source"], paths["target"])
    assert caught.value.code == "BAD_PATH"
    assert fingerprint(source) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["old"]


@POSIX_ENCODING
def test_a_surrogate_escaped_byte_in_a_root_path_is_accepted(tmp_path: Path) -> None:
    require_usable_path(tmp_path / ESCAPED_BYTE_NAME)
    assert read_layout(tmp_path / ESCAPED_BYTE_NAME) == CURRENT_LAYOUT
