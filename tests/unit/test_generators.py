#!/usr/bin/env python3

"""
test_generators.py --- checks that seeded shows are deterministic, valid and use every path style
"""

from showpy.choreo.models import PathStyle
from showpy.show.documents import encode_show
from showpy.show.validate import validate_show
from tests.fixtures.generators import seeded_show

SEED = 3
SPOTS = 40
SETS = 8


def test_seeded_show_is_deterministic() -> None:
    first, second = seeded_show(SEED, SPOTS, SETS), seeded_show(SEED, SPOTS, SETS)
    assert encode_show(first) == encode_show(second)
    assert encode_show(seeded_show(SEED + 1, SPOTS, SETS)) != encode_show(first)


def test_seeded_show_has_the_requested_size_and_validates() -> None:
    show = seeded_show(SEED, SPOTS, SETS)
    assert (len(show.cast.spots), len(show.sets)) == (SPOTS, SETS)
    report = validate_show(show)
    assert report.ok, [i.code for i in report.issues if i.level == "error"]


def test_seeded_show_uses_every_path_style_ramps_and_holds() -> None:
    show = seeded_show(SEED, SPOTS, SETS)
    styles = {row.style for spec in show.transitions for row in spec.rows.values()}
    assert styles == set(PathStyle)
    assert any(t.ramp_to is not None for t in show.timeline.tempos)
    assert show.timeline.holds
