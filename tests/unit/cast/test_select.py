#!/usr/bin/env python3

"""
test_select.py --- tests the selection grammar and the spatial, ordering and pairing picks

Contains:
    test_*(): one behavior of showpy.cast.select each
"""

import pytest
import shapely

from showpy.cast.models import Cast, Group, Shape, Spot
from showpy.cast.select import (
    Selection,
    alternate,
    in_region,
    is_label_dependent,
    mirror_pairs,
    near,
    ordered_along,
    select,
    split,
    where,
)
from showpy.choreo.models import Form, Placement, Set
from showpy.core.errors import GeometryError, SchemaError, UnknownRefError
from showpy.core.types import Region
from showpy.geo.shapes import line
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show

RECT = Shape(kind="rectangle", width_m=1.0, depth_m=0.5)


def mixed_cast() -> Cast:
    groups = {
        "trumpets": Group(key="trumpets", label_prefix="T"),
        "tubas": Group(key="tubas", label_prefix="TB"),
        "flags": Group(key="flags", label_prefix="flag-"),
    }
    spots = (
        Spot(id="T1", group="trumpets", tags=frozenset({"soloist"})),
        Spot(id="T2", group="trumpets"),
        Spot(id="T10", group="trumpets"),
        Spot(id="TB1", group="tubas", shape=RECT),
        Spot(id="flag-1", group="flags", shape=RECT),
        Spot(id="flag-2", group="flags", tags=frozenset({"soloist"})),
    )
    return Cast(groups=groups, spots=spots, selections={"front": frozenset({"T2", "flag-1"})})


def mixed_show() -> Show:
    cast = mixed_cast()
    placements = tuple(
        Placement(entity=spot_id, x=float(i), y=0.0, form="arc1" if i < 3 else None, order=i)
        for i, spot_id in enumerate(cast.ids())
    )
    arc = Form(id="arc1", set="1", kind="arc", geometry={}, members=("T10", "T2", "T1"))
    base = two_set_show()
    return base.model_copy(
        update={
            "cast": cast,
            "sets": (Set(id="1", counts=0.0, placements=placements, forms=("arc1",)),),
            "forms": {"arc1": arc},
            "transitions": (),
        }
    )


def ids(source: Show | Set | Cast, expr: str) -> tuple[str, ...]:
    return select(source, expr).ids


def test_single_id_and_all() -> None:
    cast = mixed_cast()
    assert ids(cast, "T2") == ("T2",)
    assert ids(cast, "all") == cast.ids()


def test_range_keeps_range_order_and_skips_gaps() -> None:
    cast = mixed_cast()
    assert ids(cast, "T1-T2") == ("T1", "T2")
    assert ids(cast, "T1-T10") == ("T1", "T2", "T10")


def test_range_with_unknown_endpoint_raises() -> None:
    with pytest.raises(UnknownRefError):
        select(mixed_cast(), "T1-T5")


def test_prefix_matches_label_prefix_only() -> None:
    cast = mixed_cast()
    assert ids(cast, "T*") == ("T1", "T2", "T10")
    assert ids(cast, "TB*") == ("TB1",)
    assert ids(cast, "flag-*") == ("flag-1", "flag-2")


def test_hyphenated_id_is_one_word() -> None:
    assert ids(mixed_cast(), "flag-2") == ("flag-2",)


def test_group_tag_shape_and_named_selection() -> None:
    cast = mixed_cast()
    assert ids(cast, "group:flags") == ("flag-1", "flag-2")
    assert ids(cast, "tag:soloist") == ("T1", "flag-2")
    assert ids(cast, "shape:rectangle") == ("TB1", "flag-1")
    assert ids(cast, "sel:front") == ("T2", "flag-1")


def test_form_uses_member_order_on_a_show() -> None:
    assert ids(mixed_show(), "form:arc1") == ("T10", "T2", "T1")


def test_form_on_a_set_uses_placement_order() -> None:
    assert ids(mixed_show().set("1"), "form:arc1") == ("T1", "T2", "T10")


def test_form_on_a_cast_raises() -> None:
    with pytest.raises(SchemaError):
        select(mixed_cast(), "form:arc1")


def test_group_on_a_set_raises_with_hint() -> None:
    with pytest.raises(SchemaError) as info:
        select(mixed_show().set("1"), "group:flags")
    assert "Show" in info.value.hint


def test_operators_combine_left_to_right() -> None:
    cast = mixed_cast()
    assert ids(cast, "T* + TB1") == ("T1", "T2", "T10", "TB1")
    assert ids(cast, "all - T*") == ("TB1", "flag-1", "flag-2")
    assert ids(cast, "tag:soloist & T*") == ("T1",)
    assert ids(cast, "T1 + T2 & T2") == ("T2",)


def test_parentheses_group() -> None:
    assert ids(mixed_cast(), "T1 + (T2 & T2)") == ("T1", "T2")


def test_operators_next_to_parentheses_need_no_spaces() -> None:
    assert ids(mixed_cast(), "(T1)+(T2)&(T2)") == ("T2",)
    assert ids(mixed_cast(), "(T*)-(T2)") == ("T1", "T10")


def test_selection_description_is_the_expression() -> None:
    assert select(mixed_cast(), "T1 + T2").description == "T1 + T2"


@pytest.mark.parametrize(
    ("expr", "position"),
    [("T1 + ", 5), ("T1 T2", 3), ("(T1", 3), ("T1)", 2), ("- T1", 0), ("", 0), ("bogus:x", 0)],
)
def test_bad_token_reports_position(expr: str, position: int) -> None:
    with pytest.raises(SchemaError) as info:
        select(mixed_cast(), expr)
    assert info.value.context["position"] == position
    assert "group:" in info.value.hint


def test_unknown_id_suggests_close_matches() -> None:
    with pytest.raises(UnknownRefError) as info:
        select(mixed_cast(), "T1 + T3")
    assert "T1" in info.value.hint or "T2" in info.value.hint
    assert info.value.context["position"] == 5


@pytest.mark.parametrize("expr", ["group:drums", "tag:leader", "sel:back", "X*"])
def test_unknown_reference_raises(expr: str) -> None:
    with pytest.raises(UnknownRefError):
        select(mixed_cast(), expr)


def test_bad_shape_kind_raises_schema_error() -> None:
    with pytest.raises(SchemaError):
        select(mixed_cast(), "shape:hexagon")


def test_list_and_selection_pass_through_validated() -> None:
    cast = mixed_cast()
    assert select(cast, ["T2", "T1", "T2"]).ids == ("T2", "T1")
    assert select(cast, Selection(ids=("TB1",))).ids == ("TB1",)
    with pytest.raises(UnknownRefError):
        select(cast, ["T1", "T9"])


def test_is_label_dependent() -> None:
    assert is_label_dependent("T1-T4 + group:flags")
    assert is_label_dependent("T*")
    assert not is_label_dependent("group:flags - tag:soloist")
    assert not is_label_dependent("all")


def spatial_set() -> Set:
    return Set(
        id="1",
        counts=0.0,
        placements=(
            Placement(entity="A", x=-2.0, y=1.0, form="f", order=2),
            Placement(entity="B", x=2.0, y=1.0, form="f", order=0),
            Placement(entity="C", x=0.0, y=3.0, form="f", order=1),
            Placement(entity="D", x=1.0, y=1.0, on_field=False),
            Placement(entity="E", x=-1.0, y=2.0),
            Placement(entity="F", x=1.05, y=2.0),
        ),
        forms=("f",),
    )


def test_in_region_counts_boundary_and_skips_off_field() -> None:
    square = shapely.Polygon([(-2, 0), (2, 0), (2, 2), (-2, 2)])
    assert in_region(spatial_set(), square).ids == ("A", "B", "E", "F")


def test_in_region_accepts_a_ring_and_a_region() -> None:
    ring = [(-3.0, 0.0), (0.0, 0.0), (0.0, 3.0), (-3.0, 3.0)]
    assert in_region(spatial_set(), ring).ids == ("A", "C", "E")
    assert in_region(spatial_set(), Region(exterior=tuple(ring))).ids == ("A", "C", "E")


def test_in_region_rejects_too_few_points() -> None:
    with pytest.raises(GeometryError):
        in_region(spatial_set(), [(0.0, 0.0), (1.0, 1.0)])


def test_near_includes_radius_and_skips_off_field() -> None:
    assert near(spatial_set(), (2.0, 1.5), 1.1).ids == ("B", "F")
    assert near(spatial_set(), (0.0, 1.0), 2.0).ids == ("A", "B", "C", "E", "F")


def test_near_rejects_negative_radius() -> None:
    with pytest.raises(GeometryError):
        near(spatial_set(), (0.0, 0.0), -1.0)


def test_where_filters_by_predicate() -> None:
    assert where(spatial_set(), lambda p: p.y > 1.5).ids == ("C", "E", "F")


def test_ordered_along_form_x_y() -> None:
    s = spatial_set()
    assert ordered_along(s, ["A", "B", "C", "E"], by="form").ids == ("B", "C", "A", "E")
    assert ordered_along(s, ["F", "E", "A", "B"], by="x").ids == ("A", "E", "F", "B")
    assert ordered_along(s, ["C", "E", "A", "B"], by="y").ids == ("A", "B", "E", "C")


def test_ordered_along_curve_breaks_ties_by_natural_id() -> None:
    tied = Set(
        id="1",
        counts=0.0,
        placements=(
            Placement(entity="T10", x=1.0, y=1.0),
            Placement(entity="T2", x=1.0, y=-1.0),
            Placement(entity="T1", x=3.0, y=0.0),
        ),
    )
    assert ordered_along(tied, "all", by=line((0.0, 0.0), (4.0, 0.0))).ids == ("T2", "T10", "T1")


def test_split_makes_near_equal_consecutive_groups() -> None:
    parts = split(Selection(ids=tuple("ABCDEFG")), 3)
    assert [p.ids for p in parts] == [("A", "B", "C"), ("D", "E"), ("F", "G")]


def test_split_rejects_k_below_one() -> None:
    with pytest.raises(SchemaError):
        split(["A"], 0)


def test_alternate_interleaves() -> None:
    parts = alternate(list("ABCDE"))
    assert [p.ids for p in parts] == [("A", "C", "E"), ("B", "D")]
    assert [p.ids for p in alternate(list("ABCDE"), 3)] == [("A", "D"), ("B", "E"), ("C",)]


def test_mirror_pairs_pairs_across_axis_within_tol() -> None:
    assert mirror_pairs(spatial_set(), "all") == [("A", "B"), ("E", "F")]
    assert mirror_pairs(spatial_set(), "all", tol=0.01) == [("A", "B")]


def test_mirror_pairs_uses_optimal_assignment() -> None:
    crowded = Set(
        id="1",
        counts=0.0,
        placements=(
            Placement(entity="L1", x=-1.0, y=0.0),
            Placement(entity="L2", x=-1.0, y=0.3),
            Placement(entity="R1", x=1.0, y=0.25),
            Placement(entity="R2", x=1.0, y=0.55),
        ),
    )
    assert mirror_pairs(crowded, "all", tol=0.3) == [("L1", "R1"), ("L2", "R2")]


def test_mirror_pairs_respects_axis() -> None:
    assert mirror_pairs(spatial_set(), ["E", "F"], axis_x=1.0, tol=0.5) == []
    assert mirror_pairs(spatial_set(), ["C", "B"], axis_x=1.0, tol=0.1) == []


def test_regression_glued_operators_raise_instead_of_misparsing() -> None:
    with pytest.raises(SchemaError) as info:
        select(mixed_cast(), "T1+T2-T10")
    assert info.value.context["position"] == 2
    with pytest.raises(SchemaError):
        select(mixed_cast(), "T1 +T2")
    assert ids(mixed_cast(), "T1 + T2 - T2") == ("T1",)
    assert ids(mixed_cast(), "T1-T2") == ("T1", "T2")
