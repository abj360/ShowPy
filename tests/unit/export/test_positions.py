#!/usr/bin/env python3

"""
test_positions.py --- tests merging CSV positions into a show
"""

import pytest

from showpy.core.errors import LockedError, SchemaError, UnknownRefError
from showpy.export.positions import import_positions
from tests.fixtures.shows import two_set_show

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
