#!/usr/bin/env python3

"""
test_storage_crash.py --- stops file commits after every write step and checks the reopened root

Every folder, file, link, flush, removal and move a layout-2 commit makes goes through the write
functions of `durable_io`. A shim wraps them and stops one commit after its n-th call, for every
n, on a file system with and without hard links. A process crash keeps every call made; a power
loss also drops each name whose folder was not flushed since it changed, and the bytes of each
file created in place and not flushed. A fresh repository on the root must then see the parent or
the new revision as head and load every revision; fsck may find only unreachable files and stale
temps; gc must leave the root clean with the head and documents unchanged; and a retry of the
same commit lands, or conflicts because the stopped commit had already landed.

Without hard links a file is created in place and then written, so a stop in between leaves it
empty. An empty layout file, first manifest or new slot makes reads refuse the root until a fsck
repair removes it; an empty blob or record makes a retry fail closed until gc removes it. Both
outcomes are pinned, and every check above then runs on the repaired or cleaned root. An
unstopped commit must survive a power loss too.

The model's limits: a stop falls between write calls, never inside one; a power loss empties a
file whose bytes were not flushed but never keeps a part of them; unflushed changes are lost all
together, never some kept and others reordered; a macOS barrier flush counts as a full one; and
a lost modification time set by `freshen` is not modelled.

Contains:
    Crash: stops a commit; a BaseException, so no library handler maps or swallows it
    Scenario: holds the commits that set a root up and the commit a crash stops
    Landed: holds one revision expected in the root, with its documents
    Counted: holds the write calls of an unstopped commit and the revision it lands
    Change: holds one change a write call made that a power loss can undo
    WriteShim: counts a commit's write calls, stops it after one and models a power loss
    test_crash_at_every_write_step_leaves_old_or_new_head: fuzzes the scenario, modes and step
    test_every_write_step_of_every_scenario_survives_a_crash: stops each commit at every step
"""

import errno
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path, PurePosixPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, StorageError
from e8s.show import durable_io, torn_reads
from e8s.show.file_backend import (
    LOG_DIR,
    MANIFEST_FILE,
    OBJECT_DIR,
    RECORD_DIR,
    SHOWS_DIR,
    SLOT_DIGITS,
)
from e8s.show.file_repository import FileRepository
from e8s.show.fsck import fsck
from e8s.show.fsck_findings import STALE_TEMP, TORN_FILE, TORN_SLOT, UNREACHABLE
from e8s.show.gc import gc
from e8s.show.history_models import Revision
from e8s.show.layout import LAYOUT_FILE
from e8s.show.storage import ROOT_REVISION

SHOW_ID = "show_1"
FIRST_AT = datetime(2026, 11, 27, tzinfo=UTC)
TICK = timedelta(seconds=1)
# gc compares file modification times, which the file system's clock sets, with `at`. This time
# lies after any file a run writes, so gc treats every unreachable file and temp as old.
GC_AFTER_EVERY_FILE = datetime(2200, 1, 1, tzinfo=UTC)
LEFT_FOR_GC = frozenset({UNREACHABLE, STALE_TEMP})
# A torn file's kind: the fsck code it gets, and the code a read of the root raises.
TORN_OUTCOMES = {
    LAYOUT_FILE: (TORN_FILE, ErrorCode.CORRUPT),
    MANIFEST_FILE: (TORN_FILE, ErrorCode.CORRUPT),
    LOG_DIR: (TORN_SLOT, ErrorCode.TORN_SLOT),
}
NO_LINK_ERRNO = errno.EPERM
# The write functions whose calls are the steps; a call one of them makes to another is part of
# the outer step. A call that raises changes nothing on disk, so it is no step.
WRITE_FUNCTIONS = (
    "make_folder",
    "write_temp_pieces",
    "link_new",
    "link_file",
    "open_new",
    "write_new",
    "flush_file",
    "flush_directory",
    "remove_file",
    "freshen",
    "move_new",
)
# A flush changes nothing a reader sees; what it makes durable matters only on power loss, which
# WriteShim models from the flush calls themselves. Skipping the drive's flush keeps runs fast.
FLUSH_FUNCTIONS = ("flush_file", "flush_directory")
# A stopped writer never finishes the file it left torn, so the pauses before each reread of it
# only cost time; the rereads themselves still run.
NO_PAUSES = (0.0,) * len(torn_reads.TORN_READ_PAUSES_S)
CRASH_EXAMPLES = 40
NAME = "name"
FOLDER = "folder"
CONTENT = "content"
REMOVED = "removed"

type Changes = Mapping[str, bytes | None]
type Documents = Mapping[str, bytes]
type StrPath = str | os.PathLike[str]


class Crash(BaseException):
    """Stops a commit as a crash would; not an Exception, so no library handler catches it.

    Attributes:
        step: Number of the write call after which the commit stopped.
    """

    def __init__(self, step: int) -> None:
        super().__init__(f"stopped after write call {step}")
        self.step = step


@dataclass(frozen=True)
class Scenario:
    """Holds the commits that set a root up and the commit a crash stops.

    Attributes:
        name: Names the scenario in test output.
        setups: Changes committed one by one before the stopped commit.
        crashed: Changes of the commit the crash stops.
    """

    name: str
    setups: tuple[Changes, ...]
    crashed: Changes


@dataclass(frozen=True)
class Landed:
    """Holds one revision expected in the root.

    Attributes:
        revision: The revision as its write returned it.
        documents: Every document stored at the revision.
    """

    revision: Revision
    documents: Documents


@dataclass(frozen=True)
class Counted:
    """Holds what an unstopped run of a scenario's last commit did.

    Attributes:
        steps: Write calls the commit made.
        landed: The revision it landed, with its documents.
    """

    steps: int
    landed: Landed


@dataclass(frozen=True)
class Change:
    """Holds one change a write call made that a power loss undoes until it is flushed.

    Attributes:
        kind: NAME or FOLDER for a created entry, CONTENT for a new file's bytes, REMOVED for a
            removed file.
        path: The entry the change concerns; for CONTENT, the file's first name.
        data: Bytes of a removed file, which a power loss brings back.
        file: For CONTENT, the number the shim gave the file, which every name of it shares.
    """

    kind: str
    path: Path
    data: bytes = b""
    file: int = 0


SCENARIOS = (
    Scenario("first_commit", (), {"/renders/a.bin": b"a"}),
    Scenario("later_commit", ({"/renders/a.bin": b"a"},), {"/renders/a.bin": b"b"}),
    Scenario(
        "several_blobs",
        ({"/renders/a.bin": b"a"},),
        {"/renders/b.bin": b"b", "/renders/c.bin": b"c", "/renders/d.bin": b"d"},
    ),
    Scenario(
        "replace_and_delete",
        ({"/renders/a.bin": b"a", "/renders/b.bin": b"b", "/renders/c.bin": b"c"},),
        {"/renders/a.bin": b"A", "/renders/b.bin": None, "/renders/c.bin": b"a"},
    ),
)
SCENARIOS_BY_NAME = {scenario.name: scenario for scenario in SCENARIOS}


def _as_path(value: object) -> Path:
    assert isinstance(value, Path), value
    return value


def _missing_folders(path: Path) -> list[Path]:
    """Returns the folders a make_folder call on `path` creates, outermost first."""
    missing: list[Path] = []
    while not os.path.lexists(path):
        missing.append(path)
        path = path.parent
    return missing[::-1]


def _saved_bytes(path: Path) -> bytes | None:
    """Returns the bytes of a regular file about to be removed, None when there is none."""
    if path.is_symlink() or not path.is_file():
        return None
    return path.read_bytes()


class WriteShim:
    """Counts the write calls of a commit, stops it after one of them and models a power loss.

    After the stop every write call raises Crash without running, as the process is gone; a
    descriptor the stopped call opened is closed, as the system closes a dead process's files.

    Attributes:
        stop_after: Number of the call after which the commit stops; None never stops it.
        calls: Write calls completed so far.
        has_crashed: Whether the commit was stopped.
    """

    def __init__(self, stop_after: int | None) -> None:
        self.stop_after = stop_after
        self.calls = 0
        self.has_crashed = False
        self._depth = 0
        self._changes: list[Change] = []
        self._durable: set[int] = set()
        self._files: dict[tuple[int, int], int] = {}
        self._names: dict[int, list[Path]] = {}
        self._nested_flushes: set[tuple[int, int]] = set()

    def install(self, patches: pytest.MonkeyPatch) -> None:
        """Replaces each write function of durable_io with a counting wrapper."""
        for name in WRITE_FUNCTIONS:
            patches.setattr(durable_io, name, self._wrapped(name, getattr(durable_io, name)))

    def _wrapped(self, name: str, real: Callable[..., object]) -> Callable[..., object]:
        def wrapped(*args: object, **kwargs: object) -> object:
            if self.has_crashed:
                raise Crash(self.calls)
            if self._depth:
                if name == "flush_file" and isinstance(args[0], int):
                    self._nested_flushes.add(_inode(os.fstat(args[0])))
                return real(*args, **kwargs)
            self._depth += 1
            try:
                result = self._observed(name, real, args, kwargs)
            finally:
                self._depth -= 1
            self.calls += 1
            if self.calls != self.stop_after:
                return result
            self.has_crashed = True
            if name == "open_new" and isinstance(result, int):
                os.close(result)
            raise Crash(self.calls)

        return wrapped

    def _observed(
        self,
        name: str,
        real: Callable[..., object],
        args: tuple[object, ...],
        kwargs: Mapping[str, object],
    ) -> object:
        """Runs one write call and records the changes a power loss could undo."""
        created = _missing_folders(_as_path(args[0])) if name == "make_folder" else []
        removed = _saved_bytes(_as_path(args[0])) if name in {"remove_file", "move_new"} else None
        self._nested_flushes.clear()
        result = real(*args, **kwargs)
        self._changes.extend(Change(FOLDER, folder) for folder in created)
        if name == "write_temp_pieces":
            temp = _as_path(result)
            self._changes.append(Change(NAME, temp))
            is_flushed = _inode(temp.lstat()) in self._nested_flushes
            self._new_file(temp, temp.lstat(), is_flushed=is_flushed)
        elif name in {"link_new", "link_file", "move_new"} and result is True:
            target = _as_path(args[1])
            self._changes.append(Change(NAME, target))
            self._names.setdefault(self._files.get(_inode(target.lstat()), 0), []).append(target)
        elif name == "open_new" and isinstance(result, int):
            target = _as_path(args[0])
            self._changes.append(Change(NAME, target))
            self._new_file(target, os.fstat(result), is_flushed=False)
        elif name == "flush_file" and isinstance(args[0], int):
            self._mark_file_durable(self._files.get(_inode(os.fstat(args[0])), 0))
        elif name == "flush_directory":
            self._mark_folder_durable(_as_path(args[0]))
        if removed is not None and (name == "remove_file" or result is True):
            self._changes.append(Change(REMOVED, _as_path(args[0]), removed))
        return result

    def _new_file(self, path: Path, info: os.stat_result, *, is_flushed: bool) -> None:
        """Records a new file's bytes, durable only when a flush of the file already ran."""
        number = max(self._names, default=0) + 1
        self._files[_inode(info)] = number
        self._names[number] = [path]
        self._changes.append(Change(CONTENT, path, file=number))
        if is_flushed:
            self._durable.add(len(self._changes) - 1)

    def _mark_file_durable(self, number: int) -> None:
        for index, change in enumerate(self._changes):
            if change.kind == CONTENT and change.file == number:
                self._durable.add(index)

    def _mark_folder_durable(self, folder: Path) -> None:
        for index, change in enumerate(self._changes):
            if change.kind != CONTENT and change.path.parent == folder:
                self._durable.add(index)

    def lose_unflushed(self) -> None:
        """Undoes, newest first, every change no flush made durable, as a power loss would."""
        for index in reversed(range(len(self._changes))):
            if index in self._durable:
                continue
            change = self._changes[index]
            if change.kind == FOLDER and os.path.lexists(change.path):
                shutil.rmtree(change.path)
            elif change.kind == NAME:
                change.path.unlink(missing_ok=True)
            elif change.kind == CONTENT:
                self._empty_file(change.file)
            elif change.kind == REMOVED and not os.path.lexists(change.path):
                change.path.write_bytes(change.data)

    def _empty_file(self, number: int) -> None:
        """Empties a file whose bytes no flush made durable, through any name it still has."""
        for path in self._names[number]:
            if path.is_file() and self._files.get(_inode(path.lstat())) == number:
                path.write_bytes(b"")
                return


def _inode(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _skip_flush(*_args: object, **_options: object) -> None:
    """Stands in for a flush, whose effect only a power loss shows and the shim models."""


def _refuse_link(source: StrPath, _target: StrPath, **_options: bool) -> None:
    """Fails every hard link, as a file system without them does."""
    raise OSError(NO_LINK_ERRNO, os.strerror(NO_LINK_ERRNO), str(source))


def _applied(changes: Changes, before: Documents) -> dict[str, bytes]:
    after = dict(before)
    for path, data in changes.items():
        if data is None:
            after.pop(path, None)
        else:
            after[path] = data
    return after


def _crash_at(scenario: Scenario) -> datetime:
    return FIRST_AT + TICK * (len(scenario.setups) + 1)


def _head_of(history: list[Landed]) -> str:
    return history[-1].revision.id if history else ROOT_REVISION


def _set_up(root: Path, scenario: Scenario) -> list[Landed]:
    """Commits the scenario's setup changes, returning the revisions they landed."""
    repo, history = FileRepository(root), list[Landed]()
    for index, changes in enumerate(scenario.setups):
        before = history[-1].documents if history else {}
        at = FIRST_AT + TICK * (index + 1)
        landed = repo.transaction(SHOW_ID, changes, expected_revision=_head_of(history), at=at)
        history.append(Landed(landed, _applied(changes, before)))
    return history


def _new_root(folder: str, *, has_links: bool, patches: pytest.MonkeyPatch) -> Path:
    """Creates the repository root, refusing hard links in it when `has_links` is false.

    Every flush is skipped for the run, the shim still counting and modelling each one, and a
    torn file is reread without pauses.
    """
    if not has_links:
        patches.setattr(os, "link", _refuse_link)
    for name in FLUSH_FUNCTIONS:
        patches.setattr(durable_io, name, _skip_flush)
    patches.setattr(torn_reads, "TORN_READ_PAUSES_S", NO_PAUSES)
    root = Path(folder) / "repo"
    root.mkdir()
    return root


def _commit_crashed(root: Path, scenario: Scenario, parent: str) -> Revision:
    return FileRepository(root).transaction(
        SHOW_ID, scenario.crashed, expected_revision=parent, at=_crash_at(scenario)
    )


@cache
def _counted(name: str, *, has_links: bool) -> Counted:
    """Runs a scenario's last commit unstopped and counts its write calls."""
    scenario = SCENARIOS_BY_NAME[name]
    with (
        tempfile.TemporaryDirectory(prefix="e8s-crash-") as folder,
        pytest.MonkeyPatch.context() as patches,
    ):
        root = _new_root(folder, has_links=has_links, patches=patches)
        history = _set_up(root, scenario)
        shim = WriteShim(stop_after=None)
        with pytest.MonkeyPatch.context() as shim_patches:
            shim.install(shim_patches)
            revision = _commit_crashed(root, scenario, _head_of(history))
        assert revision.parent == _head_of(history)
        before = history[-1].documents if history else {}
        landed = Landed(revision, _applied(scenario.crashed, before))
        shim.lose_unflushed()
        _require_loads(root, [*history, landed])
    return Counted(shim.calls, landed)


def _require_loads(root: Path, landed: list[Landed]) -> None:
    """Requires a fresh repository on `root` to hold exactly the landed revisions."""
    repo = FileRepository(root)
    assert repo.head(SHOW_ID) == _head_of(landed)
    assert repo.revisions(SHOW_ID) == [entry.revision for entry in landed]
    for entry in landed:
        assert dict(repo.documents(SHOW_ID, entry.revision.id)) == entry.documents


def _require_left_for_gc(root: Path) -> None:
    """Requires fsck to pass, finding nothing but unreachable files and stale temps."""
    report = fsck(root)
    assert report.ok, report.findings
    assert {finding.code for finding in report.findings} <= LEFT_FOR_GC, report.findings


def _torn_kind(path: PurePosixPath) -> str:
    """Names the kind of file a torn-file finding points at."""
    if path.name == LAYOUT_FILE:
        return LAYOUT_FILE
    if path.name == MANIFEST_FILE:
        return MANIFEST_FILE
    assert path.parent.name == LOG_DIR, path
    return LOG_DIR


def _repair_torn_last_file(root: Path, history: list[Landed], *, has_links: bool) -> None:
    """Requires the designed outcome of a torn layout file, manifest or slot, then repairs it.

    Without hard links a file is created in place and then written, so a writer that stops in
    between leaves the layout file, a first manifest or the new slot empty. Reads then refuse
    the root with a pinned code, fsck reports the torn file, and a repair at a time past the
    stale-temp age removes it. With hard links every file appears whole, so nothing is torn.
    """
    failing = [item for item in fsck(root).findings if item.code not in LEFT_FOR_GC]
    if not failing:
        return
    assert not has_links, failing
    assert len(failing) == 1, failing
    torn = failing[0]
    kind = _torn_kind(PurePosixPath(torn.path))
    fsck_code, read_code = TORN_OUTCOMES[kind]
    assert torn.code == fsck_code
    if kind == LOG_DIR:
        assert PurePosixPath(torn.path).name == f"{len(history) + 1:0{SLOT_DIGITS}d}"
    with pytest.raises(StorageError) as refused:
        FileRepository(root).head(SHOW_ID)
    assert refused.value.code == read_code
    repaired = fsck(root, repair=True, confirm=True, at=GC_AFTER_EVERY_FILE)
    assert repaired.repaired == (torn.path,)
    assert repaired.ok, repaired.findings


def _torn_in_place(root: Path) -> list[Path]:
    """Lists the empty blob and record files a writer stopped between creating and writing."""
    return [
        path
        for folder in (OBJECT_DIR, RECORD_DIR)
        for path in sorted(root.glob(f"{SHOWS_DIR}/*/{folder}/*"))
        if path.stat().st_size == 0
    ]


def _retry(root: Path, history: list[Landed], new: Landed) -> Revision:
    changes = {path: new.documents.get(path) for path in new.revision.changed}
    return FileRepository(root).transaction(
        SHOW_ID, changes, expected_revision=_head_of(history), at=new.revision.at
    )


def _require_retry(root: Path, history: list[Landed], new: Landed, *, has_landed: bool) -> None:
    """Retries the stopped commit: it lands, or conflicts when the stopped one had landed."""
    if has_landed:
        with pytest.raises(ConflictError) as lost:
            _retry(root, history, new)
        assert lost.value.code == ErrorCode.CONFLICT
    else:
        assert _retry(root, history, new) == new.revision
    _require_loads(root, [*history, new])
    _require_left_for_gc(root)


def _require_retry_refused_until_gc(root: Path, history: list[Landed], new: Landed) -> None:
    """Requires a retry to fail closed on a torn blob or record until gc removes the file.

    A blob or record file that a writer created and stopped before writing is never written
    over: a reader cannot tell it from one another writer is still filling. So a retry that
    needs the file fails with CORRUPT and lands nothing, and once gc removes the file, which it
    does only when the file is older than the stale-temp age, the retry lands.
    """
    with pytest.raises(StorageError) as refused:
        _retry(root, history, new)
    assert refused.value.code == ErrorCode.CORRUPT
    _require_loads(root, history)
    _require_left_for_gc(root)
    gc(root, at=GC_AFTER_EVERY_FILE, confirm=True)
    _require_retry(root, history, new, has_landed=False)


def _require_recovered(
    root: Path, history: list[Landed], new: Landed, retry_root: Path, *, has_links: bool
) -> None:
    """Requires the reopened root to hold the parent or the new head, and to clean up."""
    _repair_torn_last_file(root, history, has_links=has_links)
    head = FileRepository(root).head(SHOW_ID)
    assert head in {_head_of(history), new.revision.id}
    landed = [*history, new] if head == new.revision.id else history
    _require_loads(root, landed)
    _require_left_for_gc(root)
    is_torn = bool(_torn_in_place(root))
    assert not (is_torn and has_links)
    shutil.copytree(root, retry_root, symlinks=True)
    report = gc(root, at=GC_AFTER_EVERY_FILE, confirm=True)
    assert report.skipped == ()
    after = fsck(root)
    assert after.ok, after.findings
    assert after.findings == ()
    _require_loads(root, landed)
    if is_torn:
        _require_retry_refused_until_gc(retry_root, history, new)
    else:
        _require_retry(retry_root, history, new, has_landed=head == new.revision.id)


def _crash_and_check(scenario: Scenario, step: int, *, has_links: bool, is_power_loss: bool) -> int:
    """Stops the scenario's last commit after write call `step` and checks what is left.

    Args:
        scenario: The commits to run.
        step: Number of the write call after which the commit stops, from 1.
        has_links: Whether the file system can hard-link.
        is_power_loss: Whether the changes no flush made durable are lost too.

    Returns:
        stopped_at: The write call after which the shim stopped the commit.
    """
    expected = _counted(scenario.name, has_links=has_links)
    with (
        tempfile.TemporaryDirectory(prefix="e8s-crash-") as folder,
        pytest.MonkeyPatch.context() as patches,
    ):
        root = _new_root(folder, has_links=has_links, patches=patches)
        history = _set_up(root, scenario)
        shim = WriteShim(stop_after=step)
        with pytest.MonkeyPatch.context() as shim_patches:
            shim.install(shim_patches)
            with pytest.raises(Crash) as stopped:
                _commit_crashed(root, scenario, _head_of(history))
        assert stopped.value.step == step
        if is_power_loss:
            shim.lose_unflushed()
        retry_root = Path(folder) / "retry"
        _require_recovered(root, history, expected.landed, retry_root, has_links=has_links)
    return stopped.value.step


@settings(max_examples=CRASH_EXAMPLES, deadline=None)
@given(
    scenario=st.sampled_from(SCENARIOS),
    has_links=st.booleans(),
    is_power_loss=st.booleans(),
    data=st.data(),
)
def test_crash_at_every_write_step_leaves_old_or_new_head(
    scenario: Scenario, *, has_links: bool, is_power_loss: bool, data: st.DataObject
) -> None:
    """Stops a commit after a drawn write call; the reopened root holds the old or new head."""
    steps = _counted(scenario.name, has_links=has_links).steps
    assert steps > 0
    step = data.draw(st.integers(min_value=1, max_value=steps), label="step")
    _crash_and_check(scenario, step, has_links=has_links, is_power_loss=is_power_loss)


@pytest.mark.parametrize("is_power_loss", [False, True], ids=["process", "power"])
@pytest.mark.parametrize("has_links", [True, False], ids=["links", "no_links"])
@pytest.mark.parametrize("scenario", SCENARIOS, ids=[scenario.name for scenario in SCENARIOS])
def test_every_write_step_of_every_scenario_survives_a_crash(
    scenario: Scenario, *, has_links: bool, is_power_loss: bool
) -> None:
    """Stops each commit after every one of its write calls in turn, counted unstopped first."""
    steps = _counted(scenario.name, has_links=has_links).steps
    assert steps > 0
    stopped = {
        _crash_and_check(scenario, step, has_links=has_links, is_power_loss=is_power_loss)
        for step in range(1, steps + 1)
    }
    assert stopped == set(range(1, steps + 1))
