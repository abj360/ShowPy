#!/usr/bin/env python3

"""
test_commit_writes.py --- tests how a layout-2 commit places its files, with and without hard links

Contains:
    test_commit_lands_without_hard_links: stores and reads back a show where os.link fails with
        EPERM, trying a link only for the probe and leaving no temp file
    test_one_winner_without_hard_links: lands exactly one of eight racing threads in each of 200
        trials without hard links, every loser naming the winner
    test_hard_links_are_probed_once_per_backend: probes on a backend's first commit only, with
        and without hard links
    test_failed_probe_fails_closed_and_is_tried_again: fails a commit whose probe link fails for
        another reason with IO, and probes again on the next commit
    test_exclusive_create_never_replaces_a_file: keeps an existing file and the target of an
        existing symlink untouched
    test_lost_slot_race_is_the_same_conflict_without_hard_links: gives the loser the same
        ConflictError as with links and leaves the winner's slot as it was
    test_flush_order_is_the_same_without_hard_links: flushes each file before the next write and
        the record and blob folders before the slot, then the slot's folder fully, in both paths
    test_first_commit_flushes_its_folders_before_the_slot_without_hard_links: flushes the root,
        shows/ and the show folder before the first slot in both paths
    test_regression_failed_flush_keeps_a_visible_file: keeps a blob another commit landed on and a
        slot a reader saw when their flush fails, so nothing goes missing and no gap opens
"""

import errno
import os
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.canonical import jcs
from e8s.core.errors import ConflictError, E8sError, StorageError
from e8s.examples import two_set_show
from e8s.show import durable_io
from e8s.show.backend import digest
from e8s.show.document_repository import DocumentRepository, revision_id
from e8s.show.file_backend import SLOT_DIGITS, FileBackend
from e8s.show.history_models import Revision
from e8s.show.layout import read_layout
from e8s.show.show_folders import show_folder

AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SHOW_ID = "unlinked"
RENDER = "/renders/a.txt"
SHARED = "/renders/shared.txt"
RACERS = 8
RACE_TRIALS = 200
BARRIER_TIMEOUT_S = 60.0
JOIN_TIMEOUT_S = 300.0
COMMITS = 3
PLACE = "place"
FLUSH = "flush"
FOLDER = "dir"
COMMIT_POINT = "commit point"


def _change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    data = f"render {n}".encode()
    revision = Revision(
        id=revision_id(parent, AT, {RENDER: digest(data)}), parent=parent, at=AT, changed=(RENDER,)
    )
    return revision, {RENDER: digest(data)}, {digest(data): data}


def _landed(backend: FileBackend, count: int, parent: str = "", start: int = 0) -> list[Revision]:
    landed: list[Revision] = []
    for n in range(start, start + count):
        revision, tree, blobs = _change(parent, n)
        backend.commit(SHOW_ID, revision, tree, blobs)
        landed.append(revision)
        parent = revision.id
    return landed


def _slot(root: Path, seq: int) -> Path:
    return root / "shows" / show_folder(SHOW_ID) / "log" / f"{seq:0{SLOT_DIGITS}d}"


def _without_hard_links(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Makes os.link fail with EPERM, as on exFAT, recording the name each link tried."""
    tried: list[Path] = []

    def refuse(_source: str | Path, target: str | Path, **_kwargs: object) -> None:
        tried.append(Path(target))
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse)
    return tried


def _record_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Records each placement by folder name, each file flush and each folder flush, in order."""
    calls: list[tuple[str, ...]] = []
    real_link_new, real_create_new = durable_io.link_new, durable_io.create_new
    real_flush_file, real_flush_directory = durable_io.flush_file, durable_io.flush_directory

    def link_new(source: Path, target: Path) -> bool:
        calls.append((PLACE, target.parent.name))
        return real_link_new(source, target)

    def create_new(target: Path, data: bytes) -> bool:
        calls.append((PLACE, target.parent.name))
        return real_create_new(target, data)

    def flush_file(descriptor: int) -> None:
        calls.append((FLUSH,))
        real_flush_file(descriptor)

    def flush_directory(
        path: Path, *, is_commit_point: bool = False, follows_links: bool = False
    ) -> None:
        calls.append((FOLDER, path.name, COMMIT_POINT if is_commit_point else ""))
        real_flush_directory(path, is_commit_point=is_commit_point, follows_links=follows_links)

    monkeypatch.setattr(durable_io, "link_new", link_new)
    monkeypatch.setattr(durable_io, "create_new", create_new)
    monkeypatch.setattr(durable_io, "flush_file", flush_file)
    monkeypatch.setattr(durable_io, "flush_directory", flush_directory)
    return calls


def test_commit_lands_without_hard_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tried = _without_hard_links(monkeypatch)
    repo = DocumentRepository(FileBackend(tmp_path))
    first = repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT)
    second = repo.transaction(SHOW_ID, {RENDER: b"render"}, expected_revision=first.id, at=AT)
    reopened = DocumentRepository(FileBackend(tmp_path))
    assert [revision.id for revision in reopened.revisions(SHOW_ID)] == [first.id, second.id]
    assert reopened.get_show(SHOW_ID).replace(revision="") == two_set_show()
    assert reopened.read(SHOW_ID, RENDER) == b"render"
    assert _slot(tmp_path, 2).read_bytes() == jcs(second.to_json()) + b"\n"
    assert read_layout(tmp_path) == 2
    assert list((tmp_path / "tmp").iterdir()) == []
    assert [path.parent.name for path in tried] == ["tmp"]


def _race(root: Path, parent: str) -> list[tuple[bool, str]]:
    """Races RACERS threads to land one commit each on `parent`, returning each outcome."""
    barrier = threading.Barrier(RACERS, timeout=BARRIER_TIMEOUT_S)
    outcomes: list[tuple[bool, str]] = []

    def attempt(racer: int) -> None:
        repo = DocumentRepository(FileBackend(root))
        changes = {f"/renders/{racer}.txt": f"racer {racer}".encode(), SHARED: b"shared"}
        barrier.wait()
        try:
            landed = repo.transaction(SHOW_ID, changes, expected_revision=parent, at=AT)
        except ConflictError as error:
            outcomes.append((False, str(error.context["head"])))
        except E8sError as error:
            outcomes.append((False, f"{error.code}: {error.message}"))
        else:
            outcomes.append((True, landed.id))

    threads = [threading.Thread(target=attempt, args=(racer,)) for racer in range(RACERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT_S)
    assert not any(thread.is_alive() for thread in threads)
    return outcomes


@pytest.mark.slow
def test_one_winner_without_hard_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _without_hard_links(monkeypatch)
    for trial in range(RACE_TRIALS):
        root = tmp_path / str(trial)
        parent = ""
        if trial % 2:
            seeded = DocumentRepository(FileBackend(root))
            parent = seeded.transaction(SHOW_ID, {SHARED: b"seed"}, expected_revision="", at=AT).id
        outcomes = _race(root, parent)
        winners = [named for has_landed, named in outcomes if has_landed]
        assert len(outcomes) == RACERS, trial
        assert len(winners) == 1, (trial, outcomes)
        assert all(named == winners[0] for _, named in outcomes), (trial, outcomes)
        history = [revision.id for revision in FileBackend(root).log(SHOW_ID)]
        assert history == ([parent, winners[0]] if parent else [winners[0]]), trial


def _probes(calls: list[tuple[str, ...]]) -> int:
    return calls.count((PLACE, "tmp"))


def test_hard_links_are_probed_once_per_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_writes(monkeypatch)
    linked = FileBackend(tmp_path / "linked")
    landed = _landed(linked, COMMITS)
    assert _probes(calls) == 1
    _landed(FileBackend(tmp_path / "linked"), 1, landed[-1].id, COMMITS)
    assert _probes(calls) == 2
    tried = _without_hard_links(monkeypatch)
    unlinked = FileBackend(tmp_path / "unlinked")
    landed = _landed(unlinked, COMMITS)
    assert len(tried) == 1
    _landed(FileBackend(tmp_path / "unlinked"), 1, landed[-1].id, COMMITS)
    assert len(tried) == 2
    assert all(path.parent.name == "tmp" for path in tried)


def test_failed_probe_fails_closed_and_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny(_source: str | Path, target: str | Path, **_kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "Permission denied", str(target))

    backend = FileBackend(tmp_path)
    revision, tree, blobs = _change("", 0)
    with monkeypatch.context() as patched:
        patched.setattr(os, "link", deny)
        with pytest.raises(StorageError) as caught:
            backend.commit(SHOW_ID, revision, tree, blobs)
    assert caught.value.code == "IO"
    assert caught.value.context["errno"] == errno.EACCES
    assert list((tmp_path / "tmp").iterdir()) == []
    assert not (tmp_path / "layout.json").exists()
    calls = _record_writes(monkeypatch)
    backend.commit(SHOW_ID, revision, tree, blobs)
    assert calls[:2] == [(FLUSH,), (PLACE, "tmp")]
    assert backend.head(SHOW_ID) == revision.id


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs privilege")
def test_exclusive_create_never_replaces_a_file(tmp_path: Path) -> None:
    existing, aimed, linked = tmp_path / "existing", tmp_path / "aimed", tmp_path / "linked"
    existing.write_bytes(b"old")
    aimed.write_bytes(b"target")
    linked.symlink_to(aimed)
    assert durable_io.create_new(existing, b"new") is False
    assert durable_io.create_new(linked, b"new") is False
    assert existing.read_bytes() == b"old"
    assert aimed.read_bytes() == b"target"
    assert linked.is_symlink()


def _lost_race(root: Path) -> tuple[ConflictError, bytes, bytes]:
    """Lands slot 2 from one backend, then commits on the same parent from a warm second one."""
    writer, loser = FileBackend(root), FileBackend(root)
    [first] = _landed(writer, 1)
    assert loser.head(SHOW_ID) == first.id
    _landed(writer, 1, first.id, 1)
    slot = _slot(root, 2).read_bytes()
    revision, tree, blobs = _change(first.id, 2)
    with pytest.raises(ConflictError) as caught:
        loser.commit(SHOW_ID, revision, tree, blobs)
    return caught.value, slot, _slot(root, 2).read_bytes()


def test_lost_slot_race_is_the_same_conflict_without_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked, linked_before, linked_after = _lost_race(tmp_path / "linked")
    _without_hard_links(monkeypatch)
    unlinked, unlinked_before, unlinked_after = _lost_race(tmp_path / "unlinked")
    assert unlinked_after == unlinked_before == linked_before == linked_after
    assert (type(unlinked), unlinked.message, unlinked.hint, unlinked.context) == (
        type(linked),
        linked.message,
        linked.hint,
        linked.context,
    )
    records = tmp_path / "unlinked" / "shows" / show_folder(SHOW_ID) / "records"
    assert len(list(records.iterdir())) == 2


def _second_commit_writes(root: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    backend = FileBackend(root)
    [first] = _landed(backend, 1)
    with monkeypatch.context() as patched:
        calls = _record_writes(patched)
        _landed(backend, 1, first.id, 1)
    return calls


def _without_file_flushes(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [call for call in calls if call != (FLUSH,)]


def test_flush_order_is_the_same_without_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = _second_commit_writes(tmp_path / "linked", monkeypatch)
    _without_hard_links(monkeypatch)
    unlinked = _second_commit_writes(tmp_path / "unlinked", monkeypatch)
    assert unlinked == [
        (PLACE, "objects"),
        (FLUSH,),
        (PLACE, "records"),
        (FLUSH,),
        (FOLDER, "objects", ""),
        (FOLDER, "records", ""),
        (PLACE, "log"),
        (FLUSH,),
        (FOLDER, "log", COMMIT_POINT),
    ]
    assert _without_file_flushes(linked) == _without_file_flushes(unlinked)


def _first_commit_writes(root: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    with monkeypatch.context() as patched:
        calls = _record_writes(patched)
        _landed(FileBackend(root), 1)
    return calls


def _folders_before_the_slot(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [call for call in calls[: calls.index((PLACE, "log"))] if call[0] == FOLDER]


def _chain(root: str) -> list[tuple[str, ...]]:
    return [
        (FOLDER, root, ""),
        (FOLDER, "shows", ""),
        (FOLDER, show_folder(SHOW_ID), ""),
        (FOLDER, "objects", ""),
        (FOLDER, "records", ""),
    ]


def test_first_commit_flushes_its_folders_before_the_slot_without_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = _first_commit_writes(tmp_path / "linked", monkeypatch)
    _without_hard_links(monkeypatch)
    unlinked = _first_commit_writes(tmp_path / "unlinked", monkeypatch)
    assert _folders_before_the_slot(linked) == _chain("linked")
    assert _folders_before_the_slot(unlinked) == _chain("unlinked")
    assert unlinked[-3:] == [(PLACE, "log"), (FLUSH,), (FOLDER, "log", COMMIT_POINT)]


def _fail_flush_while_visible(
    monkeypatch: pytest.MonkeyPatch, path: Path, while_visible: Callable[[], object]
) -> None:
    """Fails the flush of `path` once, after `while_visible` ran with every byte of it in place."""
    real_flush_file = durable_io.flush_file

    def flaky_flush(descriptor: int) -> None:
        if path.exists() and os.path.samestat(os.fstat(descriptor), path.stat()):
            monkeypatch.setattr(durable_io, "flush_file", real_flush_file)
            while_visible()
            raise OSError(errno.EIO, "Input/output error")
        real_flush_file(descriptor)

    monkeypatch.setattr(durable_io, "flush_file", flaky_flush)


def _shared_blob_lands_twice(root: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Lands B on a blob A created in place while A's flush of that blob fails."""
    writer, other = FileBackend(root), FileBackend(root)
    [base] = _landed(writer, 1)
    shared = b"shared bytes"
    changes = {}
    for name in ("a", "b"):
        path = f"/renders/{name}.txt"
        changes[name] = (
            Revision(
                id=revision_id(base.id, AT, {path: digest(shared)}),
                parent=base.id,
                at=AT,
                changed=(path,),
            ),
            {path: digest(shared)},
            {digest(shared): shared},
        )
    blob = root / "shows" / show_folder(SHOW_ID) / "objects" / digest(shared)
    _fail_flush_while_visible(monkeypatch, blob, lambda: other.commit(SHOW_ID, *changes["b"]))
    with pytest.raises(StorageError) as caught:
        writer.commit(SHOW_ID, *changes["a"])
    assert caught.value.code == "IO"
    reader = FileBackend(root)
    landed = changes["b"][0]
    assert reader.head(SHOW_ID) == landed.id
    assert reader.blob(SHOW_ID, digest(shared)) == shared
    return [revision.id for revision in reader.log(SHOW_ID)]


def _seen_slot_stays(root: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Lets a warm backend see slot 2 while its flush fails, then commit a child on it."""
    writer, warm = FileBackend(root), FileBackend(root)
    [base] = _landed(writer, 1)
    revision, tree, blobs = _change(base.id, 1)
    seen: list[str] = []
    _fail_flush_while_visible(monkeypatch, _slot(root, 2), lambda: seen.append(warm.head(SHOW_ID)))
    with pytest.raises(StorageError) as caught:
        writer.commit(SHOW_ID, revision, tree, blobs)
    assert caught.value.code == "IO"
    assert seen == [revision.id]
    _landed(warm, 1, revision.id, 2)
    return [landed.id for landed in FileBackend(root).log(SHOW_ID)][:2]


VISIBLE_FILES: dict[str, Callable[[Path, pytest.MonkeyPatch], list[str]]] = {
    "shared blob": _shared_blob_lands_twice,
    "slot a reader saw": _seen_slot_stays,
}


@pytest.mark.parametrize("case", sorted(VISIBLE_FILES))
def test_regression_failed_flush_keeps_a_visible_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Keeps a file whose bytes were all written when its flush fails: a reader may rely on it."""
    _without_hard_links(monkeypatch)
    history = VISIBLE_FILES[case](tmp_path, monkeypatch)
    assert len(history) == 2
