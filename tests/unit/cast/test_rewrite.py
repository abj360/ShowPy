#!/usr/bin/env python3

"""
test_rewrite.py --- tests rewriting and dropping spot IDs across a whole show

Contains:
    test_*(): one behavior of stage.cast.rewrite each
"""

from stage.cast.rewrite import faces_entity, rewrite_spot_ids
from stage.choreo.models import FacingMode
from stage.core.selection import Selection
from tests.fixtures.cast_shows import rich_show


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
