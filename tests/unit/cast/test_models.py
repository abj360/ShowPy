#!/usr/bin/env python3

"""
test_models.py --- tests Shape, Group, Spot, LabelScheme and Cast
"""

import math
import subprocess
import sys

import pytest
from pydantic import ValidationError as PydanticValidationError

from showpy.cast.models import Cast, Group, LabelScheme, Shape, Spot
from showpy.core.errors import GeometryError, ShowPyError, UnknownRefError


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


REJECTED = (ShowPyError, PydanticValidationError)
HASH_SEEDS = ("1", "2", "3")
SUBPROCESS_TIMEOUT_S = 20
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
    outputs = []
    for seed in HASH_SEEDS:
        done = subprocess.run(
            [sys.executable, "-c", CAST_SCRIPT_HEAD + body],
            env={"PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        outputs.append(done.stdout + done.stderr[-200:])
    return outputs


def _small_cast() -> Cast:
    return Cast(
        groups={"g": Group(key="g", label_prefix="T", limits={"speed": 2.0})},
        spots=(Spot(id="T1", group="g"),),
        selections={"s": frozenset({"T1"})},
        label_scheme=LabelScheme(prefixes={"g": "X"}),
    )


@pytest.mark.defect("UP-9.3-53")
@pytest.mark.xfail(strict=True, reason="UP-9.3-53")
def test_regression_spot_tags_serialize_in_a_stable_order() -> None:
    """Dumps Spot.tags in the same order under every hash seed (audit 9.3; P0 #9)."""
    outputs = _under_seeds("print(cast.spots[0].model_dump_json())\n")
    assert len(set(outputs)) == 1, outputs


@pytest.mark.defect("UP-9.3-54")
@pytest.mark.xfail(strict=True, reason="UP-9.3-54")
def test_regression_cast_selections_serialize_in_a_stable_order() -> None:
    """Dumps Cast.selections in the same order under every hash seed (audit 9.3; P0 #9)."""
    outputs = _under_seeds("print(cast.model_dump_json())\n")
    assert len(set(outputs)) == 1, outputs


@pytest.mark.defect("UP-9.3-55")
@pytest.mark.xfail(strict=True, reason="UP-9.3-55")
def test_regression_cast_content_hash_ignores_hash_seed() -> None:
    """Gives one content hash for a cast under every hash seed (audit 9.3; P0 #9)."""
    outputs = _under_seeds("print(content_hash(cast))\n")
    assert len(set(outputs)) == 1, outputs


@pytest.mark.defect("UP-9.3-56")
@pytest.mark.xfail(strict=True, reason="UP-9.3-56")
def test_regression_group_limits_are_immutable() -> None:
    """Refuses in-place edits to Group.limits of a frozen model (audit 9.3)."""
    group = _small_cast().groups["g"]
    with pytest.raises((TypeError, ShowPyError)):
        group.limits["speed"] = 99.0


@pytest.mark.defect("UP-9.3-57")
@pytest.mark.xfail(strict=True, reason="UP-9.3-57")
def test_regression_cast_groups_are_immutable() -> None:
    """Refuses `cast.groups.pop(...)` on a frozen cast (audit 9.3)."""
    cast = _small_cast()
    with pytest.raises((TypeError, ShowPyError, AttributeError)):
        cast.groups.pop("g")


@pytest.mark.defect("UP-9.3-58")
@pytest.mark.xfail(strict=True, reason="UP-9.3-58")
def test_regression_cast_selections_are_immutable() -> None:
    """Refuses in-place edits to Cast.selections (audit 9.3)."""
    cast = _small_cast()
    with pytest.raises((TypeError, ShowPyError, AttributeError)):
        cast.selections.pop("s")


@pytest.mark.defect("UP-9.3-59")
@pytest.mark.xfail(strict=True, reason="UP-9.3-59")
def test_regression_label_scheme_prefixes_are_immutable() -> None:
    """Refuses in-place edits to LabelScheme.prefixes (audit 9.3)."""
    scheme = _small_cast().label_scheme
    with pytest.raises((TypeError, ShowPyError)):
        scheme.prefixes["g"] = "Z"


@pytest.mark.defect("UP-9.3-60")
@pytest.mark.xfail(strict=True, reason="UP-9.3-60")
def test_regression_group_rejects_negative_default_radius() -> None:
    """Rejects a negative default radius (audit 9.3)."""
    with pytest.raises(REJECTED):
        Group(key="g", label_prefix="T", default_radius=-1.0)


@pytest.mark.defect("UP-9.3-61")
@pytest.mark.xfail(strict=True, reason="UP-9.3-61")
def test_regression_group_rejects_invalid_color() -> None:
    """Rejects a color that is not a hex string (audit 9.3)."""
    with pytest.raises(REJECTED):
        Group(key="g", label_prefix="T", color="not-a-color")


@pytest.mark.defect("UP-9.3-62")
@pytest.mark.xfail(strict=True, reason="UP-9.3-62")
def test_regression_group_rejects_nan_limits() -> None:
    """Rejects a NaN limit override (audit 9.3)."""
    with pytest.raises(REJECTED):
        Group(key="g", label_prefix="T", limits={"speed": math.nan})


@pytest.mark.defect("UP-9.3-63")
@pytest.mark.xfail(strict=True, reason="UP-9.3-63")
def test_regression_shape_rejects_negative_size_on_the_inactive_kind() -> None:
    """Rejects a negative rectangle width on a circle (audit 9.3)."""
    with pytest.raises(REJECTED):
        Shape(kind="circle", width_m=-1.0)


@pytest.mark.defect("UP-9.3-64")
@pytest.mark.xfail(strict=True, reason="UP-9.3-64")
def test_regression_label_scheme_rejects_negative_pad() -> None:
    """Rejects pad=-3 with a ShowPyError, not a bare ValueError from formatting (audit 9.3)."""
    with pytest.raises(REJECTED):
        LabelScheme(pad=-3).label("T", 1)


@pytest.mark.defect("UP-9.3-65")
@pytest.mark.xfail(strict=True, reason="UP-9.3-65")
@pytest.mark.timeout(5, method="signal")
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_label_scheme_rejects_huge_pad() -> None:
    """Rejects pad=10**7 instead of building 10 MB labels (audit 9.3)."""
    with pytest.raises(REJECTED):
        LabelScheme(pad=HUGE_PAD).label("T", 1)


@pytest.mark.defect("UP-9.3-66")
@pytest.mark.xfail(strict=True, reason="UP-9.3-66")
def test_regression_cast_rejects_group_key_mismatch() -> None:
    """Rejects a group stored under a key that is not its own (audit 9.3)."""
    with pytest.raises(REJECTED):
        Cast(groups={"a": Group(key="b", label_prefix="B")})


@pytest.mark.defect("UP-9.3-67")
@pytest.mark.xfail(strict=True, reason="UP-9.3-67")
def test_regression_cast_rejects_selections_naming_missing_spots() -> None:
    """Rejects a named selection that holds a spot the cast lacks (audit 9.3)."""
    with pytest.raises(REJECTED):
        Cast(
            groups={"g": Group(key="g", label_prefix="T")},
            spots=(Spot(id="T1", group="g"),),
            selections={"s": frozenset({"T9"})},
        )


@pytest.mark.defect("UP-9.3-68")
@pytest.mark.xfail(strict=True, reason="UP-9.3-68")
def test_regression_cast_rejects_prefixes_for_unknown_groups() -> None:
    """Rejects a label-scheme prefix that names no group (audit 9.3)."""
    with pytest.raises(REJECTED):
        Cast(
            groups={"g": Group(key="g", label_prefix="T")},
            label_scheme=LabelScheme(prefixes={"ghost": "X"}),
        )


@pytest.mark.defect("UP-9.3-69")
@pytest.mark.xfail(strict=True, reason="UP-9.3-69")
def test_regression_spot_rejects_empty_id() -> None:
    """Rejects an empty spot ID, which no selector can name (audit 9.3)."""
    with pytest.raises(REJECTED):
        Spot(id="", group="g")


@pytest.mark.defect("UP-9.3-70")
@pytest.mark.xfail(strict=True, reason="UP-9.3-70")
@pytest.mark.parametrize("spot_id", ["a b", "T(1)", "T+1"])
def test_regression_spot_rejects_ids_with_spaces_or_operators(spot_id: str) -> None:
    """Rejects IDs holding spaces, parentheses or operators (audit 9.3)."""
    with pytest.raises(REJECTED):
        Spot(id=spot_id, group="g")


@pytest.mark.defect("UP-9.3-71")
@pytest.mark.xfail(strict=True, reason="UP-9.3-71")
@pytest.mark.parametrize("spot_id", ["all", "T*", "group:1"])
def test_regression_spot_rejects_reserved_selector_words(spot_id: str) -> None:
    """Rejects IDs that collide with selector syntax such as `all` (audit 9.3)."""
    with pytest.raises(REJECTED):
        Spot(id=spot_id, group="g")
