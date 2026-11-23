#!/usr/bin/env python3

"""
test_sections.py --- tests splitting shows into movements and joining them back

Contains:
    test_regression_join_after_split_restores_the_show: restores the show exactly when its two split
        sections are joined again
    test_regression_join_after_split_keeps_markers_that_repeat_a_label: keeps a marker whose label
        repeats at another count across split and join
    test_regression_join_after_split_keeps_one_facing_override_across_the_split: rejoins a facing
        override cut by a split into one override
    test_regression_split_does_not_leave_a_ramp_longer_than_its_section: clips a tempo ramp to the
        length of the section it ends up in
    test_regression_split_drops_audio_whose_section_starts_after_the_half_ends: keeps an audio track
        out of a half that ends before the track's section
    test_regression_split_puts_a_cue_in_one_half_only: keeps cue c1 in a single half of a split
    test_split_then_join_keeps_a_stale_form: carries a stale form into its section so a rejoin
        restores the show
"""

from datetime import UTC, datetime

import pytest

from e8s.choreo.form_models import Form
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import FacingMode, FacingOverride, FixedFacing, TransitionSpec
from e8s.core.errors import SchemaError, TimelineError
from e8s.core.ids import FormId, SetId, SpotId, make_tid
from e8s.core.util import approx_equal
from e8s.show.manage import add_set, index, renumber
from e8s.show.models import Pin, Show
from e8s.show.sections import join_sections, split_section
from e8s.time.clock import count_to_seconds
from e8s.time.models import AudioTrack, Cue, Hold, Marker, MeasureSetting, TempoSection
from tests.fixtures.recipes import LINE_RECIPE
from tests.fixtures.shows import two_set_show
from tests.golden.opener import opener_show
from tests.helpers import replaced_form_show

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
    override = FacingOverride(
        entities=(SpotId("T1"),),
        mode=FacingMode.FIXED,
        value=FixedFacing(degrees=90.0),
        start=10,
        end=14,
    )
    form = Form(id=FormId("f3"), set=SetId("3"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    return show.model_copy(
        update={
            "timeline": timeline,
            "facing_overrides": (override,),
            "forms": {"f3": form},
            "transitions": (TransitionSpec(id=make_tid("3", "4"), counts=8.0),),
            "pins": (Pin(target="set", id="4"), Pin(target="entity", id="T2")),
        }
    )


def test_split_shares_boundary_and_shifts_clock() -> None:
    show = _four_sets()
    first, second = split_section(show, "3")
    assert first.set_ids() == ["1", "2", "3"]
    assert second.set_ids() == ["3", "4"]
    assert second.sets[0].counts == 0.0
    assert second.forms == {"f3": show.forms[FormId("f3")]}
    assert second.transitions == show.transitions
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
        assert approx_equal(count_to_seconds(second.timeline, local), whole, abs_tol=1e-6)
    track = second.timeline.audio[0]
    assert approx_equal(
        track.sync_offset_s, 1.0 + count_to_seconds(show.timeline, 12.0), abs_tol=1e-9
    )


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
    moved = relabelled.sets[0].with_placements({"T1": PlacementPatch(x=9.0)})
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
    assert approx_equal(index(joined).total_counts, 20.0, abs_tol=TOL)


def test_regression_join_after_split_restores_the_show() -> None:
    """Restores the show exactly when its two split sections are joined again."""
    show = opener_show()
    assert join_sections(*split_section(show, "2")) == show


SPLIT_SET = "3"
WINDOW_ACROSS_SPLIT = (4.0, 16.0)
RAMP_COUNTS = 16.0


def _round_trip(show: Show) -> Show:
    return join_sections(*split_section(show, SPLIT_SET))


def _three_sets_with(**timeline_fields: object) -> Show:
    show = add_set(add_set(two_set_show(), "3", 4.0), "4", 8.0)
    return show.model_copy(update={"timeline": show.timeline.model_copy(update=timeline_fields)})


@pytest.mark.defect("UP-9.7-48")
@pytest.mark.xfail(strict=True, reason="UP-9.7-48")
def test_regression_join_after_split_keeps_markers_that_repeat_a_label() -> None:
    """Keeps a marker whose label repeats at another count across split and join (audit 9.7)."""
    markers = (Marker(label="hit", at_count=4.0), Marker(label="hit", at_count=14.0))
    show = _three_sets_with(markers=markers)
    assert _round_trip(show).timeline.markers == markers


@pytest.mark.defect("UP-9.7-49")
@pytest.mark.xfail(strict=True, reason="UP-9.7-49")
def test_regression_join_after_split_keeps_one_facing_override_across_the_split() -> None:
    """Rejoins a facing override cut by a split into one override (audit 9.7 sections)."""
    show = _three_sets_with()
    override = FacingOverride(
        entities=(SpotId("T1"),),
        mode=FacingMode.FIXED,
        value=FixedFacing(degrees=90.0),
        start=WINDOW_ACROSS_SPLIT[0],
        end=WINDOW_ACROSS_SPLIT[1],
    )
    show = show.model_copy(update={"facing_overrides": (override,)})
    assert _round_trip(show).facing_overrides == (override,)


@pytest.mark.defect("UP-9.7-50")
@pytest.mark.xfail(strict=True, reason="UP-9.7-50")
def test_regression_split_does_not_leave_a_ramp_longer_than_its_section() -> None:
    """Clips a tempo ramp to the length of the section it ends up in (audit 9.7 sections)."""
    ramp = TempoSection(
        at_count=0.0, counts_per_minute=120.0, ramp_to=60.0, ramp_counts=RAMP_COUNTS
    )
    first, _ = split_section(_three_sets_with(tempos=(ramp,)), SPLIT_SET)
    longest = max(t.at_count + (t.ramp_counts or 0.0) for t in first.timeline.tempos)
    assert longest <= index(first).total_counts + TOL


@pytest.mark.defect("UP-9.7-86")
@pytest.mark.xfail(strict=True, reason="UP-9.7-86")
def test_regression_split_drops_audio_whose_section_starts_after_the_half_ends() -> None:
    """Keeps an audio track out of a half that ends before the track's section (audit 9.7)."""
    late_track = AudioTrack(file="late.wav", label="late", section=(14.0, 20.0))
    first, _ = split_section(_three_sets_with(audio=(late_track,)), SPLIT_SET)
    length = index(first).total_counts
    stranded = [t.label for t in first.timeline.audio if t.section and t.section[0] >= length]
    assert stranded == []


@pytest.mark.defect("UP-9.7-87")
@pytest.mark.xfail(strict=True, reason="UP-9.7-87")
def test_regression_split_puts_a_cue_in_one_half_only() -> None:
    """Keeps cue c1 in a single half of a split (audit 9.7 sections)."""
    cue = Cue(label="c1", order=1, estimated_seconds=2.0)
    first, second = split_section(_three_sets_with(cues=(cue,)), SPLIT_SET)
    in_first = "c1" in {c.label for c in first.timeline.cues}
    in_second = "c1" in {c.label for c in second.timeline.cues}
    assert in_first != in_second


def test_split_then_join_keeps_a_stale_form() -> None:
    """Carries a stale form into its section so a rejoin restores the show (ruling L-G3)."""
    show = add_set(replaced_form_show(), "3", 4.0)
    first, second = split_section(show, "2")
    assert "a" in first.forms
    assert "a" in second.forms
    assert join_sections(first, second).forms == show.forms
