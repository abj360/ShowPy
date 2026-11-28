#!/usr/bin/env python3

"""
test_paused_writer.py --- tests commits that meet a file another writer created but has not written

Without hard links a writer creates each file under its final name and writes it with its next
call. These tests stop one writer between the two calls, run a second commit against the empty
file, and stand for the wait with a simulated clock, so no outcome depends on real timing.

Contains:
    test_regression_writer_stalled_for_a_second_is_waited_for: lands one of two commits and gives
        the other a ConflictError naming it when the writer of a shared layout file, manifest,
        blob, record or slot stalls for a second between creating and writing it
    test_file_left_unwritten_fails_closed_after_every_pause: fails the second commit closed after
        one bounded run of pauses while the file stays empty, then lands the stalled writer
"""

import errno
import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.errors import ConflictError, E8sError, StorageError
from e8s.show import durable_io, torn_reads
from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import FileBackend
from e8s.show.fsck import fsck
from e8s.show.history_models import Revision
from e8s.show.storage import ROOT_REVISION

AT = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
SHOW_ID = "paused"
SHARED = b"shared"
SEED = b"seed"
WRITER_PATH = "/renders/writer.txt"
OTHER_PATH = "/renders/other.txt"
SEED_PATH = "/renders/seed.txt"
# A loaded CI runner held a writer back for longer than 0.1 s between creating a blob and writing
# it; one second stands for such a stall.
STALL_S = 1.0
EVENT_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class _Case:
    """Names the file the stalled writer holds empty and the commits that meet it.

    Attributes:
        is_stalled_on: Tells whether a file the writer writes is the one it stalls on.
        is_first_commit: Whether both commits land on an empty show, not on a seeded revision.
        is_same_revision: Whether the second commit is the writer's own revision, so they share
            the record.
        torn_code: Code the second commit raises when the file stays empty past every pause.
    """

    is_stalled_on: Callable[[Path], bool]
    is_first_commit: bool = False
    is_same_revision: bool = False
    torn_code: str = "CORRUPT"


CASES = {
    "layout": _Case(lambda path: path.name == "layout.json", is_first_commit=True),
    "manifest": _Case(lambda path: path.name == "show.json", is_first_commit=True),
    "blob": _Case(lambda path: path.name == digest(SHARED)),
    "record": _Case(lambda path: path.parent.name == "records", is_same_revision=True),
    "slot": _Case(lambda path: path.parent.name == "log", torn_code="TORN_SLOT"),
}


@dataclass
class _StalledWriter:
    """Holds one writer between creating its stalled file and writing it, on a simulated clock.

    Attributes:
        created: Set once the writer has created the file and stopped.
        release: Lets the writer write the file.
        written: Set once the writer has written the file.
        waited: Seconds each simulated pause of the main thread stood for, before the release.
        outcome: The writer's commit result: True when it landed, else the error it raised.
    """

    created: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    written: threading.Event = field(default_factory=threading.Event)
    waited: list[float] = field(default_factory=list)
    outcome: list[bool | E8sError] = field(default_factory=list)


def _without_hard_links(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_source: str | Path, _target: str | Path, **_kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse)


def _change(parent: str, path: str) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    tree = {path: digest(SHARED)}
    if parent != ROOT_REVISION:
        tree[SEED_PATH] = digest(SEED)
    changed = {path: digest(SHARED)}
    revision = Revision(id=revision_id(parent, AT, changed), parent=parent, at=AT, changed=(path,))
    return revision, tree, {digest(SHARED): SHARED}


def _seeded(root: Path, case: _Case) -> str:
    if case.is_first_commit:
        return ROOT_REVISION
    tree = {SEED_PATH: digest(SEED)}
    seed = Revision(id=revision_id("", AT, tree), parent="", at=AT, changed=(SEED_PATH,))
    FileBackend(root).commit(SHOW_ID, seed, tree, {digest(SEED): SEED})
    return seed.id


def _stall(
    monkeypatch: pytest.MonkeyPatch, case: _Case, writer: threading.Thread, stall_s: float
) -> _StalledWriter:
    """Stops `writer` before it writes its stalled file and simulates the main thread's pauses.

    Once the pauses add up to `stall_s`, the writer writes the file before the pause ends. Every
    later pause is real, so a reread of a file the released writer has not finished yet still
    waits for it.
    """
    stalled = _StalledWriter()
    real_write_new, real_sleep = durable_io.write_new, time.sleep

    def write_new(descriptor: int, target: Path, data: bytes) -> None:
        is_stalled = threading.current_thread() is writer and case.is_stalled_on(target)
        if is_stalled and not stalled.created.is_set():
            stalled.created.set()
            stalled.release.wait(EVENT_TIMEOUT_S)
        real_write_new(descriptor, target, data)
        if is_stalled:
            stalled.written.set()

    def sleep(seconds: float) -> None:
        if threading.current_thread() is not threading.main_thread() or stalled.release.is_set():
            real_sleep(seconds)
            return
        stalled.waited.append(seconds)
        if sum(stalled.waited) >= stall_s and not stalled.release.is_set():
            stalled.release.set()
            stalled.written.wait(EVENT_TIMEOUT_S)

    monkeypatch.setattr(durable_io, "write_new", write_new)
    monkeypatch.setattr(time, "sleep", sleep)
    return stalled


def _commit(root: Path, change: tuple[Revision, dict[str, str], dict[str, bytes]]) -> bool:
    FileBackend(root).commit(SHOW_ID, *change)
    return True


def _start_writer(
    monkeypatch: pytest.MonkeyPatch, root: Path, case: _Case, parent: str, stall_s: float
) -> tuple[threading.Thread, _StalledWriter]:
    """Starts the writer's commit and returns once it has created its stalled file."""
    change = _change(parent, WRITER_PATH)

    def run() -> None:
        try:
            stalled.outcome.append(_commit(root, change))
        except E8sError as error:
            stalled.outcome.append(error)

    writer = threading.Thread(target=run, daemon=True)
    stalled = _stall(monkeypatch, case, writer, stall_s)
    writer.start()
    assert stalled.created.wait(EVENT_TIMEOUT_S)
    return writer, stalled


def _second_commit(root: Path, case: _Case, parent: str) -> bool | E8sError:
    change = _change(parent, WRITER_PATH if case.is_same_revision else OTHER_PATH)
    try:
        return _commit(root, change)
    except E8sError as error:
        return error


def _finish(writer: threading.Thread, stalled: _StalledWriter) -> bool | E8sError:
    stalled.release.set()
    writer.join(EVENT_TIMEOUT_S)
    assert not writer.is_alive()
    [outcome] = stalled.outcome
    return outcome


def _winner(root: Path, parent: str) -> str:
    """Returns the one revision that landed on `parent`, checking the root is clean."""
    log = FileBackend(root).log(SHOW_ID)
    assert log[-1].parent == parent
    assert len(log) == (1 if parent == ROOT_REVISION else 2)
    report = fsck(root)
    assert report.ok, report.findings
    assert report.findings == ()
    return log[-1].id


@pytest.mark.parametrize("kind", sorted(CASES))
def test_regression_writer_stalled_for_a_second_is_waited_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    case = CASES[kind]
    _without_hard_links(monkeypatch)
    parent = _seeded(tmp_path, case)
    writer, stalled = _start_writer(monkeypatch, tmp_path, case, parent, STALL_S)
    try:
        second = _second_commit(tmp_path, case, parent)
    finally:
        first = _finish(writer, stalled)
    winner = _winner(tmp_path, parent)
    outcomes = [first, second]
    assert outcomes.count(True) == 1, outcomes
    [lost] = [outcome for outcome in outcomes if outcome is not True]
    assert isinstance(lost, ConflictError), lost
    assert lost.context["head"] == winner


@pytest.mark.parametrize("kind", sorted(CASES))
def test_file_left_unwritten_fails_closed_after_every_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    case = CASES[kind]
    _without_hard_links(monkeypatch)
    parent = _seeded(tmp_path, case)
    writer, stalled = _start_writer(monkeypatch, tmp_path, case, parent, math.inf)
    try:
        second = _second_commit(tmp_path, case, parent)
    finally:
        first = _finish(writer, stalled)
    assert isinstance(second, StorageError), second
    assert second.code == case.torn_code
    assert stalled.waited == list(torn_reads.TORN_READ_PAUSES_S)
    assert first is True
    _winner(tmp_path, parent)
