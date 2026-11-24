#!/usr/bin/env python3

"""
model_examples.py --- example instances of every public model, for JSON round-trip tests

Contains:
    ABSTRACT_MODELS: names the public model bases that are never instantiated themselves
    NOT_JSON_MODELS: names the public models that refuse to_json and from_json by design
    harvested_examples(): collects every model instance the example and seeded shows hold
    built_examples(): builds instances of the model classes the example shows do not hold
    model_examples(): maps each model class to its examples, variants included
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import cache

import numpy as np

import e8s
from e8s.cast.spots import rename_spots
from e8s.choreo.avoid import min_gaps
from e8s.choreo.engine import PathEngine
from e8s.choreo.engine_models import (
    BendFix,
    DelayFix,
    Fix,
    ReassignFix,
    StaggerFix,
)
from e8s.choreo.form_fns import DEFAULT_FORM_FNS, form_fns
from e8s.choreo.form_models import (
    CrowdedStroke,
    RoseFormParams,
    SineWaveFormParams,
    SpiralFormParams,
)
from e8s.choreo.form_text import text_report
from e8s.choreo.forms import line_form, text_form
from e8s.choreo.match import match
from e8s.choreo.match_models import MatchOptions, MatchWeights
from e8s.choreo.models import PlacementPatch
from e8s.choreo.motion import DEFAULT_MOTIONS, motions
from e8s.choreo.motion_stats import motion_contrast, speed
from e8s.choreo.path_models import (
    EntityFacing,
    FacingMode,
    FacingOverride,
    FigureEightParams,
    FixedFacing,
    PointFacing,
    SpiralParams,
    ZigzagParams,
)
from e8s.choreo.recipe_models import (
    CurveRecipe,
    CustomRecipe,
    FillRecipe,
    GeneratedRecipe,
    PolylineRecipe,
    ScatterRecipe,
    SvgRecipe,
)
from e8s.choreo.set_snapping import snap
from e8s.core.error_codes import ErrorCode
from e8s.core.ids import NoteId, SetId, SpotId, make_tid
from e8s.core.selection import Selection
from e8s.core.types import Box2, Box3, FrozenModel, Region
from e8s.core.units import DEFAULT_UNITS
from e8s.core.validation import ValidationIssue, ValidationReport
from e8s.examples import crossing_show, two_set_show
from e8s.geo.curves import (
    BezierCurve,
    CircleCurve,
    CompositeCurve,
    EllipseCurve,
    Polyline,
    Segment,
    SpiralCurve,
    SplineCurve,
    SvgCurve,
    TrimmedCurve,
)
from e8s.geo.fonts import BUNDLED_FONTS, bundled_fonts
from e8s.geo.measure import form_metrics
from e8s.geo.text_layout import layout_text
from e8s.show._notes_document import Note, NotesDocument
from e8s.show.anchors import Anchor
from e8s.show.collab import preview_proposal, propose
from e8s.show.collab_models import (
    CanvasEvent,
    Comment,
    CountsOp,
    FormOp,
    Highlight,
    PathsOp,
    PlacementOp,
)
from e8s.show.counts import spot_counts
from e8s.show.history import diff_paths, diff_sets
from e8s.show.history_models import Revision
from e8s.show.manage import index
from e8s.show.migrate import DroppedFields, MigrationReport
from e8s.show.models import Pin
from e8s.time.measures import count_to_measure
from e8s.time.models import AudioTrack, Cue, Hold, Marker, MeasureSetting
from e8s.time.timeline import set_measures
from tests.fixtures.generators import seeded_show
from tests.fixtures.model_variants import variants

AT = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
LINE_POINTS = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.5], [3.0, 1.5]])
# Bases that exist to be subclassed; every concrete subclass has its own example.
ABSTRACT_MODELS = frozenset(
    {"FrozenModel", "CurveBase", "CurveModel", "RecipeBase", "CurveRecipeBase"}
)
NOT_JSON_MODELS = frozenset({"FormFnRegistry", "ShowLookup"})
SEEDED_SEED = 11
SEEDED_SPOTS = 8
SEEDED_SETS = 6
TEXT_SPOTS = 40


def _collect(value: object, found: dict[type[FrozenModel], list[FrozenModel]]) -> None:
    """Adds every model instance reachable from `value`, grouped by class."""
    if isinstance(value, FrozenModel):
        found.setdefault(type(value), []).append(value)
        for name in type(value)._field_names():
            _collect(vars(value).get(name), found)
    elif isinstance(value, Mapping):
        for item in value.values():
            _collect(item, found)
    elif isinstance(value, list | tuple):
        for item in value:
            _collect(item, found)


def harvested_examples() -> dict[type[FrozenModel], list[FrozenModel]]:
    """Collects every model instance the example and seeded shows hold.

    Returns:
        examples: Model class mapped to its instances, in the order the shows hold them.
    """
    found: dict[type[FrozenModel], list[FrozenModel]] = {}
    seeded = seeded_show(SEEDED_SEED, SEEDED_SPOTS, SEEDED_SETS)
    for show in (two_set_show(), crossing_show(), seeded):
        _collect(show, found)
    return found


def _engine_examples() -> list[FrozenModel]:
    show = crossing_show()
    tid = make_tid("1", "2")
    engine = PathEngine(show)
    compiled = engine.compile(tid)
    return [
        compiled,
        engine.sample(2.0),
        engine.segments(tid)[0],
        min_gaps(show, tid)[0],
        speed(show, "T1", tid),
        motion_contrast(show, tid),
    ]


def _fix_examples() -> list[FrozenModel]:
    bend = BendFix(entity=SpotId("T1"), point=(1.0, 2.0), at=0.5)
    return [
        bend,
        DelayFix(entity=SpotId("T1"), counts=2.0),
        ReassignFix(),
        StaggerFix(order=(SpotId("T1"), SpotId("T2")), step=0.5),
        Fix(kind="waypoint", params=bend, new_gap=1.5, max_speed_after=2.0),
    ]


def _curve_examples() -> list[FrozenModel]:
    circle = CircleCurve(center=(0.0, 0.0), radius=2.0)
    segment = Segment(a=(0.0, 0.0), b=(4.0, 0.0))
    return [
        BezierCurve(control=((0.0, 0.0), (1.0, 2.0), (3.0, 2.0), (4.0, 0.0))),
        CompositeCurve(parts=(segment, Segment(a=(4.0, 0.0), b=(4.0, 3.0)))),
        EllipseCurve(center=(0.0, 0.0), rx=3.0, ry=2.0),
        SpiralCurve(center=(0.0, 0.0), r0=1.0, r1=3.0, turns=2.0),
        SplineCurve(points=((0.0, 0.0), (1.0, 1.0), (2.0, 0.0), (3.0, 1.0))),
        SvgCurve(d="M 0 0 L 2 0 L 2 2"),
        TrimmedCurve(base=circle, s0=0.5, s1=3.0),
    ]


def _recipe_examples() -> list[FrozenModel]:
    square = Region(exterior=((-2.0, -2.0), (2.0, -2.0), (2.0, 2.0), (-2.0, 2.0)))
    polyline = Polyline(points=((0.0, 0.0), (2.0, 0.0), (2.0, 2.0)))
    return [
        CurveRecipe(curve=CircleCurve(center=(0.0, 0.0), radius=2.0)),
        CustomRecipe(points=((0.0, 0.0), (1.0, 1.0))),
        FillRecipe(outline=square, interval=1.0),
        GeneratedRecipe(fn="spiral", params={"turns": 2.0}),
        PolylineRecipe(curve=polyline),
        ScatterRecipe(region=square, min_gap=0.5, seed=3),
        SvgRecipe(curves=(polyline,)),
    ]


def _params_examples() -> list[FrozenModel]:
    examples: list[FrozenModel] = [
        FigureEightParams(),
        SpiralParams(),
        ZigzagParams(),
        RoseFormParams(),
        SineWaveFormParams(),
        SpiralFormParams(),
        EntityFacing(entity=SpotId("T2")),
        FixedFacing(degrees=90.0),
        PointFacing(point=(1.0, 2.0)),
        FacingOverride(
            entities="T1",
            mode=FacingMode.FIXED,
            start=0.0,
            end=4.0,
            value=FixedFacing(degrees=45.0),
        ),
        MatchOptions(),
        MatchWeights(),
        match([(0.0, 0.0), (10.0, 0.0)], [(10.0, 1.0), (0.0, 1.0)]),
        match([(0.0, 0.0)], [line_form("f", [], (0.0, 0.0), (2.0, 0.0), n=2)]),
        PlacementPatch(x=1.0, facing=90.0),
    ]
    return examples


def _text_examples() -> list[FrozenModel]:
    font = BUNDLED_FONTS["simplex"]
    form = text_form(
        "t",
        [f"S{i}" for i in range(TEXT_SPOTS)],
        "HI",
        fonts=[font],
        height=2.0,
        at=(0.0, 0.0),
        n=TEXT_SPOTS,
    )
    registry = bundled_fonts()
    loaded = registry.get(font)
    return [
        FormOp(form=form),
        form.recipe,
        text_report(form),
        CrowdedStroke(
            char="!", line=0, column=0, stroke=0, first_slot=0, spots=2, gap=0.1, wanted=0.2
        ),
        layout_text("HI", [font], 2.0, (0.0, 0.0))[0],
        loaded,
        next(iter(loaded.glyphs.values())),
        registry,
    ]


def _timeline_examples() -> list[FrozenModel]:
    timeline = set_measures(two_set_show().timeline, MeasureSetting(counts_per_measure=4.0))
    label = count_to_measure(timeline, 6.0)
    assert label is not None
    return [
        AudioTrack(file="a.wav", label="music", sync_offset_s=1.0),
        Cue(label="go", order=1, estimated_seconds=3.0),
        Hold(at_count=4.0, seconds=2.0),
        Marker(label="hit", at_count=4.0),
        MeasureSetting(counts_per_measure=4.0),
        label,
    ]


def _collab_examples() -> list[FrozenModel]:
    show = two_set_show()
    anchor = Anchor.on_set("1")
    pin = Pin(target="set", id="1")
    placement = PlacementOp(set_id=SetId("2"), placement=show.sets[1].placements[0])
    proposal = propose(show, [placement], "nudge", "p1")
    examples: list[FrozenModel] = [
        anchor,
        pin,
        placement,
        CountsOp(set_id=SetId("2"), counts=6.0),
        PathsOp(spec=show.transition(make_tid("1", "2"))),
        Comment(id="c1", anchor=anchor, text="look here"),
        Highlight(anchor=anchor, ttl_s=5.0),
        CanvasEvent(id="e1", at=AT, kind="pin", payload=pin),
        proposal,
        preview_proposal(show, proposal),
        Revision(id="r2", parent="r1", at=AT),
        Note(id=NoteId("n1"), text="tighten", anchor=anchor, author="ada", created_at=AT),
        NotesDocument(show=show.id),
    ]
    return examples


def _report_examples() -> list[FrozenModel]:
    show = two_set_show()
    issue = ValidationIssue(
        level="warning", code=ErrorCode.SCHEMA, where="sets.1", message="odd", hint="check it"
    )
    _renamed, rewrite = rename_spots(show, {"T1": "X1"})
    return [
        issue,
        ValidationReport(issues=(issue,)),
        rewrite,
        MigrationReport(from_version=1),
        DroppedFields(document="/show/show.json", location="", fields={"zones": []}),
        diff_sets(show.sets[0], show.sets[1]),
        diff_paths(
            show.transition(make_tid("1", "2")), crossing_show().transition(make_tid("1", "2"))
        ),
        index(show),
        index(show).entries[0],
        spot_counts(show)[0],
        Selection(ids=(SpotId("T1"), SpotId("T2"))),
        snap(show.sets[1], 1.0)[1],
        form_metrics(LINE_POINTS),
        form_fns()[0],
        motions(DEFAULT_MOTIONS)[0],
        DEFAULT_UNITS,
        DEFAULT_FORM_FNS,
        show.lookup,
        Box2(min=(0.0, 0.0), max=(1.0, 2.0)),
        Box3(min=(0.0, 0.0, 0.0), max=(1.0, 2.0, 3.0)),
    ]


BUILDERS: tuple[Callable[[], list[FrozenModel]], ...] = (
    _engine_examples,
    _fix_examples,
    _curve_examples,
    _recipe_examples,
    _params_examples,
    _text_examples,
    _timeline_examples,
    _collab_examples,
    _report_examples,
)


def built_examples() -> dict[type[FrozenModel], list[FrozenModel]]:
    """Builds instances of the model classes the example shows do not hold.

    Returns:
        examples: Model class mapped to the instances built through the public API.
    """
    found: dict[type[FrozenModel], list[FrozenModel]] = {}
    for build in BUILDERS:
        for model in build():
            _collect(model, found)
    return found


def _public_model(name: str) -> type[FrozenModel]:
    model = getattr(e8s, name)
    assert isinstance(model, type), name
    assert issubclass(model, FrozenModel), name
    return model


@cache
def model_examples() -> dict[type[FrozenModel], tuple[FrozenModel, ...]]:
    """Maps each model class to its examples: harvested, built, then the variants.

    The variants move optional fields off their defaults, so a field that to_json drops or
    from_json ignores changes the round trip.

    Returns:
        examples: Model class mapped to its instances; built once per test session.
    """
    found = harvested_examples()
    for model, built in built_examples().items():
        found.setdefault(model, []).extend(built)
    first = {model: instances[0] for model, instances in found.items()}
    for variant in variants(first, _public_model):
        _collect(variant, found)
    return {model: tuple(instances) for model, instances in found.items()}
