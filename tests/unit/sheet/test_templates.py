#!/usr/bin/env python3

"""
test_templates.py --- tests built-in sheet templates, lookup, customization, edits and saving

Contains:
    test_*(): one behavior of e8s.sheet.templates each
    test_regression_blank_sheets_with_different_content_get_different_refs(): gives blank sheets
        that differ in content different refs
    test_regression_blank_sheet_rejects_infinite_width(): rejects an infinite width instead of
        building a sheet
    test_regression_customize_rejects_non_numeric_size(): rejects a non-numeric size with a
        E8sError, not ValueError
    test_regression_customize_rejects_bad_name_type(): rejects a non-string name with an E8sError,
        not a pydantic error
    test_regression_customize_resize_updates_ref_and_name(): refreshes the ref and name of a resized
        sheet instead of keeping stale ones
    test_regression_resize_drops_guides_off_the_sheet(): rejects or drops a guide line that lies
        off the sheet after a resize
    test_regression_get_sheet_without_version_returns_latest_saved(): returns the latest saved
        version for a versionless ref, as documented
    test_regression_sheet_templates_rejects_unknown_surface_type(): rejects an unknown surface type
        instead of returning an empty list
"""

import pytest

from e8s.core.errors import (
    ConflictError,
    E8sError,
    SchemaError,
    UnknownRefError,
    VersionError,
)
from e8s.core.units import convert
from e8s.core.util import approx_equal
from e8s.sheet.bounds import on_surface
from e8s.sheet.models import Grid, GuideLine, SurfaceType
from e8s.sheet.templates import (
    InMemorySheetStore,
    add_guide,
    blank_sheet,
    customize,
    get_sheet,
    remove_guide,
    save_sheet,
    sheet_templates,
)

TOL = 1e-9
HS_REF = "sheet:hs-football@1"
COLLEGE_REF = "sheet:college-football@1"
NFL_REF = "sheet:nfl-football@1"
GYM_REF = "sheet:gym-basketball@1"


def _ft(feet: float, inches: float = 0.0) -> float:
    return convert(feet, "ft", "m") + convert(inches, "in", "m")


def _guide_y(ref: str, guide_id: str) -> float:
    sheet = get_sheet(ref)
    return next(g.points[0][1] for g in sheet.guides if g.id == guide_id)


def test_templates_cover_every_built_in_surface() -> None:
    refs = {s.ref for s in sheet_templates()}
    assert {HS_REF, COLLEGE_REF, NFL_REF, GYM_REF} <= refs
    assert any(s.surface_type == SurfaceType.BLANK for s in sheet_templates())


def test_templates_filter_by_surface_type() -> None:
    fields = sheet_templates(SurfaceType.FOOTBALL_FIELD)
    assert {s.ref for s in fields} == {HS_REF, COLLEGE_REF, NFL_REF}
    assert sheet_templates(SurfaceType.STREET) == []


def test_football_field_is_120_yards_by_160_feet() -> None:
    sheet = get_sheet(HS_REF)
    width, depth = sheet.size_m
    assert approx_equal(width, 109.728, abs_tol=TOL)
    assert approx_equal(depth, 48.768, abs_tol=TOL)
    xs = [p[0] for p in sheet.outline]
    ys = [p[1] for p in sheet.outline]
    assert approx_equal(
        [min(xs), max(xs), min(ys), max(ys)], [-54.864, 54.864, 0.0, 48.768], abs_tol=TOL
    )


@pytest.mark.parametrize(
    ("ref", "from_sideline"),
    [(HS_REF, _ft(53, 4)), (COLLEGE_REF, _ft(60)), (NFL_REF, _ft(70, 9))],
)
def test_hashes_sit_at_rule_book_distances(ref: str, from_sideline: float) -> None:
    depth = get_sheet(ref).size_m[1]
    assert approx_equal(_guide_y(ref, "hash-front"), from_sideline, abs_tol=TOL)
    assert approx_equal(_guide_y(ref, "hash-back"), depth - from_sideline, abs_tol=TOL)


def test_yard_lines_every_five_yards_with_labels() -> None:
    sheet = get_sheet(HS_REF)
    yards = [g for g in sheet.guides if g.kind == "yard"]
    assert len(yards) == 21
    labels = sorted({g.label for g in yards})
    assert labels == sorted({"G", "5", "10", "15", "20", "25", "30", "35", "40", "45", "50"})
    left_45 = next(g for g in yards if g.id == "yard-left-45")
    assert approx_equal(left_45.points[0][0], -convert(5, "yd", "m"), abs_tol=TOL)


def test_field_has_sidelines_end_lines_and_center() -> None:
    sheet = get_sheet(HS_REF)
    kinds = {g.id: g.kind for g in sheet.guides}
    assert kinds["sideline-front"] == "sideline"
    assert kinds["sideline-back"] == "sideline"
    assert kinds["end-line-left"] == "edge"


def test_gym_template_is_a_basketball_court() -> None:
    width, depth = get_sheet(GYM_REF).size_m
    assert approx_equal((width, depth), (28.6512, 15.24), abs_tol=TOL)


def test_get_sheet_without_version_returns_template() -> None:
    assert get_sheet("sheet:hs-football").ref == HS_REF


def test_get_sheet_unknown_suggests_close_matches() -> None:
    with pytest.raises(UnknownRefError) as info:
        get_sheet("sheet:hs-footbal@1")
    assert "hs-football" in info.value.hint


def test_get_sheet_falls_back_to_store() -> None:
    store = InMemorySheetStore()
    saved = save_sheet(store, customize(get_sheet(HS_REF), name="Lincoln HS"))
    assert get_sheet(saved, store).name == "Lincoln HS"


def test_blank_sheet_dimensions_and_units() -> None:
    sheet = blank_sheet(10.0, 6.0, grid_spacing=2.0, units="ft")
    assert sheet.size_m == (10.0, 6.0)
    assert approx_equal(sheet.grid.spacing, (_ft(2), _ft(2)), abs_tol=TOL)
    assert sheet.grid.unit_name == "ft"
    assert approx_equal(sorted(p[0] for p in sheet.outline), [-5, -5, 5, 5], abs_tol=TOL)


def test_blank_sheet_rejects_non_positive_size() -> None:
    with pytest.raises(SchemaError):
        blank_sheet(0.0, 5.0)


def test_customize_resize_regenerates_outline_and_keeps_user_items() -> None:
    tape = GuideLine(id="tape", kind="custom", points=((0.0, 1.0), (1.0, 1.0)))
    sheet = add_guide(blank_sheet(10.0, 6.0), tape)
    resized = customize(sheet, size=(20.0, 8.0), name="Big room")
    assert resized.size_m == (20.0, 8.0)
    assert resized.name == "Big room"
    assert max(p[0] for p in resized.walkable.exterior) == 10.0
    assert resized.guides[-1] == tape


def test_customize_size_only_for_blank_sheets() -> None:
    with pytest.raises(SchemaError):
        customize(get_sheet(HS_REF), size=(10.0, 10.0))


def test_customize_rejects_unknown_fields_and_keeps_input() -> None:
    sheet = blank_sheet(10.0, 6.0)
    with pytest.raises(SchemaError):
        customize(sheet, owner="me")
    changed = customize(sheet, grid=Grid(spacing=(0.5, 0.5)))
    assert changed.grid.spacing == (0.5, 0.5)
    assert sheet.grid.spacing == (1.0, 1.0)


def test_add_and_remove_items() -> None:
    sheet = blank_sheet(10.0, 6.0)
    guide = GuideLine(id="tape", kind="custom", points=((0.0, 1.0), (1.0, 1.0)))
    edited = add_guide(sheet, guide)
    assert guide in edited.guides
    restored = remove_guide(edited, "tape")
    assert restored == sheet


def test_duplicate_ids_raise() -> None:
    sheet = blank_sheet(10.0, 6.0)
    with pytest.raises(SchemaError):
        add_guide(sheet, GuideLine(id="center-line", kind="custom", points=((0.0, 0.0),)))


def test_remove_unknown_suggests_close_matches() -> None:
    sheet = add_guide(
        blank_sheet(10.0, 6.0), GuideLine(id="tape", kind="custom", points=((0.0, 1.0),))
    )
    with pytest.raises(UnknownRefError) as info:
        remove_guide(sheet, "tapee")
    assert "tape" in info.value.hint


def test_save_sheet_bumps_version_and_records_parent() -> None:
    store = InMemorySheetStore()
    first = save_sheet(store, get_sheet(HS_REF), scope="team")
    assert first == "sheet:hs-football@2"
    saved = store.get(first)
    assert saved is not None
    assert saved.based_on == HS_REF
    assert save_sheet(store, get_sheet(HS_REF)) == first
    renamed = customize(get_sheet(HS_REF), name="Lincoln HS")
    assert save_sheet(store, renamed) == "sheet:hs-football@3"


def test_save_sheet_rejects_bad_scope_and_version() -> None:
    store = InMemorySheetStore()
    with pytest.raises(SchemaError):
        save_sheet(store, get_sheet(HS_REF), scope="world")  # type: ignore[arg-type]
    odd = get_sheet(HS_REF).model_copy(update={"ref": "sheet:x@draft"})
    with pytest.raises(VersionError):
        save_sheet(store, odd)


def test_store_put_is_idempotent_and_refuses_overwrites() -> None:
    store = InMemorySheetStore()
    sheet = blank_sheet(4.0, 4.0)
    assert store.put(sheet, "mine") == store.put(sheet, "mine")
    with pytest.raises(ConflictError):
        store.put(sheet.model_copy(update={"name": "other"}), "mine")


def test_store_versionless_get_returns_latest() -> None:
    store = InMemorySheetStore()
    sheet = blank_sheet(4.0, 4.0)
    save_sheet(store, sheet)
    latest = save_sheet(store, customize(sheet, name="second"))
    found = store.get(sheet.ref.split("@")[0])
    assert found is not None
    assert found.ref == latest


HS_ID_REF = "sheet:hs-football"


@pytest.mark.defect("UP-9.4-30")
@pytest.mark.xfail(strict=True, reason="UP-9.4-30")
def test_regression_blank_sheets_with_different_content_get_different_refs() -> None:
    """Gives blank sheets that differ in content different refs (audit 9.4)."""
    by_grid = blank_sheet(10, 6, grid_spacing=2)
    by_units = blank_sheet(10, 6, units="ft")
    assert by_grid == by_units or by_grid.ref != by_units.ref


def test_regression_blank_sheet_rejects_infinite_width() -> None:
    """Rejects an infinite width instead of building a sheet (audit 9.4)."""
    with pytest.raises(SchemaError):
        blank_sheet(float("inf"), 5.0)


@pytest.mark.defect("UP-9.4-32")
@pytest.mark.xfail(strict=True, reason="UP-9.4-32")
def test_regression_customize_rejects_non_numeric_size() -> None:
    """Rejects a non-numeric size with an E8sError, not ValueError (audit 9.4)."""
    with pytest.raises(E8sError):
        customize(blank_sheet(10, 6), size=("wide", "deep"))


def test_regression_customize_rejects_bad_name_type() -> None:
    """Rejects a non-string name with an E8sError, not a pydantic error (audit 9.4)."""
    with pytest.raises(SchemaError):
        customize(blank_sheet(10, 6), name=5)


@pytest.mark.defect("UP-9.4-34")
@pytest.mark.xfail(strict=True, reason="UP-9.4-34")
def test_regression_customize_resize_updates_ref_and_name() -> None:
    """Refreshes the ref and name of a resized sheet instead of keeping stale ones (audit 9.4)."""
    original = blank_sheet(10, 6)
    resized = customize(original, size=(12.0, 6.0))
    assert resized.ref != original.ref
    assert resized.name != original.name


@pytest.mark.defect("UP-9.4-36")
@pytest.mark.xfail(strict=True, reason="UP-9.4-36")
def test_regression_get_sheet_without_version_returns_latest_saved() -> None:
    """Returns the latest saved version for a versionless ref, as documented (audit 9.4)."""
    store = InMemorySheetStore()
    template = get_sheet(HS_ID_REF)
    saved = save_sheet(store, template.model_copy(update={"name": "my field"}))
    found = get_sheet(HS_ID_REF, store)
    assert found.ref == saved
    assert found.name == "my field"


@pytest.mark.defect("UP-9.4-37")
@pytest.mark.xfail(strict=True, reason="UP-9.4-37")
def test_regression_sheet_templates_rejects_unknown_surface_type() -> None:
    """Rejects an unknown surface type instead of returning an empty list (audit 9.4)."""
    with pytest.raises(E8sError):
        sheet_templates("nope")  # type: ignore[arg-type]


@pytest.mark.defect("UP-9.4-53")
@pytest.mark.xfail(strict=True, reason="UP-9.4-53")
def test_regression_resize_drops_guides_off_the_sheet() -> None:
    """Rejects or drops a guide line that lies off the sheet after a resize (audit 9.4)."""
    tape = GuideLine(id="far", kind="custom", points=((4.5, 1.0), (4.5, 2.0)))
    sheet = add_guide(blank_sheet(10, 6), tape)
    try:
        resized = customize(sheet, size=(4.0, 6.0))
    except E8sError:
        return
    for guide in resized.guides:
        assert all(on_surface(resized, point) for point in guide.points)
