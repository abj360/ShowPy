#!/usr/bin/env python3

"""
test_upgrade_writers.py --- tests that an upgrade refuses a source a writer changed while it ran

Contains:
    test_regression_a_source_that_changed_during_the_upgrade_is_refused: raises CONFLICT and
        writes no layout file when a revision, a show or an asset lands on the source mid-upgrade
"""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from e8s.core.errors import StorageError
from e8s.show import upgrade
from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.history_models import Revision
from e8s.show.layout1 import Layout1Reader
from e8s.show.storage_models import ShowUpgrade
from e8s.show.upgrade import upgrade_repository
from e8s.show.upgrade_source import SourceShow
from tests.fixtures.layout1 import append_layout1
from tests.fixtures.upgrade_repos import AT, copied, expect_unfinished

LEGACY_SHOW = "legacy-show"
AFTER = "/renders/after.txt"


def _commit(source: Path) -> None:
    reader = Layout1Reader(source)
    head = reader.head(LEGACY_SHOW)
    data = b"after"
    tree = {**reader.tree(LEGACY_SHOW, head), AFTER: digest(data)}
    child_id = revision_id(head, AT, {AFTER: digest(data)})
    child = Revision(id=child_id, parent=head, at=AT, changed=(AFTER,))
    append_layout1(source, LEGACY_SHOW, child, tree, {digest(data): data})


def _new_show(source: Path) -> None:
    shutil.copytree(source / LEGACY_SHOW, source / "another")


def _new_asset(source: Path) -> None:
    (source / "assets").mkdir()
    (source / "assets" / digest(b"audio")).write_bytes(b"audio")


WRITERS: dict[str, Callable[[Path], None]] = {
    "commit": _commit,
    "show": _new_show,
    "asset": _new_asset,
}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_regression_a_source_that_changed_during_the_upgrade_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    real = upgrade._upgrade_show

    def then_write(
        reader: Layout1Reader, writer: upgrade._Target, listed: SourceShow
    ) -> ShowUpgrade:
        upgraded = real(reader, writer, listed)
        WRITERS[name](source)
        return upgraded

    monkeypatch.setattr(upgrade, "_upgrade_show", then_write)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, target)
    assert caught.value.code == "CONFLICT"
    assert "stop every writer" in caught.value.hint
    expect_unfinished(target)
