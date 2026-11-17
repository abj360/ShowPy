#!/usr/bin/env python3

"""
test_integrity.py --- checks that the write funnel refuses bad paths and undecodable shows

Contains:
    test_regression_look_alike_paths_are_refused: refuses paths that only look inside a root
    test_regression_deleting_a_bad_path_is_refused: checks the path of a deletion too
    test_regression_unknown_root_names_the_roots_in_the_hint: gives a hint listing the roots
    test_every_known_root_accepts_a_document: accepts a document under each known root
    test_regression_garbage_show_document_is_a_schema_error: refuses garbage in a show document
    test_regression_edit_that_breaks_the_show_is_refused: refuses a valid-JSON edit that breaks
        the schema
    test_non_show_documents_are_not_decoded: lands a commit with no show document untouched
    test_valid_show_decodes_on_change: returns the decoded show when a show document changed
"""

from datetime import UTC, datetime

import pytest

from showpy.core.errors import SchemaError, StorageError
from showpy.examples import two_set_show
from showpy.show.documents import SHOW_DOC, encode_show, paths_doc
from showpy.show.integrity import KNOWN_ROOTS, require_known_path, require_valid_tree
from showpy.show.repositories import InMemoryRepository

AT = datetime(2026, 11, 11, tzinfo=UTC)
SHOW_ID = "integrity"


def _repo() -> tuple[InMemoryRepository, str]:
    repo = InMemoryRepository()
    revision = repo.transaction(
        SHOW_ID,
        encode_show(two_set_show()),
        expected_revision="",
        actor="designer",
        at=AT,
        message="create",
    )
    return repo, revision.id


def _commit(repo: InMemoryRepository, head: str, changes: dict[str, bytes | None]) -> None:
    repo.transaction(
        SHOW_ID, changes, expected_revision=head, actor="designer", at=AT, message="edit"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/mystery/x.txt",
        "/sets/../etc/passwd",
        "/sets/./1/set.py",
        "/sets//1/set.py",
        "/sets/1/",
        "/sets/",
        "sets/1/set.py",
        "/sets/1\\set.py",
        "/sets/1/a\x00b",
        "/lib/x.py",
        "/lib/drafts/../x",
        "/",
        "",
    ],
)
def test_regression_look_alike_paths_are_refused(path: str) -> None:
    """Refuses paths outside the roots, or inside one only in appearance (ruling A3-R1)."""
    repo, head = _repo()
    with pytest.raises(StorageError) as caught:
        _commit(repo, head, {path: b"1"})
    assert caught.value.code == "BAD_PATH"
    assert caught.value.context["path"] == path
    assert repo.head(SHOW_ID) == head


def test_regression_deleting_a_bad_path_is_refused() -> None:
    """Checks the path of a deletion, even for a path that was never stored."""
    repo, head = _repo()
    with pytest.raises(StorageError) as caught:
        _commit(repo, head, {"/mystery/x.txt": None})
    assert caught.value.code == "BAD_PATH"


def test_regression_unknown_root_names_the_roots_in_the_hint() -> None:
    """Names every known root in the hint."""
    with pytest.raises(StorageError) as caught:
        require_known_path("/mystery/x.txt")
    assert all(root in caught.value.hint for root in KNOWN_ROOTS)


@pytest.mark.parametrize("root", KNOWN_ROOTS)
def test_every_known_root_accepts_a_document(root: str) -> None:
    """Accepts a plain document under each known root."""
    require_known_path(f"{root}a.txt")


def test_regression_garbage_show_document_is_a_schema_error() -> None:
    """Raises SchemaError, not a raw JSON error, for garbage in a show document."""
    repo, head = _repo()
    with pytest.raises(SchemaError):
        _commit(repo, head, {SHOW_DOC: b"garbage"})
    assert repo.head(SHOW_ID) == head


def test_regression_edit_that_breaks_the_show_is_refused() -> None:
    """Refuses valid JSON that fails the show schema and leaves the head alone."""
    repo, head = _repo()
    with pytest.raises(SchemaError):
        _commit(repo, head, {paths_doc(("1", "2")): b'{"unexpected": []}'})
    assert repo.head(SHOW_ID) == head


def test_non_show_documents_are_not_decoded() -> None:
    """Lands a commit that touches no show document, even while a stored one is untouched."""
    repo, head = _repo()
    _commit(repo, head, {"/renders/a.txt": b"hi", "/canvas/comments.json": b"[]"})
    assert repo.read(SHOW_ID, "/renders/a.txt") == b"hi"


def test_valid_show_decodes_on_change() -> None:
    """Returns the decoded show when a show document changed, None otherwise."""
    docs = encode_show(two_set_show())
    assert require_valid_tree({}, docs) == two_set_show()
    assert require_valid_tree(docs, docs) is None
