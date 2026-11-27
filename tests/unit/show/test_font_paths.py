#!/usr/bin/env python3

"""
test_font_paths.py --- tests that every commit refuses a bad font document and allows deleting one

Contains:
    test_regression_commit_refuses_a_bad_font_document_name: refuses a written font document whose
        name is not /assets/fonts/<64 lowercase hex>.json, leaving the head unchanged
    test_regression_commit_refuses_a_font_document_that_does_not_hash_to_its_name: refuses bytes
        stored under another hash with FONT_HASH_MISMATCH
    test_regression_commit_refuses_an_oversize_font_document: refuses a document over
        MAX_FONT_BYTES even when it is named by its own hash
    test_regression_commit_refuses_a_font_document_load_fonts_would_refuse: refuses hash-named
        bytes that are not a font, naming the path
    test_regression_stored_bad_font_document_can_be_deleted: deletes a bad font document stored
        before the check, and commits other paths beside it
    test_commit_refuses_a_bad_font_document_over_a_layout1_tree: refuses a bad font document and
        stores a good one through a workspace over a layout-1 tree
    test_font_documents_are_checked_only_when_written: parses no font a commit leaves unchanged,
        and parses a font written again at the same path once
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError, FontError, SchemaError
from e8s.core.util import asset_uri, content_hash
from e8s.examples import two_set_show
from e8s.geo import fonts
from e8s.geo.fonts import MAX_FONT_BYTES
from e8s.geo.models import Font
from e8s.show import font_paths
from e8s.show.documents import encode_show
from e8s.show.font_documents import load_fonts
from e8s.show.font_paths import FONTS_ROOT
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.workspace import open_workspace
from tests.fixtures.fonts import tiny_bytes
from tests.fixtures.layout1 import layout1_documents

AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
STEP = timedelta(seconds=1)
SHOW_ID = "show_1"
HASH_PREFIX = "asset:sha256:"


def _hex(data: bytes) -> str:
    return asset_uri(data).removeprefix(HASH_PREFIX)


def _font_path(data: bytes) -> str:
    return f"{FONTS_ROOT}{_hex(data)}.json"


def _repo() -> tuple[InMemoryRepository, str]:
    repo = InMemoryRepository()
    head = repo.transaction(SHOW_ID, encode_show(two_set_show()), expected_revision="", at=AT)
    return repo, head.id


def _stored(repo: InMemoryRepository, head: str, docs: dict[str, bytes]) -> str:
    """Stores documents through the backend, as an older release did, so no commit check runs."""
    tree = {**repo.backend.tree(SHOW_ID, head), **{p: blob_digest(d) for p, d in docs.items()}}
    revision = Revision(id=content_hash(tree), parent=head, at=AT + STEP, changed=tuple(docs))
    repo.backend.commit(SHOW_ID, revision, tree, {blob_digest(d): d for d in docs.values()})
    return revision.id


def _refused(repo: InMemoryRepository, head: str, path: str, data: bytes) -> E8sError:
    with pytest.raises(E8sError) as caught:
        repo.transaction(SHOW_ID, {path: data}, expected_revision=head, at=AT + STEP)
    assert repo.head(SHOW_ID) == head
    assert path in caught.value.message
    assert "path" in caught.value.context
    return caught.value


@pytest.mark.parametrize(
    "name",
    [
        "x.json",
        "notes.json",
        "a/{hex}.json",
        "{upper}.json",
        "{hex}.txt",
        "{hex}.JSON",
        "{short}.json",
        "{hex}0.json",
        "{hex}.json/x.json",
    ],
)
def test_regression_commit_refuses_a_bad_font_document_name(name: str) -> None:
    data = tiny_bytes()
    hex_name = _hex(data)
    path = FONTS_ROOT + name.format(hex=hex_name, upper=hex_name.upper(), short=hex_name[:63])
    repo, head = _repo()
    refused = _refused(repo, head, path, data)
    assert isinstance(refused, SchemaError)
    assert "SHA-256" in refused.hint
    assert "64 lowercase hex" in refused.hint


def test_regression_commit_refuses_a_font_document_that_does_not_hash_to_its_name() -> None:
    repo, head = _repo()
    path = _font_path(tiny_bytes())
    refused = _refused(repo, head, path, tiny_bytes(family="Other"))
    assert isinstance(refused, FontError)
    assert refused.code == ErrorCode.FONT_HASH_MISMATCH


def test_regression_commit_refuses_an_oversize_font_document() -> None:
    data = b" " * (MAX_FONT_BYTES + 1)
    repo, head = _repo()
    refused = _refused(repo, head, _font_path(data), data)
    assert isinstance(refused, FontError)
    assert str(MAX_FONT_BYTES) in refused.message


@pytest.mark.parametrize("data", [b"{}", b"[]", tiny_bytes(format="showpy.font.v9")])
def test_regression_commit_refuses_a_font_document_load_fonts_would_refuse(data: bytes) -> None:
    stored, base = _repo()
    _stored(stored, base, {_font_path(data): data})
    with pytest.raises(FontError):
        load_fonts(open_workspace(stored, SHOW_ID))
    repo, head = _repo()
    refused = _refused(repo, head, _font_path(data), data)
    assert isinstance(refused, FontError)
    assert refused.code == ErrorCode.BAD_FONT


def test_regression_stored_bad_font_document_can_be_deleted() -> None:
    repo, head = _repo()
    bad = {f"{FONTS_ROOT}x.json": b"{}", _font_path(tiny_bytes()): tiny_bytes(family="Other")}
    stored = _stored(repo, head, bad)
    moved = repo.transaction(
        SHOW_ID, {"/renders/a.txt": b"hi"}, expected_revision=stored, at=AT + 2 * STEP
    )
    cleaned = repo.transaction(
        SHOW_ID, dict.fromkeys(bad), expected_revision=moved.id, at=AT + 3 * STEP
    )
    assert not [p for p in repo.documents(SHOW_ID, cleaned.id) if p.startswith(FONTS_ROOT)]


def test_commit_refuses_a_bad_font_document_over_a_layout1_tree() -> None:
    repo = InMemoryRepository()
    docs = layout1_documents(two_set_show().replace(id=SHOW_ID))
    tree = {path: blob_digest(raw) for path, raw in docs.items()}
    revision = Revision(id=content_hash(tree), parent="", at=AT, changed=tuple(sorted(docs)))
    repo.backend.commit(SHOW_ID, revision, tree, {blob_digest(raw): raw for raw in docs.values()})
    ws = open_workspace(repo, SHOW_ID)
    data = tiny_bytes()
    with pytest.raises(FontError) as caught:
        ws.commit({_font_path(data): tiny_bytes(family="Other")}, at=AT + STEP)
    assert caught.value.code == ErrorCode.FONT_HASH_MISMATCH
    ws.commit({_font_path(data): data}, at=AT + 2 * STEP)
    assert asset_uri(data) in load_fonts(ws).fonts


def _counting_loads(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    calls: list[bytes] = []
    original = fonts.load_font

    def counted(data: bytes) -> Font:
        calls.append(data)
        return original(data)

    monkeypatch.setattr(font_paths, "load_font", counted)
    return lambda: len(calls)


def test_font_documents_are_checked_only_when_written(monkeypatch: pytest.MonkeyPatch) -> None:
    loads = _counting_loads(monkeypatch)
    repo, head = _repo()
    data = tiny_bytes()
    path = _font_path(data)
    head = repo.transaction(SHOW_ID, {path: data}, expected_revision=head, at=AT + STEP).id
    assert loads() == 1
    edits: list[dict[str, bytes | None]] = [{"/renders/a.txt": b"hi"}, {path: None}, {path: data}]
    for step, changes in enumerate(edits, 2):
        head = repo.transaction(SHOW_ID, changes, expected_revision=head, at=AT + step * STEP).id
    assert loads() == 1
