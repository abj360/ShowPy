#!/usr/bin/env python3

"""
test_crash_repair_hints.py --- tests that a file a crashed writer left torn fails with a hint
that names the repair, and that the repair works

Without hard links a commit or an upgrade creates each file under its final name and then
writes it, so one stopped in between leaves the file empty or cut short. Each test stops a real
writer there, checks the CORRUPT error and its hint, then runs the call the hint spells, taken
from the hint's own text.

Contains:
    Crash: stops a writer the way a killed process does
    StubClock: stands in for datetime in a hint's call, so its now is the tests' fixed time
    test_regression_torn_blob_or_record_hint_names_the_gc_that_clears_it: fails the retry of a
        commit that stopped while writing a blob or record with a hint naming fsck and gc, which
        removes the file once it is a day old so the retry lands
    test_damaged_blob_or_record_keeps_the_restore_hint: keeps the backup hint for a blob or
        record that is no shorter start of what the commit writes
    test_regression_truncated_reachable_blob_hint_falls_back_to_a_backup: names the backup for a
        cut-short blob a slot reaches, which fsck reports as BAD_BLOB and gc never removes
    test_regression_torn_layout_or_manifest_hint_names_the_fsck_repair_that_clears_it: fails a
        read of a root whose first commit stopped while writing layout.json or show.json with a
        hint naming the fsck repair, which removes the file once it is a day old
    test_torn_layout_or_manifest_hint_says_when_the_repair_does_not_apply: keeps a torn layout
        file of a root with a show folder and a torn manifest of a show with a slot, as the hint
        says, pointing at a backup instead
    test_regression_layout_torn_by_a_stopped_upgrade_hint_names_the_upgrade_again: names
        deleting the target and upgrading again, which then lands the upgrade
    test_damaged_layout_or_manifest_keeps_the_restore_hint: keeps the backup hint for a layout
        file or manifest that is no shorter start of what a commit writes
    test_every_library_text_saying_a_day_matches_the_stale_temp_age: ties the "a day" every hint
        and message states to STALE_TEMP_AGE
"""

import ast
import errno
import json
import os
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

import pytest

import e8s
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show import durable_io
from e8s.show.backend_checks import CORRUPT_HINT
from e8s.show.commit_writes import STALE_TEMP_AGE
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import MANIFEST_FILE, FileBackend
from e8s.show.file_repository import FileRepository
from e8s.show.fsck import fsck
from e8s.show.fsck_findings import BAD_BLOB, UNREACHABLE
from e8s.show.gc import gc
from e8s.show.history_models import Revision
from e8s.show.layout import LAYOUT_FILE
from e8s.show.storage_models import FsckReport, GcReport
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.fsck_repos import (
    AT,
    DAY,
    SHOW_ID,
    STATIC_BYTES,
    age_by,
    blob_path,
    codes,
    landed_revisions,
    numbered_change,
    record_path,
    relative,
    show_path,
)

pytestmark = pytest.mark.usefixtures("no_reread_pauses")

HOUR = timedelta(hours=1)
# The share of a file's bytes the stopped writer got onto the disk.
WRITTEN_SHARES = [0.0, 0.5]
WRITTEN_IDS = ["empty", "cut_short"]
TORN_KINDS = ["blob", "record"]
FIRST_FILES = [LAYOUT_FILE, MANIFEST_FILE]
NEW_BLOB = b"render 1"
TORN_PREFIX_BYTES = 5
BACKUP = "restore the show folder from a backup"
# A call a hint spells: fsck or gc on the root, whose arguments may hold one call of their own.
HINT_CALL = re.compile(r"\b(?:fsck|gc)\(root(?:[^()]|\([^()]*\))*\)")
LIBRARY = Path(e8s.__file__).parent
STALE_AGE_WORDS = "a day"
SAYS_A_DAY = {"backend_checks.py", "commit_writes.py", "fsck.py", "layout.py", "manifests.py"}

type Change = tuple[Revision, dict[str, str], dict[str, bytes]]
type StrPath = str | os.PathLike[str]


class Crash(BaseException):
    """Stops a writer the way a killed process does; no library handler catches it."""


class StubClock:
    """Stands in for datetime in a hint's call, so its now is the tests' fixed time."""

    @staticmethod
    def now(zone: tzinfo) -> datetime:
        return AT.astimezone(zone)


def _refuse_link(source: StrPath, _target: StrPath, **_options: bool) -> None:
    raise OSError(errno.EPERM, os.strerror(errno.EPERM), str(source))


def _stopped_writing(
    run: Callable[[], object], is_target: Callable[[Path], bool], share: float
) -> None:
    """Runs a writer without hard links and stops it inside the write of one file.

    Args:
        run: The commit or upgrade to stop.
        is_target: Tells the file whose write stops it.
        share: Share of the file's bytes written before the stop.
    """
    write_new = durable_io.write_new

    def stopping(descriptor: int, target: Path, data: bytes) -> None:
        if not is_target(target):
            write_new(descriptor, target, data)
            return
        os.write(descriptor, data[: int(len(data) * share)])
        raise Crash

    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(os, "link", _refuse_link)
        patches.setattr(durable_io, "write_new", stopping)
        with pytest.raises(Crash):
            run()


def _commit_stopped_writing(root: Path, change: Change, torn: Path, share: float) -> None:
    def commit() -> None:
        FileBackend(root).commit(SHOW_ID, *change)

    _stopped_writing(commit, lambda target: target == torn, share)


def _run_hint_call(hint: str, root: Path) -> object:
    """Runs the one call in a hint that confirms a change, exactly as the hint spells it.

    Args:
        hint: The error's hint.
        root: Repository root the call's `root` names.

    Returns:
        report: What the call returned.
    """
    calls = [call for call in HINT_CALL.findall(hint) if "confirm=True" in call]
    assert len(calls) == 1, hint
    names = {"fsck": fsck, "gc": gc, "root": root, "datetime": StubClock, "UTC": UTC}
    return eval(calls[0], names)


def _repaired(hint: str, root: Path) -> tuple[str, ...]:
    report = _run_hint_call(hint, root)
    assert isinstance(report, FsckReport)
    return report.repaired


def _torn_path(root: Path, kind: str, change: Change) -> Path:
    """Names the new blob or the record of revision 1, which only its commit writes."""
    if kind == "record":
        return record_path(root, change[0])
    return blob_path(root, NEW_BLOB)


def _first_file_path(root: Path, name: str) -> Path:
    """Names the layout file or the manifest a show's first commit writes."""
    return root / LAYOUT_FILE if name == LAYOUT_FILE else show_path(root) / MANIFEST_FILE


def _refused_read(root: Path) -> StorageError:
    with pytest.raises(StorageError) as caught:
        FileRepository(root).head(SHOW_ID)
    assert caught.value.code == ErrorCode.CORRUPT
    return caught.value


def _refused_retry(root: Path, change: Change) -> StorageError:
    with pytest.raises(StorageError) as caught:
        FileBackend(root).commit(SHOW_ID, *change)
    assert caught.value.code == ErrorCode.CORRUPT
    return caught.value


@pytest.mark.parametrize("share", WRITTEN_SHARES, ids=WRITTEN_IDS)
@pytest.mark.parametrize("kind", TORN_KINDS)
def test_regression_torn_blob_or_record_hint_names_the_gc_that_clears_it(
    tmp_path: Path, kind: str, share: float
) -> None:
    landed = landed_revisions(tmp_path, 1)
    change = numbered_change(landed[-1].id, 1)
    torn = _torn_path(tmp_path, kind, change)
    _commit_stopped_writing(tmp_path, change, torn, share)
    assert torn.exists()
    hint = _refused_retry(tmp_path, change).hint
    assert "fsck(root)" in hint
    assert UNREACHABLE in hint
    assert BACKUP in hint
    assert (UNREACHABLE, relative(tmp_path, torn)) in codes(tmp_path)
    age_by(torn, STALE_TEMP_AGE - HOUR)
    assert isinstance(_run_hint_call(hint, tmp_path), GcReport)
    assert torn.exists()
    age_by(torn, STALE_TEMP_AGE + HOUR)
    _run_hint_call(hint, tmp_path)
    assert not torn.exists()
    FileBackend(tmp_path).commit(SHOW_ID, *change)
    assert FileBackend(tmp_path).log(SHOW_ID) == [*landed, change[0]]
    assert codes(tmp_path) == []


@pytest.mark.parametrize("kind", TORN_KINDS)
def test_damaged_blob_or_record_keeps_the_restore_hint(tmp_path: Path, kind: str) -> None:
    landed = landed_revisions(tmp_path, 1)
    change = numbered_change(landed[-1].id, 1)
    damaged = _torn_path(tmp_path, kind, change)
    damaged.parent.mkdir(parents=True, exist_ok=True)
    damaged.write_bytes(b"not what this commit writes")
    assert _refused_retry(tmp_path, change).hint == CORRUPT_HINT


@pytest.mark.parametrize("share", WRITTEN_SHARES, ids=WRITTEN_IDS)
def test_regression_truncated_reachable_blob_hint_falls_back_to_a_backup(
    tmp_path: Path, share: float
) -> None:
    landed = landed_revisions(tmp_path, 1)
    reached = blob_path(tmp_path, STATIC_BYTES)
    reached.write_bytes(STATIC_BYTES[: int(len(STATIC_BYTES) * share)])
    hint = _refused_retry(tmp_path, numbered_change(landed[-1].id, 1)).hint
    assert (BAD_BLOB, relative(tmp_path, reached)) in codes(tmp_path)
    assert BACKUP in hint


@pytest.mark.parametrize("share", WRITTEN_SHARES, ids=WRITTEN_IDS)
@pytest.mark.parametrize("name", FIRST_FILES)
def test_regression_torn_layout_or_manifest_hint_names_the_fsck_repair_that_clears_it(
    tmp_path: Path, name: str, share: float
) -> None:
    change = numbered_change("", 0)
    torn = _first_file_path(tmp_path, name)
    _commit_stopped_writing(tmp_path, change, torn, share)
    hint = _refused_read(tmp_path).hint
    age_by(torn, STALE_TEMP_AGE - HOUR)
    assert _repaired(hint, tmp_path) == ()
    age_by(torn, STALE_TEMP_AGE + HOUR)
    assert _repaired(hint, tmp_path) == (relative(tmp_path, torn),)
    assert fsck(tmp_path).ok
    FileBackend(tmp_path).commit(SHOW_ID, *change)
    assert FileRepository(tmp_path).head(SHOW_ID) == change[0].id
    assert fsck(tmp_path).ok


@pytest.mark.parametrize(
    ("name", "condition", "backup"),
    [
        (LAYOUT_FILE, "while the root holds no show folder", "restore layout.json from a backup"),
        (MANIFEST_FILE, "while the show has no slot", BACKUP),
    ],
)
def test_torn_layout_or_manifest_hint_says_when_the_repair_does_not_apply(
    tmp_path: Path, name: str, condition: str, backup: str
) -> None:
    landed_revisions(tmp_path, 1)
    torn = _first_file_path(tmp_path, name)
    cut = torn.read_bytes()[:TORN_PREFIX_BYTES]
    torn.write_bytes(cut)
    age_by(torn, 2 * DAY)
    hint = _refused_read(tmp_path).hint
    assert condition in hint
    assert backup in hint
    assert _repaired(hint, tmp_path) == ()
    assert torn.read_bytes() == cut


def _layout_1_root(source: Path) -> str:
    """Builds a layout-1 root holding one show with one empty revision."""
    revision = revision_id("", AT, {})
    stored = {"revision": {"id": revision, "parent": "", "at": AT.isoformat()}, "tree": {}}
    for name in ("next", "trees"):
        (source / SHOW_ID / name).mkdir(parents=True)
    (source / SHOW_ID / "next" / "root").write_text(revision)
    (source / SHOW_ID / "trees" / f"{revision}.json").write_text(json.dumps(stored))
    return revision


@pytest.mark.parametrize("share", WRITTEN_SHARES, ids=WRITTEN_IDS)
def test_regression_layout_torn_by_a_stopped_upgrade_hint_names_the_upgrade_again(
    tmp_path: Path, share: float
) -> None:
    source, target = tmp_path / "old", tmp_path / "new"
    revision = _layout_1_root(source)

    def upgrade() -> None:
        upgrade_repository(source, target)

    _stopped_writing(upgrade, lambda path: path == target / LAYOUT_FILE, share)
    hint = _refused_read(target).hint
    assert "upgrade_repository" in hint
    assert "delete the target" in hint
    age_by(target / LAYOUT_FILE, 2 * DAY)
    assert _repaired(hint, target) == ()
    shutil.rmtree(target)
    upgrade_repository(source, target)
    assert FileRepository(target).head(SHOW_ID) == revision


@pytest.mark.parametrize("name", FIRST_FILES)
def test_damaged_layout_or_manifest_keeps_the_restore_hint(tmp_path: Path, name: str) -> None:
    landed_revisions(tmp_path, 1)
    _first_file_path(tmp_path, name).write_bytes(b"not what a commit writes")
    hint = _refused_read(tmp_path).hint
    assert "from a backup" in hint
    assert "fsck(" not in hint


def _says_a_day(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and STALE_AGE_WORDS in node.value
        for node in ast.walk(tree)
    )


def test_every_library_text_saying_a_day_matches_the_stale_temp_age() -> None:
    sayers = {path.name for path in LIBRARY.rglob("*.py") if _says_a_day(path)}
    assert sayers >= SAYS_A_DAY
    assert STALE_TEMP_AGE == DAY
