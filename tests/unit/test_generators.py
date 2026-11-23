#!/usr/bin/env python3

"""
test_generators.py --- checks seeded shows are deterministic, valid, varied in style and entrances

Contains:
    test_seeded_show_is_deterministic: builds equal shows from equal seeds, different from others
    test_seeded_show_has_the_requested_size_and_validates: builds the requested spots and sets
    test_seeded_show_uses_every_path_style_ramps_and_holds: uses every style, a ramp and a hold
    test_seeded_show_has_entrances_and_exits: moves spots both off and onto the field
    test_regression_seeded_show_never_draws_from_generator_uniform: scales random() in Python, so
        arm64 and x86_64 draw the same form centers, spans and headings
"""

import pytest

from showpy.choreo.path_models import PathStyle
from showpy.core.util import seeded_rng
from showpy.show.documents import encode_show
from showpy.show.validate import validate_show
from tests.fixtures import generators
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


def test_seeded_show_has_entrances_and_exits() -> None:
    show = seeded_show(SEED, SPOTS, SETS)
    moves = [
        (before.get(p.entity).on_field, p.on_field)
        for before, after in zip(show.sets, show.sets[1:], strict=False)
        for p in after.placements
    ]
    assert (True, False) in moves
    assert (False, True) in moves


class _UniformRefusingRng:
    """Wraps a generator and refuses `uniform`, whose last bit differs between platforms."""

    def __init__(self, seed: int) -> None:
        self._rng = seeded_rng(seed)

    def uniform(self, *_args: object, **_kwargs: object) -> float:
        raise AssertionError("Generator.uniform fuses a multiply-add on arm64 only")

    def __getattr__(self, name: str) -> object:
        return getattr(self._rng, name)


def test_regression_seeded_show_never_draws_from_generator_uniform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Builds the show without `Generator.uniform`, so it draws the same bits on every platform.

    The large golden show differed between arm64 macOS and x86_64 Linux from its second set on,
    because numpy's arm64 build fuses `low + scale * u` into one rounding.
    """
    expected = encode_show(seeded_show(SEED, SPOTS, SETS))
    monkeypatch.setattr(generators, "seeded_rng", _UniformRefusingRng)
    assert encode_show(seeded_show(SEED, SPOTS, SETS)) == expected
