#!/usr/bin/env python3

"""
test_upgrade.py --- tests upgrade_repository, which copies a layout-1 root into a new layout-2 root

Contains:
    test_layout1_root_is_read_only: reads every revision, tree and blob of the v0.4.0 fixture as
        v0.4.0 stored them, refuses every write with OLD_LAYOUT whose hint runs as given, and
        changes nothing under the root
    test_regression_every_released_fixture_upgrades_with_ids_kept: keeps every revision's ID,
        parent, time and tree and every revision's show for the v0.1.1 to v0.4.0 fixtures, after
        which fsck is clean and a write lands
    test_upgrade_never_touches_the_source: leaves the bytes, times and modes of every source file
        and folder as they were, whether blobs are linked or copied
    test_interrupted_upgrade_target_is_refused: leaves a target without layout.json that readers
        and a second upgrade refuse, and that upgrades once deleted
    test_upgrade_links_blobs_on_one_file_system: hard-links every blob from the source
    test_upgrade_copies_blobs_where_links_are_refused: copies every blob, verified, when the file
        system refuses a hard link
    test_upgrade_refuses_a_blob_that_does_not_match_its_digest: stops before the layout file,
        whether the blob is linked or copied
    test_upgrade_refuses_a_target_in_use_or_a_source_of_another_layout: refuses a target that is
        not empty, holds a repository, is a file or lies inside the source, and a source that holds
        no layout-1 repository
    test_upgrade_refuses_links_and_special_files_in_the_source: names a symlink or FIFO and
        writes nothing
    test_upgrade_leaves_out_what_no_revision_uses: reports temp files, housekeeping entries,
        unreachable blobs and stray entries with a reason, and copies none of them
    test_upgrade_keeps_the_read_caps: upgrades a show at the revision cap, refuses one past it,
        a history past the read budget and a blob past its size cap
    test_upgrade_is_deterministic: gives byte-identical targets and equal reports for one source
    test_regression_a_missing_blob_is_corrupt_naming_its_revision: raises CORRUPT naming the blob
        and the first revision whose tree names it, not IO, and writes no layout file
    test_upgrade_copies_blobs_another_user_owns: copies instead of linking a blob the current user
        does not own, whose shared inode a later commit could not freshen
    test_regression_a_failed_final_flush_says_the_target_must_not_be_used: names the target as
        unusable though it holds layout.json, and says to delete it and run again
    test_upgrade_flushes_every_folder_before_the_layout_file: flushes each show's folders,
        assets/, shows/, tmp/ and the root before layout.json is placed, then the root fully
"""

import errno
import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import e8s
from e8s.core.errors import StorageError, VersionError
from e8s.examples import two_set_show
from e8s.show import durable_io, file_backend, layout1, upgrade
from e8s.show.backend import digest
from e8s.show.commit_writes import FilePlacer
from e8s.show.document_repository import DocumentRepository
from e8s.show.file_backend import FileBackend
from e8s.show.file_repository import FileRepository
from e8s.show.fsck import fsck
from e8s.show.models import Show
from e8s.show.show_folders import show_folder
from e8s.show.show_io import load_show_at
from e8s.show.storage_models import ShowUpgrade, UpgradeReport
from e8s.show.upgrade import upgrade_repository
from e8s.show.workspace import open_workspace
from tests.fixtures.upgrade_repos import (
    AT,
    FIXTURES,
    LATEST,
    SHOW_ID,
    built,
    copied,
    expect_unfinished,
    fingerprint,
    refuse_links_from,
)

RELEASES = ("v0.1.1", "v0.2.0", "v0.3.0", "v0.4.0")
# One past the last revision whose ID does not recompute under the current scheme: v0.1.1 and
# v0.2.0 wrote every ID under the old one, v0.3.0 and v0.4.0 only their second revision.
LEGACY_BEFORE = {"v0.1.1": 4, "v0.2.0": 4, "v0.3.0": 3, "v0.4.0": 3}
LEGACY_SHOW = "legacy-show"
AFTER = "/renders/after.txt"
UPGRADE_CALL = re.compile(r"e8s\.upgrade_repository\(.*?\)\)")
OUTSIDE_DIGEST = "f" * 64
SKIP_LINKS = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege")
HAS_FIFOS = hasattr(os, "mkfifo") and sys.platform != "win32"


def _objects(root: Path, show_id: str = LEGACY_SHOW) -> Path:
    return root / show_id / "objects"


def _target_objects(target: Path, show_id: str = LEGACY_SHOW) -> Path:
    return target / "shows" / show_folder(show_id) / "objects"


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else b"<folder>"
        for path in sorted(root.rglob("*"))
    }


def _head_blob(source: Path) -> str:
    repo = FileRepository(source)
    return min(repo.backend.tree(LEGACY_SHOW, repo.head(LEGACY_SHOW)).values())


def _refused_writes(source: Path, head: str) -> list[Callable[[], object]]:
    repo = FileRepository(source)
    return [
        lambda: repo.transaction(LEGACY_SHOW, {AFTER: b"x"}, expected_revision=head, at=AT),
        lambda: repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT),
        lambda: repo.put_show(LEGACY_SHOW, two_set_show(), expected_revision=head, at=AT),
        lambda: DocumentRepository(FileBackend(source)).put_show(
            SHOW_ID, two_set_show(), expected_revision="", at=AT
        ),
    ]


def test_layout1_root_is_read_only(tmp_path: Path) -> None:
    source = copied(tmp_path)
    expected = json.loads((FIXTURES / LATEST / "expected.json").read_text())
    before = fingerprint(source)
    repo = FileRepository(source)
    assert repo.shows() == [LEGACY_SHOW]
    assert repo.head(LEGACY_SHOW) == expected["head"]
    revisions = repo.revisions(LEGACY_SHOW)
    assert [revision.id for revision in revisions] == expected["revisions"]
    for revision in revisions:
        oracle = Show.model_validate(expected["shows"][revision.id])
        assert repo.get_show(LEGACY_SHOW, revision.id) == oracle
        for blob in repo.backend.tree(LEGACY_SHOW, revision.id).values():
            assert digest(repo.backend.blob(LEGACY_SHOW, blob)) == blob
    for write in _refused_writes(source, expected["head"]):
        with pytest.raises(VersionError) as caught:
            write()
        assert caught.value.code == "OLD_LAYOUT"
        assert "upgrade_repository(Path(" in caught.value.hint
    assert fingerprint(source) == before
    call = UPGRADE_CALL.search(caught.value.hint)
    assert call is not None
    eval(call.group(0), {"e8s": e8s, "Path": Path})
    assert FileRepository(tmp_path / "old-layout2").head(LEGACY_SHOW) == expected["head"]
    assert fingerprint(source) == before


@pytest.mark.parametrize("release", RELEASES)
def test_regression_every_released_fixture_upgrades_with_ids_kept(
    tmp_path: Path, release: str
) -> None:
    source, target = copied(tmp_path, release), tmp_path / "new"
    expected = json.loads((FIXTURES / release / "expected.json").read_text())
    show_id = expected["show_id"]
    old = FileRepository(source)
    report = upgrade_repository(source, target)
    new = FileRepository(target)
    assert new.shows() == [show_id]
    history = old.revisions(show_id)
    assert new.revisions(show_id) == history
    assert [revision.id for revision in history] == expected["revisions"]
    old_ws, new_ws = open_workspace(old, show_id), open_workspace(new, show_id)
    blobs: set[str] = set()
    for revision in history:
        tree = old.backend.tree(show_id, revision.id)
        assert new.backend.tree(show_id, revision.id) == tree
        assert load_show_at(new_ws, revision.id) == load_show_at(old_ws, revision.id)
        blobs |= set(tree.values())
    folder = target / "shows" / show_folder(show_id)
    manifest = json.loads((folder / "show.json").read_bytes())
    legacy = [revision.id for revision in history][: LEGACY_BEFORE[release] - 1]
    assert manifest == {
        "id": show_id,
        "legacy_before": LEGACY_BEFORE[release],
        "legacy_count": len(legacy),
        "legacy_digest": hashlib.sha256(
            json.dumps(legacy, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    shown = report.shows[0]
    expected_show = ShowUpgrade(show=show_id, revisions=len(history), blobs_linked=len(blobs))
    assert shown.replace(dropped=()) == expected_show
    assert report.skipped == ()
    assert fsck(target).findings == ()
    landed = new.transaction(show_id, {AFTER: b"after"}, expected_revision=history[-1].id, at=AT)
    reopened = FileRepository(target)
    assert reopened.head(show_id) == landed.id
    assert reopened.read(show_id, AFTER) == b"after"
    assert fsck(target).findings == ()


@pytest.mark.parametrize("links_work", [True, False], ids=["linked", "copied"])
def test_upgrade_never_touches_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, links_work: bool
) -> None:
    source = copied(tmp_path)
    (source / LEGACY_SHOW / "trees" / ".tmp-left").write_bytes(b"crashed writer")
    (source / "notes.txt").write_text("mine")
    if not links_work:
        refuse_links_from(monkeypatch, source, errno.EXDEV)
    before = fingerprint(source)
    report = upgrade_repository(source, tmp_path / "new")
    assert fingerprint(source) == before
    linked = report.shows[0].blobs_linked
    assert (linked > 0) is links_work


def test_interrupted_upgrade_target_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    real = durable_io.link_file
    linked: list[Path] = []

    def fail_fourth(src: Path, dst: Path) -> bool | None:
        if len(linked) == 3:
            raise OSError(errno.EIO, os.strerror(errno.EIO), str(dst))
        linked.append(dst)
        return real(src, dst)

    monkeypatch.setattr(durable_io, "link_file", fail_fourth)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, target)
    assert caught.value.code == "IO"
    monkeypatch.undo()
    assert all(path.exists() for path in linked)
    expect_unfinished(target)
    with pytest.raises(StorageError) as again:
        upgrade_repository(source, target)
    assert again.value.code == "BAD_PATH"
    assert "delete it" in again.value.hint
    shutil.rmtree(target)
    upgrade_repository(source, target)
    assert fsck(target).findings == ()


def test_upgrade_links_blobs_on_one_file_system(tmp_path: Path) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    shown = upgrade_repository(source, target).shows[0]
    blobs = sorted(_target_objects(target).iterdir())
    assert (shown.blobs_linked, shown.blobs_copied) == (len(blobs), 0)
    assert blobs
    for blob in blobs:
        assert blob.samefile(_objects(source) / blob.name)


@pytest.mark.parametrize("code", [errno.EXDEV, errno.EPERM, errno.ENOTSUP])
def test_upgrade_copies_blobs_where_links_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    refused = refuse_links_from(monkeypatch, source, code)
    shown = upgrade_repository(source, target).shows[0]
    blobs = sorted(_target_objects(target).iterdir())
    assert len(refused) == 1
    assert (shown.blobs_linked, shown.blobs_copied) == (0, len(blobs))
    for blob in blobs:
        assert not blob.samefile(_objects(source) / blob.name)
        assert blob.read_bytes() == (_objects(source) / blob.name).read_bytes()
    assert list((target / "tmp").iterdir()) == []
    assert fsck(target).findings == ()
    old, new = FileRepository(source), FileRepository(target)
    assert new.get_show(LEGACY_SHOW) == old.get_show(LEGACY_SHOW)


@pytest.mark.parametrize("links_work", [True, False], ids=["linked", "copied"])
def test_upgrade_refuses_a_blob_that_does_not_match_its_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, links_work: bool
) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    damaged = _objects(source) / _head_blob(source)
    damaged.write_bytes(damaged.read_bytes() + b" ")
    if not links_work:
        refuse_links_from(monkeypatch, source, errno.EXDEV)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, target)
    assert caught.value.code == "CORRUPT"
    assert caught.value.context["file"] == damaged.name
    assert "do not match their digest" in caught.value.message
    expect_unfinished(target)
    assert list((target / "tmp").iterdir()) == []


def _in_use_targets(tmp_path: Path, source: Path) -> dict[str, Path]:
    """Maps the start of each refusal message to a target the upgrade must refuse."""
    full = tmp_path / "full"
    full.mkdir()
    (full / "notes.txt").write_text("mine")
    stored = tmp_path / "stored"
    FileRepository(stored).put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT)
    afile = tmp_path / "afile"
    afile.write_text("mine")
    return {
        "is not empty": full,
        "already holds a repository": stored,
        "is not a folder": afile,
        "is inside the source": source / "new",
    }


def test_upgrade_refuses_a_target_in_use_or_a_source_of_another_layout(tmp_path: Path) -> None:
    source = copied(tmp_path)
    before = fingerprint(source)
    for refusal, target in _in_use_targets(tmp_path, source).items():
        with pytest.raises(StorageError) as caught:
            upgrade_repository(source, target)
        assert caught.value.code == "BAD_PATH"
        assert refusal in caught.value.message
        assert "pass a folder that does not exist yet" in caught.value.hint
    assert fingerprint(source) == before
    layout_2 = tmp_path / "stored"
    for not_layout_1 in (layout_2, tmp_path / "missing"):
        with pytest.raises(StorageError) as caught:
            upgrade_repository(not_layout_1, tmp_path / "new")
        assert caught.value.code == "BAD_PATH"
        assert "written before v0.5.0" in caught.value.hint
    assert not (tmp_path / "new").exists()
    quiet = tmp_path / "quiet"
    quiet.mkdir()
    (quiet / ".DS_Store").write_bytes(b"finder")
    upgrade_repository(source, quiet)
    assert fsck(quiet).findings == ()


def _symlinked_blob(source: Path, outside: Path) -> str:
    (_objects(source) / OUTSIDE_DIGEST).symlink_to(outside)
    return f"{LEGACY_SHOW}/objects/{OUTSIDE_DIGEST}"


def _symlinked_root_entry(source: Path, outside: Path) -> str:
    (source / "elsewhere").symlink_to(outside.parent, target_is_directory=True)
    return "elsewhere"


def _symlinked_next(source: Path, outside: Path) -> str:
    (source / "other").mkdir()
    (source / "other" / "next").symlink_to(outside.parent, target_is_directory=True)
    return "other/next"


def _fifo_record(source: Path, _outside: Path) -> str:
    os.mkfifo(source / LEGACY_SHOW / "trees" / f"{'e' * 64}.json")
    return f"{LEGACY_SHOW}/trees/{'e' * 64}.json"


SPECIAL_ENTRIES = [
    pytest.param(_symlinked_blob, marks=SKIP_LINKS, id="symlinked-blob"),
    pytest.param(_symlinked_root_entry, marks=SKIP_LINKS, id="symlinked-root-entry"),
    pytest.param(_symlinked_next, marks=SKIP_LINKS, id="symlinked-next"),
    pytest.param(
        _fifo_record, marks=pytest.mark.skipif(not HAS_FIFOS, reason="no FIFOs"), id="fifo"
    ),
]


@pytest.mark.parametrize("make", SPECIAL_ENTRIES)
def test_upgrade_refuses_links_and_special_files_in_the_source(
    tmp_path: Path, make: Callable[[Path, Path], str]
) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    outside = tmp_path / "outside" / "file"
    outside.parent.mkdir()
    outside.write_bytes(b"not the repository's")
    relative = make(source, outside)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, target)
    assert caught.value.code == "CORRUPT"
    assert relative in caught.value.message
    assert caught.value.context["file"] == Path(relative).name
    assert "never follows a link" in caught.value.hint
    assert not target.exists()


def test_upgrade_leaves_out_what_no_revision_uses(tmp_path: Path) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    show = source / LEGACY_SHOW
    unused = hashlib.sha256(b"unused").hexdigest()
    (show / "objects" / unused).write_bytes(b"unused")
    (show / "objects" / ".DS_Store").write_bytes(b"finder")
    (show / "objects" / "nested").mkdir()
    (show / "trees" / ".tmp-crashed").write_bytes(b"half")
    (show / "notes.txt").write_text("mine")
    (source / "drafts").mkdir()
    (source / ".DS_Store").write_bytes(b"finder")
    report = upgrade_repository(source, target)
    assert report.skipped == (
        (".DS_Store", "entry the operating system adds to a folder"),
        ("drafts", "not a layout-1 show folder: it holds no next/root link"),
    )
    assert report.shows[0].skipped == tuple(
        sorted(
            [
                (f"{LEGACY_SHOW}/notes.txt", "not part of a layout-1 show folder"),
                (f"{LEGACY_SHOW}/objects/.DS_Store", "entry the operating system adds to a folder"),
                (
                    f"{LEGACY_SHOW}/objects/nested",
                    "folder inside a store folder, which holds only files",
                ),
                (f"{LEGACY_SHOW}/objects/{unused}", "blob no revision's tree names"),
                (f"{LEGACY_SHOW}/trees/.tmp-crashed", "temp file a layout-1 writer left behind"),
            ]
        )
    )
    assert not (_target_objects(target) / unused).exists()
    assert fsck(target).findings == ()


def test_upgrade_keeps_the_read_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cap = 3
    monkeypatch.setattr(layout1, "MAX_REVISIONS", cap)
    monkeypatch.setattr(file_backend, "MAX_REVISIONS", cap)
    built(tmp_path / "at_cap", cap)
    upgrade_repository(tmp_path / "at_cap", tmp_path / "at_cap_new")
    assert len(FileRepository(tmp_path / "at_cap_new").revisions(SHOW_ID)) == cap
    built(tmp_path / "past_cap", cap + 1)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(tmp_path / "past_cap", tmp_path / "past_cap_new")
    assert caught.value.code == "TOO_LARGE"
    expect_unfinished(tmp_path / "past_cap_new")
    monkeypatch.setattr(upgrade, "LOG_READ_MAX_BYTES", 100)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(tmp_path / "at_cap", tmp_path / "budget_new")
    assert caught.value.code == "TOO_LARGE"
    expect_unfinished(tmp_path / "budget_new")
    monkeypatch.setattr(upgrade, "LOG_READ_MAX_BYTES", file_backend.LOG_READ_MAX_BYTES)
    monkeypatch.setattr(upgrade, "BLOB_MAX_BYTES", 8)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(tmp_path / "at_cap", tmp_path / "blob_new")
    assert caught.value.code == "CORRUPT"
    assert "larger than 8 bytes" in caught.value.message
    expect_unfinished(tmp_path / "blob_new")


def test_upgrade_is_deterministic(tmp_path: Path) -> None:
    source = copied(tmp_path)
    (source / LEGACY_SHOW / "trees" / ".tmp-left").write_bytes(b"crashed writer")
    first = upgrade_repository(source, tmp_path / "first")
    second = upgrade_repository(source, tmp_path / "second")
    assert first == second
    assert isinstance(first, UpgradeReport)
    assert all(isinstance(show, ShowUpgrade) for show in first.shows)
    assert _files(tmp_path / "first") == _files(tmp_path / "second")


def test_upgrade_flushes_every_folder_before_the_layout_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    (source / "assets").mkdir()
    (source / "assets" / digest(b"audio")).write_bytes(b"audio")
    events: list[tuple[str, Path, bool]] = []
    real_flush, real_place = durable_io.flush_directory, FilePlacer.place

    def flush(path: Path, *, is_commit_point: bool = False, follows_links: bool = False) -> None:
        events.append(("flush", path, is_commit_point))
        real_flush(path, is_commit_point=is_commit_point, follows_links=follows_links)

    def place(placer: FilePlacer, show_id: str, path: Path, data: bytes) -> bool:
        events.append(("place", path, False))
        return real_place(placer, show_id, path, data)

    monkeypatch.setattr(durable_io, "flush_directory", flush)
    monkeypatch.setattr(FilePlacer, "place", place)
    upgrade_repository(source, target)
    placed = events.index(("place", target / "layout.json", False))
    before = {path for kind, path, _ in events[:placed] if kind == "flush"}
    show = target / "shows" / show_folder(LEGACY_SHOW)
    expected = {show, show / "log", show / "records", show / "objects"}
    expected |= {target / "assets", target / "shows", target / "tmp", target}
    assert expected <= before
    assert events[placed + 1 :] == [("flush", target, True)]


def test_regression_a_missing_blob_is_corrupt_naming_its_revision(tmp_path: Path) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    old = FileRepository(source)
    history = old.revisions(LEGACY_SHOW)
    missing = _head_blob(source)
    first = next(r.id for r in history if missing in old.backend.tree(LEGACY_SHOW, r.id).values())
    (_objects(source) / missing).unlink()
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, target)
    assert caught.value.code == "CORRUPT"
    assert caught.value.context["file"] == missing
    assert first in caught.value.message
    expect_unfinished(target)


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="no POSIX owners")
def test_upgrade_copies_blobs_another_user_owns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    owner = (_objects(source) / _head_blob(source)).stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: owner + 1)
    shown = upgrade_repository(source, target).shows[0]
    assert shown.blobs_linked == 0
    assert shown.blobs_copied == len(list(_target_objects(target).iterdir()))
    for blob in _target_objects(target).iterdir():
        assert not blob.samefile(_objects(source) / blob.name)


def test_regression_a_failed_final_flush_says_the_target_must_not_be_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = copied(tmp_path), tmp_path / "new"
    real_flush = durable_io.flush_directory

    def flush(path: Path, *, is_commit_point: bool = False, follows_links: bool = False) -> None:
        if is_commit_point:
            raise OSError(errno.EIO, os.strerror(errno.EIO), str(path))
        real_flush(path, is_commit_point=is_commit_point, follows_links=follows_links)

    monkeypatch.setattr(durable_io, "flush_directory", flush)
    with pytest.raises(StorageError) as caught:
        upgrade_repository(source, target)
    assert caught.value.code == "IO"
    assert "must not be used" in caught.value.message
    assert "delete the target" in caught.value.hint
    assert (target / "layout.json").exists()
