#!/usr/bin/env python3

"""
test_torn_reads.py --- tests how the file backend reads a slot another writer may still be filling

Contains:
    test_regression_torn_slot_is_reported_with_the_repair_command: raises TORN_SLOT for an empty
        slot, with a hint that names the fsck repair call
    test_torn_slot_is_reread_three_times_then_reported: rereads an empty, cut-short or unfinished
        slot three times, pausing before each, then raises TORN_SLOT
    test_slot_finished_between_rereads_reads_as_landed: reads a slot whose writer finishes it
        during the first pause
    test_finished_line_that_fails_a_check_is_corrupt_at_once: reports a complete but wrong slot
        as CORRUPT without a reread
    test_torn_last_slot_is_never_read_as_the_end_of_history: fails a cold and a warm head and a
        log closed instead of reporting the slot before it as the head
    test_commit_after_a_torn_slot_fails_closed: refuses to commit on the parent of a torn slot
"""

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.canonical import jcs
from e8s.core.errors import StorageError
from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import SLOT_DIGITS, FileBackend
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder

AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SHOW_ID = "torn"
RENDER = "/renders/a.txt"
REREADS = 3
REPAIR_CALL = "fsck(root, repair=True, confirm=True)"


def _change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    data = f"render {n}".encode()
    revision = Revision(
        id=revision_id(parent, AT, {RENDER: digest(data)}), parent=parent, at=AT, changed=(RENDER,)
    )
    return revision, {RENDER: digest(data)}, {digest(data): data}


def _landed(backend: FileBackend, count: int) -> list[Revision]:
    landed: list[Revision] = []
    parent = ""
    for n in range(count):
        revision, tree, blobs = _change(parent, n)
        backend.commit(SHOW_ID, revision, tree, blobs)
        landed.append(revision)
        parent = revision.id
    return landed


def _slot(root: Path, seq: int) -> Path:
    return root / "shows" / show_folder(SHOW_ID) / "log" / f"{seq:0{SLOT_DIGITS}d}"


def _record_pauses(monkeypatch: pytest.MonkeyPatch, on_pause: Callable[[], object]) -> list[float]:
    """Replaces time.sleep with a recorder that runs `on_pause` instead of waiting."""
    pauses: list[float] = []

    def pause(seconds: float) -> None:
        pauses.append(seconds)
        on_pause()

    monkeypatch.setattr(time, "sleep", pause)
    return pauses


def _record_opens(monkeypatch: pytest.MonkeyPatch, path: Path) -> list[Path]:
    """Records every os.open of `path`, which each read of that file makes once."""
    opens: list[Path] = []
    real_open = os.open

    def recorded(name: str | Path, flags: int, *args: int, **kwargs: int) -> int:
        if Path(name) == path:
            opens.append(path)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recorded)
    return opens


def test_regression_torn_slot_is_reported_with_the_repair_command(tmp_path: Path) -> None:
    """Raises TORN_SLOT for an empty slot, naming the fsck call that removes a torn last slot."""
    _landed(FileBackend(tmp_path), 2)
    _slot(tmp_path, 2).write_bytes(b"")
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == "TORN_SLOT"
    assert REPAIR_CALL in caught.value.hint
    assert caught.value.context["file"] == f"{2:0{SLOT_DIGITS}d}"
    assert caught.value.context["show"] == SHOW_ID


TORN_FORMS: dict[str, Callable[[bytes], bytes]] = {
    "empty": lambda _: b"",
    "cut short": lambda full: full[: len(full) // 2],
    "no newline": lambda full: full[:-1],
}


@pytest.mark.parametrize("form", sorted(TORN_FORMS))
def test_torn_slot_is_reread_three_times_then_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    _landed(FileBackend(tmp_path), 2)
    slot = _slot(tmp_path, 2)
    slot.write_bytes(TORN_FORMS[form](slot.read_bytes()))
    backend = FileBackend(tmp_path)
    pauses = _record_pauses(monkeypatch, lambda: None)
    opens = _record_opens(monkeypatch, slot)
    with pytest.raises(StorageError) as caught:
        backend.log(SHOW_ID)
    assert caught.value.code == "TORN_SLOT"
    assert len(pauses) == REREADS
    assert all(seconds > 0 for seconds in pauses)
    assert len(opens) == 1 + REREADS


def test_slot_finished_between_rereads_reads_as_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    landed = _landed(FileBackend(tmp_path), 2)
    slot = _slot(tmp_path, 2)
    full = slot.read_bytes()
    slot.write_bytes(full[:10])
    pauses = _record_pauses(monkeypatch, lambda: slot.write_bytes(full))
    assert FileBackend(tmp_path).log(SHOW_ID) == landed
    assert len(pauses) == 1


FINISHED_BUT_WRONG: dict[str, Callable[[Revision], bytes]] = {
    "not json": lambda _: b"{\n",
    "not canonical": lambda revision: jcs(revision.to_json()).replace(b'","', b'", "', 1) + b"\n",
    "missing keys": lambda _: b"{}\n",
}


@pytest.mark.parametrize("wrong", sorted(FINISHED_BUT_WRONG))
def test_finished_line_that_fails_a_check_is_corrupt_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrong: str
) -> None:
    landed = _landed(FileBackend(tmp_path), 2)
    _slot(tmp_path, 2).write_bytes(FINISHED_BUT_WRONG[wrong](landed[1]))
    pauses = _record_pauses(monkeypatch, lambda: None)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == "CORRUPT"
    assert pauses == []


def test_torn_last_slot_is_never_read_as_the_end_of_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warm = FileBackend(tmp_path)
    _landed(warm, 1)
    _slot(tmp_path, 2).write_bytes(b"")
    _record_pauses(monkeypatch, lambda: None)
    reads: list[Callable[[], object]] = [
        lambda: FileBackend(tmp_path).head(SHOW_ID),
        lambda: warm.head(SHOW_ID),
        lambda: FileBackend(tmp_path).log(SHOW_ID),
    ]
    for read in reads:
        with pytest.raises(StorageError) as caught:
            read()
        assert caught.value.code == "TORN_SLOT"


def test_commit_after_a_torn_slot_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FileBackend(tmp_path)
    [first] = _landed(backend, 1)
    _slot(tmp_path, 2).write_bytes(b"")
    _record_pauses(monkeypatch, lambda: None)
    revision, tree, blobs = _change(first.id, 1)
    with pytest.raises(StorageError) as caught:
        backend.commit(SHOW_ID, revision, tree, blobs)
    assert caught.value.code == "TORN_SLOT"
    assert _slot(tmp_path, 2).read_bytes() == b""
