#!/usr/bin/env python3

"""
model_variants.py --- variants of the example models whose optional fields leave their defaults

Contains:
    AFFINE: a shear-and-shift transform used wherever a recipe takes one
    Example: stands for a model's first example inside a change set
    variant_changes(): lists, per model name, field changes that move optional fields off default
    variants(): builds each variant by replacing fields on a model's first example
"""

from collections.abc import Callable, Mapping

from e8s.cast.models import LabelScheme, Shape
from e8s.choreo.form_models import CrowdedStroke, SpacingSpec
from e8s.choreo.match_models import MatchWeights
from e8s.choreo.path_models import (
    Easing,
    FacingMode,
    FixedFacing,
    PointFacing,
    TurnDirection,
)
from e8s.core.ids import FormId, RouteId, SetId, SpotId, make_tid
from e8s.core.types import FrozenModel, Region
from e8s.geo.curves import Segment
from e8s.show.models import Pin
from e8s.time.models import AudioTrack, Cue, Hold, Marker, MarkerKind, MeasureLabel, MeasureSetting

AFFINE = ((1.0, 0.2, 2.0), (0.0, 1.0, 1.0), (0.0, 0.0, 1.0))
SQUARE = Region(exterior=((-2.0, -2.0), (2.0, -2.0), (2.0, 2.0), (-2.0, 2.0)))
HOLED = Region(
    exterior=((-4.0, -4.0), (4.0, -4.0), (4.0, 4.0), (-4.0, 4.0)),
    holes=(((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)),),
)
RECIPES = (
    "ArcRecipe",
    "BlockRecipe",
    "CircleRecipe",
    "CurveRecipe",
    "CustomRecipe",
    "FillRecipe",
    "GeneratedRecipe",
    "LineRecipe",
    "PolylineRecipe",
    "ScatterRecipe",
    "SvgRecipe",
    "WedgeRecipe",
)
type Changes = Mapping[str, object]


class Example:
    """Stands for the first example of a public model, resolved when the variant is built.

    Attributes:
        name: Public name of the model whose first example fills the field.
    """

    def __init__(self, name: str) -> None:
        self.name = name


def _shapes_and_people() -> dict[str, tuple[Changes, ...]]:
    return {
        "Shape": (
            {"kind": "rectangle", "width_m": 1.2, "depth_m": 0.8, "rotation_deg": 30.0},
            {"radius_m": 0.5},
        ),
        "Spot": (
            {
                "symbol": "star",
                "color": "#112233",
                "tags": ("lead",),
                "active": False,
                "shape": Shape(radius_m=0.5),
            },
        ),
        "Group": (
            {
                "symbol": "square",
                "color": "#445566",
                "default_radius": 0.5,
                "limits": {"speed": 2.0},
                "moves": False,
            },
        ),
        "LabelScheme": ({"prefixes": {"trumpets": "X"}, "start": 0, "pad": 2},),
        "Cast": (
            {
                "selections": {"pair": (SpotId("T1"), SpotId("T2"))},
                "label_scheme": LabelScheme(start=0, pad=2),
            },
        ),
        "Selection": ({"description": "front row"},),
    }


def _choreo() -> dict[str, tuple[Changes, ...]]:
    return {
        **{name: ({"transform": AFFINE, "n": 4},) for name in RECIPES},
        "Placement": ({"pinned": True, "note": "mark", "on_field": False},),
        "PlacementPatch": (
            {
                "y": 2.0,
                "form": FormId("f1"),
                "order": 3,
                "pinned": True,
                "note": "nudge",
                "on_field": False,
            },
        ),
        "Set": ({"label": "Opener", "cue": "lights", "source_hash": "abc", "state": "approved"},),
        "SpacingSpec": (
            {"mode": "interval", "interval": 1.5, "start": 0.5, "end": 3.0, "align": "center"},
            {"mode": "count", "gaps": ((1, 0.5),), "align": "end"},
        ),
        "Form": (
            {"locked": True, "meta": {"by": "ada"}},
            {"spacing": SpacingSpec(mode="interval", interval=1.0)},
        ),
        "Easing": (
            {"kind": "ease_in", "strength": 3.0},
            {"kind": "custom", "points": ((0.0, 0.0), (0.5, 0.8), (1.0, 1.0))},
        ),
        "PathRow": (
            {
                "end": 6.0,
                "facing_mode": FacingMode.FIXED,
                "facing_value": FixedFacing(degrees=90.0),
                "turn_counts": 2.0,
                "turn_direction": TurnDirection.CLOCKWISE,
                "pace": 1.5,
                "locked": True,
                "note": "watch",
            },
        ),
        "MotionPlan": (
            {
                "easing": Easing(kind="ease_out"),
                "facing_mode": FacingMode.TOWARD_POINT,
                "facing_value": PointFacing(point=(1.0, 2.0)),
                "turn_counts": 1.0,
                "turn_direction": TurnDirection.COUNTERCLOCKWISE,
            },
        ),
        "FacingOverride": ({"turn_counts": 2.0},),
        "Waypoint": ({"at": 0.5},),
        "Route": ({"start": 1.0, "end": 6.0},),
        "TransitionSpec": ({"notes": "slow", "source_hash": "def"},),
        "MatchOptions": (
            {
                "keep_groups": False,
                "group_slots": {"trumpets": (0, 1)},
                "pinned": {SpotId("T1"): 0},
                "forbid": ((SpotId("T2"), 1),),
                "no_backward": 0.5,
                "max_speed": 3.0,
                "objective": "sum_squares",
                "power": 2.0,
                "preserve_order": True,
                "weights": MatchWeights(backward=1.0),
                "counts": 8.0,
            },
        ),
        "MatchWeights": ({"backward": 1.0, "crossing": 2.0, "group_spread": 0.5, "turn": 0.25},),
        "MatchResult": (
            {
                "max_speed": 1.5,
                "crossings": 2,
                "unassigned_slots": (3,),
            },
        ),
        "CustomRecipe": ({"facings": (90.0, 0.0), "n": 2, "transform": AFFINE},),
        "FillRecipe": ({"pattern": "hex", "angle_deg": 15.0, "transform": AFFINE, "n": 4},),
        "BlockRecipe": ({"anchor": "front-left", "transform": AFFINE, "n": 4},),
        "TextRecipe": (
            {
                "transform": AFFINE,
                "rotation": 10.0,
                "align": "center",
                "anchor": "middle",
                "letter_spacing": 0.1,
                "line_spacing": 1.5,
            },
            {"spacing": 0.5},
        ),
        "TextReport": (
            {
                "crowded": (
                    CrowdedStroke(
                        char="!",
                        line=0,
                        column=0,
                        stroke=0,
                        first_slot=0,
                        spots=2,
                        gap=0.1,
                        wanted=0.2,
                    ),
                )
            },
        ),
        "CompiledTransition": ({"warnings": ("tight turn",)},),
        "SpiralParams": ({"turns": 2, "clockwise": True},),
        "WeaveParams": ({"cycles": 3.0},),
        "ZigzagParams": ({"amplitude": 2.0, "cycles": 3.0},),
        "FigureEightParams": ({"amplitude": 2.0},),
        "RoseFormParams": ({"center": (1.0, 9.0), "radius": 4.0, "petals": 7.0, "inner": 0.3},),
        "SineWaveFormParams": (
            {
                "start": (-6.0, 9.0),
                "length": 12.0,
                "amplitude": 1.5,
                "wavelength": 6.0,
                "phase_deg": 45.0,
            },
        ),
        "SpiralFormParams": (
            {"center": (1.0, 9.0), "r0": 0.5, "r1": 4.0, "turns": 3.0, "start_deg": 30.0},
        ),
    }


def _curves() -> dict[str, tuple[Changes, ...]]:
    return {
        "CircleCurve": ({"ccw": False},),
        "CompositeCurve": (
            {
                "parts": (
                    Segment(a=(0.0, 0.0), b=(2.0, 0.0)),
                    Segment(a=(2.0, 0.0), b=(2.0, 2.0)),
                    Segment(a=(2.0, 2.0), b=(0.0, 0.0)),
                ),
                "closed": True,
            },
        ),
        "EllipseCurve": ({"rotation_deg": 30.0},),
        "SpiralCurve": ({"start_deg": 45.0},),
        "SplineCurve": ({"closed": True, "alpha": 0.25},),
        "Polyline": ({"closed": True},),
        "SvgCurve": ({"d": "M 0 0 L 2 0 L 2 2 Z", "closed": True, "transform": AFFINE},),
        "Region": ({"holes": HOLED.holes, "exterior": HOLED.exterior},),
    }


def _time() -> dict[str, tuple[Changes, ...]]:
    return {
        "AudioTrack": ({"gain": 0.5, "section": (0.0, 4.0)},),
        "Hold": ({"note": "breathe"},),
        "Marker": ({"kind": MarkerKind.REHEARSAL},),
        "MeasureSetting": ({"start_measure": 2, "changes": ((8.0, 3.0),)},),
        "Timeline": (
            {
                "holds": (Hold(at_count=4.0, seconds=2.0),),
                "markers": (Marker(label="A", at_count=4.0),),
                "measures": MeasureSetting(counts_per_measure=4.0),
                "cues": (Cue(label="go", order=1, estimated_seconds=3.0),),
                "audio": (AudioTrack(file="a.wav", label="music"),),
                "total_counts": 16.0,
            },
        ),
    }


def _show() -> dict[str, tuple[Changes, ...]]:
    label = MeasureLabel(measure=2, count_in_measure=1.0)
    return {
        "Anchor": (
            {"kind": "transition", "set_id": None, "tid": make_tid("1", "2")},
            {"kind": "point", "set_id": None, "point": (1.0, 2.0)},
            {"kind": "area", "set_id": None, "area": SQUARE},
            {"kind": "spots", "set_id": None, "spots": (SpotId("T1"),)},
        ),
        "Pin": ({"by": "ada", "reason": "approved"},),
        "Comment": ({"resolved": True, "author": "ada"},),
        "Highlight": ({"anchor": None, "zone": "pit"},),
        "CanvasEvent": ({"revision": "r1"},),
        "CountsOp": ({"rescale": True},),
        "Proposal": (
            {"state": "rejected", "reason": "too far"},
            {"state": "accepted", "accepted_revision": "r2"},
        ),
        "ItemKey": ({"role": "drill"},),
        "ItemChange": ({"before": {"x": 1.0, "tags": [1, None]}, "after": [True, "moved"]},),
        "ChangeSet": ({"changes": (Example("ItemChange"),)},),
        "MergeConflict": ({"base": {"n": 1}, "ours": [1, 2], "theirs": "moved"},),
        "MergeResult": ({"show": None, "conflicts": (Example("MergeConflict"),)},),
        "IdChanges": ({"sets": (("1", "a"), ("2", "b")), "spots": (("T1", "T9"),)},),
        "FsckReport": ({"findings": (Example("FsckFinding"),), "ok": False},),
        "GcReport": ({"removed": ("a", "b"), "bytes_freed": 12, "dry_run": True},),
        "UpgradeReport": ({"shows": ("s",), "revisions": 3, "blobs_linked": 2, "blobs_copied": 1},),
        "ConformanceReport": ({"passed": ("a",), "failed": (("b", "no lock"),)},),
        "Revision": ({"changed": ("/show/show.json",)},),
        "Show": (
            {
                "pack": "marching",
                "pins": (Pin(target="set", id="1"),),
                "facing_overrides": (Example("FacingOverride"),),
                "extensions": {"x-host": {"n": 1}},
                "revision": "r1",
            },
        ),
        "ShowIndexEntry": ({"measure": label, "cue": "go"},),
        "ValidationIssue": ({"context": {"set": "2"}},),
        "PathDiff": ({"changed_rows": (SpotId("T1"),), "routes_changed": (RouteId("r"),)},),
        "SetDiff": (
            {
                "facing_changed": (SpotId("T1"),),
                "added": (SpotId("T5"),),
                "removed": (SpotId("T4"),),
                "forms_changed": (FormId("f1"),),
            },
        ),
        "MigrationReport": (
            {
                "renamed": {"set": (("a", "b"),)},
                "repairs": ("sorted tempo",),
                "dropped": (Example("DroppedFields"),),
            },
        ),
        "DroppedFields": ({"location": "cast.spots[0]", "stored_id": "T1"},),
        "RewriteReport": (
            {
                "dropped_routes": ((make_tid("1", "2"), RouteId("r")),),
                "dropped_facings": (Example("FacingOverride"),),
                "unresolved_selectors": ("sel:gone",),
            },
        ),
        "Grid": ({"origin": (1.0, 1.0), "major_every": 4, "rotation_deg": 15.0},),
        "Sheet": ({"based_on": "sheet:gym@1", "owner": "ada", "thumbnail": "thumb.png"},),
        "ProposalPreview": (
            {
                "counts": {SetId("2"): 6.0},
                "placements": {SetId("2"): (Example("Placement"),)},
                "paths": (Example("TransitionSpec"),),
                "forms": (Example("Form"),),
                "blocked": ("set 1 is pinned",),
            },
        ),
    }


def variant_changes() -> dict[str, tuple[Changes, ...]]:
    """Lists, per public model name, field changes that move its optional fields off default.

    Returns:
        changes: Model name mapped to the change sets; each builds one variant.
    """
    return _shapes_and_people() | _choreo() | _curves() | _time() | _show()


def variants(
    first: Mapping[type[FrozenModel], FrozenModel],
    named: Callable[[str], type[FrozenModel]],
) -> list[FrozenModel]:
    """Builds each variant by replacing fields on the first example of its model.

    Args:
        first: Model class mapped to its first example.
        named: Resolves a public model name to its class.

    Returns:
        built: One model per change set, each revalidated by `replace`.
    """

    def resolved(value: object) -> object:
        if isinstance(value, Example):
            return first[named(value.name)]
        if isinstance(value, tuple):
            return tuple(resolved(item) for item in value)
        if isinstance(value, Mapping):
            return {key: resolved(item) for key, item in value.items()}
        return value

    return [
        first[named(name)].replace(**{field: resolved(v) for field, v in changes.items()})
        for name, change_sets in variant_changes().items()
        for changes in change_sets
    ]
