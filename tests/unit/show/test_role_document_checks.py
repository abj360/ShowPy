#!/usr/bin/env python3

"""
test_role_document_checks.py --- refuses stored role documents that would decode to a wrong show

Contains:
    test_regression_role_tree_without_cast_document_is_refused: refuses a format-2 tree whose cast
        is in neither show.json nor /cast/cast.json, on decode and on a commit that deletes it
"""

import json
from datetime import UTC, datetime

import pytest

from e8s.core.errors import SchemaError
from e8s.examples import two_set_show
from e8s.show.documents import SHOW_DOC, decode_show, dump_show_doc, encode_show
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import open_workspace
from tests.fixtures.layout1 import layout1_documents

AT = datetime(2026, 11, 24, tzinfo=UTC)
SHOW_ID = "show-1"
CAST_DOC = "/cast/cast.json"


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
