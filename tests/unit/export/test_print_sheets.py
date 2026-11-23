#!/usr/bin/env python3

"""
test_print_sheets.py --- tests coordinate sheets, set charts and the cue sheet

Also holds the audited crossing-label defects (UP-9.5-39, UP-9.5-40) as strict xfails.

Contains:
    test_regression_crossing_labels_tell_both_sides_apart(): lists two labels for two `30` lines
    test_regression_out_and_back_crossing_is_reported_twice(): reports both crossings of a loop that
        cuts the same guide twice
"""

import math

import pytest

from e8s.choreo.models import Placement, PlacementPatch, Set
from e8s.choreo.path_models import PathRow, PathStyle
from e8s.core.errors import SchemaError, UnknownRefError
from e8s.core.ids import FormId, SetId, SpotId
from e8s.core.util import approx_equal
from e8s.export._models import Claim
from e8s.export._print_sheets import chart, coordinate_sheet, coordinate_sheets, cue_sheet
from e8s.sheet.models import GuideLine
from e8s.show.models import Show
from e8s.time.models import Cue, Marker, MeasureSetting, Timeline, TimelineMode
from tests.fixtures.export_shows import AT, guided_show, live_publication, performer
from tests.fixtures.motion_shows import with_rows, with_targets
from tests.fixtures.shows import two_set_show

TOL = 1e-9
GUIDE_SPAN = 50.0
GUIDE_Y = 7.0
FULL_TURN = 360.0
TWO_CROSSINGS = 2


def _three_set_show() -> Show:
    show = two_set_show()
    third = Set(
        id=SetId("3"),
        counts=4.0,
        placements=tuple(p.model_copy(update={"x": p.x + 1.0}) for p in show.sets[1].placements),
        label="Hit",
        cue="B",
    )
    return show.model_copy(update={"sets": (*show.sets, third)})


def test_sheet_has_one_row_per_set_with_counts_and_positions() -> None:
    sheet = coordinate_sheet(guided_show(), "T1")
    assert sheet.entity == "T1"
    assert [r.set for r in sheet.rows] == ["1", "2"]
    assert [r.counts for r in sheet.rows] == [0.0, 8.0]
    assert [r.cumulative for r in sheet.rows] == [0.0, 8.0]
    assert sheet.rows[1].xy == (0.0, 9.0)
    assert sheet.rows[1].position_text == "0.00 m right of center, 9.00 m upstage of front edge"
    assert sheet.rows[0].measure is None


def test_cumulative_counts_and_measures_accumulate() -> None:
    show = _three_set_show()
    timeline = show.timeline.model_copy(update={"measures": MeasureSetting(counts_per_measure=4.0)})
    show = show.model_copy(update={"timeline": timeline})
    rows = coordinate_sheet(show, "T2").rows
    assert [r.cumulative for r in rows] == [0.0, 8.0, 12.0]
    assert [(r.measure.measure, r.measure.count_in_measure) for r in rows if r.measure] == [
        (1, 1.0),
        (3, 1.0),
        (4, 1.0),
    ]


def test_behavior_describes_the_arriving_move() -> None:
    behaviors = {s.entity: s.rows[1].behavior for s in coordinate_sheets(guided_show())}
    assert behaviors == {
        "T1": "move 8",
        "T2": "delay 2, move 6",
        "T3": "hold",
        "T4": "rotate 180° in 8",
    }
    assert coordinate_sheet(guided_show(), "T1").rows[0].behavior == "start"


def test_behavior_reports_an_early_arrival() -> None:
    show = with_rows({"T1": PathRow(entity=SpotId("T1"), end=6.0)})
    assert coordinate_sheet(show, "T1").rows[1].behavior == "move 6, hold 2"


def test_crossings_list_guides_crossed_on_the_way_in() -> None:
    crossings = {s.entity: s.rows[1].crossings for s in coordinate_sheets(guided_show())}
    assert crossings == {"T1": ("A",), "T2": ("A",), "T3": (), "T4": ("A",)}
    assert coordinate_sheet(guided_show(), "T1").rows[0].crossings == ()


def test_precision_overrides_the_coordinate_rounding() -> None:
    row = coordinate_sheet(guided_show(), "T2", precision=0.5).rows[0]
    assert row.position_text == "2.0 m right of center, 5.0 m upstage of front edge"
    with pytest.raises(SchemaError):
        coordinate_sheet(guided_show(), "T2", precision=0.0)


def test_facing_comes_from_the_engine() -> None:
    show = guided_show()
    turned = show.sets[1].with_placements({"T1": PlacementPatch(facing=90.0)})
    show = show.model_copy(update={"sets": (show.sets[0], turned)})
    rows = coordinate_sheet(show, "T1").rows
    assert approx_equal(rows[0].facing, 0.0, abs_tol=TOL)
    assert approx_equal(rows[1].facing, 90.0, abs_tol=TOL)


def test_performer_name_appears_once_claimed() -> None:
    show = guided_show()
    pub = live_publication(show)
    claim = Claim(spot=SpotId("T2"), performer=performer("Ada"), claimed_at=AT)
    pub = pub.model_copy(update={"claims": (claim,)})
    sheets = coordinate_sheets(show, "T1-T2", publication=pub)
    assert [(s.entity, s.performer_name) for s in sheets] == [("T1", None), ("T2", "Ada")]


def test_unknown_spot_is_refused_with_suggestions() -> None:
    with pytest.raises(UnknownRefError) as caught:
        coordinate_sheet(guided_show(), "T9")
    assert "T4" in caught.value.hint


def test_off_field_rows_say_so() -> None:
    show = two_set_show()
    first = show.sets[0].with_placements({"T1": PlacementPatch(on_field=False)})
    show = show.model_copy(update={"sets": (first, show.sets[1])})
    rows = coordinate_sheet(show, "T1").rows
    assert rows[0].position_text == "off field"
    assert rows[1].behavior == "enter, move 8"


def test_chart_lists_every_spot_of_one_set() -> None:
    show = guided_show()
    show = show.model_copy(
        update={
            "sets": (
                show.sets[0].with_placements(
                    {"T3": PlacementPatch(note="solo", form=FormId("line1"))}
                ),
                show.sets[1],
            )
        }
    )
    result = chart(show, "1")
    assert result.set_id == "1"
    assert [r.entity for r in result.rows] == ["T1", "T2", "T3", "T4"]
    third = result.rows[2]
    assert (third.group, third.note, third.form, third.xy) == (
        "trumpets",
        "solo",
        "line1",
        (4.0, 5.0),
    )
    assert third.position_text.startswith("4.00 m right of center")


def test_chart_refuses_an_unknown_set() -> None:
    with pytest.raises(UnknownRefError):
        chart(guided_show(), "9")


def test_cue_sheet_merges_sets_markers_and_cues_in_count_order() -> None:
    show = _three_set_show()
    timeline = show.timeline.model_copy(
        update={
            "markers": (Marker(label="hit", at_count=4.0),),
            "measures": MeasureSetting(counts_per_measure=4.0),
        }
    )
    table = cue_sheet(show.model_copy(update={"timeline": timeline}))
    assert list(table.columns) == ["count", "seconds", "measure", "kind", "label", "detail"]
    assert list(table["kind"]) == ["marker", "set"]
    assert list(table["count"]) == [4.0, 12.0]
    assert list(table["seconds"]) == [2.0, 6.0]
    assert list(table["measure"]) == ["2:1", "4:1"]
    assert list(table["label"]) == ["hit", "set 3"]
    assert list(table["detail"]) == ["custom", "Hit; cue B"]


def test_cue_sheet_places_cue_mode_cues_by_estimated_time() -> None:
    show = two_set_show()
    timeline = Timeline(
        mode=TimelineMode.CUES,
        cues=(
            Cue(label="lights", order=1, estimated_seconds=10.0),
            Cue(label="curtain", order=2, estimated_seconds=5.0),
        ),
    )
    table = cue_sheet(show.model_copy(update={"timeline": timeline}))
    assert list(table["label"]) == ["lights", "curtain"]
    assert list(table["seconds"]) == [0.0, 10.0]
    assert all(math.isnan(c) for c in table["count"])


def test_sheets_never_mutate_the_show() -> None:
    show = guided_show()
    before = show.model_dump()
    coordinate_sheets(show)
    chart(show, "2")
    cue_sheet(show)
    assert show.model_dump() == before


def test_single_set_show_has_start_rows_only() -> None:
    show = two_set_show()
    show = show.model_copy(update={"sets": show.sets[:1]})
    rows = coordinate_sheet(show, "T1").rows
    assert [(r.set, r.behavior, r.crossings) for r in rows] == [("1", "start", ())]
    assert rows[0].xy == Placement(entity=SpotId("T1"), x=0.0, y=5.0).point


def _twin_guides_show() -> Show:
    """Returns the two-set show with guides labelled `30` at x=-1 and x=1; T2 crosses both."""
    show = with_targets({"T2": {"x": -3.0}})
    guides = tuple(
        GuideLine(
            id=f"yard-{side}", kind="yard", points=((x, -GUIDE_SPAN), (x, GUIDE_SPAN)), label="30"
        )
        for side, x in (("left", -1.0), ("right", 1.0))
    )
    return show.model_copy(update={"sheet": show.sheet.model_copy(update={"guides": guides})})


@pytest.mark.defect("UP-9.5-39")
@pytest.mark.xfail(strict=True, reason="UP-9.5-39")
def test_regression_crossing_labels_tell_both_sides_apart() -> None:
    """Lists two distinct labels when a path crosses both `30` lines of the field (audit 9.5).

    The label is deduplicated by text, so the home and away 30 collapse into one `30`.
    """
    crossings = coordinate_sheet(_twin_guides_show(), "T2").rows[1].crossings
    assert len(set(crossings)) == TWO_CROSSINGS


@pytest.mark.defect("UP-9.5-40")
@pytest.mark.xfail(strict=True, reason="UP-9.5-40")
def test_regression_out_and_back_crossing_is_reported_twice() -> None:
    """Reports both crossings of a loop that cuts the same guide twice (audit 9.5).

    A full turn about a pivot on the guide crosses it going out and coming back; only the
    first crossing is reported today.
    """
    loop = PathRow(
        entity=SpotId("T1"), style=PathStyle.ROTATE, pivot=(0.0, GUIDE_Y), sweep_deg=FULL_TURN
    )
    show = with_rows({"T1": loop}, show=with_targets({"T1": {"y": 5.0}}, show=guided_show()))
    assert len(coordinate_sheet(show, "T1").rows[1].crossings) == TWO_CROSSINGS
