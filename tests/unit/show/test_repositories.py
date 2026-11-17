#!/usr/bin/env python3

"""
test_repositories.py --- encodes audited repository defects (UP-9.7-02, -14 to -17) as strict xfails

Contains:
    test_regression_memory_commit_has_one_winner_per_parent: lands exactly one of many threads
        racing on one parent
    test_regression_show_ids_differing_by_case_are_distinct_or_rejected: keeps IDs differing only by
        case from sharing a folder name
    test_regression_reserved_device_show_ids_are_rejected_or_stored_safely: rejects Windows device
        names as show IDs or stores them under another name
    test_regression_trailing_dot_show_id_is_rejected_or_stored_safely: rejects a show ID ending in a
        dot or stores it under another name
    test_regression_overlong_show_id_is_rejected_or_stored_safely: rejects a 300-character show ID
        or stores it under a short name
"""

import contextlib
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from showpy.core.errors import ConflictError, StorageError
from showpy.show.file_repository import FileRepository
from showpy.show.repositories import InMemoryRepository
from showpy.show.storage import Repository
from tests.fixtures.shows import two_set_show
from tests.helpers import fails_after

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


@fails_after(5)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_memory_commit_has_one_winner_per_parent() -> None:
    """Lands exactly one of many threads racing on one parent (audit 9.7 item 2; P0 #2)."""
    winners, stored = _race_winners()
    assert len(winners) == 1
    assert set(winners) <= stored
    assert len(stored) == 1


SAFE_NAME_MAX = 255


def _folders(root: Path) -> set[str]:
    return {p.name for p in root.iterdir()} if root.exists() else set()


def _created_folders(repo: FileRepository, show_id: str) -> set[str] | None:
    """Returns the folders one create adds, or None when the repository refused the ID."""
    before = _folders(repo.root)
    try:
        _create(repo, show_id)
    except ConflictError:
        raise  # colliding with an existing show is the defect, not a deliberate refusal
    except StorageError:
        return None
    assert show_id in repo.shows()
    assert repo.head(show_id) != ""
    return _folders(repo.root) - before


@pytest.mark.defect("UP-9.7-14")
@pytest.mark.xfail(strict=True, reason="UP-9.7-14")
def test_regression_show_ids_differing_by_case_are_distinct_or_rejected(tmp_path: Path) -> None:
    """Keeps IDs differing only by case from sharing a folder name (audit 9.7 item 8)."""
    repo = FileRepository(tmp_path)
    first = _created_folders(repo, "Show")
    second = _created_folders(repo, "show")
    if first is None or second is None:
        return
    assert {n.lower() for n in first}.isdisjoint({n.lower() for n in second})
    lowered = [str(p).lower() for p in tmp_path.rglob("*")]
    assert len(lowered) == len(set(lowered))


@pytest.mark.defect("UP-9.7-15")
@pytest.mark.xfail(strict=True, reason="UP-9.7-15")
@pytest.mark.parametrize("show_id", ["CON", "nul"])
def test_regression_reserved_device_show_ids_are_rejected_or_stored_safely(
    tmp_path: Path, show_id: str
) -> None:
    """Rejects Windows device names as show IDs or stores them under another name (audit 9.7 #8)."""
    created = _created_folders(FileRepository(tmp_path), show_id)
    assert created is None or show_id not in created


@pytest.mark.defect("UP-9.7-16")
@pytest.mark.xfail(strict=True, reason="UP-9.7-16")
def test_regression_trailing_dot_show_id_is_rejected_or_stored_safely(tmp_path: Path) -> None:
    """Rejects a show ID ending in a dot or stores it under another name (audit 9.7 item 8)."""
    created = _created_folders(FileRepository(tmp_path), "abc.")
    # Windows silently strips a trailing dot, so "abc" is the raw ID stored unsafely too.
    assert created is None or not any(name.rstrip(". ") == "abc" for name in created)


@pytest.mark.defect("UP-9.7-17")
@pytest.mark.xfail(strict=True, reason="UP-9.7-17")
def test_regression_overlong_show_id_is_rejected_or_stored_safely(tmp_path: Path) -> None:
    """Rejects a 300-character show ID or stores it under a short name (audit 9.7 item 8)."""
    created = _created_folders(FileRepository(tmp_path), "a" * MAX_SHOW_ID_LENGTH)
    assert created is None or all(len(n) <= SAFE_NAME_MAX for n in created)
