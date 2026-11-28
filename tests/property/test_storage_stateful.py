#!/usr/bin/env python3

"""
test_storage_stateful.py --- drives both storage backends through random commits, races and gc

A model keeps every landed revision with its documents. Each step commits, races two writers on
one parent, reverts, reads an old revision, reopens the repository or collects garbage, then the
invariants compare the store with the model: the head, every revision's documents, and fsck.

Contains:
    Landed: holds one revision the model expects in the store, with its documents
    MemoryStore: hands out repositories over one shared in-memory backend
    FileStore: hands out file repositories over one temporary root, removed at teardown
    StorageRules: runs commits, races, reverts and reads against a store and a model of it
    FileRules: adds reopening, gc and fsck, which only a file repository has
    MemoryStorageMachine: runs StorageRules over the in-memory backend
    FileStorageMachine: runs StorageRules and FileRules over the layout-2 file backend
    TestMemoryStorage: runs MemoryStorageMachine under pytest
    TestFileStorage: runs FileStorageMachine under pytest
"""

import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path, PurePosixPath

import pytest
from hypothesis import event, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from e8s.core.errors import ConflictError, StorageError
from e8s.show.backend import digest
from e8s.show.document_repository import DocumentRepository, revision_id
from e8s.show.file_repository import FileRepository
from e8s.show.fsck import fsck
from e8s.show.fsck_findings import UNREACHABLE
from e8s.show.gc import gc
from e8s.show.history import revert
from e8s.show.history_models import Revision
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.storage import ROOT_REVISION
from e8s.show.workspace import open_workspace

SHOW_ID = "show_1"
PATHS = ("/renders/a.bin", "/renders/b.bin", "/exports/c.bin")
SHARED_CONTENTS = (b"", b"x", b"y", b"xy")
FORCED_SUFFIX = b"!"
OBJECT_DIR = "objects"
FIRST_AT = datetime(2026, 11, 27, tzinfo=UTC)
TICK = timedelta(seconds=1)
# gc compares file modification times, which the file system's clock sets, with `at`. These two
# times lie before and after any file a run writes, so gc keeps everything or treats every
# unreachable file as old, whatever the real clock reads.
GC_BEFORE_ANY_FILE = datetime(2000, 1, 1, tzinfo=UTC)
GC_AFTER_EVERY_FILE = datetime(2200, 1, 1, tzinfo=UTC)
MAX_PICK = 1_000
MACHINE_EXAMPLES = 25
MACHINE_STEPS = 20
# Hypothesis times one example, so the deadline covers a whole run of steps, each with flushed
# file writes. State machines run without one by default; this keeps that default.
MACHINE_SETTINGS = settings(
    max_examples=MACHINE_EXAMPLES, stateful_step_count=MACHINE_STEPS, deadline=None
)

type Documents = Mapping[str, bytes]
type Changes = Mapping[str, bytes | None]

contents = st.one_of(st.sampled_from(SHARED_CONTENTS), st.binary(min_size=1, max_size=16))
change_sets = st.dictionaries(
    st.sampled_from(PATHS), st.one_of(st.none(), contents), min_size=1, max_size=len(PATHS)
)
picks = st.integers(min_value=0, max_value=MAX_PICK)


@dataclass(frozen=True)
class Landed:
    """Holds one revision the model expects in the store.

    Attributes:
        revision: The revision as the write returned it.
        documents: Every document stored at the revision.
    """

    revision: Revision
    documents: Documents


class MemoryStore:
    """Hands out repositories over one in-memory backend, as writers in one process see it."""

    def __init__(self) -> None:
        self._backend = InMemoryRepository().backend

    def open(self) -> DocumentRepository:
        """Returns a new repository over the shared backend."""
        return DocumentRepository(self._backend)

    def close(self) -> None:
        """Does nothing: the backend goes with the machine."""


class FileStore:
    """Hands out file repositories over one temporary root, as separate processes see it.

    Attributes:
        root: The repository root, removed by close().
    """

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="e8s-storage-"))

    def open(self) -> FileRepository:
        """Returns a new repository on the root, with nothing cached."""
        return FileRepository(self.root)

    def close(self) -> None:
        """Removes the root and everything under it."""
        shutil.rmtree(self.root)


def _applied(changes: Changes, before: Documents) -> dict[str, bytes]:
    """Returns the documents a write of `changes` leaves on top of `before`."""
    after = dict(before)
    for path, data in changes.items():
        if data is None:
            after.pop(path, None)
        else:
            after[path] = data
    return after


def _landing(changes: Changes, before: Documents) -> Changes:
    """Returns `changes`, or them with one document altered when they would change nothing."""
    if _applied(changes, before) != before:
        return changes
    path = min(changes)
    return {**changes, path: before.get(path, b"") + FORCED_SUFFIX}


def _changed[V](before: Mapping[str, V], after: Mapping[str, V]) -> tuple[str, ...]:
    return tuple(sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p)))


def _prepared(
    parent: str, before: Documents, changes: Changes, at: datetime
) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    """Builds the revision, tree and blobs a writer sends a backend, as the repository would.

    Args:
        parent: The head the writer read.
        before: The documents at that head.
        changes: The writer's edit.
        at: Time stamp of the revision.

    Returns:
        commit: The revision, its whole path-to-digest tree and the blobs of the written bytes.
    """
    after = _applied(changes, before)
    tree = {path: digest(data) for path, data in after.items()}
    old = {path: digest(data) for path, data in before.items()}
    changed = _changed(old, tree)
    revision = Revision(
        id=revision_id(parent, at, {path: tree.get(path) for path in changed}),
        parent=parent,
        at=at,
        changed=changed,
    )
    blobs = {digest(data): data for data in changes.values() if data is not None}
    return revision, tree, blobs


class StorageRules:
    """Runs commits, races, reverts and reads against a store and a model of it.

    Attributes:
        store: Where the repositories under test keep their revisions.
        repo: The writer most rules use.
        rival: A second writer on the same store, for races.
        log: Every revision the model expects, oldest first.
        lost_blobs: Digests a losing writer stored that no revision may need.
        clock: Time stamp of the last write; each write moves it on by TICK.
    """

    store: MemoryStore | FileStore
    repo: DocumentRepository
    rival: DocumentRepository
    log: list[Landed]
    lost_blobs: set[str]
    clock: datetime

    def _start(self, store: MemoryStore | FileStore) -> None:
        self.store = store
        self.repo = store.open()
        self.rival = store.open()
        self.log = []
        self.lost_blobs = set()
        self.clock = FIRST_AT

    def _next_at(self) -> datetime:
        self.clock += TICK
        return self.clock

    def _head(self) -> str:
        return self.log[-1].revision.id if self.log else ROOT_REVISION

    def _documents(self) -> Documents:
        return self.log[-1].documents if self.log else {}

    def _pick(self, index: int) -> Landed:
        return self.log[index % len(self.log)]

    def _record(self, landed: Revision, after: Documents, at: datetime) -> None:
        """Checks a write's returned revision against the model, then records it."""
        before, head = self._documents(), self._head()
        if after == before:
            assert landed.id == head, "a write that changes nothing must return the head"
            return
        assert landed.parent == head
        assert landed.at == at
        assert landed.changed == _changed(before, after)
        self.log.append(Landed(landed, dict(after)))

    def _unreachable(self) -> set[str]:
        reachable = {digest(data) for entry in self.log for data in entry.documents.values()}
        return self.lost_blobs - reachable

    @rule(changes=change_sets)
    def commit(self, changes: Changes) -> None:
        """Writes one edit at the head, as one revision or as nothing when nothing differs."""
        after, at = _applied(changes, self._documents()), self._next_at()
        if not self.log and not after:
            with pytest.raises(StorageError, match="nothing to save"):
                self.repo.transaction(SHOW_ID, changes, expected_revision=ROOT_REVISION, at=at)
            return
        landed = self.repo.transaction(SHOW_ID, changes, expected_revision=self._head(), at=at)
        self._record(landed, after, at)

    @rule(
        winner=change_sets,
        loser=change_sets,
        main_wins=st.booleans(),
        through_backend=st.booleans(),
    )
    def racing_commit(
        self, winner: Changes, loser: Changes, *, main_wins: bool, through_backend: bool
    ) -> None:
        """Lets two writers that read the same head both write: one lands, one changes nothing.

        The loser either asks its repository, which sees the moved head, or sends its commit
        straight to the backend, as a writer that passed that check just before the winner
        landed would; then only the backend's compare-and-swap stops it.
        """
        first, second = (self.repo, self.rival) if main_wins else (self.rival, self.repo)
        parent, before = self._head(), self._documents()
        assert second.head(SHOW_ID) == parent
        winner, loser = _landing(winner, before), _landing(loser, before)
        at = self._next_at()
        landed = first.transaction(SHOW_ID, winner, expected_revision=parent, at=at)
        self._record(landed, _applied(winner, before), at)
        lost_at = self._next_at()
        lose: Callable[[], object] = (
            partial(second.backend.commit, SHOW_ID, *_prepared(parent, before, loser, lost_at))
            if through_backend
            else partial(second.transaction, SHOW_ID, loser, expected_revision=parent, at=lost_at)
        )
        with pytest.raises(ConflictError):
            lose()
        event(f"loser stopped by the {'backend' if through_backend else 'repository'}")
        if through_backend:
            self.lost_blobs |= {digest(data) for data in loser.values() if data is not None}

    @precondition(lambda self: self.log)
    @rule(pick=picks)
    def revert_to(self, pick: int) -> None:
        """Saves an earlier revision's documents as the new head."""
        target, at = self._pick(pick), self._next_at()
        landed = revert(open_workspace(self.repo, SHOW_ID), target.revision.id, at)
        self._record(landed, target.documents, at)

    @precondition(lambda self: self.log)
    @rule(pick=picks)
    def read_old_revision(self, pick: int) -> None:
        """Reads one revision path by path and through a workspace."""
        target = self._pick(pick)
        target_id = target.revision.id
        assert self.repo.get_revision(SHOW_ID, target_id) == target.revision
        assert self.repo.paths(SHOW_ID, target_id) == sorted(target.documents)
        for path in PATHS:
            assert self.repo.read(SHOW_ID, path, target_id) == target.documents.get(path)
        tree = open_workspace(self.repo, SHOW_ID).tree_at(target_id)
        assert dict(tree) == target.documents

    @invariant()
    def head_matches_the_model(self) -> None:
        """Requires every writer, and one with nothing cached, to see the model's head."""
        head = self._head()
        assert self.repo.head(SHOW_ID) == head
        assert self.rival.head(SHOW_ID) == head
        assert self.store.open().head(SHOW_ID) == head

    @invariant()
    def every_revision_loads(self) -> None:
        """Requires the log and every revision's documents to equal the model's."""
        assert self.repo.revisions(SHOW_ID) == [entry.revision for entry in self.log]
        for entry in self.log:
            assert dict(self.repo.documents(SHOW_ID, entry.revision.id)) == entry.documents

    def teardown(self) -> None:
        """Removes the store's files."""
        self.store.close()


class FileRules(StorageRules):
    """Adds the rules only a file repository has: reopening, gc and fsck.

    Attributes:
        root: The repository root every writer opens.
    """

    root: Path

    @rule()
    def reopen(self) -> None:
        """Replaces both writers with repositories that start with nothing cached."""
        self.repo = self.store.open()
        self.rival = self.store.open()

    @precondition(lambda self: self.log)
    @rule(after_every_file=st.booleans())
    def collect_garbage(self, *, after_every_file: bool) -> None:
        """Runs gc dry, then confirmed: it removes exactly the blobs only a losing writer stored."""
        at = GC_AFTER_EVERY_FILE if after_every_file else GC_BEFORE_ANY_FILE
        expected = self._unreachable() if after_every_file else set()
        if expected:
            event("gc removes blobs only a loser stored")
        planned = gc(self.root, at=at)
        report = gc(self.root, at=at, confirm=True)
        assert planned.dry_run
        assert not report.dry_run
        assert planned.removed == report.removed
        assert report.skipped == ()
        removed = [PurePosixPath(path) for path in report.removed]
        assert all(path.parent.name == OBJECT_DIR for path in removed)
        assert sorted(path.name for path in removed) == sorted(expected)
        if after_every_file:
            self.lost_blobs.clear()

    @precondition(lambda self: self.log)
    @invariant()
    def fsck_is_ok(self) -> None:
        """Requires fsck to pass, finding nothing but the blobs only a losing writer stored."""
        report = fsck(self.root)
        assert report.ok, report.findings
        assert all(finding.code == UNREACHABLE for finding in report.findings), report.findings
        found = [PurePosixPath(finding.path) for finding in report.findings]
        assert all(path.parent.name == OBJECT_DIR for path in found)
        assert sorted(path.name for path in found) == sorted(self._unreachable())


@MACHINE_SETTINGS
class MemoryStorageMachine(StorageRules, RuleBasedStateMachine):
    """Runs the storage rules over the in-memory backend."""

    def __init__(self) -> None:
        super().__init__()
        self._start(MemoryStore())


@MACHINE_SETTINGS
class FileStorageMachine(FileRules, RuleBasedStateMachine):
    """Runs the storage and file rules over the layout-2 file backend in a temporary root."""

    def __init__(self) -> None:
        super().__init__()
        store = FileStore()
        self.root = store.root
        self._start(store)


TestMemoryStorage = MemoryStorageMachine.TestCase
TestFileStorage = FileStorageMachine.TestCase
