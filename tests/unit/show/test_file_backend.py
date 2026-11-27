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
    test_regression_repository_backend_is_read_only: refuses to assign a new backend to a repository
    test_regression_record_with_a_wrong_id_is_corrupt: refuses a record whose digests do not hash to
        its revision ID, in log and in tree
    test_regression_slot_that_differs_from_its_record_is_corrupt: refuses a slot whose revision is
        not the one in its record
    test_regression_log_checks_each_record_seq_against_its_slot: refuses a record whose seq is not
        its slot's number
    test_legacy_ids_accepted_only_before_legacy_before: accepts old-scheme IDs only in slots
        before the manifest's legacy_before
    test_current_ids_are_required_when_the_manifest_has_no_legacy_before: refuses an old-scheme ID
        when the manifest names no legacy slots
    test_regression_cold_commit_on_a_legacy_head_lands: commits on a legacy head from a cold backend
    test_manifest_legacy_before_must_be_a_positive_integer: refuses a manifest whose legacy_before
        is not a positive integer
    test_regression_symlinked_show_folder_is_refused: refuses a symlinked shows, show, log, records,
        objects or tmp folder for reads and writes, leaving the target untouched
    test_regression_shows_refuses_a_symlinked_folder: refuses to list shows through a symlinked
        shows/ or show folder
    test_regression_junction_under_the_root_is_refused: refuses a junction in place of the show
        folder (Windows only)
    test_log_longer_than_max_revisions_is_refused: refuses a log over MAX_REVISIONS
    test_log_reads_at_most_the_byte_budget: refuses a log whose slots and records pass the budget
    test_every_read_error_names_the_show: carries the real show ID in every read error
    test_regression_shows_errors_never_carry_an_empty_show: leaves out the show key when a folder
        is unreadable before its show is known
    test_regression_fifo_swapped_in_after_lstat_is_refused: refuses a FIFO swapped in for a slot,
        record, manifest or blob, naming that file, with the writer thread joined
"""

import contextlib
import errno
import json
import multiprocessing
import os
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.canonical import jcs
from e8s.core.errors import ConflictError, StorageError, UnknownRefError, VersionError
from e8s.core.util import content_hash
from e8s.show import file_backend
from e8s.show.backend import digest
from e8s.show.backend_checks import ReadBudget
from e8s.show.document_dump import dump_json
from e8s.show.document_repository import DocumentRepository, revision_id
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
# The cold head checks the show's manifest once: one lookup, then one read that looks it up.
MANIFEST_LOOKUPS = 2
# The first read also checks that shows/, the show folder, log/, records/, objects/ and tmp/ are
# real folders.
FOLDER_LOOKUPS = 6
RACERS = 8
RACE_TRIALS = 200
RACE_TIMEOUT_S = 120.0
UNKNOWN_ID = "f" * 64


def _change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    data = f"render {n}".encode()
    revision = Revision(
        id=revision_id(parent, AT, {RENDER: digest(data)}), parent=parent, at=AT, changed=(RENDER,)
    )
    return revision, {RENDER: digest(data)}, {digest(data): data}


def _legacy_change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    """Builds a revision whose ID is an old-scheme name, which no recomputation reproduces."""
    revision, tree, blobs = _change(parent, n)
    return revision.replace(id=content_hash({"legacy": n})), tree, blobs


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
    """Records the path of every no-follow lookup (os.lstat or os.stat without following links).

    Python 3.13 routes Path.lstat through os.stat(follow_symlinks=False); 3.14 calls os.lstat.

    Attributes:
        paths: Paths looked up, in call order.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paths: list[Path] = []
        real_lstat = os.lstat
        real_stat = os.stat

        def recorded_lstat(path: str | Path, **kwargs: bool) -> os.stat_result:
            self.paths.append(Path(path))
            return real_lstat(path, **kwargs)

        def recorded_stat(path: str | Path, **kwargs: bool) -> os.stat_result:
            if kwargs.get("follow_symlinks", True) is False:
                self.paths.append(Path(path))
            return real_stat(path, **kwargs)

        monkeypatch.setattr(os, "lstat", recorded_lstat)
        monkeypatch.setattr(os, "stat", recorded_stat)

    def slots(self) -> list[int]:
        return [int(p.name) for p in self.paths if p.parent.name == "log"]


class _SlotReadRecorder:
    """Records the slot number of every slot read the backend makes while it is installed.

    Attributes:
        seqs: Slot numbers read, in call order.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.seqs: list[int] = []
        real = FileBackend._read_slot

        def recorded(
            backend: FileBackend, show_id: str, seq: int, budget: ReadBudget | None = None
        ) -> Revision:
            self.seqs.append(seq)
            return real(backend, show_id, seq, budget)

        monkeypatch.setattr(FileBackend, "_read_slot", recorded)


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
    assert len(recorder.slots()) <= COLD_LOOKUPS_MAX
    assert len(recorder.paths) <= COLD_LOOKUPS_MAX + MANIFEST_LOOKUPS + FOLDER_LOOKUPS
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
    recorder = _SlotReadRecorder(monkeypatch)
    assert backend.log(SHOW_ID, since=landed[2].id) == landed[3:]
    assert recorder.seqs == [3, 4, 5]
    assert backend.log(SHOW_ID, since=landed[-1].id) == []


def test_unlanded_or_unknown_revision_is_an_unknown_ref(tmp_path: Path) -> None:
    backend = FileBackend(tmp_path)
    landed = _landed(backend, 2)
    loser, tree, blobs = _change(landed[0].id, 99)
    with pytest.raises(ConflictError):
        backend.commit(SHOW_ID, loser, tree, blobs)
    assert _record(tmp_path, loser.id).exists()
    for unknown in (loser.id, UNKNOWN_ID, "../escape"):
        with pytest.raises(UnknownRefError):
            backend.log(SHOW_ID, since=unknown)
        with pytest.raises(UnknownRefError):
            backend.tree(SHOW_ID, unknown)
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
    """Refuses to read, or report the head of, a show whose folder another show's manifest holds."""
    monkeypatch.setattr(file_backend, "show_folder", lambda _: "shared-" + "0" * 16)
    _landed(FileBackend(tmp_path), 1)
    for read in (FileBackend(tmp_path).log, FileBackend(tmp_path).head):
        with pytest.raises(StorageError) as caught:
            read("collides")
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


def test_regression_repository_backend_is_read_only(tmp_path: Path) -> None:
    repo = DocumentRepository(FileBackend(tmp_path))
    with pytest.raises(AttributeError):
        object.__setattr__(repo, "backend", FileBackend(tmp_path / "other"))


def _set_manifest(root: Path, **extra: object) -> None:
    (_folder(root) / "show.json").write_bytes(dump_json({"id": SHOW_ID, **extra}))


def _mixed_history(root: Path, legacy: int, total: int) -> list[Revision]:
    """Lands `legacy` old-scheme revisions, then current-scheme ones, `total` in all."""
    backend = FileBackend(root)
    landed: list[Revision] = []
    for n in range(total):
        parent = landed[-1].id if landed else ""
        make = _legacy_change if n < legacy else _change
        revision, tree, blobs = make(parent, n)
        backend.commit(SHOW_ID, revision, tree, blobs)
        landed.append(revision)
    return landed


def test_regression_record_with_a_wrong_id_is_corrupt(tmp_path: Path) -> None:
    """Refuses a record whose digests no longer hash to the revision ID it is named by."""
    landed = _landed(FileBackend(tmp_path), 2)
    _rewrite_record(tmp_path, landed[1], tree={RENDER: "a" * 64})
    for read in (
        lambda: FileBackend(tmp_path).log(SHOW_ID),
        lambda: FileBackend(tmp_path).tree(SHOW_ID, landed[1].id),
    ):
        with pytest.raises(StorageError) as caught:
            read()
        assert caught.value.code == "CORRUPT"
        assert caught.value.context["file"] == f"{landed[1].id}.json"
        assert "does not hash to its name" in caught.value.message


def test_regression_slot_that_differs_from_its_record_is_corrupt(tmp_path: Path) -> None:
    landed = _landed(FileBackend(tmp_path), 2)
    later = landed[1].replace(at=AT.replace(hour=13))
    _rewrite_slot(tmp_path, later.to_json())
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == "CORRUPT"
    assert caught.value.context["file"] == f"{2:0{SLOT_DIGITS}d}"


def test_regression_log_checks_each_record_seq_against_its_slot(tmp_path: Path) -> None:
    landed = _landed(FileBackend(tmp_path), 3)
    _rewrite_record(tmp_path, landed[1], seq=3)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == "CORRUPT"
    assert caught.value.context["file"] == f"{2:0{SLOT_DIGITS}d}"
    assert "seq" in caught.value.message


def test_legacy_ids_accepted_only_before_legacy_before(tmp_path: Path) -> None:
    landed = _mixed_history(tmp_path, legacy=2, total=4)
    _set_manifest(tmp_path, legacy_before=3)
    backend = FileBackend(tmp_path)
    assert backend.log(SHOW_ID) == landed
    assert backend.tree(SHOW_ID, landed[0].id) == {RENDER: digest(b"render 0")}
    for legacy_before, bad_slot in ((2, 2), (1, 1)):
        _set_manifest(tmp_path, legacy_before=legacy_before)
        with pytest.raises(StorageError) as caught:
            FileBackend(tmp_path).log(SHOW_ID)
        assert caught.value.code == "CORRUPT"
        assert caught.value.context["file"] == f"{landed[bad_slot - 1].id}.json"


def test_current_ids_are_required_when_the_manifest_has_no_legacy_before(tmp_path: Path) -> None:
    _mixed_history(tmp_path, legacy=1, total=2)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == "CORRUPT"


def test_regression_cold_commit_on_a_legacy_head_lands(tmp_path: Path) -> None:
    """Commits on a head with an old-scheme ID from a backend that has read nothing yet."""
    landed = _mixed_history(tmp_path, legacy=2, total=2)
    _set_manifest(tmp_path, legacy_before=3)
    revision, tree, blobs = _change(landed[-1].id, 7)
    FileBackend(tmp_path).commit(SHOW_ID, revision, tree, blobs)
    assert FileBackend(tmp_path).log(SHOW_ID) == [*landed, revision]


@pytest.mark.parametrize("legacy_before", [0, -1, True, "2", 1.5, None])
def test_manifest_legacy_before_must_be_a_positive_integer(
    tmp_path: Path, legacy_before: object
) -> None:
    _landed(FileBackend(tmp_path), 1)
    _set_manifest(tmp_path, legacy_before=legacy_before)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == "CORRUPT"
    assert caught.value.context["file"] == "show.json"


def _outside_folder(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    return outside


def _symlink_shows(root: Path, outside: Path) -> None:
    (root / "shows").rename(outside / "shows")
    (root / "shows").symlink_to(outside / "shows", target_is_directory=True)


def _symlink_show_folder(root: Path, outside: Path) -> None:
    _folder(root).rename(outside / _folder(root).name)
    _folder(root).symlink_to(outside / _folder(root).name, target_is_directory=True)


def _symlink_inner_folder(name: str) -> Callable[[Path, Path], None]:
    def swap(root: Path, outside: Path) -> None:
        (_folder(root) / name).rename(outside / name)
        (_folder(root) / name).symlink_to(outside / name, target_is_directory=True)

    return swap


def _symlink_tmp_folder(root: Path, outside: Path) -> None:
    (root / "tmp").rmdir()
    (root / "tmp").symlink_to(outside, target_is_directory=True)


SYMLINKS: dict[str, Callable[[Path, Path], None]] = {
    "shows": _symlink_shows,
    "show folder": _symlink_show_folder,
    "log folder": _symlink_inner_folder("log"),
    "records folder": _symlink_inner_folder("records"),
    "objects folder": _symlink_inner_folder("objects"),
    "tmp folder": _symlink_tmp_folder,
}


def _snapshot(folder: Path) -> dict[str, bytes | None]:
    """Maps every path under `folder` to its bytes, or None for a folder."""
    return {
        str(path): path.read_bytes() if path.is_file() else None
        for path in sorted(folder.rglob("*"))
    }


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs privilege on Windows")
@pytest.mark.parametrize("kind", sorted(SYMLINKS))
def test_regression_symlinked_show_folder_is_refused(tmp_path: Path, kind: str) -> None:
    """Refuses to read or write through a symlinked folder, so no write leaves the root."""
    root = tmp_path / "root"
    warm = FileBackend(root)
    landed = _landed(warm, 1)
    outside = _outside_folder(tmp_path)
    SYMLINKS[kind](root, outside)
    before = _snapshot(outside)
    revision, tree, blobs = _change(landed[0].id, 5)
    with pytest.raises(StorageError) as written:
        warm.commit(SHOW_ID, revision, tree, blobs)
    assert written.value.code == "CORRUPT"
    assert "symbolic link" in written.value.message
    assert _snapshot(outside) == before
    if kind != "tmp folder":
        with pytest.raises(StorageError) as read:
            FileBackend(root).log(SHOW_ID)
        assert read.value.code == "CORRUPT"
        assert read.value.context["show"] == SHOW_ID


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs privilege on Windows")
@pytest.mark.parametrize("kind", ["shows", "show folder"])
def test_regression_shows_refuses_a_symlinked_folder(tmp_path: Path, kind: str) -> None:
    """Lists no show through a symlinked shows/ or show folder, so nothing outside the root."""
    root = tmp_path / "root"
    _landed(FileBackend(root), 1)
    SYMLINKS[kind](root, _outside_folder(tmp_path))
    with pytest.raises(StorageError) as caught:
        FileBackend(root).shows()
    assert caught.value.code == "CORRUPT"
    assert "symbolic link" in caught.value.message


@pytest.mark.skipif(sys.platform != "win32", reason="junctions exist only on Windows")
def test_regression_junction_under_the_root_is_refused(tmp_path: Path) -> None:
    if sys.platform != "win32":
        return
    import _winapi

    root = tmp_path / "root"
    warm = FileBackend(root)
    landed = _landed(warm, 1)
    outside = _outside_folder(tmp_path)
    moved = outside / _folder(root).name
    _folder(root).rename(moved)
    _winapi.CreateJunction(str(moved), str(_folder(root)))
    before = _snapshot(outside)
    revision, tree, blobs = _change(landed[0].id, 5)
    with pytest.raises(StorageError) as written:
        warm.commit(SHOW_ID, revision, tree, blobs)
    assert written.value.code == "CORRUPT"
    assert "symbolic link or junction" in written.value.message
    assert _snapshot(outside) == before
    with pytest.raises(StorageError) as read:
        FileBackend(root).log(SHOW_ID)
    assert read.value.code == "CORRUPT"
    assert read.value.context["show"] == SHOW_ID


def test_log_longer_than_max_revisions_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_backend, "MAX_REVISIONS", 3)
    backend = FileBackend(tmp_path)
    landed = _landed(backend, 3)
    assert FileBackend(tmp_path).log(SHOW_ID) == landed
    _landed(backend, 1, landed[-1].id, start=3)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == "TOO_LARGE"
    assert caught.value.context["show"] == SHOW_ID
    assert caught.value.context["cap"] == 3


def test_log_reads_at_most_the_byte_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _landed(FileBackend(tmp_path), 4)
    monkeypatch.setattr(file_backend, "LOG_READ_MAX_BYTES", 600)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == "TOO_LARGE"
    assert caught.value.context["show"] == SHOW_ID


def test_every_read_error_names_the_show(tmp_path: Path) -> None:
    landed = _landed(FileBackend(tmp_path), 2)
    _slot(tmp_path, 2).write_bytes(b"{")
    _record(tmp_path, landed[0].id).write_bytes(b"[]")
    reads: dict[str, Callable[[], object]] = {
        "slot": lambda: FileBackend(tmp_path).log(SHOW_ID),
        "record": lambda: FileBackend(tmp_path).tree(SHOW_ID, landed[0].id),
        "blob": lambda: FileBackend(tmp_path).blob(SHOW_ID, "../x"),
        "unknown revision": lambda: FileBackend(tmp_path).tree(SHOW_ID, UNKNOWN_ID),
    }
    for name, read in reads.items():
        with pytest.raises((StorageError, UnknownRefError)) as caught:
            read()
        assert caught.value.context["show"] == SHOW_ID, name


def test_regression_shows_errors_never_carry_an_empty_show(tmp_path: Path) -> None:
    _landed(FileBackend(tmp_path), 1)
    manifest = _folder(tmp_path) / "show.json"
    manifest.write_bytes(b"{")
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).shows()
    assert caught.value.code == "CORRUPT"
    assert caught.value.context.get("show") != ""
    assert caught.value.context["file"] == "show.json"
    assert "repository of :" not in caught.value.message
    manifest.write_bytes(dump_json({"id": SHOW_ID}))
    (tmp_path / "shows" / "stray").mkdir()
    with pytest.raises(StorageError) as stray:
        FileBackend(tmp_path).shows()
    assert stray.value.context.get("show") != ""
    assert "repository of :" not in stray.value.message


CAN_MAKE_FIFOS = hasattr(os, "mkfifo") and sys.platform != "win32"
FIFO_JOIN_S = 5.0


def _swap_for_fifo_after_lstat(monkeypatch: pytest.MonkeyPatch, target: Path) -> threading.Thread:
    """Swaps `target` for a FIFO that a writer thread fills once a reader opens it."""
    content, stale = target.read_bytes(), target.lstat()
    target.unlink()
    os.mkfifo(target)

    def fill() -> None:
        with contextlib.suppress(BrokenPipeError), target.open("wb") as writer:
            writer.write(content)

    real_lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda p: stale if p == target else real_lstat(p))
    writer = threading.Thread(target=fill, daemon=True)
    writer.start()
    return writer


def _join_or_release(writer: threading.Thread, target: Path) -> None:
    """Joins the writer; one still blocked means nothing opened the FIFO, so it is released."""
    writer.join(FIFO_JOIN_S)
    if writer.is_alive():
        os.close(os.open(target, os.O_RDONLY | os.O_NONBLOCK))
        writer.join(FIFO_JOIN_S)
    assert not writer.is_alive()


def _log(backend: FileBackend, _landed: list[Revision]) -> object:
    return backend.log(SHOW_ID)


def _tree(backend: FileBackend, landed: list[Revision]) -> object:
    return backend.tree(SHOW_ID, landed[0].id)


def _blob(backend: FileBackend, _landed: list[Revision]) -> object:
    return backend.blob(SHOW_ID, digest(b"render 0"))


FIFO_TARGETS: dict[
    str,
    tuple[Callable[[Path, list[Revision]], Path], Callable[[FileBackend, list[Revision]], object]],
] = {
    "slot": (lambda root, _: _slot(root, 2), _log),
    "record": (lambda root, landed: _record(root, landed[0].id), _tree),
    "manifest": (lambda root, _: _folder(root) / "show.json", _log),
    "blob": (lambda root, _: _folder(root) / "objects" / digest(b"render 0"), _blob),
}


@pytest.mark.skipif(not CAN_MAKE_FIFOS, reason="needs mkfifo, O_NONBLOCK and O_NOFOLLOW")
@pytest.mark.parametrize("kind", sorted(FIFO_TARGETS))
def test_regression_fifo_swapped_in_after_lstat_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Refuses a FIFO that holds valid content, naming the file that was swapped."""
    landed = _landed(FileBackend(tmp_path), 2)
    locate, read = FIFO_TARGETS[kind]
    target = locate(tmp_path, landed)
    writer = _swap_for_fifo_after_lstat(monkeypatch, target)
    try:
        with pytest.raises(StorageError) as caught:
            read(FileBackend(tmp_path), landed)
    finally:
        _join_or_release(writer, target)
    assert caught.value.code == "CORRUPT"
    assert caught.value.context["file"] == target.name
    assert "not a regular file" in caught.value.message
