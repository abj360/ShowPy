#!/usr/bin/env python3

"""
test_fuzz_migrate.py --- fuzzes the v1 and v2 decoders over real stored fixture documents

Each released fixture's head tree (v1) and its migration (v2) are mutated deep inside one real
document, damaged at the byte level, or given a hostile header; decoding, migrating and the
v1 interchange import may only reject with a ShowPyError, never crash or hang.

Contains:
    test_fuzz_decode_nested_mutations(): decodes v1 and v2 trees with one nested value replaced
    test_fuzz_decode_damaged_bytes(): decodes trees whose document bytes are cut or spliced
    test_fuzz_decode_hostile_headers(): decodes trees whose show.json header is any JSON value
    test_fuzz_import_mutated_v1_documents(): imports v1 interchange documents with one value
        replaced
"""

import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from showpy.show.documents import SHOW_DOC, decode_show, is_show_doc, migrate_documents
from showpy.show.file_repository import FileRepository
from showpy.show.interchange import SCHEMA_TAG, import_json
from tests.helpers import only_showpy_errors

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "repos"
TAGS = ("v0.1.1", "v0.2.0", "v0.3.0")
SHOW_ID = "legacy-show"
MAX_PICKS = 10
JSON_LEAVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=8),
)
JSON_VALUES = st.recursive(
    JSON_LEAVES,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(st.text(max_size=6), inner, max_size=4)
    ),
    max_leaves=12,
)
SPLICES = st.sampled_from(
    [b"NaN", b"Infinity", b'"\\ud800"', b"\xef\xbb\xbf", b'"id": "x", "id"', b"\xff", b"[" * 200]
)


def _head(tag: str) -> dict[str, bytes]:
    return dict(FileRepository(FIXTURES / tag / "repo").documents(SHOW_ID))


V1_TREES = [_head(tag) for tag in TAGS]
TREES = V1_TREES + [migrate_documents(tree)[0] for tree in V1_TREES]
DOC_PATHS = [sorted(p for p in tree if is_show_doc(p)) for tree in TREES]
V1_DUMPS = [decode_show(tree).model_dump(mode="json") for tree in V1_TREES]


def _replace_nested(document: object, picks: list[int], value: object) -> object:
    """Returns the document with the value at the picked nested position replaced."""
    if not picks or not isinstance(document, dict | list) or not document:
        return value
    if isinstance(document, dict):
        key = sorted(document)[picks[0] % len(document)]
        return {**document, key: _replace_nested(document[key], picks[1:], value)}
    index = picks[0] % len(document)
    return [
        *document[:index],
        _replace_nested(document[index], picks[1:], value),
        *document[index + 1 :],
    ]


def _decode_both(docs: dict[str, bytes]) -> None:
    decode_show(docs)
    migrate_documents(docs)


@st.composite
def nested_mutations(draw: st.DrawFn) -> dict[str, bytes]:
    """Draws a fixture tree with one value deep inside one show document replaced."""
    index = draw(st.integers(min_value=0, max_value=len(TREES) - 1))
    path = draw(st.sampled_from(DOC_PATHS[index]))
    picks = draw(st.lists(st.integers(min_value=0), max_size=MAX_PICKS))
    document = _replace_nested(json.loads(TREES[index][path]), picks, draw(JSON_VALUES))
    return {**TREES[index], path: json.dumps(document).encode()}


@st.composite
def damaged_bytes(draw: st.DrawFn) -> dict[str, bytes]:
    """Draws a fixture tree with one show document cut short or spliced with hostile bytes."""
    index = draw(st.integers(min_value=0, max_value=len(TREES) - 1))
    path = draw(st.sampled_from(DOC_PATHS[index]))
    raw = TREES[index][path]
    at = draw(st.integers(min_value=0, max_value=len(raw)))
    damaged = raw[:at] if draw(st.booleans()) else raw[:at] + draw(SPLICES) + raw[at:]
    return {**TREES[index], path: damaged}


@given(docs=nested_mutations())
def test_fuzz_decode_nested_mutations(docs: dict[str, bytes]) -> None:
    only_showpy_errors(lambda: _decode_both(docs), sorted(docs))


@given(docs=damaged_bytes())
def test_fuzz_decode_damaged_bytes(docs: dict[str, bytes]) -> None:
    only_showpy_errors(lambda: _decode_both(docs), docs.get(SHOW_DOC))


@given(
    index=st.integers(min_value=0, max_value=len(TREES) - 1),
    header=st.dictionaries(st.sampled_from(["format", "version"]), JSON_VALUES, max_size=2),
)
def test_fuzz_decode_hostile_headers(index: int, header: dict[str, object]) -> None:
    meta = json.loads(TREES[index][SHOW_DOC])
    meta.pop("format", None)
    meta.pop("version", None)
    docs = {**TREES[index], SHOW_DOC: json.dumps({**header, **meta}).encode()}
    only_showpy_errors(lambda: _decode_both(docs), header)


@given(
    index=st.integers(min_value=0, max_value=len(V1_DUMPS) - 1),
    picks=st.lists(st.integers(min_value=0), max_size=MAX_PICKS),
    value=JSON_VALUES,
)
def test_fuzz_import_mutated_v1_documents(index: int, picks: list[int], value: object) -> None:
    show = _replace_nested(V1_DUMPS[index], picks, value)
    document = {"schema": SCHEMA_TAG, "show": show}
    only_showpy_errors(lambda: import_json(document), picks)
