#!/usr/bin/env python3

"""
test_positions.py --- tests merging CSV positions into a show

Also holds the audited import defects (UP-9.5-41 to UP-9.5-44); UP-9.5-43 and -44 are fixed.
Also holds UP-9.5-58, found by the importer fuzzers, and UP-9.5-60, found by the final review.

Contains:
    test_regression_import_positions_reads_a_utf8_bom(): reads a CSV with a byte order mark
    test_regression_import_positions_reads_semicolon_delimited_csv(): reads semicolon CSV
    test_regression_import_positions_normalizes_facing(): stores facing 720 as 0 instead of keeping
        it unnormalized
    test_regression_import_positions_rejects_ids_with_spaces(): rejects an entity ID with a space
    test_regression_import_positions_rejects_ids_with_path_separators_and_names_the_line(): rejects
        `../x` and names the line
    test_regression_import_positions_wraps_a_negative_facing(): stores -90 as 270
    test_regression_import_positions_rejects_a_bare_carriage_return_with_schema_error(): wraps a
        csv.Error in SchemaError
    test_regression_import_positions_caps_the_placement_count(): refuses a placement blowup
    test_regression_import_positions_caps_the_text_size(): refuses a CSV over the character cap
    test_regression_import_positions_caps_placements_before_ordering_sets(): refuses 32,000 new
        sets fast
    test_regression_import_positions_refuses_a_set_inside_a_locked_transition(): raises
        LockedError without force, and the forced result saves
    test_regression_import_positions_drops_paths_between_sets_it_separates(): drops stored paths
        between sets the import splits, so the result saves
"""

import math
import sys
from datetime import UTC, datetime

import pytest

from showpy.choreo.path_models import PathRow, TransitionSpec
from showpy.core.caps import MAX_POINTS, MAX_TEXT_CHARS
from showpy.core.errors import LockedError, SchemaError, UnknownRefError
from showpy.core.ids import SpotId, make_tid
from showpy.core.util import approx_equal
from showpy.export import positions
from showpy.export.positions import import_positions
from showpy.show.models import Show
from showpy.show.pinning import pin
from showpy.show.repositories import InMemoryRepository
from tests.fixtures.shows import two_set_show
from tests.helpers import fails_after

TOL = 1e-9
FIRST_NEW_SET = 3
HANG_BACKSTOP_SECONDS = 3.0
MANY_NEW_SETS = 32_000
FULL_TURN = 360.0
IMPORT_TEXT = """set,counts,entity,x,y,facing
1,0,T1,0,5,0
1,0,N1,10,5,
2,8,T1,1,9,90
3,4,N2,3,3,
"""


def test_import_positions_creates_sets_and_spots() -> None:
    show = import_positions(two_set_show(), IMPORT_TEXT, group="trumpets")
    assert show.set_ids() == ["1", "2", "3"]
    assert show.cast.ids() == ("T1", "T2", "T3", "T4", "N1", "N2")
    assert show.cast.spot("N2").group == "trumpets"
    assert show.set("3").counts == 4.0
    for current in show.sets:
        assert set(current.entity_ids()) == set(show.cast.ids())


def test_import_positions_moves_existing_and_copies_forward() -> None:
    show = import_positions(two_set_show(), IMPORT_TEXT, group="trumpets")
    assert show.set("2").get("T1").point == (1.0, 9.0)
    assert show.set("2").get("T1").facing == 90.0
    assert show.set("2").get("T2").point == (2.0, 9.0)
    assert show.set("3").get("T1").point == (1.0, 9.0)
    assert show.set("2").get("N1").point == (10.0, 5.0)


def test_import_positions_keeps_spots_off_field_before_they_appear() -> None:
    show = import_positions(two_set_show(), IMPORT_TEXT, group="trumpets")
    n2 = [show.set(s).get("N2") for s in ("1", "2", "3")]
    assert [p.on_field for p in n2] == [False, False, True]
    assert all(p.point == (3.0, 3.0) for p in n2)


def test_import_positions_maps_column_names() -> None:
    text = "Set,Len,Who,X,Y\n2,8,T1,5,5\n"
    columns = {"set": "Set", "counts": "Len", "entity": "Who", "x": "X", "y": "Y"}
    show = import_positions(two_set_show(), text, group="trumpets", columns=columns)
    assert show.set("2").get("T1").point == (5.0, 5.0)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("set,counts,entity,x\n1,0,T1,0\n", SchemaError),
        ("set,counts,entity,x,y\n1,0,T1,zero,5\n", SchemaError),
        ("set,counts,entity,x,y\n1,0,T1,0,5\n1,0,T1,1,5\n", SchemaError),
        ("set,counts,entity,x,y\n2,4,T1,0,5\n", SchemaError),
        ("set,counts,entity,x,y\n9,4,T1,0,5\n9,2,T2,0,5\n", SchemaError),
    ],
)
def test_import_positions_refuses_bad_rows(text: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        import_positions(two_set_show(), text, group="trumpets")


def test_import_positions_refuses_unknown_group_and_mapping() -> None:
    with pytest.raises(UnknownRefError):
        import_positions(two_set_show(), IMPORT_TEXT, group="tubas")
    with pytest.raises(UnknownRefError):
        import_positions(two_set_show(), IMPORT_TEXT, group="trumpets", columns={"who": "W"})


def test_import_positions_respects_pins() -> None:
    show = two_set_show()
    pinned = show.sets[1].with_placements({"T1": {"pinned": True}})
    show = show.model_copy(update={"sets": (show.sets[0], pinned)})
    with pytest.raises(LockedError):
        import_positions(show, IMPORT_TEXT, group="trumpets")
    forced = import_positions(show, IMPORT_TEXT, group="trumpets", force=True)
    assert forced.set("2").get("T1").point == (1.0, 9.0)


def test_import_positions_inserts_new_sets_where_the_file_puts_them() -> None:
    between = "set,counts,entity,x,y\n1,0,T1,0,5\n1A,4,T1,0,7\n2,8,T1,0,9\n"
    assert import_positions(two_set_show(), between, "trumpets").set_ids() == ["1", "1A", "2"]
    leading = "set,counts,entity,x,y\n0,0,T1,0,1\n1,0,T1,0,5\n"
    assert import_positions(two_set_show(), leading, "trumpets").set_ids() == ["0", "1", "2"]
    appended = "set,counts,entity,x,y\n3,4,T1,0,1\n"
    assert import_positions(two_set_show(), appended, "trumpets").set_ids() == ["1", "2", "3"]


SIMPLE_TEXT = "set,counts,entity,x,y\n3,4,T1,1,1\n"


@pytest.mark.defect("UP-9.5-41")
@pytest.mark.xfail(strict=True, reason="UP-9.5-41")
def test_regression_import_positions_reads_a_utf8_bom() -> None:
    """Reads a CSV that starts with a UTF-8 byte order mark, as Excel writes it (audit 9.5)."""
    plain = import_positions(two_set_show(), SIMPLE_TEXT, group="trumpets")
    bom = import_positions(two_set_show(), "﻿" + SIMPLE_TEXT, group="trumpets")
    assert bom == plain


@pytest.mark.defect("UP-9.5-42")
@pytest.mark.xfail(strict=True, reason="UP-9.5-42")
def test_regression_import_positions_reads_semicolon_delimited_csv() -> None:
    """Reads a semicolon-delimited CSV, as Excel writes in many locales (audit 9.5)."""
    plain = import_positions(two_set_show(), SIMPLE_TEXT, group="trumpets")
    semicolons = import_positions(two_set_show(), SIMPLE_TEXT.replace(",", ";"), "trumpets")
    assert semicolons == plain


def test_regression_import_positions_normalizes_facing() -> None:
    """Stores facing 720 as 0 instead of keeping it unnormalized (audit 9.5)."""
    text = "set,counts,entity,x,y,facing\n3,4,T1,1,1,720\n"
    facing = import_positions(two_set_show(), text, group="trumpets").set("3").get("T1").facing
    assert 0.0 <= facing < FULL_TURN
    assert approx_equal(facing, 0.0, abs_tol=TOL)


def test_regression_import_positions_rejects_ids_with_spaces() -> None:
    """Rejects an entity ID that contains a space instead of creating a spot with it."""
    with pytest.raises(SchemaError):
        import_positions(two_set_show(), "set,counts,entity,x,y\n3,4,T 9,1,1\n", "trumpets")


def test_regression_import_positions_rejects_ids_with_path_separators_and_names_the_line() -> None:
    """Rejects `../x` with a SchemaError that names the line."""
    with pytest.raises(SchemaError, match="line 2"):
        import_positions(two_set_show(), "set,counts,entity,x,y\n3,4,../x,1,1\n", "trumpets")


def test_regression_import_positions_wraps_a_negative_facing() -> None:
    """Stores facing -90 as 270."""
    text = "set,counts,entity,x,y,facing\n3,4,T1,1,1,-90\n"
    facing = import_positions(two_set_show(), text, group="trumpets").set("3").get("T1").facing
    assert approx_equal(facing, 270.0, abs_tol=TOL)


def test_regression_import_positions_rejects_a_bare_carriage_return_with_schema_error() -> None:
    """Raises SchemaError for an unquoted bare CR; csv.Error used to leak (UP-9.5-58)."""
    with pytest.raises(SchemaError, match="malformed"):
        import_positions(two_set_show(), "\r0", "trumpets")


def _refuse_set_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes ordering sets fail the test, so a refusal proves the cap ran before it."""

    def ordered_too_early(*_args: object) -> list[str]:
        raise AssertionError("sets were ordered before the placement cap")

    monkeypatch.setattr(positions, "_set_order", ordered_too_early)


@fails_after(HANG_BACKSTOP_SECONDS)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_import_positions_caps_the_placement_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuses rows that would build over MAX_POINTS placements; 400 rows took 1 s (UP-9.5-60)."""
    _refuse_set_ordering(monkeypatch)
    side = math.isqrt(MAX_POINTS) + 1
    rows = "".join(f"{FIRST_NEW_SET + i},4,E{i},1,1\n" for i in range(side))
    with pytest.raises(SchemaError, match="placements"):
        import_positions(two_set_show(), "set,counts,entity,x,y\n" + rows, "trumpets")


def test_regression_import_positions_caps_the_text_size() -> None:
    """Refuses a CSV text over MAX_TEXT_CHARS before parsing it."""
    with pytest.raises(SchemaError, match="characters"):
        import_positions(two_set_show(), "set\n" + "x" * MAX_TEXT_CHARS, "trumpets")


@fails_after(HANG_BACKSTOP_SECONDS)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_import_positions_caps_placements_before_ordering_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuses 32,000 new sets before ordering them; ordering first took 7.6 s (UP-9.5-60).

    The 586 KB CSV fits the text cap; each row adds a set and a spot, so the placements
    (32,002 sets by 32,004 spots) exceed MAX_POINTS long before any set is ordered.
    """
    _refuse_set_ordering(monkeypatch)
    rows = "".join(f"{FIRST_NEW_SET + i},4,E{i},1,1\n" for i in range(MANY_NEW_SETS))
    with pytest.raises(SchemaError, match="placements"):
        import_positions(two_set_show(), "set,counts,entity,x,y\n" + rows, "trumpets")


BETWEEN_TEXT = "set,counts,entity,x,y\n1,0,T1,0,0\n1b,4,T1,1,1\n2,8,T1,0,9\n"
SAVED_AT = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _saves(show: Show) -> None:
    InMemoryRepository().put_show("s", show, expected_revision="", at=SAVED_AT)


def test_regression_import_positions_refuses_a_set_inside_a_locked_transition() -> None:
    """Raises LockedError for a set inserted into a pinned transition, and saves when forced."""
    locked = pin(two_set_show(), "transition", "1->2")
    with pytest.raises(LockedError):
        import_positions(locked, BETWEEN_TEXT, "trumpets")
    forced = import_positions(locked, BETWEEN_TEXT, "trumpets", force=True)
    assert forced.set_ids() == ["1", "1b", "2"]
    _saves(forced)


def test_regression_import_positions_drops_paths_between_sets_it_separates() -> None:
    """Drops a stored transition that is no longer between consecutive sets."""
    base = two_set_show()
    spec = TransitionSpec(
        id=make_tid("1", "2"), counts=8.0, rows={SpotId("T1"): PathRow(entity=SpotId("T1"), end=2)}
    )
    stored = base.model_copy(update={"transitions": (spec,)})
    merged = import_positions(stored, BETWEEN_TEXT, "trumpets")
    assert merged.transitions == ()
    _saves(merged)
