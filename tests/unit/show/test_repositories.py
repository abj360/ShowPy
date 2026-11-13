#!/usr/bin/env python3

"""
test_repositories.py --- encodes audited repository defects (UP-9.7-02, -12 to -17) as strict xfails

Contains:
    test_regression_memory_commit_has_one_winner_per_parent: UP-9.7-02
    test_regression_repository_refuses_agent_pinned_move: UP-9.7-12
    test_regression_repository_refuses_agent_limits_write: UP-9.7-13
    test_regression_show_ids_differing_by_case_are_distinct_or_rejected: UP-9.7-14
    test_regression_reserved_device_show_ids_are_rejected: UP-9.7-15
    test_regression_trailing_dot_show_id_is_rejected: UP-9.7-16
    test_regression_overlong_show_id_is_a_storage_error: UP-9.7-17
"""

import contextlib
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from showpy.core.errors import ConflictError, LockedError, ShowPyError, StorageError
from showpy.show.file_repository import FileRepository
from showpy.show.files import LIMITS_DOC
from showpy.show.repositories import InMemoryRepository
from showpy.show.storage import Repository
from tests.fixtures.shows import two_set_show
from tests.helpers import pin_spot

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
SHOW_ID = "show-1"
BARRIER_TIMEOUT_S = 1.0
THREAD_COUNT = 8
MAX_SHOW_ID_LENGTH = 300


def _create(repo: Repository, show_id: str = SHOW_ID) -> str:
    return repo.put_show(
        show_id, two_set_show(), expected_revision="", actor="designer", at=AT, message="create"
    ).id


class _SyncedBlobs(dict[str, bytes]):
    """Parks each committer between the head check and the append, so two overlap."""

    def __init__(self) -> None:
        super().__init__()
        self.barrier = threading.Barrier(THREAD_COUNT, timeout=BARRIER_TIMEOUT_S)

    def update(self, *args: Any, **kwargs: Any) -> None:  # Any: mirrors dict.update
        with contextlib.suppress(threading.BrokenBarrierError):
            self.barrier.wait()
        super().update(*args, **kwargs)


def _race_winners() -> tuple[list[str], set[str]]:
    repo = InMemoryRepository()
    backend = cast("Any", repo.backend)
    backend._blobs = _SyncedBlobs()
    winners: list[str] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        try:
            rev = repo.transaction(
                SHOW_ID,
                {f"/renders/{n}.txt": f"x{n}".encode()},
                expected_revision="",
                actor="agent",
                at=AT,
                message=f"w{n}",
            )
        except ConflictError:
            return
        with lock:
            winners.append(rev.id)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(THREAD_COUNT)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return winners, {r.id for r in repo.revisions(SHOW_ID)}


@pytest.mark.defect("UP-9.7-02")
@pytest.mark.xfail(strict=True, reason="UP-9.7-02")
@pytest.mark.timeout(5, method="signal")
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_memory_commit_has_one_winner_per_parent() -> None:
    """Lands exactly one of many threads racing on one parent (audit 9.7 item 2; P0 #3)."""
    winners, stored = _race_winners()
    assert len(winners) == 1
    assert set(winners) <= stored
    assert len(stored) == 1


@pytest.mark.defect("UP-9.7-12")
@pytest.mark.xfail(strict=True, reason="UP-9.7-12")
def test_regression_repository_refuses_agent_pinned_move() -> None:
    """Refuses an agent put_set that moves a pinned spot (audit 9.7 item 7)."""
    repo = InMemoryRepository()
    rev = repo.put_show(
        SHOW_ID,
        pin_spot(two_set_show(), "2", "T1"),
        expected_revision="",
        actor="designer",
        at=AT,
        message="create",
    ).id
    moved = repo.get_set(SHOW_ID, "2").with_placements({"T1": {"x": 7.0}}, force=True)
    with pytest.raises(LockedError):
        repo.put_set(SHOW_ID, moved, expected_revision=rev, actor="agent", at=AT, message="m")


@pytest.mark.defect("UP-9.7-13")
@pytest.mark.xfail(strict=True, reason="UP-9.7-13")
def test_regression_repository_refuses_agent_limits_write() -> None:
    """Refuses an agent transaction that writes the designer's limits file (audit 9.7 item 7)."""
    repo = InMemoryRepository()
    rev = _create(repo)
    with pytest.raises(LockedError):
        repo.transaction(
            SHOW_ID,
            {LIMITS_DOC: b"max_speed: 99"},
            expected_revision=rev,
            actor="agent",
            at=AT,
            message="raise limits",
        )


@pytest.mark.defect("UP-9.7-14")
@pytest.mark.xfail(strict=True, reason="UP-9.7-14")
def test_regression_show_ids_differing_by_case_are_distinct_or_rejected(tmp_path: Path) -> None:
    """Keeps IDs differing only by case from sharing a folder name (audit 9.7 item 8)."""
    root = tmp_path / "repo"
    repo = FileRepository(root)
    _create(repo, "Show")
    try:
        _create(repo, "show")
    except StorageError:
        return
    assert len({p.name.lower() for p in root.iterdir()}) == 2


@pytest.mark.defect("UP-9.7-15")
@pytest.mark.xfail(strict=True, reason="UP-9.7-15")
@pytest.mark.parametrize("show_id", ["CON", "nul"])
def test_regression_reserved_device_show_ids_are_rejected(show_id: str) -> None:
    """Rejects Windows device names as show IDs (audit 9.7 item 8)."""
    with pytest.raises(StorageError):
        _create(InMemoryRepository(), show_id)


@pytest.mark.defect("UP-9.7-16")
@pytest.mark.xfail(strict=True, reason="UP-9.7-16")
def test_regression_trailing_dot_show_id_is_rejected() -> None:
    """Rejects a show ID ending in a dot (audit 9.7 item 8)."""
    with pytest.raises(StorageError):
        _create(InMemoryRepository(), "abc.")


@pytest.mark.defect("UP-9.7-17")
@pytest.mark.xfail(strict=True, reason="UP-9.7-17")
def test_regression_overlong_show_id_is_a_storage_error(tmp_path: Path) -> None:
    """Maps a 300-character show ID to a ShowPyError, not OSError (audit 9.7 item 8)."""
    with pytest.raises(ShowPyError):
        _create(FileRepository(tmp_path / "repo"), "a" * MAX_SHOW_ID_LENGTH)
