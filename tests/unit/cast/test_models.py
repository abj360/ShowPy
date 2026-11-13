#!/usr/bin/env python3

"""
test_models.py --- tests Shape, Group, Spot, LabelScheme and Cast
"""

import pytest

from showpy.cast.models import Cast, Group, LabelScheme, Shape, Spot
from showpy.core.errors import GeometryError, UnknownRefError


def test_group_name_defaults_from_key() -> None:
    assert Group(key="front-guard", label_prefix="G").name == "Front Guard"


def test_group_keeps_explicit_name() -> None:
    assert Group(key="tpt", name="Trumpets", label_prefix="T").name == "Trumpets"


def test_shape_defaults_to_circle_and_validates_size() -> None:
    assert Shape().kind == "circle"
    assert Shape().radius_m == 0.3
    with pytest.raises(GeometryError):
        Shape(kind="rectangle", width_m=0.0, depth_m=1.0)
    with pytest.raises(GeometryError):
        Shape(radius_m=-1.0)


def test_shape_extent_for_circle_and_rectangle() -> None:
    assert Shape(radius_m=0.5).extent_m == 0.5
    assert Shape(kind="rectangle", width_m=0.6, depth_m=0.8).extent_m == pytest.approx(0.5)


def test_spot_defaults() -> None:
    spot = Spot(id="T1", group="trumpets")
    assert spot.active
    assert spot.claimable == "performer"
    assert spot.tags == frozenset()
    assert spot.symbol is None


def test_cast_spot_lookup_and_unknown_hint() -> None:
    cast = Cast(
        groups={"t": Group(key="t", label_prefix="T")},
        spots=(Spot(id="T1", group="t"), Spot(id="T2", group="t")),
    )
    assert cast.spot("T2").id == "T2"
    with pytest.raises(UnknownRefError) as info:
        cast.spot("T3")
    assert "T1" in info.value.hint or "T2" in info.value.hint


def test_cast_rejects_unknown_group_and_duplicate_ids() -> None:
    with pytest.raises(UnknownRefError):
        Cast(groups={}, spots=(Spot(id="T1", group="t"),))
    with pytest.raises(UnknownRefError):
        Cast(
            groups={"t": Group(key="t", label_prefix="T")},
            spots=(Spot(id="T1", group="t"), Spot(id="T1", group="t")),
        )


def test_label_scheme_prefix_falls_back_to_group() -> None:
    group = Group(key="t", label_prefix="T")
    assert LabelScheme().prefix_for(group) == "T"
    assert LabelScheme(prefixes={"t": "TP"}).prefix_for(group) == "TP"
    assert LabelScheme(pad=2).label("T", 3) == "T03"


def test_cast_round_trips_json() -> None:
    cast = Cast(groups={"t": Group(key="t", label_prefix="T")}, spots=(Spot(id="T1", group="t"),))
    assert Cast.model_validate_json(cast.model_dump_json()) == cast
