#!/usr/bin/env python3

"""
test_fuzz_import.py --- fuzzes import_json and import_positions with hostile input

Contains:
    test_fuzz_import_json_arbitrary_values(): feeds arbitrary JSON values to import_json
    test_fuzz_import_json_mutated_documents(): feeds valid documents with one value replaced
    test_fuzz_import_json_raw_text_and_bytes(): feeds bytes and strings to import_json
    test_fuzz_import_positions_arbitrary_text(): feeds arbitrary text, group and columns
    test_fuzz_import_positions_mutated_csv(): feeds valid CSV with cells and layout damaged
"""

import copy
from collections.abc import Mapping
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from showpy.examples import crossing_show, two_set_show
from showpy.show.interchange import POSITION_FIELDS, export_json, import_json, import_positions
from tests.helpers import only_showpy_errors

MAX_LEAVES = 20
HUGE_INT = 10**400
LONG_CELL_CHARS = 5000
JSON_LEAVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.sampled_from([HUGE_INT, -HUGE_INT, 2**63, -(2**63)]),
    st.floats(allow_nan=False),
    st.text(),
)
JSON_VALUES = st.recursive(
    JSON_LEAVES,
    lambda inner: st.one_of(st.lists(inner), st.dictionaries(st.text(), inner)),
    max_leaves=MAX_LEAVES,
)
BASE_SHOW = two_set_show()
VALID_DOCUMENTS = [export_json(two_set_show()), export_json(crossing_show())]
CELL_DAMAGE = st.one_of(
    st.text(),
    st.just(""),
    st.just("\x00"),
    st.just("nan"),
    st.just("inf"),
    st.just('"'),
    st.just("1e999"),
    st.just("9" * LONG_CELL_CHARS),
    st.just("x" * LONG_CELL_CHARS),
)
CSV_ROW_COUNT = st.integers(min_value=0, max_value=6)


def _paths(node: object, prefix: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    """Lists the path to every nested value of a JSON document, below the root."""
    found: list[tuple[object, ...]] = []
    children: list[tuple[object, object]] = []
    if isinstance(node, dict):
        children = list(node.items())
    elif isinstance(node, list):
        children = list(enumerate(node))
    for key, child in children:
        found.append((*prefix, key))
        found.extend(_paths(child, (*prefix, key)))
    return found


DOCUMENT_PATHS = [_paths(document) for document in VALID_DOCUMENTS]


# Any: a mutated document holds arbitrary nested JSON values.
def _replaced(document: dict[str, Any], path: tuple[object, ...], value: object) -> dict[str, Any]:
    """Returns a copy of `document` with the node at `path` replaced, or its key renamed."""
    mutated = copy.deepcopy(document)
    parent: Any = mutated
    for step in path[:-1]:
        parent = parent[step]
    parent[path[-1]] = value
    return mutated


@st.composite
def mutated_documents(draw: st.DrawFn) -> dict[str, Any]:
    """Draws a valid document with one nested value replaced by an arbitrary JSON value."""
    index = draw(st.integers(min_value=0, max_value=len(VALID_DOCUMENTS) - 1))
    path = draw(st.sampled_from(DOCUMENT_PATHS[index]))
    mutated = _replaced(VALID_DOCUMENTS[index], path, draw(JSON_VALUES))
    if isinstance(path[-1], str) and draw(st.booleans()):
        parent: Any = mutated
        for step in path[:-1]:
            parent = parent[step]
        parent[draw(st.text())] = parent.pop(path[-1])
    return mutated


@given(value=JSON_VALUES)
def test_fuzz_import_json_arbitrary_values(value: object) -> None:
    """Rejects any JSON value, mapping or not, with a ShowPyError."""
    only_showpy_errors(lambda: import_json(value), value)  # type: ignore[arg-type]


@given(document=mutated_documents())
def test_fuzz_import_json_mutated_documents(document: dict[str, Any]) -> None:
    """Accepts or rejects a valid document with one damaged value, with a ShowPyError at worst."""
    only_showpy_errors(lambda: import_json(document), document)


@given(raw=st.one_of(st.binary(), st.text()))
def test_fuzz_import_json_raw_text_and_bytes(raw: bytes | str) -> None:
    """Rejects raw bytes and strings, which are not documents, with a ShowPyError."""
    only_showpy_errors(lambda: import_json(raw), raw)  # type: ignore[arg-type]


FIELD_NAMES: list[str] = list(POSITION_FIELDS)
COLUMN_MAPS: st.SearchStrategy[Mapping[str, str] | None] = st.one_of(
    st.none(),
    st.dictionaries(st.sampled_from(FIELD_NAMES), st.text(), max_size=len(FIELD_NAMES)),
    st.dictionaries(st.text(), st.text(), max_size=3),
)


@given(text=st.text(), group=st.text(), columns=COLUMN_MAPS)
def test_fuzz_import_positions_arbitrary_text(
    text: str, group: str, columns: Mapping[str, str] | None
) -> None:
    """Rejects arbitrary CSV text, group names and column maps with a ShowPyError."""
    only_showpy_errors(
        lambda: import_positions(BASE_SHOW, text, group, columns), (text, group, columns)
    )


@st.composite
def mutated_csv(draw: st.DrawFn) -> tuple[str, str, Mapping[str, str] | None]:
    """Draws position CSV from real spot IDs with damaged cells, columns and quoting."""
    spots = [spot.id for spot in BASE_SHOW.cast.spots]
    header: list[str] = list(POSITION_FIELDS)
    if draw(st.booleans()):
        header = draw(st.permutations(header))[: draw(st.integers(min_value=0, max_value=6))]
    if draw(st.booleans()):
        header.extend(draw(st.lists(st.text(max_size=5), max_size=3)))
    lines = [draw(st.sampled_from([",", ";", "\t", "|"])).join(header)]
    delimiter = lines[0][len(header[0])] if len(header) > 1 and header[0] else ","
    for number in range(draw(CSV_ROW_COUNT)):
        cells = [str(number % 3 + 1), "4", draw(st.sampled_from(spots)), "1.5", "2.5", "90"]
        for position in range(len(cells)):
            if draw(st.integers(min_value=0, max_value=5)) == 0:
                cells[position] = draw(CELL_DAMAGE)
        if draw(st.booleans()):
            cells = draw(st.permutations(cells))[: draw(st.integers(min_value=0, max_value=8))]
        quote = draw(st.sampled_from(["", '"', "'"]))
        lines.append(delimiter.join(f"{quote}{cell}{quote}" for cell in cells))
    columns = draw(COLUMN_MAPS)
    return draw(st.sampled_from(["\n", "\r\n", "\r"])).join(lines), draw(st.text()), columns


@given(case=mutated_csv())
def test_fuzz_import_positions_mutated_csv(case: tuple[str, str, Mapping[str, str] | None]) -> None:
    """Accepts or rejects damaged position CSV, with a ShowPyError at worst."""
    text, group, columns = case
    only_showpy_errors(lambda: import_positions(BASE_SHOW, text, group, columns), case)
