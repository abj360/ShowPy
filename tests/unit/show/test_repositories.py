#!/usr/bin/env python3

"""
test_repositories.py --- tests show-ID storage safety and repository races; open defects xfail

Contains:
    test_regression_memory_commit_has_one_winner_per_parent: lands exactly one of many threads
        racing on one parent
    test_regression_show_ids_differing_by_case_are_distinct_or_rejected: keeps IDs differing only by
        case from sharing a folder name
    test_regression_reserved_device_show_ids_are_rejected_or_stored_safely: rejects Windows device
        names as show IDs or stores them under another name
    test_regression_new_device_name_show_is_refused_before_writing: refuses to create a show named
        like a Windows device, in memory or on disk, writing nothing
    test_existing_device_name_show_keeps_loading_and_committing: reads and extends a show stored
        under a device name before the refusal existed
    test_regression_device_name_is_refused_before_its_path_is_touched: refuses a new device-name
        show with BAD_PATH even where looking up its folder fails, as Windows' console device does
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
    test_regression_put_forms_refuses_a_form_id_another_set_holds: refuses a form whose ID is
        stored under another set, keeping that set's form
    test_regression_put_show_refuses_sets_differing_only_by_case: refuses a show whose defaulted
        set keys collide case-insensitively, leaving head unchanged
    test_regression_form_op_refuses_a_form_id_another_set_holds: refuses a FormOp whose form ID is
        stored under another set
    test_regression_text_getters_raise_schema_error_for_tampered_bytes: raises SchemaError naming
        the path, not UnicodeDecodeError, for a script, limits or waivers file that is not UTF-8
    test_regression_get_show_reads_no_render_blobs: loads and saves a show through the repository
        and a workspace without reading a render or export blob
    test_regression_in_memory_commit_conflict_under_the_lock: raises ConflictError naming only the
        racing revision when it lands between the head check and the commit
    test_regression_file_conflict_lists_every_revision_since_the_parent: raises ConflictError naming
        every racing revision when several land before a file-backend commit
"""

import contextlib
import errno
import os
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from showpy.core.errors import ConflictError, SchemaError, StorageError
from showpy.core.ids import WINDOWS_DEVICE_NAMES
from showpy.core.util import content_hash
from showpy.show.changes import apply_change
from showpy.show.documents import script_doc
from showpy.show.file_repository import FileRepository
from showpy.show.files import LIMITS_DOC, WAIVERS_DOC
from showpy.show.integrity import blob_digest
from showpy.show.models import FormOp, Revision
from showpy.show.repositories import DocumentRepository, InMemoryRepository, RepositoryBackend
from showpy.show.storage import Repository, load_show, open_workspace, save_show
from tests.fixtures.shows import two_set_show
from tests.helpers import context_entries, fails_after, replaced_form_show

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


@pytest.mark.parametrize("show_id", ["CON", "nul"])
def test_regression_reserved_device_show_ids_are_rejected_or_stored_safely(
    tmp_path: Path, show_id: str
) -> None:
    """Rejects Windows device names as show IDs or stores them under another name (audit 9.7 #8)."""
    created = _created_folders(FileRepository(tmp_path), show_id)
    assert created is None or show_id not in created


DEVICE_SHOW_IDS = ["con", "CON", "nul", "aux.1", "com1", "LPT9.txt"]
DEVICE_NAME_CODE = "BAD_PATH"


@pytest.mark.parametrize("show_id", DEVICE_SHOW_IDS)
@pytest.mark.parametrize("backend", ["memory", "file"])
def test_regression_new_device_name_show_is_refused_before_writing(
    tmp_path: Path, show_id: str, backend: str
) -> None:
    """Refuses a new show named like a Windows device, in memory or on disk, writing nothing."""
    repo: InMemoryRepository | FileRepository = (
        InMemoryRepository() if backend == "memory" else FileRepository(tmp_path)
    )
    with pytest.raises(StorageError) as caught:
        _create(repo, show_id)
    assert caught.value.code == DEVICE_NAME_CODE
    assert caught.value.context == {"show": show_id}
    assert all(name in caught.value.hint for name in WINDOWS_DEVICE_NAMES)
    assert repo.shows() == []
    assert _folders(tmp_path) == set()


def test_regression_device_name_is_refused_before_its_path_is_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuses a new device-name show with BAD_PATH even where looking up its folder fails."""
    repo = FileRepository(tmp_path)
    real_lstat = os.lstat

    def console_lstat(path: str | Path, **kwargs: Any) -> os.stat_result:
        if Path(path).name.casefold() == "con":
            raise OSError(errno.EINVAL, "Invalid argument", str(path))
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", console_lstat)
    with pytest.raises(StorageError) as caught:
        _create(repo, "con")
    assert caught.value.code == DEVICE_NAME_CODE


@pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot create a device-name folder")
def test_existing_device_name_show_keeps_loading_and_committing(tmp_path: Path) -> None:
    """Reads and extends a show stored under a device name before the refusal existed."""
    repo = FileRepository(tmp_path)
    head = _create(repo, SHOW_ID)
    stored = repo.get_show(SHOW_ID)
    (tmp_path / SHOW_ID).rename(tmp_path / "CON")
    assert repo.shows() == ["CON"]
    assert repo.get_show("CON") == stored
    landed = repo.transaction("CON", {"/renders/a.txt": b"a"}, expected_revision=head, at=AT)
    assert repo.head("CON") == landed.id
    assert repo.read("CON", "/renders/a.txt") == b"a"
    with pytest.raises(StorageError) as restarted:
        _create(repo, "CON")
    assert restarted.value.code == DEVICE_NAME_CODE


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


def test_regression_put_forms_refuses_a_form_id_another_set_holds() -> None:
    """Refuses to take over a form that another set lists, and leaves the show unchanged."""
    repo = InMemoryRepository()
    show = replaced_form_show()
    revision = repo.put_show(SHOW_ID, show, expected_revision="", at=AT)
    takeover = show.forms["b"].replace(set="1")
    with pytest.raises(SchemaError):
        repo.put_forms(SHOW_ID, "1", (takeover,), expected_revision=revision.id, at=AT)
    assert repo.get_show(SHOW_ID).forms["b"].set == "2"
    assert [f.id for f in repo.get_forms(SHOW_ID, "2")] == ["b"]


def test_regression_put_show_refuses_sets_differing_only_by_case() -> None:
    """Refuses sets "a" and "A" whose keys would share a folder on a case-insensitive disk."""
    repo = InMemoryRepository()
    revision = repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT)
    base = two_set_show()
    lower = base.sets[0].model_copy(update={"id": "a", "key": None})
    upper = base.sets[1].model_copy(update={"id": "A", "key": None})
    clash = base.model_copy(update={"sets": (lower, upper)})
    with pytest.raises(SchemaError):
        repo.put_show(SHOW_ID, clash, expected_revision=revision.id, at=AT)
    assert repo.head(SHOW_ID) == revision.id


def test_regression_form_op_refuses_a_form_id_another_set_holds() -> None:
    """Refuses a FormOp that would move a form from the set that lists it to another set."""
    show = replaced_form_show()
    takeover = show.forms["b"].replace(set="1")
    with pytest.raises(SchemaError):
        apply_change(show, [FormOp(form=takeover)], force=True)


@pytest.mark.parametrize("path", sorted(TEXT_GETTERS))
def test_regression_text_getters_raise_schema_error_for_tampered_bytes(path: str) -> None:
    """Raises SchemaError naming the path, not UnicodeDecodeError, for bytes that are not UTF-8."""
    repo = _tampered(path, b"\xff\xfe")
    with pytest.raises(SchemaError) as caught:
        TEXT_GETTERS[path](repo)
    assert caught.value.context["path"] == path


class _HookedBackend:
    """Delegates to another backend, records every blob digest read and can run a racer once.

    Attributes:
        inner: Backend every call is delegated to.
        blobs_read: Digests passed to blob(), in call order.
        racer: Runs once at the start of the next commit, before the inner commit.
    """

    def __init__(self, inner: RepositoryBackend) -> None:
        self.inner = inner
        self.blobs_read: list[str] = []
        self.racer: Callable[[], object] | None = None

    def show_ids(self) -> list[str]:
        return self.inner.show_ids()

    def log(self, show_id: str) -> list[Revision]:
        return self.inner.log(show_id)

    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        return self.inner.tree(show_id, revision_id)

    def blob(self, show_id: str, digest: str) -> bytes:
        self.blobs_read.append(digest)
        return self.inner.blob(show_id, digest)

    def commit(
        self, show_id: str, revision: Revision, tree: dict[str, str], blobs: dict[str, bytes]
    ) -> None:
        racer, self.racer = self.racer, None
        if racer is not None:
            racer()
        self.inner.commit(show_id, revision, tree, blobs)


def test_regression_get_show_reads_no_render_blobs() -> None:
    backend = _HookedBackend(InMemoryRepository().backend)
    repo = DocumentRepository(backend)
    media = {"/renders/a.png": b"render", "/exports/b.pdf": b"export"}
    head = repo.transaction(SHOW_ID, media, expected_revision=_create(repo), at=AT).id
    backend.blobs_read.clear()
    show = repo.get_show(SHOW_ID)
    head = repo.put_show(SHOW_ID, show.replace(title="T2"), expected_revision=head, at=AT).id
    ws = open_workspace(repo, SHOW_ID)
    save_show(ws, load_show(ws).replace(title="T3"), AT)
    assert backend.blobs_read
    assert not {blob_digest(raw) for raw in media.values()} & set(backend.blobs_read)


def test_regression_in_memory_commit_conflict_under_the_lock() -> None:
    backend = _HookedBackend(InMemoryRepository().backend)
    repo = DocumentRepository(backend)
    head = _create(repo)
    landed: list[Revision] = []

    def race() -> None:
        landed.append(
            repo.transaction(SHOW_ID, {"/renders/b.txt": b"b"}, expected_revision=head, at=AT)
        )

    backend.racer = race
    with pytest.raises(ConflictError) as caught:
        repo.transaction(SHOW_ID, {"/renders/a.txt": b"a"}, expected_revision=head, at=AT)
    assert [r.id for r in landed] == [repo.head(SHOW_ID)]
    assert caught.value.context["head"] == landed[0].id
    assert [c["id"] for c in context_entries(caught.value, "changes")] == [landed[0].id]
    assert repo.paths(SHOW_ID).count("/renders/a.txt") == 0


def test_regression_file_conflict_lists_every_revision_since_the_parent(tmp_path: Path) -> None:
    """Names both racing revisions, and no older one, when they land before a file commit."""
    backend = _HookedBackend(FileRepository(tmp_path).backend)
    repo = DocumentRepository(backend)
    head = _create(repo)
    landed: list[Revision] = []

    def race() -> None:
        parent = head
        for path in ("/renders/b.txt", "/renders/c.txt"):
            landed.append(repo.transaction(SHOW_ID, {path: b"x"}, expected_revision=parent, at=AT))
            parent = landed[-1].id

    backend.racer = race
    with pytest.raises(ConflictError) as caught:
        repo.transaction(SHOW_ID, {"/renders/a.txt": b"a"}, expected_revision=head, at=AT)
    assert caught.value.context["head"] == landed[-1].id == repo.head(SHOW_ID)
    assert [c["id"] for c in context_entries(caught.value, "changes")] == [r.id for r in landed]
    assert "/renders/a.txt" not in repo.paths(SHOW_ID)
