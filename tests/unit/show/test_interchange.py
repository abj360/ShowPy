#!/usr/bin/env python3

"""
test_interchange.py --- tests JSON interchange, embedded fonts and CSV position import

Also holds the audited import defects (UP-9.5-41 to UP-9.5-44); UP-9.5-43 and -44 are fixed.
Also holds UP-9.5-58, found by the importer fuzzers, and UP-9.5-60, found by the final review.

Contains:
    test_export_json_*(): checks the tagged, header-first, JSON-safe document and its `include`
    test_json_round_trip_restores_the_show(): restores a show from its own export
    test_regression_import_json_*(): checks version, tag, surrogate, mapping and error-count rules
    test_regression_export_json_ignores_hash_seed(): dumps one JSON under every hash seed
    test_import_json_*(): refuses wrong schemas, the old stage tag; loads documents with fonts
    test_export_*font*(): embeds, omits and fails closed on the fonts of text forms
    test_import_fonts_*(): round-trips, rejects tampered and malformed fonts
    test_regression_import_fonts_*(): rejects hostile documents; accepts an older font by URI
    test_regression_import_json_refuses_an_unknown_top_level_key(): names the key and the rule
    test_import_json_keeps_x_keys(): accepts x- keys; export writes them back via `extensions`
    test_export_json_refuses_extensions_that_are_not_x_keys(): fails closed on other names
    test_interchange_old_paths_warn_and_resolve(): serves the four names from their old modules
    test_import_positions_*(): merges CSV rows into sets and spots, refuses bad rows and pins
    test_regression_import_positions_*(): covers the audited CSV import defects
"""

import base64
import importlib
import json
import math
import sys
from datetime import UTC, datetime

import numpy as np
import pytest

from showpy.choreo.forms import slots, text_form
from showpy.choreo.path_models import PathRow, TransitionSpec
from showpy.core.caps import MAX_POINTS, MAX_TEXT_CHARS
from showpy.core.deprecation import DeprecatedWarning
from showpy.core.errors import (
    FontError,
    LockedError,
    MissingFontError,
    SchemaError,
    UnknownRefError,
    VersionError,
)
from showpy.core.ids import FormId, SpotId, make_tid
from showpy.core.resources import font_path
from showpy.core.util import approx_equal, asset_uri
from showpy.geo.fonts import MAX_FONT_BYTES, FontRegistry, bundled_fonts, register_font
from showpy.show import interchange
from showpy.show.interchange import (
    FONTS_KEY,
    MAX_IMPORT_FONTS,
    SCHEMA_TAG,
    export_json,
    import_fonts,
    import_json,
    import_positions,
)
from showpy.show.migrate import CURRENT_VERSION, FORMAT
from showpy.show.models import Show
from showpy.show.pinning import pin
from showpy.show.repositories import InMemoryRepository
from tests.fixtures.cast_shows import rich_show
from tests.fixtures.fonts import tiny_bytes
from tests.fixtures.shows import two_set_show
from tests.helpers import digests_under_hash_seeds, fails_after

TOL = 1e-9
EMPTY_FONTS = FontRegistry(fonts={}, sources={}, names={})
FIRST_NEW_SET = 3
HANG_BACKSTOP_SECONDS = 3.0
MANY_NEW_SETS = 32_000
FULL_TURN = 360.0


FIRST_NEW_SET = 3
HANG_BACKSTOP_SECONDS = 3.0
MANY_NEW_SETS = 32_000
FULL_TURN = 360.0
IMPORT_TEXT = """set,counts,entity,x,y,facing
1,0,T1,0,5,0
1,0,N1,10,5,
2,8,T1,1,9,90
3,4,N2,3,3,
"""


def test_import_positions_creates_sets_and_spots() -> None:
    show = import_positions(two_set_show(), IMPORT_TEXT, group="trumpets")
    assert show.set_ids() == ["1", "2", "3"]
    assert show.cast.ids() == ("T1", "T2", "T3", "T4", "N1", "N2")
    assert show.cast.spot("N2").group == "trumpets"
    assert show.set("3").counts == 4.0
    for current in show.sets:
        assert set(current.entity_ids()) == set(show.cast.ids())


def test_import_positions_moves_existing_and_copies_forward() -> None:
    show = import_positions(two_set_show(), IMPORT_TEXT, group="trumpets")
    assert show.set("2").get("T1").point == (1.0, 9.0)
    assert show.set("2").get("T1").facing == 90.0
    assert show.set("2").get("T2").point == (2.0, 9.0)
    assert show.set("3").get("T1").point == (1.0, 9.0)
    assert show.set("2").get("N1").point == (10.0, 5.0)


def test_import_positions_keeps_spots_off_field_before_they_appear() -> None:
    show = import_positions(two_set_show(), IMPORT_TEXT, group="trumpets")
    n2 = [show.set(s).get("N2") for s in ("1", "2", "3")]
    assert [p.on_field for p in n2] == [False, False, True]
    assert all(p.point == (3.0, 3.0) for p in n2)


def test_import_positions_maps_column_names() -> None:
    text = "Set,Len,Who,X,Y\n2,8,T1,5,5\n"
    columns = {"set": "Set", "counts": "Len", "entity": "Who", "x": "X", "y": "Y"}
    show = import_positions(two_set_show(), text, group="trumpets", columns=columns)
    assert show.set("2").get("T1").point == (5.0, 5.0)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("set,counts,entity,x\n1,0,T1,0\n", SchemaError),
        ("set,counts,entity,x,y\n1,0,T1,zero,5\n", SchemaError),
        ("set,counts,entity,x,y\n1,0,T1,0,5\n1,0,T1,1,5\n", SchemaError),
        ("set,counts,entity,x,y\n2,4,T1,0,5\n", SchemaError),
        ("set,counts,entity,x,y\n9,4,T1,0,5\n9,2,T2,0,5\n", SchemaError),
    ],
)
def test_import_positions_refuses_bad_rows(text: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        import_positions(two_set_show(), text, group="trumpets")


def test_import_positions_refuses_unknown_group_and_mapping() -> None:
    with pytest.raises(UnknownRefError):
        import_positions(two_set_show(), IMPORT_TEXT, group="tubas")
    with pytest.raises(UnknownRefError):
        import_positions(two_set_show(), IMPORT_TEXT, group="trumpets", columns={"who": "W"})


def test_import_positions_respects_pins() -> None:
    show = two_set_show()
    pinned = show.sets[1].with_placements({"T1": {"pinned": True}})
    show = show.model_copy(update={"sets": (show.sets[0], pinned)})
    with pytest.raises(LockedError):
        import_positions(show, IMPORT_TEXT, group="trumpets")
    forced = import_positions(show, IMPORT_TEXT, group="trumpets", force=True)
    assert forced.set("2").get("T1").point == (1.0, 9.0)


def test_import_positions_inserts_new_sets_where_the_file_puts_them() -> None:
    between = "set,counts,entity,x,y\n1,0,T1,0,5\n1A,4,T1,0,7\n2,8,T1,0,9\n"
    assert import_positions(two_set_show(), between, "trumpets").set_ids() == ["1", "1A", "2"]
    leading = "set,counts,entity,x,y\n0,0,T1,0,1\n1,0,T1,0,5\n"
    assert import_positions(two_set_show(), leading, "trumpets").set_ids() == ["0", "1", "2"]
    appended = "set,counts,entity,x,y\n3,4,T1,0,1\n"
    assert import_positions(two_set_show(), appended, "trumpets").set_ids() == ["1", "2", "3"]


SIMPLE_TEXT = "set,counts,entity,x,y\n3,4,T1,1,1\n"


@pytest.mark.defect("UP-9.5-41")
@pytest.mark.xfail(strict=True, reason="UP-9.5-41")
def test_regression_import_positions_reads_a_utf8_bom() -> None:
    """Reads a CSV that starts with a UTF-8 byte order mark, as Excel writes it (audit 9.5)."""
    plain = import_positions(two_set_show(), SIMPLE_TEXT, group="trumpets")
    bom = import_positions(two_set_show(), "﻿" + SIMPLE_TEXT, group="trumpets")
    assert bom == plain


@pytest.mark.defect("UP-9.5-42")
@pytest.mark.xfail(strict=True, reason="UP-9.5-42")
def test_regression_import_positions_reads_semicolon_delimited_csv() -> None:
    """Reads a semicolon-delimited CSV, as Excel writes in many locales (audit 9.5)."""
    plain = import_positions(two_set_show(), SIMPLE_TEXT, group="trumpets")
    semicolons = import_positions(two_set_show(), SIMPLE_TEXT.replace(",", ";"), "trumpets")
    assert semicolons == plain


def test_regression_import_positions_normalizes_facing() -> None:
    """Stores facing 720 as 0 instead of keeping it unnormalized (audit 9.5)."""
    text = "set,counts,entity,x,y,facing\n3,4,T1,1,1,720\n"
    facing = import_positions(two_set_show(), text, group="trumpets").set("3").get("T1").facing
    assert 0.0 <= facing < FULL_TURN
    assert approx_equal(facing, 0.0, abs_tol=TOL)


def test_regression_import_positions_rejects_ids_with_spaces() -> None:
    """Rejects an entity ID that contains a space instead of creating a spot with it."""
    with pytest.raises(SchemaError):
        import_positions(two_set_show(), "set,counts,entity,x,y\n3,4,T 9,1,1\n", "trumpets")


def test_regression_import_positions_rejects_ids_with_path_separators_and_names_the_line() -> None:
    """Rejects `../x` with a SchemaError that names the line."""
    with pytest.raises(SchemaError, match="line 2"):
        import_positions(two_set_show(), "set,counts,entity,x,y\n3,4,../x,1,1\n", "trumpets")


def test_regression_import_positions_wraps_a_negative_facing() -> None:
    """Stores facing -90 as 270."""
    text = "set,counts,entity,x,y,facing\n3,4,T1,1,1,-90\n"
    facing = import_positions(two_set_show(), text, group="trumpets").set("3").get("T1").facing
    assert approx_equal(facing, 270.0, abs_tol=TOL)


def test_regression_import_positions_rejects_a_bare_carriage_return_with_schema_error() -> None:
    """Raises SchemaError for an unquoted bare CR; csv.Error used to leak (UP-9.5-58)."""
    with pytest.raises(SchemaError, match="malformed"):
        import_positions(two_set_show(), "\r0", "trumpets")


def _refuse_set_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes ordering sets fail the test, so a refusal proves the cap ran before it."""

    def ordered_too_early(*_args: object) -> list[str]:
        raise AssertionError("sets were ordered before the placement cap")

    monkeypatch.setattr(interchange, "_set_order", ordered_too_early)


@fails_after(HANG_BACKSTOP_SECONDS)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_import_positions_caps_the_placement_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuses rows that would build over MAX_POINTS placements; 400 rows took 1 s (UP-9.5-60)."""
    _refuse_set_ordering(monkeypatch)
    side = math.isqrt(MAX_POINTS) + 1
    rows = "".join(f"{FIRST_NEW_SET + i},4,E{i},1,1\n" for i in range(side))
    with pytest.raises(SchemaError, match="placements"):
        import_positions(two_set_show(), "set,counts,entity,x,y\n" + rows, "trumpets")


def test_regression_import_positions_caps_the_text_size() -> None:
    """Refuses a CSV text over MAX_TEXT_CHARS before parsing it."""
    with pytest.raises(SchemaError, match="characters"):
        import_positions(two_set_show(), "set\n" + "x" * MAX_TEXT_CHARS, "trumpets")


@fails_after(HANG_BACKSTOP_SECONDS)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_import_positions_caps_placements_before_ordering_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuses 32,000 new sets before ordering them; ordering first took 7.6 s (UP-9.5-60).

    The 586 KB CSV fits the text cap; each row adds a set and a spot, so the placements
    (32,002 sets by 32,004 spots) exceed MAX_POINTS long before any set is ordered.
    """
    _refuse_set_ordering(monkeypatch)
    rows = "".join(f"{FIRST_NEW_SET + i},4,E{i},1,1\n" for i in range(MANY_NEW_SETS))
    with pytest.raises(SchemaError, match="placements"):
        import_positions(two_set_show(), "set,counts,entity,x,y\n" + rows, "trumpets")


BETWEEN_TEXT = "set,counts,entity,x,y\n1,0,T1,0,0\n1b,4,T1,1,1\n2,8,T1,0,9\n"
SAVED_AT = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _saves(show: Show) -> None:
    InMemoryRepository().put_show("s", show, expected_revision="", at=SAVED_AT)


def test_regression_import_positions_refuses_a_set_inside_a_locked_transition() -> None:
    """Raises LockedError for a set inserted into a pinned transition, and saves when forced."""
    locked = pin(two_set_show(), "transition", "1->2")
    with pytest.raises(LockedError):
        import_positions(locked, BETWEEN_TEXT, "trumpets")
    forced = import_positions(locked, BETWEEN_TEXT, "trumpets", force=True)
    assert forced.set_ids() == ["1", "1b", "2"]
    _saves(forced)


def test_regression_import_positions_drops_paths_between_sets_it_separates() -> None:
    """Drops a stored transition that is no longer between consecutive sets."""
    base = two_set_show()
    spec = TransitionSpec(
        id=make_tid("1", "2"), counts=8.0, rows={SpotId("T1"): PathRow(entity=SpotId("T1"), end=2)}
    )
    stored = base.model_copy(update={"transitions": (spec,)})
    merged = import_positions(stored, BETWEEN_TEXT, "trumpets")
    assert merged.transitions == ()
    _saves(merged)


def test_export_json_is_tagged_and_json_safe() -> None:
    exported = export_json(rich_show())
    assert (exported["format"], exported["version"]) == (FORMAT, CURRENT_VERSION)
    assert json.loads(json.dumps(exported)) == exported


def test_json_round_trip_restores_the_show() -> None:
    show = rich_show()
    assert import_json(json.loads(json.dumps(export_json(show)))) == show


def test_include_limits_top_level_fields_and_keeps_the_id() -> None:
    exported = export_json(two_set_show(), include=["sets", "title"])
    assert set(exported["show"]) == {"id", "sets", "title"}


def test_include_refuses_unknown_fields() -> None:
    with pytest.raises(UnknownRefError) as caught:
        export_json(two_set_show(), include=["setz"])
    assert "sets" in caught.value.hint


@pytest.mark.parametrize(
    "document",
    [
        {"show": {}},
        {"schema": "showpy.show/1", "show": {}},
        {"schema": "showpy.show.v1"},
        {"schema": "showpy.show.v1", "show": {"id": "x"}},
    ],
)
def test_import_json_refuses_wrong_or_missing_schema(document: dict[str, object]) -> None:
    with pytest.raises(SchemaError):
        import_json(document)


def test_export_json_writes_the_format_header_first() -> None:
    exported = export_json(two_set_show())
    assert list(exported)[:3] == ["format", "version", "show"]
    assert (exported["format"], exported["version"]) == ("showpy.show", 2)
    assert "schema" not in exported


def test_regression_import_json_reads_a_version_1_document() -> None:
    show = rich_show()
    document = {"schema": SCHEMA_TAG, "show": json.loads(json.dumps(show.model_dump(mode="json")))}
    assert import_json(document) == show


def test_regression_import_json_refuses_a_newer_version() -> None:
    document = {**export_json(two_set_show()), "version": CURRENT_VERSION + 1}
    with pytest.raises(VersionError) as caught:
        import_json(document)
    assert caught.value.context["version"] == CURRENT_VERSION + 1
    assert caught.value.context["newest"] == CURRENT_VERSION


def test_regression_import_json_refuses_mixed_tags() -> None:
    with pytest.raises(SchemaError) as caught:
        import_json({**export_json(two_set_show()), "schema": SCHEMA_TAG})
    assert caught.value.code == "UNKNOWN_FORMAT"


def test_regression_import_json_refuses_a_lone_surrogate() -> None:
    document = export_json(two_set_show())
    document["show"] = {**document["show"], "title": "\ud800"}
    with pytest.raises(SchemaError) as caught:
        import_json(document)
    assert caught.value.code == "STRICT_JSON"


def test_import_json_rejects_the_old_stage_tag() -> None:
    document = {**json.loads(json.dumps(export_json(rich_show()))), "schema": "stage.show.v1"}
    with pytest.raises(SchemaError) as caught:
        import_json(document)
    assert "showpy.show.v1" in str(caught.value)
    assert "showpy.show.v1" in (caught.value.hint or "")


def test_regression_export_json_include_accepts_a_single_field_name() -> None:
    """Treats `include="title"` as one field instead of iterating its characters."""
    document = export_json(two_set_show(), include="title")
    assert sorted(document["show"]) == ["id", "title"]


def test_regression_import_json_rejects_a_non_mapping_with_schema_error() -> None:
    """Raises SchemaError for `[1]`; an AttributeError used to leak from `.get` (UP-9.5-35)."""
    with pytest.raises(SchemaError):
        import_json([1])  # type: ignore[arg-type]


def test_regression_export_json_ignores_hash_seed() -> None:
    """Dumps the same JSON export under every hash seed, spot tags included (audit 4.3; P0 #9)."""
    digests = digests_under_hash_seeds(
        "from showpy.show.interchange import export_json",
        "json.dumps(export_json(show), sort_keys=True).encode()",
    )
    assert all(first == second for first, second in digests), digests
    assert len({first for first, _ in digests}) == 1, digests


def test_regression_import_json_keeps_the_duplicate_set_id_error() -> None:
    document = export_json(two_set_show())
    sets = document["show"]["sets"]
    document["show"]["sets"] = [*sets, sets[0]]
    with pytest.raises(SchemaError) as info:
        import_json(document)
    assert info.value.message == "set ids repeat"


@pytest.mark.parametrize(
    "tag", [{"schema": SCHEMA_TAG}, {"format": FORMAT, "version": CURRENT_VERSION}]
)
def test_regression_import_json_counts_field_errors_under_error_count(
    tag: dict[str, object],
) -> None:
    with pytest.raises(SchemaError) as caught:
        import_json({**tag, "show": {"id": "x"}})
    assert "errors" not in caught.value.context
    error_count = caught.value.context["error_count"]
    assert isinstance(error_count, int)
    assert error_count >= 1


TEXT_MEMBERS = ["T1", "T2", "T3", "T4"]


def _text_show() -> tuple[Show, FontRegistry, str]:
    registry, tiny = register_font(bundled_fonts(), tiny_bytes())
    simplex = registry.resolve("simplex")
    base = two_set_show()
    forms = {
        "tiny": text_form("tiny", TEXT_MEMBERS, "I", fonts=[tiny, simplex], height=2.0,
                          at=(0, 5), n=4, font_registry=registry),
    }  # fmt: skip
    return base.replace(forms=forms), registry, tiny


def test_export_embeds_every_font_a_text_form_uses() -> None:
    show, registry, tiny = _text_show()
    fonts = export_json(show, font_registry=registry)[FONTS_KEY]
    assert list(fonts) == sorted([tiny, registry.resolve("simplex")])
    assert base64.b64decode(fonts[tiny]) == tiny_bytes()


def test_export_without_text_forms_has_no_fonts_key() -> None:
    assert set(export_json(two_set_show())) == {"format", "version", "show"}


def test_import_fonts_round_trips_slots() -> None:
    show, registry, _ = _text_show()
    form = show.forms[FormId("tiny")]
    document = json.loads(json.dumps(export_json(show, font_registry=registry)))
    rebuilt = import_fonts(document, font_registry=EMPTY_FONTS)
    assert np.array_equal(slots(form, font_registry=rebuilt), slots(form, font_registry=registry))


def test_import_fonts_rejects_tampered_data() -> None:
    show, registry, tiny = _text_show()
    document = export_json(show, font_registry=registry)
    document[FONTS_KEY][tiny] = base64.b64encode(tiny_bytes(family="Evil")).decode()
    with pytest.raises(FontError) as info:
        import_fonts(document)
    assert info.value.code == "FONT_HASH_MISMATCH"
    assert info.value.context["uri"] == tiny


def test_import_fonts_rejects_bad_base64() -> None:
    with pytest.raises(FontError) as info:
        import_fonts({FONTS_KEY: {asset_uri(b"x"): "not base64!"}})
    assert info.value.code == "BAD_FONT"


def test_export_with_a_missing_font_fails_closed() -> None:
    show, _, _ = _text_show()
    with pytest.raises(MissingFontError):
        export_json(show)


def test_export_include_without_forms_embeds_no_fonts() -> None:
    show, registry, _ = _text_show()
    exported = export_json(show, include=["title"], font_registry=registry)
    assert set(exported) == {"format", "version", "show"}


def test_import_json_still_loads_a_document_with_fonts() -> None:
    show, registry, _ = _text_show()
    loaded = import_json(export_json(show, font_registry=registry))
    assert loaded.forms[FormId("tiny")] == show.forms[FormId("tiny")]


def test_import_fonts_without_a_fonts_key_returns_the_registry() -> None:
    assert import_fonts({"schema": SCHEMA_TAG}) is bundled_fonts()


def _entry(data: bytes) -> dict[str, str]:
    return {asset_uri(data): base64.b64encode(data).decode()}


def test_regression_import_fonts_rejects_hostile_documents() -> None:
    uri = asset_uri(b"x")
    too_long = "A" * (4 * math.ceil(MAX_FONT_BYTES / 3) + 5)
    hostile: list[object] = [
        [1],
        "text",
        7,
        None,
        {1: "AA=="},
        {uri: 7},
        {uri: None},
        {uri: ["AA=="]},
        {uri: too_long},
        {uri: "caf\u00e9"},
        {f"asset:sha256:{i:064x}": "AA==" for i in range(MAX_IMPORT_FONTS + 1)},
    ]
    for value in hostile:
        with pytest.raises(FontError) as info:
            import_fonts({FONTS_KEY: value})
        assert info.value.code == "BAD_FONT", value
    with pytest.raises(FontError):
        import_fonts([1])  # type: ignore[arg-type]
    assert import_fonts({FONTS_KEY: _entry(tiny_bytes())}).names["tiny"] == asset_uri(tiny_bytes())


def _older_simplex() -> bytes:
    document = json.loads(font_path("simplex.json").read_bytes())
    return json.dumps({**document, "family": "Simplex, an older release"}).encode()


def test_regression_import_fonts_accepts_an_older_font_under_a_taken_name() -> None:
    older, uri = register_font(EMPTY_FONTS, _older_simplex())
    form = text_form("old", [f"S{i}" for i in range(36)], "HI", fonts=[uri], height=2.0,
                     at=(0, 5), n=36, font_registry=older)  # fmt: skip
    document = export_json(two_set_show().replace(forms={"old": form}), font_registry=older)
    imported = import_fonts(document)
    assert imported.names["simplex"] == bundled_fonts().resolve("simplex")
    assert imported.source(uri) == _older_simplex()
    assert np.array_equal(slots(form, font_registry=imported), slots(form, font_registry=older))


@pytest.mark.parametrize(
    ("old_module", "name"),
    [
        ("showpy.export.data", "export_json"),
        ("showpy.export.data", "import_json"),
        ("showpy.export.data", "import_fonts"),
        ("showpy.export.positions", "import_positions"),
    ],
)
def test_interchange_old_paths_warn_and_resolve(old_module: str, name: str) -> None:
    module = importlib.import_module(old_module)
    with pytest.warns(DeprecatedWarning, match=f"`{name}` moved to `showpy.show.interchange"):
        old = getattr(module, name)
    assert old is getattr(interchange, name)


def test_regression_import_json_refuses_an_unknown_top_level_key() -> None:
    document = {**export_json(two_set_show()), "titel": "typo"}
    with pytest.raises(SchemaError) as caught:
        import_json(document)
    assert "titel" in caught.value.message
    assert caught.value.context["key"] == "titel"
    assert "x-" in (caught.value.hint or "")


def test_import_json_keeps_x_keys() -> None:
    show = two_set_show()
    extensions = {"x-extra": {"host": "demo", "n": [1, 2]}}
    document = json.loads(json.dumps(export_json(show, extensions=extensions)))
    assert document["x-extra"] == extensions["x-extra"]
    kept = {key: value for key, value in document.items() if key.startswith("x-")}
    assert import_json(document) == show
    assert export_json(import_json(document), extensions=kept) == document


@pytest.mark.parametrize("key", ["extra", "X-extra", "show", "x_extra"])
def test_export_json_refuses_extensions_that_are_not_x_keys(key: str) -> None:
    with pytest.raises(SchemaError) as caught:
        export_json(two_set_show(), extensions={key: 1})
    assert caught.value.context["key"] == key
