#!/usr/bin/env python3

"""
test_shared_root.py --- tests that a file repository and a file asset store share one root

Contains:
    test_regression_an_asset_put_before_the_first_show_leaves_a_layout_2_root: opens, commits
        to and checks a root whose first write was an asset
    test_an_asset_put_after_the_first_show_keeps_the_repository_sound: opens and checks a root
        whose asset came after its first commit
    test_a_layout_1_root_with_assets_stays_layout_1: reads a layout-1 root that holds assets as
        layout 1, which refuses new assets and commits
    test_a_root_with_assets_and_an_unknown_entry_is_corrupt: fails closed on anything else
    test_a_file_named_assets_is_corrupt: fails closed when `assets` is no folder
    test_an_upgrade_refuses_a_target_that_holds_assets: refuses an assets-only target, which
        is not empty
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError, VersionError
from e8s.examples import two_set_show
from e8s.show.assets import FileAssetStore
from e8s.show.file_repository import FileRepository
from e8s.show.fsck import fsck
from e8s.show.gc import gc
from e8s.show.layout import CURRENT_LAYOUT, LEGACY_LAYOUT, read_layout
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.upgrade_repos import FIXTURES, LATEST

AT = datetime(2026, 11, 27, tzinfo=UTC)
SHOW_ID = "show_1"


def _commit(root: Path) -> str:
    repo = FileRepository(root)
    return repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT).id


def test_regression_an_asset_put_before_the_first_show_leaves_a_layout_2_root(
    tmp_path: Path,
) -> None:
    uri = FileAssetStore(tmp_path).put(b"audio")
    assert read_layout(tmp_path) == CURRENT_LAYOUT
    assert fsck(tmp_path).ok
    assert gc(tmp_path, at=AT).removed == ()
    head = _commit(tmp_path)
    assert FileRepository(tmp_path).head(SHOW_ID) == head
    assert (tmp_path / "layout.json").is_file()
    assert fsck(tmp_path).ok
    assert FileAssetStore(tmp_path).get(uri) == b"audio"


def test_an_asset_put_after_the_first_show_keeps_the_repository_sound(tmp_path: Path) -> None:
    head = _commit(tmp_path)
    uri = FileAssetStore(tmp_path).put(b"audio")
    assert FileRepository(tmp_path).head(SHOW_ID) == head
    assert fsck(tmp_path).ok
    assert gc(tmp_path, at=AT, confirm=True).removed == ()
    assert FileAssetStore(tmp_path).get(uri) == b"audio"


def test_a_layout_1_root_with_assets_stays_layout_1(tmp_path: Path) -> None:
    root = tmp_path / "old"
    shutil.copytree(FIXTURES / LATEST / "repo", root)
    (root / "assets").mkdir(exist_ok=True)
    assert read_layout(root) == LEGACY_LAYOUT
    with pytest.raises(VersionError) as caught:
        FileAssetStore(root).put(b"audio")
    assert caught.value.code == ErrorCode.OLD_LAYOUT
    with pytest.raises(VersionError):
        _commit(root)


def test_a_root_with_assets_and_an_unknown_entry_is_corrupt(tmp_path: Path) -> None:
    FileAssetStore(tmp_path).put(b"audio")
    (tmp_path / "notes.txt").write_text("mine")
    with pytest.raises(StorageError) as caught:
        read_layout(tmp_path)
    assert caught.value.code == ErrorCode.CORRUPT


def test_a_file_named_assets_is_corrupt(tmp_path: Path) -> None:
    (tmp_path / "assets").write_text("not a folder")
    with pytest.raises(StorageError) as caught:
        read_layout(tmp_path)
    assert caught.value.code == ErrorCode.CORRUPT


def test_an_upgrade_refuses_a_target_that_holds_assets(tmp_path: Path) -> None:
    source = tmp_path / "old"
    shutil.copytree(FIXTURES / LATEST / "repo", source)
    target = tmp_path / "new"
    FileAssetStore(target).put(b"audio")
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, target)
    assert caught.value.code == ErrorCode.BAD_PATH
    assert "not empty" in caught.value.message
