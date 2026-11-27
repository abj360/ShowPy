#!/usr/bin/env python3

"""
test_archive_export.py --- tests that export_archive writes a history as a repository holds it

Contains:
    test_export_holds_the_slots_records_and_reachable_blobs_as_stored: writes the manifest,
        every slot, every record and every blob a tree names, byte for byte as the file
        backend stores them, and nothing else
    test_equal_histories_give_equal_archive_bytes: gives the same bytes for one history held in
        a memory and in a file repository, and for two exports of one repository
    test_export_reads_each_blob_once: reads each reachable blob once, and no other
    test_export_of_an_unknown_show_raises_unknown_ref: names close show IDs
    test_regression_export_refuses_a_history_whose_ids_do_not_recompute: raises at export,
        naming the revision and the limit, instead of writing an archive import refuses
    test_regression_export_refuses_every_upgraded_release_fixture: refuses the show of each
        released fixture after its upgrade, whose ID and older-scheme IDs import refuses
    test_regression_export_refuses_a_tree_with_an_injected_unchanged_entry: refuses a record
        whose tree gained a path its revision did not change, which reads accept
    test_export_refuses_a_blob_whose_bytes_do_not_hash_to_its_digest: refuses a backend that
        returns other bytes than a tree names
    test_export_refuses_a_history_over_the_archive_caps: refuses a history with more entries
        than an archive holds
"""

import json
import shutil
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError, UnknownRefError
from e8s.show import archive_zip
from e8s.show.archive import export_archive
from e8s.show.backend import digest
from e8s.show.backend_checks import recomputes
from e8s.show.document_dump import dump_json
from e8s.show.file_repository import FileRepository
from e8s.show.history_models import Revision
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.show_folders import show_folder
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.archives import AT, SHOW_ID, entry_map, with_history
from tests.fixtures.upgrade_repos import FIXTURES

RELEASES = ("v0.1.1", "v0.2.0", "v0.3.0", "v0.4.0")


def test_export_holds_the_slots_records_and_reachable_blobs_as_stored(tmp_path: Path) -> None:
    repo = FileRepository(tmp_path)
    landed = with_history(repo, 5)
    folder = tmp_path / "shows" / show_folder(SHOW_ID)
    stored = {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and path.name != "show.json"
    }
    entries = entry_map(export_archive(repo, SHOW_ID))
    manifest = entries.pop("archive.json")
    assert json.loads(manifest) == {"format": "e8.show-archive", "show": SHOW_ID, "version": 1}
    assert entries == stored
    reachable = {d for r in landed for d in repo.backend.tree(SHOW_ID, r.id).values()}
    assert {name for name in entries if name.startswith("objects/")} == {
        f"objects/{d}" for d in reachable
    }


def test_equal_histories_give_equal_archive_bytes(tmp_path: Path) -> None:
    memory, files = InMemoryRepository(), FileRepository(tmp_path)
    assert with_history(memory, 7) == with_history(files, 7)
    archive = export_archive(memory, SHOW_ID)
    assert export_archive(files, SHOW_ID) == archive
    assert export_archive(FileRepository(tmp_path), SHOW_ID) == archive
    assert export_archive(memory, SHOW_ID) == archive


def test_export_reads_each_blob_once(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = InMemoryRepository()
    landed = with_history(repo, 6)
    reachable = {d for r in landed for d in repo.backend.tree(SHOW_ID, r.id).values()}
    read: list[str] = []
    real = repo.backend.blob

    def counted(show_id: str, blob: str) -> bytes:
        read.append(blob)
        return real(show_id, blob)

    monkeypatch.setattr(repo.backend, "blob", counted)
    export_archive(repo, SHOW_ID)
    assert sorted(read) == sorted(reachable)


def test_export_of_an_unknown_show_raises_unknown_ref() -> None:
    repo = InMemoryRepository()
    with_history(repo, 1)
    with pytest.raises(UnknownRefError, match="show_1"):
        export_archive(repo, "show_2")


def test_regression_export_refuses_a_history_whose_ids_do_not_recompute() -> None:
    repo = InMemoryRepository()
    landed = with_history(repo, 2)
    tree = repo.backend.tree(SHOW_ID, landed[-1].id)
    older = Revision(id="e" * 64, parent=landed[-1].id, at=AT, changed=("/renders/1.txt",))
    repo.backend.commit(
        SHOW_ID, older, {k: v for k, v in tree.items() if k != older.changed[0]}, {}
    )
    with pytest.raises(StorageError) as caught:
        export_archive(repo, SHOW_ID)
    assert caught.value.code == ErrorCode.STORAGE
    assert caught.value.context["revision"] == older.id
    assert caught.value.context["seq"] == len(landed) + 1
    assert "layout 1" in caught.value.hint


@pytest.mark.parametrize("release", RELEASES)
def test_regression_export_refuses_every_upgraded_release_fixture(
    tmp_path: Path, release: str
) -> None:
    source, target = tmp_path / "old", tmp_path / "new"
    shutil.copytree(FIXTURES / release / "repo", source)
    upgrade_repository(source, target)
    repo = FileRepository(target)
    for show_id in repo.shows():
        revisions = repo.revisions(show_id)
        assert not all(recomputes(r, repo.backend.tree(show_id, r.id)) for r in revisions)
        with pytest.raises(StorageError) as caught:
            export_archive(repo, show_id)
        assert caught.value.code == ErrorCode.BAD_PATH
        assert "cannot be archived" in caught.value.message


def test_regression_export_refuses_a_tree_with_an_injected_unchanged_entry(
    tmp_path: Path,
) -> None:
    repo = FileRepository(tmp_path)
    landed = with_history(repo, 3)
    record = tmp_path / "shows" / show_folder(SHOW_ID) / "records" / f"{landed[1].id}.json"
    stored = json.loads(record.read_bytes())
    stored["tree"]["/renders/injected.txt"] = digest(b"render 1")
    record.write_bytes(dump_json(stored))
    assert "/renders/injected.txt" in FileRepository(tmp_path).paths(SHOW_ID, landed[1].id)
    with pytest.raises(StorageError) as caught:
        export_archive(FileRepository(tmp_path), SHOW_ID)
    assert caught.value.code == ErrorCode.CORRUPT
    assert caught.value.context["entry"] == f"records/{landed[1].id}.json"


def test_export_refuses_a_blob_whose_bytes_do_not_hash_to_its_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = InMemoryRepository()
    with_history(repo, 2)
    monkeypatch.setattr(repo.backend, "blob", lambda _show, _digest: b"other bytes")
    with pytest.raises(StorageError) as caught:
        export_archive(repo, SHOW_ID)
    assert caught.value.code == ErrorCode.CORRUPT
    assert str(caught.value.context["entry"]).startswith("objects/")


def test_export_refuses_a_history_over_the_archive_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = InMemoryRepository()
    with_history(repo, 4)
    monkeypatch.setattr(archive_zip, "MAX_ARCHIVE_ENTRIES", 5)
    with pytest.raises(StorageError) as caught:
        export_archive(repo, SHOW_ID)
    assert caught.value.code == ErrorCode.TOO_LARGE
