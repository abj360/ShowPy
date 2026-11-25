#!/usr/bin/env python3

"""
test_role_document_checks.py --- refuses stored role documents that would decode to a wrong show

Contains:
    test_regression_role_tree_without_cast_document_is_refused: refuses a format-2 tree whose cast
        is in neither show.json nor /cast/cast.json, on decode and on a commit that deletes it
    test_regression_set_document_of_neither_form_is_refused: refuses a set.json with neither
        placements nor a placement order, and writes an empty set in the role form
    test_regression_placement_order_spot_ids_are_checked: refuses a traversal, empty, control,
        padded, lookalike, non-NFC, reserved or overlong spot ID in the placement order
    test_regression_spot_lists_in_order_errors_are_capped: lists at most 20 spots, each cut short,
        with a total count, for extra role rows and repeated order entries
    test_regression_role_reencoding_keeps_stray_documents: keeps a set.json and forms.json of an
        unlisted folder through the revision that re-encodes a layout-1 tree as role documents
    test_regression_cast_root_holds_only_the_cast_document: refuses another name under `/cast/`
        on decode and as a stored path
    test_regression_noncanonical_layout1_tree_normalizes_once: re-encodes a format-2 layout-1 tree
        holding `-0.0` and a reformatted sheet in one revision before two writes to different
        documents, and adds nothing on a later save
    test_regression_decode_cache_sees_a_rewritten_role_document: decodes the new positions when
        only drill.json changes under a shared DecodeCache
    test_regression_role_document_keys_are_quoted_in_errors: quotes and escapes raw role-document
        keys in the errors that list them
    test_regression_cast_documents_error_cuts_each_path: cuts each listed stray path under `/cast/`
        and bounds the message
"""

import json
import re
from datetime import UTC, datetime

import pytest

from e8s.cast.models import Cast
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError
from e8s.core.util import content_hash
from e8s.examples import two_set_show
from e8s.show.decode_cache import DecodeCache
from e8s.show.documents import SHOW_DOC, decode_show, dump_json, dump_show_doc, encode_show
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest, require_known_path
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.role_documents import require_known_cast_documents
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import open_workspace
from tests.fixtures.layout1 import layout1_documents

AT = datetime(2026, 11, 24, tzinfo=UTC)
SHOW_ID = "show-1"
CAST_DOC = "/cast/cast.json"
ORDER_FIELD = "placement_order"
ROLE_NAMES = ("drill.json", "facing.json", "labels.json")
LONG_ID_CHARS = 10_000
NEGATIVE_ZERO = b'"x": -0.0'
TOTAL_COUNTS = 100.0
MOVED_X = 7.5
LISTED_SPOTS = 20
STRAY_SPOTS = 30
MAX_MESSAGE_CHARS = 4_000


def test_regression_role_tree_without_cast_document_is_refused() -> None:
    docs = {p: raw for p, raw in encode_show(two_set_show()).items() if p != CAST_DOC}
    with pytest.raises(SchemaError, match="cast"):
        decode_show(docs)
    layout1 = layout1_documents(two_set_show())
    meta = {k: v for k, v in json.loads(layout1[SHOW_DOC]).items() if k != "cast"}
    with pytest.raises(SchemaError, match="cast"):
        decode_show({**layout1, SHOW_DOC: dump_show_doc(meta)})
    repo = InMemoryRepository()
    ws = open_workspace(repo, SHOW_ID)
    head = save_show(ws, two_set_show(), AT)
    with pytest.raises(SchemaError, match="cast"):
        ws.commit({CAST_DOC: None}, at=AT)
    assert repo.head(SHOW_ID) == head.id
    assert load_show(ws).cast == two_set_show().cast


def test_regression_set_document_of_neither_form_is_refused() -> None:
    show = two_set_show()
    empty = show.replace(
        cast=Cast(), sets=tuple(s.replace(placements=()) for s in show.sets), transitions=()
    )
    docs = encode_show(empty)
    assert json.loads(docs["/sets/1/set.json"])[ORDER_FIELD] == []
    assert all(json.loads(docs[f"/sets/1/{name}"]) == {} for name in ROLE_NAMES)
    assert decode_show(docs) == empty
    bare = {p: raw for p, raw in docs.items() if not p.endswith(ROLE_NAMES)}
    for current in empty.sets:
        path = f"/sets/{current.key}/set.json"
        bare[path] = dump_json(
            {k: v for k, v in json.loads(docs[path]).items() if k != ORDER_FIELD}
        )
    with pytest.raises(SchemaError, match=ORDER_FIELD):
        decode_show(bare)


def _renamed_spot(docs: dict[str, bytes], old: str, new: str) -> dict[str, bytes]:
    """Renames one spot in set 2's placement order and role documents, keeping them in step."""
    renamed = dict(docs)
    body = json.loads(docs["/sets/2/set.json"])
    body[ORDER_FIELD] = [new if spot == old else spot for spot in body[ORDER_FIELD]]
    renamed["/sets/2/set.json"] = dump_json(body)
    for name in ROLE_NAMES:
        rows = json.loads(docs[f"/sets/2/{name}"])
        rows[new] = rows.pop(old)
        renamed[f"/sets/2/{name}"] = dump_json(rows)
    return renamed


@pytest.mark.parametrize(
    "spot",
    ["../x", "", "\x00", "T1 ", "T\uff11", "\u212a1", "con", "x" * LONG_ID_CHARS],
)
def test_regression_placement_order_spot_ids_are_checked(spot: str) -> None:
    docs = _renamed_spot(encode_show(two_set_show()), "T1", spot)
    with pytest.raises(SchemaError, match=r"/sets/2/set\.json lists the spot ID"):
        decode_show(docs)


def test_regression_spot_lists_in_order_errors_are_capped() -> None:
    docs = encode_show(two_set_show())
    facing = json.loads(docs["/sets/2/facing.json"])
    strays = {f"S{n:02}{'x' * LONG_ID_CHARS}": {"facing": 0.0} for n in range(STRAY_SPOTS)}
    with pytest.raises(SchemaError) as extra:
        decode_show({**docs, "/sets/2/facing.json": dump_json({**facing, **strays})})
    assert extra.value.context["extra_count"] == STRAY_SPOTS
    listed = extra.value.context["extra"]
    assert isinstance(listed, list)
    assert len(listed) <= LISTED_SPOTS + 1
    assert all(len(str(spot)) < LONG_ID_CHARS for spot in listed)
    assert str(STRAY_SPOTS) in str(extra.value)
    body = json.loads(docs["/sets/2/set.json"])
    body[ORDER_FIELD] = [f"S{n:02}" for n in range(STRAY_SPOTS)] * 2
    with pytest.raises(SchemaError) as repeated:
        decode_show({**docs, "/sets/2/set.json": dump_json(body)})
    assert repeated.value.context["repeated_count"] == STRAY_SPOTS
    assert "S19" in str(repeated.value)
    assert "S20" not in str(repeated.value)
    assert f"(+{STRAY_SPOTS - LISTED_SPOTS} more)" in str(repeated.value)


def _stored_repo(docs: dict[str, bytes]) -> InMemoryRepository:
    """Stores documents through the backend, as an older release could, with no commit check."""
    repo = InMemoryRepository()
    tree = {p: blob_digest(raw) for p, raw in docs.items()}
    revision = Revision(id=content_hash(tree), parent="", at=AT, changed=tuple(sorted(docs)))
    repo.backend.commit(SHOW_ID, revision, tree, {blob_digest(raw): raw for raw in docs.values()})
    return repo


def test_regression_role_reencoding_keeps_stray_documents() -> None:
    docs = layout1_documents(two_set_show())
    strays = {"/sets/9/set.json": docs["/sets/1/set.json"], "/sets/9/forms.json": b"[]\n"}
    repo = _stored_repo({**docs, **strays})
    ws = open_workspace(repo, SHOW_ID)
    before = load_show(ws)
    edit = ws.write("/config/limits.yaml", "{}\n", at=AT)
    normalizing = repo.revisions(SHOW_ID)[-2]
    assert edit.parent == normalizing.id
    assert not set(strays) & set(normalizing.changed)
    tree = ws.tree_at(edit.id)
    assert {p: tree[p] for p in strays} == strays
    assert "/sets/1/drill.json" in tree
    assert load_show(ws).replace(revision="") == before.replace(revision="")


@pytest.mark.parametrize("path", ["/cast/x.json", "/cast/cast.yaml", "/cast/sub/cast.json"])
def test_regression_cast_root_holds_only_the_cast_document(path: str) -> None:
    with pytest.raises(SchemaError, match=re.escape(path)):
        decode_show({**encode_show(two_set_show()), path: b"{}\n"})
    with pytest.raises(StorageError) as caught:
        require_known_path(path)
    assert caught.value.code == ErrorCode.BAD_PATH
    assert CAST_DOC in caught.value.hint
    require_known_path(CAST_DOC)


def test_regression_noncanonical_layout1_tree_normalizes_once() -> None:
    docs = layout1_documents(two_set_show())
    docs["/sets/1/set.json"] = docs["/sets/1/set.json"].replace(b'"x": 0.0', NEGATIVE_ZERO, 1)
    assert NEGATIVE_ZERO in docs["/sets/1/set.json"]
    docs["/show/sheet.json"] = json.dumps(json.loads(docs["/show/sheet.json"]), indent=4).encode()
    repo = InMemoryRepository()
    repo.transaction(SHOW_ID, docs, expected_revision="", at=AT)
    ws = open_workspace(repo, SHOW_ID)
    count = len(repo.revisions(SHOW_ID))
    meta = json.loads(ws.read(SHOW_DOC))
    meta["title"] = "Renamed"
    ws.write(SHOW_DOC, json.dumps(meta), at=AT)
    timeline = json.loads(ws.read("/time/timeline.json"))
    timeline["total_counts"] = TOTAL_COUNTS
    ws.write("/time/timeline.json", json.dumps(timeline), at=AT)
    roles = {f"/sets/{key}/{name}" for key in ("1", "2") for name in ("set.json", *ROLE_NAMES)}
    assert [set(r.changed) for r in repo.revisions(SHOW_ID)[count:]] == [
        roles | {CAST_DOC, SHOW_DOC, "/show/sheet.json"},
        {SHOW_DOC},
        {"/time/timeline.json"},
    ]
    save_show(ws, load_show(ws), AT)
    assert len(repo.revisions(SHOW_ID)) == count + 3
    tree = ws.tree_at(ws.revision)
    assert not [p for p, raw in tree.items() if b"-0.0" in raw]


def test_regression_decode_cache_sees_a_rewritten_role_document() -> None:
    docs = encode_show(two_set_show())
    cache = DecodeCache()
    digests = {p: blob_digest(raw) for p, raw in docs.items()}
    assert decode_show(docs, cache=cache, digests=digests) == two_set_show()
    drill = json.loads(docs["/sets/2/drill.json"])
    drill["T1"]["x"] = MOVED_X
    moved = {**docs, "/sets/2/drill.json": dump_json(drill)}
    moved_digests = {p: blob_digest(raw) for p, raw in moved.items()}
    decoded = decode_show(moved, cache=cache, digests=moved_digests)
    assert decoded.set("2").get("T1").x == MOVED_X
    assert decoded.set("2").get("T2") == two_set_show().set("2").get("T2")


def test_regression_role_document_keys_are_quoted_in_errors() -> None:
    docs = encode_show(two_set_show())
    hostile = "S1\nforged line\x1b[31m"
    facing = json.loads(docs["/sets/2/facing.json"])
    with pytest.raises(SchemaError) as extra:
        decode_show({**docs, "/sets/2/facing.json": dump_json({**facing, hostile: {}})})
    assert repr(hostile) in extra.value.message
    assert "\n" not in extra.value.message
    assert "\x1b" not in extra.value.message
    first = next(iter(facing))
    stray_row = {**facing, first: {**facing[first], hostile: 1}}
    with pytest.raises(SchemaError) as stray:
        decode_show({**docs, "/sets/2/facing.json": dump_json(stray_row)})
    assert repr(hostile) in stray.value.message
    assert "\n" not in stray.value.message
    assert "\x1b" not in stray.value.message


def test_regression_cast_documents_error_cuts_each_path() -> None:
    long_paths = [f"/cast/{n:02}{'x' * LONG_ID_CHARS}.json" for n in range(STRAY_SPOTS)]
    with pytest.raises(SchemaError) as caught:
        require_known_cast_documents([CAST_DOC, *long_paths])
    assert len(caught.value.message) < MAX_MESSAGE_CHARS
    assert f"(+{STRAY_SPOTS - LISTED_SPOTS} more)" in caught.value.message
    assert caught.value.context["count"] == STRAY_SPOTS
