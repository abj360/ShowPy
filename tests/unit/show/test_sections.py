#!/usr/bin/env python3

"""
test_sections.py --- tests splitting shows into movements and joining them back
"""

from datetime import UTC, datetime

import pytest

from showpy.choreo.models import FacingMode, FacingOverride, Form, TransitionSpec
from showpy.core.errors import SchemaError, TimelineError
from showpy.core.util import approx_equal
from showpy.notes.models import Anchor, Note
from showpy.show.manage import add_set, index, renumber
from showpy.show.models import Pin, Show
from showpy.show.sections import join_sections, split_section
from showpy.time.clock import count_to_seconds
from showpy.time.models import AudioTrack, Hold, Marker, MeasureSetting, TempoSection
from tests.fixtures.shows import two_set_show
from tests.golden.opener import opener_show

AT = datetime(2026, 11, 11, tzinfo=UTC)
TOL = 1e-9


def _four_sets() -> Show:
    show = add_set(add_set(two_set_show(), "3", 4.0), "4", 8.0)
    timeline = show.timeline.model_copy(
        update={
            "tempos": (
                TempoSection(at_count=0.0, counts_per_minute=120.0, ramp_to=60.0, ramp_counts=16.0),
            ),
            "holds": (Hold(at_count=12.0, seconds=2.0), Hold(at_count=4.0, seconds=1.0)),
            "markers": (Marker(label="hit", at_count=14.0),),
            "measures": MeasureSetting(counts_per_measure=4.0),
            "audio": (AudioTrack(file="a.wav", label="music", sync_offset_s=1.0),),
        }
    )
    override = FacingOverride(entities=("T1",), mode=FacingMode.FIXED, value=90.0, start=10, end=14)
    form = Form(id="f3", set="3", kind="line", geometry={}, members=("T1",))
    notes = (
        Note(id="a", text="x", anchor=Anchor.on_set("2"), author="d", created_at=AT),
        Note(id="b", text="y", anchor=Anchor.on_set("4"), author="d", created_at=AT),
    )
    return show.model_copy(
        update={
            "timeline": timeline,
            "facing_overrides": (override,),
            "forms": {"f3": form},
            "transitions": (TransitionSpec(id=("3", "4"), counts=8.0),),
            "notes": notes,
            "pins": (Pin(target="set", id="4"), Pin(target="entity", id="T2")),
        }
    )


def test_split_shares_boundary_and_shifts_clock() -> None:
    show = _four_sets()
    first, second = split_section(show, "3")
    assert first.set_ids() == ["1", "2", "3"]
    assert second.set_ids() == ["3", "4"]
    assert second.sets[0].counts == 0.0
    assert second.forms == {"f3": show.forms["f3"]}
    assert second.transitions == show.transitions
    assert [n.id for n in first.notes] == ["a"]
    assert [p.id for p in second.pins] == ["4", "T2"]
    assert second.facing_overrides[0].start == 0.0
    assert first.facing_overrides[0].end == 12.0
    assert [h.at_count for h in second.timeline.holds] == [0.0]
    assert second.timeline.markers[0].at_count == 2.0
    assert second.timeline.measures == MeasureSetting(counts_per_measure=4.0, start_measure=4)


def test_split_keeps_seconds_of_later_counts() -> None:
    show = _four_sets()
    _, second = split_section(show, "3")
    for local in (0.5, 2.0, 7.0):
        whole = count_to_seconds(show.timeline, 12.0 + local) - count_to_seconds(
            show.timeline, 12.0
        )
        assert approx_equal(count_to_seconds(second.timeline, local), whole, 1e-6)
    track = second.timeline.audio[0]
    assert approx_equal(track.sync_offset_s, 1.0 + count_to_seconds(show.timeline, 12.0), 1e-9)


def test_split_mid_measure_raises() -> None:
    show = add_set(two_set_show(), "3", 3.0)
    timeline = show.timeline.model_copy(update={"measures": MeasureSetting(counts_per_measure=4.0)})
    with pytest.raises(TimelineError):
        split_section(show.model_copy(update={"timeline": timeline}), "3")


def test_join_inverts_split() -> None:
    show = _four_sets()
    joined = join_sections(*split_section(show, "3"))
    assert joined.sets == show.sets
    assert joined.transitions == show.transitions
    assert joined.forms == show.forms
    assert sorted(n.id for n in joined.notes) == ["a", "b"]
    assert [e.start_count for e in index(joined).entries] == [0.0, 8.0, 12.0, 20.0]
    assert [h.at_count for h in joined.timeline.holds] == [4.0, 12.0]


def test_join_disjoint_sections_and_clashes() -> None:
    first = two_set_show()
    second, _ = renumber(add_set(two_set_show(), "3", 4.0))
    with pytest.raises(SchemaError):
        join_sections(first, second)
    relabelled = second.model_copy(
        update={"sets": tuple(s.model_copy(update={"id": f"B{s.id}"}) for s in second.sets)}
    )
    joined = join_sections(first, relabelled)
    assert joined.set_ids() == ["1", "2", "B1", "B2", "B3"]
    other_cast = relabelled.model_copy(update={"cast": first.cast.model_copy(update={"spots": ()})})
    with pytest.raises(SchemaError):
        join_sections(first, other_cast)
    moved = relabelled.sets[0].with_placements({"T1": {"x": 9.0}})
    bad_boundary = second.model_copy(
        update={
            "sets": (
                second.sets[0].model_copy(update={"id": "2", "placements": moved.placements}),
                *second.sets[1:],
            )
        }
    )
    with pytest.raises(SchemaError):
        join_sections(first, bad_boundary)
    assert approx_equal(index(joined).total_counts, 20.0, TOL)


def test_regression_join_after_split_restores_the_show() -> None:
    show = opener_show()
    assert join_sections(*split_section(show, "2")) == show
