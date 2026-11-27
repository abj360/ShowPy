#!/usr/bin/env python3

"""
test_workspace.py --- tests a workspace's reads, writes and normalizing rewrite over stored trees

Contains:
    test_regression_unlisted_layout1_set_document_does_not_keep_needs_rewrite_true: leaves
        no rewrite pending after one write, and keeps the unlisted set.json as it was
"""

from datetime import UTC, datetime

from e8s.core.util import content_hash
from e8s.examples import two_set_show
from e8s.show.document_paths import set_doc
from e8s.show.documents import needs_rewrite
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.workspace import open_workspace
from tests.fixtures.layout1 import layout1_documents

SHOW_ID = "show_1"
AT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
STRAY_KEY = "9"


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
