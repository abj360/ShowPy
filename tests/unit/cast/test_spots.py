#!/usr/bin/env python3

"""
test_spots.py --- tests creating, copying, deleting, regrouping and relabeling spots in a show

Contains:
    test_*(): one behavior of showpy.cast.spots each
"""

from datetime import UTC, datetime
from inspect import signature

import pytest
import shapely

from showpy.cast.models import Group, LabelScheme, Shape
from showpy.cast.spots import (
    _rename_everywhere,
    add_group,
    add_spot,
    add_spots_along,
    define_selection,
    delete_spots,
    duplicate_spots,
    edit_group,
    fill_spots,
    regroup,
    relabel,
    remove_group,
    set_claimable,
    set_shape,
)
from showpy.choreo.models import (
    FacingMode,
    PathRow,
)
from showpy.core.errors import (
    LockedError,
    SchemaError,
    SpacingError,
    UnknownRefError,
    ValidationError,
)
from showpy.core.selection import Selection
from showpy.core.types import Region
from showpy.core.util import approx_equal
from showpy.geo.regions import region
from showpy.geo.shapes import line
from showpy.show.models import Show
from showpy.show.pinning import pin
from tests.fixtures.cast_shows import rich_show
from tests.fixtures.shows import two_set_show

AT = datetime(2026, 11, 11, tzinfo=UTC)
SQUARE = Region(exterior=((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)))


def with_brass(show: Show) -> Show:
    return add_group(show, Group(key="brass", label_prefix="B", default_radius=0.5))


def test_add_spot_takes_next_free_label_and_group_radius() -> None:
    show = add_spot(with_brass(two_set_show()), (1.0, 2.0), "brass", "2")
    spot = show.cast.spot("B1")
    assert spot.group == "brass"
    assert spot.shape.radius_m == 0.5
    assert approx_equal(spot.shape.width_m, 1.0, tol=1e-9)


def test_add_spot_exists_in_every_set_off_field_before_creation() -> None:
    show = add_spot(two_set_show(), (1.0, 2.0), "trumpets", "2")
    before, after = show.set("1").get("T5"), show.set("2").get("T5")
    assert not before.on_field
    assert after.on_field
    assert before.point == after.point == (1.0, 2.0)


def test_add_spot_fills_the_lowest_gap() -> None:
    show = delete_spots(two_set_show(), ["T2"])
    assert add_spot(show, (0.0, 0.0), "trumpets", "1").cast.ids()[-1] == "T2"


def test_add_spot_uses_scheme_prefix_and_padding() -> None:
    show = two_set_show()
    scheme = LabelScheme(prefixes={"trumpets": "Tr"}, start=1, pad=2)
    show = show.model_copy(update={"cast": show.cast.model_copy(update={"label_scheme": scheme})})
    assert add_spot(show, (0.0, 0.0), "trumpets", "1").cast.ids()[-1] == "Tr01"


def test_add_spot_unknown_group_and_set_raise() -> None:
    with pytest.raises(UnknownRefError):
        add_spot(two_set_show(), (0.0, 0.0), "drums", "1")
    with pytest.raises(UnknownRefError):
        add_spot(two_set_show(), (0.0, 0.0), "trumpets", "9")


def test_add_spot_does_not_mutate_input() -> None:
    show = two_set_show()
    add_spot(show, (0.0, 0.0), "trumpets", "1")
    assert show == two_set_show()


def test_add_spots_along_spaces_evenly() -> None:
    show = add_spots_along(
        with_brass(two_set_show()), line((0.0, 0.0), (4.0, 0.0)), 3, "brass", "1"
    )
    xs = [show.set("1").get(i).x for i in ("B1", "B2", "B3")]
    assert approx_equal(xs, [0.0, 2.0, 4.0], tol=1e-9)


def test_fill_spots_by_interval() -> None:
    square = shapely.Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    show = fill_spots(with_brass(two_set_show()), square, "brass", "1", interval=1.0)
    assert len([s for s in show.cast.spots if s.group == "brass"]) == 9


def test_fill_spots_by_count_keeps_exactly_n_inside() -> None:
    square = shapely.Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    show = fill_spots(with_brass(two_set_show()), SQUARE, "brass", "1", n=7)
    brass = [show.set("1").get(s.id) for s in show.cast.spots if s.group == "brass"]
    assert len(brass) == 7
    assert all(square.buffer(1e-9).covers(shapely.Point(p.point)) for p in brass)


def test_fill_spots_needs_exactly_one_of_interval_and_n() -> None:
    square = shapely.Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    with pytest.raises(SchemaError):
        fill_spots(two_set_show(), square, "trumpets", "1")
    with pytest.raises(SchemaError):
        fill_spots(two_set_show(), square, "trumpets", "1", interval=1.0, n=3)


def test_fill_spots_rejects_n_below_one_and_empty_area() -> None:
    with pytest.raises(SpacingError):
        fill_spots(two_set_show(), SQUARE, "trumpets", "1", n=0)
    with pytest.raises(SpacingError):
        fill_spots(two_set_show(), shapely.Polygon(), "trumpets", "1", n=3)


def test_duplicate_spots_copies_attributes_at_offset() -> None:
    show = set_shape(two_set_show(), ["T1"], Shape(kind="rectangle"))
    show = duplicate_spots(show, ["T1", "T2"], (0.0, 1.0), "2")
    assert show.cast.ids()[-2:] == ("T5", "T6")
    assert show.cast.spot("T5").shape.kind == "rectangle"
    assert show.set("2").get("T5").point == (0.0, 10.0)
    assert not show.set("1").get("T6").on_field


def test_delete_spots_removes_every_reference() -> None:
    show = delete_spots(rich_show(), ["T4"])
    assert "T4" not in show.cast.ids()
    assert all("T4" not in s.entity_ids() for s in show.sets)
    assert show.forms["line1"].members == ("T1", "T2", "T3")
    spec = show.transition(("1", "2"))
    assert "T4" not in spec.rows
    assert spec.routes["r1"].members == ("T3",)
    assert spec.routes["r1"].offsets == (0.0,)
    assert show.cast.selections["pair"] == frozenset({"T1"})
    assert show.zones[2].applies_to == Selection(ids=())


def test_delete_spots_refuses_pinned_without_force() -> None:
    show = two_set_show()
    pinned = show.set("2").with_placements({"T1": {"pinned": True}})
    show = show.model_copy(update={"sets": (show.set("1"), pinned)})
    with pytest.raises(LockedError):
        delete_spots(show, ["T1"])
    assert "T1" not in delete_spots(show, ["T1"], force=True).cast.ids()


def test_delete_spots_refuses_locked_form_without_force() -> None:
    show = rich_show()
    locked = show.forms["line1"].model_copy(update={"locked": True})
    show = show.model_copy(update={"forms": {"line1": locked}})
    with pytest.raises(LockedError):
        delete_spots(show, ["T2"])


def test_delete_spots_refuses_when_others_face_the_spot() -> None:
    with pytest.raises(ValidationError) as info:
        delete_spots(rich_show(), ["T3"])
    assert "T3" in info.value.message


def test_regroup_relabels_and_returns_mapping() -> None:
    show, mapping = regroup(with_brass(rich_show()), ["T3", "T4"], "brass")
    assert mapping == {"T3": "B1", "T4": "B2"}
    assert show.cast.spot("B1").group == "brass"
    assert show.forms["line1"].members == ("T1", "T2", "B1", "B2")


def test_regroup_keeps_labels_that_already_fit() -> None:
    show = add_group(two_set_show(), Group(key="more", label_prefix="T"))
    show, mapping = regroup(show, ["T2"], "more")
    assert mapping == {}
    assert show.cast.spot("T2").group == "more"


def test_add_group_rejects_duplicate_key() -> None:
    with pytest.raises(SchemaError):
        add_group(two_set_show(), Group(key="trumpets", label_prefix="X"))


def test_edit_group_changes_fields_but_not_key() -> None:
    show = edit_group(two_set_show(), "trumpets", color="#ff0000", moves=False)
    assert show.cast.groups["trumpets"].color == "#ff0000"
    with pytest.raises(SchemaError):
        edit_group(two_set_show(), "trumpets", key="x")
    with pytest.raises(SchemaError):
        edit_group(two_set_show(), "trumpets", default_radius="wide")


def test_remove_group_reassigns_members() -> None:
    show, mapping = remove_group(with_brass(two_set_show()), "trumpets", reassign_to="brass")
    assert "trumpets" not in show.cast.groups
    assert mapping == {"T1": "B1", "T2": "B2", "T3": "B3", "T4": "B4"}


def test_remove_group_with_members_needs_target() -> None:
    with pytest.raises(SchemaError):
        remove_group(two_set_show(), "trumpets")
    with pytest.raises(SchemaError):
        remove_group(two_set_show(), "trumpets", reassign_to="trumpets")
    assert "brass" not in remove_group(with_brass(two_set_show()), "brass")[0].cast.groups


def test_relabel_numbers_in_natural_order() -> None:
    show = two_set_show()
    show = _rename_everywhere(show, {"T1": "T10", "T2": "T9"})
    relabeled, mapping = relabel(show, LabelScheme(prefixes={"trumpets": "Tp"}, start=1))
    assert mapping == {"T3": "Tp1", "T4": "Tp2", "T9": "Tp3", "T10": "Tp4"}
    assert relabeled.cast.label_scheme.prefixes == {"trumpets": "Tp"}
    assert relabeled.cast.ids() == ("Tp4", "Tp3", "Tp1", "Tp2")


def test_relabel_rejects_colliding_prefixes() -> None:
    show = add_group(two_set_show(), Group(key="other", label_prefix="O"))
    show = add_spot(show, (0.0, 0.0), "other", "1")
    with pytest.raises(SchemaError):
        relabel(show, LabelScheme(prefixes={"other": "T"}))


def test_rename_everywhere_rewrites_every_reference() -> None:
    mapping = {"T1": "A1", "T2": "A2", "T3": "A3", "T4": "A4"}
    show = _rename_everywhere(rich_show(), mapping)
    assert show.cast.ids() == ("A1", "A2", "A3", "A4")
    assert all(s.entity_ids() == ("A1", "A2", "A3", "A4") for s in show.sets)
    assert show.forms["line1"].members == ("A1", "A2", "A3", "A4")
    spec = show.transition(("1", "2"))
    assert set(spec.rows) == {"A1", "A3", "A4"}
    assert spec.rows["A1"].entity == "A1"
    assert spec.rows["A1"].facing_value == "A2"
    assert spec.routes["r1"].members == ("A3", "A4")
    anchor = show.notes[0].anchor
    assert anchor.spots == ("A2",)
    override = show.facing_overrides[0]
    assert override.entities == ("A1", "A2")
    assert override.value == "A3"
    assert show.cast.selections["pair"] == frozenset({"A1", "A4"})
    assert [z.applies_to for z in show.zones] == [
        ("A1", "A2"),
        "group:trumpets",
        Selection(ids=("A4",)),
    ]


def test_rename_everywhere_swaps_simultaneously() -> None:
    show = _rename_everywhere(rich_show(), {"T1": "T2", "T2": "T1"})
    assert show.cast.ids() == ("T2", "T1", "T3", "T4")
    assert show.transition(("1", "2")).rows["T2"].facing_value == "T1"
    assert show.set("1").get("T2").x == 0.0


def test_rename_everywhere_rejects_collisions_and_unknown_ids() -> None:
    with pytest.raises(SchemaError):
        _rename_everywhere(rich_show(), {"T1": "T2"})
    with pytest.raises(UnknownRefError):
        _rename_everywhere(rich_show(), {"T9": "T10"})


def test_rename_everywhere_leaves_degree_facings_alone() -> None:
    show = rich_show()
    row = PathRow(entity="T2", facing_mode=FacingMode.FIXED, facing_value=90.0)
    spec = show.transition(("1", "2"))
    spec = spec.model_copy(update={"rows": {**spec.rows, "T2": row}})
    show = _rename_everywhere(show.model_copy(update={"transitions": (spec,)}), {"T2": "X2"})
    assert show.transition(("1", "2")).rows["X2"].facing_value == 90.0


def test_rename_everywhere_with_empty_mapping_is_identity() -> None:
    assert _rename_everywhere(rich_show(), {}) == rich_show()


def test_define_selection_stores_resolved_ids() -> None:
    show = define_selection(two_set_show(), "ends", "T1 + T4")
    assert show.cast.selections["ends"] == frozenset({"T1", "T4"})
    with pytest.raises(SchemaError):
        define_selection(two_set_show(), "bad name", "T1")


def test_set_shape_and_set_claimable() -> None:
    show = set_shape(two_set_show(), "T1-T2", Shape(kind="rectangle"))
    assert [s.shape.kind for s in show.cast.spots] == ["rectangle", "rectangle", "circle", "circle"]
    show = set_claimable(show, ["T3"], "crew")
    assert show.cast.spot("T3").claimable == "crew"
    with pytest.raises(SchemaError):
        set_claimable(show, ["T3"], "audience")  # type: ignore[arg-type]


EDITS_THAT_NEED_FORCE = (add_spot, add_spots_along, fill_spots, duplicate_spots, regroup, relabel)
PREFIX_X = LabelScheme(prefixes={"trumpets": "X"})
UNIT_SQUARE = region([(0, 0), (2, 0), (2, 2), (0, 2)])


def _with_pinned_set() -> Show:
    return pin(two_set_show(), "set", "2")


def _with_second_group(show: Show) -> Show:
    return add_group(show, Group(key="brass", label_prefix="B"))


@pytest.mark.defect("UP-9.3-88")
@pytest.mark.xfail(strict=True, reason="UP-9.3-88")
def test_regression_add_spot_refuses_a_pinned_set() -> None:
    """Refuses an unpinned placement in a pinned set (audit 9.3; P0 #13)."""
    with pytest.raises(LockedError):
        add_spot(_with_pinned_set(), (1.0, 2.0), "trumpets", "2")


@pytest.mark.defect("UP-9.3-89")
@pytest.mark.xfail(strict=True, reason="UP-9.3-89")
def test_regression_add_spots_along_refuses_a_pinned_set() -> None:
    """Raises LockedError for a curve fill into a pinned set (audit 9.3; P0 #13)."""
    with pytest.raises(LockedError):
        add_spots_along(_with_pinned_set(), line((0, 0), (4, 0)), 3, "trumpets", "1")


@pytest.mark.defect("UP-9.3-90")
@pytest.mark.xfail(strict=True, reason="UP-9.3-90")
def test_regression_fill_spots_refuses_a_pinned_set() -> None:
    """Raises LockedError for an area fill into a pinned set (audit 9.3; P0 #13)."""
    with pytest.raises(LockedError):
        fill_spots(_with_pinned_set(), UNIT_SQUARE, "trumpets", "1", interval=1.0)


@pytest.mark.defect("UP-9.3-91")
@pytest.mark.xfail(strict=True, reason="UP-9.3-91")
def test_regression_duplicate_spots_refuses_a_pinned_set() -> None:
    """Raises LockedError for copies written into a pinned set (audit 9.3; P0 #13)."""
    with pytest.raises(LockedError):
        duplicate_spots(_with_pinned_set(), ["T1"], (0.0, 1.0), "1")


@pytest.mark.defect("UP-9.3-92")
@pytest.mark.xfail(strict=True, reason="UP-9.3-92")
def test_regression_regroup_refuses_a_pinned_spot() -> None:
    """Refuses to regroup a pinned spot (audit 9.3; P0 #13)."""
    show = _with_second_group(pin(two_set_show(), "entity", "T1"))
    with pytest.raises(LockedError):
        regroup(show, ["T1"], "brass")


@pytest.mark.defect("UP-9.3-93")
@pytest.mark.xfail(strict=True, reason="UP-9.3-93")
def test_regression_relabel_refuses_a_pinned_spot() -> None:
    """Raises LockedError when relabel renames a pinned spot, absent force (audit 9.3; P0 #13).

    Assumes the stricter policy; the audit leaves "renames allowed" open.
    """
    with pytest.raises(LockedError):
        relabel(pin(two_set_show(), "entity", "T1"), PREFIX_X)


@pytest.mark.defect("UP-9.3-94")
@pytest.mark.xfail(strict=True, reason="UP-9.3-94")
def test_regression_relabel_refuses_a_locked_form_member() -> None:
    """Raises LockedError when relabel renames a member of a locked form (audit 9.3; P0 #13)."""
    show = rich_show()
    locked = show.forms["line1"].model_copy(update={"locked": True})
    with pytest.raises(LockedError):
        relabel(show.model_copy(update={"forms": {"line1": locked}}), PREFIX_X)


@pytest.mark.defect("UP-9.3-95")
@pytest.mark.xfail(strict=True, reason="UP-9.3-95")
def test_regression_cast_edits_accept_force() -> None:
    """Gives every cast edit a `force` parameter like delete_spots (audit 9.3; P0 #13)."""
    missing = [
        fn.__name__ for fn in EDITS_THAT_NEED_FORCE if "force" not in signature(fn).parameters
    ]
    assert missing == []
