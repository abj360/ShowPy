#!/usr/bin/env python3

"""
test_strategy_coverage.py --- pins what the show strategies can generate, one feature per case

Each case asks Hypothesis to find a generated show (or path row) with one feature. Cases are
parametrized over the library's own enums and literals, so a new path style, easing, facing
mode or form kind without strategy support fails here.
"""

from collections.abc import Callable, Iterator
from typing import get_args

import pytest
from hypothesis import HealthCheck, Phase, find, settings
from hypothesis import strategies as st
from hypothesis.errors import NoSuchExample

from showpy.choreo.models import (
    Easing,
    FacingMode,
    FacingSpec,
    FormKind,
    PathRow,
    PathStyle,
    Placement,
    TransitionSpec,
)
from showpy.show.models import Show
from tests.strategies.paths import path_rows
from tests.strategies.show import shows

pytestmark = pytest.mark.slow

SEARCH = settings(
    max_examples=2000,
    phases=[Phase.generate],
    database=None,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)
EASING_KINDS: tuple[str, ...] = get_args(Easing.model_fields["kind"].annotation)
FORM_FACING_MODES: tuple[str, ...] = get_args(FacingSpec.model_fields["mode"].annotation)
FORM_KINDS: tuple[str, ...] = get_args(FormKind.__value__)


def _rows(show: Show) -> Iterator[tuple[TransitionSpec, PathRow]]:
    return ((spec, row) for spec in show.transitions for row in spec.rows.values())


def _moves(show: Show) -> Iterator[tuple[Placement, Placement]]:
    for before, after in zip(show.sets, show.sets[1:], strict=False):
        yield from ((before.get(p.entity), p) for p in after.placements)


def _found(predicate: Callable[[Show], bool]) -> None:
    """Finds a generated show with the feature; maps to a bool so no show repr is built."""
    assert find(shows().map(predicate), bool, settings=SEARCH)


@pytest.mark.parametrize("style", list(PathStyle))
def test_shows_generate_every_path_style(style: PathStyle) -> None:
    _found(lambda show: any(row.style is style for _, row in _rows(show)))


@pytest.mark.parametrize("style", list(PathStyle))
def test_path_rows_generate_every_path_style(style: PathStyle) -> None:
    styles = shows(min_sets=2).flatmap(path_rows).map(lambda row: row.style)
    assert find(styles, lambda found: found is style, settings=SEARCH) is style


@pytest.mark.parametrize("kind", EASING_KINDS)
def test_shows_generate_every_easing(kind: str) -> None:
    _found(lambda show: any(row.easing.kind == kind for _, row in _rows(show)))


@pytest.mark.parametrize("mode", list(FacingMode))
def test_shows_generate_every_path_facing_mode(mode: FacingMode) -> None:
    _found(lambda show: any(row.facing_mode is mode for _, row in _rows(show)))


@pytest.mark.parametrize("mode", FORM_FACING_MODES)
def test_shows_generate_every_form_facing_mode(mode: str) -> None:
    _found(lambda show: any(form.facing.mode == mode for form in show.forms.values()))


@pytest.mark.parametrize("kind", FORM_KINDS)
def test_shows_generate_every_form_kind(kind: str) -> None:
    _found(lambda show: any(form.kind == kind for form in show.forms.values()))


@pytest.mark.parametrize("timed", [True, False])
def test_shows_generate_timed_and_untimed_waypoints(timed: bool) -> None:
    def has(show: Show) -> bool:
        return any((w.at is not None) == timed for _, r in _rows(show) for w in r.waypoints)

    _found(has)


def test_shows_generate_delayed_starts_and_early_ends() -> None:
    _found(lambda show: any(row.start > 0 and row.end is None for _, row in _rows(show)))
    _found(lambda s: any(r.end is not None and r.end < spec.counts for spec, r in _rows(s)))


@pytest.mark.parametrize("entering", [True, False])
def test_shows_generate_entrances_and_exits(entering: bool) -> None:
    def has(show: Show) -> bool:
        return any(a.on_field != entering and b.on_field == entering for a, b in _moves(show))

    _found(has)


def test_search_settings_cannot_find_the_impossible() -> None:
    with pytest.raises(NoSuchExample):
        find(st.integers(0, 3), lambda n: n > 3, settings=SEARCH)
