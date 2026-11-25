#!/usr/bin/env python3

"""
test_file_backend.py --- tests the layout-2 file backend: slots, records, manifests and head

Contains:
    test_commit_writes_the_layout_2_files: lands the layout file, manifest, slot, record and blob
        where layout 2 puts them, leaving no temp file
    test_warm_head_probes_one_slot: looks up only the slot after the remembered one
    test_cold_head_needs_at_most_22_lookups_at_1000_revisions: gallops and bisects to the head of
        1,000 revisions within 2 log2(1000) + 2 lookups
    test_one_winner_per_slot_across_eight_processes: lands exactly one of eight racing processes
        in each of 200 trials, every loser naming the winner
    test_regression_deleted_slot_is_reported_corrupt: reports a deleted slot as CORRUPT naming it,
        from a cold head and from a warm log
    test_regression_commit_refuses_to_refill_a_slot_gap: refuses to claim a slot whose successor
        exists, leaving the gap for repair
    test_conflict_lists_the_landed_revisions_from_the_slots: names every revision landed since the
        parent, read from the slots
    test_log_since_reads_only_the_later_slots: reads the since slot and the ones after it only
    test_unlanded_or_unknown_revision_is_an_unknown_ref: refuses a loser's record, an unknown and
        a malformed revision in log and tree
    test_commit_on_an_unlanded_parent_is_a_conflict: lists every landed revision for a parent no
        slot holds
    test_backend_refuses_to_write_a_layout_1_root: raises VersionError and writes nothing
    test_regression_shows_skips_housekeeping_names_and_refuses_strays: lists shows past .DS_Store
        and fails closed on any other entry
    test_shows_lists_only_shows_with_a_first_slot: leaves out folders a failed first commit left
    test_commit_refuses_malformed_caller_ids: blames the caller with BAD_ID for each malformed ID
    test_identical_record_and_shared_blob_are_kept: lands a commit whose record and blob already
        hold the same bytes
    test_differing_record_blocks_the_commit: refuses a commit whose record name holds other bytes
    test_failed_temp_write_leaves_no_temp_file: maps a failed flush to IO and removes the temp file
    test_regression_folder_collision_is_corrupt: refuses to read a show whose folder's manifest
        names another show
    test_tampered_log_files_are_corrupt: maps each tampered slot, record and manifest to CORRUPT
    test_tampered_records_are_corrupt: maps each tampered revision record to CORRUPT in tree()
    test_tampered_blobs_are_corrupt: maps a swapped or misnamed blob to CORRUPT, on read and when a
        commit would reuse it
    test_file_repository_writes_layout_2_and_keeps_layout_1_roots: stores a new root in layout 2
        and opens a layout-1 root with its own backend
    test_regression_file_repository_writes_beside_housekeeping_entries: stores shows in a root that
        holds .DS_Store or an unreadable lost+found
"""

import errno
import json
import multiprocessing
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.canonical import jcs
from e8s.core.errors import ConflictError, StorageError, UnknownRefError, VersionError
from e8s.core.util import content_hash
from e8s.show import file_backend
from e8s.show.backend import digest
from e8s.show.document_dump import dump_json
from e8s.show.document_repository import DocumentRepository
from e8s.show.file_backend import SLOT_DIGITS, FileBackend
from e8s.show.file_repository import FileRepository
from e8s.show.history_models import Revision
from e8s.show.layout import read_layout
from e8s.show.layout1 import _FileBackend
from e8s.show.show_folders import show_folder
from tests.fixtures.slot_race import RACE_SHOW_ID, race_once_per_trial
from tests.helpers import context_entries

AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SHOW_ID = "slots"
RENDER = "/renders/a.txt"
COLD_REVISIONS = 1000
COLD_LOOKUPS_MAX = 22
RACERS = 8
RACE_TRIALS = 200
RACE_TIMEOUT_S = 120.0
UNKNOWN_ID = "f" * 64


def _change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    data = f"render {n}".encode()
    revision = Revision(
        id=content_hash({"parent": parent, "n": n}), parent=parent, at=AT, changed=(RENDER,)
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


def _folder(root: Path) -> Path:
    return root / "shows" / show_folder(SHOW_ID)


def _slot(root: Path, seq: int) -> Path:
    return _folder(root) / "log" / f"{seq:0{SLOT_DIGITS}d}"


def _record(root: Path, revision_id: str) -> Path:
    return _folder(root) / "records" / f"{revision_id}.json"


class _LstatRecorder:
    """Records the path of every os.lstat call while it is installed.

    Attributes:
        paths: Paths looked up, in call order.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paths: list[Path] = []
        real = os.lstat

        def recorded(path: str | Path, **kwargs: bool) -> os.stat_result:
            self.paths.append(Path(path))
            return real(path, **kwargs)

        monkeypatch.setattr(os, "lstat", recorded)

    def slots(self) -> list[int]:
        return [int(p.name) for p in self.paths if p.parent.name == "log"]


def test_commit_writes_the_layout_2_files(tmp_path: Path) -> None:
    backend = FileBackend(tmp_path)
    [first] = _landed(backend, 1)
    folder = _folder(tmp_path)
    layout = json.loads((tmp_path / "layout.json").read_bytes())
    assert layout == {"format": "e8.repository", "layout": 2}
    assert json.loads((folder / "show.json").read_bytes()) == {"id": SHOW_ID}
    slot = _slot(tmp_path, 1).read_bytes()
    assert slot == jcs(first.to_json()) + b"\n"
    record = json.loads(_record(tmp_path, first.id).read_bytes())
    assert record["seq"] == 1
    assert record["revision"]["id"] == first.id
    assert record["tree"] == {RENDER: digest(b"render 0")}
    assert (folder / "objects" / digest(b"render 0")).read_bytes() == b"render 0"
    assert list((tmp_path / "tmp").iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["layout.json", "shows", "tmp"]
    assert backend.shows() == [SHOW_ID]
    assert FileBackend(tmp_path).log(SHOW_ID) == [first]


def test_warm_head_probes_one_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    landed = _landed(FileBackend(tmp_path), 3)
    backend = FileBackend(tmp_path)
    assert backend.head(SHOW_ID) == landed[-1].id
    recorder = _LstatRecorder(monkeypatch)
    assert backend.head(SHOW_ID) == landed[-1].id
    assert recorder.paths == [_slot(tmp_path, 4)]


@pytest.mark.slow
def test_cold_head_needs_at_most_22_lookups_at_1000_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    landed = _landed(FileBackend(tmp_path), COLD_REVISIONS)
    recorder = _LstatRecorder(monkeypatch)
    assert FileBackend(tmp_path).head(SHOW_ID) == landed[-1].id
    assert len(recorder.paths) <= COLD_LOOKUPS_MAX
    assert recorder.slots()[-1] == COLD_REVISIONS


def _race_trials(root: Path) -> tuple[list[tuple[str, str]], list[int]]:
    """Prepares one root per trial: odd trials start from one landed revision, even ones empty."""
    trials: list[tuple[str, str]] = []
    before: list[int] = []
    for trial in range(RACE_TRIALS):
        folder = root / str(trial)
        parent = ""
        if trial % 2:
            repo = DocumentRepository(FileBackend(folder))
            seeded = {"/renders/seed.txt": b"seed"}
            parent = repo.transaction(RACE_SHOW_ID, seeded, expected_revision="", at=AT).id
        trials.append((str(folder), parent))
        before.append(trial % 2)
    return trials, before


@pytest.mark.slow
def test_one_winner_per_slot_across_eight_processes(tmp_path: Path) -> None:
    trials, before = _race_trials(tmp_path)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(RACERS)
    results: multiprocessing.Queue[tuple[int, int, bool, str]] = context.Queue()
    racers = [
        context.Process(target=race_once_per_trial, args=(barrier, trials, racer, results))
        for racer in range(RACERS)
    ]
    for process in racers:
        process.start()
    outcomes = [results.get(timeout=RACE_TIMEOUT_S) for _ in range(RACERS * RACE_TRIALS)]
    for process in racers:
        process.join(RACE_TIMEOUT_S)
        assert process.exitcode == 0
    for trial, (root, _) in enumerate(trials):
        reported = [o for o in outcomes if o[0] == trial]
        winners = [revision for _, _, has_landed, revision in reported if has_landed]
        assert len(winners) == 1
        assert {revision for *_, revision in reported} == set(winners)
        log = FileBackend(Path(root)).log(RACE_SHOW_ID)
        assert [r.id for r in log][before[trial] :] == winners


@pytest.mark.parametrize("deleted", [1, 2])
def test_regression_deleted_slot_is_reported_corrupt(tmp_path: Path, deleted: int) -> None:
    """Reports a deleted slot as CORRUPT, naming it, instead of rolling the head back."""
    writer = FileBackend(tmp_path)
    _landed(writer, 3)
    _slot(tmp_path, deleted).unlink()
    for read in (lambda: FileBackend(tmp_path).head(SHOW_ID), lambda: writer.log(SHOW_ID)):
        with pytest.raises(StorageError) as caught:
            read()
        assert caught.value.code == "CORRUPT"
        assert caught.value.context["file"] == f"{deleted:0{SLOT_DIGITS}d}"


def test_regression_commit_refuses_to_refill_a_slot_gap(tmp_path: Path) -> None:
    """Refuses to claim slot 2 while slot 3 exists, so a deleted slot is never refilled."""
    backend = FileBackend(tmp_path)
    landed = _landed(backend, 3)
    _slot(tmp_path, 2).unlink()
    revision, tree, blobs = _change(landed[0].id, 99)
    with pytest.raises(StorageError) as caught:
        backend.commit(SHOW_ID, revision, tree, blobs)
    assert caught.value.code == "CORRUPT"
    assert caught.value.context["file"] == f"{2:0{SLOT_DIGITS}d}"
    assert not _slot(tmp_path, 2).exists()
    assert backend.head(SHOW_ID) == landed[-1].id


def test_conflict_lists_the_landed_revisions_from_the_slots(tmp_path: Path) -> None:
    """Names every revision that landed since the parent, oldest first, read from the slots."""
    writer, late = FileBackend(tmp_path), FileBackend(tmp_path)
    [base] = _landed(writer, 1)
    assert late.head(SHOW_ID) == base.id
    racing = _landed(writer, 2, base.id, start=1)
    revision, tree, blobs = _change(base.id, 99)
    with pytest.raises(ConflictError) as caught:
        late.commit(SHOW_ID, revision, tree, blobs)
    assert caught.value.context["head"] == racing[-1].id
    assert [c["id"] for c in context_entries(caught.value, "changes")] == [r.id for r in racing]
    assert writer.log(SHOW_ID) == [base, *racing]


def test_log_since_reads_only_the_later_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    landed = _landed(FileBackend(tmp_path), 5)
    backend = FileBackend(tmp_path)
    backend.head(SHOW_ID)
    recorder = _LstatRecorder(monkeypatch)
    assert backend.log(SHOW_ID, since=landed[2].id) == landed[3:]
    assert min(recorder.slots()) == 3
    assert backend.log(SHOW_ID, since=landed[-1].id) == []


def test_unlanded_or_unknown_revision_is_an_unknown_ref(tmp_path: Path) -> None:
    backend = FileBackend(tmp_path)
    landed = _landed(backend, 2)
    loser, tree, blobs = _change(landed[0].id, 99)
    with pytest.raises(ConflictError):
        backend.commit(SHOW_ID, loser, tree, blobs)
    assert _record(tmp_path, loser.id).exists()
    for revision_id in (loser.id, UNKNOWN_ID, "../escape"):
        with pytest.raises(UnknownRefError):
            backend.log(SHOW_ID, since=revision_id)
        with pytest.raises(UnknownRefError):
            backend.tree(SHOW_ID, revision_id)
    assert backend.tree(SHOW_ID, landed[0].id) == {RENDER: digest(b"render 0")}


def test_commit_on_an_unlanded_parent_is_a_conflict(tmp_path: Path) -> None:
    backend = FileBackend(tmp_path)
    landed = _landed(backend, 2)
    revision, tree, blobs = _change(UNKNOWN_ID, 99)
    with pytest.raises(ConflictError) as caught:
        backend.commit(SHOW_ID, revision, tree, blobs)
    assert [c["id"] for c in context_entries(caught.value, "changes")] == [r.id for r in landed]


def test_backend_refuses_to_write_a_layout_1_root(tmp_path: Path) -> None:
    legacy = DocumentRepository(_FileBackend(tmp_path))
    legacy.transaction("old", {RENDER: b"x"}, expected_revision="", at=AT)
    revision, tree, blobs = _change("", 0)
    with pytest.raises(VersionError) as caught:
        FileBackend(tmp_path).commit(SHOW_ID, revision, tree, blobs)
    assert caught.value.context["layout"] == 1
    assert not (tmp_path / "layout.json").exists()
    assert not (tmp_path / "shows").exists()


def test_regression_shows_skips_housekeeping_names_and_refuses_strays(tmp_path: Path) -> None:
    """Lists shows past a Finder .DS_Store file; any other stray entry under shows/ fails closed."""
    backend = FileBackend(tmp_path)
    _landed(backend, 1)
    (tmp_path / "shows" / ".DS_Store").write_bytes(b"finder")
    assert backend.shows() == [SHOW_ID]
    (tmp_path / "shows" / "stray").mkdir()
    with pytest.raises(StorageError) as caught:
        backend.shows()
    assert caught.value.code == "CORRUPT"


def test_shows_lists_only_shows_with_a_first_slot(tmp_path: Path) -> None:
    assert FileBackend(tmp_path / "new").shows() == []
    backend = FileBackend(tmp_path)
    _landed(backend, 1)
    (tmp_path / "shows" / show_folder("empty")).mkdir()
    unlanded = tmp_path / "shows" / show_folder("unlanded")
    unlanded.mkdir()
    (unlanded / "show.json").write_text('{"id": "unlanded"}')
    assert backend.shows() == [SHOW_ID]
    assert backend.head("unlanded") == ""
    assert backend.log("unlanded") == []


@pytest.mark.parametrize("role", ["revision id", "parent id", "blob digest", "tree digest"])
def test_commit_refuses_malformed_caller_ids(tmp_path: Path, role: str) -> None:
    revision, tree, blobs = _change(UNKNOWN_ID, 0)
    bad = "../" + "a" * 61
    if role == "revision id":
        revision = revision.replace(id=bad)
    elif role == "parent id":
        revision = revision.replace(parent=bad)
    elif role == "blob digest":
        blobs = {bad: b"x"}
    else:
        tree = {RENDER: bad}
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).commit(SHOW_ID, revision, tree, blobs)
    assert caught.value.code == "BAD_ID"
    assert not tmp_path.joinpath("layout.json").exists()


def test_identical_record_and_shared_blob_are_kept(tmp_path: Path) -> None:
    backend = FileBackend(tmp_path)
    [first] = _landed(backend, 1)
    revision, tree, blobs = _change(first.id, 0)
    record = dump_json({"revision": revision.to_json(), "seq": 2, "tree": tree})
    _record(tmp_path, revision.id).write_bytes(record)
    backend.commit(SHOW_ID, revision, tree, blobs)
    assert FileBackend(tmp_path).log(SHOW_ID) == [first, revision]


def test_differing_record_blocks_the_commit(tmp_path: Path) -> None:
    backend = FileBackend(tmp_path)
    [first] = _landed(backend, 1)
    revision, tree, blobs = _change(first.id, 1)
    _record(tmp_path, revision.id).write_text("{}")
    with pytest.raises(StorageError) as caught:
        backend.commit(SHOW_ID, revision, tree, blobs)
    assert caught.value.code == "CORRUPT"
    assert not _slot(tmp_path, 2).exists()


def test_failed_temp_write_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FileBackend(tmp_path)
    [first] = _landed(backend, 1)

    def failing_fsync(_descriptor: int) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    revision, tree, blobs = _change(first.id, 1)
    with pytest.raises(StorageError) as caught:
        backend.commit(SHOW_ID, revision, tree, blobs)
    assert caught.value.code == "IO"
    assert list((tmp_path / "tmp").iterdir()) == []
    assert not _slot(tmp_path, 2).exists()


def _rewrite_slot(root: Path, content: object) -> None:
    _slot(root, 2).write_bytes(json.dumps(content).encode())


def _rewrite_record(root: Path, stored: Revision, **changes: object) -> None:
    path = _record(root, stored.id)
    path.write_text(json.dumps(json.loads(path.read_bytes()) | changes))


TAMPERS: dict[str, Callable[[Path, list[Revision]], object]] = {
    "slot not an object": lambda root, _: _rewrite_slot(root, [1]),
    "slot malformed": lambda root, landed: _rewrite_slot(
        root, landed[1].to_json() | {"at": "noon"}
    ),
    "slot id not hex": lambda root, landed: _rewrite_slot(root, landed[1].to_json() | {"id": "x"}),
    "slot not json": lambda root, _: _slot(root, 2).write_bytes(b"{"),
    "slot forks": lambda root, landed: _rewrite_slot(
        root, landed[1].to_json() | {"parent": UNKNOWN_ID}
    ),
    "record gone": lambda root, landed: _record(root, landed[1].id).unlink(),
    "manifest names another show": lambda root, _: (_folder(root) / "show.json").write_text(
        '{"id": "other"}'
    ),
    "manifest has extra keys": lambda root, _: (_folder(root) / "show.json").write_text(
        f'{{"id": "{SHOW_ID}", "x": 1}}'
    ),
}
TREE_TAMPERS: dict[str, Callable[[Path, list[Revision]], object]] = {
    "record seq zero": lambda root, landed: _rewrite_record(root, landed[1], seq=0),
    "record seq bool": lambda root, landed: _rewrite_record(root, landed[1], seq=True),
    "record missing tree": lambda root, landed: _record(root, landed[1].id).write_text(
        '{"seq": 2}'
    ),
    "record tree bad digest": lambda root, landed: _rewrite_record(
        root, landed[1], tree={RENDER: "x"}
    ),
    "record names another": lambda root, landed: _rewrite_record(
        root, landed[1], revision=landed[0].to_json()
    ),
}


@pytest.mark.parametrize("tamper", sorted(TAMPERS))
def test_tampered_log_files_are_corrupt(tmp_path: Path, tamper: str) -> None:
    landed = _landed(FileBackend(tmp_path), 2)
    TAMPERS[tamper](tmp_path, landed)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == "CORRUPT"


@pytest.mark.parametrize("tamper", sorted(TREE_TAMPERS))
def test_tampered_records_are_corrupt(tmp_path: Path, tamper: str) -> None:
    landed = _landed(FileBackend(tmp_path), 2)
    TREE_TAMPERS[tamper](tmp_path, landed)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).tree(SHOW_ID, landed[1].id)
    assert caught.value.code == "CORRUPT"


def test_tampered_blobs_are_corrupt(tmp_path: Path) -> None:
    _landed(FileBackend(tmp_path), 1)
    stored = digest(b"render 0")
    (_folder(tmp_path) / "objects" / stored).write_bytes(b"swapped")
    backend = FileBackend(tmp_path)
    for name in (stored, "../escape"):
        with pytest.raises(StorageError) as caught:
            backend.blob(SHOW_ID, name)
        assert caught.value.code == "CORRUPT"
    revision, tree, blobs = _change(backend.head(SHOW_ID), 0)
    with pytest.raises(StorageError) as reused:
        backend.commit(SHOW_ID, revision, tree, blobs)
    assert reused.value.code == "CORRUPT"
    assert not _slot(tmp_path, 2).exists()


def test_regression_folder_collision_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuses to read a show whose folder another show's manifest holds."""
    monkeypatch.setattr(file_backend, "show_folder", lambda _: "shared-" + "0" * 16)
    _landed(FileBackend(tmp_path), 1)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log("collides")
    assert caught.value.code == "CORRUPT"


def test_file_repository_writes_layout_2_and_keeps_layout_1_roots(tmp_path: Path) -> None:
    repo = FileRepository(tmp_path / "new")
    landed = repo.transaction(SHOW_ID, {RENDER: b"x"}, expected_revision="", at=AT)
    assert isinstance(repo.backend, FileBackend)
    assert read_layout(tmp_path / "new") == 2
    assert _slot(tmp_path / "new", 1).exists()
    assert FileRepository(tmp_path / "new").revisions(SHOW_ID) == [landed]
    DocumentRepository(_FileBackend(tmp_path / "old")).transaction(
        SHOW_ID, {RENDER: b"x"}, expected_revision="", at=AT
    )
    legacy = FileRepository(tmp_path / "old")
    assert isinstance(legacy.backend, _FileBackend)
    assert legacy.head(SHOW_ID) == landed.id


CANNOT_CHMOD = sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0)


@pytest.mark.parametrize("entry", [".DS_Store", "lost+found"])
def test_regression_file_repository_writes_beside_housekeeping_entries(
    tmp_path: Path, entry: str
) -> None:
    """Stores shows in a root holding .DS_Store, or an unreadable lost+found where chmod works."""
    if entry == ".DS_Store":
        (tmp_path / entry).write_bytes(b"finder")
    elif CANNOT_CHMOD:
        pytest.skip("needs permission checks that bind this user")
    else:
        (tmp_path / entry).mkdir(mode=0)
    try:
        landed = FileRepository(tmp_path).transaction(
            SHOW_ID, {RENDER: b"x"}, expected_revision="", at=AT
        )
        assert FileRepository(tmp_path).head(SHOW_ID) == landed.id
    finally:
        (tmp_path / entry).chmod(0o700)
