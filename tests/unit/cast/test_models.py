#!/usr/bin/env python3

"""
test_models.py --- tests Shape, Group, Spot, LabelScheme and Cast

Contains:
    test_*(): one behavior of showpy.cast.models each
    test_regression_spot_tags_serialize_in_a_stable_order: dumps Spot.tags identically under every
        hash seed
    test_regression_cast_selections_serialize_in_a_stable_order: dumps Cast.selections identically
        under every hash seed
    test_regression_cast_content_hash_ignores_hash_seed: gives one content hash for a cast under
        every hash seed
    test_regression_frozenset_tags_and_selections_store_in_one_order: stores a frozenset input in
        natural order under every hash seed
    test_regression_tags_and_selections_dedupe_keeping_first_occurrence: drops repeats, keeps the
        given order of non-set input
    test_regression_v02_array_documents_still_decode: decodes JSON arrays written by v0.2
    test_regression_group_limits_are_immutable: refuses in-place edits to Group.limits of a frozen
        model
    test_regression_cast_groups_are_immutable: refuses `cast.groups.pop(...)` on a frozen cast
    test_regression_cast_selections_are_immutable: refuses in-place edits to Cast.selections
    test_regression_label_scheme_prefixes_are_immutable: refuses in-place edits to
        LabelScheme.prefixes
    test_regression_group_rejects_negative_default_radius: rejects a negative default radius
    test_regression_group_rejects_invalid_color: rejects a color that is not a hex string
    test_regression_group_rejects_nan_limits: rejects a NaN limit override
    test_regression_shape_rejects_negative_size_on_the_inactive_kind: rejects a negative rectangle
        width on a circle
    test_regression_label_scheme_rejects_negative_pad: rejects pad=-3 with a ShowPyError, not a bare
        ValueError from formatting
    test_regression_label_scheme_rejects_huge_pad: rejects pad=10**7 instead of building 10 MB
        labels
    test_regression_cast_rejects_group_key_mismatch: rejects a group stored under a key that is not
        its own
    test_regression_cast_rejects_selections_naming_missing_spots: rejects a named selection that
        holds a spot the cast lacks
    test_regression_cast_rejects_prefixes_for_unknown_groups: rejects a label-scheme prefix that
        names no group
    test_regression_spot_rejects_empty_id: rejects an empty spot ID, which no selector can name
    test_regression_spot_rejects_ids_with_spaces_or_operators: rejects IDs holding spaces,
        parentheses or operators
    test_regression_spot_rejects_reserved_selector_words: rejects IDs that collide with selector
        syntax such as `all`
    test_regression_cast_spot_index_is_fresh_after_replace: finds a spot added through `replace`
        although the old cast already cached its index
"""

import math
import sys
from typing import cast as typing_cast

import pytest
from pydantic import ValidationError as PydanticValidationError

from showpy.cast.models import Cast, Group, LabelScheme, Shape, Spot
from showpy.core.errors import GeometryError, ShowPyError, UnknownRefError
from showpy.core.ids import SpotId
from tests.helpers import fails_after, outputs_under_hash_seeds


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
    spot = Spot(id=SpotId("T1"), group="trumpets")
    assert spot.active
    assert spot.claimable == "performer"
    assert spot.tags == ()
    assert spot.symbol is None


def test_cast_spot_lookup_and_unknown_hint() -> None:
    cast = Cast(
        groups={"t": Group(key="t", label_prefix="T")},
        spots=(Spot(id=SpotId("T1"), group="t"), Spot(id=SpotId("T2"), group="t")),
    )
    assert cast.spot("T2").id == "T2"
    with pytest.raises(UnknownRefError) as info:
        cast.spot("T3")
    assert "T1" in info.value.hint or "T2" in info.value.hint


def test_cast_rejects_unknown_group_and_duplicate_ids() -> None:
    with pytest.raises(UnknownRefError):
        Cast(groups={}, spots=(Spot(id=SpotId("T1"), group="t"),))
    with pytest.raises(UnknownRefError):
        Cast(
            groups={"t": Group(key="t", label_prefix="T")},
            spots=(Spot(id=SpotId("T1"), group="t"), Spot(id=SpotId("T1"), group="t")),
        )


def test_label_scheme_prefix_falls_back_to_group() -> None:
    group = Group(key="t", label_prefix="T")
    assert LabelScheme().prefix_for(group) == "T"
    assert LabelScheme(prefixes={"t": "TP"}).prefix_for(group) == "TP"
    assert LabelScheme(pad=2).label("T", 3) == "T03"


def test_cast_round_trips_json() -> None:
    cast = Cast(
        groups={"t": Group(key="t", label_prefix="T")}, spots=(Spot(id=SpotId("T1"), group="t"),)
    )
    assert Cast.model_validate_json(cast.model_dump_json()) == cast


REJECTED = (ShowPyError, PydanticValidationError)
HUGE_PAD = 10**7
CAST_SCRIPT_HEAD = (
    "from showpy.cast.models import Cast, Group, Spot\n"
    "from showpy.core.util import content_hash\n"
    "ids = [f'T{i}' for i in range(1, 7)]\n"
    "cast = Cast(\n"
    "    groups={'g': Group(key='g', label_prefix='T')},\n"
    "    spots=tuple(\n"
    "        Spot(id=i, group='g', tags=frozenset({'alpha', 'beta', 'gamma', 'delta', 'eps'}))\n"
    "        for i in ids\n"
    "    ),\n"
    "    selections={'s': frozenset(ids)},\n"
    ")\n"
)


def _under_seeds(body: str) -> list[str]:
    return outputs_under_hash_seeds(CAST_SCRIPT_HEAD + body)


def _small_cast() -> Cast:
    return Cast(
        groups={"g": Group(key="g", label_prefix="T", limits={"speed": 2.0})},
        spots=(Spot(id=SpotId("T1"), group="g"),),
        selections={"s": (SpotId("T1"),)},
        label_scheme=LabelScheme(prefixes={"g": "X"}),
    )


def test_regression_spot_tags_serialize_in_a_stable_order() -> None:
    """Dumps Spot.tags identically under every hash seed (audit 9.3; P0 #9).

    A fix must store a sorted, deduplicated tuple or reject set input.
    """
    outputs = _under_seeds("print(cast.spots[0].model_dump_json())\n")
    assert len(set(outputs)) == 1, outputs


def test_regression_cast_selections_serialize_in_a_stable_order() -> None:
    """Dumps Cast.selections identically under every hash seed (audit 9.3; P0 #9).

    A fix must store a sorted, deduplicated tuple or reject set input.
    """
    outputs = _under_seeds("print(cast.model_dump_json())\n")
    assert len(set(outputs)) == 1, outputs


def test_regression_cast_content_hash_ignores_hash_seed() -> None:
    """Gives one content hash for a cast under every hash seed (audit 9.3; P0 #9).

    A fix must store a sorted, deduplicated tuple or reject set input.
    """
    outputs = _under_seeds("print(content_hash(cast))\n")
    assert len(set(outputs)) == 1, outputs


def test_regression_frozenset_tags_and_selections_store_in_one_order() -> None:
    """Stores a frozenset input as one natural-order tuple under every hash seed (ruling D-R1)."""
    script = (
        CAST_SCRIPT_HEAD + "print(cast.spots[0].tags, cast.selections['s'])\n"
        "print(Spot(id='T1', group='g', tags=frozenset({'b', 'a10', 'a2'})).tags)\n"
    )
    outputs = outputs_under_hash_seeds(script)
    assert len(set(outputs)) == 1, outputs
    assert "('alpha', 'beta', 'delta', 'eps', 'gamma')" in outputs[0]
    assert "('T1', 'T2', 'T3', 'T4', 'T5', 'T6')" in outputs[0]
    assert "('a2', 'a10', 'b')" in outputs[0]


def test_regression_tags_and_selections_dedupe_keeping_first_occurrence() -> None:
    """Drops repeated tags and selection IDs and keeps the order a list was given in."""
    spot = Spot.model_validate({"id": "T1", "group": "g", "tags": ["z", "a", "z"]})
    assert spot.tags == ("z", "a")
    cast = Cast.model_validate(
        {
            "groups": {"g": Group(key="g", label_prefix="T")},
            "spots": (spot, Spot(id=SpotId("T2"), group="g")),
            "selections": {"s": ["T2", "T1", "T2"]},
        }
    )
    assert cast.selections["s"] == ("T2", "T1")


def test_regression_v02_array_documents_still_decode() -> None:
    """Decodes the JSON arrays a v0.2 document holds for tags and selections."""
    document = (
        '{"groups": {"g": {"key": "g", "label_prefix": "T"}},'
        ' "spots": [{"id": "T1", "group": "g", "tags": ["b", "a"]}],'
        ' "selections": {"s": ["T1"]}}'
    )
    cast = Cast.model_validate_json(document)
    assert cast.spots[0].tags == ("b", "a")
    assert cast.selections == {"s": ("T1",)}
    assert Cast.model_validate_json(cast.model_dump_json()) == cast


def test_regression_group_limits_are_immutable() -> None:
    """Refuses in-place edits to Group.limits of a frozen model (audit 9.3)."""
    group = _small_cast().groups["g"]
    with pytest.raises((TypeError, ShowPyError)):
        typing_cast(dict[str, float], group.limits)["speed"] = 99.0


def test_regression_cast_groups_are_immutable() -> None:
    """Refuses `cast.groups.pop(...)` on a frozen cast (audit 9.3)."""
    cast = _small_cast()
    with pytest.raises((TypeError, ShowPyError, AttributeError)):
        typing_cast(dict[str, Group], cast.groups).pop("g")


def test_regression_cast_selections_are_immutable() -> None:
    """Refuses in-place edits to Cast.selections (audit 9.3)."""
    cast = _small_cast()
    with pytest.raises((TypeError, ShowPyError, AttributeError)):
        typing_cast(dict[str, tuple[str, ...]], cast.selections).pop("s")


def test_regression_label_scheme_prefixes_are_immutable() -> None:
    """Refuses in-place edits to LabelScheme.prefixes (audit 9.3)."""
    scheme = _small_cast().label_scheme
    with pytest.raises((TypeError, ShowPyError)):
        typing_cast(dict[str, str], scheme.prefixes)["g"] = "Z"


def test_regression_group_rejects_negative_default_radius() -> None:
    """Rejects a negative default radius (audit 9.3)."""
    with pytest.raises(REJECTED):
        Group(key="g", label_prefix="T", default_radius=-1.0)


def test_regression_group_rejects_invalid_color() -> None:
    """Rejects a color that is not a hex string (audit 9.3)."""
    with pytest.raises(REJECTED):
        Group(key="g", label_prefix="T", color="not-a-color")


def test_regression_group_rejects_nan_limits() -> None:
    """Rejects a NaN limit override (audit 9.3)."""
    with pytest.raises(REJECTED):
        Group(key="g", label_prefix="T", limits={"speed": math.nan})


def test_regression_shape_rejects_negative_size_on_the_inactive_kind() -> None:
    """Rejects a negative rectangle width on a circle (audit 9.3)."""
    with pytest.raises(REJECTED):
        Shape(kind="circle", width_m=-1.0)


def test_regression_label_scheme_rejects_negative_pad() -> None:
    """Rejects pad=-3 with a ShowPyError, not a bare ValueError from formatting (audit 9.3)."""
    with pytest.raises(REJECTED):
        LabelScheme(pad=-3).label("T", 1)


@fails_after(5)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_label_scheme_rejects_huge_pad() -> None:
    """Rejects pad=10**7 instead of building 10 MB labels (audit 9.3)."""
    with pytest.raises(REJECTED):
        LabelScheme(pad=HUGE_PAD).label("T", 1)


def test_regression_cast_rejects_group_key_mismatch() -> None:
    """Rejects a group stored under a key that is not its own (audit 9.3)."""
    with pytest.raises(REJECTED):
        Cast(groups={"a": Group(key="b", label_prefix="B")})


def test_regression_cast_rejects_selections_naming_missing_spots() -> None:
    """Rejects a named selection that holds a spot the cast lacks (audit 9.3)."""
    with pytest.raises(REJECTED):
        Cast(
            groups={"g": Group(key="g", label_prefix="T")},
            spots=(Spot(id=SpotId("T1"), group="g"),),
            selections={"s": (SpotId("T9"),)},
        )


def test_regression_cast_rejects_prefixes_for_unknown_groups() -> None:
    """Rejects a label-scheme prefix that names no group (audit 9.3)."""
    with pytest.raises(REJECTED):
        Cast(
            groups={"g": Group(key="g", label_prefix="T")},
            label_scheme=LabelScheme(prefixes={"ghost": "X"}),
        )


def test_regression_spot_rejects_empty_id() -> None:
    """Rejects an empty spot ID, which no selector can name (audit 9.3)."""
    with pytest.raises(REJECTED):
        Spot(id=SpotId(""), group="g")


@pytest.mark.parametrize("spot_id", ["a b", "T(1)", "T+1"])
def test_regression_spot_rejects_ids_with_spaces_or_operators(spot_id: str) -> None:
    """Rejects IDs holding spaces, parentheses or operators (audit 9.3)."""
    with pytest.raises(REJECTED):
        Spot(id=SpotId(spot_id), group="g")


@pytest.mark.parametrize("spot_id", ["all", "T*", "group:1"])
def test_regression_spot_rejects_reserved_selector_words(spot_id: str) -> None:
    """Rejects IDs that collide with selector syntax such as `all` (audit 9.3)."""
    with pytest.raises(REJECTED):
        Spot(id=SpotId(spot_id), group="g")


def test_regression_cast_spot_index_is_fresh_after_replace() -> None:
    """Finds a spot added through `replace` although the old cast already cached its index."""
    cast = _small_cast()
    assert cast.spot("T1").group == "g"
    grown = cast.replace(spots=(*cast.spots, Spot(id=SpotId("T2"), group="g")))
    assert grown.spot("T2").id == "T2"
    with pytest.raises(UnknownRefError):
        cast.spot("T2")
