#!/usr/bin/env python3

"""
test_assets.py --- tests the file-backed asset store

Contains:
    test_file_asset_store_round_trips_and_dedupes: equal bytes give one URI and one file
    test_second_instance_dedupes: a second store on the same root keeps the first file
    test_regression_tampered_asset_is_corrupt: changed bytes raise CORRUPT naming the URI
    test_unknown_asset_names_put: a missing asset raises UnknownRefError naming put()
    test_non_asset_uri_is_schema_error: a foreign URI raises SchemaError
    test_malformed_hash_cannot_escape_root: a traversal URI is unknown, never read
    test_put_leaves_no_temp_file: the temp folder holds nothing after a put
    test_symlinked_asset_is_refused: a symlink named like an asset raises CORRUPT
    test_put_falls_back_without_hard_links: EPERM from os.link uses an exclusive create
    test_put_maps_os_errors_to_storage_error: an OS failure becomes StorageError with IO
    test_put_refuses_oversized_asset: bytes over the cap raise TOO_LARGE
    test_regression_failed_in_place_write_leaves_no_torn_file: a failed write is removed
    test_regression_put_refuses_a_layout_1_root: raises OLD_LAYOUT and creates nothing under a
        layout-1 root, whose stored assets stay readable
"""

import errno
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError, UnknownRefError, VersionError
from e8s.core.util import asset_uri
from e8s.show import assets
from e8s.show.assets import FileAssetStore
from e8s.show.integrity import blob_digest
from tests.fixtures.layout1 import stored_layout1

LAYOUT_1_AT = datetime(2026, 11, 27, tzinfo=UTC)


def test_file_asset_store_round_trips_and_dedupes(tmp_path: Path) -> None:
    store = FileAssetStore(tmp_path)
    first = store.put(b"audio bytes")
    assert first == asset_uri(b"audio bytes")
    assert store.put(b"audio bytes") == first
    assert store.get(first) == b"audio bytes"
    assert [p.name for p in (tmp_path / "assets").iterdir()] == [blob_digest(b"audio bytes")]


def test_second_instance_dedupes(tmp_path: Path) -> None:
    uri = FileAssetStore(tmp_path).put(b"same")
    path = tmp_path / "assets" / blob_digest(b"same")
    inode = path.stat().st_ino
    assert FileAssetStore(tmp_path).put(b"same") == uri
    assert path.stat().st_ino == inode
    assert FileAssetStore(tmp_path).get(uri) == b"same"


def test_regression_tampered_asset_is_corrupt(tmp_path: Path) -> None:
    store = FileAssetStore(tmp_path)
    uri = store.put(b"original")
    (tmp_path / "assets" / blob_digest(b"original")).write_bytes(b"tampered")
    with pytest.raises(StorageError) as raised:
        store.get(uri)
    assert raised.value.code == ErrorCode.CORRUPT
    assert uri in str(raised.value)
    assert blob_digest(b"original") in str(raised.value)


def test_unknown_asset_names_put(tmp_path: Path) -> None:
    with pytest.raises(UnknownRefError) as raised:
        FileAssetStore(tmp_path).get(asset_uri(b"never stored"))
    assert "put()" in (raised.value.hint or "")


def test_non_asset_uri_is_schema_error(tmp_path: Path) -> None:
    with pytest.raises(SchemaError):
        FileAssetStore(tmp_path).get("https://example.com/a")


def test_malformed_hash_cannot_escape_root(tmp_path: Path) -> None:
    (tmp_path / "secret").write_bytes(b"x")
    with pytest.raises(UnknownRefError):
        FileAssetStore(tmp_path / "root").get("asset:sha256:../../secret")


def test_put_leaves_no_temp_file(tmp_path: Path) -> None:
    store = FileAssetStore(tmp_path)
    store.put(b"one")
    store.put(b"one")
    assert list((tmp_path / "tmp").iterdir()) == []


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_symlinked_asset_is_refused(tmp_path: Path) -> None:
    store = FileAssetStore(tmp_path)
    uri = store.put(b"real")
    path = tmp_path / "assets" / blob_digest(b"real")
    target = tmp_path / "elsewhere"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(StorageError) as raised:
        store.get(uri)
    assert raised.value.code == ErrorCode.CORRUPT


def test_put_falls_back_without_hard_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "no links")

    monkeypatch.setattr(os, "link", refuse)
    store = FileAssetStore(tmp_path)
    uri = store.put(b"no links here")
    assert store.get(uri) == b"no links here"
    assert store.put(b"no links here") == uri
    assert list((tmp_path / "tmp").iterdir()) == []


def test_put_maps_os_errors_to_storage_error(tmp_path: Path) -> None:
    (tmp_path / "assets").write_bytes(b"a file where the folder belongs")
    with pytest.raises(StorageError) as raised:
        FileAssetStore(tmp_path).put(b"x")
    assert raised.value.code == ErrorCode.IO


def test_put_refuses_oversized_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assets, "BLOB_MAX_BYTES", 4)
    with pytest.raises(StorageError) as raised:
        FileAssetStore(tmp_path).put(b"12345")
    assert raised.value.code == ErrorCode.TOO_LARGE
    assert not (tmp_path / "assets").exists()


def test_regression_failed_in_place_write_leaves_no_torn_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_link(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "no links")

    real_fsync = os.fsync
    failures = {"left": 1}

    def failing_fsync(descriptor: int) -> None:
        if failures["left"] and not (tmp_path / "assets" / blob_digest(b"torn")).exists():
            real_fsync(descriptor)
            return
        if failures["left"]:
            failures["left"] -= 1
            raise OSError(errno.ENOSPC, "disk full")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "link", refuse_link)
    store = FileAssetStore(tmp_path)
    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(StorageError) as raised:
        store.put(b"torn")
    assert raised.value.code == ErrorCode.IO
    assert not (tmp_path / "assets" / blob_digest(b"torn")).exists()
    uri = store.put(b"torn")
    assert store.get(uri) == b"torn"


def test_regression_put_refuses_a_layout_1_root(tmp_path: Path) -> None:
    """A layout-1 root is read-only: storing an asset there would be outside every upgrade."""
    stored_layout1(tmp_path, "old", {"/renders/a.txt": b"x"}, at=LAYOUT_1_AT)
    before = sorted(path.name for path in tmp_path.iterdir())
    with pytest.raises(VersionError) as caught:
        FileAssetStore(tmp_path).put(b"audio")
    assert caught.value.code == ErrorCode.OLD_LAYOUT
    assert "upgrade_repository(" in caught.value.hint
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / blob_digest(b"audio")).write_bytes(b"audio")
    assert FileAssetStore(tmp_path).get(asset_uri(b"audio")) == b"audio"
