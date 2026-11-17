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
    test_regression_get_forms_omits_forms_the_set_no_longer_lists: leaves a stale form out of a
        set's forms
    test_put_forms_keeps_stale_forms_of_the_set_in_the_show: keeps a stale form stored when the
        set's forms are replaced
    test_regression_put_forms_then_get_forms_returns_the_put_forms: lists newly put forms in the
        set that listed others
    test_regression_text_getters_raise_schema_error_for_tampered_bytes: raises SchemaError naming
        the path, not UnicodeDecodeError, for a script, limits or waivers file that is not UTF-8
"""

import contextlib
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from showpy.core.errors import ConflictError, SchemaError, StorageError
from showpy.core.util import content_hash
from showpy.show.documents import script_doc
from showpy.show.file_repository import FileRepository
from showpy.show.files import LIMITS_DOC, WAIVERS_DOC
from showpy.show.integrity import blob_digest
from showpy.show.models import Revision
from showpy.show.repositories import InMemoryRepository
from showpy.show.storage import Repository
from tests.fixtures.shows import two_set_show
from tests.helpers import fails_after, replaced_form_show

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
SHOW_ID = "show-1"
BARRIER_TIMEOUT_S = 1.0
THREAD_COUNT = 8
MAX_SHOW_ID_LENGTH = 300


def _create(repo: Repository, show_id: str = SHOW_ID) -> str:
    return repo.put_show(show_id, two_set_show(), expected_revision="", at=AT).id


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
                at=AT,
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


def test_regression_overlong_show_id_is_rejected_or_stored_safely(tmp_path: Path) -> None:
    """Rejects a 300-character show ID or stores it under a short name (audit 9.7 item 8)."""
    created = _created_folders(FileRepository(tmp_path), "a" * MAX_SHOW_ID_LENGTH)
    assert created is None or all(len(n) <= SAFE_NAME_MAX for n in created)


def test_regression_get_forms_omits_forms_the_set_no_longer_lists() -> None:
    """Leaves a stale form out of a set's forms (ruling L-G3)."""
    repo = InMemoryRepository()
    repo.put_show(SHOW_ID, replaced_form_show(), expected_revision="", at=AT)
    assert [f.id for f in repo.get_forms(SHOW_ID, "2")] == ["b"]


def test_put_forms_keeps_stale_forms_of_the_set_in_the_show() -> None:
    """Keeps a stale form stored when the set's forms are replaced through put_forms."""
    repo = InMemoryRepository()
    revision = repo.put_show(SHOW_ID, replaced_form_show(), expected_revision="", at=AT)
    current = repo.get_forms(SHOW_ID, "2")
    repo.put_forms(SHOW_ID, "2", current, expected_revision=revision.id, at=AT)
    assert sorted(repo.get_show(SHOW_ID).forms) == ["a", "b"]


def test_regression_put_forms_then_get_forms_returns_the_put_forms() -> None:
    """Lists newly put forms in the set that listed others; stale forms stay stored."""
    repo = InMemoryRepository()
    show = replaced_form_show()
    revision = repo.put_show(SHOW_ID, show, expected_revision="", at=AT)
    fresh = show.forms["b"].replace(id="c")
    repo.put_forms(SHOW_ID, "2", (fresh,), expected_revision=revision.id, at=AT)
    assert [f.id for f in repo.get_forms(SHOW_ID, "2")] == ["c"]
    assert "a" in repo.get_show(SHOW_ID).forms


def _tampered(path: str, raw: bytes) -> InMemoryRepository:
    """Stores `raw` at `path` through the backend, past the commit check, as a tampered repo."""
    repo = InMemoryRepository()
    head = _create(repo)
    tree = {**repo.backend.tree(SHOW_ID, head), path: blob_digest(raw)}
    revision = Revision(id=content_hash(tree), parent=head, at=AT, changed=(path,))
    repo.backend.commit(SHOW_ID, revision, tree, {blob_digest(raw): raw})
    return repo


TEXT_GETTERS: dict[str, Callable[[InMemoryRepository], str]] = {
    script_doc("1"): lambda repo: repo.get_script(SHOW_ID, "1"),
    LIMITS_DOC: lambda repo: repo.get_limits(SHOW_ID),
    WAIVERS_DOC: lambda repo: repo.get_waivers(SHOW_ID),
}


@pytest.mark.parametrize("path", sorted(TEXT_GETTERS))
def test_regression_text_getters_raise_schema_error_for_tampered_bytes(path: str) -> None:
    """Raises SchemaError naming the path, not UnicodeDecodeError, for bytes that are not UTF-8."""
    repo = _tampered(path, b"\xff\xfe")
    with pytest.raises(SchemaError) as caught:
        TEXT_GETTERS[path](repo)
    assert caught.value.context["path"] == path
