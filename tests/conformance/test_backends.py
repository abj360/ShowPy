#!/usr/bin/env python3

"""
test_backends.py --- runs the backend conformance kit on every backend, and on broken adapters

Contains:
    test_memory_backend_conforms: passes every check on the in-memory backend
    test_file_backend_conforms: passes every check on the file backend
    test_file_backend_without_hard_links_conforms: passes every check where os.link fails
    test_a_backend_that_lets_two_writers_win_fails_the_kit: fails one_winner_per_parent
    test_a_backend_that_checks_then_writes_fails_the_race: fails one_winner_per_parent when
        racing writers all pass a head check made before they write
    test_each_broken_rule_fails_its_check: fails the check of each rule an adapter breaks
    test_a_check_that_raises_fails_alone: reports the exception and still runs every check
    test_regression_a_hung_writer_fails_the_race_without_blocking_exit: fails
        one_winner_per_parent within the writer timeout, leaving only daemon threads behind
    test_regression_a_backend_refusing_ids_outside_the_show_id_rule_conforms: passes every check
        when show IDs outside SHOW_ID_PATTERN are refused with StorageError
"""

import errno
import itertools
import os
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, StorageError
from e8s.show import conformance_checks
from e8s.show.backend import RepositoryBackend, conflict_error
from e8s.show.conformance import CHECK_NAMES, check_backend
from e8s.show.document_repository import SHOW_ID_PATTERN
from e8s.show.file_backend import FileBackend
from e8s.show.history_models import Revision
from e8s.show.memory_backend import _MemoryBackend
from e8s.show.storage import ROOT_REVISION
from e8s.show.storage_models import ConformanceReport

RACE_WINDOW_S = 0.05
HUNG_WRITER_TIMEOUT_S = 0.2
RELEASE_TIMEOUT_S = 10.0


def _fresh_roots(tmp_path: Path) -> Callable[[], RepositoryBackend]:
    numbers = itertools.count()
    return lambda: FileBackend(tmp_path / f"root_{next(numbers)}")


def _require_conforms(report: ConformanceReport) -> None:
    assert report.failed == ()
    assert report.passed == CHECK_NAMES


def test_memory_backend_conforms() -> None:
    _require_conforms(check_backend(_MemoryBackend))


def test_file_backend_conforms(tmp_path: Path) -> None:
    _require_conforms(check_backend(_fresh_roots(tmp_path)))


def test_file_backend_without_hard_links_conforms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tried: list[Path] = []

    def refuse(_source: str | Path, target: str | Path, **_kwargs: object) -> None:
        tried.append(Path(target))
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse)
    _require_conforms(check_backend(_fresh_roots(tmp_path)))
    assert tried


class _Adapter:
    """Passes every call to a real memory backend; subclasses break one rule."""

    def __init__(self) -> None:
        self.inner = _MemoryBackend()

    def shows(self) -> list[str]:
        return self.inner.shows()

    def head(self, show_id: str) -> str:
        return self.inner.head(show_id)

    def log(self, show_id: str, *, since: str = ROOT_REVISION) -> list[Revision]:
        return self.inner.log(show_id, since=since)

    def tree(self, show_id: str, revision_id: str) -> Mapping[str, str]:
        return self.inner.tree(show_id, revision_id)

    def blob(self, show_id: str, digest: str) -> bytes:
        return self.inner.blob(show_id, digest)

    def commit(
        self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
    ) -> None:
        self.inner.commit(show_id, revision, tree, blobs)


class _TwoWinners(_Adapter):
    """Tells a writer that lost the race on its parent that its commit landed."""

    def commit(
        self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
    ) -> None:
        try:
            super().commit(show_id, revision, tree, blobs)
        except ConflictError:
            return


def test_a_backend_that_lets_two_writers_win_fails_the_kit() -> None:
    failed = dict(check_backend(_TwoWinners).failed)
    assert "one_winner_per_parent" in failed
    assert "did not raise ConflictError" in failed["one_winner_per_parent"]


class _CheckThenWrite(_Adapter):
    """Compares the parent with the head, waits, then writes, hiding a lost race as a success.

    One writer at a time gets the right answer; racing writers all pass the comparison first.
    """

    def commit(
        self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
    ) -> None:
        if self.head(show_id) != revision.parent:
            raise conflict_error(show_id, revision.parent, self.log(show_id, since=revision.parent))
        time.sleep(RACE_WINDOW_S)
        try:
            super().commit(show_id, revision, tree, blobs)
        except ConflictError:
            return


def test_a_backend_that_checks_then_writes_fails_the_race() -> None:
    failed = dict(check_backend(_CheckThenWrite).failed)
    assert failed["one_winner_per_parent"].endswith("racing commits on one parent landed")


class _KeepsCallerBlobs(_Adapter):
    """Serves blobs from the mappings callers handed in, so a changed bytearray changes them."""

    def __init__(self) -> None:
        super().__init__()
        self.handed: dict[str, bytes] = {}

    def blob(self, show_id: str, digest: str) -> bytes:
        return self.handed.get(digest) or super().blob(show_id, digest)

    def commit(
        self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
    ) -> None:
        super().commit(show_id, revision, tree, blobs)
        self.handed.update(blobs)


class _ListsTriedShows(_Adapter):
    """Lists a show as soon as a commit to it is tried, even one that is refused."""

    def __init__(self) -> None:
        super().__init__()
        self.tried: set[str] = set()

    def shows(self) -> list[str]:
        return sorted(self.tried | set(super().shows()))

    def commit(
        self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
    ) -> None:
        self.tried.add(show_id)
        super().commit(show_id, revision, tree, blobs)


class _NewestFirst(_Adapter):
    def log(self, show_id: str, *, since: str = ROOT_REVISION) -> list[Revision]:
        return super().log(show_id, since=since)[::-1]


class _FirstHead(_Adapter):
    """Reports a show's first revision as its head."""

    def head(self, show_id: str) -> str:
        log = super().log(show_id)
        return log[0].id if log else ROOT_REVISION


class _EmptyForMissingBlobs(_Adapter):
    def blob(self, show_id: str, digest: str) -> bytes:
        try:
            return super().blob(show_id, digest)
        except StorageError:
            return b""


class _SharedBlobs(_Adapter):
    """Serves a blob from whichever show stores it."""

    def blob(self, show_id: str, digest: str) -> bytes:
        for stored in [show_id, *super().shows()]:
            try:
                return super().blob(stored, digest)
            except StorageError:
                continue
        return super().blob(show_id, digest)


class _KeyErrors(_Adapter):
    def tree(self, show_id: str, revision_id: str) -> Mapping[str, str]:
        if revision_id not in {r.id for r in super().log(show_id)}:
            raise KeyError(revision_id)
        return super().tree(show_id, revision_id)


class _FoldsPaths(_Adapter):
    """Stores tree paths case-folded, as a case-insensitive store would."""

    def commit(
        self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
    ) -> None:
        folded = {path.casefold(): digest for path, digest in tree.items()}
        super().commit(show_id, revision, folded, blobs)


class _CutsPaths(_Adapter):
    """Cuts tree paths to 255 characters, as a store with a name limit would."""

    def commit(
        self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
    ) -> None:
        cut = {path[:255]: digest for path, digest in tree.items()}
        super().commit(show_id, revision, cut, blobs)


@pytest.mark.parametrize(
    ("adapter", "check"),
    [
        (_KeepsCallerBlobs, "read_your_writes"),
        (_ListsTriedShows, "failed_commit_changes_nothing"),
        (_NewestFirst, "log_since_is_ordered"),
        (_FirstHead, "head_matches_log"),
        (_EmptyForMissingBlobs, "blob_reads_are_verified"),
        (_SharedBlobs, "ids_are_never_trusted"),
        (_KeyErrors, "errors_are_storage_errors"),
        (_FoldsPaths, "unicode_and_case_paths"),
        (_CutsPaths, "long_names"),
    ],
)
def test_each_broken_rule_fails_its_check(
    adapter: Callable[[], RepositoryBackend], check: str
) -> None:
    report = check_backend(adapter)
    assert check in dict(report.failed)
    assert set(report.passed) | set(dict(report.failed)) == set(CHECK_NAMES)


class _Crashes(_Adapter):
    def shows(self) -> list[str]:
        raise RuntimeError("backend crashed")


def test_a_check_that_raises_fails_alone() -> None:
    report = check_backend(_Crashes)
    failed = dict(report.failed)
    assert failed["read_your_writes"] == "raised RuntimeError: backend crashed"
    assert "one_winner_per_parent" in report.passed
    assert len(report.passed) + len(failed) == len(CHECK_NAMES)


class _HangsInRaces(_Adapter):
    """Blocks every racing commit until the test releases it."""

    def __init__(self, release: threading.Event) -> None:
        super().__init__()
        self.release = release

    def commit(
        self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
    ) -> None:
        if any(data.startswith(b"rival") for data in blobs.values()):
            self.release.wait(RELEASE_TIMEOUT_S)
        super().commit(show_id, revision, tree, blobs)


def test_regression_a_hung_writer_fails_the_race_without_blocking_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conformance_checks, "WRITER_TIMEOUT_S", HUNG_WRITER_TIMEOUT_S)
    release = threading.Event()
    before = set(threading.enumerate())
    try:
        failed = dict(check_backend(lambda: _HangsInRaces(release)).failed)
        stuck = [thread for thread in threading.enumerate() if thread not in before]
        assert "one_winner_per_parent" in failed
        assert stuck
        assert all(thread.daemon for thread in stuck)
    finally:
        release.set()


class _StrictShowIds(_Adapter):
    """Refuses to store a show whose ID breaks the repository's show ID rule."""

    def commit(
        self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
    ) -> None:
        if not SHOW_ID_PATTERN.fullmatch(show_id):
            raise StorageError(
                f"show id {show_id!r} cannot be stored",
                hint="use letters, digits, _, . and - only",
                code=ErrorCode.BAD_PATH,
            )
        super().commit(show_id, revision, tree, blobs)


def test_regression_a_backend_refusing_ids_outside_the_show_id_rule_conforms() -> None:
    _require_conforms(check_backend(_StrictShowIds))
