#!/usr/bin/env python3

"""
test_leaving_names.py --- tests that the export names and Spot.claimable warn before they leave

Contains:
    test_leaving_export_names_warn(): warns with the shared wording on `showpy.<name>` access
    test_leaving_names_warn_from_their_export_module(): warns from showpy.export.<module> too
    test_from_import_of_a_leaving_name_warns_at_the_import_line(): warns on `from ... import`
    test_an_unknown_name_in_an_export_module_is_an_attribute_error(): refuses unknown names
    test_library_modules_import_without_warning(): imports the private modules silently
    test_leaving_export_rows_cover_the_decided_names(): checks the 41 names, kinds, removal
        releases and replacements of each group
    test_spot_claimable_warns(): warns on an outside read, not on decoding a stored show
    test_leave_field_reads_silently_from_inside_the_library(): keeps library reads quiet
"""

import importlib
import subprocess
import sys
import warnings
from collections import Counter

import pytest
from pydantic import BaseModel

import showpy
from showpy._api_table import LEAVING, PUBLIC_API
from showpy.cast.models import Spot
from showpy.core.deprecation import DeprecatedWarning, leave_field
from showpy.core.ids import SpotId
from showpy.show.interchange import export_json, import_json
from tests.fixtures.shows import two_set_show

PUBLIC_MODULES = {name: module for name, module, _attribute in PUBLIC_API}
EXPORT_ROWS = [row for row in LEAVING if ".export." in PUBLIC_MODULES.get(row[0], "")]
LEAVING_COUNT = 41
PUBLISHING_NAMES = {
    "Claim", "Performer", "PerformerView", "Publication", "claim_spot", "claims", "join_link",
    "notify_changes", "pause", "performer_view", "publish", "qr_code", "reassign", "release",
    "swap_claims", "unclaimed", "unpublish", "verify_join_token", "who_is_on",
}  # fmt: skip
VIDEO_NAMES = {"export_video", "render_frame", "video_canvas"}
TABLE_NAMES = {
    "FormatRegistry", "TextTable", "as_table", "export_frames_csv", "formats",
    "register_exporter", "register_importer", "to_csv", "to_text",
}  # fmt: skip
PRINT_NAMES = {
    "Chart", "ChartRow", "CoordinateSheet", "SheetRow", "chart", "coordinate_sheet",
    "coordinate_sheets", "cue_sheet", "escape_text", "to_pdf",
}  # fmt: skip
PRINT_REPLACEMENTS = ("coordinate_table", "describe_transition")
PLAIN_ENDING = "."
PRINT_ENDING = (
    "; its replacements `showpy.coordinate_table` and `showpy.describe_transition` arrive in 0.7.0."
)
TABLE_ENDING = "; its replacement `showpy.frames` arrives in 0.7.0."
ENDINGS = {
    **dict.fromkeys(PUBLISHING_NAMES | VIDEO_NAMES, PLAIN_ENDING),
    **dict.fromkeys(PRINT_NAMES, PRINT_ENDING),
    **dict.fromkeys(TABLE_NAMES, TABLE_ENDING),
}


@pytest.mark.parametrize("row", EXPORT_ROWS, ids=[row[0] for row in EXPORT_ROWS])
def test_leaving_export_names_warn(row: tuple[str, str, object, str, str]) -> None:
    name, _kind, _replacement, since, remove = row
    module = importlib.import_module(PUBLIC_MODULES[name])
    for _ in range(2):
        with pytest.warns(DeprecatedWarning) as caught:
            resolved = getattr(showpy, name)
        expected = f"`showpy.{name}` is deprecated since {since} and leaves the library in {remove}"
        assert str(caught[0].message) == expected + ENDINGS[name]
        assert caught[0].filename == __file__
        assert resolved is getattr(module, name)
    assert name not in vars(showpy)
    assert name in showpy.__all__


@pytest.mark.parametrize("row", EXPORT_ROWS, ids=[row[0] for row in EXPORT_ROWS])
def test_leaving_names_warn_from_their_export_module(
    row: tuple[str, str, object, str, str],
) -> None:
    name, _kind, _replacement, since, remove = row
    private = PUBLIC_MODULES[name]
    public = private.replace("._", ".")
    with pytest.warns(DeprecatedWarning) as caught:
        resolved = getattr(importlib.import_module(public), name)
    expected = f"`{public}.{name}` is deprecated since {since} and leaves the library in {remove}"
    assert str(caught[0].message) == expected + ENDINGS[name]
    assert caught[0].filename == __file__
    assert resolved is getattr(importlib.import_module(private), name)


def test_from_import_of_a_leaving_name_warns_at_the_import_line() -> None:
    with pytest.warns(DeprecatedWarning, match="`showpy.export.publish.claim_spot` is") as caught:
        from showpy.export.publish import claim_spot
    assert caught[0].filename == __file__
    assert claim_spot.__module__ == "showpy.export._publish"


def test_an_unknown_name_in_an_export_module_is_an_attribute_error() -> None:
    module = importlib.import_module("showpy.export.publish")
    assert not hasattr(module, "nothing")


def test_library_modules_import_without_warning() -> None:
    private = ("_publish", "_video", "_data", "_pdf", "_print_sheets", "_models")
    imports = ", ".join(f"showpy.export.{module}" for module in private)
    code = f"import {imports}, showpy.examples.publishing"
    subprocess.run([sys.executable, "-W", "error", "-c", code], check=True)


def test_leaving_export_rows_cover_the_decided_names() -> None:
    removal = {name: remove for name, _kind, _replacement, _since, remove in EXPORT_ROWS}
    replacement = {name: rep for name, _kind, rep, _since, _remove in EXPORT_ROWS}
    assert len(EXPORT_ROWS) == len(removal) == LEAVING_COUNT
    assert Counter(kind for _name, kind, *_rest in EXPORT_ROWS) == {"type": 10, "export": 31}
    assert {n for n, r in removal.items() if r == "0.6.0"} == PUBLISHING_NAMES | VIDEO_NAMES
    assert {n for n, r in removal.items() if r == "0.7.0"} == PRINT_NAMES | TABLE_NAMES
    assert {n for n, rep in replacement.items() if rep is None} == PUBLISHING_NAMES | VIDEO_NAMES
    assert {n for n, rep in replacement.items() if rep == PRINT_REPLACEMENTS} == PRINT_NAMES
    assert {n for n, rep in replacement.items() if rep == "frames"} == TABLE_NAMES
    assert {since for _n, _k, _r, since, _rm in EXPORT_ROWS} == {"0.4.0"}


def test_spot_claimable_warns() -> None:
    spot = Spot(id=SpotId("T1"), group="trumpets")
    message = "`showpy.Spot.claimable` is deprecated since 0.4.0 and leaves the library in 0.7.0."
    with pytest.warns(DeprecatedWarning) as caught:
        assert spot.claimable == "performer"
    assert str(caught[0].message) == message
    assert caught[0].filename == __file__
    show = two_set_show()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        decoded = import_json(export_json(show))
        assert decoded == show
        assert decoded.cast.spots[0].model_dump()["claimable"] == "performer"
        assert spot.model_copy(update={"claimable": "crew"}) != spot
        assert "claimable=" in repr(spot)
    assert ("Spot.claimable", "field", None, "0.4.0", "0.7.0") in LEAVING


def test_leave_field_reads_silently_from_inside_the_library() -> None:
    class Toy(BaseModel):
        going: str = "x"

    leave_field(Toy, "going", since="0.4.0", remove="9.0.0", replacement="newer")
    with pytest.warns(DeprecatedWarning, match="`showpy.Toy.going` .* `showpy.newer` arrive"):
        assert Toy().going == "x"
    inside = {"__name__": "showpy.inside", "Toy": Toy}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert eval("Toy().going", inside) == "x"
