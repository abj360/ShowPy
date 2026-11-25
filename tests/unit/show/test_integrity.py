#!/usr/bin/env python3

"""
test_integrity.py --- checks that the write funnel refuses bad paths and undecodable shows

Contains:
    test_regression_lazy_blobs_digests_are_read_only: the digest view refuses writes and does not
        alias the tree it was built from
    test_regression_look_alike_paths_are_refused: refuses paths that only look inside a root
    test_regression_deleting_a_bad_path_is_refused: checks the path of a deletion too
    test_regression_unknown_root_names_the_roots_in_the_hint: gives a hint listing the roots
    test_every_known_root_accepts_a_document: accepts a document under each known root
    test_regression_garbage_show_document_is_a_schema_error: refuses garbage in a show document
    test_regression_edit_that_breaks_the_show_is_refused: refuses a valid-JSON edit that breaks
        the schema
    test_non_show_documents_are_not_decoded: lands a commit with no show document untouched
    test_valid_show_decodes_on_change: returns the decoded show when a show document changed
    test_cached_and_plain_paths_agree: returns equal shows with and without a decode cache
    test_regression_cache_does_not_hide_a_bad_rewrite_of_a_cached_path: refuses invalid bytes for
        a path whose valid bytes were cached
    test_regression_cache_digest_twin_at_another_path_is_still_validated: refuses a forms document
        copied from another set although the same bytes are cached there
    test_uncached_invalid_document_is_refused: refuses an invalid document never seen before
    test_cache_serves_unchanged_documents: decodes only the changed document on a one-set edit
    test_cache_respects_its_bound: evicts the oldest entry past max_entries
    test_one_spot_edit_decodes_only_changed_documents: counts per-document decodes on a warm cache
    test_regression_show_json_alone_changing_is_still_validated_whole: refuses a show invalid
        only through the combination while every cached set is reused
    test_regression_trailing_newline_paths_are_refused: refuses `$`-anchored look-alike paths
    test_regression_control_characters_in_paths_are_refused: refuses C0, DEL and C1 characters
    test_regression_dashed_set_ids_are_a_schema_error_cached_or_not: refuses set IDs that make
        transition paths ambiguous, with no raw KeyError
    test_regression_orphan_show_documents_are_refused: refuses show documents decode never reads
    test_removing_a_set_deletes_its_documents: keeps save_show from leaving orphans
    test_regression_show_id_with_trailing_newline_is_refused: refuses a show id ending in a newline
    test_regression_missing_show_document_is_a_schema_error: raises SchemaError for a commit
        without show.json
    test_regression_commit_refuses_bad_structured_file: refuses invalid UTF-8 or wrong-shaped
        JSON at structured paths and invalid UTF-8 at text paths
    test_regression_bom_json_is_refused_at_commit: refuses a JSON file that starts with a UTF-8
        BOM, which the reader returns as text json.loads rejects
    test_structured_check_decodes_only_changed_structured_blobs: loads no blob for show
        documents or raw-byte paths
    test_regression_commit_refuses_deep_or_huge_json: raises SchemaError, not RecursionError or
        ValueError, for deep nesting or an overlong int in a show or free JSON document
    test_regression_commit_refuses_a_cast_spot_still_placed: refuses a show.json that drops a cast
        spot some set still places
    test_regression_transaction_refuses_a_set_id_put_show_refuses: refuses a set.json whose ID
        the show encoder refuses, so the show stays saveable
    test_regression_commit_refuses_derived_tables: refuses the derived index and placements
        tables with READ_ONLY through a raw transaction and Workspace.commit on both backends
    test_regression_lazy_blobs_get_fails_closed_on_a_missing_blob: raises StorageError, not None,
        when the backend has no blob for a path in the tree
    test_regression_cast_path_error_cuts_the_path: cuts an overlong path under `/cast/` in the
        refusal message
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from e8s.cast.models import Shape
from e8s.cast.spots import set_shape
from e8s.choreo.forms import line_form
from e8s.core.errors import LockedError, SchemaError, StorageError
from e8s.examples import two_set_show
from e8s.show.decode_cache import DecodeCache
from e8s.show.document_repository import DocumentRepository
from e8s.show.documents import (
    SHOW_DOC,
    dump_json,
    encode_show,
    forms_doc,
    is_show_doc,
    paths_doc,
    set_doc,
)
from e8s.show.file_repository import FileRepository
from e8s.show.integrity import (
    KNOWN_ROOTS,
    LazyBlobs,
    blob_digest,
    require_known_path,
    require_valid_digests,
    require_valid_tree,
)
from e8s.show.manage import remove_set
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.models import Show
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import open_workspace
from tests.fixtures.generators import seeded_show
from tests.helpers import HOSTILE_JSON

AT = datetime(2026, 11, 11, tzinfo=UTC)
SHOW_ID = "integrity"
LONG_PATH_CHARS = 10_000
MAX_MESSAGE_CHARS = 400


def _repo() -> tuple[InMemoryRepository, str]:
    repo = InMemoryRepository()
    revision = repo.transaction(
        SHOW_ID,
        encode_show(two_set_show()),
        expected_revision="",
        at=AT,
    )
    return repo, revision.id


def _commit(repo: InMemoryRepository, head: str, changes: dict[str, bytes | None]) -> None:
    repo.transaction(SHOW_ID, changes, expected_revision=head, at=AT)


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
    """Accepts a plain document under each known root; `/cast/` holds only cast.json."""
    require_known_path(f"{root}{'cast.json' if root == '/cast/' else 'a.txt'}")


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


def _digests(docs: dict[str, bytes]) -> dict[str, str]:
    return {path: blob_digest(raw) for path, raw in docs.items()}


def _validate(
    before: dict[str, bytes], after: dict[str, bytes], cache: DecodeCache | None
) -> Show | None:
    by_digest = {blob_digest(raw): raw for raw in after.values()}
    return require_valid_digests(_digests(before), _digests(after), by_digest.__getitem__, cache)


def test_cached_and_plain_paths_agree() -> None:
    """Returns equal shows with and without a decode cache, warm or cold."""
    docs = encode_show(two_set_show())
    cache = DecodeCache()
    plain = require_valid_tree({}, docs)
    assert _validate({}, docs, cache) == plain
    assert _validate({}, docs, cache) == plain


def test_regression_cache_does_not_hide_a_bad_rewrite_of_a_cached_path() -> None:
    """Refuses invalid bytes for a set document whose valid bytes were cached."""
    docs = encode_show(two_set_show())
    cache = DecodeCache()
    _validate({}, docs, cache)
    broken = {**docs, set_doc("1"): b'{"id": "1"}'}
    with pytest.raises(SchemaError):
        _validate(docs, broken, cache)


def test_regression_cache_digest_twin_at_another_path_is_still_validated() -> None:
    """Refuses a forms document copied to another set, though its bytes are cached at home."""
    show = two_set_show()
    first, second = show.set_ids()[:2]
    members = [spot.id for spot in show.cast.spots[:2]]
    form = line_form("f", members, (0.0, 0.0), (4.0, 0.0), set_id=first)
    forms_bytes = dump_json([form._dumped()])
    docs = {**encode_show(show), forms_doc(first): forms_bytes}
    cache = DecodeCache()
    _validate({}, docs, cache)
    with pytest.raises(SchemaError):
        _validate(docs, {**docs, forms_doc(second): forms_bytes}, cache)


def test_uncached_invalid_document_is_refused() -> None:
    """Refuses an invalid document whose digest was never cached."""
    docs = encode_show(two_set_show())
    cache = DecodeCache()
    _validate({}, docs, cache)
    with pytest.raises(SchemaError):
        _validate(docs, {**docs, paths_doc(("1", "2")): b"[1]"}, cache)


def test_cache_serves_unchanged_documents() -> None:
    """Decodes only the changed document when one set is edited on a warm cache."""
    docs = encode_show(two_set_show())
    cache = DecodeCache()
    _validate({}, docs, cache)
    cold = len(cache)
    body = json.loads(docs[set_doc("1")])
    body["label"] = "renamed"
    edited = {**docs, set_doc("1"): json.dumps(body).encode()}
    _validate(docs, edited, cache)
    assert len(cache) == cold + 1


def test_cache_respects_its_bound() -> None:
    """Evicts the oldest entry once max_entries is exceeded."""
    cache = DecodeCache(max_entries=2)
    for index in range(3):
        cache.get_or_decode(f"/p{index}", "d", partial(int, index))
    assert len(cache) == 2
    calls: list[str] = []

    def decode() -> int:
        calls.append("decoded")
        return 0

    cache.get_or_decode("/p0", "d", decode)
    assert calls == ["decoded"]


def _count_decodes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    decoded: list[str] = []
    original = DecodeCache.get_or_decode

    def counting(self: DecodeCache, path: str, digest: str, decode: Callable[[], Any]) -> Any:
        def counted() -> Any:
            decoded.append(path)
            return decode()

        return original(self, path, digest, counted)

    monkeypatch.setattr(DecodeCache, "get_or_decode", counting)
    return decoded


def test_one_spot_edit_decodes_only_changed_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """Counts decodes on a warm cache: a cast edit decodes none, a set edit decodes one set."""
    repo = InMemoryRepository()
    ws = open_workspace(repo, SHOW_ID)
    save_show(ws, seeded_show(3, 30, 8), AT)
    calls = _count_decodes(monkeypatch)
    show = load_show(open_workspace(repo, SHOW_ID))
    calls.clear()
    shape = Shape(kind="rectangle")
    save_show(ws, set_shape(show, show.cast.spots[0].id, shape), AT)
    assert calls == []
    ws = open_workspace(repo, SHOW_ID)
    show = load_show(ws)
    calls.clear()
    renamed = tuple(s.replace(label="renamed") if s.id == "3" else s for s in show.sets)
    save_show(ws, show.replace(sets=renamed), AT)
    assert calls == ["/sets/3/set.json"]


def test_regression_show_json_alone_changing_is_still_validated_whole() -> None:
    """Refuses a show.json that repeats a set id, with every set document served from cache."""
    docs = encode_show(two_set_show())
    cache = DecodeCache()
    _validate({}, docs, cache)
    meta = json.loads(docs[SHOW_DOC])
    broken = {**docs, SHOW_DOC: json.dumps({**meta, "sets": ["1", "1"]}).encode()}
    with pytest.raises(SchemaError):
        _validate(docs, broken, cache)


@pytest.mark.parametrize("path", ["/sets/1/set.json\n", "/show/show.json\n", "/sets/1\n/set.json"])
def test_regression_trailing_newline_paths_are_refused(path: str) -> None:
    """Refuses a newline anywhere in a path, through the funnel and through the patterns."""
    assert not is_show_doc(path)
    repo, head = _repo()
    with pytest.raises(StorageError) as caught:
        _commit(repo, head, {path: b"{}"})
    assert caught.value.code == "BAD_PATH"


@pytest.mark.parametrize("char", ["\x01", "\x1f", "\x7f", "\x80", "\x85", "\x9f"])
def test_regression_control_characters_in_paths_are_refused(char: str) -> None:
    """Refuses C0, DEL and C1 characters in a committed path."""
    with pytest.raises(StorageError) as caught:
        require_known_path(f"/renders/a{char}b.txt")
    assert caught.value.code == "BAD_PATH"


def _dashed_docs() -> dict[str, bytes]:
    docs = encode_show(two_set_show())
    meta = json.loads(docs[SHOW_DOC])
    return {**docs, SHOW_DOC: json.dumps({**meta, "sets": ["1-2", "3"]}).encode()}


def test_regression_dashed_set_ids_are_a_schema_error_cached_or_not() -> None:
    """Raises the same SchemaError, naming the ID, with a warm cache and without one."""
    docs = encode_show(two_set_show())
    cache = DecodeCache()
    _validate({}, docs, cache)
    dashed = _dashed_docs()
    for used in (cache, None):
        with pytest.raises(SchemaError) as caught:
            _validate(docs, dashed, used)
        assert "1-2" in str(caught.value.context)


@pytest.mark.parametrize("path", ["/sets/zzz/set.json", "/transitions/1-9/paths.json"])
def test_regression_orphan_show_documents_are_refused(path: str) -> None:
    """Refuses a show document that decode never reads, naming its path."""
    docs = encode_show(two_set_show())
    with pytest.raises(SchemaError) as caught:
        require_valid_tree(docs, {**docs, path: b"{}"})
    assert path in str(caught.value.context)


def test_removing_a_set_deletes_its_documents() -> None:
    """Keeps save_show from leaving the removed set's documents behind."""
    show = seeded_show(3, 10, 4)
    repo = InMemoryRepository()
    save_show(open_workspace(repo, SHOW_ID), show, AT)
    ws = open_workspace(repo, SHOW_ID)
    save_show(ws, remove_set(load_show(ws), "2", force=True), AT)
    stored = repo.paths(SHOW_ID)
    assert not [p for p in stored if p.startswith("/sets/2/")]
    assert "/transitions/2-3/paths.json" not in stored


def test_regression_missing_show_document_is_a_schema_error() -> None:
    """Raises SchemaError, not StorageError, when a commit leaves no show.json."""
    repo, head = _repo()
    with pytest.raises(SchemaError):
        _commit(repo, head, {SHOW_DOC: None})


def test_regression_show_id_with_trailing_newline_is_refused() -> None:
    """Refuses a show id that only matches the pattern before a trailing newline."""
    with pytest.raises(StorageError) as caught:
        InMemoryRepository().revisions("s\n")
    assert caught.value.code == "BAD_PATH"


BAD_STRUCTURED = [
    ("/canvas/comments.json", b"\xff\xfe"),
    ("/canvas/comments.json", b'{"id": "c1"}'),
    ("/canvas/comments.json", b"["),
    ("/canvas/selection.json", b'{"ids": 3}'),
    ("/publish/claims.json", b"{"),
    ("/config/limits.yaml", b"\xff\xfe"),
    ("/sets/1/set.py", b"\xff\xfe"),
    ("/lib/drafts/a.py", b"\xff\xfe"),
]


@pytest.mark.parametrize(("path", "raw"), BAD_STRUCTURED)
def test_regression_commit_refuses_bad_structured_file(path: str, raw: bytes) -> None:
    """Refuses a commit of undecodable or wrong-shaped content at a structured path."""
    repo, head = _repo()
    with pytest.raises(SchemaError) as caught:
        _commit(repo, head, {path: raw})
    assert caught.value.context["path"] == path


def test_structured_check_decodes_only_changed_structured_blobs() -> None:
    docs = encode_show(two_set_show())
    after = {**docs, "/renders/a.png": b"png", "/canvas/comments.json": b"[]"}
    by_digest = {blob_digest(raw): raw for raw in after.values()}
    loaded: list[bytes] = []

    def blob(digest: str) -> bytes:
        loaded.append(by_digest[digest])
        return by_digest[digest]

    require_valid_digests(_digests(docs), _digests(after), blob)
    assert loaded == [b"[]"]


@pytest.mark.parametrize(
    "path", ["/canvas/comments.json", "/canvas/selection.json", "/exports/x.json"]
)
def test_regression_bom_json_is_refused_at_commit(path: str) -> None:
    """Refuses a JSON file with a UTF-8 BOM, so read-back text always parses."""
    repo, head = _repo()
    body = b"[]" if path.endswith("comments.json") else b'{"ids": []}'
    with pytest.raises(SchemaError) as caught:
        _commit(repo, head, {path: b"\xef\xbb\xbf" + body})
    assert caught.value.context["path"] == path


@pytest.mark.parametrize("raw", HOSTILE_JSON.values(), ids=HOSTILE_JSON.keys())
@pytest.mark.parametrize("path", [SHOW_DOC, "/config/x.json", "/exports/x.json"])
def test_regression_commit_refuses_deep_or_huge_json(path: str, raw: bytes) -> None:
    """Refuses nesting past the stack or an overlong int with SchemaError naming the path."""
    repo, head = _repo()
    with pytest.raises(SchemaError) as caught:
        _commit(repo, head, {path: raw})
    assert caught.value.context["path"] == path
    assert repo.head(SHOW_ID) == head


@pytest.mark.defect("UP-9.7-94")
@pytest.mark.xfail(strict=True, reason="UP-9.7-94")
def test_regression_commit_refuses_a_cast_spot_still_placed() -> None:
    """Refuses a show.json that drops a cast spot some set still places (ruling L-A3)."""
    repo, head = _repo()
    body = json.loads(encode_show(two_set_show())[SHOW_DOC])
    dropped = body["cast"]["spots"].pop()["id"]
    assert dropped in {p.entity for p in two_set_show().set("1").placements}
    with pytest.raises(SchemaError):
        _commit(repo, head, {SHOW_DOC: dump_json(body)})
    assert repo.head(SHOW_ID) == head


def test_regression_transaction_refuses_a_set_id_put_show_refuses() -> None:
    """Refuses a set.json whose ID the show encoder refuses, so put_show keeps working after."""
    repo, head = _repo()
    body = json.loads(encode_show(two_set_show())[set_doc("2")])
    with pytest.raises(SchemaError):
        _commit(repo, head, {set_doc("2"): dump_json({**body, "id": "=1+2", "key": "2"})})
    assert repo.head(SHOW_ID) == head
    edited = repo.get_show(SHOW_ID).replace(title="T2")
    assert repo.put_show(SHOW_ID, edited, expected_revision=head, at=AT).parent == head


@pytest.mark.parametrize("path", ["/show/index.csv", "/sets/1/placements.csv"])
@pytest.mark.parametrize("backend", ["memory", "file"])
def test_regression_commit_refuses_derived_tables(path: str, backend: str, tmp_path: Path) -> None:
    repo: DocumentRepository = (
        InMemoryRepository() if backend == "memory" else FileRepository(tmp_path / "repo")
    )
    head = repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT).id
    payload = b'=HYPERLINK("x")\n'
    with pytest.raises(LockedError) as raw:
        repo.transaction(SHOW_ID, {path: payload}, expected_revision=head, at=AT)
    with pytest.raises(LockedError) as committed:
        open_workspace(repo, SHOW_ID).commit({path: payload}, at=AT)
    assert raw.value.code == committed.value.code == "READ_ONLY"
    assert repo.head(SHOW_ID) == head
    assert path not in repo.paths(SHOW_ID)


def _missing_blob(digest: str) -> bytes:
    raise KeyError(digest)


def test_regression_lazy_blobs_get_fails_closed_on_a_missing_blob() -> None:
    """Raises StorageError for a tree path whose blob is gone; an absent path still gives None."""
    blobs = LazyBlobs({"/renders/a.txt": "d"}, _missing_blob)
    with pytest.raises(StorageError):
        blobs.get("/renders/a.txt")
    with pytest.raises(StorageError):
        blobs["/renders/a.txt"]
    assert blobs.get("/renders/b.txt") is None


def test_regression_lazy_blobs_digests_are_read_only() -> None:
    tree = {"/renders/a.txt": "d"}
    blobs = LazyBlobs(tree, _missing_blob)
    digests = blobs.digests
    assert digests == tree
    with pytest.raises(TypeError):
        digests["/renders/a.txt"] = "e"  # type: ignore[index]
    assert not hasattr(digests, "update")
    assert blobs.digests["/renders/a.txt"] == "d"


def test_regression_cast_path_error_cuts_the_path() -> None:
    path = f"/cast/{'x' * LONG_PATH_CHARS}.json"
    with pytest.raises(StorageError) as caught:
        require_known_path(path)
    assert len(caught.value.message) < MAX_MESSAGE_CHARS
    assert caught.value.message.startswith("/cast/")
