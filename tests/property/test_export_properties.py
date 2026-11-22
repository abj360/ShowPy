#!/usr/bin/env python3

"""
test_export_properties.py --- checks export round-trips: JSON, CSV cells, PDF strings, imports
"""

import csv
import io
import json
import re
from collections.abc import Sequence

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from showpy.choreo.models import PlacementPatch
from showpy.export.data import export_json, import_json, to_csv
from showpy.export.pdf import ENCODING, REPLACEMENTS, escape_text
from showpy.export.positions import import_positions
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show

SET_COUNTS = {"1": 0.0, "2": 8.0, "3": 4.0, "4": 2.0}
ENTITIES = ("T1", "T2", "T3", "T4", "N1", "N2", "N3")
ORDER_COUNTS = {"0": 4.0, "1": 0.0, "1A": 4.0, "2": 8.0, "3": 4.0, "4": 2.0, "5": 4.0}
PDF_ESCAPE = re.compile(rb"\\([0-7]{3}|.)", re.DOTALL)

coordinates = st.floats(min_value=-50.0, max_value=50.0, allow_nan=False)
facings = st.floats(min_value=0.0, max_value=359.0, allow_nan=False)


@st.composite
def edited_shows(draw: st.DrawFn) -> Show:
    show = two_set_show()
    sets = tuple(
        s.with_placements(
            {
                p.entity: PlacementPatch(
                    x=draw(coordinates),
                    y=draw(coordinates),
                    facing=draw(facings),
                    note=draw(st.none() | st.text(max_size=8)),
                )
                for p in s.placements
            }
        )
        for s in show.sets
    )
    return show.model_copy(update={"sets": sets, "title": draw(st.text(max_size=12))})


def _unescape(literal: bytes) -> bytes:
    def one(match: re.Match[bytes]) -> bytes:
        token = match[1]
        return bytes([int(token, 8)]) if len(token) == 3 else token

    return PDF_ESCAPE.sub(one, literal)


@settings(max_examples=40)
@given(edited_shows())
def test_json_round_trip_is_exact(show: Show) -> None:
    assert import_json(json.loads(json.dumps(export_json(show)))) == show


@given(st.text())
def test_csv_cells_survive_any_text(text: str) -> None:
    written = to_csv(pd.DataFrame({"cell": [text]}), neutralize=False)
    parsed = list(csv.reader(io.StringIO(written, newline="")))
    assert parsed == [["cell"], [text]]


@given(st.text())
def test_pdf_strings_are_printable_and_reversible(text: str) -> None:
    escaped = escape_text(text)
    assert all(0x20 <= byte < 0x7F for byte in escaped)
    assert re.search(rb"(?<!\\)(?:\\\\)*[()]", escaped) is None
    expected = text
    for old, new in REPLACEMENTS.items():
        expected = expected.replace(old, new)
    assert _unescape(escaped) == expected.encode(ENCODING, errors="replace")


@st.composite
def position_rows(draw: st.DrawFn) -> list[tuple[str, str, float, float]]:
    keys = draw(
        st.lists(
            st.tuples(st.sampled_from(sorted(SET_COUNTS)), st.sampled_from(ENTITIES)),
            min_size=1,
            max_size=12,
            unique=True,
        )
    )
    return [(set_id, entity, draw(coordinates), draw(coordinates)) for set_id, entity in keys]


@settings(max_examples=60)
@given(position_rows())
def test_import_places_every_spot_in_every_set(rows: list[tuple[str, str, float, float]]) -> None:
    lines = ["set,counts,entity,x,y"]
    lines += [f"{s},{SET_COUNTS[s]},{e},{x!r},{y!r}" for s, e, x, y in rows]
    show = import_positions(two_set_show(), "\n".join(lines), group="trumpets")
    cast_ids = set(show.cast.ids())
    for current in show.sets:
        assert sorted(current.entity_ids()) == sorted(cast_ids)
    for set_id, entity, x, y in rows:
        placement = show.set(set_id).get(entity)
        assert placement.point == (x, y)
        assert placement.on_field


def _inserted_order(stored: Sequence[str], file_ids: Sequence[str]) -> list[str]:
    """Orders sets by inserting each new one after its file predecessor, one at a time.

    The quadratic reference the linear `_set_order` replaced (UP-9.5-60).
    """
    order = list(stored)
    for position, set_id in enumerate(file_ids):
        if set_id in order:
            continue
        if position:
            order.insert(order.index(file_ids[position - 1]) + 1, set_id)
            continue
        later = [s for s in file_ids[1:] if s in order]
        order.insert(order.index(later[0]) if later else len(order), set_id)
    return order


@settings(max_examples=80)
@given(st.lists(st.sampled_from(sorted(ORDER_COUNTS)), min_size=1, unique=True))
def test_import_orders_new_sets_after_their_file_predecessor(file_ids: list[str]) -> None:
    lines = ["set,counts,entity,x,y"] + [f"{s},{ORDER_COUNTS[s]},T1,1,1" for s in file_ids]
    show = import_positions(two_set_show(), "\n".join(lines), group="trumpets")
    assert show.set_ids() == _inserted_order(two_set_show().set_ids(), file_ids)
