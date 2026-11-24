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
"""

import json
from datetime import UTC, datetime

import pytest

from e8s.cast.models import Cast
from e8s.core.errors import SchemaError
from e8s.examples import two_set_show
from e8s.show.documents import SHOW_DOC, decode_show, dump_json, dump_show_doc, encode_show
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import open_workspace
from tests.fixtures.layout1 import layout1_documents

AT = datetime(2026, 11, 24, tzinfo=UTC)
SHOW_ID = "show-1"
CAST_DOC = "/cast/cast.json"
ORDER_FIELD = "placement_order"
ROLE_NAMES = ("drill.json", "facing.json", "labels.json")
LONG_ID_CHARS = 10_000
LISTED_SPOTS = 20
STRAY_SPOTS = 30


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
