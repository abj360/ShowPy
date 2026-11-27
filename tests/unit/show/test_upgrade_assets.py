#!/usr/bin/env python3

"""
test_upgrade_assets.py --- tests that an upgrade carries the asset store of a layout-1 root

Contains:
    test_upgrade_carries_the_assets_of_a_layout_1_root: links or copies every asset, verified,
        leaves out what is no asset with a reason, and the new root serves each asset
    test_upgrade_refuses_an_asset_that_does_not_match_its_name: stops before the layout file
"""

import errno
from pathlib import Path

import pytest

from e8s.core.errors import StorageError
from e8s.core.util import asset_uri
from e8s.show.assets import FileAssetStore
from e8s.show.backend import digest
from e8s.show.fsck import fsck
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.upgrade_repos import copied, expect_unfinished, refuse_links_from

AUDIO = b"audio"
LEFT_BY_A_WRITER = "temp file a layout-1 writer left behind"


def _with_assets(tmp_path: Path) -> Path:
    source = copied(tmp_path)
    assets = source / "assets"
    assets.mkdir()
    (assets / digest(AUDIO)).write_bytes(AUDIO)
    (assets / ".tmp-crashed").write_bytes(b"half")
    return source


@pytest.mark.parametrize("links_work", [True, False], ids=["linked", "copied"])
def test_upgrade_carries_the_assets_of_a_layout_1_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, links_work: bool
) -> None:
    source = _with_assets(tmp_path)
    if not links_work:
        refuse_links_from(monkeypatch, source, errno.EXDEV)
    report = upgrade_repository(source, tmp_path / "new")
    assert (report.assets_linked, report.assets_copied) == ((1, 0) if links_work else (0, 1))
    assert ("assets/.tmp-crashed", LEFT_BY_A_WRITER) in report.skipped
    assert not any(path == "assets" for path, _ in report.skipped)
    assert FileAssetStore(tmp_path / "new").get(asset_uri(AUDIO)) == AUDIO
    assert not (tmp_path / "new" / "assets" / ".tmp-crashed").exists()
    assert fsck(tmp_path / "new").findings == ()


def test_upgrade_refuses_an_asset_that_does_not_match_its_name(tmp_path: Path) -> None:
    source = _with_assets(tmp_path)
    (source / "assets" / digest(b"video")).write_bytes(b"not the video")
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, tmp_path / "new")
    assert caught.value.code == "CORRUPT"
    assert "do not match their digest" in caught.value.message
    expect_unfinished(tmp_path / "new")
