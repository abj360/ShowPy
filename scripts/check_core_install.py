#!/usr/bin/env python3

"""
check_core_install.py --- checks that a showpy install without extras imports and fails closed

Run it with the interpreter of a venv that holds the showpy wheel and no extra: every module
must import without loading an optional dependency, and every extra-gated call must raise
MissingExtraError whose hint names its extra.

Contains:
    OPTIONAL_MODULES: names the top-level modules that only the extras install
    GatedCall: names one extra-gated call, its extra and the optional modules it needs
    GATED_CALLS: lists one call per extra-gated function or output format
    import_every_module(): imports every module under showpy and returns their names
    loaded_optional_modules(): lists the optional modules already imported
    gate_failure(): runs one gated call and describes how it failed to raise MissingExtraError
    main(): runs every check and exits 1 on the first kind of failure
"""

import importlib
import pkgutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import showpy
from showpy.choreo.engine import PathEngine
from showpy.choreo.motion_stats import energy_curve, phrase_profile, speeds
from showpy.choreo.paths import paths_table
from showpy.core.extras import MissingExtraError
from showpy.examples import COUNTS, EXAMPLE_TID, two_set_show
from showpy.export._print_sheets import cue_sheet
from showpy.export._publish import publish, qr_code
from showpy.export._video import export_video, render_frame, video_canvas
from showpy.sheet.render import SheetCanvas, _draw_sheet, _render_sheet
from showpy.sheet.templates import _blank_sheet

if TYPE_CHECKING:
    from PIL import ImageDraw

OPTIONAL_MODULES = ("pandas", "PIL", "segno", "imageio_ffmpeg")
JOIN_TOKEN = "k3Qz9-high-entropy-join-token"
PUBLISHED_AT = datetime(2026, 1, 1, tzinfo=UTC)
SHEET_SIDE_M = 4.0


@dataclass(frozen=True)
class GatedCall:
    """Names one extra-gated call, the extra that unlocks it and the modules it needs.

    Attributes:
        name: The function, with the output format when only that format is gated.
        extra: The showpy extra whose install command the error hint must name.
        modules: Top-level optional modules the call needs; missing any one must raise.
        call: Runs the call on example inputs.
    """

    name: str
    extra: str
    modules: tuple[str, ...]
    call: Callable[[], object]


def _first_set_table() -> object:
    return two_set_show().sets[0].table()


def _draw_blank_sheet() -> None:
    sheet = _blank_sheet(SHEET_SIDE_M, SHEET_SIDE_M)
    # A core install cannot build an ImageDraw; the gate fires before the drawing is touched.
    _draw_sheet(cast("ImageDraw.ImageDraw", None), SheetCanvas.for_sheet(sheet), sheet)


def _render_first_frame() -> object:
    show = two_set_show()
    return render_frame(show, PathEngine(show).sample(0.0), video_canvas(show))


def _qr_code_of_example() -> bytes:
    pub = publish(two_set_show(), "p1", "https://x.example", PUBLISHED_AT, join_token=JOIN_TOKEN)
    return qr_code(pub, JOIN_TOKEN)


GATED_CALLS = (
    GatedCall("Set.table", "tables", ("pandas",), _first_set_table),
    GatedCall(
        "paths_table",
        "tables",
        ("pandas",),
        lambda: paths_table(two_set_show().transition(EXAMPLE_TID)),
    ),
    GatedCall("speeds", "tables", ("pandas",), lambda: speeds(two_set_show(), EXAMPLE_TID)),
    GatedCall("energy_curve", "tables", ("pandas",), lambda: energy_curve(two_set_show())),
    GatedCall(
        "phrase_profile",
        "tables",
        ("pandas",),
        lambda: phrase_profile(two_set_show(), 0.0, COUNTS),
    ),
    GatedCall("cue_sheet", "tables", ("pandas",), lambda: cue_sheet(two_set_show())),
    GatedCall(
        "render_sheet(fmt='png')",
        "render",
        ("PIL",),
        lambda: _render_sheet(_blank_sheet(SHEET_SIDE_M, SHEET_SIDE_M), fmt="png"),
    ),
    GatedCall(
        "render_sheet(fmt='pdf')",
        "render",
        ("PIL",),
        lambda: _render_sheet(_blank_sheet(SHEET_SIDE_M, SHEET_SIDE_M), fmt="pdf"),
    ),
    GatedCall("draw_sheet", "render", ("PIL",), _draw_blank_sheet),
    GatedCall("render_frame", "render", ("PIL",), _render_first_frame),
    GatedCall("qr_code", "qr", ("segno", "PIL"), _qr_code_of_example),
    GatedCall(
        "export_video",
        "video",
        ("imageio_ffmpeg", "PIL"),
        lambda: export_video(two_set_show(), (0.0, 1.0), fps=2, px_per_m=4.0),
    ),
)


def import_every_module() -> list[str]:
    """Imports every module and package under showpy.

    Returns:
        names: Dotted names of the imported modules, in walk order.
    """
    names = [info.name for info in pkgutil.walk_packages(showpy.__path__, "showpy.")]
    for name in names:
        importlib.import_module(name)
    return names


def loaded_optional_modules() -> list[str]:
    """Lists the optional top-level modules that are already imported."""
    return [name for name in OPTIONAL_MODULES if name in sys.modules]


def gate_failure(gated: GatedCall) -> str | None:
    """Runs one gated call and describes how it failed to raise the right MissingExtraError.

    Args:
        gated: The call to run where its extra is missing.

    Returns:
        failure: An `E:` line, or None when the call raised MissingExtraError naming its extra.
    """
    try:
        gated.call()
    except MissingExtraError as err:
        if f"showpy[{gated.extra}]" in err.hint:
            return None
        return f"E: {gated.name} names the wrong extra: {err.hint}"
    return f"E: {gated.name} ran without the {gated.extra} extra"


def main() -> int:
    """Imports every module, checks no optional module loaded, then runs every gated call."""
    names = import_every_module()
    print(f"Imported {len(names)} modules")
    loaded = loaded_optional_modules()
    if loaded:
        print(f"E: importing showpy loaded {', '.join(loaded)}")
        return 1
    failures = [failure for gated in GATED_CALLS if (failure := gate_failure(gated))]
    for failure in failures:
        print(failure)
    if failures:
        return 1
    print(f"Checked {len(GATED_CALLS)} gated calls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
