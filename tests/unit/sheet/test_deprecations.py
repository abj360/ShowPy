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
"""

import ast
import importlib
import warnings
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import showpy
from showpy._api_table import LEAVING, PUBLIC_API
from showpy.core.deprecation import DeprecatedWarning
from showpy.core.ids import LandmarkId, ZoneId
from showpy.examples import two_set_show
from showpy.export.data import export_json, import_json
from showpy.sheet.models import CoordinateSystem, GuideLine, Landmark, Zone, ZoneKind
from showpy.sheet.render import SheetCanvas
from showpy.sheet.surfaces import NFHS_HASH, _proscenium_stage
from showpy.sheet.templates import InMemorySheetStore, _blank_sheet
from showpy.show.models import Show

SHEET_SOURCE = Path(showpy.__file__).parent / "sheet"
SHEET = _blank_sheet(10.0, 6.0)
STAGE = _proscenium_stage()
SHOW = two_set_show()
POINT = (1.0, 1.0)
PROP = Landmark(id=LandmarkId("prop"), name="prop table", kind="point", geometry=((1.0, 1.0),))
PIT = Zone(id=ZoneId("pit"), kind=ZoneKind.PIT, polygon=STAGE.zones[0].polygon)
TAPE = GuideLine(id="tape", kind="custom", points=((0.0, 1.0), (1.0, 1.0)))
RENDER_NAMES = {"SheetCanvas", "draw_sheet", "render_sheet"}
FIELD_NAMES = {"Show.coordinates", "Show.zones"}


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


def _leaving_text(qualified: str, since: str, remove: str, replacement: str | None) -> str:
    text = f"`{qualified}` is deprecated since {since} and leaves the library in {remove}"
    if replacement is None:
        return f"{text}."
    return f"{text}; its replacement `showpy.{replacement}` arrives in {remove}."


@pytest.mark.parametrize("row", FUNCTION_ROWS, ids=[row[0] for row in FUNCTION_ROWS])
def test_leaving_sheet_names_warn(row: tuple[str, str, str | None, str, str]) -> None:
    name, _kind, replacement, since, remove = row
    module = importlib.import_module(PUBLIC_MODULES[name])
    public, implementation = getattr(module, name), getattr(module, f"_{name}")
    with pytest.warns(DeprecatedWarning) as caught:
        result = CALLS[name](public)
    assert len(caught) == 1
    assert str(caught[0].message) == _leaving_text(
        f"{module.__name__}.{name}", since, remove, replacement
    )
    assert caught[0].filename == __file__
    assert result == CALLS[name](implementation)


@pytest.mark.parametrize("row", TYPE_ROWS, ids=[row[0] for row in TYPE_ROWS])
def test_leaving_sheet_types_warn_on_top_level_access(
    row: tuple[str, str, str | None, str, str],
) -> None:
    name, _kind, replacement, since, remove = row
    module = importlib.import_module(PUBLIC_MODULES[name])
    for _ in range(2):
        with pytest.warns(DeprecatedWarning) as caught:
            resolved = getattr(showpy, name)
        assert str(caught[0].message) == _leaving_text(f"showpy.{name}", since, remove, replacement)
        assert caught[0].filename == __file__
        assert resolved is getattr(module, name)
    assert name not in vars(showpy)


def test_leaving_show_sheet_fields_warn() -> None:
    document = export_json(SHOW)
    version_one = {"schema": "showpy.show.v1", "show": SHOW.model_dump(mode="json")}
    with pytest.warns(DeprecatedWarning) as coordinates:
        assert SHOW.coordinates == SHOW.coordinates
    assert [str(w.message) for w in coordinates] == [
        _leaving_text("showpy.Show.coordinates", "0.4.0", "0.7.0", None)
    ] * 2
    with pytest.warns(DeprecatedWarning) as zones:
        assert SHOW.zones == SHOW.zones
    assert [str(w.message) for w in zones] == [
        _leaving_text("showpy.Show.zones", "0.4.0", "0.7.0", None)
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
    assert importlib.import_module("showpy.sheet.coords")._to_coords(SHOW, POINT)
    assert importlib.import_module("showpy.sheet.bounds")._zones_at(SHOW, POINT) is not None


def _decorated_rows() -> dict[str, tuple[str | None, str, str]]:
    found: dict[str, tuple[str | None, str, str]] = {}
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


REPLACEMENTS = {
    "to_coords": "locate",
    "from_coords": "at_location",
    "CoordinateSystem": "Location",
    "CoordinateKind": "Location",
    "nearest_landmarks": "markings_near",
    "proscenium_stage": "floor",
    "sheet_templates": "sheets",
    "get_sheet": "sheet",
    "GuideLine": "Marking",
    "on_surface": "on_sheet",
}


def test_leaving_rows_name_only_the_replacements_the_adr_names() -> None:
    named = {name: replacement for name, _k, replacement, _s, _r in LEAVING if replacement}
    assert named == REPLACEMENTS
