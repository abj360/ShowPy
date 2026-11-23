#!/usr/bin/env python3

"""
test_purity_properties.py --- checks that edit functions never mutate the show they are given

Contains:
    EDITS: one representative edit per package, keyed by package name
    assert_edit_is_pure(): serializes a show before and after one edit and requires equal bytes
    test_edits_leave_their_input_show_unchanged: applies assert_edit_is_pure to each generated
        show (audit 4.4)
    test_purity_check_fails_for_a_mutating_edit: negative control that a mutating edit is caught
"""

import contextlib
import warnings
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from e8s.cast.models import Shape
from e8s.cast.spots import set_shape
from e8s.choreo.form_ops import transform_form
from e8s.choreo.path_models import PathStyle
from e8s.choreo.paths import default_paths, set_style
from e8s.choreo.set_transforms import shift
from e8s.core.deprecation import DeprecatedWarning
from e8s.core.errors import E8sError
from e8s.examples import two_set_show
from e8s.geo.affine import translation_matrix
from e8s.notes.models import NotesDocument
from e8s.notes.notes import add_note
from e8s.show.manage import add_set, set_counts
from e8s.show.models import Anchor, Show
from e8s.time.models import Hold
from e8s.time.timeline import add_hold
from tests.strategies.forms import with_form
from tests.strategies.show import shows

pytestmark = pytest.mark.slow

MAX_SPOTS = 6
MAX_SETS = 3
MIN_SETS = 2
PURITY_EXAMPLES = 25
FORM_ID = "purity_form"
NOTE_ID = "purity_note"
NEW_SET_ID = "99P"
NEW_SET_COUNTS = 4.0
RETIMED_COUNTS = 6.0
SHIFT_M = 0.5
HOLD_SECONDS = 1.5
AT = datetime(2026, 11, 13, tzinfo=UTC)


def _spots(show: Show) -> object:
    return set_shape(show, show.cast.ids()[0], Shape(kind="rectangle"))


def _sets(show: Show) -> object:
    return shift(show.sets[-1], SHIFT_M, SHIFT_M, force=True)


def _forms(show: Show) -> object:
    return [
        transform_form(f, translation_matrix(0.0, SHIFT_M), force=True) for f in show.forms.values()
    ]


def _paths(show: Show) -> object:
    tid = (show.sets[0].id, show.sets[1].id)
    spec = show.transitions[0] if show.transitions else default_paths(show, tid)
    return set_style(spec, list(show.cast.ids()), PathStyle.HOLD, force=True)


def _timeline(show: Show) -> object:
    return add_hold(show.timeline, Hold(at_count=show.sets[-1].counts, seconds=HOLD_SECONDS))


def _notes(show: Show) -> object:
    notes = NotesDocument(show=show.id)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecatedWarning)
        return add_note(notes, show, "hit", Anchor.on_set(show.sets[-1].id), NOTE_ID, AT, "pat")


def _manage(show: Show) -> object:
    grown = add_set(show, NEW_SET_ID, NEW_SET_COUNTS, force=True)
    return grown, set_counts(show, show.sets[-1].id, RETIMED_COUNTS, rescale=True, force=True)


EDITS: dict[str, Callable[[Show], object]] = {
    "spots": _spots,
    "sets": _sets,
    "forms": _forms,
    "paths": _paths,
    "timeline": _timeline,
    "notes": _notes,
    "manage": _manage,
}


def assert_edit_is_pure(edit: Callable[[Show], object], show: Show) -> None:
    """Requires `show` to serialize identically after `edit`, even when the edit rejects it."""
    before = show.model_dump_json()
    with contextlib.suppress(E8sError):
        edit(show)
    assert show.model_dump_json() == before


def _retitle_in_place(show: Show) -> object:
    object.__setattr__(show, "title", "mutated")
    return show


@pytest.mark.parametrize("edit", EDITS.values(), ids=EDITS.keys())
@settings(max_examples=PURITY_EXAMPLES, deadline=None)
@given(data=st.data())
def test_edits_leave_their_input_show_unchanged(
    edit: Callable[[Show], object], data: st.DataObject
) -> None:
    """Leaves the input show byte-identical after one edit, even when the edit rejects it."""
    show = data.draw(shows(max_spots=MAX_SPOTS, max_sets=MAX_SETS, min_sets=MIN_SETS))
    show = data.draw(with_form(show, show.sets[0].id, FORM_ID))
    assert_edit_is_pure(edit, show)


def test_purity_check_fails_for_a_mutating_edit() -> None:
    """Fails the purity check for an edit that rewrites its input in place."""
    with pytest.raises(AssertionError):
        assert_edit_is_pure(_retitle_in_place, two_set_show())
