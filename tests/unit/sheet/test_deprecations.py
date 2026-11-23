#!/usr/bin/env python3

"""
test_deprecations.py --- checks every sheet name that leaves the library warns and says when

Contains:
    test_leaving_sheet_names_warn: warns DeprecatedWarning on each leaving function call and
        each leaving type's top-level access, and returns what the implementation returns
    test_leaving_show_sheet_fields_warn: warns on an outside read of Show.coordinates and
        Show.zones, and stays silent for stored-show decoding and the library's own reads
    test_leaving_functions_are_decorated_as_their_rows_say: matches each wrapper's replacement,
        since and remove to its row
    test_leaving_rows_name_each_removal_release: pins the removal release of every row
    test_leaving_rows_cover_the_decided_names: counts the rows of each group of names
    test_every_leaving_row_has_its_expected_message: pins one message ending to each of the rows
    test_only_the_rendering_names_leave_without_a_replacement: keeps three rows plain
"""

import ast
import importlib
import warnings
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import e8s
from e8s._api_table import LEAVING as ALL_LEAVING
from e8s._api_table import PUBLIC_API
from e8s.core.deprecation import DeprecatedWarning
from e8s.core.ids import LandmarkId, ZoneId
from e8s.examples import two_set_show
from e8s.sheet.models import CoordinateSystem, GuideLine, Landmark, Zone, ZoneKind
from e8s.sheet.render import SheetCanvas
from e8s.sheet.surfaces import NFHS_HASH, _proscenium_stage
from e8s.sheet.templates import InMemorySheetStore, _blank_sheet
from e8s.show.interchange import export_json, import_json
from e8s.show.models import Show

SHEET_SOURCE = Path(e8s.__file__).parent / "sheet"
SHEET = _blank_sheet(10.0, 6.0)
STAGE = _proscenium_stage()
SHOW = two_set_show()
POINT = (1.0, 1.0)
PROP = Landmark(id=LandmarkId("prop"), name="prop table", kind="point", geometry=((1.0, 1.0),))
PIT = Zone(id=ZoneId("pit"), kind=ZoneKind.PIT, polygon=STAGE.zones[0].polygon)
TAPE = GuideLine(id="tape", kind="custom", points=((0.0, 1.0), (1.0, 1.0)))
RENDER_NAMES = {"SheetCanvas", "draw_sheet", "render_sheet"}
FIELD_NAMES = {"Show.coordinates", "Show.zones"}
EXPORT_NAMES = {name for name, module, _attribute in PUBLIC_API if ".export." in module}
NOTES_NAMES = {name for name, module, _attribute in PUBLIC_API if ".notes." in module}
OTHER_LANES = EXPORT_NAMES | NOTES_NAMES | {"import_notes"}
LEAVING = tuple(
    row
    for row in ALL_LEAVING
    if row[0] not in OTHER_LANES and not row[0].startswith(("Spot.", "RewriteReport."))
)
type LeavingRow = tuple[str, str, str | tuple[str, ...] | None, str, str]


def _draw_sheet_call(draw_sheet: Callable[..., object]) -> object:
    draw = ImageDraw.Draw(Image.new("RGB", (100, 100)))
    return draw_sheet(draw, SheetCanvas.for_sheet(SHEET), SHEET)


CALLS: dict[str, Callable[[Callable[..., object]], object]] = {
    "to_coords": lambda f: f(SHOW, POINT),
    "from_coords": lambda f: f(SHOW, "L3.50 U2.00"),
    "coordinate_presets": lambda f: f(SHEET),
    "set_coordinates": lambda f: f(SHOW, CoordinateSystem(units="ft")),
    "landmark": lambda f: f(SHEET, "center"),
    "landmark_anchor": lambda f: f(PROP),
    "landmark_keys": lambda f: f(PROP),
    "nearest_landmarks": lambda f: f(SHEET, POINT),
    "zones_at": lambda f: f(SHOW, POINT),
    "line_landmark": lambda f: f("edge", "edge", (0.0, 0.0), (1.0, 0.0)),
    "point_landmark": lambda f: f("mark", "mark", (0.0, 0.0)),
    "add_landmark": lambda f: f(SHEET, PROP),
    "remove_landmark": lambda f: f(SHEET, "center"),
    "add_zone": lambda f: f(SHEET, PIT),
    "remove_zone": lambda f: f(STAGE, "wing-sr"),
    "proscenium_stage": lambda f: f(),
    "draw_sheet": _draw_sheet_call,
    "render_sheet": lambda f: f(SHEET),
    "sheet_templates": lambda f: f(),
    "get_sheet": lambda f: f("sheet:nfl-football"),
    "customize": lambda f: f(SHEET, name="renamed"),
    "football_field": lambda f: f("test-field", "Test field", NFHS_HASH),
    "gym_floor": lambda f: f(),
    "on_surface": lambda f: f(SHEET, POINT),
    "blank_sheet": lambda f: f(10.0, 6.0),
    "add_guide": lambda f: f(SHEET, TAPE),
    "remove_guide": lambda f: f(SHEET, "center-line"),
    "save_sheet": lambda f: f(InMemorySheetStore(), SHEET),
    "template_ref": lambda f: f("test-field"),
}
FUNCTION_ROWS = [row for row in LEAVING if row[1] == "function"]
TYPE_ROWS = [row for row in LEAVING if row[1] == "type"]
FIELD_ROWS = [row for row in LEAVING if row[1] == "field"]
PUBLIC_MODULES = {name: module for name, module, _attribute in PUBLIC_API}
PLAIN = "."
COORDINATE_WORDING = (
    "; its replacements `e8s.locate`, `e8s.at_location` and `e8s.Location` arrive in 0.7.0."
)
SHEET_MARKINGS = "; its replacements `e8s.Marking` and `e8s.markings_near` arrive in 0.7.0."
SHEET_SHAPE = (
    "; its replacements `e8s.sheets`, `e8s.sheet`, `e8s.field`, `e8s.floor`, "
    "`e8s.Marking` and `e8s.on_sheet` arrive in 0.7.0."
)


def _single(replacement: str) -> str:
    return f"; its replacement `e8s.{replacement}` arrives in 0.7.0."


ENDINGS: dict[str, str] = {
    "to_coords": _single("locate"),
    "from_coords": _single("at_location"),
    "coordinate_presets": COORDINATE_WORDING,
    "set_coordinates": COORDINATE_WORDING,
    "CoordinateSystem": _single("Location"),
    "CoordinateKind": _single("Location"),
    "landmark": SHEET_MARKINGS,
    "landmark_anchor": SHEET_MARKINGS,
    "landmark_keys": SHEET_MARKINGS,
    "nearest_landmarks": _single("markings_near"),
    "zones_at": SHEET_MARKINGS,
    "line_landmark": SHEET_MARKINGS,
    "point_landmark": SHEET_MARKINGS,
    "add_landmark": SHEET_MARKINGS,
    "remove_landmark": SHEET_MARKINGS,
    "add_zone": SHEET_MARKINGS,
    "remove_zone": SHEET_MARKINGS,
    "Landmark": SHEET_MARKINGS,
    "Zone": SHEET_MARKINGS,
    "ZoneKind": SHEET_MARKINGS,
    "proscenium_stage": _single("floor"),
    "SheetCanvas": PLAIN,
    "draw_sheet": PLAIN,
    "render_sheet": PLAIN,
    "sheet_templates": _single("sheets"),
    "get_sheet": _single("sheet"),
    "customize": SHEET_SHAPE,
    "football_field": _single("field"),
    "gym_floor": _single("floor"),
    "GuideLine": _single("Marking"),
    "on_surface": _single("on_sheet"),
    "blank_sheet": SHEET_SHAPE,
    "add_guide": SHEET_SHAPE,
    "remove_guide": SHEET_SHAPE,
    "save_sheet": SHEET_SHAPE,
    "template_ref": SHEET_SHAPE,
    "SheetStore": SHEET_SHAPE,
    "InMemorySheetStore": SHEET_SHAPE,
    "Show.coordinates": _single("Location"),
    "Show.zones": _single("Marking"),
}


def _leaving_text(qualified: str, name: str, remove: str) -> str:
    text = f"`{qualified}` is deprecated since 0.4.0 and leaves the library in {remove}"
    return f"{text}{ENDINGS[name]}"


@pytest.mark.parametrize("row", FUNCTION_ROWS, ids=[row[0] for row in FUNCTION_ROWS])
def test_leaving_sheet_names_warn(row: LeavingRow) -> None:
    name, _kind, _replacement, _since, remove = row
    module = importlib.import_module(PUBLIC_MODULES[name])
    public, implementation = getattr(module, name), getattr(module, f"_{name}")
    with pytest.warns(DeprecatedWarning) as caught:
        result = CALLS[name](public)
    assert len(caught) == 1
    assert str(caught[0].message) == _leaving_text(f"{module.__name__}.{name}", name, remove)
    assert caught[0].filename == __file__
    assert result == CALLS[name](implementation)


@pytest.mark.parametrize("row", TYPE_ROWS, ids=[row[0] for row in TYPE_ROWS])
def test_leaving_sheet_types_warn_on_top_level_access(
    row: LeavingRow,
) -> None:
    name, _kind, _replacement, _since, remove = row
    module = importlib.import_module(PUBLIC_MODULES[name])
    for _ in range(2):
        with pytest.warns(DeprecatedWarning) as caught:
            resolved = getattr(e8s, name)
        assert str(caught[0].message) == _leaving_text(f"e8s.{name}", name, remove)
        assert caught[0].filename == __file__
        assert resolved is getattr(module, name)
    assert name not in vars(e8s)


def test_leaving_show_sheet_fields_warn() -> None:
    document = export_json(SHOW)
    version_one = {"schema": "showpy.show.v1", "show": SHOW.model_dump(mode="json")}
    with pytest.warns(DeprecatedWarning) as coordinates:
        assert SHOW.coordinates == SHOW.coordinates
    assert [str(w.message) for w in coordinates] == [
        _leaving_text("e8s.Show.coordinates", "Show.coordinates", "0.7.0")
    ] * 2
    with pytest.warns(DeprecatedWarning) as zones:
        assert SHOW.zones == SHOW.zones
    assert [str(w.message) for w in zones] == [
        _leaving_text("e8s.Show.zones", "Show.zones", "0.7.0")
    ] * 2
    assert {row[0] for row in FIELD_ROWS} == FIELD_NAMES
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert import_json(document) == SHOW
        assert import_json(version_one) == SHOW
        assert Show.model_validate(SHOW.model_dump(mode="json")) == SHOW
        assert SHOW.model_copy(update={"title": "again"}).title == "again"
    assert caught == []


def test_leaving_show_fields_stay_silent_inside_the_library() -> None:
    assert importlib.import_module("e8s.sheet.coords")._to_coords(SHOW, POINT)
    assert importlib.import_module("e8s.sheet.bounds")._zones_at(SHOW, POINT) is not None


def _decorated_rows() -> dict[str, tuple[str | tuple[str, ...] | None, str, str]]:
    found: dict[str, tuple[str | tuple[str, ...] | None, str, str]] = {}
    for path in sorted(SHEET_SOURCE.glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and getattr(decorator.func, "id", "") == (
                    "leaving_call"
                ):
                    keywords = {kw.arg: ast.literal_eval(kw.value) for kw in decorator.keywords}
                    assert not decorator.args
                    found[node.name] = (
                        keywords.get("replacement"),
                        keywords["since"],
                        keywords["remove"],
                    )
    return found


def test_leaving_functions_are_decorated_as_their_rows_say() -> None:
    decorated = _decorated_rows()
    rows = {
        name: (replacement, since, remove)
        for name, _kind, replacement, since, remove in FUNCTION_ROWS
    }
    assert decorated == rows


def test_leaving_rows_name_each_removal_release() -> None:
    removal = {name: remove for name, _kind, _replacement, _since, remove in LEAVING}
    assert {name for name, remove in removal.items() if remove == "0.6.0"} == RENDER_NAMES
    assert set(removal.values()) == {"0.6.0", "0.7.0"}
    assert {since for *_rest, since, _remove in LEAVING} == {"0.4.0"}


def test_leaving_rows_cover_the_decided_names() -> None:
    groups = Counter(kind for _name, kind, *_rest in LEAVING)
    assert groups == {"function": 29, "type": 9, "field": 2}
    assert len(LEAVING) == 40
    assert len({name for name, *_rest in LEAVING}) == 40


def test_every_leaving_row_has_its_expected_message() -> None:
    assert list(ENDINGS) == [name for name, *_rest in LEAVING]


def test_only_the_rendering_names_leave_without_a_replacement() -> None:
    plain = {name for name, _kind, replacement, _since, _remove in LEAVING if replacement is None}
    assert plain == RENDER_NAMES
    assert [name for name, ending in ENDINGS.items() if ending == PLAIN] == [
        "SheetCanvas",
        "draw_sheet",
        "render_sheet",
    ]
