#!/usr/bin/env python3

"""
test_workspace.py --- tests a workspace's reads, writes and normalizing rewrite over stored trees

Contains:
    test_regression_write_with_an_older_expected_revision_normalizes_that_revision: stages a write
        on the show at the expected revision, not the one the workspace was opened at
    test_regression_unlisted_layout1_set_document_does_not_keep_needs_rewrite_true: leaves
        no rewrite pending after one write, and keeps the unlisted set.json as it was
"""

import json
from datetime import UTC, datetime, timedelta

from e8s.core.util import content_hash
from e8s.examples import two_set_show
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
LEGACY_SCRIPT = b"# legacy\n"


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
