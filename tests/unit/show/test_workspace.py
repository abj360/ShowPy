#!/usr/bin/env python3

"""
test_workspace.py --- tests a workspace's reads, writes and normalizing rewrite over stored trees

Contains:
    test_tree_at_reads_a_revision_once: lists a revision's documents once while two revisions are
        read in turn
    test_workspace_total_bytes_cap: keeps cached blob bytes under the cap, never storing a blob
        larger than it
    test_regression_write_with_an_older_expected_revision_normalizes_that_revision: stages a write
        on the show at the expected revision, not the one the workspace was opened at
    test_get_script_on_a_v1_head_migrates_once: migrates a v1 tree once across reads and a write
    test_regression_v1_workspace_reads_a_file_at_a_renamed_set_folder: reads `/sets/con_/set.py`
        of a v1 tree whose stored folder is `/sets/con/`
    test_canonical_revisions_are_bounded: remembers at most the cap of canonical revisions
    test_regression_unlisted_layout1_set_document_does_not_keep_needs_rewrite_true: leaves
        no rewrite pending after one write, and keeps the unlisted set.json as it was
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from e8s.core.util import content_hash
from e8s.examples import two_set_show
from e8s.show import documents as documents_module
from e8s.show import workspace as workspace_module
from e8s.show.document_paths import set_doc
from e8s.show.documents import SHOW_DOC, dump_json, needs_rewrite
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.manage import add_set
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.migrate import without_header
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import open_workspace
from tests.fixtures.layout1 import layout1_documents

SHOW_ID = "show_1"
AT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
STRAY_KEY = "9"
STEP = timedelta(seconds=1)
LEGACY_SET = "con"
MIGRATED_SET = "con_"
LEGACY_SCRIPT = b"# legacy\n"
SMALL_BLOB_CAP = 1_000
BLOB_SIZE = 600


def _stored_repo(docs: dict[str, bytes]) -> InMemoryRepository:
    """Stores documents as an older release did, through the backend, so no commit check runs."""
    repo = InMemoryRepository()
    tree = {path: blob_digest(raw) for path, raw in docs.items()}
    revision = Revision(id=content_hash(tree), parent="", at=AT, changed=tuple(sorted(docs)))
    repo.backend.commit(SHOW_ID, revision, tree, {blob_digest(raw): raw for raw in docs.values()})
    return repo


def test_regression_unlisted_layout1_set_document_does_not_keep_needs_rewrite_true() -> None:
    docs = layout1_documents(two_set_show())
    docs[set_doc(STRAY_KEY)] = docs[set_doc("1")]
    repo = _stored_repo(docs)
    assert needs_rewrite(repo.documents(SHOW_ID))
    open_workspace(repo, SHOW_ID).write("/sets/1/set.py", "# edit\n", at=AT)
    head = repo.documents(SHOW_ID)
    assert not needs_rewrite(head)
    assert head[set_doc(STRAY_KEY)] == docs[set_doc(STRAY_KEY)]


def _v1_docs() -> dict[str, bytes]:
    """Stores the example show as v0.2 did, its first set in the reserved-name folder `con`."""
    docs = layout1_documents(two_set_show())
    meta = without_header(json.loads(docs[SHOW_DOC]))
    meta["sets"] = [LEGACY_SET, "2"]
    docs[SHOW_DOC] = dump_json(meta)
    body = json.loads(docs.pop(set_doc("1")))
    docs[set_doc(LEGACY_SET)] = dump_json({**body, "id": LEGACY_SET})
    docs[f"/sets/{LEGACY_SET}/set.py"] = LEGACY_SCRIPT
    return docs


def _history(count: int) -> InMemoryRepository:
    repo = InMemoryRepository()
    save_show(open_workspace(repo, SHOW_ID), two_set_show(), AT)
    for n in range(1, count):
        ws = open_workspace(repo, SHOW_ID)
        ws.write("/sets/1/set.py", f"# {n}\n", at=AT + STEP * n)
    return repo


def _counting(monkeypatch: pytest.MonkeyPatch, target: object, name: str) -> Callable[[], int]:
    calls: list[tuple[object, ...]] = []
    original = getattr(target, name)

    def counted(*args: object, **kwargs: object) -> object:
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, counted)
    return lambda: len(calls)


def test_tree_at_reads_a_revision_once(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _history(3)
    first, second, _ = (r.id for r in repo.revisions(SHOW_ID))
    reads = _counting(monkeypatch, repo, "documents")
    ws = open_workspace(repo, SHOW_ID)
    before = reads()
    for revision in (first, second, first, second, first):
        ws.tree_at(revision)
    assert reads() - before == 2


def test_workspace_total_bytes_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace_module, "WORKSPACE_BLOB_BYTES", SMALL_BLOB_CAP)
    repo = InMemoryRepository()
    files = {f"/renders/{name}.bin": name.encode() * BLOB_SIZE for name in "ab"}
    files["/renders/huge.bin"] = b"h" * (SMALL_BLOB_CAP + 1)
    repo.transaction(SHOW_ID, files, expected_revision="", at=AT)
    reads = _counting(monkeypatch, repo.backend, "blob")
    ws = open_workspace(repo, SHOW_ID)
    ws.read("/renders/a.bin")
    ws.read("/renders/a.bin")
    assert reads() == 1
    ws.read("/renders/b.bin")
    ws.read("/renders/a.bin")
    assert reads() == 3
    ws.read("/renders/huge.bin")
    ws.read("/renders/huge.bin")
    assert reads() == 5
    ws.read("/renders/a.bin")
    assert reads() == 5


def test_regression_write_with_an_older_expected_revision_normalizes_that_revision() -> None:
    repo = _stored_repo(_v1_docs())
    stale = open_workspace(repo, SHOW_ID)
    other = open_workspace(repo, SHOW_ID)
    grown = add_set(load_show(other), "3", 4.0)
    head = save_show(other, grown, AT + STEP).id
    stale.write("/sets/3/set.py", "# third\n", expected_revision=head, at=AT + 2 * STEP)
    tree = repo.documents(SHOW_ID)
    assert tree["/sets/3/set.py"] == b"# third\n"
    assert not needs_rewrite(tree)


def test_get_script_on_a_v1_head_migrates_once(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _stored_repo(_v1_docs())
    migrations = _counting(monkeypatch, documents_module, "read_migrated")
    ws = open_workspace(repo, SHOW_ID)
    for _ in range(3):
        assert ws.read(f"/sets/{MIGRATED_SET}/set.py") == LEGACY_SCRIPT.decode()
    ws.write(f"/sets/{MIGRATED_SET}/set.py", "# edited\n", at=AT)
    assert migrations() == 1


def test_regression_v1_workspace_reads_a_file_at_a_renamed_set_folder() -> None:
    ws = open_workspace(_stored_repo(_v1_docs()), SHOW_ID)
    assert f"{MIGRATED_SET}/" in ws.ls("/sets/")
    assert ws.read(f"/sets/{MIGRATED_SET}/set.py") == LEGACY_SCRIPT.decode()
    assert ws.ls(f"/sets/{MIGRATED_SET}/").count("set.py") == 1


def test_canonical_revisions_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = 2
    monkeypatch.setattr(workspace_module, "WORKSPACE_CANONICAL_REVISIONS", cap)
    repo = _history(1)
    ws = open_workspace(repo, SHOW_ID)
    for n in range(cap + 3):
        ws.write("/sets/1/set.py", f"# {n}\n", at=AT + STEP * (n + 1))
    assert len(ws._canonical_revisions) <= cap
