#!/usr/bin/env python3

"""
test_rewrite.py --- tests rewriting and dropping spot IDs across a whole show

Contains:
    test_*(): one behavior of showpy.cast.rewrite each
"""

import pytest

from showpy.cast.models import LabelScheme
from showpy.cast.rewrite import faces_entity, rewrite_spot_ids
from showpy.cast.spots import delete_spots, relabel
from showpy.choreo.models import FacingMode
from showpy.core.errors import SchemaError
from showpy.core.selection import Selection
from showpy.core.types import Region
from showpy.sheet.models import Zone, ZoneKind
from showpy.show.models import Show
from showpy.show.pinning import pin
from tests.fixtures.cast_shows import rich_show
from tests.fixtures.shows import two_set_show


def test_faces_entity_only_for_toward_entity_strings() -> None:
    assert faces_entity(FacingMode.TOWARD_ENTITY, "T2") == "T2"
    assert faces_entity(FacingMode.TOWARD_POINT, (1.0, 2.0)) is None
    assert faces_entity(FacingMode.FIXED, 90.0) is None


def test_drop_keeps_containers_and_removes_ids() -> None:
    show = rewrite_spot_ids(rich_show(), lambda i: None if i in {"T1", "T2"} else i)
    assert show.notes[0].anchor.spots == ()
    assert show.facing_overrides[0].entities == ()
    assert show.zones[0].applies_to == ()
    assert show.zones[1].applies_to == "group:trumpets"
    assert show.zones[2].applies_to == Selection(ids=("T4",))
    assert show.forms["line1"].members == ("T3", "T4")


def test_identity_rewrite_freezes_label_expressions_only() -> None:
    show = rewrite_spot_ids(rich_show(), lambda i: i)
    assert [z.applies_to for z in show.zones] == [
        ("T1", "T2"),
        "group:trumpets",
        Selection(ids=("T4",)),
    ]
    assert show.cast == rich_show().cast
    assert show.sets == rich_show().sets


SQUARE = Region(exterior=((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)))
RENAME_T_TO_X = LabelScheme(prefixes={"trumpets": "X"})


def _zone(applies_to: str) -> Zone:
    return Zone(id="z", kind=ZoneKind.CUSTOM, polygon=SQUARE, applies_to=applies_to)


def _pinned_show() -> Show:
    return pin(two_set_show(), "entity", "T1")


def _pin_ids(show: Show) -> list[str]:
    return [p.id for p in show.pins]


@pytest.mark.defect("UP-9.3-79")
@pytest.mark.xfail(strict=True, reason="UP-9.3-79")
def test_regression_relabel_rewrites_pins() -> None:
    """Renames the spot a pin names when the spot is relabeled (audit 9.3)."""
    relabeled, _ = relabel(_pinned_show(), RENAME_T_TO_X)
    assert _pin_ids(relabeled) == ["X1"]


@pytest.mark.defect("UP-9.3-80")
@pytest.mark.xfail(strict=True, reason="UP-9.3-80")
def test_regression_forced_delete_drops_pins_on_the_spot() -> None:
    """Leaves no pin naming a spot that `delete_spots(force=True)` removed (audit 9.3)."""
    remaining = delete_spots(_pinned_show(), "T1", force=True)
    assert "T1" not in _pin_ids(remaining)


@pytest.mark.defect("UP-9.3-81")
@pytest.mark.xfail(strict=True, reason="UP-9.3-81")
def test_regression_relabel_rewrites_sheet_zones() -> None:
    """Rewrites `applies_to` of zones stored on the sheet when spots are relabeled (audit 9.3)."""
    show = two_set_show()
    sheet = show.sheet.model_copy(update={"zones": (_zone("T1 + T2"),)})
    relabeled, _ = relabel(show.model_copy(update={"sheet": sheet}), RENAME_T_TO_X)
    assert "T1" not in str(relabeled.sheet.zones[0].applies_to)


@pytest.mark.defect("UP-9.3-82")
@pytest.mark.xfail(strict=True, reason="UP-9.3-82")
def test_regression_stale_selector_does_not_block_unrelated_delete() -> None:
    """Deletes T1 although a zone names the long-gone spot T9 (audit 9.3)."""
    show = two_set_show().model_copy(update={"zones": (_zone("T9"),)})
    assert "T1" not in delete_spots(show, "T1").cast.ids()


@pytest.mark.defect("UP-9.3-83")
@pytest.mark.xfail(strict=True, reason="UP-9.3-83")
def test_regression_old_glued_selector_does_not_block_relabel() -> None:
    """Relabels a show whose zone still uses the pre-2026-11-12 syntax `T1+T2` (audit 9.3)."""
    show = two_set_show().model_copy(update={"zones": (_zone("T1+T2"),)})
    relabeled, _ = relabel(show, RENAME_T_TO_X)
    assert "T1" not in relabeled.cast.ids()


@pytest.mark.defect("UP-9.3-84")
@pytest.mark.xfail(strict=True, reason="UP-9.3-84")
def test_regression_dropping_all_route_members_drops_the_route() -> None:
    """Drops or flags a route left without members (audit 9.3)."""
    show = rewrite_spot_ids(rich_show(), lambda i: None if i in {"T3", "T4"} else i)
    assert all(route.members for spec in show.transitions for route in spec.routes.values())


@pytest.mark.defect("UP-9.3-85")
@pytest.mark.xfail(strict=True, reason="UP-9.3-85")
def test_regression_dropping_all_override_entities_drops_the_override() -> None:
    """Drops or flags a facing override left without entities (audit 9.3)."""
    show = rewrite_spot_ids(rich_show(), lambda i: None if i in {"T1", "T2"} else i)
    assert all(len(override.entities) > 0 for override in show.facing_overrides)


@pytest.mark.defect("UP-9.3-86")
@pytest.mark.xfail(strict=True, reason="UP-9.3-86")
def test_regression_dropping_all_anchored_spots_drops_the_note_anchor() -> None:
    """Drops or flags a note anchored to no spots (audit 9.3)."""
    show = rewrite_spot_ids(rich_show(), lambda i: None if i == "T2" else i)
    assert all(note.anchor.spots != () for note in show.notes)


@pytest.mark.defect("UP-9.3-87")
@pytest.mark.xfail(strict=True, reason="UP-9.3-87")
def test_regression_many_to_one_rename_is_a_schema_error() -> None:
    """Reports a rename that maps two spots to one ID as a SchemaError with context (audit 9.3)."""
    with pytest.raises(SchemaError) as excinfo:
        rewrite_spot_ids(two_set_show(), lambda i: "T9" if i in {"T1", "T2"} else i)
    assert excinfo.value.context
